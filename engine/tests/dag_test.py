#!/usr/bin/env python3

import os, sys, json, time, tempfile, importlib.util, socket, subprocess, threading

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from pnlib import db
from importlib.machinery import SourceFileLoader
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
    path = tempfile.mktemp(prefix="pn_dag_", suffix=".db")
    cx = db.connect(path)
    for name, uid in (("alice", 5001), ("bob", 5002), ("planner", 5003)):
        cx.execute("INSERT OR IGNORE INTO principals(name,uid,kind,note) VALUES(?,?,?,?)",
                   (name, uid, "user", f"test tenant {name}"))
    for principal, cap in (("alice", "task_type:echo.test"),
                           ("alice", "task_type:sleep.test"),
                           ("alice", "task_type:review.post"),
                           ("bob", "task_type:echo.test"),

                           ("planner", "task_type:echo.test"),
                           ("planner", "task_type:sleep.test")):
        if not cx.execute("SELECT 1 FROM grants WHERE principal=? AND cap=?",
                          (principal, cap)).fetchone():
            cx.execute("INSERT INTO grants(principal,cap) VALUES(?,?)", (principal, cap))
    cx.commit()
    return cx, path

def _terminate(cx, jid, state, exit_code=0):

    db.mark_terminal(cx, jid, state, exit_code)

def test_schema(cx):
    print("[a] v8 schema: caps column; deps -> `blocked`; no-deps -> `queued`; integrity ok")
    jcols = {r["name"] for r in cx.execute("PRAGMA table_info(jobs)")}
    check("caps" in jcols, "v8 jobs.caps column present")
    check({"deps", "group_id", "parent_job"} <= jcols, "v3 deps/group_id/parent_job present (USED)")

    j0 = db.submit(cx, ["/bin/echo", "x"], "/tmp", {}, "{}", 100, 64, "nodeps",
                   principal="alice", task_type="echo.test")
    check(cx.execute("SELECT state FROM jobs WHERE id=?", (j0,)).fetchone()[0] == "queued",
          "a no-deps job starts `queued` (backward-compat)")

    j1 = db.submit(cx, ["/bin/echo", "y"], "/tmp", {}, "{}", 100, 64, "deps",
                   principal="alice", task_type="echo.test", deps=[j0])
    check(cx.execute("SELECT state FROM jobs WHERE id=?", (j1,)).fetchone()[0] == "blocked",
          "a job WITH deps starts `blocked`")
    check(db._deps_of(db.get(cx, j1, scope_all=True)) == [j0], "deps persisted as a job-id list")
    check(cx.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "integrity_check ok")

def test_classify(cx):
    print("[b] dep_state / deps_satisfied / deps_failed classify deps")
    a = db.submit(cx, ["/bin/echo", "a"], "/tmp", {}, "{}", 100, 64, "ca",
                  principal="alice", task_type="echo.test")
    b = db.submit(cx, ["/bin/echo", "b"], "/tmp", {}, "{}", 100, 64, "cb",
                  principal="alice", task_type="echo.test")
    child = db.submit(cx, ["/bin/echo", "c"], "/tmp", {}, "{}", 100, 64, "cc",
                      principal="alice", task_type="echo.test", deps=[a, b])
    cj = db.get(cx, child, scope_all=True)

    check(not db.deps_satisfied(cx, cj) and db.deps_failed(cx, cj) is None,
          "deps pending -> not satisfied, not failed")
    _terminate(cx, a, "done", 0)
    check(not db.deps_satisfied(cx, cj), "one dep done, one pending -> still not satisfied")
    _terminate(cx, b, "done", 0)
    check(db.deps_satisfied(cx, cj), "BOTH deps done -> satisfied")

    a2 = db.submit(cx, ["/bin/echo", "a2"], "/tmp", {}, "{}", 100, 64, "ca2",
                   principal="alice", task_type="echo.test")
    f = db.submit(cx, ["/bin/echo", "f"], "/tmp", {}, "{}", 100, 64, "cf",
                  principal="alice", task_type="echo.test", deps=[a2])
    _terminate(cx, a2, "failed", 1)
    fj = db.get(cx, f, scope_all=True)
    det = db.deps_failed(cx, fj)
    check(det is not None and a2 in det["failed_deps"], "a failed dep -> deps_failed reports it")

    m = db.submit(cx, ["/bin/echo", "m"], "/tmp", {}, "{}", 100, 64, "cm",
                  principal="alice", task_type="echo.test", deps=[999999])
    mj = db.get(cx, m, scope_all=True)
    check(db.deps_failed(cx, mj) is not None, "a MISSING dep is treated as a hard failure")

