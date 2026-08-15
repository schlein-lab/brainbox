#!/usr/bin/env python3

import os, sys, json, time, tempfile, threading

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from pnlib.warmsession import WarmSession, PinnedReasoner, Saturated

PY = sys.executable
FAKEWARM = os.path.join(ROOT, "tests", "fakewarm.py")
PASS = FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {label}")
    else:
        FAIL += 1; print(f"  FAIL  {label}")

def _script(actions):
    p = tempfile.mktemp(prefix="warm_script_", suffix=".json")
    with open(p, "w") as f:
        json.dump([a if isinstance(a, str) else json.dumps(a) for a in actions], f)
    return p

SUBMIT = {"op": "submit", "task_type": "echo.test", "params": {"msg": "x"}}

def _cmd(extra_env=None):
    return f"{PY} {FAKEWARM}"

def mk_session(script_path=None, env=None, **kw):
    e = {}
    if script_path:
        e["PN_FAKEWARM_SCRIPT"] = script_path
    if env:
        e.update(env)
    return WarmSession("sonnet", f"{PY} {FAKEWARM}", e, **kw)

def test_spawn_and_reuse():
    print("[a/b] spawn->ready once; N reasons reuse the SAME long-lived process")
    sp = _script([SUBMIT, SUBMIT, SUBMIT])
    s = mk_session(sp)
    s.spawn()
    check(s.state == "ready" and s._alive(), "spawned and ready (process alive)")
    pid1 = s.proc.pid
    r1 = s.ask("turn1"); r2 = s.ask("turn2"); r3 = s.ask("turn3")
    check(all(r.get("ok") for r in (r1, r2, r3)), "three sequential reasons all ok")
    check(json.loads(r1["text"])["op"] == "submit", "the warm session served a closed-world action")
    check(s.served == 3 and s.spawn_count == 1, "served=3 across ONE spawn (warm reuse, not per-call)")
    check(s.proc.pid == pid1, "the SAME backend process served every turn (pinned/long-lived)")
    s.retire()

def test_health():
    print("[c] health-check: alive->True; retired->False (a rotate trigger)")
    sp = _script([SUBMIT])
    s = mk_session(sp)
    s.spawn()
    check(s.health_check() is True, "a live pinned session pings healthy")
    s.retire()
    check(s.health_check() is False, "a retired session pings unhealthy (rotate trigger)")

def test_backpressure():
    print("[d] backpressure: one in-flight reason; concurrent ask blocks / Saturated(block=False)")

    sp = _script([SUBMIT, SUBMIT])

    s = mk_session(sp, request_timeout=5)
    s.spawn()
    got = s._lock.acquire(blocking=False)
    check(got, "took the session request lock (simulating an in-flight reason)")
    try:
        raised = False
        try:
            s.ask("concurrent", block=False)
        except Saturated:
            raised = True
        check(raised, "a non-blocking ask while busy raises Saturated (backpressure)")

        done = {"v": False}
        def _bg():
            s.ask("blocking", block=True); done["v"] = True
        t = threading.Thread(target=_bg, daemon=True); t.start()
        time.sleep(0.3)
        check(done["v"] is False, "a blocking ask WAITS while a reason is in flight (backpressure)")
    finally:
        s._lock.release()
    t.join(timeout=5)
    check(done["v"] is True, "the blocking ask completes once the in-flight reason releases")
    s.retire()

def test_crash_then_respawn():
    print("[e] crash->dead (rotate trigger); next ask lazy-respawns a fresh live session")
    sp = _script([SUBMIT, SUBMIT])
    s = mk_session(sp, env={"PN_FAKEWARM_CRASH": "0"})
    s.spawn()
    r = s.ask("turn-that-crashes")
    check(r.get("ok") is False, "a backend crash before answering returns ok=False (no hang)")
    check(s.state == "dead", "the session is marked dead (a rotate trigger for the keeper)")

    sp2 = _script([SUBMIT])
    s2 = mk_session(sp2)
    s2.spawn()
    r2 = s2.ask("fresh")
    check(r2.get("ok") and json.loads(r2["text"])["op"] == "submit",
          "a fresh warm session serves normally after a prior crash (respawn works)")
    s.retire(); s2.retire()

def test_wedge_watchdog():
    print("[f] wedge->watchdog: a backend hung past the timeout is KILLED; ask returns ok=False")
    sp = _script([SUBMIT])
    s = mk_session(sp, env={"PN_FAKEWARM_WEDGE": "0"}, request_timeout=1.0)
    s.spawn()
    t0 = time.time()
    r = s.ask("turn-that-wedges", timeout=1.0)
    dt = time.time() - t0
    check(r.get("ok") is False, "a wedged backend ask returns ok=False (not a forever hang)")
    check(dt < 5.0, f"the watchdog killed the wedged backend promptly ({dt:.1f}s)")
    s.retire()

