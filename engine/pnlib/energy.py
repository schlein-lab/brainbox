
from __future__ import annotations

import glob
import os
from typing import Any, Dict, List, Optional

__all__ = ["detect", "energy_bias", "rank_jobs", "ABSENT"]

_THERMAL_TRIP_C = 85.0
_THERMAL_HEADROOM_C = 20.0

_BATTERY_PENALTY = 1.0

_THERMAL_PENALTY = 1.0

def _read_text(path: str) -> Optional[str]:

    try:
        with open(path, "r") as f:
            return f.read().strip()
    except (OSError, ValueError):
        return None

def _read_int(path: str) -> Optional[int]:
    s = _read_text(path)
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        return None

def _detect_thermal(root: str) -> Dict[str, Any]:
    zones: List[float] = []
    for tp in sorted(glob.glob(os.path.join(root, "class", "thermal", "thermal_zone*", "temp"))):
        milli = _read_int(tp)
        if milli is None:
            continue
        zones.append(milli / 1000.0)
    if not zones:
        return {}
    return {"thermal_c": zones, "thermal_max_c": max(zones)}

def _detect_power(root: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    on_battery: Optional[bool] = None
    battery_pct: Optional[int] = None
    ac_online: Optional[bool] = None
    for psdir in sorted(glob.glob(os.path.join(root, "class", "power_supply", "*"))):
        kind = (_read_text(os.path.join(psdir, "type")) or "").lower()
        if kind == "battery":
            status = (_read_text(os.path.join(psdir, "status")) or "").lower()
            cap = _read_int(os.path.join(psdir, "capacity"))
            if cap is not None:
                battery_pct = cap if battery_pct is None else min(battery_pct, cap)

            if status == "discharging":
                on_battery = True
            elif status in ("charging", "full") and on_battery is None:
                on_battery = False
        elif kind in ("mains", "usb", "usb_pd", "ac"):
            online = _read_int(os.path.join(psdir, "online"))
            if online is not None:
                ac_online = bool(online)
    if battery_pct is None and ac_online is None and on_battery is None:
        return {}

    if on_battery is None and ac_online is not None and battery_pct is not None:
        on_battery = not ac_online
    if battery_pct is not None:
        out["battery_pct"] = battery_pct
    if on_battery is not None:
        out["on_battery"] = on_battery
    if ac_online is not None:
        out["ac_online"] = ac_online
    return out

def _detect_cpufreq(root: str) -> Dict[str, Any]:
    base = os.path.join(root, "devices", "system", "cpu", "cpu0", "cpufreq")
    gov = _read_text(os.path.join(base, "scaling_governor"))
    cur = _read_int(os.path.join(base, "scaling_cur_freq"))
    mx = _read_int(os.path.join(base, "cpuinfo_max_freq"))
    out: Dict[str, Any] = {}
    if gov is not None:
        out["governor"] = gov
    if cur is not None and mx:
        out["cpu_freq_frac"] = max(0.0, min(1.0, cur / mx))
    return out

def detect(root: str = "/sys") -> Optional[Dict[str, Any]]:

    reading: Dict[str, Any] = {}
    sources: List[str] = []
    for name, sub in (
        ("thermal", _detect_thermal(root)),
        ("power", _detect_power(root)),
        ("cpufreq", _detect_cpufreq(root)),
    ):
        if sub:
            reading.update(sub)
            sources.append(name)
    if not reading:
        return None
    reading["sources"] = sources
    return reading

def energy_bias(reading: Optional[Dict[str, Any]], job: Any) -> float:

    if not reading:
        return 0.0
    cost = _job_cost(job)
    bias = 0.0
    if reading.get("on_battery"):
        pct = reading.get("battery_pct")

        dod = 1.0 - (pct / 100.0) if isinstance(pct, (int, float)) else 0.5
        bias -= _BATTERY_PENALTY * cost * max(0.0, min(1.0, dod))
    tmax = reading.get("thermal_max_c")
    if isinstance(tmax, (int, float)):
        headroom = _THERMAL_TRIP_C - tmax
        if headroom < _THERMAL_HEADROOM_C:
            pressure = (_THERMAL_HEADROOM_C - headroom) / _THERMAL_HEADROOM_C
            bias -= _THERMAL_PENALTY * cost * max(0.0, min(1.0, pressure))
    return bias

def _job_cost(job: Any) -> float:

    val: Any = 1.0
    if isinstance(job, dict):
        for k in ("cost", "weight", "cpu", "cpu_width"):
            if k in job and job[k] is not None:
                val = job[k]
                break
    else:
        for k in ("cost", "weight", "cpu", "cpu_width"):
            if getattr(job, k, None) is not None:
                val = getattr(job, k)
                break
    try:
        c = float(val)
    except (TypeError, ValueError):
        return 1.0
    return c if c >= 0.0 else 0.0

ABSENT = object()

def rank_jobs(jobs: List[Dict[str, Any]], reading: Any = ABSENT) -> List[Dict[str, Any]]:

    def score(job: Dict[str, Any]) -> float:
        base = float(job.get("base", 0.0))
        if reading is ABSENT:
            return base
        return base + energy_bias(reading, job)

    indexed = list(enumerate(jobs))
    indexed.sort(key=lambda p: (-score(p[1]), p[1].get("id", p[0])))
    return [j for _, j in indexed]
