
from __future__ import annotations
import os
import random

TRANSIENT, REFUSED, FATAL = "transient", "refused", "fatal"

_TRANSIENT_CODES = frozenset({124, 125, 137, 143, -15, -9})

_TRANSIENT_MARKERS = ("connection refused", "connection reset", "timed out", "timeout",
                      "node draining", "http 503", "http/1.1 503", "status 503",
                      " 503 ", "temporarily unavailable", "broken pipe")

_REFUSED_MARKERS = ("http 451", "http/1.1 451", "status 451", "error 451", " 451 ",
                    "refusal", "refused")

def _envf(name, dflt):
    try:
        return float(os.environ.get(name, "") or dflt)
    except (TypeError, ValueError):
        return dflt

def refused_markers():
    raw = os.environ.get("PN_REFUSED_MARKERS", "").strip()
    if raw:
        return tuple(m.strip().lower() for m in raw.split(",") if m.strip())
    return _REFUSED_MARKERS

def classify(code, terminal_hint=None, err_tail=None, oom=False):

    if code == 0 and terminal_hint != "timeout":
        return None
    if terminal_hint == "timeout" or oom:
        return TRANSIENT
    try:
        c = int(code)
    except (TypeError, ValueError):
        c = None
    if c is not None and (c in _TRANSIENT_CODES or c < 0):
        return TRANSIENT
    text = (err_tail or "").lower()
    if text:
        if any(m in text for m in _TRANSIENT_MARKERS):
            return TRANSIENT
        if any(m in text for m in refused_markers()):
            return REFUSED
    return FATAL

def backoff_s(attempts, base=None, cap=None, jitter=None, rnd=None):

    b = _envf("PN_BACKOFF_BASE_S", 30.0) if base is None else float(base)
    if b <= 0:
        return 0.0
    c = _envf("PN_BACKOFF_CAP_S", 1800.0) if cap is None else float(cap)
    j = _envf("PN_BACKOFF_JITTER", 0.2) if jitter is None else float(jitter)
    j = max(0.0, min(j, 0.9))
    try:
        n = max(0, int(attempts))
    except (TypeError, ValueError):
        n = 0
    raw = min(b * (2.0 ** min(n, 30)), max(b, c))
    r = (rnd or random).uniform(1.0 - j, 1.0 + j)
    return raw * r
