#!/usr/bin/env python3

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pnlib.healer import probes, loop as healloop, notify, watchdog, observability
from pnlib.healer.probes import OK, WARN, CRITICAL, Reading

_p = 0
_f = 0

def check(cond, msg):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {msg}")
    else:
        _f += 1
        print(f"  FAIL  {msg}")

class ScriptedProbe(probes.Probe):

    def __init__(self, signal, seq):
        self.signal = signal
        self._seq = list(seq)
        self._i = 0

    def read(self, now=None):
        sev = self._seq[min(self._i, len(self._seq) - 1)]
        self._i += 1
        return Reading(self.signal, sev, 0 if sev == OK else 99,
                       f"{self.signal} = {sev}", unit="%", ts=now)

print("--- disk-full -> warn-admin (with klartext) ---")
state = {"free_gib": 7, "total_gib": 100}
df = lambda path: (state["free_gib"] * 1024 ** 3, state["total_gib"] * 1024 ** 3)
sink = notify.MockSink()
notifier = notify.HealerNotifier(sink, cooldown_s=1800, clock=lambda: 1000.0)

rem_calls = []
def failing_gc(reading):
    rem_calls.append(reading.signal)
    return False

disk_probe = probes.DiskFreeProbe("/", df_source=df, warn_pct=10, crit_pct=5)
lp = healloop.HealLoop([disk_probe], notifier=notifier,
                       remediations={"disk:/": failing_gc})

decs = []
for t in range(4):
    _, d = lp.step(now=t)
    decs.append(d[0])

levels = [d.level_name for d in decs]
check(levels[0] == "notice", f"step1 blip-absorb -> notice ({levels[0]})")
check("auto-remediate" in levels, f"reaches auto-remediate rung ({levels})")
check(decs[-1].level_name in ("warn-admin", "safe-degrade"),
      f"sustained disk-full escalates to warn-admin ({levels[-1]})")
check(len(rem_calls) >= 1, "auto-remediation was attempted before paging")
check(sink.count("disk-full", "warn") >= 1, "notify sink received a disk-full warn")
warn_msg = next(m for m in sink.sent if m["condition"] == "disk-full")
check("Klartext" in warn_msg["body"] and "Speicher" in warn_msg["body"],
      "warn body is human-readable KLARTEXT (Speicher/Klartext present)")
check(warn_msg["severity"] == WARN and "disk:/" in warn_msg["subject"],
      "warn message carries severity + signal")

state["free_gib"] = 60
for t in range(4, 7):
    lp.step(now=t)
check(sink.count("disk-full", "resolved") >= 1,
      "recovery emits an edge-triggered 'resolved' notice")

print("--- dying-disk -> warned ---")
smart = {"reallocated_sector_ct": 0, "current_pending_sector": 8, "offline_uncorrectable": 2}
dmesg = ["[123.4] EXT4-fs error (device sda1): ext4_find_entry: reading directory lblock"]
dd_probe = probes.DyingDiskProbe("sda", smart_fn=lambda: smart, dmesg_fn=lambda: dmesg)
r0 = dd_probe.read(now=0)
check(r0.severity == CRITICAL, f"dying-disk probe reads CRITICAL ({r0.severity})")

sink2 = notify.MockSink()
notifier2 = notify.HealerNotifier(sink2, cooldown_s=1800, clock=lambda: 2000.0)
lp2 = healloop.HealLoop([dd_probe], notifier=notifier2)
for t in range(3):
    lp2.step(now=t)
check(sink2.count("dying-disk", "warn") >= 1, "dying-disk paged the admin")
dmsg = next(m for m in sink2.sent if m["condition"] == "dying-disk")
check("STIRBT" in dmsg["klartext"] or "STIRBT" in dmsg["body"],
      "dying-disk warning is klartext ('STIRBT')")

print("--- transient blip -> NO warn (hysteresis) ---")
sink3 = notify.MockSink()
notifier3 = notify.HealerNotifier(sink3, clock=lambda: 3000.0)
blip = ScriptedProbe("disk:/blip", [WARN, OK, OK, OK])
lp3 = healloop.HealLoop([blip], notifier=notifier3)
blip_levels = []
for t in range(4):
    _, d = lp3.step(now=t)
    blip_levels.append(d[0].level_name)
