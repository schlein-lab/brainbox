#!/usr/bin/env python3

from __future__ import annotations
import os, sys, time, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from pnlib import sched, profile, llmpool, db
from pnlib.profile import ResourceProfile

PASS = FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")

def section(name):
    print(f"\n== {name} ==")

def test_step1_lease():
    section("STEP 1 — pn-llmd lease/release (LeaseTable)")
    lt = llmpool.LeaseTable(pool_slots=4)
    hr0 = lt.headroom()
    check(hr0["llm_free"] == 4 and hr0["llm_pool"] == 4, "fresh table: 4/4 free")

    r = lt.lease("j1", 2, "dedicated")
    check(r["ok"] and r.get("leased"), "dedicated lease(2) ok")
    check(lt.headroom()["llm_free"] == 2, "free decrements to 2 after dedicated(2)")

    r = lt.lease("j2", 4, "loose")
    check(r["ok"], "loose lease(4) ok")
    check(abs(lt.headroom()["llm_free"] - 1.0) < 1e-6, "loose(4)=1 slot -> free 1")

    r = lt.lease("j3", 2, "dedicated")
    check(not r["ok"] and r.get("blocked"), "saturating lease BLOCKED (backpressure)")
    check(abs(lt.headroom()["llm_free"] - 1.0) < 1e-6, "free unchanged after a blocked lease")

    r = lt.lease("j0", 0, "loose")
    check(r["ok"] and r.get("noop"), "weight-0 lease is a no-op")
    check(abs(lt.headroom()["llm_free"] - 1.0) < 1e-6, "free unchanged by a weight-0 lease")

    lt.release("j1")
    check(abs(lt.headroom()["llm_free"] - 3.0) < 1e-6, "release(j1) restores 2 slots -> free 3")
    r = lt.release("j1")
    check(r["ok"] and not r["released"], "double release is a safe no-op")

    r = lt.lease("j3", 2, "dedicated")
    check(r["ok"], "after release, the once-blocked lease now fits")

    before = lt.headroom()["llm_free"]
    r = lt.lease("j3", 2, "dedicated")
    check(r["ok"] and r.get("already"), "re-lease same job_id is idempotent")
    check(abs(lt.headroom()["llm_free"] - before) < 1e-6, "re-lease does not double-charge")

    lt2 = llmpool.LeaseTable(pool_slots=4, lease_ttl_s=0.05)
    lt2.lease("live", 1, "dedicated")
    lt2.lease("orphan", 1, "dedicated")
    time.sleep(0.08)
    lt2.lease("live", 1, "dedicated")
    rep = lt2.sweep_expired()
    check("orphan" in rep["expired"] and "live" not in rep["expired"],
          "sweep expires the un-renewed lease, keeps the renewed (re-asserted) one")
    check(abs(lt2.headroom()["llm_free"] - 3.0) < 1e-6, "orphan slot reclaimed by the TTL sweep")

def test_step2_llm_ok():
    section("STEP 2 — sched.llm_ok admission predicate")
    ded = ResourceProfile(llm_weight=2, llm_kind="dedicated")
    loose = ResourceProfile(llm_weight=6, llm_kind="loose")
    none = ResourceProfile(llm_weight=0)

    check(sched.llm_ok({"llm_free": 0}, none).admit, "llm_weight=0 admitted even at free=0")
    check(sched.llm_ok(None, none).admit, "llm_weight=0 admitted with no headroom")

    check(not sched.llm_ok({"llm_free": 1}, ded).admit, "dedicated(2) refused when free=1")

    check(sched.llm_ok({"llm_free": 2}, ded).admit, "dedicated(2) admitted when free=2")
    check(sched.llm_ok({"llm_free": 2}, loose).admit, "loose(6=1.5) admitted when free=2")
    check(not sched.llm_ok({"llm_free": 1}, loose).admit, "loose(6=1.5) refused when free=1")

    check(not sched.llm_ok({"llm_free": 3}, ded, llm_reserved=2).admit,
          "dedicated(2) refused when free=3 but 2 already reserved this tick")
    check(sched.llm_ok({"llm_free": 4}, ded, llm_reserved=2).admit,
          "dedicated(2) admitted when free=4 and 2 reserved this tick")

    check(not sched.llm_ok({"llm_free": None}, ded).admit, "LLM down (free=None) -> dedicated refused")
    check(not sched.llm_ok({"llm_free": None}, loose).admit, "LLM down (free=None) -> loose refused")
    check(sched.llm_ok({"llm_free": None}, none).admit, "LLM down -> non-LLM still admitted")

    check(sched.llm_ok({}, ded).admit, "no llm_free key -> gate skipped (admitted)")
    check(sched.llm_ok(None, ded).admit, "hr=None -> gate skipped (admitted)")