def test_pinned_rotate_retire():
    print("[g/h] PinnedReasoner.rotate (atomic handoff, primed) + retire (the keeper's kill_fn)")
    sp = _script([SUBMIT, SUBMIT, SUBMIT, SUBMIT, SUBMIT])
    pr = PinnedReasoner("sonnet", f"{PY} {FAKEWARM}", {"PN_FAKEWARM_SCRIPT": sp},
                        request_timeout=5)
    sid0 = pr.start()
    r = pr.ask("turn1")
    check(r.get("ok"), "the pinned reasoner serves a reason")
    old_proc_pid = pr.session.proc.pid
    new_id, old_id = pr.rotate(digest="# digest\n- intent: keep going")
    check(new_id != old_id and old_id == sid0, "rotate produced a FRESH session id (old replaced)")
    check(pr.session.proc.pid != old_proc_pid, "rotate spawned a NEW backend process (old retired)")
    check(pr.session.health_check() is True, "the freshly-rotated session is live + healthy")
    r2 = pr.ask("turn-after-rotate")
    check(r2.get("ok"), "the brain keeps reasoning on the rotated session (zero loss of capability)")

    pr.retire()
    check(pr.session is None, "retire killed the pinned session (kill_fn: runaway brain killable)")
    r3 = pr.ask("turn-after-retire")
    check(r3.get("ok"), "the next ask lazy-respawns after a retire (resilient)")
    pr.retire()

def test_daemon_pin_verbs():
    print("[i] pn-llmd daemon: reason / pin-health / pin-rotate / pin-retire over the live socket")
    import subprocess
    rt = tempfile.mkdtemp(prefix="warm_llmd_")
    sock = os.path.join(rt, "pn-llmd.sock")
    sp = _script([SUBMIT, SUBMIT, SUBMIT, SUBMIT, SUBMIT])
    env = dict(os.environ)
    env["PN_LLM_SOCK"] = sock
    env["PN_LLM_POOL"] = "1"
    env["PN_LLM_CMD"] = f"{PY} {os.path.join(ROOT, 'tests', 'fakellm.py')} {{model}}"
    env["PN_LLM_PIN_CMD"] = f"{PY} {FAKEWARM}"
    env["PN_FAKEWARM_SCRIPT"] = sp
    proc = subprocess.Popen([PY, os.path.join(ROOT, "tools", "pn-llmd")], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def call(req, timeout=10):
        import socket as sk
        s = sk.socket(sk.AF_UNIX, sk.SOCK_STREAM); s.settimeout(timeout); s.connect(sock)
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            c = s.recv(65536)
            if not c:
                break
            buf += c
        s.close()
        return json.loads(buf.split(b"\n", 1)[0].decode()) if buf else {}

    try:
        for _ in range(50):
            if os.path.exists(sock):
                break
            time.sleep(0.1)
        r = call({"verb": "reason", "prompt": "decide"})
        check(r.get("ok") and json.loads(r["text"])["op"] == "submit",
              "the `reason` verb serves a closed-world action on the PINNED warm session")
        h = call({"verb": "pin-health"})
        check(h.get("ok") and h.get("alive") is True, "pin-health reports the warm session alive")
        info = call({"verb": "pin-info"})
        sid0 = (info.get("pin") or {}).get("session", {}).get("session_id")
        rot = call({"verb": "pin-rotate", "digest": "# d"})
        check(rot.get("ok") and rot.get("rotated") and rot.get("new_session") != sid0,
              "pin-rotate replaced the pinned session (keeper's kill_fn target)")
        r2 = call({"verb": "reason", "prompt": "again"})
        check(r2.get("ok"), "the brain keeps reasoning after a daemon-level rotation")
        ret = call({"verb": "pin-retire"})
        check(ret.get("ok") and ret.get("retired"), "pin-retire killed the pinned session (runaway killable)")
        r3 = call({"verb": "reason", "prompt": "respawn"})
        check(r3.get("ok"), "the next reason lazy-respawns after a retire")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        subprocess.run(["rm", "-rf", rt])

def main():
    print("=== P5 warm pinned reasoning-session lifecycle (FAKE NDJSON backend; no credential) ===")
    test_spawn_and_reuse()
    test_health()
    test_backpressure()
    test_crash_then_respawn()
    test_wedge_watchdog()
    test_pinned_rotate_retire()
    test_daemon_pin_verbs()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