def test_promote(cx):
    print("[c] gate_dag promotes a blocked job ONLY when ALL deps are `done` (strict order)")
    a = db.submit(cx, ["/bin/echo", "a"], "/tmp", {}, "{}", 100, 64, "pa",
                  principal="alice", task_type="echo.test")
    b = db.submit(cx, ["/bin/echo", "b"], "/tmp", {}, "{}", 100, 64, "pb",
                  principal="alice", task_type="echo.test", deps=[a])
    c = db.submit(cx, ["/bin/echo", "c"], "/tmp", {}, "{}", 100, 64, "pc",
                  principal="alice", task_type="echo.test", deps=[b])
    db.gate_dag(cx)
    check(cx.execute("SELECT state FROM jobs WHERE id=?", (b,)).fetchone()[0] == "blocked",
          "B stays blocked while A is not done")
    _terminate(cx, a, "done", 0)
    res = db.gate_dag(cx)
    check(b in res["promoted"], "A done -> B promoted to queued")
    check(cx.execute("SELECT state FROM jobs WHERE id=?", (b,)).fetchone()[0] == "queued",
          "B is now queued")
    check(cx.execute("SELECT state FROM jobs WHERE id=?", (c,)).fetchone()[0] == "blocked",
          "C stays blocked while B is not done (strict A->B->C order)")
    _terminate(cx, b, "done", 0)
    res = db.gate_dag(cx)
    check(c in res["promoted"], "B done -> C promoted")

def test_propagate(cx):
    print("[d] failure propagation: a failed dep cancels dependents (dep_failed), recursively")
    a = db.submit(cx, ["/bin/echo", "a"], "/tmp", {}, "{}", 100, 64, "fa",
                  principal="alice", task_type="echo.test")
    b = db.submit(cx, ["/bin/echo", "b"], "/tmp", {}, "{}", 100, 64, "fb",
                  principal="alice", task_type="echo.test", deps=[a])
    c = db.submit(cx, ["/bin/echo", "c"], "/tmp", {}, "{}", 100, 64, "fc",
                  principal="alice", task_type="echo.test", deps=[b])
    _terminate(cx, a, "failed", 1)
    res = db.gate_dag(cx)
    cancelled_ids = [i for i, _ in res["cancelled"]]
    check(b in cancelled_ids and c in cancelled_ids,
          "A failed -> B AND C cancelled (RECURSIVE propagation)")
    check(cx.execute("SELECT state FROM jobs WHERE id=?", (b,)).fetchone()[0] == "cancelled",
          "B is cancelled")
    check(cx.execute("SELECT state FROM jobs WHERE id=?", (c,)).fetchone()[0] == "cancelled",
          "C is cancelled (the transitive dependent)")

    evs = db.events(cx, b, scope_all=True)
    note = next((e for e in evs if e["kind"] == "note"), None)
    check(note is not None and "dep_failed" in (note["data"] or ""),
          "the cancellation carries a `dep_failed` reason note")

    check(all(j["state"] != "blocked" for j in db.blocked_jobs(cx)
              if a in db._deps_of(j) or b in db._deps_of(j)),
          "no orphaned `blocked` job waits on the dead upstream")

    check(cx.execute("SELECT finished_at FROM jobs WHERE id=?", (b,)).fetchone()[0] is not None,
          "a dep_failed-cancelled job has finished_at set (GC retention governs it)")

