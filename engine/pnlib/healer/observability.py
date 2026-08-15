
from __future__ import annotations

import time

from .probes import OK, NOTICE, WARN, CRITICAL, sev_rank
from .loop import LEVEL_NAME, LVL_OK

_MARK = {OK: "[ OK ]", NOTICE: "[NOTE]", WARN: "[WARN]", CRITICAL: "[CRIT]"}

def _fmt_ts(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:
        return str(ts)

def render_status(readings, decisions=None, now=None, title="BRAINARBEIT — SYSTEM-GESUNDHEIT"):

    now = time.time() if now is None else now
    dec_by_sig = {d.signal: d for d in (decisions or [])}

    ordered = sorted(readings, key=lambda r: (-sev_rank(r.severity), r.signal))

    n_warn = sum(1 for r in readings if r.severity == WARN)
    n_crit = sum(1 for r in readings if r.severity == CRITICAL)
    n_note = sum(1 for r in readings if r.severity == NOTICE)
    if n_crit:
        overall = "KRITISCH"
    elif n_warn:
        overall = "WARNUNG"
    elif n_note:
        overall = "HINWEIS"
    else:
        overall = "GESUND"

    width = 72
    sig_w = max((len(r.signal) for r in readings), default=6)
    sig_w = min(max(sig_w, 6), 22)

    lines = []
    lines.append("=" * width)
    lines.append(f" {title}")
    lines.append(f" Stand: {_fmt_ts(now)}   Gesamt: {overall}"
                 f"   ({len(readings)} Sonden: {n_crit} krit / {n_warn} warn / {n_note} hinw)")
    lines.append("=" * width)

    for r in ordered:
        mark = _MARK.get(r.severity, "[????]")
        val = "" if r.value is None else f"{r.value}{r.unit}"
        line = f" {mark} {r.signal.ljust(sig_w)}  {val}"
        dec = dec_by_sig.get(r.signal)
        if dec is not None and dec.level != LVL_OK:
            line += f"  -> {dec.level_name}"
        lines.append(line)
        lines.append(f"        {r.klartext}")

    if not readings:
        lines.append(" (keine Sonden konfiguriert)")

    lines.append("=" * width)
    if overall == "GESUND":
        lines.append(" Alles im grünen Bereich.")
    else:
        worst = ordered[0]
        lines.append(f" Handlungsbedarf: {worst.signal} — {worst.klartext}")
    lines.append("=" * width)
    return "\n".join(lines)
