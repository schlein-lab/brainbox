
from __future__ import annotations

DEFAULT_SEED_SVC_S = 60.0

def _nn(v, dflt=0.0) -> float:

    try:
        f = float(v)
    except (TypeError, ValueError):
        f = float(dflt)
    return f if f > 0 else 0.0

def project_eta(position, svc_s, max_concurrent, own_svc_s=None) -> dict:

    try:
        pos = int(position)
    except (TypeError, ValueError):
        pos = 1
    if pos < 1:
        pos = 1
    ahead = pos - 1
    try:
        mc = int(max_concurrent)
    except (TypeError, ValueError):
        mc = 1
    mc = max(1, mc)
    svc = _nn(svc_s)
    own = _nn(own_svc_s) if own_svc_s is not None else svc
    eta_start = (ahead / mc) * svc
    return {
        "position": pos,
        "ahead": ahead,
        "eta_start_s": eta_start,
        "eta_done_s": eta_start + own,
    }

def waiting_on(position, state=None) -> str:

    if state and state != "queued":
        return str(state)
    try:
        pos = int(position)
    except (TypeError, ValueError):
        pos = 1
    return "dispatching" if pos <= 1 else "position"

def human(seconds) -> str:

    if seconds is None:
        return "?"
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return "?"
    if s < 0:
        return "?"
    s = int(round(s))
    if s < 60:
        return f"~{s}s"
    if s < 3600:
        return f"~{(s + 30) // 60}m"
    if s < 86400:
        h, rem = divmod(s, 3600)
        m = (rem + 30) // 60
        if m == 60:
            h += 1; m = 0
        return f"~{h}h" if m == 0 else f"~{h}h{m}m"
    return ">1d"
