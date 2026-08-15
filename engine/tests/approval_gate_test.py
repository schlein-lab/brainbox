#!/usr/bin/env python3

import io, os, sys, json, time, tempfile, importlib.util, socket, subprocess, threading
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
    path = tempfile.mktemp(prefix="pn_ag_", suffix=".db")
    cx = db.connect(path)
    for name, uid in (("alice", 5001), ("bob", 5002)):
        cx.execute("INSERT OR IGNORE INTO principals(name,uid,kind,note) VALUES(?,?,?,?)",
                   (name, uid, "user", f"test tenant {name}"))
    for principal, cap in (("alice", "task_type:review.post"),
                           ("alice", "task_type:deploy.irreversible"),
                           ("alice", "task_type:echo.test"),
                           ("bob", "task_type:review.post")):
        if not cx.execute("SELECT 1 FROM grants WHERE principal=? AND cap=?",
                          (principal, cap)).fetchone():
            cx.execute("INSERT INTO grants(principal,cap) VALUES(?,?)", (principal, cap))
    cx.commit()
    return cx, path

def test_schema(cx):
    print("[a] v7 schema migration + the seeded approval gates")
    jcols = {r["name"] for r in cx.execute("PRAGMA table_info(jobs)")}
    check({"approval_kind", "revise_count", "revise_max", "held_state", "held_exit_code"} <= jcols,
          "v7 jobs columns present")
    ttcols = {r["name"] for r in cx.execute("PRAGMA table_info(task_types)")}
    check("approval" in ttcols, "task_types.approval column present")
    check(db.get_task_type(cx, "review.post")["approval"] == "post", "review.post is a POST gate")
    check(db.get_task_type(cx, "deploy.irreversible")["approval"] == "pre",
          "deploy.irreversible is a PRE gate")

    check(db.get_task_type(cx, "commission.build")["approval"] == "none",
          "commission.build ist Housekeeping-Default (keine Freigabe)")
    check(int(db.get_task_type(cx, "commission.build")["needs_confirmation"]) == 0,
          "commission.build needs_confirmation=0 (laeuft sofort)")
    check(cx.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "integrity_check ok")

def test_hold_state(cx):
    print("[b] hold_for_approval parks a FINISHED job awaiting_approval (distinct from staged)")
    jid = db.submit(cx, ["/bin/echo", "r"], "/tmp", {}, "{}", 100, 64, "post-job",
                    principal="alice", task_type="review.post")

    db.set_record(cx, jid, record_commit="deadbeef", result_hash="a" * 64)
    db.hold_for_approval(cx, jid, "post-nonce-1", "done", 0, approval_kind="post")
    row = cx.execute("SELECT state, approval_state, approval_kind, held_state, held_exit_code, "
                     "record_commit FROM jobs WHERE id=?", (jid,)).fetchone()
    check(row["state"] == "awaiting_approval", "post job parks `awaiting_approval` (not done)")
    check(row["state"] != "staged", "awaiting_approval is DISTINCT from the pre-dispatch staged")
    check(row["approval_state"] == "pending" and row["approval_kind"] == "post",
          "approval_state=pending, approval_kind=post")
    check(row["held_state"] == "done" and row["held_exit_code"] == 0,
          "the would-be terminal verdict is HELD on the row")
    check(row["record_commit"] == "deadbeef", "the built Record digest is on the row pre-approval")
    return jid

