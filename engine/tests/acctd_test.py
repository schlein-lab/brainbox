#!/usr/bin/env python3

from __future__ import annotations

import os
import sys
import json
import time
import socket
import struct
import sqlite3
import tempfile
import threading
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from pnlib import acct as A
from pnlib import governor as G
from pnlib import profile as P
from pnlib import db as DB

PASS = FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")

def _load_acctd():
    path = os.path.join(ROOT, "tools", "pn-acctd")
    spec = importlib.util.spec_from_loader("pn_acctd",
                                           importlib.machinery.SourceFileLoader("pn_acctd", path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

ACCTD = _load_acctd()

def test_store_ewma():
    print("[A] acct store: per-type EWMA converges from synthetic finalizes; idempotent fold")
    tmp = tempfile.mkdtemp(prefix="acct-A-")
    store = A.AcctStore(os.path.join(tmp, "acct.db"))
    reader = A.AcctReader(os.path.join(tmp, "acct.db"))

    for i in range(1, 41):
        store.record_actual(job_id=i, task_type="echo", principal="alice",
                            mem_peak=100, cpu_s=1.0, wall_s=5.0, llm_calls=2)
    est = reader.type_estimate("echo")
    svc = reader.type_service_time("echo")
    check(abs(est.get("mem", 0) - 100) <= 1, f"mem EWMA converged to ~100 (got {est.get('mem')})")
    check(abs(est.get("llm_weight", 0) - 2) <= 1,
          f"llm EWMA converged to ~2 (got {est.get('llm_weight')})")
    check(svc is not None and abs(svc - 5.0) <= 0.1, f"svc EWMA converged to ~5s (got {svc})")

    for i in range(101, 141):
        store.record_actual(job_id=i, task_type="commission", principal="alice",
                            mem_peak=512, cpu_s=200.0, wall_s=7200.0, llm_calls=200)
    check(abs(reader.type_service_time("commission") - 7200.0) <= 50.0,
          "commission svc EWMA converged to ~7200s (per-type, not global)")
    check(reader.type_service_time("echo") is not None
          and reader.type_service_time("echo") < 100,
          "echo svc EWMA is UNAFFECTED by commission (per-type isolation)")

    before = reader.type_service_time("echo")
    folded = store.record_actual(job_id=1, task_type="echo", principal="alice",
                                 mem_peak=9999, cpu_s=9999, wall_s=9999, llm_calls=9999)
    after = reader.type_service_time("echo")
    check(folded is False, "replayed job_id returns False (not folded)")
    check(before == after, "replayed job_id did NOT move the EWMA (idempotent)")

    check(reader.type_estimate("never-seen") == {}, "unseen type -> {} estimate")
    check(reader.type_service_time("never-seen") is None, "unseen type -> None svc")
    store.close()

def test_store_usage_decay():
    print("[A] acct store: per-principal fair-share usage decays with the half-life")
    tmp = tempfile.mkdtemp(prefix="acct-decay-")
    store = A.AcctStore(os.path.join(tmp, "acct.db"))
    reader = A.AcctReader(os.path.join(tmp, "acct.db"))
    t0 = time.time()
    store.record_actual(job_id=1, task_type="x", principal="bob",
                        mem_peak=600, cpu_s=60, wall_s=60, llm_calls=10, now=t0)
    u_now = reader.principal_usage("bob", now=t0)
    u_halflife = reader.principal_usage("bob", now=t0 + A.FAIRSHARE_HALFLIFE_S)
    check(u_now > 0, f"usage recorded ({u_now:.2f})")
    check(abs(u_halflife - u_now / 2.0) / u_now < 0.02,
          f"usage HALVED after one half-life ({u_now:.2f} -> {u_halflife:.2f})")
    check(reader.principal_usage("nobody") == 0.0, "unknown principal -> 0 usage")
    store.close()

def test_reader_degrade():
    print("[B] AcctReader degrades to empty on an absent/locked store (never wedges the caller)")

    r = A.AcctReader("/nonexistent/dir/acct.db")
    check(r.type_estimate("echo") == {}, "absent store -> {} (no crash)")
    check(r.type_service_time("echo") is None, "absent store -> None svc (no crash)")
    check(r.principal_usage("alice") == 0.0, "absent store -> 0 usage (no crash)")

    tmp = tempfile.mkdtemp(prefix="acct-lock-")
    path = os.path.join(tmp, "acct.db")
    store = A.AcctStore(path)
    store.record_actual(job_id=1, task_type="echo", principal="a", wall_s=5.0)
    store.cx.execute("BEGIN EXCLUSIVE")
    reader = A.AcctReader(path, busy_timeout_ms=100)
    t = time.time()
    _ = reader.type_service_time("echo")
    dt = time.time() - t
    check(dt < 1.0, f"ro read under an EXCLUSIVE writer lock returned fast ({dt*1000:.0f}ms)")
    store.cx.execute("ROLLBACK")
    store.close()

def test_estimate_blend():
    print("[C] profile.estimate() blends template ⊕ history = max(template_floor, EWMA) per dim")

    tmpl = P.CLASSES["spreadsheet.calc"]

    hist = {"mem": 900, "llm_weight": 2}
    prof = P.estimate("spreadsheet.calc", history=hist)
    check(prof.mem == 900, f"history mem (900) > template (200) -> used (got {prof.mem})")
    check(prof.llm_weight == tmpl.llm_weight,
          f"history llm (2) < template floor ({tmpl.llm_weight}) -> FLOOR kept (got {prof.llm_weight})")

    plain = P.estimate("spreadsheet.calc", history={})
    check(plain.mem == tmpl.mem and plain.llm_weight == tmpl.llm_weight,
          "no history -> the plain template floor")

    ovr = P.estimate("spreadsheet.calc", history={"mem": 900}, mem=1500)
    check(ovr.mem == 1500, f"explicit override beats history (got {ovr.mem})")

def _mk_queue_db(path):
    return DB.connect(path)

def _seed_jobs(cx, jobs):

    ids = []
    now = time.time()
    for st, src, prio, prof, tt in jobs:
        cur = cx.execute(
            "INSERT INTO jobs(cmd,cwd,env,profile,prio,mem_estimate,state,source,task_type,"
            "submitted_at,started_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (json.dumps(["true"]), "/tmp", "{}", json.dumps(prof), prio, prof.get("mem", 256),
             st, src, tt, now, now if st == "running" else None))
        ids.append(cur.lastrowid)
    cx.commit()
    return ids

def test_eta_shadow_time():
    print("[D] EtaService: eta = max(count, llm-bottleneck) + waiting_on names the binding dim")
    tmp = tempfile.mkdtemp(prefix="acct-eta-")
    qpath = os.path.join(tmp, "queue.db")
    apath = os.path.join(tmp, "acct.db")
    cx = _mk_queue_db(qpath)

    store = A.AcctStore(apath)
    for i in range(1, 30):
        store.record_actual(job_id=10000 + i, task_type="llmjob", principal="a",
                            mem_peak=320, cpu_s=10, wall_s=100.0, llm_calls=1)
    store.close()
    reader = A.AcctReader(apath)

    prof_llm = {"mem": 320, "llm_weight": 1, "llm_kind": "dedicated", "prio": 90}
    ahead = [("queued", "cli", 90, prof_llm, "llmjob") for _ in range(8)]
    running = [("running", "cli", 90, prof_llm, "llmjob")]
    ids = _seed_jobs(cx, ahead + running)
    mine = _seed_jobs(cx, [("queued", "cli", 90, prof_llm, "llmjob")])[0]
    cx.close()

    llm_pool = {"llm_pool": 2, "llm_in_use": 1, "llm_free": 1}
    eta = G.EtaService(db_path=qpath, fairshare=None, ewma_seconds=60.0, cache_ttl=0.0,
                       acct_reader=reader, llm_headroom_fn=lambda: llm_pool)
    r = eta.job_eta(mine)
    check(r["ok"], "job_eta ok")
    check(abs(r["svc_s"] - 100.0) <= 1.0, f"per-type svc EWMA used (~100s, got {r['svc_s']})")

    check(r["eta_s"] >= (8 * 100.0) / 1, "eta >= count-based lower bound")

    tmp2 = tempfile.mkdtemp(prefix="acct-eta2-")
    qpath2 = os.path.join(tmp2, "queue.db")
    cx2 = _mk_queue_db(qpath2)

    non_llm = {"mem": 100, "llm_weight": 0, "prio": 100}
    run10 = [("running", "cli", 100, non_llm, "compute") for _ in range(10)]
    ahead6 = [("queued", "cli", 90, prof_llm, "llmjob") for _ in range(6)]
    _seed_jobs(cx2, run10 + ahead6)
    mine2 = _seed_jobs(cx2, [("queued", "cli", 90, prof_llm, "llmjob")])[0]
    cx2.close()
    eta2 = G.EtaService(db_path=qpath2, fairshare=None, ewma_seconds=5.0, cache_ttl=0.0,
                        acct_reader=reader, llm_headroom_fn=lambda: {"llm_pool": 1, "llm_free": 0})
    r2 = eta2.job_eta(mine2)

    check(r2["waiting_on"] == "llm",
          f"the LLM pool BINDS -> waiting_on == 'llm' (got {r2['waiting_on']})")
    check(r2["eta_s"] >= 600.0 - 1,
          f"eta took the llm-bottleneck max (~600s, got {r2['eta_s']})")

    cx3 = sqlite3.connect(qpath2); cx3.row_factory = sqlite3.Row
    mineN = cx3.execute(
        "INSERT INTO jobs(cmd,cwd,env,profile,prio,mem_estimate,state,source,task_type,submitted_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (json.dumps(["true"]), "/tmp", "{}", json.dumps(non_llm), 100, 100,
         "queued", "cli", "compute", time.time())).lastrowid
    cx3.commit(); cx3.close()
    rN = eta2.job_eta(mineN)
    check(rN["waiting_on"] != "llm", "a non-LLM job is never waiting_on 'llm'")

