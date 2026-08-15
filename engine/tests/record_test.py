#!/usr/bin/env python3

import os, sys, json, time, tempfile, shutil, subprocess, importlib, socket
os.environ.setdefault("PN_DISPATCH_BACKEND", "systemd")
from importlib.machinery import SourceFileLoader
import importlib.util
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pn_voraussetzung import live_moeglich

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

PASS, FAIL = 0, 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")

def _fresh_imports(data_dir):

    os.environ["PN_DATA_DIR"] = data_dir
    for m in list(sys.modules):
        if m == "pnlib" or m.startswith("pnlib."):
            del sys.modules[m]
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    pnlib = importlib.import_module("pnlib")
    db = importlib.import_module("pnlib.db")
    record = importlib.import_module("pnlib.record")
    meters = importlib.import_module("pnlib.meters")
    return pnlib, db, record, meters

def _load_pnd(data_dir):

    os.environ["PN_DATA_DIR"] = data_dir
    for m in list(sys.modules):
        if m == "pnlib" or m.startswith("pnlib."):
            del sys.modules[m]
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    loader = SourceFileLoader("pnd_mod", os.path.join(ROOT, "tools", "pnd"))
    spec = importlib.util.spec_from_loader("pnd_mod", loader)
    pnd = importlib.util.module_from_spec(spec)
    loader.exec_module(pnd)
    return pnd

def _make_job(db, cx, principal="alice", task_type="echo.test", cmd=None):
    cmd = cmd or ["/bin/echo", "hi"]
    jid = db.submit(cx, cmd, "/tmp", {}, "{}", 100, 64, "t",
                    principal=principal, task_type=task_type)
    return db.get(cx, jid, scope_all=True)

def _write_good_work(record, job):

    ws = record.mkspace(job["id"])
    with open(os.path.join(ws, "artifacts", "out.txt"), "w") as f:
        f.write("result bytes\n")
    return ws

def test_done_gate(data_dir):
    print("[a] done-gate refuses `done` without a valid record")
    pnlib, db, record, meters = _fresh_imports(data_dir)
    cx = db.connect(os.path.join(data_dir, "queue.db"))

    job = _make_job(db, cx)
    db.set_workspace(cx, job["id"], record.mkspace(job["id"]))
    raised = None
    try:
        db.finalize(cx, job["id"], "done", 0, record_ok=False)
    except ValueError as e:
        raised = str(e)
    check(raised is not None and "no-done-without-record" in raised,
          f"db.finalize(done, record_ok=0) REJECTED ({raised!r})")

    row = db.get(cx, job["id"], scope_all=True)
    check(row["state"] != "done",
          f"rejected finalize left state={row['state']!r} (never a transient done)")

    ws = _write_good_work(record, job)
    db.set_workspace(cx, job["id"], ws)
    job = db.get(cx, job["id"], scope_all=True)
    info = record.write_record(job, argv=["/bin/echo", "hi"], exit_code=0,
                               started_at=time.time(), finished_at=time.time())
    ok_no_emit = record.compute_record_ok(job, work_success=True, committed=True, emitted=False)
    check(ok_no_emit is False, "record_ok=False when not emitted (emission-aware gate)")
    ok_no_commit = record.compute_record_ok(job, work_success=True, committed=False, emitted=True)
    check(ok_no_commit is False, "record_ok=False when not git-committed")
    ok_full = record.compute_record_ok(job, work_success=True, committed=True, emitted=True)
    check(ok_full is True, "record_ok=True only when work+layout+commit+emit all hold")

    bad = _make_job(db, cx, principal="bob")
    badws = record.mkspace(bad["id"])
    db.set_workspace(cx, bad["id"], badws)
    bad = db.get(cx, bad["id"], scope_all=True)
    check(record.compute_record_ok(bad, work_success=True, committed=True, emitted=True) is False,
          "incomplete workspace layout -> record_ok=False")
    cx.close()