def test_three_decisions(cx):
    print("[c] resolve_approval on a post gate: approve / reject / revise (separation of duties)")

    j1 = db.submit(cx, ["/bin/echo", "x"], "/tmp", {}, "{}", 100, 64, "p1",
                   principal="alice", task_type="review.post")
    db.hold_for_approval(cx, j1, "n-approve", "done", 0, approval_kind="post")

    rself = db.resolve_approval(cx, "n-approve", "approve", principal="alice")
    check(not rself["ok"] and "self-approve" in rself["error"],
          "submitter self-approve REFUSED (separation of duties)")

    r = db.resolve_approval(cx, "n-approve", "approve", principal="operator", scope_all=True)
    check(r["ok"] and r.get("finalize") and r["held_state"] == "done",
          "authorized operator approve -> ok + finalize flag + held verdict (done)")
    check(cx.execute("SELECT approval_state FROM jobs WHERE id=?", (j1,)).fetchone()[0]
          == "approved", "gate marked approved")
    check(cx.execute("SELECT approved_by FROM jobs WHERE id=?", (j1,)).fetchone()[0]
          == "operator", "approved_by records the RESOLVER (separation-of-duties audit)")

    r2 = db.resolve_approval(cx, "n-approve", "approve", principal="operator", scope_all=True)
    check(r2["ok"] and r2.get("idempotent"), "re-approve -> idempotent no-op success")

    j2 = db.submit(cx, ["/bin/echo", "y"], "/tmp", {}, "{}", 100, 64, "p2",
                   principal="alice", task_type="review.post")
    db.set_record(cx, j2, record_commit="c2", result_hash="b" * 64)
    db.hold_for_approval(cx, j2, "n-reject", "done", 0, approval_kind="post")
    rj = db.resolve_approval(cx, "n-reject", "reject", principal="operator", scope_all=True)
    check(rj["ok"] and rj["state"] == "rejected" and rj["decision"] == "rejected",
          "operator reject -> terminal `rejected`")
    row = cx.execute("SELECT finished_at FROM jobs WHERE id=?", (j2,)).fetchone()
    check(row["finished_at"] is not None, "rejected job has finished_at set (retention still governs)")

    rj2 = db.resolve_approval(cx, "n-reject", "approve", principal="operator", scope_all=True)
    check(not rj2["ok"] and "already rejected" in rj2["error"], "approve after reject refused")

    j3 = db.submit(cx, ["/bin/echo", "z"], "/tmp", {}, "{}", 100, 64, "p3",
                   principal="alice", task_type="review.post")
    db.hold_for_approval(cx, j3, "n-revise", "done", 0, approval_kind="post")
    rv = db.resolve_approval(cx, "n-revise", "revise", principal="alice", feedback="tighten it")
    check(rv["ok"] and rv["state"] == "queued" and rv["revise_count"] == 1,
          "submitter revise (no side effect) -> re-queued, revise_count=1")
    row = cx.execute("SELECT state, approval_state, approval_nonce, revise_count "
                     "FROM jobs WHERE id=?", (j3,)).fetchone()
    check(row["state"] == "queued" and row["approval_state"] is None
          and row["approval_nonce"] is None,
          "revise clears the gate so the re-run can re-park awaiting_approval")
    return j3

def test_bounded_revise(cx):
    print("[d] the revise loop is BOUNDED (revise_max)")
    jid = db.submit(cx, ["/bin/echo", "b"], "/tmp", {}, "{}", 100, 64, "loop",
                    principal="alice", task_type="review.post")

    cx.execute("UPDATE jobs SET revise_max=2 WHERE id=?", (jid,)); cx.commit()
    for i in range(2):
        db.hold_for_approval(cx, jid, f"n-loop-{i}", "done", 0, approval_kind="post")
        r = db.resolve_approval(cx, f"n-loop-{i}", "revise", principal="alice", feedback=f"r{i}")
        check(r["ok"] and r["revise_count"] == i + 1, f"revise {i+1}/2 accepted")

    db.hold_for_approval(cx, jid, "n-loop-final", "done", 0, approval_kind="post")
    r = db.resolve_approval(cx, "n-loop-final", "revise", principal="alice", feedback="again")
    check(not r["ok"] and "exhausted" in r["error"], "revise past revise_max is refused")

    rself = db.resolve_approval(cx, "n-loop-final", "approve", principal="alice")
    check(not rself["ok"] and "self-approve" in rself["error"],
          "submitter still cannot self-approve after the revise loop is exhausted")
    ra = db.resolve_approval(cx, "n-loop-final", "approve", principal="operator", scope_all=True)
    check(ra["ok"] and ra.get("finalize"),
          "authorized operator approve still works after the revise loop is exhausted")

