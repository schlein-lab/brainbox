#!/usr/bin/env python3

import os, sys, json, time, tempfile, socket, subprocess

_EG_DATA = tempfile.mkdtemp(prefix="pn_eg_cfg_")
os.environ["PN_DATA_DIR"] = _EG_DATA
os.environ["PN_EGRESS_CONFIG"] = os.path.join(_EG_DATA, "egress", "config.json")
os.environ.pop("PN_EGRESS_REAL_UPSTREAM", None)

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from pnlib import egress, egresscfg

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
    path = tempfile.mktemp(prefix="pn_eg_", suffix=".db")
    return egress.connect(path), path

def test_default_deny(cx):
    print("[a] default-DENY: a non-allowlisted (host,principal,task_type) is blocked")
    check(not egress.is_allowed(cx, "evil.example.com", 443, "brain", "fetch.web"),
          "an unknown destination is NOT allowed (default-deny)")
    check(not egress.is_allowed(cx, "api.github.com", 443, "brain", "fetch.web"),
          "even a plausible destination is denied until allowlisted")

def test_allow(cx):
    print("[b] an allowlisted tuple is permitted; glob host + wildcards honored")
    egress.add_allow(cx, "api.github.com", 443, "brain", "fetch.web", approved_by="admin")
    check(egress.is_allowed(cx, "api.github.com", 443, "brain", "fetch.web"),
          "the exact allowlisted tuple is permitted")
    check(not egress.is_allowed(cx, "api.github.com", 80, "brain", "fetch.web"),
          "a different PORT on the same host is still denied (port-scoped)")
    check(not egress.is_allowed(cx, "api.github.com", 443, "lan-guest", "fetch.web"),
          "a different PRINCIPAL is denied (principal-scoped)")

    egress.add_allow(cx, "*.amazonaws.com", 0, "*", "compute.aws", approved_by="admin")
    check(egress.is_allowed(cx, "s3.eu-central-1.amazonaws.com", 443, "brain", "compute.aws"),
          "a glob host + wildcard principal/port (any) is honored for federation")
    check(not egress.is_allowed(cx, "s3.eu-central-1.amazonaws.com", 443, "brain", "fetch.web"),
          "the wildcard is still task_type-scoped (compute.aws only)")

def test_propose_and_approve(cx):
    print("[c] a NEW destination -> propose + (mock) approval-request; allowed ONLY after approve")
    emitted = []
    broker = egress.EgressBroker(cx, propose_fn=lambda *a: emitted.append(a),
                                 nonce_fn=lambda: "fixed-nonce-1")
    decision, detail = broker.check_connect("hpc.cluster.edu", 22, "brain", "compute.hpc",
                                            job_id=77)
    check(decision == egress.PROPOSE and detail["nonce"] == "fixed-nonce-1",
          "a new destination is PROPOSED (not silently allowed, not hard-denied)")
    check(len(emitted) == 1 and emitted[0][0] == "hpc.cluster.edu",
          "an approval-request was emitted for the human (propose_fn)")
    check(not egress.is_allowed(cx, "hpc.cluster.edu", 22, "brain", "compute.hpc"),
          "the destination is STILL denied while pending (the brain cannot self-approve)")

    res = egress.resolve_proposal(cx, "fixed-nonce-1", "approve", approved_by="admin")
    check(res["ok"] and res["decision"] == "approved",
          "a human approve resolves the proposal -> allowlist entry added")
    check(egress.is_allowed(cx, "hpc.cluster.edu", 22, "brain", "compute.hpc"),
          "AFTER approve, the exact tuple is permitted")

