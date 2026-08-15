
from __future__ import annotations

import time

OK = "ok"
NOTICE = "notice"
WARN = "warn"
CRITICAL = "critical"

_SEV_ORDER = {OK: 0, NOTICE: 1, WARN: 2, CRITICAL: 3}

def sev_max(a, b):

    return a if _SEV_ORDER.get(a, 0) >= _SEV_ORDER.get(b, 0) else b

def sev_rank(s):
    return _SEV_ORDER.get(s, 0)

class Reading:

    __slots__ = ("signal", "severity", "value", "unit", "detail", "klartext", "ts")

    def __init__(self, signal, severity, value, klartext, unit="", detail=None, ts=None):
        self.signal = signal
        self.severity = severity
        self.value = value
        self.unit = unit
        self.detail = detail or {}
        self.klartext = klartext
        self.ts = time.time() if ts is None else ts

    @property
    def ok(self):
        return self.severity == OK

    def as_dict(self):
        return {
            "signal": self.signal,
            "severity": self.severity,
            "value": self.value,
            "unit": self.unit,
            "detail": dict(self.detail),
            "klartext": self.klartext,
            "ts": self.ts,
        }

    def __repr__(self):
        return f"<Reading {self.signal} {self.severity} {self.value}{self.unit}>"

class Probe:

    signal = "probe"

    def read(self, now=None):
        raise NotImplementedError

    def _guard(self, fn, now):
        try:
            return fn(now)
        except Exception as exc:
            return Reading(
                self.signal, CRITICAL, None,
                f"Sonde '{self.signal}' fehlgeschlagen: {exc.__class__.__name__}: {exc}",
                detail={"error": str(exc), "error_type": exc.__class__.__name__},
                ts=now,
            )

class DiskFreeProbe(Probe):

    def __init__(self, path="/", df_source=None, warn_pct=10.0, crit_pct=5.0):
        self.path = path
        self.signal = f"disk:{path}"
        self.warn_pct = float(warn_pct)
        self.crit_pct = float(crit_pct)
        self._df = df_source or _statvfs_source

    def read(self, now=None):
        return self._guard(self._read, now)

    def _read(self, now):
        free_b, total_b = self._df(self.path)
        free_b = max(0, int(free_b))
        total_b = max(1, int(total_b))
        free_pct = round(100.0 * free_b / total_b, 2)
        if free_pct <= self.crit_pct:
            sev = CRITICAL
        elif free_pct <= self.warn_pct:
            sev = WARN
        else:
            sev = OK
        gib = free_b / (1024 ** 3)
        kt = f"Speicher {self.path}: {free_pct:.1f}% frei ({gib:.1f} GiB)"
        if sev != OK:
            kt += f" — unter dem {self.warn_pct:.0f}%-Limit" if sev == WARN else \
                  f" — KRITISCH unter {self.crit_pct:.0f}%"
        return Reading(self.signal, sev, free_pct, kt, unit="%",
                       detail={"path": self.path, "free_bytes": free_b, "total_bytes": total_b},
                       ts=now)

def _statvfs_source(path):
    import os
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize

class DyingDiskProbe(Probe):

    _DMESG_MARKERS = ("ext4-fs error", "ext4_", "i/o error", "failed command",
                      "medium error", "unrecovered read error", "buffer i/o error")

    def __init__(self, device="sda", smart_fn=None, dmesg_fn=None,
                 realloc_warn=1, crc_warn=1):
        self.device = device
        self.signal = f"disk-health:{device}"
        self._smart = smart_fn or (lambda: {})
        self._dmesg = dmesg_fn or (lambda: [])
        self.realloc_warn = int(realloc_warn)
        self.crc_warn = int(crc_warn)

    def read(self, now=None):
        return self._guard(self._read, now)

    def _read(self, now):
        smart = self._smart() or {}
        lines = self._dmesg() or []
        pending = int(smart.get("current_pending_sector", 0) or 0)
        offline = int(smart.get("offline_uncorrectable", 0) or 0)
        realloc = int(smart.get("reallocated_sector_ct", 0) or 0)
        crc = int(smart.get("udma_crc_error_count", 0) or 0)
        hits = [ln for ln in lines
                if any(m in ln.lower() for m in self._DMESG_MARKERS)]

        sev = OK
        reasons = []
        if pending > 0 or offline > 0 or hits:
            sev = CRITICAL
            if pending:
                reasons.append(f"{pending} pending sectors")
            if offline:
                reasons.append(f"{offline} offline-uncorrectable")
            if hits:
                reasons.append(f"{len(hits)} Kernel-EXT4/I-O-Fehler")
        elif realloc >= self.realloc_warn or crc >= self.crc_warn:
            sev = WARN
            if realloc:
                reasons.append(f"{realloc} reallocated sectors")
            if crc:
                reasons.append(f"{crc} UDMA-CRC-Fehler")

        if sev == OK:
            kt = f"Disk {self.device}: SMART sauber, kein Kernel-Fehler"
        else:
            tag = "STIRBT" if sev == CRITICAL else "degradiert"
            kt = f"Disk {self.device} {tag}: " + ", ".join(reasons)
        return Reading(self.signal, sev, len(hits), kt, unit=" ext4-errs",
                       detail={"device": self.device, "pending": pending, "offline": offline,
                               "reallocated": realloc, "crc": crc,
                               "dmesg_hits": hits[:10]},
                       ts=now)