def test_cross_tenant(cx):
    print("[e] cross-tenant: B can NOT resolve A's post-gate job (no nonce oracle)")
    jid = db.submit(cx, ["/bin/echo", "x"], "/tmp", {}, "{}", 100, 64, "xt",
                    principal="alice", task_type="review.post")
    db.hold_for_approval(cx, jid, "n-xt", "done", 0, approval_kind="post")
    for dec in ("approve", "reject", "revise"):
        r = db.resolve_approval(cx, "n-xt", dec, principal="bob", feedback="x")
        check(not r["ok"] and "unknown" in r["error"],
              f"bob {dec} on alice's nonce -> unknown nonce (no oracle)")

    rself = db.resolve_approval(cx, "n-xt", "approve", principal="alice")
    check(not rself["ok"] and "self-approve" in rself["error"],
          "owner alice CANNOT self-approve (separation of duties)")

    ra = db.resolve_approval(cx, "n-xt", "approve", principal="operator", scope_all=True)
    check(ra["ok"] and ra.get("finalize"),
          "an authorized operator (view:all/approval:resolve) approves successfully")

def test_pre_gate_unbroken(cx):
    print("[f] the v6 PRE-dispatch gate is UNBROKEN (approve->queued, deny->cancelled, idempotent)")
    j1 = db.submit(cx, ["/bin/echo", "x"], "/tmp", {}, "{}", 100, 64, "pre1",
                   principal="alice", task_type="echo.test")
    db.stage_for_approval(cx, j1, "pre-n1", approval_kind="pre")
    row = cx.execute("SELECT state, approval_state FROM jobs WHERE id=?", (j1,)).fetchone()
    check(row["state"] == "staged" and row["approval_state"] == "pending", "pre gate parks staged")

    rself = db.resolve_approval(cx, "pre-n1", "approve", principal="alice")
    check(not rself["ok"] and "self-approve" in rself["error"],
          "pre gate: submitter self-approve REFUSED (separation of duties)")
    r = db.resolve_approval(cx, "pre-n1", "approve", principal="operator", scope_all=True)
    check(r["ok"] and r["state"] == "queued" and not r.get("idempotent"),
          "authorized operator pre approve -> queued")
    r2 = db.resolve_approval(cx, "pre-n1", "approve", principal="operator", scope_all=True)
    check(r2["ok"] and r2["idempotent"], "pre re-approve -> idempotent no-op")
    j2 = db.submit(cx, ["/bin/echo", "y"], "/tmp", {}, "{}", 100, 64, "pre2",
                   principal="alice", task_type="echo.test")
    db.stage_for_approval(cx, j2, "pre-n2", approval_kind="pre")
    rd = db.resolve_approval(cx, "pre-n2", "deny", principal="operator", scope_all=True)
    check(rd["ok"] and rd["state"] == "cancelled", "operator pre deny -> cancelled")

def test_inbox(cx):
    print("[g] pending_approvals inbox: per-principal + carries the approval-request payload")
    j = db.submit(cx, ["/bin/echo", "i"], "/tmp", {}, "{}", 100, 64, "inbox",
                  principal="alice", task_type="review.post")
    db.set_record(cx, j, record_commit="cc", result_hash="d" * 64)
    db.hold_for_approval(cx, j, "n-inbox", "done", 0, approval_kind="post")
    db.add_typed_event(cx, j, "approval-request",
                       {"job_id": j, "nonce": "n-inbox", "gate": "post",
                        "summary": "review me", "result": {"result_hash": "d" * 64}})
    inbox = db.pending_approvals(cx, principal="alice")
    mine = [p for p in inbox if p["id"] == j]
    check(len(mine) == 1, "alice's job appears in her pending inbox")
    check(mine[0]["approval_request"] and mine[0]["approval_request"]["summary"] == "review me",
          "inbox item carries the approval-request review payload")
    bob_inbox = db.pending_approvals(cx, principal="bob")
    check(all(p["id"] != j for p in bob_inbox), "bob's inbox does NOT contain alice's pending job")

    admin_inbox = db.pending_approvals(cx, scope_all=True)
    check(any(p["id"] == j for p in admin_inbox), "admin view:all sees the pending job")

