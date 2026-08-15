
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from pnlib.schema_bitemporal import ORIGINS

class OriginKind(str, enum.Enum):

    HUMAN = "human"
    AGENT = "agent"
    MODEL = "model"

    @classmethod
    def coerce(cls, v: "OriginKind | str") -> "OriginKind":
        if isinstance(v, cls):
            return v
        if isinstance(v, str):
            try:
                return cls(v)
            except ValueError:
                pass
        raise ValueError(f"origin kind must be one of {tuple(k.value for k in cls)}, got {v!r}")

assert tuple(k.value for k in OriginKind) == ORIGINS, "OriginKind must mirror schema_bitemporal.ORIGINS"

@dataclass(frozen=True)
class Origin:

    kind: OriginKind
    component: str
    actor: Optional[str] = None
    agent_id: Optional[str] = None

    def __post_init__(self):
        object.__setattr__(self, "kind", OriginKind.coerce(self.kind))
        if not isinstance(self.component, str) or not self.component.strip():
            raise ValueError("Origin.component is required and must be a non-empty string")

    @classmethod
    def human(cls, component: str, *, actor: Optional[str] = None,
              agent_id: Optional[str] = None) -> "Origin":
        return cls(OriginKind.HUMAN, component, actor=actor, agent_id=agent_id)

    @classmethod
    def agent(cls, component: str, *, agent_id: Optional[str] = None,
              actor: Optional[str] = None) -> "Origin":
        return cls(OriginKind.AGENT, component, actor=actor, agent_id=agent_id)

    @classmethod
    def model(cls, component: str, *, agent_id: Optional[str] = None,
              actor: Optional[str] = None) -> "Origin":
        return cls(OriginKind.MODEL, component, actor=actor, agent_id=agent_id)

    def to_dict(self) -> dict:

        d = {"kind": self.kind.value, "component": self.component}
        if self.actor is not None:
            d["actor"] = self.actor
        if self.agent_id is not None:
            d["agent_id"] = self.agent_id
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Origin":
        return cls(OriginKind.coerce(d["kind"]), d["component"],
                   actor=d.get("actor"), agent_id=d.get("agent_id"))

def origin(kind, component: str, *, actor: Optional[str] = None,
           agent_id: Optional[str] = None) -> Origin:

    return Origin(kind, component, actor=actor, agent_id=agent_id)

def require_origin(value) -> Origin:

    if value is None:
        raise ValueError("origin is REQUIRED: every ledger record must carry a provenance stamp")
    if isinstance(value, Origin):
        return value
    if isinstance(value, dict):
        return Origin.from_dict(value)
    raise TypeError(f"origin must be a pnlib.origin.Origin (or its dict form), got {type(value).__name__}")
