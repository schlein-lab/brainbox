
from __future__ import annotations
import os, subprocess, functools, time, shutil, sys
from . import DATA_DIR

CG_ROOT = "/sys/fs/cgroup"

def _read_int(path):
    try:
        with open(path) as f:
            v = f.read().strip()
        return None if v == "max" else int(v)
    except Exception:
        return None

def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return ""

@functools.lru_cache(maxsize=8)
def _batch_tier_dir() -> str | None:

    try:
        from . import cgdispatch
        return cgdispatch.batch_tier_dir()
    except Exception:
        return None

_CG_WARNED = set()

def cgroup_path(user_unit: str) -> str | None:

    if not user_unit:
        return None
    if user_unit.startswith("/") and os.path.isdir(user_unit):
        return user_unit
    if user_unit in ("pn-batch.slice", "batch", "pn.slice/batch"):
        d = _batch_tier_dir()
        if d and os.path.isdir(d):
            return d
    direct = os.path.join(CG_ROOT, user_unit.lstrip("/"))
    if os.path.isdir(direct):
        return direct
    if shutil.which("systemctl"):
        try:
            out = subprocess.run(
                ["systemctl", "--user", "show", user_unit, "-p", "ControlGroup"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            cg = out.split("=", 1)[1] if "=" in out else ""
            if cg:
                return CG_ROOT + cg
        except Exception:
            pass
    if user_unit not in _CG_WARNED:
        _CG_WARNED.add(user_unit)
        print("[meters] WARN cgroup '%s' nicht aufloesbar - per-cgroup Messung fehlt" % user_unit,
              file=sys.stderr, flush=True)
    return None

def _psi(cgroup_rel_unit, field, allow_global):

    path = None
    if cgroup_rel_unit:
        cg = cgroup_path(cgroup_rel_unit)
        if cg:
            path = os.path.join(cg, "memory.pressure")
        elif not allow_global:
            return None
    if path is None:
        path = "/proc/pressure/memory"
    for line in _read(path).splitlines():
        if line.startswith(field):
            for tok in line.split():
                if tok.startswith("avg10="):
                    try:
                        return float(tok.split("=", 1)[1])
                    except Exception:
                        return 0.0
    return 0.0

PROTECTED_TIERS = ("pn.slice/critical", "pn-misc.slice", "pn.slice/interactive")

def psi_protected_full_avg10():

    vals = [v for v in (_psi(t, "full", False) for t in PROTECTED_TIERS) if v is not None]
    return max(vals) if vals else None

def psi_full_avg10(cgroup_rel_unit: str | None = None, allow_global: bool = True):

    return _psi(cgroup_rel_unit, "full", allow_global)

def meminfo():

    d = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, rest = line.partition(":")
        try:
            d[k] = int(rest.split()[0]) // 1024
        except Exception:
            pass
    return {
        "total": d.get("MemTotal", 0),
        "available": d.get("MemAvailable", 0),
        "swap_total": d.get("SwapTotal", 0),
        "swap_free": d.get("SwapFree", 0),
    }

def psi_some_avg10(cgroup_rel_unit: str | None = None, allow_global: bool = True):

    return _psi(cgroup_rel_unit, "some", allow_global)

def interactive_memory_current_mib() -> int:

    try:
        from . import cgdispatch
        d = cgdispatch.interactive_tier_dir()
    except Exception:
        d = None
    if not d:
        return 0
    v = _read_int(os.path.join(d, "memory.current"))
    if v is None:
        return 0
    sess = _read_int(os.path.join(d, "sessions", "memory.current")) or 0
    return max(0, v - sess) // (1024 * 1024)

def batch_memory_current_mib(slice_unit="pn-batch.slice") -> int:
    cg = cgroup_path(slice_unit)
    if not cg:
        return 0
    v = _read_int(os.path.join(cg, "memory.current"))
    return (v // (1024 * 1024)) if v is not None else 0

def batch_memory_peak_mib(slice_unit="pn-batch.slice") -> int | None:
    cg = cgroup_path(slice_unit)
    if not cg:
        return None
    v = _read_int(os.path.join(cg, "memory.peak"))
    return (v // (1024 * 1024)) if v is not None else None

def cpu_count() -> int:
    return os.cpu_count() or 1

def loadavg() -> tuple[float, float, float]:
    try:
        return os.getloadavg()
    except Exception:
        return (0.0, 0.0, 0.0)

def disk_free_mib(path="/") -> int:

    p = path
    try:
        while p and not os.path.exists(p):
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
        st = os.statvfs(p or "/")
        return (st.f_bavail * st.f_frsize) // (1024 * 1024)
    except Exception:
        return 1 << 30

def disk_free_pct(path="/") -> float:

    p = path
    try:
        while p and not os.path.exists(p):
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
        st = os.statvfs(p or "/")
        total = st.f_blocks * st.f_frsize
        if total <= 0:
            return 100.0
        return 100.0 * (st.f_bavail * st.f_frsize) / total
    except Exception:
        return 100.0

def snapshot(slice_unit="pn-batch.slice", data_dir: str | None = None) -> dict:

    mi = meminfo()
    dd = data_dir or DATA_DIR
    data_free = disk_free_mib(dd)
    return {
        "ts": time.time(),
        "mem_total": mi["total"],
        "mem_available": mi["available"],
        "swap_total": mi["swap_total"],
        "swap_used": mi["swap_total"] - mi["swap_free"],
        "batch_current": batch_memory_current_mib(slice_unit),

        "psi_avg10": psi_some_avg10(slice_unit, allow_global=False),
        "psi_batch_full_avg10": psi_full_avg10(slice_unit, allow_global=False),
        "psi_global_avg10": psi_some_avg10(None),

        "psi_root_full_avg10": psi_full_avg10(None),

        "psi_ctl_full_avg10": psi_protected_full_avg10(),
        "cg_batch_dir": cgroup_path(slice_unit),

        "interactive_current": interactive_memory_current_mib(),
        "cpu_count": cpu_count(),
        "load1": loadavg()[0],

        "disk_free": data_free,
        "data_dir": dd,
        "data_free": data_free,
        "data_free_pct": disk_free_pct(dd),
        "root_free": disk_free_mib("/"),
    }

if __name__ == "__main__":
    import json
    print(json.dumps(snapshot(), indent=2))