check(blip_levels[0] == "notice", f"blip first seen as notice only ({blip_levels[0]})")
check(all(l != "warn-admin" for l in blip_levels), f"blip never reaches warn-admin ({blip_levels})")
check(sink3.count() == 0, f"a transient blip sends NOTHING to the admin ({sink3.sent})")

print("--- auto-remediation success -> no admin warn ---")
st4 = {"free_gib": 7, "total_gib": 100}
df4 = lambda path: (st4["free_gib"] * 1024 ** 3, st4["total_gib"] * 1024 ** 3)
def good_gc(reading):
    st4["free_gib"] = 55
    return True
sink4 = notify.MockSink()
notifier4 = notify.HealerNotifier(sink4, clock=lambda: 4000.0)
dp4 = probes.DiskFreeProbe("/", df_source=df4, warn_pct=10, crit_pct=5)
lp4 = healloop.HealLoop([dp4], notifier=notifier4, remediations={"disk:/": good_gc})
for t in range(5):
    lp4.step(now=t)
check(("disk:/", True) in lp4.remediation_log, "successful remediation recorded")
check(sink4.count("disk-full", "warn") == 0,
      f"a self-healed disk does NOT page the admin ({sink4.sent})")

print("--- two-tier watchdog schedule ---")
deep_hits = []
fast_hits = []
wd = watchdog.Watchdog(fast_interval_s=1.0, deep_interval_s=5.0, clock=lambda: 0.0)
for now in range(0, 7):
    wd.pet(now=now)
    fired = wd.tick(now=now,
                    fast_fn=lambda n: fast_hits.append(n),
                    deep_fn=lambda n: deep_hits.append(n) or "swept")
check(wd.fast_ticks == 7, f"fast tier fires every tick (7) -> got {wd.fast_ticks}")
check(wd.deep_ticks == 2, f"deep sweep fires on its slow schedule (t=0,5 -> 2) got {wd.deep_ticks}")
check(deep_hits == [0, 5], f"deep sweep ran at t=0 and t=5 ({deep_hits})")

stale = []
wd2 = watchdog.Watchdog(fast_interval_s=1.0, deep_interval_s=100.0,
                        liveness_timeout_s=3.0, clock=lambda: 0.0,
                        on_stale=lambda now, age: stale.append(age))
wd2.pet(now=0)
wd2.tick(now=1)
wd2.tick(now=10)
check(wd2.liveness_ok(now=1) is False or wd2.stale_events == 1,
      f"stale pet trips the liveness alarm (stale_events={wd2.stale_events})")
check(len(stale) == 1, f"on_stale callback fired once ({stale})")

print("--- klartext status renders ---")
readings = [
    Reading("disk:/", WARN, 7.0, "Speicher /: 7.0% frei (7.0 GiB) — unter dem 10%-Limit", unit="%"),
    Reading("disk-health:sda", CRITICAL, 1, "Disk sda STIRBT: 8 pending sectors", unit=" ext4-errs"),
    Reading("service:pnd", OK, True, "Dienst pnd: läuft"),
    Reading("memory", OK, 40.0, "RAM: 40.0% belegt (9000 MiB frei)", unit="%"),
]
_, live_decs = lp.step(now=99)
status = observability.render_status(readings, now=1751500000.0)
check(isinstance(status, str) and status.count("\n") >= 6, "status is a multi-line block")
check("[WARN]" in status and "[CRIT]" in status and "[ OK ]" in status,
      "status shows per-signal severity markers")
check("KRITISCH" in status, "overall verdict reflects worst signal (KRITISCH)")
check(status.index("disk-health:sda") < status.index("service:pnd"),
      "worst signal sorts to the top")
check("SYSTEM-GESUNDHEIT" in status, "status has a klartext title")
print()
print(status)
print()

print(f"=== {_p} passed, {_f} failed ===")
sys.exit(0 if _f == 0 else 1)