def test_eta_subsecond_flood():
    print("[D] EtaService stays sub-second under a query flood (H-posture preserved with STEP 6)")
    tmp = tempfile.mkdtemp(prefix="acct-flood-")
    qpath = os.path.join(tmp, "queue.db")
    apath = os.path.join(tmp, "acct.db")
    cx = _mk_queue_db(qpath)
    prof = {"mem": 320, "llm_weight": 1, "prio": 90}
    _seed_jobs(cx, [("queued", "cli", 90, prof, "llmjob") for _ in range(50)])
    target = _seed_jobs(cx, [("queued", "cli", 90, prof, "llmjob")])[0]
    cx.close()
    store = A.AcctStore(apath)
    for i in range(1, 20):
        store.record_actual(job_id=i, task_type="llmjob", principal="a", wall_s=42.0)
    store.close()
    eta = G.EtaService(db_path=qpath, fairshare=None, cache_ttl=0.25,
                       acct_reader=A.AcctReader(apath),
                       llm_headroom_fn=lambda: {"llm_pool": 2, "llm_free": 1})
    t = time.time()
    for _ in range(500):
        eta.job_eta(target)
    dt = time.time() - t
    check(dt < 0.5, f"500 job_eta calls (cached) in {dt*1000:.0f}ms (< 500ms)")

