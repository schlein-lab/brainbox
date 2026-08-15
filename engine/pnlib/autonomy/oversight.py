
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from typing import Optional, Tuple

from .levels import (
    CEREMONY_NONE, CEREMONY_NOTIFY, CEREMONY_APPROVE, CEREMONY_ENVELOPE,
    CEREMONY_NAMES, require_ceremony_stage,
)

class OversightError(Exception):
    pass

ROLE_OPERATOR = "operator"
ROLE_APPROVER = "approver"
ROLE_OBSERVER = "observer"
ROLES = (ROLE_OPERATOR, ROLE_APPROVER, ROLE_OBSERVER)

@dataclass(frozen=True)
class Oversight:

    job_id: str
    operator: str
    approver: Optional[str] = None
    observer: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.job_id, str) or not self.job_id:
            raise OversightError("job_id must be a non-empty string")
        if not isinstance(self.operator, str) or not self.operator:
            raise OversightError("operator must be a non-empty string")
        for role, val in (("approver", self.approver), ("observer", self.observer)):
            if val is not None and (not isinstance(val, str) or not val):
                raise OversightError(f"{role} must be a non-empty string or None")

    def has(self, role: str) -> bool:
        if role == ROLE_OPERATOR:
            return True
        if role == ROLE_APPROVER:
            return self.approver is not None
        if role == ROLE_OBSERVER:
            return self.observer is not None
        raise OversightError(f"unknown role {role!r}")

    def role_of(self, principal: str) -> Optional[str]:

        if principal == self.operator:
            return ROLE_OPERATOR
        if self.approver is not None and principal == self.approver:
            return ROLE_APPROVER
        if self.observer is not None and principal == self.observer:
            return ROLE_OBSERVER
        return None

    def separation_ok(self) -> bool:

        return self.approver is None or self.approver != self.operator

    def ceremony_satisfiable(self, stage: int) -> Tuple[bool, str]:

        st = require_ceremony_stage(stage)
        if st == CEREMONY_NONE:
            return True, ""
        if st == CEREMONY_NOTIFY:
            if self.observer is None:
                return False, "NOTIFY ceremony needs an observer, none configured"
            return True, ""
        if st == CEREMONY_APPROVE:
            if self.approver is None:
                return False, "APPROVE ceremony needs an approver, none configured"
            if not self.separation_ok():
                return False, "APPROVE ceremony violates separation of duties (approver == operator)"
            return True, ""
        if st == CEREMONY_ENVELOPE:

            if self.observer is None:
                return False, "ENVELOPE (full-auto) ceremony needs a standing observer, none configured"
            return True, ""
        return False, f"unknown ceremony stage {st!r}"

@dataclass(frozen=True)
class Approval:

    job_id: str
    action: str
    approver: str
    stage: int
    at: float
    note: str = ""

    def to_dict(self) -> dict:
        return {"job_id": self.job_id, "action": self.action, "approver": self.approver,
                "stage": self.stage, "stage_name": CEREMONY_NAMES.get(self.stage, "?"),
                "at": self.at, "note": self.note}

class OversightSession:

    def __init__(self, oversight: Oversight) -> None:
        if not isinstance(oversight, Oversight):
            raise OversightError("OversightSession needs an Oversight")
        self.oversight = oversight
        self._approvals: list[Approval] = []

    def approve(self, action: str, *, approver: str, stage: int = CEREMONY_APPROVE,
                at: Optional[float] = None, note: str = "") -> Approval:

        st = require_ceremony_stage(stage)
        ok, why = self.oversight.ceremony_satisfiable(st)
        if not ok:
            raise OversightError(f"cannot approve: {why}")
        if st >= CEREMONY_APPROVE:
            if approver != self.oversight.approver:
                raise OversightError(
                    f"approver {approver!r} is not the configured approver "
                    f"{self.oversight.approver!r}")
            if approver == self.oversight.operator:
                raise OversightError("separation of duties: operator cannot approve its own action")
        rec = Approval(job_id=self.oversight.job_id, action=action, approver=approver,
                       stage=st, at=_time.time() if at is None else float(at), note=note)
        self._approvals.append(rec)
        return rec

    def approvals(self) -> Tuple[Approval, ...]:
        return tuple(self._approvals)

    def is_satisfied(self, action: str, stage: int) -> bool:

        st = require_ceremony_stage(stage)
        if st in (CEREMONY_NONE, CEREMONY_ENVELOPE):
            ok, _ = self.oversight.ceremony_satisfiable(st)
            return ok
        if st == CEREMONY_APPROVE:

            return any(a.action == action and a.stage == CEREMONY_APPROVE for a in self._approvals)
        return any(a.action == action and a.stage >= st for a in self._approvals)

__all__ = [
    "OversightError", "Oversight", "OversightSession", "Approval",
    "ROLE_OPERATOR", "ROLE_APPROVER", "ROLE_OBSERVER", "ROLES",
]