def test_finalize_atomic(data_dir):
    print("[b] finalize_job() atomicity: no transient `done` with record_ok=0")
    pnd = _load_pnd(data_dir)
    pnlib, db, record, meters = _fresh_imports(data_dir)
    cx = db.connect(os.path.join(data_dir, "queue.db"))
    pnd.CX = cx

    job = _make_job(db, cx, principal="alice")
    ws = _write_good_work(record, job)
    db.set_workspace(cx, job["id"], ws)
    db.mark_running(cx, job["id"], "u", os.path.join(data_dir, "x.out"))
    job = db.get(cx, job["id"], scope_all=True)
    state = pnd.finalize_job(job, 0, "done")
    row = db.get(cx, job["id"], scope_all=True)
    check(state == "done" and row["state"] == "done" and row["record_ok"] == 1,
          f"good job -> done with record_ok=1 (state={row['state']}, record_ok={row['record_ok']})")
    check(row["record_commit"], f"record_commit set (git OID={str(row['record_commit'])[:12]})")

    check(not (row["state"] == "done" and row["record_ok"] == 0),
          "INVARIANT: never (state=done AND record_ok=0)")

    bad = _make_job(db, cx, principal="bob", cmd=["/bin/false"])
    ws2 = record.mkspace(bad["id"])
    db.set_workspace(cx, bad["id"], ws2)
    bad = db.get(cx, bad["id"], scope_all=True)
    st2 = pnd.finalize_job(bad, 1, "failed")
    row2 = db.get(cx, bad["id"], scope_all=True)
    check(st2 == "failed" and row2["state"] == "failed" and row2["record_ok"] == 0,
          f"work failure -> failed + record_ok=0 (never done) (state={row2['state']})")

    check(row2["record_commit"], "failed job STILL has a committed record (provenance preserved)")
    cx.close()

def test_replication_decoupled(data_dir):
    print("[c] replication-only failure keeps the job non-failed + retained (re-pushed on recovery)")
    pnd = _load_pnd(data_dir)
    pnlib, db, record, meters = _fresh_imports(data_dir)
    cx = db.connect(os.path.join(data_dir, "queue.db"))
    pnd.CX = cx

    os.environ.pop("PN_REPLICA_DIR", None)
    job = _make_job(db, cx, principal="alice")
    ws = _write_good_work(record, job)
    db.set_workspace(cx, job["id"], ws)
    db.mark_running(cx, job["id"], "u", os.path.join(data_dir, "y.out"))
    job = db.get(cx, job["id"], scope_all=True)
    pnd.finalize_job(job, 0, "done")
    row = db.get(cx, job["id"], scope_all=True)

    check(row["state"] == "done" and row["record_ok"] == 1 and row["replicated"] == 0,
          f"DONE with replicated=0 (off-box NOT required for done) "
          f"(state={row['state']}, replicated={row['replicated']})")

    pnd.replicate_sweep()
    row = db.get(cx, job["id"], scope_all=True)
    check(row["state"] == "done" and row["replicated"] == 0 and row["workspace_path"],
          "replication failure -> job still done, still replicated=0, workspace RETAINED")
    pend = db.replication_pending(cx)
    check(any(p["id"] == job["id"] for p in pend),
          "unreplicated result is in replication_pending (will be re-pushed)")

    replica = os.path.join(data_dir, "offbox")
    os.makedirs(replica, exist_ok=True)
    os.environ["PN_REPLICA_DIR"] = replica
    pnd.replicate_sweep()
    row = db.get(cx, job["id"], scope_all=True)
    check(row["replicated"] == 1 and row["result_uri"],
          f"recovery: re-push set replicated=1 + locator ({row['result_uri']})")

    before = row["replicated_at"]
    pnd.replicate_sweep()
    row2 = db.get(cx, job["id"], scope_all=True)
    check(row2["replicated"] == 1, "second sweep is idempotent (still replicated=1)")
    cx.close()

