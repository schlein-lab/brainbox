
from __future__ import annotations

import time
from dataclasses import dataclass, field, fields
from typing import Any, List, Optional, Sequence

from pnlib import energy as pnenergy
from pnlib.brain.waist import BrainRequest, BrainReply, ProviderRegistry

_RESIDENCY_ORDER = {"airgap": 0, "local": 1, "cloud": 2, "any": 3}
_RESIDENCY_ALIASES = {
    None: "any", "": "any", "any": "any", "*": "any",
    "airgap": "airgap", "air-gapped": "airgap", "air_gapped": "airgap", "airgapped": "airgap",
    "offline": "airgap", "on-box": "airgap", "onbox": "airgap", "sovereign": "airgap",
    "local": "local", "on-prem": "local", "on_prem": "local", "onprem": "local", "lan": "local",
    "private": "local", "house": "local",
    "cloud": "cloud", "remote": "cloud", "api": "cloud", "external": "cloud", "internet": "cloud",
}

def norm_residency(value: Any) -> str:

    if isinstance(value, str):
        value = value.strip().lower()
    canon = _RESIDENCY_ALIASES.get(value)
    if canon is not None:
        return canon
    return "cloud"

def _residency_level(value: Any) -> int:
    return _RESIDENCY_ORDER[norm_residency(value)]

@dataclass
class ProviderProfile:

    provider: Any = None
    residency: str = "cloud"
    headroom: float = 1.0
    cost: float = 0.0
    latency_ms: float = 0.0
    energy_weight: float = 1.0
    cooldown_until: float = 0.0
    capabilities: Any = None

    def __post_init__(self):
        self.residency = norm_residency(self.residency)
        if self.capabilities is None and self.provider is not None:
            self.capabilities = self.provider.capabilities

    @property
    def name(self) -> str:
        caps = self.capabilities
        if caps is not None:
            return caps.name
        if self.provider is not None:
            return self.provider.capabilities.name
        return "<unnamed>"

    def in_cooldown(self, now: float) -> bool:
        return self.cooldown_until > now

_AUTO = object()

@dataclass
class RouteConstraints:

    residency: Optional[str] = None
    min_headroom: Optional[float] = None
    max_cost: Optional[float] = None
    max_latency_ms: Optional[float] = None
    require_streaming: bool = False
    require_tools: bool = False
    require_vision: bool = False
    require_system: bool = False
    model: Optional[str] = None
    kind: Optional[str] = None
    exclude: Sequence[str] = ()
    prefer: Sequence[str] = ()
    energy_reading: Any = _AUTO
    use_energy: bool = True

    @classmethod
    def from_obj(cls, obj: Any) -> "RouteConstraints":

        if obj is None:
            return cls()
        if isinstance(obj, RouteConstraints):
            return obj
        if isinstance(obj, dict):
            known = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in obj.items() if k in known})
        raise TypeError(f"constraints must be RouteConstraints|dict|None, got {type(obj)!r}")

    def is_empty(self) -> bool:

        return (self.residency is None and self.min_headroom is None and self.max_cost is None
                and self.max_latency_ms is None and not self.require_streaming
                and not self.require_tools and not self.require_vision and not self.require_system
                and self.model is None and self.kind is None
                and not self.exclude and not self.prefer)

@dataclass
class RouteDecision:

    ok: bool
    provider: Any = None
    name: str = ""
    reason: str = ""
    chain: List[str] = field(default_factory=list)
    rejected: List[tuple] = field(default_factory=list)
    retry_after: Optional[float] = None

    @property
    def refused(self) -> bool:
        return not self.ok

    def to_dict(self) -> dict:
        d = {"ok": self.ok, "name": self.name, "reason": self.reason,
             "chain": list(self.chain), "rejected": [list(r) for r in self.rejected]}
        if self.retry_after is not None:
            d["retry_after"] = self.retry_after
        return d