def _scratch_pnd():

    rt = tempfile.mkdtemp(prefix="pn_ag_rt_")
    data = tempfile.mkdtemp(prefix="pn_ag_data_")
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

def _wait_state(ipc, jid, want, tries=120, every=0.2):
    st = None
    for _ in range(tries):
        jr = ipc({"verb": "job", "id": jid})
        if jr.get("ok"):
            st = jr["job"]["state"]
            if (want(st) if callable(want) else st == want):
                return st
        time.sleep(every)
    return st

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

def test_live_post_gate():
    if not live_moeglich('test_live_post_gate'):
        return
    print("[h/i/l] LIVE: post-gate job parks awaiting_approval WITH the artifact; approve->done; "
          "the bus carries it; non-owner dropped (scratch pnd)")
    proc, rt, data, sock, ipc = _scratch_pnd()
    try:
        check(ipc({"verb": "ping"}).get("ok"), "scratch pnd up")

        worker = os.path.join(rt, "post_worker.sh")
        with open(worker, "w") as f:
            f.write("#!/bin/bash\n"
                    "echo 'the produced result' > \"$PN_WORKSPACE/artifacts/result.txt\"\n"
                    "echo done\n")
        os.chmod(worker, 0o755)

        sink, stop = [], threading.Event()
        ts = threading.Thread(target=_subscribe_collect,
                              args=(sock, {"verb": "subscribe", "topics": ["user/admin"],
                                           "after_id": 0}, stop, sink), daemon=True)
        ts.start()

        r = ipc({"verb": "submit", "cmd": [worker], "class": "worker", "tag": "post-canary",
                 "approval": "post"})
        check(r.get("ok"), f"submit ok (id={r.get('id')})")
        jid = r["id"]
        st = _wait_state(ipc, jid, "awaiting_approval")
        check(st == "awaiting_approval", f"post job parked awaiting_approval (state={st})")

        cvm = ipc({"verb": "cvm", "id": jid}).get("cvm", {})
        check(cvm.get("awaiting_approval") and cvm.get("approval_kind") == "post",
              "CVM reports awaiting_approval + gate kind=post")
        req_ev = cvm.get("approval_request") or {}
        res = req_ev.get("result") or {}
        check(bool(res.get("result_hash")) and bool(res.get("record_commit")),
              "approval-request carries the result DIGEST (hash + commit)")
        prev = req_ev.get("preview") or {}
        arts = prev.get("artifacts") or []
        check(any(a["path"].endswith("result.txt") for a in arts),
              "approval-request preview lists the produced artifact")
        pend = ipc({"verb": "pending"}).get("pending", [])
        check(any(p["id"] == jid for p in pend), "the job appears in the pending-approvals inbox")
        nonce = req_ev.get("nonce")
        check(bool(nonce), "approval-request carries the nonce")

        import sqlite3
        cxv = sqlite3.connect(os.path.join(data, "portioneer", "queue.db"))
        cxv.row_factory = sqlite3.Row
        cxv.execute("INSERT OR IGNORE INTO principals(name,uid,kind) VALUES('bob',5002,'user')")
        cxv.commit()
        dropped = db.authorize_topics(cxv, "bob", ["user/admin", f"job/{jid}"])
        cxv.close()
        check(dropped == [], "a non-owner is DROPPED from the owner's bus topics (no oracle)")

        a1 = ipc({"verb": "approve", "nonce": nonce})
        a2 = ipc({"verb": "approve", "nonce": nonce})
        check(a1.get("ok") and a1.get("state") == "done", f"live approve -> done (state={a1.get('state')})")
        check(a2.get("ok") and a2.get("idempotent"), "live re-approve -> idempotent no-op")
        j = ipc({"verb": "job", "id": jid})["job"]
        check(j["state"] == "done" and j["record_ok"] == 1,
              "approved post job is done WITH record_ok=1 (no-done-without-record)")
        time.sleep(0.8); stop.set(); ts.join(timeout=3)
        kinds = [m["event"]["kind"] for m in sink if m.get("type") == "event"]
        check("approval-request" in kinds, "the bus carried the approval-request")
        got_done = any(m.get("type") == "event" and m["event"]["kind"] == "state"
                       and json.loads(m["event"]["data"]).get("state") == "done" for m in sink)
        check(got_done, "the bus carried the terminal `done` state after approval")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        subprocess.run(["rm", "-rf", rt, data])

