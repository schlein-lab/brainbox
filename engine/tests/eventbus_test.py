#!/usr/bin/env python3

import os, sys, json, time, tempfile, importlib.util, socket, subprocess, threading
os.environ.setdefault("PN_DISPATCH_BACKEND", "systemd")
from importlib.machinery import SourceFileLoader

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from pnlib import db
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pn_voraussetzung import live_moeglich

_loader = SourceFileLoader("pnd_mod", os.path.join(ROOT, "tools", "pnd"))
_spec = importlib.util.spec_from_loader("pnd_mod", _loader)
pnd = importlib.util.module_from_spec(_spec)
_loader.exec_module(pnd)

PASS, FAIL = 0, 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")

def fresh_db():
    path = tempfile.mktemp(prefix="pn_eb_", suffix=".db")
    cx = db.connect(path)
    for name, uid in (("alice", 5001), ("bob", 5002)):
        cx.execute("INSERT OR IGNORE INTO principals(name,uid,kind,note) VALUES(?,?,?,?)",
                   (name, uid, "user", f"test tenant {name}"))
    for principal, cap in (("alice", "task_type:echo.test"),
                           ("bob", "task_type:echo.test")):
        if not cx.execute("SELECT 1 FROM grants WHERE principal=? AND cap=?",
                          (principal, cap)).fetchone():
            cx.execute("INSERT INTO grants(principal,cap) VALUES(?,?)", (principal, cap))
    cx.commit()
    return cx, path

def test_typed_fanout(cx):
    print("[a] typed events carry a topic + cursor; fan-out to per-job + per-principal topics")
    ja = db.submit(cx, ["/bin/echo", "a"], "/tmp", {}, "{}", 100, 64, "tag-a",
                   principal="alice", task_type="echo.test")
    ids = db.add_typed_event(cx, ja, "progress", {"done": 1, "total": 2, "msg": "half"})
    check(len(ids) == 2, f"add_typed_event fanned out to 2 topics (got {len(ids)})")
    rows = cx.execute("SELECT topic FROM job_events WHERE job_id=? AND kind='progress'",
                      (ja,)).fetchall()
    topics = {r["topic"] for r in rows}
    check(topics == {f"job/{ja}", "user/alice"},
          f"fan-out topics == job/<id> + user/alice ({topics})")
    check(all(isinstance(i, int) and i > 0 for i in ids), "each event got a monotonic cursor id")
    return ja

def test_topic_authz(cx, ja):
    print("[b] per-principal topic authorization (the §12 close on the EVENT path)")
    jb = db.submit(cx, ["/bin/echo", "b"], "/tmp", {}, "{}", 100, 64, "tag-b",
                   principal="bob", task_type="echo.test")
    db.add_typed_event(cx, jb, "progress", {"msg": "bob-only"})

    allowed = db.authorize_topics(cx, "alice",
                                  ["user/alice", f"job/{ja}", "user/bob", f"job/{jb}", "group/x"])
    check(set(allowed) == {"user/alice", f"job/{ja}"},
          f"alice authorized only her topics ({allowed})")

    admin_allowed = db.authorize_topics(cx, "admin",
                                        ["user/alice", "user/bob", f"job/{jb}"], scope_all=True)
    check(set(admin_allowed) == {"user/alice", "user/bob", f"job/{jb}"},
          "admin view:all authorizes every requested topic")

    alice_evs = db.events_since(cx, ["user/alice", f"job/{ja}"], after_id=0)
    check(all(e["job_id"] != jb for e in alice_evs),
          "alice's authorized stream contains NO event from bob's job")

    bob_allowed = db.authorize_topics(cx, "bob", ["user/bob", f"job/{jb}"])
    bob_evs = db.events_since(cx, bob_allowed, after_id=0)
    check(any(e["job_id"] == jb for e in bob_evs), "bob's stream carries his own event")
    return jb

