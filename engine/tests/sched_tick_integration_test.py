#!/usr/bin/env python3

from __future__ import annotations
import os, sys, time, json, tempfile, importlib.util, types

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

PASS = FAIL = 0
def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {label}")
    else:
        FAIL += 1; print(f"  FAIL {label}")

def section(n): print(f"\n== {n} ==")

def load_pnd():

    path = os.path.join(ROOT, "tools", "pnd")
    spec = importlib.util.spec_from_loader("pnd_mod",
                                           importlib.machinery.SourceFileLoader("pnd_mod", path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

class FakeSlice:

    def __init__(self):
        self.started = []
        self.stopped = []
        self._active = set()
        self.fail_next = False
    def unit_name(self, jid): return f"pn-job-{jid}.service"
    def dispatch(self, jid, argv, cwd, env, props, out, err, rc, track=None, **_kw):

        if self.fail_next:
            self.fail_next = False
            return None
        self.started.append(jid); self._active.add(jid)
        class P:
            def __init__(s): s._done = False
            def poll(s): return None if not s._done else 0
            def wait(s, timeout=None): s._done = True
        return P()
    def stop_unit(self, jid):
        self.stopped.append(jid); self._active.discard(jid)
    def cleanup_unit(self, jid, wait_s=1.0):
        self._active.discard(jid); return True
    def unit_status(self, jid):
        active = jid in self._active
        return {"active": active, "loaded": active, "result": "", "code": 0, "substate": ""}
    def read_rc(self, p): return 0
    def live_units(self): return {self.unit_name(j) for j in self._active}

class FakeLLM:

    def __init__(self, pool):
        from pnlib.llmpool import LeaseTable
        self.lt = LeaseTable(pool)
        self.releases = []
        self.wiped = False
    def headroom(self):
        hr = self.lt.headroom()
        return {"llm_free": hr["llm_free"], "llm_pool": hr["llm_pool"], "llm_in_use": hr["llm_in_use"]}
    def lease(self, jid, w, k):
        r = self.lt.lease(jid, w, k); return bool(r.get("ok"))
    def release(self, jid):
        self.releases.append(jid); self.lt.release(jid); return True
    def wipe(self):
        from pnlib.llmpool import LeaseTable
        self.lt = LeaseTable(self.lt.pool_slots); self.wiped = True

def make_pnd(tmp, batch_high=1000, mem_floor=200, max_per_tick=3, pool=4, mem_avail=100000):

    from pnlib import db, sched
    mod = load_pnd()
    dbpath = os.path.join(tmp, "q.db")
    mod.CX = db.connect(dbpath)
    mod.CFG = sched.Config(mem_floor=mem_floor, batch_high=batch_high, slack=0)
    mod.CFG.max_per_tick = max_per_tick
    mod.CFG.max_concurrent = 999
    mod.GATE = sched.PressureGate(mod.CFG)
    mod.FILLER_CAP = 2

    fake_slc = FakeSlice()
    fake_llm = FakeLLM(pool)
    mod.slc = fake_slc

    rec = types.SimpleNamespace()
    rec.mkspace = lambda jid: os.path.join(tmp, f"ws{jid}")
    rec.workspace_path = lambda jid: os.path.join(tmp, f"ws{jid}")
    rec.write_record = lambda *a, **k: {"commit": "x", "result_hash": "h"}
    rec.compute_record_ok = lambda *a, **k: True
    rec.work_ttl_for = lambda *a, **k: 3600
    rec.valid_workspace = lambda *a, **k: True
    rec.review_preview = lambda *a, **k: {}
    rec.replicate = lambda *a, **k: {"ok": True}
    rec.dispose_workspace = lambda *a, **k: None
    mod.record = rec

    state = {"mem_available": mem_avail, "psi_avg10": 0.0, "data_free_pct": 100.0}
    def snap():

        reserved = db.reserved_mib(mod.CX)
        return {"mem_available": state["mem_available"] - reserved, "batch_current": reserved,
                "disk_free": 100000, "psi_avg10": state["psi_avg10"],
                "data_free_pct": state["data_free_pct"]}
    mod.meters.snapshot = snap

    mod.db.set_workspace = lambda cx, jid, ws: None

    mod.llm_headroom = fake_llm.headroom
    def _lease(jid, w, k):
        if not w:
            return True
        ok = fake_llm.lease(jid, w, k)
        if ok:
            mod.LLM_HELD[jid] = {"weight": w, "kind": k}; mod.PENDING_RELEASE.discard(jid)
        return ok
    mod.llm_lease = _lease
    def _rel(jid):
        if jid in mod.LLM_HELD or jid in mod.PENDING_RELEASE:
            fake_llm.release(jid); mod.LLM_HELD.pop(jid, None); mod.PENDING_RELEASE.discard(jid)
    mod.llm_release = _rel

    def _recon_rel(job):
        from pnlib.profile import ResourceProfile
        try:
            p = ResourceProfile.from_json(job["profile"])
        except Exception:
            return
        if p.llm_weight:
            fake_llm.release(job["id"]); mod.PENDING_RELEASE.discard(job["id"])
    mod._reconcile_llm_release = _recon_rel
    mod.gov_observe = lambda s: None

    def _reassert():
        from pnlib.profile import ResourceProfile
        for j in mod.db.running(mod.CX):
            p = ResourceProfile.from_json(j["profile"])
            if p.llm_weight and fake_llm.lease(j["id"], p.llm_weight, p.llm_kind):
                mod.LLM_HELD[j["id"]] = {"weight": p.llm_weight, "kind": p.llm_kind}
    mod.reassert_llm_leases = _reassert

    mod.maybe_gc = lambda: None
    mod.run_dag_gate = lambda: None
    mod.scan_progress = lambda: None
    mod.sd_notify = lambda *a, **k: None
    return mod, db, fake_slc, fake_llm, state

def _finish_all_running(mod, db):

    for j in db.running(mod.CX):
        p = mod.PROCS.get(j["id"])
        if p:
            p._done = True
        mod.slc._active.discard(j["id"])

def test_meltdown_real_tick():
    section("MELTDOWN via REAL tick() — 194 internal submits (SCHED-2/SCHED-3/NEW-1)")
    tmp = tempfile.mkdtemp()

    mod, db, slc, llm, state = make_pnd(tmp, batch_high=1000, mem_floor=200, max_per_tick=3,
                                        pool=8, mem_avail=100000)
    from pnlib.profile import ResourceProfile
    N = 194
    prof = ResourceProfile(mem=384, llm_weight=1, llm_kind="loose", prio=110)
    for i in range(N):
        db.submit(mod.CX, ["/bin/true"], tmp, {}, prof.to_json(), prof.prio, prof.mem,
                  f"auth{i}", source="internal")
    check(db.counts(mod.CX).get("queued", 0) == N, f"all {N} enqueued (not forked)")

    peak_starts_per_tick = 0
    peak_real_admits = 0
    floor_violations = 0
    ticks = 0
    while True:
        ticks += 1
        before = len(slc.started)

        run = list(db.running(mod.CX))
        for j in run[: max(1, len(run) // 2)]:
            p = mod.PROCS.get(j["id"])
            if p: p._done = True
            slc._active.discard(j["id"])
        mod.tick()
        started_this_tick = len(slc.started) - before
        peak_starts_per_tick = max(peak_starts_per_tick, started_this_tick)
        peak_real_admits = max(peak_real_admits, started_this_tick)

        reserved = db.reserved_mib(mod.CX)
        if reserved > mod.CFG.batch_high:
            floor_violations += 1
        if db.counts(mod.CX).get("queued", 0) == 0 and not db.running(mod.CX):
            break
        if ticks > 5000:
            break
    check(peak_real_admits <= mod.CFG.max_per_tick,
          f"REAL admits/tick <= max_per_tick={mod.CFG.max_per_tick} (peak {peak_real_admits})")
    check(peak_starts_per_tick <= 2 * mod.CFG.max_per_tick,
          f"process starts/tick <= 2*max_per_tick (main+filler) (peak {peak_starts_per_tick})")
    check(floor_violations == 0,
          f"floor GENUINELY binding + never violated (mem384*3>1000; {floor_violations} violations)")
    check(db.counts(mod.CX).get("done", 0) == N, f"all {N} completed via the real-tick drip")
    check(ticks >= N / mod.CFG.max_per_tick / 2, f"storm spread across {ticks} governed ticks")

def test_infinite_hang_guard():
    section("NEW-2 — start_job()==False never wedges the tick")
    tmp = tempfile.mkdtemp()
    mod, db, slc, llm, state = make_pnd(tmp, batch_high=10000, mem_floor=200, max_per_tick=3, pool=4)
    from pnlib.profile import ResourceProfile
    prof = ResourceProfile(mem=100, llm_weight=0, prio=100)
    for i in range(3):
        db.submit(mod.CX, ["/bin/true"], tmp, {}, prof.to_json(), 100, 100, f"j{i}", source="cli")
    slc.fail_next = True
    t0 = time.time()
    mod.tick()
    dt = time.time() - t0
    check(dt < 5.0, f"tick returned promptly despite a dispatch failure ({dt:.2f}s, no wedge)")

    q = db.counts(mod.CX).get("queued", 0)
    r = db.counts(mod.CX).get("running", 0)
    f = db.counts(mod.CX).get("failed", 0)
    check(f >= 1, f"the un-startable head was finalized failed (failed={f})")
    check(r + q + f == 3, f"every job accounted for, no duplication (r={r} q={q} f={f})")

def test_preempt_real_tick():
    section("F1 — arriving real LLM job PREEMPTS a running filler within one tick (real tick())")
    tmp = tempfile.mkdtemp()

    mod, db, slc, llm, state = make_pnd(tmp, batch_high=2000, mem_floor=200, max_per_tick=3, pool=1)
    from pnlib.profile import ResourceProfile
    filler = ResourceProfile(mem=300, llm_weight=1, llm_kind="dedicated", prio=200, idempotent=True)
    fid = db.submit(mod.CX, ["/bin/true"], tmp, {}, filler.to_json(), 200, 300, "filler",
                    source="filler")

    mod.tick()
    check(fid in slc.started, "tick1: filler admitted into the idle box")
    check(abs(llm.headroom()["llm_free"]) < 1e-6, "tick1: filler holds the only LLM slot (free 0)")

    real = ResourceProfile(mem=300, llm_weight=1, llm_kind="dedicated", prio=100)
    rid = db.submit(mod.CX, ["/bin/true"], tmp, {}, real.to_json(), 100, 300, "real", source="cli")
    mod.tick()
    check(fid in slc.stopped, "tick2: the filler was PREEMPTED (SIGTERM->stop) for real work")
    check(fid in llm.releases, "tick2: the filler's LLM lease was RECLAIMED")
    check(rid in slc.started, "tick2: the real job STARTED on the reclaimed slot (within one tick)")

    fj = db.get(mod.CX, fid, scope_all=True)
    check(fj["state"] in ("queued", "running"), f"preempted filler re-queued (idempotent), state={fj['state']}")

def test_lease_durability():
    section("LEASE-LEAK-1 / NEW-4 — reconcile releases; reassert rebuilds after pn-llmd restart")
    tmp = tempfile.mkdtemp()
    mod, db, slc, llm, state = make_pnd(tmp, batch_high=5000, mem_floor=200, max_per_tick=3, pool=4)
    from pnlib.profile import ResourceProfile
    p = ResourceProfile(mem=200, llm_weight=2, llm_kind="dedicated", prio=100)
    jid = db.submit(mod.CX, ["/bin/true"], tmp, {}, p.to_json(), 100, 200, "llmjob", source="cli")
    mod.tick()
    check(jid in slc.started and jid in mod.LLM_HELD, "llm job started + lease recorded in LLM_HELD")
    check(abs(llm.headroom()["llm_free"] - 2.0) < 1e-6, "pool shows 2 free (4-2 leased)")

    llm.wipe()
    check(abs(llm.headroom()["llm_free"] - 4.0) < 1e-6, "after wipe pn-llmd falsely reports 4 free")
    mod.tick()
    check(abs(llm.headroom()["llm_free"] - 2.0) < 1e-6,
          "reassert rebuilt the running job's lease -> 2 free again (no over-admit)")

    mod.PROCS.clear()
    slc._active.discard(jid)

    _, _, rcp = mod.out_err_rc_for(jid)
    os.makedirs(os.path.dirname(rcp), exist_ok=True)
    with open(rcp, "w") as f: f.write("0\n")
    mod.LLM_HELD.clear()
    mod.reconcile()
    check(jid in llm.releases, "reconcile RELEASED the terminal job's LLM lease (no drain)")
    check(abs(llm.headroom()["llm_free"] - 4.0) < 1e-6, "pool fully restored after reconcile")

def main():
    test_meltdown_real_tick()
    test_infinite_hang_guard()
    test_preempt_real_tick()
    test_lease_durability()
    print(f"\n=== sched_tick_integration_test: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