def test_gc_never_deletes_unreplicated(data_dir):
    print("[d] GC never deletes an unreplicated copy; deletes only after TTL AND replicated")
    pnd = _load_pnd(data_dir)
    pnlib, db, record, meters = _fresh_imports(data_dir)
    cx = db.connect(os.path.join(data_dir, "queue.db"))
    pnd.CX = cx
    os.environ.pop("PN_REPLICA_DIR", None)

    u = _make_job(db, cx, principal="alice")
    wsu = _write_good_work(record, u)
    db.set_workspace(cx, u["id"], wsu)
    u = db.get(cx, u["id"], scope_all=True)
    pnd.finalize_job(u, 0, "done")

    cx.execute("UPDATE jobs SET retention_until=? WHERE id=?", (time.time() - 10, u["id"]))
    cx.commit()
    check(db.gc_candidates(cx) == [], "gc_candidates excludes the TTL-elapsed UNREPLICATED job")
    pnd.gc_sweep()
    ru = db.get(cx, u["id"], scope_all=True)
    check(ru["workspace_path"] and os.path.isdir(wsu),
          "GC sweep did NOT delete the unreplicated work-copy (bytes retained on disk)")

    replica = os.path.join(data_dir, "offbox2")
    os.makedirs(replica, exist_ok=True)
    r = _make_job(db, cx, principal="bob")
    wsr = _write_good_work(record, r)
    db.set_workspace(cx, r["id"], wsr)
    r = db.get(cx, r["id"], scope_all=True)
    pnd.finalize_job(r, 0, "done")
    res = record.replicate({**db.get(cx, r["id"], scope_all=True)}, target=replica)
    check(res.get("ok"), f"R replicated to target ({res.get('result_uri')})")
    db.mark_replicated(cx, r["id"], result_uri=res.get("result_uri"),
                       result_hash=res.get("result_hash"))
    cx.execute("UPDATE jobs SET retention_until=? WHERE id=?", (time.time() - 10, r["id"]))
    cx.commit()
    cands = {c["id"] for c in db.gc_candidates(cx)}
    check(r["id"] in cands and u["id"] not in cands,
          "gc_candidates = {replicated+TTL job} ONLY (unreplicated excluded)")
    pnd.gc_sweep()
    rr = db.get(cx, r["id"], scope_all=True)
    check(rr["workspace_path"] is None and not os.path.isdir(wsr),
          "GC deleted the replicated+TTL work-copy (workspace_path NULL, bytes gone)")

    check(os.path.isdir(replica) and len(os.listdir(replica)) >= 1,
          "off-box replica still present (canonical artifact home)")
    w = db.whereis(cx, r["id"], scope_all=True)
    check(w["status"] == "done" and w["who"] == "bob" and w["replicated"]
          and "off-box" in w["bytes_status"],
          f"whereis answers status/who from ledger + surfaces off-box bytes ({w['bytes_status']})")

    db.mark_workspace_gcd(cx, u["id"])
    wu = db.whereis(cx, u["id"], scope_all=True)
    check(wu["bytes_status"] == "gc'd, replica-unverified" and wu["status"] == "done",
          "whereis(unreplicated, gone) -> 'gc'd, replica-unverified' (status still answered)")

    u2 = _make_job(db, cx, principal="alice")
    ws2 = _write_good_work(record, u2)
    db.set_workspace(cx, u2["id"], ws2)
    os.environ.pop("PN_REPLICA_DIR", None)
    u2 = db.get(cx, u2["id"], scope_all=True)
    pnd.finalize_job(u2, 0, "done")
    victims = {v["id"] for v in db.disk_pressure_victims(cx)}
    check(u2["id"] not in victims,
          "disk_pressure_victims excludes the unreplicated copy (stop-and-shout, not delete)")
    cx.close()

def _finalize_good(pnd, db, record, cx, principal="alice"):

    j = _make_job(db, cx, principal=principal)
    ws = _write_good_work(record, j)
    db.set_workspace(cx, j["id"], ws)
    db.mark_running(cx, j["id"], "u", os.path.join(os.path.dirname(ws), "x.out"))
    j = db.get(cx, j["id"], scope_all=True)
    pnd.finalize_job(j, 0, "done")
    return db.get(cx, j["id"], scope_all=True)