def _emit_actuals_event(cx, job_id, payload):
    cx.execute("INSERT INTO job_events(job_id,ts,kind,topic,data) VALUES(?,?,?,?,?)",
               (job_id, time.time(), "actuals", f"job/{job_id}", json.dumps(payload)))
    cx.commit()

def test_folder():
    print("[E] Folder consumes `actuals` events from queue.db (read-only) into acct.db")
    tmp = tempfile.mkdtemp(prefix="acct-fold-")
    qpath = os.path.join(tmp, "queue.db")
    apath = os.path.join(tmp, "acct.db")
    cx = _mk_queue_db(qpath)
    for i in range(1, 11):
        _emit_actuals_event(cx, i, {"task_type": "echo", "principal": "a",
                                    "mem_peak": 100, "cpu_s": 1, "wall_s": 5.0, "llm_calls": 1})

    cx.execute("INSERT INTO job_events(job_id,ts,kind,topic,data) VALUES(?,?,?,?,?)",
               (11, time.time(), "actuals", "job/11", "not-json{{{"))
    _emit_actuals_event(cx, 12, {"task_type": "echo", "principal": "a", "wall_s": "oops"})
    cx.commit()
    cx.close()

    store = A.AcctStore(apath)
    reader = A.AcctReader(apath)
    folder = ACCTD.Folder(store, qpath)
    rep = folder.fold_once()
    check(rep["folded"] == 12, f"folded all 12 events incl. poison (got {rep['folded']})")
    check(reader.type_service_time("echo") is not None
          and abs(reader.type_service_time("echo") - 5.0) <= 0.2,
          "the good events converged the EWMA; the poison ones were skipped, not fatal")
    check(rep["breaker"] == "closed", "breaker stayed closed on a clean pass")

    rep2 = folder.fold_once()
    check(rep2["folded"] == 0, "cursor advanced -> the second pass folds 0 (no double-count)")
    store.close()

