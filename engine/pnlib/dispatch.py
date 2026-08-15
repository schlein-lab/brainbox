
from __future__ import annotations
import functools
import os
import subprocess

from . import slice as _slice
from . import cgdispatch as _cg

_SYSTEMD = "systemd"
_CGROUP = "cgroup"

def systemd_user_bus_available() -> bool:

    xdg = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    priv = os.path.join(xdg, "systemd", "private")
    if not os.path.exists(priv):
        return False
    try:
        r = subprocess.run(["systemctl", "--user", "is-system-running"],
                           capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False

    err = (r.stderr or "").lower()
    if "failed to connect to bus" in err or "no medium found" in err:
        return False

    out = (r.stdout or "").strip().lower()
    KNOWN = {"running", "degraded", "maintenance", "initializing", "starting", "stopping", "offline"}
    if out in KNOWN:
        return True

    return "failed to connect to bus" not in err

@functools.lru_cache(maxsize=1)
def _selected() -> str:
    override = (os.environ.get("PN_DISPATCH_BACKEND") or "auto").strip().lower()
    if override in (_SYSTEMD, _CGROUP):
        return override
    return _SYSTEMD if systemd_user_bus_available() else _CGROUP

def backend_name(force: bool = False) -> str:
    if force:
        _selected.cache_clear()
    return _selected()

def _backend():
    return _slice if backend_name() == _SYSTEMD else _cg

def bwrap_usable() -> bool:

    return _bwrap_probe()

@functools.lru_cache(maxsize=1)
def _bwrap_probe() -> bool:
    from shutil import which
    if not which("bwrap"):
        return False
    try:
        r = subprocess.run(["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                            "--", "/bin/true"], capture_output=True, text=True, timeout=8)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

def unit_name(job_id: int) -> str:
    return _backend().unit_name(job_id)

def dispatch(job_id, argv, cwd, env, props, out_path, err_path, rc_path, track=None):
    return _backend().dispatch(job_id, argv, cwd, env, props, out_path, err_path, rc_path, track=track)

def read_rc(rc_path):
    return _backend().read_rc(rc_path)

def unit_status(job_id):
    return _backend().unit_status(job_id)

def stop_unit(job_id):
    return _backend().stop_unit(job_id)

def cleanup_unit(job_id):

    b = _backend()
    fn = getattr(b, "cleanup_unit", None)
    return fn(job_id) if fn else True

def sweep_stale_leaves(keep_ids):

    b = _backend()
    fn = getattr(b, "sweep_stale_leaves", None)
    return fn(keep_ids) if fn else 0

def job_cgroup_stats(job_id):
    return _backend().job_cgroup_stats(job_id)

def live_units():
    return _backend().live_units()

def describe() -> dict:
    b = backend_name()
    d = {"backend": b}
    if b == _CGROUP:
        d["sandbox"] = "bwrap+landlock" if bwrap_usable() else "bespoke:landlock+seccomp+prctl"
        d["landlock_abi"] = _cg.cgsandbox.landlock_available()
    else:
        d["sandbox"] = "systemd-run (Exec* properties)"
    return d