def test_pluggable_targets(data_dir):
    print("[h] pluggable off-box replication targets: LocalDir/Rsync/GitRemote/S3 (interface + ≥1 real)")
    pnlib, db, record, meters = _fresh_imports(data_dir)
    import importlib
    replication = importlib.import_module("pnlib.replication")
    recordcfg = importlib.import_module("pnlib.recordcfg")
    cx = db.connect(os.path.join(data_dir, "queue.db"))

    check(all(issubclass(t, replication.ReplicationTarget) for t in
              (replication.LocalDirTarget, replication.RsyncTarget,
               replication.GitRemoteTarget, replication.S3Target)),
          "LocalDir/Rsync/GitRemote/S3 all implement the ReplicationTarget interface")

    job = _finalize_good(None, db, record, cx) if False else None

    pnd = _load_pnd(data_dir)
    pnlib, db, record, meters = _fresh_imports(data_dir)
    replication = importlib.import_module("pnlib.replication")
    recordcfg = importlib.import_module("pnlib.recordcfg")
    cx = db.connect(os.path.join(data_dir, "queue.db"))
    pnd.CX = cx
    job = _finalize_good(pnd, db, record, cx)
    ws = job["workspace_path"]

    ldir = os.path.join(data_dir, "offbox-local")
    lt = replication.LocalDirTarget(ldir)
    r1 = lt.push(job, ws)
    r2 = lt.push(job, ws)
    check(r1["ok"] and r1["result_uri"].startswith("file://") and os.path.isdir(ldir),
          "LocalDirTarget.push replicates the record (real off-box copy)")
    check(r2.get("idempotent"), "LocalDirTarget.push is idempotent on the content digest")

    calls = []
    class FakeProc:
        returncode = 0; stderr = ""
    rt = replication.RsyncTarget("user@nas:/srv/records",
                                 runner=lambda argv: calls.append(argv) or FakeProc())
    rr = rt.push(job, ws)
    check(rr["ok"] and rr["result_uri"].startswith("rsync://") and calls
          and calls[0][0] == "rsync" and ws.rstrip("/") + "/" in calls[0],
          "RsyncTarget.push invokes rsync to the NAS dest (mocked subprocess; no real network)")
    rt_idem = replication.RsyncTarget("user@nas:/srv/records",
                                      runner=lambda argv: FakeProc(),
                                      exists_runner=lambda dest: True)
    check(rt_idem.push(job, ws).get("idempotent"),
          "RsyncTarget is idempotent when the dest already exists (exists probe)")

    git_calls = []
    gt = replication.GitRemoteTarget("git@host:records.git",
                                     runner=lambda argv, cwd: git_calls.append((argv, cwd)) or FakeProc())
    gr = gt.push(job, ws)
    check(gr["ok"] and gr["result_uri"].startswith("git+") and git_calls
          and git_calls[0][0][:2] == ["git", "push"]
          and f"HEAD:refs/records/{job['id']}" in git_calls[0][0],
          "GitRemoteTarget.push pushes the record commit to refs/records/<id> (mocked git)")

    class FakeS3:
        def __init__(self): self.put = {}
        def head_object(self, Bucket, Key): raise Exception("not found")
        def put_object(self, Bucket, Key, Body): self.put = {"bucket": Bucket, "key": Key, "n": len(Body)}
    s3c = FakeS3()
    s3 = replication.S3Target("my-bucket", "records", client=s3c)
    sr = s3.push(job, ws)
    check(sr["ok"] and sr["result_uri"].startswith("s3://my-bucket/records/") and s3c.put.get("n", 0) > 0,
          "S3Target.push uploads a tar of the record via the injected client (mocked S3)")
    s3_nowire = replication.S3Target("b", client=None)
    check(not s3_nowire.push(job, ws)["ok"],
          "S3Target without a wired client reports not-wired (no raise; result retained)")

    tgt = replication.from_config({"replication": {"target": "rsync",
                                                   "dest_root": "user@nas:/srv/r"}})
    check(isinstance(tgt, replication.RsyncTarget), "from_config selects RsyncTarget from config")
    os.environ["PN_REPLICA_DIR"] = ldir
    check(isinstance(replication.from_config({}), replication.LocalDirTarget),
          "from_config honors PN_REPLICA_DIR env (the v1 default) when no config target")
    os.environ.pop("PN_REPLICA_DIR", None)
    check(replication.from_config({}) is None,
          "from_config returns None when NO target is configured (replication stays pending)")
    cx.close()

def test_replicate_before_gc_offbox(data_dir):
    print("[i] replicate-before-GC enforced with a NON-local target: GC only after the off-box ack")
    pnd = _load_pnd(data_dir)
    pnlib, db, record, meters = _fresh_imports(data_dir)
    import importlib
    replication = importlib.import_module("pnlib.replication")
    cx = db.connect(os.path.join(data_dir, "queue.db"))
    pnd.CX = cx
    os.environ.pop("PN_REPLICA_DIR", None)

    job = _finalize_good(pnd, db, record, cx, principal="carol")
    ws = job["workspace_path"]

    cx.execute("UPDATE jobs SET retention_until=? WHERE id=?", (time.time() - 10, job["id"]))
    cx.commit()
    check(db.gc_candidates(cx) == [], "TTL-elapsed but unreplicated -> NOT a GC candidate")
    pnd.gc_sweep()
    check(os.path.isdir(ws), "GC sweep did NOT delete the unreplicated copy")

    class FakeProc:
        returncode = 0; stderr = ""
    rt = replication.RsyncTarget("user@nas:/srv/records", runner=lambda argv: FakeProc())
    res = record.replicate(job, target=rt)
    check(res["ok"] and res["result_uri"].startswith("rsync://"),
          "the record replicated to the off-box rsync target (mocked)")
    db.mark_replicated(cx, job["id"], result_uri=res["result_uri"], result_hash=res["result_hash"])
    cands = {c["id"] for c in db.gc_candidates(cx)}
    check(job["id"] in cands, "AFTER the off-box ack (replicated=1) + TTL -> it IS a GC candidate")
    pnd.gc_sweep()
    row = db.get(cx, job["id"], scope_all=True)
    check(row["workspace_path"] is None and not os.path.isdir(ws),
          "GC deleted the work-copy ONLY after the off-box replication ack")

    w = db.whereis(cx, job["id"], scope_all=True)
    check(w["replicated"] and "rsync://" in (w["result_uri"] or ""),
          "whereis surfaces the off-box rsync locator after GC (canonical home is off-box)")
    cx.close()

