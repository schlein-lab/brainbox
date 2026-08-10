
from __future__ import annotations

import datetime as _dt
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

INFO, WARNING, CRITICAL = "info", "warning", "critical"
_SEV_RANK = {INFO: 0, WARNING: 1, CRITICAL: 2}

@dataclass
class Warning:

    severity: str
    code: str
    subject: str
    message: str
    remediation: str = ""
    detail: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "subject": self.subject,
                "message": self.message, "remediation": self.remediation, "detail": self.detail}

class SmartReader(ABC):
    @abstractmethod
    def read(self, device: str) -> Dict[str, int]:

        ...

class MockSmartReader(SmartReader):

    def __init__(self, tables: Dict[str, Dict[str, int]]):
        self._tables = tables

    def read(self, device: str) -> Dict[str, int]:
        return dict(self._tables.get(device, {}))

class SmartctlReader(SmartReader):

    _ATTR_MAP = {
        5: "reallocated_sector_ct",
        197: "current_pending_sector",
        198: "offline_uncorrectable",
        199: "udma_crc_error_count",
        177: "wear_leveling_count",
        231: "ssd_life_left",
        233: "media_wearout_indicator",
    }

    def read(self, device: str) -> Dict[str, int]:
        import json
        if not shutil.which("smartctl"):
            return {}
        out = subprocess.run(["smartctl", "-A", "-j", device],
                             capture_output=True, text=True)
        try:
            data = json.loads(out.stdout or "{}")
        except Exception:
            return {}
        res: Dict[str, int] = {}
        for a in (data.get("ata_smart_attributes", {}) or {}).get("table", []):
            key = self._ATTR_MAP.get(a.get("id"))
            if key:
                res[key] = int((a.get("raw", {}) or {}).get("value", 0))

        nv = data.get("nvme_smart_health_information_log", {}) or {}
        if "percentage_used" in nv:
            res["nvme_percentage_used"] = int(nv["percentage_used"])
        if "available_spare" in nv:
            res["nvme_available_spare"] = int(nv["available_spare"])
        return res

REALLOC_WARN = 1
REALLOC_CRIT = 200
PENDING_WARN = 1
OFFLINE_UNCORR_WARN = 1
CRC_WARN = 100
NVME_USED_WARN = 80
NVME_USED_CRIT = 95
NVME_SPARE_WARN = 20
NVME_SPARE_CRIT = 10
SSD_LIFE_WARN = 20
SSD_LIFE_CRIT = 10

@dataclass
class DiskHealth:
    device: str
    healthy: bool
    warnings: List[Warning] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"device": self.device, "healthy": self.healthy,
                "warnings": [w.to_dict() for w in self.warnings]}

def _mk(sev, code, dev, msg, remediation, **detail) -> Warning:
    return Warning(severity=sev, code=code, subject=dev, message=msg,
                   remediation=remediation, detail=detail)