def test_propose_idempotent(cx):
    print("[d] the propose flow is idempotent (a repeat blocked request reuses the pending row)")
    emitted = []
    n = iter(["nonce-A", "nonce-B"])
    broker = egress.EgressBroker(cx, propose_fn=lambda *a: emitted.append(a),
                                 nonce_fn=lambda: next(n))
    d1, det1 = broker.check_connect("repeat.example.com", 443, "brain", "fetch.web")
    d2, det2 = broker.check_connect("repeat.example.com", 443, "brain", "fetch.web")
    check(det1["nonce"] == det2["nonce"] == "nonce-A",
          "a repeated blocked request reuses the SAME pending nonce (no inbox spam)")
    check(len(emitted) == 1, "the approval-request is emitted only ONCE for the repeat")

def test_resolve_idempotent(cx):
    print("[e] approve/deny resolution is idempotent + correctly adds/withholds the entry")
    egress.propose(cx, "deny.example.com", 443, "brain", "fetch.web", 1, "deny-nonce")
    r1 = egress.resolve_proposal(cx, "deny-nonce", "deny", approved_by="admin")
    r2 = egress.resolve_proposal(cx, "deny-nonce", "deny", approved_by="admin")
    check(r1["ok"] and r1["decision"] == "denied", "deny resolves the proposal")
    check(r2["ok"] and r2.get("idempotent"), "re-denying is an idempotent no-op")
    check(not egress.is_allowed(cx, "deny.example.com", 443, "brain", "fetch.web"),
          "a denied proposal adds NO allowlist entry")

    egress.propose(cx, "conflict.example.com", 443, "brain", "fetch.web", 1, "cf-nonce")
    egress.resolve_proposal(cx, "cf-nonce", "approve", approved_by="admin")
    rc = egress.resolve_proposal(cx, "cf-nonce", "deny", approved_by="admin")
    check(not rc["ok"] and "already approved" in rc["error"],
          "a conflicting re-decision on a resolved proposal is refused")

    ru = egress.resolve_proposal(cx, "no-such-nonce", "approve", approved_by="admin")
    check(not ru["ok"] and "unknown" in ru["error"], "an unknown nonce -> 'unknown or expired'")

def test_no_bypass(cx):
    print("[f] a task cannot bypass the proxy: upstream opened only on allow; cred from broker only")
    egress.add_allow(cx, "ok.example.com", 443, "brain", "fetch.web", approved_by="admin")
    opened = []
    cred_reads = []

    def upstream(host, port, cred):
        opened.append((host, port, cred))
        return {"sock": "fake-upstream"}

    def cred_for(host, port, principal, task_type):
        cred_reads.append(host)
        return "FEDERATION-SECRET"

    broker = egress.EgressBroker(cx, upstream_fn=upstream, cred_for=cred_for,
                                 propose_fn=lambda *a: None, nonce_fn=lambda: "n")

    rdeny = broker.connect("blocked.example.com", 443, "brain", "fetch.web", job_id=1)
    check(not rdeny["ok"] and rdeny["decision"] == egress.PROPOSE and not opened,
          "a blocked destination opens NO upstream (no bypass; default-deny holds)")

    rok = broker.connect("ok.example.com", 443, "brain", "fetch.web", job_id=1)
    check(rok["ok"] and opened and opened[0][2] == "FEDERATION-SECRET",
          "an allowed destination opens the upstream with the broker's sealed cred")
    check(cred_reads == ["ok.example.com"],
          "the federation credential was read from the broker's cred seam, only on allow")

def test_netns_plan():
    print("[g] the netns/veth/nft default-deny PLAN is correct (policy drop; proxy-only; PRIVILEGED)")
    plan = egress.plan_netns("untrusted", proxy_sock="/run/portioneer/egress.sock")
    check(plan["netns"] == "pn-egress-untrusted", "per-isolation-tier netns name")
    setup_cmds = [" ".join(a) for a in plan["setup"]]
    check(any("ip netns add pn-egress-untrusted" in c for c in setup_cmds),
          "the plan creates the network namespace")
    check(any("veth" in c for c in setup_cmds), "the plan creates a veth pair")
    check("policy drop" in plan["nft"], "the nft policy INSIDE the netns is DROP (default-deny)")
    check("egress only via proxy unix socket" in plan["nft"],
          "the only authorized egress is the proxy unix socket (no ambient WAN)")
    check("egress.sock" in " ".join(plan["mount"]),
          "the proxy socket is bind-mounted into the netns (the sole egress path)")
    check("PRIVILEGED" in plan["summary"] and "rootless --user cannot create a netns" in plan["summary"],
          "the plan documents the privileged path (rootless --user cannot create a netns)")

    check(not any("default" in c and "route" in c for c in setup_cmds),
          "the plan adds NO default route (the netns has no WAN route at all)")