def test_diamond(cx):
    print("[e] diamond A->{B,C}->D: D blocked until BOTH B and C are done")
    a = db.submit(cx, ["/bin/echo", "a"], "/tmp", {}, "{}", 100, 64, "da",
                  principal="alice", task_type="echo.test")
    b = db.submit(cx, ["/bin/echo", "b"], "/tmp", {}, "{}", 100, 64, "db",
                  principal="alice", task_type="echo.test", deps=[a])
    c = db.submit(cx, ["/bin/echo", "c"], "/tmp", {}, "{}", 100, 64, "dc",
                  principal="alice", task_type="echo.test", deps=[a])
    d = db.submit(cx, ["/bin/echo", "d"], "/tmp", {}, "{}", 100, 64, "dd",
                  principal="alice", task_type="echo.test", deps=[b, c])
    _terminate(cx, a, "done", 0)
    db.gate_dag(cx)
    check(cx.execute("SELECT state FROM jobs WHERE id=?", (b,)).fetchone()[0] == "queued"
          and cx.execute("SELECT state FROM jobs WHERE id=?", (c,)).fetchone()[0] == "queued",
          "A done -> both B and C promoted")
    check(cx.execute("SELECT state FROM jobs WHERE id=?", (d,)).fetchone()[0] == "blocked",
          "D stays blocked (neither B nor C done)")
    _terminate(cx, b, "done", 0)
    db.gate_dag(cx)
    check(cx.execute("SELECT state FROM jobs WHERE id=?", (d,)).fetchone()[0] == "blocked",
          "D STILL blocked with only B done (waits for BOTH)")
    _terminate(cx, c, "done", 0)
    res = db.gate_dag(cx)
    check(d in res["promoted"], "B AND C done -> D promoted (diamond join)")

def test_group(cx):
    print("[f] group_status aggregates member states into one overall")
    gid = "wf-test-1"
    a = db.submit(cx, ["/bin/echo", "a"], "/tmp", {}, "{}", 100, 64, "ga",
                  principal="alice", task_type="echo.test", group_id=gid)
    b = db.submit(cx, ["/bin/echo", "b"], "/tmp", {}, "{}", 100, 64, "gb",
                  principal="alice", task_type="echo.test", deps=[a], group_id=gid)
    gs = db.group_status(cx, gid, principal="alice")
    check(gs and gs["total"] == 2 and gs["overall"] == "pending", "pending while members not done")
    db.mark_running(cx, a, "scope-a", "/tmp/a.out")
    gs = db.group_status(cx, gid, principal="alice")
    check(gs["overall"] == "running", "overall=running while a member runs")
    _terminate(cx, a, "done", 0)
    _terminate(cx, b, "done", 0)
    gs = db.group_status(cx, gid, principal="alice")
    check(gs["overall"] == "done" and gs["done"] == 2, "overall=done when all members done")

    c = db.submit(cx, ["/bin/echo", "c"], "/tmp", {}, "{}", 100, 64, "gc",
                  principal="alice", task_type="echo.test", group_id=gid)
    _terminate(cx, c, "failed", 1)
    gs = db.group_status(cx, gid, principal="alice")
    check(gs["overall"] == "failed", "one failed member -> overall=failed")

    check(db.group_status(cx, gid, principal="bob") is None,
          "bob cannot see alice's group (principal-scoped)")