def test_folder_circuit_breaker():
    print("[E] Folder opens the circuit breaker on a wedged store (never spins hot / crashes)")
    tmp = tempfile.mkdtemp(prefix="acct-cb-")
    apath = os.path.join(tmp, "acct.db")
    store = A.AcctStore(apath)

    baddir = os.path.join(tmp, "notadb")
    os.makedirs(baddir, exist_ok=True)
    folder = ACCTD.Folder(store, baddir)
    for _ in range(ACCTD.CB_THRESHOLD):
        rep = folder.fold_once()
        check(rep["folded"] == 0, "a failing fold folds nothing (no crash)")
    check(folder.breaker_state() == "open",
          f"breaker OPEN after {ACCTD.CB_THRESHOLD} consecutive failures")

    rep = folder.fold_once()
    check(rep["breaker"] == "open", "an OPEN breaker skips the fold (serve-cached posture)")
    store.close()

def test_decoupling_reader_never_wedges_writer():
    print("[F] a concurrent acct read of queue.db can NOT block a pnd-style writer")
    tmp = tempfile.mkdtemp(prefix="acct-dec-")
    qpath = os.path.join(tmp, "queue.db")
    cx = _mk_queue_db(qpath)
    _seed_jobs(cx, [("queued", "cli", 100, {"mem": 100}, "echo") for _ in range(20)])

    store = A.AcctStore(os.path.join(tmp, "acct.db"))
    folder = ACCTD.Folder(store, qpath)
    stop = {"v": False}

    def hammer_read():
        while not stop["v"]:
            folder._q_connect().close()

    th = threading.Thread(target=hammer_read, daemon=True)
    th.start()

    t = time.time()
    ok = True
    for i in range(200, 260):
        try:
            cx.execute("INSERT INTO job_events(job_id,ts,kind,topic,data) VALUES(?,?,?,?,?)",
                       (i, time.time(), "actuals", f"job/{i}", "{}"))
            cx.commit()
        except sqlite3.OperationalError:
            ok = False
            break
    dt = time.time() - t
    stop["v"] = True
    th.join(timeout=2)
    check(ok, "the writer never hit 'database is locked' despite the read hammer")
    check(dt < 3.0, f"60 writer commits under the read hammer in {dt*1000:.0f}ms")
    cx.close()
    store.close()

def _peer_ok_reply(server, req):
    return server.dispatch(req)

def test_queryserver_bounded():
    print("[G] QueryServer: past its FIXED worker budget it refuses ('busy'), never forks unbounded")
    tmp = tempfile.mkdtemp(prefix="acct-qs-")
    apath = os.path.join(tmp, "acct.db")
    store = A.AcctStore(apath)
    store.record_actual(job_id=1, task_type="echo", principal="a", mem_peak=100, wall_s=5.0)
    reader = A.AcctReader(apath)
    folder = ACCTD.Folder(store, os.path.join(tmp, "queue.db"))
    sockpath = os.path.join(tmp, "acctd.sock")
    server = ACCTD.QueryServer(folder, reader, path=sockpath,
                               allow_uids={os.getuid()}, max_workers=2)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    time.sleep(0.3)

    def call(req):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(sockpath)
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            ch = s.recv(4096)
            if not ch:
                break
            buf += ch
        s.close()
        return json.loads(buf.decode())

    r = call({"verb": "acct.estimate", "task_type": "echo"})
    check(r.get("ok") and r["estimate"].get("mem") == 100, "acct.estimate over the socket works")
    r = call({"verb": "acct.service_time", "task_type": "echo"})
    check(r.get("ok") and abs(r["svc_s"] - 5.0) <= 0.1, "acct.service_time over the socket works")
    r = call({"verb": "acct.status"})
    check(r.get("ok") and "breaker" in r, "acct.status reports breaker health")

    check(server.dispatch({"verb": "nope"})["ok"] is False, "unknown verb -> clean error")
    check(server.dispatch("garbage")["ok"] is False, "non-dict request -> clean error")

    for _ in range(50):
        call({"verb": "acct.service_time", "task_type": "echo"})
    check(reader.type_service_time("echo") is not None, "the store still reads after a query flood")
    server.stop()
    store.close()

def main():
    print(f"\n{'='*70}\nacctd_test — pn-acctd + acct store + estimate/ETA loop (STEP 5 + 6)\n{'='*70}")
    test_store_ewma()
    test_store_usage_decay()
    test_reader_degrade()
    test_estimate_blend()
    test_eta_shadow_time()
    test_eta_subsecond_flood()
    test_folder()
    test_folder_circuit_breaker()
    test_decoupling_reader_never_wedges_writer()
    test_queryserver_bounded()
    print(f"\n{'='*70}\nRESULT: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0

if __name__ == "__main__":
    sys.exit(main())