def _feasible(profile: ProviderProfile, c: RouteConstraints) -> Optional[str]:

    caps = profile.capabilities
    name = profile.name
    if name in (c.exclude or ()):
        return "excluded"

    if c.residency is not None:
        if _residency_level(profile.residency) > _residency_level(c.residency):
            return "residency"

    if caps is not None:
        if c.model is not None and not caps.supports_model(c.model):
            return "model"
        if c.kind is not None and c.kind not in (caps.routing_kinds or ()):
            return "kind"
        if c.require_streaming and not caps.streaming:
            return "streaming"
        if c.require_tools and not caps.tools:
            return "tools"
        if c.require_vision and not caps.vision:
            return "vision"
        if c.require_system and not caps.system_prompt:
            return "system"

    if c.min_headroom is not None and profile.headroom < c.min_headroom:
        return "headroom"
    if c.max_cost is not None and profile.cost > c.max_cost:
        return "cost"
    if c.max_latency_ms is not None and profile.latency_ms > c.max_latency_ms:
        return "latency"
    return None

def _resolve_energy_reading(c: RouteConstraints):
    if not c.use_energy:
        return None
    if c.energy_reading is _AUTO:
        return pnenergy.detect()
    return c.energy_reading

class BrainRouter:

    def __init__(self, profiles: Sequence[ProviderProfile]):
        self._profiles: List[ProviderProfile] = list(profiles)
        self._by_name = {p.name: p for p in self._profiles}
        self._registry = ProviderRegistry()
        for p in self._profiles:
            if p.provider is not None:
                self._registry.register(p.provider)

    def names(self) -> List[str]:
        return [p.name for p in self._profiles]

    def profile(self, name: str) -> Optional[ProviderProfile]:
        return self._by_name.get(name)

    @property
    def registry(self) -> ProviderRegistry:
        return self._registry

    @staticmethod
    def _rank_key(profile: ProviderProfile, c: RouteConstraints, reading, now: float):

        prefer = list(c.prefer or ())
        pref_rank = prefer.index(profile.name) if profile.name in prefer else len(prefer)
        bias = pnenergy.energy_bias(reading, {"cost": profile.energy_weight})

        return (pref_rank, -profile.headroom, profile.cost, -bias, profile.latency_ms, profile.name)

    def route(self, req: Optional[BrainRequest] = None, constraints: Any = None,
              config: Optional[dict] = None, now: Optional[float] = None) -> RouteDecision:

        c = RouteConstraints.from_obj(constraints)
        now = time.time() if now is None else now

        if c.is_empty():
            prov = self._registry.select(config) if self._registry.names() else None
            if prov is None and self._profiles:
                prov = self._profiles[0].provider
            name = ""
            if prov is not None:
                name = prov.capabilities.name
            elif self._profiles:
                name = self._profiles[0].name
            return RouteDecision(ok=prov is not None, provider=prov, name=name,
                                 reason="identity",
                                 chain=[name] if name else [])

        feasible: List[ProviderProfile] = []
        rejected: List[tuple] = []
        residency_blocked = False
        for p in self._profiles:
            why = _feasible(p, c)
            if why is None:
                feasible.append(p)
            else:
                rejected.append((p.name, why))
                if why == "residency":
                    residency_blocked = True

        if not feasible:

            reason = "refused:residency" if residency_blocked else "refused:infeasible"
            return RouteDecision(ok=False, provider=None, name="", reason=reason,
                                 chain=[], rejected=rejected)

        reading = _resolve_energy_reading(c)
        feasible.sort(key=lambda p: self._rank_key(p, c, reading, now))
        chain = [p.name for p in feasible]

        for p in feasible:
            if not p.in_cooldown(now):
                return RouteDecision(ok=True, provider=p.provider, name=p.name,
                                     reason="selected", chain=chain, rejected=rejected)

        retry_after = min(p.cooldown_until for p in feasible)
        return RouteDecision(ok=False, provider=None, name="", reason="refused:cooldown",
                             chain=chain, rejected=rejected, retry_after=retry_after)

    def generate(self, req: BrainRequest, constraints: Any = None,
                 config: Optional[dict] = None, now: Optional[float] = None) -> BrainReply:

        d = self.route(req, constraints=constraints, config=config, now=now)
        if not d.ok or d.provider is None:
            return BrainReply(ok=False, provider="",
                              error=f"routing refused ({d.reason})"
                                    + (f"; retry_after={d.retry_after}"
                                       if d.retry_after is not None else ""))
        return d.provider.generate(req)

def make_router(profiles: Sequence[ProviderProfile]) -> BrainRouter:

    return BrainRouter(profiles)