def test_step2_reserved_llm():
    section("STEP 2 — db.reserved_llm accounting")
    path = os.path.join(tempfile.mkdtemp(), "q.db")
    cx = db.connect(path)

    p1 = ResourceProfile(llm_weight=2, llm_kind="dedicated").to_json()
    p2 = ResourceProfile(llm_weight=6, llm_kind="loose").to_json()
    p3 = ResourceProfile(llm_weight=0).to_json()
    for prof in (p1, p2, p3):
        jid = db.submit(cx, ["/bin/true"], "/tmp", {}, prof, 100, 100, None, source="internal")
        db.mark_running(cx, jid, f"u{jid}", "/tmp/x")
    check(abs(db.reserved_llm(cx) - 3.5) < 1e-6, "reserved_llm sums running profiles = 3.5 slots")
    cx.close()

def test_step3_position_agrees():
    section("STEP 3 — db.position == db.next_queued (aging-aware)")
    path = os.path.join(tempfile.mkdtemp(), "q.db")
    cx = db.connect(path)
    now = time.time()

    a = db.submit(cx, ["/bin/true"], "/tmp", {}, ResourceProfile().to_json(), 120, 100, "A",
                  source="cli")
    cx.execute("UPDATE jobs SET submitted_at=? WHERE id=?", (now - 7200, a)); cx.commit()

    b = db.submit(cx, ["/bin/true"], "/tmp", {}, ResourceProfile().to_json(), 100, 100, "B",
                  source="cli")

    c = db.submit(cx, ["/bin/true"], "/tmp", {}, ResourceProfile().to_json(), 100, 100, "C",
                  source="cli")

    order = []
    seen = set()
    while True:

        rows = [dict(r) for r in cx.execute(
            "SELECT * FROM jobs WHERE state='queued' AND id NOT IN (%s)" %
            (",".join(str(i) for i in seen) or "0")).fetchall()]
        if not rows:
            break
        tnow = time.time()
        head = min(rows, key=lambda r: db._eff_prio(r, tnow))
        order.append(head["id"]); seen.add(head["id"])

    agree = all(db.position(cx, jid) == (order.index(jid) + 1) for jid in order)
    check(agree, f"position ranks == dispatch order {order} (B=100 fresh, A=120 aged, C=100)")

    check(db.position(cx, b) == 1, "fresh prio-100 B is position 1")
    check(db.position(cx, a) == 3, "aged-but-still-108 A is position 3 (aging didn't overtake 100s)")

    cx.execute("UPDATE jobs SET submitted_at=? WHERE id=?", (now - 7200 * 5, a)); cx.commit()
    head = db.next_queued(cx)
    check(head["id"] == a and db.position(cx, a) == 1,
          "after heavy aging A becomes head AND position(A)==1 (still agree)")
    cx.close()