def test_handoff(cx):
    print("[g] handoff_refs / result_view: the L4 artifact hand-off primitive")
    a = db.submit(cx, ["/bin/echo", "a"], "/tmp", {}, "{}", 100, 64, "ha",
                  principal="alice", task_type="echo.test")

    db.finalize(cx, a, "done", 0, record_ok=1, record_commit="cafe1234",
                result_uri="file:///record/a", result_hash="h" * 64)
    db.add_event(cx, a, "result", {"confidence": 0.9})
    refs = db.handoff_refs(cx, [a], principal="alice")
    check(len(refs) == 1 and refs[0]["result_uri"] == "file:///record/a"
          and refs[0]["result_hash"] == "h" * 64,
          "handoff_refs resolves the upstream's emitted result_uri + hash")
    check(refs[0]["verdict"] == "done", "hand-off ref carries the upstream verdict")
    rv = db.result_view(cx, a, principal="alice")
    check(rv["verdict"] == "done" and rv["confidence"] == 0.9
          and rv["artifacts"]["result_hash"] == "h" * 64,
          "result_view gives typed {verdict, confidence, artifacts}")

    check(db.handoff_refs(cx, [a], principal="bob") == [],
          "bob cannot resolve alice's hand-off refs (principal-scoped)")

def _scratch_pnd():

    rt = tempfile.mkdtemp(prefix="pn_dag_rt_")
    data = tempfile.mkdtemp(prefix="pn_dag_data_")
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

def _wait_state(ipc, jid, want, tries=200, every=0.15):
    st = None
    for _ in range(tries):
        jr = ipc({"verb": "job", "id": jid})
        if jr.get("ok"):
            st = jr["job"]["state"]
            if (want(st) if callable(want) else st == want):
                return st
        time.sleep(every)
    return st

def _started_at(ipc, jid):
    jr = ipc({"verb": "job", "id": jid})
    return (jr.get("job") or {}).get("started_at")

def _kill(proc, rt, data):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    subprocess.run(["rm", "-rf", rt, data])

def test_live_order():
    if not live_moeglich('test_live_order'):
        return
    print("[h] LIVE: a 3-node DAG A->B->C runs STRICTLY in order (scratch pnd)")
    proc, rt, data, sock, ipc = _scratch_pnd()
    try:
        check(ipc({"verb": "ping"}).get("ok"), "scratch pnd up")

        worker = os.path.join(rt, "w.sh")
        with open(worker, "w") as f:
            f.write("#!/bin/bash\nsleep 0.3\necho \"$1\" > \"$PN_WORKSPACE/artifacts/out.txt\"\n"
                    "echo done\n")
        os.chmod(worker, 0o755)
        ra = ipc({"verb": "submit", "cmd": [worker, "A"], "class": "worker", "tag": "A"})
        a = ra["id"]
        check(ra.get("ok"), f"A submitted (id={a})")
        rb = ipc({"verb": "submit", "cmd": [worker, "B"], "class": "worker", "tag": "B",
                  "deps": [a]})
        rc = ipc({"verb": "submit", "cmd": [worker, "C"], "class": "worker", "tag": "C",
                  "deps": [rb["id"]]})
        b, c = rb["id"], rc["id"]
        check(rb.get("state") == "blocked" and rc.get("state") == "blocked",
              "B and C are submitted `blocked`")

        _wait_state(ipc, a, "done")
        sb = _wait_state(ipc, b, "done")
        sc = _wait_state(ipc, c, "done")
        check(sb == "done" and sc == "done", "all three reached done")
        ta = _started_at(ipc, a); tb = _started_at(ipc, b); tc = _started_at(ipc, c)
        fa = ipc({"verb": "job", "id": a})["job"]["finished_at"]
        fb = ipc({"verb": "job", "id": b})["job"]["finished_at"]
        check(tb >= fa, "B STARTED only after A FINISHED (strict dep ordering)")
        check(tc >= fb, "C started only after B finished (strict dep ordering)")
    finally:
        _kill(proc, rt, data)

