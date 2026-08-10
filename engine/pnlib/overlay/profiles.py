
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict

@dataclass(frozen=True)
class DeployProfile:
    name: str
    mtu: int
    persistent_keepalive: int
    listen_port: int
    behind_nat: bool
    advertises_endpoint: bool

    def as_params(self) -> Dict[str, object]:
        return asdict(self)

PI = DeployProfile(
    name="pi",
    mtu=1280,
    persistent_keepalive=25,
    listen_port=51820,
    behind_nat=True,
    advertises_endpoint=False,
)

CLOUD_VM = DeployProfile(
    name="cloud-vm",
    mtu=1420,
    persistent_keepalive=15,
    listen_port=51820,
    behind_nat=False,
    advertises_endpoint=True,
)

BARE_METAL = DeployProfile(
    name="bare-metal",
    mtu=1500,
    persistent_keepalive=0,
    listen_port=51821,
    behind_nat=False,
    advertises_endpoint=True,
)

PROFILES: Dict[str, DeployProfile] = {p.name: p for p in (PI, CLOUD_VM, BARE_METAL)}

def get_profile(name: str) -> DeployProfile:
    try:
        return PROFILES[name]
    except KeyError:
        raise KeyError(f"unknown deploy profile {name!r}; known: {sorted(PROFILES)}")

__all__ = ["DeployProfile", "PI", "CLOUD_VM", "BARE_METAL", "PROFILES", "get_profile"]