def test_live_reject_retains():
    if not live_moeglich('test_live_reject_retains'):
        return
    print("[i] LIVE: reject -> terminal `rejected` AND the artifact is RETAINED (scratch pnd)")
    proc, rt, data, sock, ipc = _scratch_pnd()
    try:
        worker = os.path.join(rt, "rej_worker.sh")
        with open(worker, "w") as f:
            f.write("#!/bin/bash\necho keep-me > \"$PN_WORKSPACE/artifacts/a.txt\"\necho done\n")
        os.chmod(worker, 0o755)
        r = ipc({"verb": "submit", "cmd": [worker], "class": "worker", "tag": "rej",
                 "approval": "post"})
        jid = r["id"]
        _wait_state(ipc, jid, "awaiting_approval")
        nonce = (ipc({"verb": "cvm", "id": jid}).get("cvm", {}).get("approval_request") or {}).get("nonce")
        rj = ipc({"verb": "reject", "nonce": nonce})
        check(rj.get("ok") and rj.get("state") == "rejected", f"live reject -> rejected ({rj.get('state')})")
        j = ipc({"verb": "job", "id": jid})["job"]
        ws = j.get("workspace_path")
        check(bool(ws) and os.path.isdir(ws), "rejected job's workspace is RETAINED on disk")
        check(os.path.isfile(os.path.join(ws, "artifacts", "a.txt")),
              "the produced artifact survives the rejection (not silently lost)")

        w = ipc({"verb": "whereis", "id": jid})
        check(w.get("ok") and w["whereis"]["status"] == "rejected", "whereis reports status=rejected")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        subprocess.run(["rm", "-rf", rt, data])