def test_live_propagate():
    if not live_moeglich('test_live_propagate'):
        return
    print("[i] LIVE: A-failure propagates -> B,C cancelled(dep_failed), no orphans (scratch pnd)")
    proc, rt, data, sock, ipc = _scratch_pnd()
    try:
        failer = os.path.join(rt, "fail.sh")
        with open(failer, "w") as f:
            f.write("#!/bin/bash\nexit 7\n")
        os.chmod(failer, 0o755)
        ok = os.path.join(rt, "ok.sh")
        with open(ok, "w") as f:
            f.write("#!/bin/bash\necho done\n")
        os.chmod(ok, 0o755)
        a = ipc({"verb": "submit", "cmd": [failer], "class": "worker", "tag": "A-fail"})["id"]
        b = ipc({"verb": "submit", "cmd": [ok], "class": "worker", "tag": "B", "deps": [a]})["id"]
        c = ipc({"verb": "submit", "cmd": [ok], "class": "worker", "tag": "C", "deps": [b]})["id"]
        check(_wait_state(ipc, a, "failed") == "failed", "A failed (exit 7)")
        sb = _wait_state(ipc, b, "cancelled")
        sc = _wait_state(ipc, c, "cancelled")
        check(sb == "cancelled", "B cancelled after A failed")
        check(sc == "cancelled", "C cancelled (recursive propagation)")

        evs = ipc({"verb": "events", "id": b}).get("events", [])
        notes = [e for e in evs if e["kind"] == "note" and "dep_failed" in (e.get("data") or "")]
        check(len(notes) >= 1, "B carries a dep_failed reason in its events")

        lst = ipc({"verb": "list", "limit": 50})
        blocked = lst.get("counts", {}).get("blocked", 0)
        check(blocked == 0, "no orphaned `blocked` jobs remain")
    finally:
        _kill(proc, rt, data)

def test_live_diamond_and_group():
    if not live_moeglich('test_live_diamond_and_group'):
        return
    print("[j] LIVE: diamond A->{B,C}->D waits for BOTH; group status aggregates (scratch pnd)")
    proc, rt, data, sock, ipc = _scratch_pnd()
    try:
        worker = os.path.join(rt, "w.sh")
        with open(worker, "w") as f:
            f.write("#!/bin/bash\nsleep 0.2\necho done\n")
        os.chmod(worker, 0o755)
        gid = "wf-diamond"
        a = ipc({"verb": "submit", "cmd": [worker], "class": "worker", "tag": "A",
                 "group_id": gid})["id"]
        b = ipc({"verb": "submit", "cmd": [worker], "class": "worker", "tag": "B",
                 "deps": [a], "group_id": gid})["id"]
        c = ipc({"verb": "submit", "cmd": [worker], "class": "worker", "tag": "C",
                 "deps": [a], "group_id": gid})["id"]
        d = ipc({"verb": "submit", "cmd": [worker], "class": "worker", "tag": "D",
                 "deps": [b, c], "group_id": gid})["id"]

        _wait_state(ipc, a, "done")

        sd_during = ipc({"verb": "job", "id": d})["job"]["state"]
        check(sd_during in ("blocked", "queued", "running") and sd_during != "done",
              "D not done while B/C in flight")
        check(_wait_state(ipc, d, "done") == "done", "D eventually runs after BOTH B and C")
        fb = ipc({"verb": "job", "id": b})["job"]["finished_at"]
        fc = ipc({"verb": "job", "id": c})["job"]["finished_at"]
        td = _started_at(ipc, d)
        check(td >= fb and td >= fc, "D started only after BOTH B and C finished (diamond join)")
        g = ipc({"verb": "group", "group_id": gid})
        check(g.get("ok") and g["group"]["overall"] == "done" and g["group"]["total"] == 4,
              "group status aggregates the 4-node workflow -> overall=done")
    finally:
        _kill(proc, rt, data)