def test_step4_ewma():
    section("STEP 4 — EtaService.observe_completion moves svc_ewma off the 60s seed")
    from pnlib.governor import EtaService
    svc = EtaService(db_path=os.path.join(tempfile.mkdtemp(), "q.db"))
    seed = svc._ewma()
    check(abs(seed - 60.0) < 1e-6, "svc_ewma starts at the 60s seed")
    for _ in range(20):
        svc.observe_completion(5.0)
    moved = svc._ewma()
    check(moved < 30.0, f"svc_ewma moved off the seed toward 5s (now {moved:.1f}s)")

    svc.observe_completion(0)
    svc.observe_completion(-3)
    check(abs(svc._ewma() - moved) < 1e-6, "non-positive observations ignored")

def test_step7_template():
    section("STEP 7 — estimate() freezes the task-type template; unseen -> class prior")

    patch = '{"mem":200,"llm_weight":6,"llm_kind":"loose","cpu_weight":40,"prio":100}'
    p = profile.estimate("spreadsheet.calc", template_patch=patch)
    check(p.mem == 200 and p.llm_weight == 6 and p.llm_kind == "loose",
          "typed submit freezes the template profile (mem200/llm6/loose)")

    rp = '{"mem":512,"llm_weight":1,"llm_kind":"dedicated","prio":200,"idempotent":true}'
    r = profile.estimate("repro.room", template_patch=rp)
    check(r.mem == 512 and r.prio == 200 and r.idempotent and r.llm_kind == "dedicated",
          "repro.room template frozen (mem512/prio200/idempotent/dedicated)")

    u = profile.estimate("nope.unseen", template_patch=None)
    base = profile.CLASSES["compute"]
    check(u.mem == base.mem and u.llm_weight == base.llm_weight,
          "unseen type w/o template -> compute class prior")

    blended = profile.estimate("spreadsheet.calc", template_patch=patch,
                               history={"mem": 50, "llm_weight": 9})
    check(blended.mem == 200, "history below template floor is clamped UP (mem stays 200)")
    check(blended.llm_weight == 9, "history above template floor is used (llm_weight -> 9)")

    o = profile.estimate("spreadsheet.calc", template_patch=patch, mem=999)
    check(o.mem == 999, "explicit override beats the template")

def test_step8_filler_and_preempt():
    section("STEP 8 — filler backfill (fits iff mem+LLM) + preemption")

    cfg = sched.Config(mem_floor=200, batch_high=1000, slack=0)
    hr4 = {"llm_free": 4.0}
    room = ResourceProfile(mem=300, llm_weight=1, llm_kind="dedicated")
    snap = {"mem_available": 4000, "batch_current": 0, "disk_free": 100000, "psi_avg10": 0}

    d1 = sched.filler_fits(cfg, dict(snap), room, reserved=0, hr=hr4, llm_reserved=0.0,
                           filler_running=0, pressure_blocked=False, filler_cap=2)
    check(d1.admit, "filler #1 fits (300<=800 ceiling, 1<=4 slots)")

    d2 = sched.filler_fits(cfg, dict(snap), room, reserved=300, hr={"llm_free": 3.0},
                           llm_reserved=1.0, filler_running=1, pressure_blocked=False, filler_cap=2)
    check(d2.admit, "filler #2 fits (600<=800, slots ok)")

    d3 = sched.filler_fits(cfg, dict(snap), room, reserved=600, hr={"llm_free": 2.0},
                           llm_reserved=2.0, filler_running=2, pressure_blocked=False, filler_cap=2)
    check(not d3.admit, "filler #3 refused: filler_cap 2/2 reached")

    d3b = sched.filler_fits(cfg, dict(snap), room, reserved=600, hr=hr4, llm_reserved=0.0,
                            filler_running=0, pressure_blocked=False, filler_cap=5)
    check(not d3b.admit, "filler refused by FILLER_RESERVE margin (900>800) even under cap")

    dl = sched.filler_fits(cfg, dict(snap), room, reserved=0, hr={"llm_free": 0.0}, llm_reserved=0.0,
                           filler_running=0, pressure_blocked=False, filler_cap=5)
    check(not dl.admit, "filler refused when LLM pool full (dedicated session unavailable)")

    dp = sched.filler_fits(cfg, dict(snap), room, reserved=0, hr=hr4, llm_reserved=0.0,
                           filler_running=0, pressure_blocked=True, filler_cap=5)
    check(not dp.admit, "filler yields under memory pressure")

    fillers = [{"id": 10, "mem": 300, "llm": 1.0}, {"id": 11, "mem": 300, "llm": 1.0}]
    plan = sched.preempt_plan(need_mem=400, need_llm=1.0, fillers=fillers)
    check(set(plan) == {10, 11}, "preempt enough fillers to free 400 MiB (both, 300 each)")
    plan2 = sched.preempt_plan(need_mem=200, need_llm=1.0, fillers=fillers)
    check(len(plan2) == 1, "preempt only ONE filler when one suffices (200 MiB + 1 slot)")
    plan3 = sched.preempt_plan(need_mem=0, need_llm=0.0, fillers=fillers)
    check(plan3 == [], "no preemption when nothing is needed (never kills real work for free)")

