#!/usr/bin/env python3

from __future__ import annotations
import os, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from pnlib import sched, db
from pnlib.profile import ResourceProfile, CLASSES, estimate

PASS = FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {label}")
    else:
        FAIL += 1; print(f"  FAIL {label}")

def section(name):
    print(f"\n== {name} ==")

section("profile: walltime + OOM fields round-trip, coercion, class defaults")

p = ResourceProfile(mem=100, timeout_s=200, max_extend_s=300,
                    oom_grow=True, oom_grow_mult=2.0, max_oom_retries=4)
q = ResourceProfile.from_json(p.to_json())
check(q.max_extend_s == 300, "max_extend_s survives to_json/from_json")
check(q.oom_grow is True and q.oom_grow_mult == 2.0 and q.max_oom_retries == 4,
      "oom_grow / mult / retries survive round-trip")

legacy = ResourceProfile.from_json('{"mem":128,"timeout_s":600}')
check(legacy.max_extend_s == 0 and legacy.oom_grow is False and legacy.max_oom_retries == 2,
      "legacy profile JSON gets safe defaults (no extension, no oom_grow)")

check(CLASSES["worker"].oom_grow is False and CLASSES["worker"].max_extend_s == 3600,
      "worker class: kein OOM-Wachstum (eigene cgroup ist gross genug), aber 3600 s Selbstverlaengerung")
check(CLASSES["filler"].oom_grow is False and CLASSES["filler"].max_extend_s == 1800,
      "filler class: kein OOM-Wachstum, 1800 s Selbstverlaengerung")
check(CLASSES["compute"].oom_grow is False and CLASSES["compute"].max_extend_s == 0,
      "compute class stays admin-only + no oom_grow (safe default)")

est = estimate("compute", template_patch={"oom_grow": True, "max_extend_s": "900",
                                          "oom_grow_mult": "1.75", "max_oom_retries": 3})
check(est.oom_grow is True and est.max_extend_s == 900 and est.oom_grow_mult == 1.75
      and est.max_oom_retries == 3, "estimate() coerces a valid oom/extend template patch")
bad = estimate("compute", template_patch={"max_extend_s": "not-a-number", "oom_grow": "yes"})
check(bad.max_extend_s == 0 and bad.oom_grow is False,
      "estimate() DROPS wrong-typed oom/extend patch values (never crashes the tick)")

props = ResourceProfile().systemd_properties()
check(any(x == "TimeoutStopSec=10s" for x in props),
      "systemd_properties emits TimeoutStopSec=10s (bounded SIGCONT->SIGTERM->SIGKILL ladder)")

section("sched.walltime_phase — the 'Anzählen' decision")

pr = ResourceProfile(timeout_s=100)
check(sched.walltime_phase(pr, 50) is None, "well inside the wall -> None")
check(sched.walltime_phase(pr, 80) == "warn", "at 0.8*limit -> warn")
check(sched.walltime_phase(pr, 80, warned=True) is None, "already warned -> no repeat warn")
check(sched.walltime_phase(pr, 101) == "kill", "past the wall -> kill")
check(sched.walltime_phase(pr, 101, warned=True) == "kill", "kill dominates even if warned")

check(sched.walltime_phase(pr, 101, extra_s=50) is None, "extension: 101s now inside 150s wall")
check(sched.walltime_phase(pr, 120, extra_s=50) == "warn", "extension: warn re-arms near NEW wall")
check(sched.walltime_phase(pr, 151, extra_s=50) == "kill", "extension: kill at the NEW wall")

section("sched.oom_grow_target — the H6 decision")

g = ResourceProfile(mem=100, oom_grow=True, oom_grow_mult=1.5, max_oom_retries=2)
check(sched.oom_grow_target(g, 0, 137, None) == 228, "137 + UNREADABLE counter -> grow 100->228 (fallback)")
check(sched.oom_grow_target(g, 0, 0, 1) == 228, "cgroup oom_kill>0 grows even on exit 0")
check(sched.oom_grow_target(g, 0, 137, 0) is None, "137 but readable oom_kill=0 -> NOT OOM -> no grow (review fix)")
check(sched.oom_grow_target(g, 0, 1, None) is None, "nonzero-but-not-137, unknown counter -> no grow")
big = ResourceProfile(mem=900, oom_grow=True, oom_grow_mult=1.5, max_oom_retries=2)
check(sched.oom_grow_target(big, 0, 0, 1) == 1350, "large job: 1.5x mult dominates (900->1350)")
check(sched.oom_grow_target(g, 2, 0, 1) is None, "retries exhausted -> no grow")
ng = ResourceProfile(mem=100, oom_grow=False)
check(sched.oom_grow_target(ng, 0, 0, 1) is None, "class opts out (oom_grow=False) -> no grow")
capped = ResourceProfile(mem=100, mem_max=120, oom_grow=True, oom_grow_mult=1.5)
check(sched.oom_grow_target(capped, 0, 0, 1) == 120, "grown mem clamped to mem_max")
atcap = ResourceProfile(mem=100, mem_max=100, oom_grow=True, oom_grow_mult=1.5)
check(sched.oom_grow_target(atcap, 0, 0, 1) is None, "already at mem_max -> None (let it fail)")
floor = ResourceProfile(mem=10, oom_grow=True, oom_grow_mult=1.5)
check(sched.oom_grow_target(floor, 0, 0, 1) == 138, "tiny job grows by at least +128 MiB")

