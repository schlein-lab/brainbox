

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional

class Placement(enum.Enum):
    SERVER_COMPOSITE = "server-composite"
    CLIENT_COMPOSITE = "client-composite"

    @property
    def is_client(self) -> bool:
        return self is Placement.CLIENT_COMPOSITE

_TIER_HEIGHT = {
    Placement.SERVER_COMPOSITE: 0,
    Placement.CLIENT_COMPOSITE: 1,
}

def higher_tier(a: Placement, b: Placement) -> Placement:

    return a if _TIER_HEIGHT[a] >= _TIER_HEIGHT[b] else b

class Policy(enum.Enum):
    CENTRALIZED = "centralized"
    OFFLOAD = "offload"
    AUTO = "auto"

@dataclass(frozen=True)
class ClientCaps:

    webgl2: bool = False
    webgpu: bool = False
    native: bool = False
    cores: int = 4
    mem_mb: int = 4096
    throttled: bool = False

    def can_composite(self) -> bool:

        if self.throttled:
            return False
        return self.webgl2 or self.webgpu or self.native

    @staticmethod
    def from_query(q: dict) -> "ClientCaps":

        def b(k: str) -> bool:
            v = q.get(k)
            return str(v).strip().lower() in ("1", "true", "yes", "on")

        def i(k: str, default: int) -> int:
            try:
                return int(q.get(k, default))
            except (TypeError, ValueError):
                return default

        return ClientCaps(
            webgl2=b("webgl2"),
            webgpu=b("webgpu"),
            native=b("native"),
            cores=i("cores", 4),
            mem_mb=i("mem_mb", 4096),
            throttled=b("throttled"),
        )

@dataclass(frozen=True)
class ServerCaps:

    has_gpu: bool = False
    cores: int = 4
    mem_mb: int = 4096
    loaded: bool = False

    def has_render_headroom(self) -> bool:

        return self.has_gpu and not self.loaded

@dataclass(frozen=True)
class Workload:

    label: str = "default"
    override: Optional[Policy] = None

@dataclass(frozen=True)
class PlacementDecision:

    placement: Placement
    policy_used: Policy
    reason: str

    @property
    def mode(self) -> str:

        return self.placement.value

def decide_placement(
    *,
    client: ClientCaps,
    server: ServerCaps,
    policy: Policy = Policy.AUTO,
    workload: Optional[Workload] = None,
) -> PlacementDecision:

    effective = policy
    reason_prefix = "seat-policy"
    if workload is not None and workload.override is not None:
        effective = workload.override
        reason_prefix = f"workload-override[{workload.label}]"

    can = client.can_composite()

    if effective is Policy.CENTRALIZED:
        return PlacementDecision(
            placement=Placement.SERVER_COMPOSITE,
            policy_used=effective,
            reason=f"{reason_prefix}: centralized -> server-composite (box owns render)",
        )

    if effective is Policy.OFFLOAD:
        if can:
            return PlacementDecision(
                placement=Placement.CLIENT_COMPOSITE,
                policy_used=effective,
                reason=f"{reason_prefix}: offload + client can_composite -> client-composite",
            )
        return PlacementDecision(
            placement=Placement.SERVER_COMPOSITE,
            policy_used=effective,
            reason=f"{reason_prefix}: offload but client cannot composite -> "
            f"server-composite (display-only fallback)",
        )

    if not can:

        return PlacementDecision(
            placement=Placement.SERVER_COMPOSITE,
            policy_used=effective,
            reason=f"{reason_prefix}: auto + client cannot composite -> server-composite",
        )

    if server.has_render_headroom():
        return PlacementDecision(
            placement=Placement.SERVER_COMPOSITE,
            policy_used=effective,
            reason=f"{reason_prefix}: auto + box has GPU render headroom -> server-composite",
        )
    return PlacementDecision(
        placement=Placement.CLIENT_COMPOSITE,
        policy_used=effective,
        reason=f"{reason_prefix}: auto + client can execute + box GPU-less/loaded -> "
        f"client-composite (higher tier, offload to capable device)",
    )

def decide_compute_placement(*args, **kwargs):

    raise NotImplementedError(
        "compute-placement (Execution-client/data-NAS) is scaffolded, not yet "
        "implemented — see module TODO"
    )