def test_user_scenario():
    section("STEP 8 — USER SCENARIO: spreadsheet + other + 2 repro rooms")

    cfg = sched.Config(mem_floor=512, batch_high=4096, slack=0)
    spreadsheet = ResourceProfile(mem=200, llm_weight=6, llm_kind="loose")
    other = ResourceProfile(mem=256, llm_weight=0)
    room = ResourceProfile(mem=512, llm_weight=1, llm_kind="dedicated")
    jobs = [("spreadsheet", spreadsheet), ("other", other), ("room1", room), ("room2", room)]

    proj = {"mem_available": 8000, "batch_current": 0, "disk_free": 100000, "psi_avg10": 0}
    hr = {"llm_free": 4.0}
    reserved = 0
    llm_reserved = 0.0
    admitted = []
    for name, prof in jobs:
        dec = sched.evaluate(cfg, proj, prof, running=len(admitted), reserved=reserved,
                             pressure_blocked=False)
        ld = sched.llm_ok(hr, prof, llm_reserved)
        if dec.admit and ld.admit:
            admitted.append(name)
            reserved += prof.mem
            proj["batch_current"] = reserved
            proj["mem_available"] -= prof.mem
            llm_reserved += sched.llm_demand_slots(prof)
    check(admitted == ["spreadsheet", "other", "room1", "room2"],
          f"all 4 admit concurrently when mem+LLM fit (got {admitted})")
    check(abs(llm_reserved - 3.5) < 1e-6, "combined LLM use = 3.5 slots (1.5 loose + 2 dedicated)")

    proj = {"mem_available": 8000, "batch_current": 0, "disk_free": 100000, "psi_avg10": 0}
    hr = {"llm_free": 3.0}
    reserved = 0; llm_reserved = 0.0; admitted = []
    for name, prof in jobs:
        dec = sched.evaluate(cfg, proj, prof, running=len(admitted), reserved=reserved,
                             pressure_blocked=False)
        ld = sched.llm_ok(hr, prof, llm_reserved)
        if dec.admit and ld.admit:
            admitted.append(name)
            reserved += prof.mem; proj["batch_current"] = reserved
            proj["mem_available"] -= prof.mem
            llm_reserved += sched.llm_demand_slots(prof)
    check("room2" not in admitted and "spreadsheet" in admitted,
          f"LLM binds at pool=3: the 2nd room waits on LLM, spreadsheet runs (got {admitted})")

def test_llm_ok_double_count_regression():
    section("F1 REGRESSION — llm_ok does NOT double-count running leases")

    job = ResourceProfile(llm_weight=1, llm_kind="dedicated")

    hr = {"llm_free": 2.0}
    check(sched.llm_ok(hr, job, llm_reserved=0.0).admit,
          "a fitting LLM job is admitted while other LLM jobs run (no double-count starvation)")

    check(not sched.llm_ok(hr, job, llm_reserved=2.0).admit,
          "seeding llm_reserved with running demand WOULD have starved it (the fixed bug)")

