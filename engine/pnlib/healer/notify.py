
from __future__ import annotations

import time

from .probes import CRITICAL, WARN

def condition_for(signal):
    s = (signal or "").lower()
    if s.startswith("disk-health:"):
        return "dying-disk"
    if s.startswith("disk:"):
        return "disk-full"
    if s.startswith("service:"):
        return "service-fault"
    if s.startswith("cert:"):
        return "cert-expiry"
    if s == "backup":
        return "backup-failed"
    if s == "memory":
        return "memory-pressure"
    return "health-fault"

_CONDITION_HINT = {
    "disk-full": "Alte Records/Work-Copies GCen, Logs rotieren, Replikation prüfen.",
    "dying-disk": "SOFORT read-only sichern und Medium tauschen (SMART/EXT4-Fehler).",
    "backup-failed": "Backup-Job und Off-Box-Ziel prüfen — keine Wiederherstellung möglich.",
    "service-fault": "Dienst über pn-init neu starten und Logs prüfen.",
    "cert-expiry": "Zertifikat erneuern, bevor es abläuft.",
    "memory-pressure": "Lastabwurf/Queue drosseln, Speicherfresser identifizieren.",
    "health-fault": "Systemzustand prüfen.",
}

class NotifySink:

    def emit(self, msg):
        raise NotImplementedError

class MockSink(NotifySink):

    def __init__(self):
        self.sent = []

    def emit(self, msg):
        self.sent.append(msg)

    def count(self, condition=None, kind=None):
        return sum(1 for m in self.sent
                   if (condition is None or m["condition"] == condition)
                   and (kind is None or m["kind"] == kind))

    def last(self):
        return self.sent[-1] if self.sent else None

class EmailSink(NotifySink):

    def __init__(self, send_fn, to="admin"):
        self._send = send_fn
        self.to = to

    def emit(self, msg):
        self._send(self.to, msg["subject"], msg["body"])

class HealerNotifier:

    def __init__(self, sink, cooldown_s=1800, clock=None):
        self.sink = sink
        self.cooldown_s = float(cooldown_s)
        self._clock = clock or time.time
        self._last_sent = {}

    def _throttled(self, key, now):
        prev = self._last_sent.get(key)
        if prev is not None and (now - prev) < self.cooldown_s:
            return True
        self._last_sent[key] = now
        return False

    def warn(self, reading, decision):

        now = self._clock()
        kind = "safe-degrade" if decision.level_name == "safe-degrade" else "warn"
        key = (reading.signal, kind)
        if self._throttled(key, now):
            return None
        condition = condition_for(reading.signal)
        sev = reading.severity
        urgency = "KRITISCH" if sev == CRITICAL else ("WARNUNG" if sev == WARN else sev.upper())
        subject = f"[Brainarbeit] {urgency}: {condition} ({reading.signal})"
        body = self._klartext(reading, decision, condition, kind)
        msg = {
            "kind": kind,
            "condition": condition,
            "signal": reading.signal,
            "severity": sev,
            "level": decision.level_name,
            "subject": subject,
            "body": body,
            "klartext": reading.klartext,
            "value": reading.value,
            "ts": now,
        }
        self.sink.emit(msg)
        return msg

    def resolved(self, reading, decision):

        now = self._clock()
        key = (reading.signal, "resolved")

        prev = self._last_sent.get(key)
        if prev is not None and (now - prev) < min(self.cooldown_s, 60):
            return None
        self._last_sent[key] = now
        condition = condition_for(reading.signal)
        subject = f"[Brainarbeit] behoben: {condition} ({reading.signal})"
        body = (f"Zustand '{condition}' ({reading.signal}) ist wieder OK.\n"
                f"  {reading.klartext}\n"
                f"  Eskalation zurückgesetzt auf: {decision.level_name}.")
        msg = {
            "kind": "resolved", "condition": condition, "signal": reading.signal,
            "severity": reading.severity, "level": decision.level_name,
            "subject": subject, "body": body, "klartext": reading.klartext,
            "value": reading.value, "ts": now,
        }
        self.sink.emit(msg)
        return msg

    @staticmethod
    def _klartext(reading, decision, condition, kind):
        hint = _CONDITION_HINT.get(condition, _CONDITION_HINT["health-fault"])
        head = ("SAFE-DEGRADE ausgelöst" if kind == "safe-degrade"
                else "Admin-Warnung")
        lines = [
            f"{head} — Zustand: {condition}",
            "",
            f"  Signal   : {reading.signal}",
            f"  Schwere  : {reading.severity}",
            f"  Messwert : {reading.value}{reading.unit}",
            f"  Klartext : {reading.klartext}",
            f"  Eskalation: {decision.level_name} "
            f"(schlechte Messungen in Folge: {decision.bad_streak})",
            "",
            f"  Empfehlung: {hint}",
        ]
        return "\n".join(lines)
