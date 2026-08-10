
from __future__ import annotations

import time

from . import probes as _probes
from .probes import OK, NOTICE, WARN, CRITICAL, sev_rank

LVL_OK = 0
LVL_NOTICE = 1
LVL_REMEDIATE = 2
LVL_WARN = 3
LVL_DEGRADE = 4

LEVEL_NAME = {
    LVL_OK: "ok",
    LVL_NOTICE: "notice",
    LVL_REMEDIATE: "auto-remediate",
    LVL_WARN: "warn-admin",
    LVL_DEGRADE: "safe-degrade",
}

class Decision:

    __slots__ = ("signal", "severity", "level", "level_name", "prev_level",
                 "transitioned", "bad_streak", "good_streak", "action", "note", "reading")

    def __init__(self, signal, severity, level, prev_level, bad_streak, good_streak,
                 reading, note=""):
        self.signal = signal
        self.severity = severity
        self.level = level
        self.level_name = LEVEL_NAME[level]
        self.prev_level = prev_level
        self.transitioned = level != prev_level
        self.bad_streak = bad_streak
        self.good_streak = good_streak
        self.reading = reading
        self.action = LEVEL_NAME[level]
        self.note = note

    @property
    def rising(self):
        return self.level > self.prev_level

    def as_dict(self):
        return {
            "signal": self.signal, "severity": self.severity,
            "level": self.level, "level_name": self.level_name,
            "prev_level": self.prev_level, "transitioned": self.transitioned,
            "rising": self.rising, "bad_streak": self.bad_streak,
            "good_streak": self.good_streak, "note": self.note,
        }

    def __repr__(self):
        arrow = "^" if self.rising else ("v" if self.level < self.prev_level else "=")
        return f"<Decision {self.signal} {self.level_name}{arrow} bad={self.bad_streak}>"

class Escalator:

    def __init__(self, signal, raise_after=2, remediate_at=2, warn_at=3, degrade_at=5,
                 clear_after=2):
        self.signal = signal
        self.raise_after = int(raise_after)
        self.remediate_at = int(remediate_at)
        self.warn_at = int(warn_at)
        self.degrade_at = int(degrade_at)
        self.clear_after = int(clear_after)
        self.level = LVL_OK
        self.bad_streak = 0
        self.good_streak = 0

    def observe(self, severity, has_remediation):

        prev = self.level
        if severity == OK:
            self.good_streak += 1
            self.bad_streak = 0

            if self.good_streak >= self.clear_after:
                self.level = LVL_OK

            return self.level, prev

        self.good_streak = 0
        self.bad_streak += 1
        crit = severity == CRITICAL
        shift = 1 if crit else 0
        r_at = max(1, self.remediate_at - shift)
        w_at = max(1, self.warn_at - shift)
        d_at = max(1, self.degrade_at - shift)

        if self.bad_streak < self.raise_after:

            target = LVL_NOTICE
        elif self.bad_streak >= d_at:
            target = LVL_DEGRADE
        elif self.bad_streak >= w_at:
            target = LVL_WARN
        elif self.bad_streak >= r_at:

            target = LVL_REMEDIATE if has_remediation else LVL_WARN
        else:
            target = LVL_NOTICE

        self.level = max(self.level, target)
        return self.level, prev

class HealLoop:

    def __init__(self, probes, notifier=None, remediations=None, degrader=None,
                 raise_after=2, remediate_at=2, warn_at=3, degrade_at=5, clear_after=2):
        self.probes = list(probes)
        self.notifier = notifier
        self.remediations = dict(remediations or {})
        self.degrader = degrader
        self._esc_kw = dict(raise_after=raise_after, remediate_at=remediate_at,
                            warn_at=warn_at, degrade_at=degrade_at, clear_after=clear_after)
        self.escalators = {}
        self.last_readings = []
        self.last_decisions = []
        self.remediation_log = []

    def _esc(self, signal):
        e = self.escalators.get(signal)
        if e is None:
            e = Escalator(signal, **self._esc_kw)
            self.escalators[signal] = e
        return e

    def observe(self, now=None):

        return [p.read(now) for p in self.probes]

    def step(self, now=None):

        if now is None:
            now = time.time()
        readings = self.observe(now)
        decisions = []
        for r in readings:
            esc = self._esc(r.signal)
            has_rem = r.signal in self.remediations
            level, prev = esc.observe(r.severity, has_rem)
            dec = Decision(r.signal, r.severity, level, prev,
                           esc.bad_streak, esc.good_streak, r)
            self._act(dec)
            decisions.append(dec)
        self.last_readings = readings
        self.last_decisions = decisions
        return readings, decisions

    def _act(self, dec):
        r = dec.reading
        if dec.level == LVL_REMEDIATE:
            fn = self.remediations.get(dec.signal)
            if fn is not None:
                try:
                    ok = bool(fn(r))
                except Exception as exc:
                    ok = False
                    dec.note = f"remediation raised {exc.__class__.__name__}"
                else:
                    dec.note = "auto-remediation " + ("succeeded" if ok else "failed")
                self.remediation_log.append((dec.signal, ok))
        elif dec.level == LVL_WARN:

            if dec.rising and self.notifier is not None:
                self.notifier.warn(r, dec)
                dec.note = "admin warned"
        elif dec.level == LVL_DEGRADE:
            if dec.rising:
                if self.notifier is not None:
                    self.notifier.warn(r, dec)
                if self.degrader is not None:
                    try:
                        self.degrader(r, dec)
                        dec.note = "safe-degrade engaged"
                    except Exception as exc:
                        dec.note = f"degrade hook raised {exc.__class__.__name__}"
        elif dec.level == LVL_OK and dec.prev_level >= LVL_WARN:

            if self.notifier is not None:
                self.notifier.resolved(r, dec)
                dec.note = "resolved, admin notified"