def test_replay_converge(cx, ja):
    print("[c] reconnect replay converges after a dropped delta")
    topics = ["user/alice", f"job/{ja}"]
    full = db.events_since(cx, topics, after_id=0)

    mid = full[len(full) // 2]["id"]
    delta = db.events_since(cx, topics, after_id=mid)

    prefix_ids = [e["id"] for e in full if e["id"] <= mid]
    delta_ids = [e["id"] for e in delta]
    converged = prefix_ids + delta_ids
    check(converged == [e["id"] for e in full],
          "prefix-seen + replayed-delta reconstructs the full ordered stream (converged)")
    check(all(i > mid for i in delta_ids), "replay returns ONLY ids strictly after the cursor")

    newid = db.add_typed_event(cx, ja, "state", {"state": "running"})[0]
    after = db.events_since(cx, topics, after_id=(delta_ids[-1] if delta_ids else mid))
    check(newid in [e["id"] for e in after], "post-reconnect event delivered from the new cursor")

def test_cvm(cx, ja):
    print("[d] the Canonical View Model — one serializer, principal-scoped")
    c_owner = db.cvm(cx, ja, principal="alice")
    c_admin = db.cvm(cx, ja, scope_all=True)
    check(c_owner is not None and c_owner["schema"] == "pn-cvm/1", "CVM has the pn-cvm/1 schema")
    check(set(c_owner) == set(c_admin), "owner CVM and admin CVM have identical SHAPE (one serializer)")
    check(c_owner["principal"] == "alice" and c_owner["id"] == ja, "CVM carries owner + id")
    check("last_event_id" in c_owner and c_owner["last_event_id"] >= 0,
          "CVM carries the current cursor (subscribe-from-here)")
    check(db.cvm(cx, ja, principal="bob") is None, "non-owner gets CVM None (principal-scoped)")

def test_approve_deny_idempotent(cx):
    print("[e] idempotent approve / deny of a staged confirmation gate (separation of duties)")

    j1 = db.submit(cx, ["/bin/echo", "x"], "/tmp", {}, "{}", 100, 64, "conf",
                   principal="alice", task_type="echo.test")
    db.stage_for_approval(cx, j1, "nonce-A")
    row = cx.execute("SELECT state, approval_state FROM jobs WHERE id=?", (j1,)).fetchone()
    check(row["state"] == "staged" and row["approval_state"] == "pending",
          "needs_confirmation job parks staged/pending")

    rself = db.resolve_approval(cx, "nonce-A", "approve", principal="alice")
    check(not rself["ok"] and "self-approve" in rself["error"],
          "submitter self-approve REFUSED (separation of duties)")

    r1 = db.resolve_approval(cx, "nonce-A", "approve", principal="operator", scope_all=True)
    check(r1["ok"] and r1["state"] == "queued" and not r1["idempotent"],
          "authorized operator approve(nonce) -> queued")

    ab = cx.execute("SELECT approved_by FROM jobs WHERE id=?", (j1,)).fetchone()[0]
    check(ab == "operator", "approved_by records the RESOLVER principal (not the submitter)")

    r2 = db.resolve_approval(cx, "nonce-A", "approve", principal="operator", scope_all=True)
    check(r2["ok"] and r2["idempotent"], "re-approve same nonce -> idempotent no-op success")

    r3 = db.resolve_approval(cx, "nonce-A", "deny", principal="operator", scope_all=True)
    check(not r3["ok"] and "already approved" in r3["error"], "deny after approve refused")

    j2 = db.submit(cx, ["/bin/echo", "y"], "/tmp", {}, "{}", 100, 64, "conf2",
                   principal="alice", task_type="echo.test")
    db.stage_for_approval(cx, j2, "nonce-B")

    rb = db.resolve_approval(cx, "nonce-B", "approve", principal="bob")
    check(not rb["ok"] and "unknown" in rb["error"], "cross-tenant approve refused (no oracle)")

    rd = db.resolve_approval(cx, "nonce-B", "deny", principal="operator", scope_all=True)
    check(rd["ok"] and rd["state"] == "cancelled", "operator deny(nonce) -> cancelled")
    rd2 = db.resolve_approval(cx, "nonce-B", "deny", principal="operator", scope_all=True)
    check(rd2["ok"] and rd2["idempotent"], "re-deny same nonce -> idempotent no-op success")

def test_group_topic_isolation(cx):
    print("[g] cross-tenant group isolation: an owner-namespaced group topic + no foreign leak")
    gid = "wf-shared"

    ja = db.submit(cx, ["/bin/echo", "a"], "/tmp", {}, "{}", 100, 64, "g-a",
                   principal="alice", task_type="echo.test", group_id=gid)
    db.add_typed_event(cx, ja, "group-status", {"overall": "running", "secret": "alice-only"})

    topics = {r["topic"] for r in cx.execute(
        "SELECT DISTINCT topic FROM job_events WHERE job_id=?", (ja,)).fetchall()}
    a_topic = db.group_topic("alice", gid)
    check(a_topic in topics, f"group event published on owner-namespaced topic {a_topic!r}")
    check(f"group/{gid}" not in topics, "no bare group/<gid> topic exists (old leaky shape gone)")

    jb = db.submit(cx, ["/bin/echo", "b"], "/tmp", {}, "{}", 100, 64, "g-b",
                   principal="bob", task_type="echo.test", group_id=gid)

    b_for_alice = db.authorize_topics(cx, "bob", [a_topic, f"group/{gid}"])
    check(b_for_alice == [],
          f"tenant B is DROPPED from ALICE's group topic + the bare gid ({b_for_alice})")

    b_topic = db.group_topic("bob", gid)
    b_allowed = db.authorize_topics(cx, "bob", [b_topic])
    check(b_allowed == [b_topic], "B is authorized only for HIS OWN namespaced group topic")
    b_evs = db.events_since(cx, b_allowed, after_id=0)
    check(all(e["job_id"] != ja for e in b_evs),
          "tenant B's authorized stream carries ZERO of alice's group events (no cross-tenant leak)")

    a_allowed = db.authorize_topics(cx, "alice", [a_topic])
    check(a_allowed == [a_topic], "the owner IS authorized for her own-namespaced group topic")
    a_grp_evs = db.events_since(cx, [a_topic], after_id=0)
    check(any(e["job_id"] == ja for e in a_grp_evs),
          "alice's own group topic still delivers her group events (functionality intact)")

def test_integrity(cx):
    print("[h] PRAGMA integrity_check + v6 schema present")
    r = cx.execute("PRAGMA integrity_check").fetchone()[0]
    check(r == "ok", f"integrity_check = {r!r}")
    jcols = {row["name"] for row in cx.execute("PRAGMA table_info(jobs)")}
    check({"steer_seq", "approval_nonce", "approval_state", "approval_at"} <= jcols,
          "v6 jobs columns present")
    ecols = {row["name"] for row in cx.execute("PRAGMA table_info(job_events)")}
    check("topic" in ecols, "job_events.topic column present")
    idx = {r["name"] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    check("idx_events_topic" in idx, "idx_events_topic index present")

def _scratch_pnd():

    rt = tempfile.mkdtemp(prefix="pn_eb_rt_")
    data = tempfile.mkdtemp(prefix="pn_eb_data_")
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = rt
    env["XDG_DATA_HOME"] = data
    env["PN_DATA_DIR"] = data
    env["PN_DURABILITY"] = "normal"
    env.pop("NOTIFY_SOCKET", None)
    boot = os.path.join(rt, "boot.py")
    with open(boot, "w") as f:
        f.write(
            "import sys, runpy\n"
            f"sys.path.insert(0, {ROOT!r})\n"
            "from pnlib import sched\n"
            "_orig = sched.Config.autoscale\n"
            "def _permissive():\n"
            "    c = _orig(); c.psi_stop = 1e9; c.mem_floor = 1; c.batch_high = 1<<30\n"
            "    c.slack = 0; return c\n"
            "sched.Config.autoscale = staticmethod(_permissive)\n"
            f"runpy.run_path({os.path.join(ROOT, 'tools', 'pnd')!r}, run_name='__main__')\n")
    proc = subprocess.Popen([sys.executable, boot], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    sock = os.path.join(rt, "pnd.sock")
    for _ in range(50):
        if os.path.exists(sock):
            break
        time.sleep(0.1)

    def ipc(req, timeout=20):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout); s.connect(sock)
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            ch = s.recv(65536)
            if not ch:
                break
            buf += ch
        s.close()
        return json.loads(buf.split(b"\n", 1)[0].decode())
    return proc, rt, data, sock, ipc

def _subscribe_collect(sock, req, stop_evt, sink, timeout=30):

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout); s.connect(sock)
    s.sendall((json.dumps(req) + "\n").encode())
    buf = b""
    try:
        while not stop_evt.is_set():
            try:
                ch = s.recv(65536)
            except socket.timeout:
                break
            if not ch:
                break
            buf += ch
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    sink.append(json.loads(line.decode()))
    finally:
        s.close()

def test_live_canary():
    if not live_moeglich('test_live_canary'):
        return
    print("[g] LIVE: subscribe delivery + cross-principal isolation + steer + approve (scratch pnd)")
    proc, rt, data, sock, ipc = _scratch_pnd()
    try:
        check(ipc({"verb": "ping"}).get("ok"), "scratch pnd up")

        worker = os.path.join(rt, "worker.sh")
        with open(worker, "w") as f:
            f.write("#!/bin/bash\n"
                    "echo '@@PN_PROGRESS {\"done\":1,\"total\":3,\"msg\":\"step1\"}'\n"
                    "sleep 3\n"
                    "echo '@@PN_PROGRESS {\"done\":2,\"total\":3,\"msg\":\"step2\"}'\n"
                    "sleep 3\n"
                    "echo done\n")
        os.chmod(worker, 0o755)
        r = ipc({"verb": "submit", "cmd": [worker], "class": "worker", "tag": "bus-canary"})
        check(r.get("ok"), f"submit ok (id={r.get('id')})")
        jid = r["id"]

        admin_sink, admin_stop = [], threading.Event()
        ta = threading.Thread(target=_subscribe_collect,
                              args=(sock, {"verb": "subscribe", "topics": ["user/admin"],
                                           "after_id": 0}, admin_stop, admin_sink), daemon=True)
        ta.start()

        state = None
        for _ in range(120):
            jr = ipc({"verb": "job", "id": jid})
            if jr.get("ok"):
                state = jr["job"]["state"]
                if state in ("done", "failed", "cancelled", "timeout"):
                    break
            time.sleep(0.2)
        time.sleep(1.0)
        admin_stop.set(); ta.join(timeout=3)
        check(state == "done", f"job completed (state={state})")
        kinds = [m["event"]["kind"] for m in admin_sink if m.get("type") == "event"]
        check("state" in kinds and "progress" in kinds,
              f"admin subscriber received typed state+progress events ({sorted(set(kinds))})")
        got_terminal = any(m.get("type") == "event" and m["event"]["kind"] == "state"
                           and json.loads(m["event"]["data"]).get("state") == "done"
                           for m in admin_sink)
        check(got_terminal, "admin subscriber received the terminal `done` state event")
        acked = any(m.get("type") == "subscribed" for m in admin_sink)
        check(acked, "subscriber got the `subscribed` ack with its authorized topics")

        empty = db.connect

        cxv = __import__("sqlite3").connect(os.path.join(data, "portioneer", "queue.db"))
        cxv.row_factory = __import__("sqlite3").Row
        cxv.execute("INSERT OR IGNORE INTO principals(name,uid,kind) VALUES('bob',5002,'user')")
        cxv.commit()
        dropped = db.authorize_topics(cxv, "bob", ["user/admin", f"job/{jid}"])
        check(dropped == [], "a non-owner principal is DROPPED from user/admin + the admin job topic")
        cxv.close()

        reader = os.path.join(rt, "reader.sh")
        flag = os.path.join(rt, "steer-seen.txt")
        with open(reader, "w") as f:
            f.write("#!/bin/bash\n"
                    "for i in $(seq 1 60); do\n"
                    "  if [ -f \"$PN_WORKSPACE/work/steer/1.json\" ]; then\n"
                    f"    cat \"$PN_WORKSPACE/work/steer/1.json\" > {flag}; echo STEERED; exit 0\n"
                    "  fi\n"
                    "  sleep 0.2\n"
                    "done\n"
                    "echo NO_STEER; exit 1\n")
        os.chmod(reader, 0o755)
        r2 = ipc({"verb": "submit", "cmd": [reader], "class": "worker", "tag": "steer-canary"})
        sid = r2["id"]

        for _ in range(60):
            jr = ipc({"verb": "job", "id": sid})
            if jr.get("ok") and jr["job"]["state"] == "running":
                break
            time.sleep(0.2)
        sres = ipc({"verb": "steer", "id": sid, "input": {"hint": "go-left"}})
        check(sres.get("ok") and sres.get("seq") == 1, f"steer accepted (seq={sres.get('seq')})")
        sstate = None
        for _ in range(80):
            jr = ipc({"verb": "job", "id": sid})
            if jr.get("ok"):
                sstate = jr["job"]["state"]
                if sstate in ("done", "failed", "cancelled", "timeout"):
                    break
            time.sleep(0.2)
        seen = ""
        try:
            seen = open(flag).read()
        except OSError:
            pass
        check(sstate == "done" and "go-left" in seen,
              f"steer reached the running worker (state={sstate}, saw={seen!r})")

        r3 = ipc({"verb": "submit", "cmd": ["/bin/echo", "approved-run"], "class": "worker",
                  "tag": "approve-canary", "needs_confirmation": True})
        check(r3.get("ok") and r3.get("state") == "staged" and r3.get("nonce"),
              f"needs_confirmation submit -> staged with nonce (state={r3.get('state')})")
        nonce = r3["nonce"]; aid = r3["id"]

        ar1 = ipc({"verb": "approve", "nonce": nonce})
        ar2 = ipc({"verb": "approve", "nonce": nonce})
        check(ar1.get("ok") and ar1.get("state") == "queued", "live approve -> queued")
        check(ar2.get("ok") and ar2.get("idempotent"), "live re-approve -> idempotent no-op")
        astate = None
        for _ in range(80):
            jr = ipc({"verb": "job", "id": aid})
            if jr.get("ok"):
                astate = jr["job"]["state"]
                if astate in ("done", "failed", "cancelled", "timeout"):
                    break
            time.sleep(0.2)
        lg = ipc({"verb": "log", "id": aid})
        check(astate == "done" and "approved-run" in (lg.get("stdout") or ""),
              f"approved job ran end-to-end (state={astate})")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        try:
            import sqlite3
            cxv = sqlite3.connect(os.path.join(data, "portioneer", "queue.db"))
            ic = cxv.execute("PRAGMA integrity_check").fetchone()[0]
            cxv.close()
            check(ic == "ok", f"scratch DB integrity_check = {ic!r}")
        except Exception as e:
            check(False, f"scratch DB integrity_check raised {e}")
        subprocess.run(["rm", "-rf", rt, data])

def main():
    print("=== P2b one-contract + typed event bus + cockpit unification — test suite ===")
    cx, path = fresh_db()
    try:
        ja = test_typed_fanout(cx)
        jb = test_topic_authz(cx, ja)
        test_replay_converge(cx, ja)
        test_cvm(cx, ja)
        test_approve_deny_idempotent(cx)
        test_group_topic_isolation(cx)
        test_integrity(cx)
    finally:
        cx.close()
        for p in (path, path + "-wal", path + "-shm"):
            try:
                os.unlink(p)
            except OSError:
                pass
    test_live_canary()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