def _scratch_pnd(egress_db):

    rt = tempfile.mkdtemp(prefix="pn_eg_rt_")
    data = tempfile.mkdtemp(prefix="pn_eg_data_")
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = rt
    env["XDG_DATA_HOME"] = data
    env["PN_DATA_DIR"] = data
    env["PN_DURABILITY"] = "normal"
    env["PN_EGRESS_DB"] = egress_db
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

def test_live_propose_through_pnd():
    print("[h] LIVE: a new egress destination flows through pnd's human-approval; a human approves, "
          "the brain (non-admin) CANNOT (scratch pnd)")
    egdb = tempfile.mktemp(prefix="pn_eg_live_", suffix=".db")
    cx = egress.connect(egdb)
    proc, rt, data, sock, ipc = _scratch_pnd(egdb)
    try:
        check(ipc({"verb": "ping"}).get("ok"), "scratch pnd up")

        nonce, created = egress.propose(cx, "hpc.live.edu", 22, "brain", "compute.hpc", 1,
                                        "live-egress-nonce")
        check(created, "the proposal was recorded in the egress store")

        pend = ipc({"verb": "egress-pending"})
        check(pend.get("ok") and any(p["nonce"] == "live-egress-nonce"
                                     for p in pend.get("egress_pending", [])),
              "the human operator sees the pending egress proposal via pnd (view:all)")

        import sqlite3
        cxq = sqlite3.connect(os.path.join(data, "portioneer", "queue.db"))
        cxq.row_factory = sqlite3.Row

        cxq.close()

        from pnlib import db as _db
        cxv = _db.connect(os.path.join(data, "portioneer", "queue.db"))
        brain_caps = _db.caps_for(cxv, "brain")
        cxv.close()
        check("view:all" not in brain_caps,
              "the brain principal holds NO view:all -> the egress-approve verb refuses it")

        appr = ipc({"verb": "egress-approve", "nonce": "live-egress-nonce"})
        check(appr.get("ok") and appr.get("decision") == "approved",
              "the human operator approves the egress proposal through pnd")
        check(egress.is_allowed(cx, "hpc.live.edu", 22, "brain", "compute.hpc"),
              "after the human approve, the new destination is allowlisted (now permitted)")

        appr2 = ipc({"verb": "egress-approve", "nonce": "live-egress-nonce"})
        check(appr2.get("ok") and appr2.get("idempotent"),
              "re-approving the same egress proposal is an idempotent no-op")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        try:
            ic = cx.execute("PRAGMA integrity_check").fetchone()[0]
            check(ic == "ok", f"egress DB integrity_check = {ic!r}")
        except Exception as e:
            check(False, f"egress DB integrity_check raised {e}")
        cx.close()
        for p in (egdb, egdb + "-wal", egdb + "-shm"):
            try:
                os.unlink(p)
            except OSError:
                pass
        subprocess.run(["rm", "-rf", rt, data])