section("db: v11 migration + grant_extension / mark_soft_warned / requeue_oom_grown")

tmp = tempfile.mkdtemp()
cx = db.connect(os.path.join(tmp, "queue.db"))
cols = {r["name"] for r in cx.execute("PRAGMA table_info(jobs)")}
check({"walltime_extra_s", "soft_warned", "oom_retries"} <= cols,
      "v11 columns present after connect()/migrate")

prof = ResourceProfile(mem=200, timeout_s=100, max_extend_s=300, oom_grow=True)
jid = db.submit(cx, ["/bin/true"], "/tmp", {}, prof.to_json(), 100, 200, "T", source="cli")

check(db.grant_extension(cx, jid, 60) is None, "grant_extension on a queued job -> None")

db.mark_running(cx, jid, "pn-job-%d.service" % jid, "/dev/null")
db.mark_soft_warned(cx, jid)
check(db.get(cx, jid, scope_all=True)["soft_warned"] == 1, "mark_soft_warned latches soft_warned=1")

total = db.grant_extension(cx, jid, 60)
row = db.get(cx, jid, scope_all=True)
check(total == 60 and abs((row["walltime_extra_s"] or 0) - 60) < 1e-6,
      "grant_extension accumulates walltime_extra_s")
check(row["soft_warned"] == 0, "grant_extension RE-ARMS the soft-warn (soft_warned back to 0)")
check(db.grant_extension(cx, jid, 40) == 100, "second grant accumulates (60+40=100)")

grown = ResourceProfile.from_json(row["profile"]); grown.mem = 400
db.requeue_oom_grown(cx, jid, 400, grown.to_json())
r2 = db.get(cx, jid, scope_all=True)
check(r2["state"] == "queued", "requeue_oom_grown -> back to queued")
check(r2["mem_estimate"] == 400, "requeue_oom_grown bumps mem_estimate (admission accounting)")
check(ResourceProfile.from_json(r2["profile"]).mem == 400, "requeue_oom_grown rewrites profile.mem")
check(r2["oom_retries"] == 1, "requeue_oom_grown increments oom_retries")
check(r2["started_at"] is None and (r2["walltime_extra_s"] or 0) == 0 and r2["soft_warned"] == 0,
      "requeue_oom_grown resets run-state (started_at/extra/soft_warned)")

db.mark_running(cx, jid, "pn-job-%d.service" % jid, "/dev/null")
db.grant_extension(cx, jid, 120)
db.mark_soft_warned(cx, jid)
db.requeue(cx, jid)
r3 = db.get(cx, jid, scope_all=True)
check(r3["state"] == "queued" and (r3["walltime_extra_s"] or 0) == 0 and r3["soft_warned"] == 0,
      "requeue() resets walltime_extra_s + soft_warned (fresh run)")
check(r3["oom_retries"] == 1, "requeue() KEEPS oom_retries (cross-run OOM budget survives a crash)")

section("SECURITY: scheduling-trust overrides gated to admin (walltime-evasion fix — review HIGH)")

import importlib.util, importlib.machinery
_pnd_path = os.path.join(ROOT, "tools", "pnd")
_spec = importlib.util.spec_from_loader("pnd_ovr", importlib.machinery.SourceFileLoader("pnd_ovr", _pnd_path))
pnd = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(pnd)

hostile = {"timeout": 10**9, "prio": 0, "mem": 1, "mem_max": 99999, "cpu_quota": 800,
           "disk_min": 0, "idempotent": True}
untrusted = {"caps": {"task_type:foo"}}
check(pnd._scheduling_overrides(hostile, untrusted) == {},
      "untrusted tenant: ALL scheduling overrides dropped")
check(pnd._scheduling_overrides(hostile, {"caps": {"view:all"}}).get("timeout_s") == 10**9,
      "admin (view:all): overrides honoured")
check(pnd._scheduling_overrides(hostile, {"caps": {"task_type:*"}}).get("prio") == 0,
      "fleet (task_type:*): overrides honoured")
check(pnd._scheduling_overrides(hostile, {"caps": set()}) == {},
      "no caps: overrides dropped")

ep = estimate("worker", **pnd._scheduling_overrides({"timeout": 10**9}, untrusted))
check(ep.timeout_s == 3600, "untrusted --timeout 1e9 -> profile keeps class wall 3600s (NOT evaded)")
ea = estimate("worker", **pnd._scheduling_overrides({"timeout": 60}, {"caps": {"view:all"}}))
check(ea.timeout_s == 60, "admin --timeout 60 -> honoured")

print(f"\n=== walltime_oom_test: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