class ServiceLivenessProbe(Probe):

    def __init__(self, name, alive_fn):
        self.name = name
        self.signal = f"service:{name}"
        self._alive = alive_fn

    def read(self, now=None):
        return self._guard(self._read, now)

    def _read(self, now):
        up = bool(self._alive(self.name))
        sev = OK if up else CRITICAL
        kt = f"Dienst {self.name}: {'läuft' if up else 'TOT / nicht erreichbar'}"
        return Reading(self.signal, sev, up, kt,
                       detail={"service": self.name, "alive": up}, ts=now)

class MemoryPressureProbe(Probe):

    def __init__(self, meminfo_source=None, warn_pct=85.0, crit_pct=95.0):
        self.signal = "memory"
        self.warn_pct = float(warn_pct)
        self.crit_pct = float(crit_pct)
        self._src = meminfo_source or _meminfo_source

    def read(self, now=None):
        return self._guard(self._read, now)

    def _read(self, now):
        total_kb, avail_kb = self._src()
        total_kb = max(1, int(total_kb))
        avail_kb = max(0, int(avail_kb))
        used_pct = round(100.0 * (total_kb - avail_kb) / total_kb, 2)
        if used_pct >= self.crit_pct:
            sev = CRITICAL
        elif used_pct >= self.warn_pct:
            sev = WARN
        else:
            sev = OK
        kt = f"RAM: {used_pct:.1f}% belegt ({avail_kb // 1024} MiB frei)"
        if sev != OK:
            kt += " — Druck HOCH" if sev == WARN else " — KRITISCH, OOM-Risiko"
        return Reading(self.signal, sev, used_pct, kt, unit="%",
                       detail={"total_kb": total_kb, "available_kb": avail_kb}, ts=now)

def _meminfo_source():
    total = avail = 0
    with open("/proc/meminfo") as fh:
        for line in fh:
            if line.startswith("MemTotal:"):
                total = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                avail = int(line.split()[1])
    return total, avail

class BackupFreshnessProbe(Probe):

    def __init__(self, last_backup_fn, max_age_s=26 * 3600, crit_age_s=72 * 3600):
        self.signal = "backup"
        self._last = last_backup_fn
        self.max_age_s = float(max_age_s)
        self.crit_age_s = float(crit_age_s)

    def read(self, now=None):
        return self._guard(self._read, now)

    def _read(self, now):
        ts = time.time() if now is None else now
        last = self._last()
        if last is None:
            return Reading(self.signal, CRITICAL, None,
                           "Backup: NIE gelaufen / unbekannt — keine Wiederherstellung möglich",
                           detail={"last_backup": None}, ts=now)
        age = max(0.0, ts - float(last))
        hours = age / 3600.0
        if age >= self.crit_age_s:
            sev = CRITICAL
        elif age >= self.max_age_s:
            sev = WARN
        else:
            sev = OK
        kt = f"Backup: letztes vor {hours:.1f} h"
        if sev == WARN:
            kt += f" — überfällig (> {self.max_age_s / 3600:.0f} h)"
        elif sev == CRITICAL:
            kt += f" — STARK überfällig (> {self.crit_age_s / 3600:.0f} h)"
        return Reading(self.signal, sev, round(hours, 2), kt, unit=" h",
                       detail={"last_backup": last, "age_s": round(age, 1)}, ts=now)

class CertExpiryProbe(Probe):

    def __init__(self, name, expiry_fn, warn_days=21, crit_days=7):
        self.name = name
        self.signal = f"cert:{name}"
        self._exp = expiry_fn
        self.warn_days = float(warn_days)
        self.crit_days = float(crit_days)

    def read(self, now=None):
        return self._guard(self._read, now)

    def _read(self, now):
        ts = time.time() if now is None else now
        exp = float(self._exp())
        days = (exp - ts) / 86400.0
        if days <= self.crit_days:
            sev = CRITICAL
        elif days <= self.warn_days:
            sev = WARN
        else:
            sev = OK
        kt = f"Zertifikat {self.name}: {days:.1f} Tage gültig"
        if sev == WARN:
            kt += " — läuft bald ab"
        elif sev == CRITICAL:
            kt += " — läuft SEHR bald / abgelaufen"
        return Reading(self.signal, sev, round(days, 2), kt, unit=" d",
                       detail={"cert": self.name, "expires_at": exp}, ts=now)