def test_step9_meltdown_pure_sanity():
    section("STEP 9 — meltdown pure-sanity (authoritative test = sched_tick_integration_test.py)")

    path = os.path.join(tempfile.mkdtemp(), "q.db")
    cx = db.connect(path)
    N = 194

    prof = ResourceProfile(mem=384, llm_weight=1, llm_kind="loose", prio=110)
    for i in range(N):
        db.submit(cx, ["/usr/bin/claude", "auth", "status"], "/tmp", {}, prof.to_json(),
                  prof.prio, prof.mem, f"auth{i}", source="internal")
    check(db.counts(cx).get("queued", 0) == N, f"all {N} storm jobs ENQUEUED (not forked)")

    cfg = sched.Config(mem_floor=200, batch_high=1000, slack=0)
    cfg.max_per_tick = 3
    pool_slots = 8
    running = {}
    max_seen_mem = 0
    max_admitted_in_one_tick = 0
    floor_violations = 0
    ticks = 0

    while True:
        ticks += 1
        for jid in list(running)[: max(1, len(running) // 2)]:
            running.pop(jid, None); db.mark_terminal(cx, jid, "done", 0)
        reserved = sum(p.mem for p in running.values())

        snap = {"mem_available": 100000 - reserved, "batch_current": reserved,
                "disk_free": 100000, "psi_avg10": 0}

        used_llm = sum(sched.llm_demand_slots(p) for p in running.values())
        hr = {"llm_free": pool_slots - used_llm}
        admitted_this_tick = 0
        llm_reserved = 0.0
        while admitted_this_tick < cfg.max_per_tick:
            job = db.next_queued(cx, exclude_filler=True)
            if not job:
                break
            jp = ResourceProfile.from_json(job["profile"])
            reserved = sum(p.mem for p in running.values())
            dec = sched.evaluate(cfg, dict(snap), jp, running=len(running), reserved=reserved,
                                 pressure_blocked=False)
            ld = sched.llm_ok(hr, jp, llm_reserved)
            if not (dec.admit and ld.admit):
                break
            db.mark_running(cx, job["id"], f"u{job['id']}", "/tmp/x")
            running[job["id"]] = jp
            admitted_this_tick += 1
            snap["mem_available"] = 100000 - sum(p.mem for p in running.values())
            snap["batch_current"] = sum(p.mem for p in running.values())
            llm_reserved += sched.llm_demand_slots(jp)
        max_admitted_in_one_tick = max(max_admitted_in_one_tick, admitted_this_tick)
        cur_mem = sum(p.mem for p in running.values())
        max_seen_mem = max(max_seen_mem, cur_mem)
        if cur_mem > cfg.batch_high:
            floor_violations += 1
        if db.counts(cx).get("queued", 0) == 0 and not running:
            break
        if ticks > 8000:
            break

    check(max_admitted_in_one_tick <= cfg.max_per_tick,
          f"REAL admits/tick <= max_per_tick={cfg.max_per_tick} (peak {max_admitted_in_one_tick})")

    check(max_admitted_in_one_tick <= 2 * cfg.max_per_tick,
          f"process-start ceiling <= 2*max_per_tick (main+filler) holds (peak {max_admitted_in_one_tick})")
    check(floor_violations == 0,
          f"floor GENUINELY binds (384*3>1000) + never exceeded (peak {max_seen_mem}M<=1000M)")
    check(max_seen_mem <= cfg.batch_high and max_seen_mem >= 384 * 2 - 1,
          f"floor is TIGHT: box ran up to but not past 2 concurrent (peak {max_seen_mem}M)")
    check(db.counts(cx).get("done", 0) == N, f"all {N} completed via the governed drip")
    check(ticks >= N / cfg.max_per_tick / 2,
          f"storm spread across {ticks} ticks (governed), not one stampede")
    cx.close()

def test_validate_source_trust():
    section("NEW-5 — `source` is a trust discriminator: untrusted clients cannot spoof it")
    import importlib.util, importlib.machinery
    path = os.path.join(ROOT, "tools", "pnd")
    spec = importlib.util.spec_from_loader(
        "pnd_src", importlib.machinery.SourceFileLoader("pnd_src", path))
    pnd = importlib.util.module_from_spec(spec); spec.loader.exec_module(pnd)

    untrusted = {"caps": set()}
    admin = {"caps": {"view:all"}}
    for src in ("filler", "internal", "klaviatur", "cron", "box"):
        eff = pnd._validate_source({"source": src}, untrusted)
        check(eff == "cli", f"untrusted source='{src}' clamped to 'cli' (no preempt/exclusion spoof)")
        eff2 = pnd._validate_source({"source": src}, admin)
        check(eff2 == src, f"admin source='{src}' honoured")

    check(pnd._validate_source({"source": "portal"}, untrusted) == "portal",
          "safe external source 'portal' passes through untrusted")
    check(pnd._validate_source({}, untrusted) == "cli", "no source -> default 'cli'")

def test_step9_klaviatur_routing():
    section("STEP 9 — Klaviatur routes on/scale-N through the GOVERNED queue (submit_fn)")
    from pnlib import governor as G

    cg = G.CgLayout(root=tempfile.mkdtemp())
    submitted = {"calls": []}

    def fake_submit(prog, n):
        ids = list(range(len(submitted["calls"]) * 1000,
                         len(submitted["calls"]) * 1000 + n))
        submitted["calls"].append((prog.name, n))
        return {"ok": True, "queued": ids}

    klav = G.Klaviatur(cg, sacred=["sshd"], submit_fn=fake_submit)
    klav.register(G.Program("web", tier=G.TIER_BATCH))
    klav.register(G.Program("sshd", tier=G.TIER_CRITICAL))

    r = klav.exec_cmd(["on", "web"], peer_uid=os.getuid())
    check(r.get("ok") and r.get("governed"), f"non-sacred `on web` is GOVERNED (queued): {r.get('msg','')[:60]}")
    check(submitted["calls"] and submitted["calls"][-1][0] == "web",
          "on web -> submit_fn enqueued a web instance")

    submitted["calls"].clear()
    r = klav.exec_cmd(["scale", "web", "194"], peer_uid=os.getuid())
    check(r.get("ok") and r.get("governed"), "scale web 194 is GOVERNED (grow via queue, not spawn)")
    check(submitted["calls"] and submitted["calls"][-1] == ("web", G.SCALE_MAX),
          f"scale grow routed through the queue, clamped to SCALE_MAX={G.SCALE_MAX} "
          f"(got {submitted['calls']})")

    r = klav.exec_cmd(["off", "sshd"], peer_uid=os.getuid())
    check(not r.get("ok"), "sacred `off sshd` still REFUSED (sacred-guard unchanged)")

def test_gap4_cpu_budget():
    section("GAP-4 — CPU core budget ACTIVE by default (dimension 2)")
    os.environ.pop("PN_CPU_BUDGET", None)
    os.environ.pop("PN_RESERVED_CORES", None)

    cfg_auto = sched.Config.autoscale()
    check(cfg_auto.cpu_budget != float("inf"),
          f"autoscale cpu_budget is finite by default (={cfg_auto.cpu_budget})")
    check(cfg_auto.cpu_budget == float(max(1, sched.meters.cpu_count() - sched.DEFAULT_RESERVED_CORES)),
          "default budget == max(1, nproc - DEFAULT_RESERVED_CORES) (control-plane reserve)")

    os.environ["PN_CPU_BUDGET"] = "off"
    check(sched.Config.autoscale().cpu_budget == float("inf"), "PN_CPU_BUDGET=off disables the gate")
    os.environ["PN_CPU_BUDGET"] = "inf"
    check(sched.Config.autoscale().cpu_budget == float("inf"), "PN_CPU_BUDGET=inf disables the gate")
    del os.environ["PN_CPU_BUDGET"]
    os.environ["PN_RESERVED_CORES"] = "2"
    check(sched.Config.autoscale().cpu_budget == float(max(1, sched.meters.cpu_count() - 2)),
          "PN_RESERVED_CORES=2 narrows the budget to nproc-2")
    del os.environ["PN_RESERVED_CORES"]

    cfg = sched.Config(mem_floor=200, batch_high=100000, slack=0, cpu_budget=5.0)
    proj = {"mem_available": 100000, "batch_current": 0, "disk_free": 100000, "psi_avg10": 0,
            "interactive_current": 0}
    wide = ResourceProfile(cpu_quota_pct=400)

    d1 = sched.evaluate(cfg, proj, wide, running=0, reserved=0, pressure_blocked=False,
                        cpu_running=0.0, track="batch", track_running=0)
    check(d1.admit, f"first wide batch job admits alone (escape valve): {d1.reason}")

    d2 = sched.evaluate(cfg, proj, wide, running=1, reserved=0, pressure_blocked=False,
                        cpu_running=4.0, track="batch", track_running=1)
    check(not d2.admit and d2.reason.startswith("cpu budget"),
          f"second wide batch job WAITS on CPU: {d2.reason}")

    d3 = sched.evaluate(cfg, proj, wide, running=0, reserved=0, pressure_blocked=False,
                        cpu_running=0.0, track="batch", track_running=0)
    check(d3.admit, "second wide job dispatches when the first ends")

    small = ResourceProfile(cpu_quota_pct=50)
    ds = sched.evaluate(cfg, proj, small, running=1, reserved=0, pressure_blocked=False,
                        cpu_running=4.0, track="batch", track_running=1)
    check(ds.admit, "a small declared job still packs into the remaining core budget")

    chat = ResourceProfile(cpu_quota_pct=None, sandbox="llm", llm_weight=100, latency="realtime")
    di = sched.evaluate(cfg, proj, chat, running=1, reserved=0, pressure_blocked=False,
                        cpu_running=5.0, track="interactive", track_running=0)
    check(di.admit, "interactive job admits despite batch CPU budget being full (latency exempt)")

    dw = sched.evaluate(cfg, proj, wide, running=1, reserved=0, pressure_blocked=False,
                        cpu_running=0.0, track="batch", track_running=0)
    check(dw.admit, "a lone batch job admits while a session cell runs in the interactive tier")

def test_g5_disk_max_profile():
    section("G5 — disk_max scratch-quota profile dimension")
    p = ResourceProfile(disk_max=100)
    check(p.disk_max == 100, "disk_max settable on ResourceProfile")
    back = ResourceProfile.from_json(p.to_json())
    check(back.disk_max == 100, "disk_max round-trips through to_json/from_json")
    check(ResourceProfile().disk_max is None, "disk_max default None (uncapped -> only disk_min_free)")

    est = profile.estimate("compute", template_patch='{"disk_max":"250"}')
    check(est.disk_max == 250, "disk_max coerced from a task-type template (numeric string -> int)")

    _prior = profile.estimate("compute").disk_max
    bad = profile.estimate("compute", template_patch='{"disk_max":"huge"}')
    check(bad.disk_max == _prior,
          "a non-numeric disk_max template value is dropped (F3), not crashed "
          f"-> class prior {_prior} stands")

def main():
    test_step1_lease()
    test_step2_llm_ok()
    test_step2_reserved_llm()
    test_step3_position_agrees()
    test_step4_ewma()
    test_step7_template()
    test_step8_filler_and_preempt()
    test_user_scenario()
    test_llm_ok_double_count_regression()
    test_step9_klaviatur_routing()
    test_step9_meltdown_pure_sanity()
    test_validate_source_trust()
    test_gap4_cpu_budget()
    test_g5_disk_max_profile()
    print(f"\n=== scheduler_test: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