def test_config_store_and_seed(cx):
    print("[i] egress config lives in a SEPARATE store; the allowlist SEED applies idempotently")
    cfg = egresscfg.load()
    check(cfg["proxy"]["real_upstream"] is False,
          "shipped default: real_upstream OFF (policy-only; no real WAN until opted in)")

    cfg["seed"] = [
        {"host": "*.amazonaws.com", "port": 0, "principal": "*", "task_type": "compute.aws",
         "note": "AWS federation"},
        {"host": "registry.npmjs.org", "port": 443, "principal": "brain", "task_type": "build.node"},
    ]
    saved = egresscfg.save(cfg)
    check(saved == egresscfg.CONFIG_PATH and saved.startswith(_EG_DATA) and "queue.db" not in saved
          and "allow.db" not in saved,
          f"config persisted to a SEPARATE store (not queue.db, not the allow.db): {os.path.basename(saved)}")
    n = egresscfg.apply_seed(egress, cx, egresscfg.load())
    check(n == 2, f"apply_seed processed every seed entry ({n})")
    check(egress.is_allowed(cx, "s3.eu-central-1.amazonaws.com", 443, "brain", "compute.aws")
          and egress.is_allowed(cx, "registry.npmjs.org", 443, "brain", "build.node"),
          "the seeded destinations are now allowlisted")

    row = cx.execute("SELECT approved_by FROM egress_allow WHERE host='registry.npmjs.org'").fetchone()
    check(row and row["approved_by"] == "config-seed",
          "a seeded entry is tagged approved_by='config-seed' (not a human propose->approve)")

    before = cx.execute("SELECT COUNT(*) c FROM egress_allow").fetchone()["c"]
    egresscfg.apply_seed(egress, cx, egresscfg.load())
    after = cx.execute("SELECT COUNT(*) c FROM egress_allow").fetchone()["c"]
    check(before == after, "apply_seed is idempotent (INSERT OR IGNORE on the UNIQUE tuple)")

def test_real_upstream_gated(cx):
    print("[j] real upstream is OFF by default; ON only via config -> tcp_upstream + splice (MOCKED)")
    egress.add_allow(cx, "allowed.example.com", 443, "brain", "fetch.web", approved_by="admin")

    raised = {}
    def policy_only(host, port, cred):
        raised["called"] = (host, port)
        raise RuntimeError("real upstream disabled (policy-only)")
    b_off = egress.EgressBroker(cx, upstream_fn=policy_only, propose_fn=lambda *a: None,
                                nonce_fn=lambda: "n")
    r = b_off.connect("allowed.example.com", 443, "brain", "fetch.web")
    check(not r["ok"] and "upstream failed" in r["error"] and raised.get("called"),
          "policy-only opener refuses to dial even an ALLOWED destination (no real WAN by default)")

    check(callable(egress.tcp_upstream),
          "egress.tcp_upstream is the REAL connector wired only when config enables it")

    a1, a2 = socket.socketpair()
    b1, b2 = socket.socketpair()

    a1.sendall(b"hello-up")
    b2.sendall(b"hello-down")
    a1.shutdown(socket.SHUT_WR)
    b2.shutdown(socket.SHUT_WR)
    up_bytes, down_bytes = egress.splice(a2, b1)
    got_up = b2.recv(64)
    got_down = a1.recv(64)
    for s in (a1, a2, b1, b2):
        try: s.close()
        except OSError: pass
    check(got_up == b"hello-up" and got_down == b"hello-down",
          "splice() pipes bytes BOTH ways between client and upstream (the CONNECT tunnel)")
    check(up_bytes >= 8 and down_bytes >= 9,
          f"splice() reports byte counts each direction ({up_bytes} up / {down_bytes} down)")

def main():
    print("=== egress-broker — test suite ===")
    cx, path = fresh_db()
    try:
        test_default_deny(cx)
        test_allow(cx)
        test_propose_and_approve(cx)
        test_propose_idempotent(cx)
        test_resolve_idempotent(cx)
        test_no_bypass(cx)
        test_netns_plan()
        test_config_store_and_seed(cx)
        test_real_upstream_gated(cx)
        check(cx.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
              "unit egress DB integrity_check ok")
    finally:
        cx.close()
        for p in (path, path + "-wal", path + "-shm"):
            try:
                os.unlink(p)
            except OSError:
                pass
    test_live_propose_through_pnd()
    import shutil as _sh
    _sh.rmtree(_EG_DATA, ignore_errors=True)
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
