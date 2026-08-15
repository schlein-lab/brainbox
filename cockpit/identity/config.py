

from __future__ import annotations

import enum
from dataclasses import dataclass

class FederationPolicy(str, enum.Enum):
    OWNER_APPROVE = "owner-approve-each-device"
    CHAIN_OF_TRUST = "chain-of-trust"

@dataclass(frozen=True)
class IdentityConfig:

    federation_policy: FederationPolicy = FederationPolicy.OWNER_APPROVE

    skew_ms: int = 120_000

    contract_version: str = "portal-contract/1"

    @staticmethod
    def default() -> "IdentityConfig":
        return IdentityConfig()
