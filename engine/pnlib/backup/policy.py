
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

@dataclass(frozen=True)
class Copy:

    target_id: str
    media: str
    offbox: bool = False

MIN_COPIES = 3
MIN_MEDIA = 2
MIN_OFFBOX = 1

@dataclass
class ThreeTwoOneReport:
    ok: bool
    n_copies: int
    n_media: int
    n_offbox: int
    distinct_targets: int
    violations: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "n_copies": self.n_copies, "n_media": self.n_media,
            "n_offbox": self.n_offbox, "distinct_targets": self.distinct_targets,
            "violations": list(self.violations),
        }

def check_3_2_1(copies: Sequence[Copy],
                min_copies: int = MIN_COPIES,
                min_media: int = MIN_MEDIA,
                min_offbox: int = MIN_OFFBOX) -> ThreeTwoOneReport:

    copies = list(copies)

    by_target: Dict[str, Copy] = {}
    for c in copies:
        by_target.setdefault(c.target_id, c)
    uniq = list(by_target.values())

    n_copies = len(uniq)
    media = {c.media for c in uniq}
    n_media = len(media)
    n_offbox = sum(1 for c in uniq if c.offbox)

    violations: List[str] = []
    if n_copies < min_copies:
        violations.append(f"only {n_copies} distinct copies; need >= {min_copies}")
    if n_media < min_media:
        violations.append(f"only {n_media} distinct media ({sorted(media)}); need >= {min_media}")
    if n_offbox < min_offbox:
        violations.append(f"only {n_offbox} off-box copies; need >= {min_offbox}")

    return ThreeTwoOneReport(
        ok=not violations, n_copies=n_copies, n_media=n_media, n_offbox=n_offbox,
        distinct_targets=len(by_target), violations=violations,
    )

@dataclass(frozen=True)
class Retention:

    daily: int = 7
    weekly: int = 4
    monthly: int = 12
    yearly: int = 3
    min_keep: int = 1

@dataclass(frozen=True)
class Snapshot:
    id: str
    when: _dt.datetime

@dataclass
class GfsPlan:
    keep: List[str]
    prune: List[str]
    reasons: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"keep": list(self.keep), "prune": list(self.prune), "reasons": self.reasons}

def _as_utc(d: _dt.datetime) -> _dt.datetime:
    if d.tzinfo is None:
        return d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)

def _period_key(tier: str, d: _dt.datetime) -> Tuple:
    d = _as_utc(d)
    if tier == "daily":
        return (d.year, d.month, d.day)
    if tier == "weekly":
        iso = d.isocalendar()
        return (iso[0], iso[1])
    if tier == "monthly":
        return (d.year, d.month)
    if tier == "yearly":
        return (d.year,)
    raise ValueError(f"unknown tier {tier!r}")

def gfs_plan(snapshots: Iterable[Snapshot], retention: Retention,
             now: Optional[_dt.datetime] = None) -> GfsPlan:

    snaps = sorted(snapshots, key=lambda s: _as_utc(s.when))
    if not snaps:
        return GfsPlan(keep=[], prune=[])

    keep: Dict[str, List[str]] = {}

    def _tier(tier: str, count: int) -> None:
        if count <= 0:
            return

        rep: Dict[Tuple, Snapshot] = {}
        for s in snaps:
            rep[_period_key(tier, s.when)] = s

        chosen_periods = sorted(rep.keys())[-count:]
        for pk in chosen_periods:
            s = rep[pk]
            keep.setdefault(s.id, []).append(tier)

    _tier("daily", retention.daily)
    _tier("weekly", retention.weekly)
    _tier("monthly", retention.monthly)
    _tier("yearly", retention.yearly)

    for s in snaps[-max(retention.min_keep, 0):]:
        keep.setdefault(s.id, []).append("min_keep")

    keep_ids = [s.id for s in snaps if s.id in keep]
    prune_ids = [s.id for s in snaps if s.id not in keep]
    return GfsPlan(keep=keep_ids, prune=prune_ids, reasons={k: keep[k] for k in keep})

@dataclass(frozen=True)
class RotateAction:
    op: str
    snapshot_id: str
    reason: str

def rotate(snapshots: Iterable[Snapshot], retention: Retention,
           now: Optional[_dt.datetime] = None) -> List[RotateAction]:

    plan = gfs_plan(snapshots, retention, now=now)
    actions: List[RotateAction] = []
    for sid in plan.keep:
        actions.append(RotateAction("keep", sid, "+".join(plan.reasons.get(sid, []))))
    for sid in plan.prune:
        actions.append(RotateAction("prune", sid, "aged-out-of-all-gfs-tiers"))
    return actions
