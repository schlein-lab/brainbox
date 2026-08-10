
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Optional

_SAFE_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")

def _assert_safe_name(kind: str, value: str) -> None:

    if not isinstance(value, str) or not _SAFE_NAME.match(value):
        raise CellError(
            f"cell {kind} {value!r} is not a safe identifier "
            f"(must match {_SAFE_NAME.pattern} — no '/', '.', whitespace, unit-file or "
            f"path-traversal characters)")

class CellError(Exception):
    pass

class IllegalTransition(CellError):
    pass

class QuotaExceeded(CellError):
    pass

class AuthorityError(CellError):
    pass

CREATED = "created"
RUNNING = "running"
FROZEN = "frozen"
STOPPED = "stopped"
DESTROYED = "destroyed"

VALID_STATES = (CREATED, RUNNING, FROZEN, STOPPED, DESTROYED)

ACTIONS = {
    "start":   (frozenset({CREATED, STOPPED}), RUNNING),
    "freeze":  (frozenset({RUNNING}),          FROZEN),
    "resume":  (frozenset({FROZEN}),           RUNNING),
    "stop":    (frozenset({RUNNING, FROZEN}),  STOPPED),
    "destroy": (frozenset({CREATED, STOPPED}), DESTROYED),
}

def can_transition(state: str, action: str) -> bool:

    spec = ACTIONS.get(action)
    return bool(spec) and state in spec[0]

def next_state(state: str, action: str) -> str:

    spec = ACTIONS.get(action)
    if spec is None:
        raise IllegalTransition(f"unknown action {action!r}")
    froms, to = spec
    if state not in froms:
        raise IllegalTransition(
            f"illegal transition: cannot {action} a cell in state {state!r} "
            f"(only from {sorted(froms)})")
    return to

@dataclass(frozen=True)
class ResourceEnvelope:

    cpu_pct: int
    mem_bytes: int
    io_bps: int
    net_bps: int
    pids: int = 512

    _DIMS = ("cpu_pct", "mem_bytes", "io_bps", "net_bps", "pids")

    def validate(self) -> "ResourceEnvelope":
        for d in self._DIMS:
            v = getattr(self, d)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise QuotaExceeded(f"envelope.{d} must be a non-negative integer, got {v!r}")
        return self

    def within(self, parent: "ResourceEnvelope") -> bool:

        return all(getattr(self, d) <= getattr(parent, d) for d in self._DIMS)

    def exceeds(self, quota: "ResourceEnvelope") -> tuple[str, ...]:

        return tuple(d for d in self._DIMS if getattr(self, d) > getattr(quota, d))

    def to_dict(self) -> dict:
        return {d: getattr(self, d) for d in self._DIMS}

@dataclass(frozen=True)
class Cell:

    id: str
    tenant: str
    envelope: ResourceEnvelope
    capabilities: frozenset
    state: str = CREATED
    parent: Optional[str] = None
    host_cred_refs: tuple = ()
    holds_no_host_creds: bool = True

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id:
            raise CellError("cell id must be a non-empty string")
        if not isinstance(self.tenant, str) or not self.tenant:
            raise CellError("cell tenant must be a non-empty string")

        _assert_safe_name("id", self.id)
        _assert_safe_name("tenant", self.tenant)
        if not isinstance(self.envelope, ResourceEnvelope):
            raise CellError("cell envelope must be a ResourceEnvelope")
        self.envelope.validate()

        caps = frozenset(str(c) for c in self.capabilities)
        object.__setattr__(self, "capabilities", caps)
        if self.state not in VALID_STATES:
            raise CellError(f"invalid cell state {self.state!r}")

        if self.host_cred_refs:
            raise AuthorityError(
                f"a cell must hold NO host credentials, got {tuple(self.host_cred_refs)!r}")
        if self.holds_no_host_creds is not True:
            raise AuthorityError("holds_no_host_creds must be True for every cell")

    def with_state(self, state: str) -> "Cell":
        return replace(self, state=state)

    def apply(self, action: str) -> "Cell":

        return self.with_state(next_state(self.state, action))

    def to_dict(self) -> dict:
        return {
            "id": self.id, "tenant": self.tenant, "state": self.state,
            "parent": self.parent, "envelope": self.envelope.to_dict(),
            "capabilities": sorted(self.capabilities),
            "host_cred_refs": list(self.host_cred_refs),
            "holds_no_host_creds": self.holds_no_host_creds,
        }

__all__ = [
    "Cell", "ResourceEnvelope",
    "CellError", "IllegalTransition", "QuotaExceeded", "AuthorityError",
    "CREATED", "RUNNING", "FROZEN", "STOPPED", "DESTROYED", "VALID_STATES", "ACTIONS",
    "can_transition", "next_state",
]