def test_live_revise():
    if not live_moeglich('test_live_revise'):
        return
    print("[j] LIVE: revise(feedback) reaches the worker via steer; job re-runs then approves (scratch pnd)")
    proc, rt, data, sock, ipc = _scratch_pnd()
    try:

        worker = os.path.join(rt, "rev_worker.sh")
        seen = os.path.join(rt, "feedback-seen.txt")
        with open(worker, "w") as f:
            f.write("#!/bin/bash\n"
                    "for s in \"$PN_WORKSPACE\"/work/steer/*.json; do\n"
                    "  [ -f \"$s\" ] || continue\n"
                    f"  cat \"$s\" >> {seen}\n"
                    "done\n"
                    "echo result > \"$PN_WORKSPACE/artifacts/out.txt\"\n"
                    "echo done\n")
        os.chmod(worker, 0o755)
        r = ipc({"verb": "submit", "cmd": [worker], "class": "worker", "tag": "rev",
                 "approval": "post"})
        jid = r["id"]
        _wait_state(ipc, jid, "awaiting_approval")
        nonce1 = (ipc({"verb": "cvm", "id": jid}).get("cvm", {}).get("approval_request") or {}).get("nonce")
        rv = ipc({"verb": "revise", "nonce": nonce1, "feedback": "please-redo-bigger"})
        check(rv.get("ok") and rv.get("state") == "queued" and rv.get("revise_count") == 1,
              f"live revise -> re-queued (revise_count={rv.get('revise_count')})")

        st = _wait_state(ipc, jid, "awaiting_approval")
        check(st == "awaiting_approval", "the revised job re-ran and re-parked awaiting_approval")
        fb = ""
        try:
            fb = open(seen).read()
        except OSError:
            pass
        check("please-redo-bigger" in fb,
              f"the reviewer's feedback reached the re-running worker via steer (saw={fb!r})")

        cvm = ipc({"verb": "cvm", "id": jid}).get("cvm", {})
        nonce2 = (cvm.get("approval_request") or {}).get("nonce")
        check(nonce2 and nonce2 != nonce1, "the re-parked job has a FRESH nonce")
        ar = ipc({"verb": "approve", "nonce": nonce2})
        check(ar.get("ok") and ar.get("state") == "done", "approving the revised result -> done")
        check(ipc({"verb": "cvm", "id": jid})["cvm"]["revise"]["count"] == 1,
              "the CVM records the consumed revise loop (count=1)")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        subprocess.run(["rm", "-rf", rt, data])

def test_live_pre_side_effect():
    if not live_moeglich('test_live_pre_side_effect'):
        return
    print("[k] LIVE: an approval:pre IRREVERSIBLE action does NOT fire pre-approval (scratch pnd)")
    proc, rt, data, sock, ipc = _scratch_pnd()
    try:
        sideeffect = os.path.join(rt, "SIDE_EFFECT_FIRED")
        worker = os.path.join(rt, "pre_worker.sh")
        with open(worker, "w") as f:
            f.write(f"#!/bin/bash\ntouch {sideeffect}\necho irreversible-done\n")
        os.chmod(worker, 0o755)

        r = ipc({"verb": "submit", "cmd": [worker], "class": "worker", "tag": "pre-irrev",
                 "approval": "pre"})
        check(r.get("ok") and r.get("state") == "staged" and r.get("nonce"),
              f"approval:pre submit -> staged with nonce (state={r.get('state')})")
        jid, nonce = r["id"], r["nonce"]

        time.sleep(3.0)
        j = ipc({"verb": "job", "id": jid})["job"]
        check(j["state"] == "staged", "the pre-gated job stays staged (never dispatched)")
        check(not os.path.exists(sideeffect),
              "the IRREVERSIBLE side effect did NOT fire before approval (side-effect safety)")

        cvm = ipc({"verb": "cvm", "id": jid}).get("cvm", {})
        act = (cvm.get("approval_request") or {}).get("action") or {}
        check(act.get("side_effecting") is True and worker in (act.get("argv") or []),
              "the pre approval-request shows the exact side-effecting action")

        ipc({"verb": "approve", "nonce": nonce})
        _wait_state(ipc, jid, lambda s: s in ("done", "failed", "rejected"))
        check(os.path.exists(sideeffect), "after approve, the action runs and the side effect fires")
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
    print("=== p-approval-gate: post-result / checkpoint human-approval gate — test suite ===")
    cx, path = fresh_db()
    try:
        test_schema(cx)
        test_hold_state(cx)
        test_three_decisions(cx)
        test_bounded_revise(cx)
        test_cross_tenant(cx)
        test_pre_gate_unbroken(cx)
        test_inbox(cx)
    finally:
        cx.close()
        for p in (path, path + "-wal", path + "-shm"):
            try:
                os.unlink(p)
            except OSError:
                pass
    test_live_post_gate()
    test_live_reject_retains()
    test_live_revise()
    test_live_pre_side_effect()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