def test_retention_config(data_dir):
    print("[j] retention/TTL config in a SEPARATE store overrides the live record seam")
    pnlib, db, record, meters = _fresh_imports(data_dir)
    import importlib
    recordcfg = importlib.import_module("pnlib.recordcfg")

    cfg = recordcfg.load()
    check(cfg["retention"]["work_ttl_s"] == record.WORK_TTL_S
          and cfg["retention"]["data_warn_pct"] == record.DATA_WARN_PCT,
          "recordcfg defaults match the shipped record retention constants")

    cfg["retention"]["work_ttl_s"] = 7200
    cfg["retention"]["work_ttl_pressure_s"] = 600
    cfg["retention"]["data_warn_pct"] = 20.0
    saved = recordcfg.save(cfg)
    check(saved == recordcfg.CONFIG_PATH and saved.startswith(data_dir) and "queue.db" not in saved,
          f"retention config persisted to a SEPARATE store (not queue.db): {os.path.basename(saved)}")
    applied = recordcfg.apply_retention(record, recordcfg.load())
    check(record.WORK_TTL_S == 7200 and record.DATA_WARN_PCT == 20.0,
          "apply_retention overrode the live record seam (WORK_TTL_S / DATA_WARN_PCT)")

    check(record.work_ttl_for(100.0) == 7200 and record.work_ttl_for(5.0) == 600,
          "record.work_ttl_for() honors the configured normal + pressure TTLs")

    os.environ["PN_WORK_TTL_S"] = "111"
    check(recordcfg.load()["retention"]["work_ttl_s"] == 111,
          "PN_WORK_TTL_S env override wins over the config file")
    os.environ.pop("PN_WORK_TTL_S", None)

def test_data_meter(data_dir):
    print("[e] disk meter + admission backstop read the DATA mount, not '/'")
    pnlib, db, record, meters = _fresh_imports(data_dir)
    snap = meters.snapshot()
    check(snap["data_dir"] == data_dir,
          f"snapshot DATA dir == the scratch DATA mount ({snap['data_dir']})")

    data_free = meters.disk_free_mib(data_dir)
    check(snap["disk_free"] == data_free,
          f"snap['disk_free'] == disk_free_mib(DATA) ({snap['disk_free']} == {data_free})")
    check("root_free" in snap and snap["root_free"] == meters.disk_free_mib("/"),
          "root '/' free is kept separately (diagnostics only, never gated on)")
    check(0.0 <= snap["data_free_pct"] <= 100.0,
          f"data_free_pct is a valid percentage ({snap['data_free_pct']:.1f}%)")

    not_yet = os.path.join(data_dir, "does", "not", "exist", "yet")
    check(meters.disk_free_mib(not_yet) < (1 << 30),
          "disk_free_mib walks up to an existing ancestor (real fs reading, not the sentinel)")

