#!/usr/bin/env python3

import os
import sys
import tempfile
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pnlib import energy

def _write(path: str, contents: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(contents)

def _make_sysfs(*, thermal_milli=None, battery_status=None, battery_cap=None,
                mains_online=None, governor=None) -> str:

    d = tempfile.mkdtemp(prefix="energy_test_sys_")
    if thermal_milli is not None:
        _write(os.path.join(d, "class/thermal/thermal_zone0/temp"), str(thermal_milli))
    if battery_status is not None or battery_cap is not None:
        base = os.path.join(d, "class/power_supply/BAT0")
        _write(os.path.join(base, "type"), "Battery")
        if battery_status is not None:
            _write(os.path.join(base, "status"), battery_status)
        if battery_cap is not None:
            _write(os.path.join(base, "capacity"), str(battery_cap))
    if mains_online is not None:
        base = os.path.join(d, "class/power_supply/AC0")
        _write(os.path.join(base, "type"), "Mains")
        _write(os.path.join(base, "online"), str(mains_online))
    if governor is not None:
        base = os.path.join(d, "devices/system/cpu/cpu0/cpufreq")
        _write(os.path.join(base, "scaling_governor"), governor)
        _write(os.path.join(base, "scaling_cur_freq"), "1200000")
        _write(os.path.join(base, "cpuinfo_max_freq"), "2400000")
    return d

def _jobs():

    return [
        {"id": "cheap", "base": 10.0, "cost": 1.0},
        {"id": "heavy", "base": 11.0, "cost": 50.0},
        {"id": "mid", "base": 5.0, "cost": 10.0},
    ]

def _order(jobs):
    return [j["id"] for j in jobs]

def test_detect_returns_none_on_this_vm():

    r = energy.detect()
    assert r is None or not r, f"expected None on VM, got {r!r}"

def test_detect_empty_tree_is_none():

    d = tempfile.mkdtemp(prefix="energy_test_empty_")
    try:
        assert energy.detect(root=d) is None
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_detect_populated_tree():

    d = _make_sysfs(thermal_milli=42000, battery_status="Discharging", battery_cap=40,
                    mains_online=0, governor="powersave")
    try:
        r = energy.detect(root=d)
        assert r is not None
        assert r["thermal_max_c"] == 42.0, r
        assert r["on_battery"] is True, r
        assert r["battery_pct"] == 40, r
        assert r["ac_online"] is False, r
        assert r["governor"] == "powersave", r
        assert set(r["sources"]) == {"thermal", "power", "cpufreq"}, r
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_bias_none_is_zero():

    for j in _jobs():
        assert energy.energy_bias(None, j) == 0.0
        assert energy.energy_bias({}, j) == 0.0

def test_inert_present_none_equals_absent():

    jobs = _jobs()
    absent = energy.rank_jobs(jobs)
    present_none = energy.rank_jobs(jobs, reading=None)
    detected = energy.rank_jobs(jobs, reading=energy.detect())
    assert _order(absent) == _order(present_none), (_order(absent), _order(present_none))
    assert _order(absent) == _order(detected), (_order(absent), _order(detected))

    assert _order(absent) == ["heavy", "cheap", "mid"], _order(absent)

def test_dimension_is_nondegenerate_when_active():

    jobs = _jobs()
    constrained = {"on_battery": True, "battery_pct": 10, "thermal_max_c": 84.0}
    ranked = energy.rank_jobs(jobs, reading=constrained)
    assert _order(ranked)[0] == "cheap", _order(ranked)
    assert _order(ranked).index("heavy") > _order(ranked).index("cheap"), _order(ranked)

    assert _order(ranked) != _order(energy.rank_jobs(jobs)), _order(ranked)

def test_detect_never_raises_on_bad_root():

    assert energy.detect(root="/nonexistent/definitely/not/here") is None
    assert energy.detect(root="/dev/null") is None

def _run_standalone() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"  FAIL  {t.__name__}: {e!r}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(_run_standalone())