def test_live_handoff():
    if not live_moeglich('test_live_handoff'):
        return
    print("[k] LIVE: a work-order hand-off passes A's artifact to B (B sees the locator/hash)")
    proc, rt, data, sock, ipc = _scratch_pnd()
    try:

        wa = os.path.join(rt, "a.sh")
        with open(wa, "w") as f:
            f.write("#!/bin/bash\necho 'A-PRODUCT' > \"$PN_WORKSPACE/artifacts/a.txt\"\necho done\n")
        os.chmod(wa, 0o755)
        seen = os.path.join(rt, "b-saw.txt")
        wb = os.path.join(rt, "b.sh")
        with open(wb, "w") as f:

            f.write(f"#!/bin/bash\necho ran > {seen}\necho done\n")
        os.chmod(wb, 0o755)
        a = ipc({"verb": "submit", "cmd": [wa], "class": "worker", "tag": "A"})["id"]
        check(_wait_state(ipc, a, "done") == "done", "A done with an artifact")

        wo = {"goal": "consume A's artifact", "inputs": [{"from_job": a}],
              "constraints": {}, "acceptance": "uses A's result_hash"}
        rb = ipc({"verb": "submit", "cmd": [wb], "class": "worker", "tag": "B",
                  "deps": [a], "work_order": wo})
        b = rb["id"]
        check(rb.get("state") == "blocked", "B blocked on A")
        check(_wait_state(ipc, b, "done") == "done", "B runs after A")

        h = ipc({"verb": "handoff", "id": b})
        check(h.get("ok") and len(h["handoff"]) == 1, "B has exactly one hand-off ref (from A)")
        ref = h["handoff"][0]
        check(ref["job_id"] == a and bool(ref["result_hash"]) and bool(ref["record_commit"]),
              "B sees A's result_hash + record_commit (the hand-off)")

        evs = ipc({"verb": "events", "id": b}).get("events", [])
        woe = next((e for e in evs if e["kind"] == "workorder"), None)
        check(woe is not None and json.loads(woe["data"]).get("handoff"),
              "B's workorder event carries the upstream hand-off refs")
        woe_data = json.loads(woe["data"])
        check(woe_data["handoff"][0]["result_hash"] == ref["result_hash"],
              "the work-order's injected hand-off ref == A's emitted result_hash")
    finally:
        _kill(proc, rt, data)

def test_live_attenuation():
    if not live_moeglich('test_live_attenuation'):
        return
    print("[l] LIVE: a sub-task with caps ⊄ parent is REJECTED at submit (attenuation)")
    proc, rt, data, sock, ipc = _scratch_pnd()
    try:

        import sqlite3
        dbp = os.path.join(data, "portioneer", "queue.db")
        cxv = sqlite3.connect(dbp)
        cxv.row_factory = sqlite3.Row
        cxv.execute("INSERT OR IGNORE INTO principals(name,uid,kind) VALUES('planner',5003,'agent')")

        for cap in ("task_type:echo.test", "task_type:sleep.test"):
            if not cxv.execute("SELECT 1 FROM grants WHERE principal='planner' AND cap=?",
                               (cap,)).fetchone():
                cxv.execute("INSERT INTO grants(principal,cap) VALUES('planner',?)", (cap,))
        cxv.commit()

        cur = cxv.execute(
            "INSERT INTO jobs(cmd,cwd,env,profile,prio,mem_estimate,state,submitter_principal,"
            "principal,caps,submitted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (json.dumps(["/bin/echo", "parent"]), "/tmp", "{}", "{}", 100, 64, "done",
             "planner", "planner", json.dumps(["task_type:echo.test"]), time.time()))
        parent_id = cur.lastrowid
        cxv.commit()
        cxv.close()

        cxv = sqlite3.connect(dbp); cxv.row_factory = sqlite3.Row

        ctx_bad = {"principal": "planner", "caps": {"task_type:echo.test", "task_type:sleep.test"}}
        refused = False
        try:
            pnd.CX = cxv
            pnd.enforce_attenuation(ctx_bad, parent_id)
        except pnd.AuthzError as e:
            refused = "attenuation" in str(e)
        check(refused, "child caps ⊄ parent caps -> AuthzError (attenuation violation)")

        ctx_ok = {"principal": "planner", "caps": {"task_type:echo.test"}}
        allowed = True
        try:
            parent_row = pnd.enforce_attenuation(ctx_ok, parent_id)
            allowed = parent_row is not None
        except pnd.AuthzError:
            allowed = False
        check(allowed, "child caps ⊆ parent caps -> allowed (attenuated decomposition)")
        cxv.close()
    finally:
        _kill(proc, rt, data)