def test_canary_and_integrity(data_dir):
    if not live_moeglich('test_canary_and_integrity'):
        return
    print("[f]+[g] P2 canary still passes WITH a record + integrity_check clean (scratch pnd)")
    rt = tempfile.mkdtemp(prefix="pn_rt_")
    cdata = tempfile.mkdtemp(prefix="pn_cdata_")
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = rt
    env["XDG_DATA_HOME"] = cdata
    env["PN_DATA_DIR"] = cdata
    env["PN_DURABILITY"] = "normal"
    env.pop("NOTIFY_SOCKET", None)
    env.pop("PN_REPLICA_DIR", None)

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
    try:
        for _ in range(50):
            if os.path.exists(sock):
                break
            time.sleep(0.1)
        check(os.path.exists(sock), f"scratch pnd up at {sock}")

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

        check(ipc({"verb": "ping"}).get("ok"), "ping ok")

        r = ipc({"verb": "submit", "task_type": "echo.test",
                 "params": {"msg": "canary-ok"}, "class": "worker"})
        check(r.get("ok") and r.get("id"), f"admin echo.test submit ok (id={r.get('id')})")
        jid = r["id"]
        state = None
        for _ in range(150):
            jr = ipc({"verb": "job", "id": jid})
            if jr.get("ok"):
                state = jr["job"]["state"]
                if state in ("done", "failed", "cancelled", "timeout"):
                    break
            time.sleep(0.2)
        check(state == "done", f"admin echo.test completed (state={state})")

        jr = ipc({"verb": "job", "id": jid})
        check(jr["job"].get("record_ok") == 1,
              f"completed job has record_ok=1 (the done-gate) (record_ok={jr['job'].get('record_ok')})")

        wr = ipc({"verb": "whereis", "id": jid})
        check(wr.get("ok") and wr["whereis"]["status"] == "done"
              and wr["whereis"]["record_ok"],
              "whereis over IPC answers from the ledger")

        ws = os.path.join(cdata, "record", "work", str(jid))
        check(os.path.isdir(os.path.join(ws, ".git"))
              and os.path.isfile(os.path.join(ws, "MANIFEST.json"))
              and os.path.isfile(os.path.join(ws, "provenance.json"))
              and os.path.isfile(os.path.join(ws, "README.md")),
              "the record exists on disk (git repo + MANIFEST + provenance + README)")

        r = ipc({"verb": "submit", "cmd": ["/bin/echo", "raw-retained"], "class": "worker"})
        check(r.get("ok"), f"admin raw cmd accepted (task.raw retained) id={r.get('id')}")
        rid = r["id"]
        rstate = None
        for _ in range(150):
            jr = ipc({"verb": "job", "id": rid})
            if jr.get("ok"):
                rstate = jr["job"]["state"]
                if rstate in ("done", "failed", "cancelled", "timeout"):
                    break
            time.sleep(0.2)
        check(rstate == "done", f"admin raw job completed (state={rstate})")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        try:
            import sqlite3
            cxv = sqlite3.connect(os.path.join(cdata, "portioneer", "queue.db"))
            ic = cxv.execute("PRAGMA integrity_check").fetchone()[0]
            cxv.close()
            check(ic == "ok", f"[g] scratch DB integrity_check = {ic!r}")
        except Exception as e:
            check(False, f"[g] scratch DB integrity_check raised {e}")
        shutil.rmtree(rt, ignore_errors=True)
        shutil.rmtree(cdata, ignore_errors=True)

def test_integrity_main(data_dir):
    print("[g] PRAGMA integrity_check on the v5-migrated DB")
    pnlib, db, record, meters = _fresh_imports(data_dir)
    cx = db.connect(os.path.join(data_dir, "queue.db"))
    r = cx.execute("PRAGMA integrity_check").fetchone()[0]
    check(r == "ok", f"integrity_check = {r!r}")
    cols = {row["name"] for row in cx.execute("PRAGMA table_info(jobs)")}
    check({"workspace_path", "record_commit", "result_uri", "result_hash",
           "replicated", "replicated_at", "retention_until"} <= cols,
          "v5 columns present")
    idx = {row["name"] for row in cx.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    check("idx_record_gc" in idx, "idx_record_gc index present")
    cx.close()

def main():
    print("=== P4 The Record + done-gate + stateless GC — test suite ===")
    base = tempfile.mkdtemp(prefix="pn_p4_")
    try:
        test_done_gate(os.path.join(base, "a"))
        test_finalize_atomic(os.path.join(base, "b"))
        test_replication_decoupled(os.path.join(base, "c"))
        test_gc_never_deletes_unreplicated(os.path.join(base, "d"))
        test_pluggable_targets(os.path.join(base, "h"))
        test_replicate_before_gc_offbox(os.path.join(base, "i"))
        test_retention_config(os.path.join(base, "j"))
        test_data_meter(os.path.join(base, "e"))
        test_integrity_main(os.path.join(base, "g"))
        test_canary_and_integrity(os.path.join(base, "f"))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