def assess_disk(device: str, attrs: Dict[str, int]) -> DiskHealth:

    ws: List[Warning] = []

    realloc = int(attrs.get("reallocated_sector_ct", 0))
    if realloc >= REALLOC_CRIT:
        ws.append(_mk(CRITICAL, "disk.reallocated.crit", device,
                      f"{device}: {realloc} reallocated sectors — disk is failing",
                      "evacuate data now; force an off-box backup and replace the disk",
                      reallocated_sector_ct=realloc))
    elif realloc >= REALLOC_WARN:
        ws.append(_mk(WARNING, "disk.reallocated", device,
                      f"{device}: {realloc} reallocated sector(s) — early wear",
                      "schedule replacement; verify backups are current",
                      reallocated_sector_ct=realloc))

    pending = int(attrs.get("current_pending_sector", 0))
    if pending >= PENDING_WARN:
        ws.append(_mk(WARNING, "disk.pending", device,
                      f"{device}: {pending} pending (unstable) sector(s)",
                      "run a surface scan; back up before it worsens", current_pending_sector=pending))

    offl = int(attrs.get("offline_uncorrectable", 0))
    if offl >= OFFLINE_UNCORR_WARN:
        ws.append(_mk(CRITICAL, "disk.uncorrectable", device,
                      f"{device}: {offl} offline-uncorrectable sector(s) — data loss risk",
                      "replace disk; restore affected files from backup",
                      offline_uncorrectable=offl))

    crc = int(attrs.get("udma_crc_error_count", 0))
    if crc >= CRC_WARN:
        ws.append(_mk(WARNING, "disk.crc", device,
                      f"{device}: {crc} interface CRC errors",
                      "check/replace the cable or SD reader", udma_crc_error_count=crc))

    used = attrs.get("nvme_percentage_used")
    if used is not None:
        used = int(used)
        if used >= NVME_USED_CRIT:
            ws.append(_mk(CRITICAL, "flash.endurance.crit", device,
                          f"{device}: {used}% of rated flash endurance consumed",
                          "replace the flash device; migrate root off it",
                          nvme_percentage_used=used))
        elif used >= NVME_USED_WARN:
            ws.append(_mk(WARNING, "flash.endurance", device,
                          f"{device}: {used}% of rated flash endurance consumed",
                          "plan flash replacement", nvme_percentage_used=used))

    spare = attrs.get("nvme_available_spare")
    if spare is not None:
        spare = int(spare)
        if spare <= NVME_SPARE_CRIT:
            ws.append(_mk(CRITICAL, "flash.spare.crit", device,
                          f"{device}: only {spare}% NVMe spare blocks remain",
                          "replace immediately; spare exhaustion bricks the device",
                          nvme_available_spare=spare))
        elif spare <= NVME_SPARE_WARN:
            ws.append(_mk(WARNING, "flash.spare", device,
                          f"{device}: NVMe spare down to {spare}%",
                          "schedule replacement", nvme_available_spare=spare))

    life = attrs.get("ssd_life_left")
    if life is not None:
        life = int(life)
        if life <= SSD_LIFE_CRIT:
            ws.append(_mk(CRITICAL, "flash.life.crit", device,
                          f"{device}: SSD life-left {life}%",
                          "replace immediately", ssd_life_left=life))
        elif life <= SSD_LIFE_WARN:
            ws.append(_mk(WARNING, "flash.life", device,
                          f"{device}: SSD life-left {life}%", "plan replacement", ssd_life_left=life))

    healthy = not any(w.severity in (WARNING, CRITICAL) for w in ws)
    return DiskHealth(device=device, healthy=healthy, warnings=ws)

def assess_disk_via(reader: SmartReader, device: str) -> DiskHealth:

    return assess_disk(device, reader.read(device))

def _parse_when(ts) -> _dt.datetime:
    if isinstance(ts, _dt.datetime):
        d = ts
    else:
        d = _dt.datetime.fromisoformat(str(ts))
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)

def backup_freshness(last_backup_at, max_age: _dt.timedelta,
                     now: Optional[_dt.datetime] = None,
                     subject: str = "backup") -> Optional[Warning]:

    now = _parse_when(now) if now is not None else _dt.datetime.now(_dt.timezone.utc)
    if last_backup_at is None:
        return _mk(CRITICAL, "backup.missing", subject,
                   "no successful backup on record",
                   "run an initial 3-2-1 backup now")
    last = _parse_when(last_backup_at)
    age = now - last
    if age > max_age:
        sev = CRITICAL if age > max_age * 3 else WARNING
        return _mk(sev, "backup.stale", subject,
                   f"last backup is {int(age.total_seconds() // 3600)}h old "
                   f"(> {int(max_age.total_seconds() // 3600)}h threshold)",
                   "trigger a fresh backup; verify off-box target reachable",
                   age_seconds=int(age.total_seconds()),
                   max_age_seconds=int(max_age.total_seconds()))
    return None

@dataclass
class HealthReport:
    ok: bool
    warnings: List[Warning] = field(default_factory=list)

    @property
    def worst(self) -> str:
        return max((w.severity for w in self.warnings), key=lambda s: _SEV_RANK[s], default=INFO)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "worst": self.worst,
                "warnings": [w.to_dict() for w in self.warnings]}

def collect_warnings(disk_reports: Optional[List[DiskHealth]] = None,
                     freshness: Optional[Warning] = None) -> HealthReport:

    ws: List[Warning] = []
    for dr in (disk_reports or []):
        ws.extend(dr.warnings)
    if freshness is not None:
        ws.append(freshness)
    ok = not any(w.severity in (WARNING, CRITICAL) for w in ws)
    return HealthReport(ok=ok, warnings=ws)