def test_live_nodeps_immediate():
    if not live_moeglich('test_live_nodeps_immediate'):
        return
    print("[m] LIVE: a no-deps job still dispatches immediately (regression)")
    proc, rt, data, sock, ipc = _scratch_pnd()
    try:
        worker = os.path.join(rt, "w.sh")
        with open(worker, "w") as f:
            f.write("#!/bin/bash\necho done\n")
        os.chmod(worker, 0o755)
        r = ipc({"verb": "submit", "cmd": [worker], "class": "worker", "tag": "plain"})
        check(r.get("ok") and r.get("state") != "blocked" and "pos" in r,
              "a no-deps submit returns a queue position (not blocked)")
        check(_wait_state(ipc, r["id"], "done") == "done", "the no-deps job ran immediately to done")
    finally:
        _kill(proc, rt, data)

def test_live_submit_dag():
    if not live_moeglich('test_live_submit_dag'):
        return
    print("[n] LIVE: submit-dag submits a work-order DAG atomically; a cycle is refused (scratch pnd)")
    proc, rt, data, sock, ipc = _scratch_pnd()
    try:
        worker = os.path.join(rt, "w.sh")
        with open(worker, "w") as f:
            f.write("#!/bin/bash\nsleep 0.15\necho done\n")
        os.chmod(worker, 0o755)

        nodes = [
            {"cmd": [worker], "class": "worker", "tag": "n0"},
            {"cmd": [worker], "class": "worker", "tag": "n1", "deps": [0]},
            {"cmd": [worker], "class": "worker", "tag": "n2", "deps": [1],
             "work_order": {"goal": "finish", "inputs": [], "constraints": {}, "acceptance": "ok"}},
        ]
        r = ipc({"verb": "submit-dag", "nodes": nodes})
        check(r.get("ok") and len(r["job_ids"]) == 3, "submit-dag created 3 jobs atomically")
        ids = r["job_ids"]

        j1 = ipc({"verb": "job", "id": ids[1]})["job"]
        check(db._deps_of(j1) == [ids[0]], "local dep index 0 resolved to the real job id")
        check(r["group"]["overall"] in ("pending", "running"), "the new workflow group is pending")

        check(_wait_state(ipc, ids[2], "done") == "done", "the DAG drains to its terminal node")
        g = ipc({"verb": "group", "group_id": r["group_id"]})
        check(g["group"]["overall"] == "done", "the workflow group aggregates to done")

        bad = [{"cmd": [worker], "class": "worker", "deps": [0]}]
        rb = ipc({"verb": "submit-dag", "nodes": bad})
        check(not rb.get("ok") and "acyclic" in rb.get("error", ""),
              "a cyclic / non-forward dep is refused (DAG must be acyclic)")
    finally:

        try:
            import sqlite3
            cxv = sqlite3.connect(os.path.join(data, "portioneer", "queue.db"))
            ic = cxv.execute("PRAGMA integrity_check").fetchone()[0]
            cxv.close()
            check(ic == "ok", f"scratch DB integrity_check = {ic!r}")
        except Exception as e:
            check(False, f"scratch DB integrity_check raised {e}")
        _kill(proc, rt, data)

def main():
    print("=== p-dag: L2 DAG + L4 work-order / hand-off layer — test suite ===")
    cx, path = fresh_db()
    try:
        test_schema(cx)
        test_classify(cx)
        test_promote(cx)
        test_propagate(cx)
        test_diamond(cx)
        test_group(cx)
        test_handoff(cx)
    finally:
        cx.close()
        for p in (path, path + "-wal", path + "-shm"):
            try:
                os.unlink(p)
            except OSError:
                pass
    test_live_order()
    test_live_propagate()
    test_live_diamond_and_group()
    test_live_handoff()
    test_live_attenuation()
    test_live_nodeps_immediate()
    test_live_submit_dag()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
