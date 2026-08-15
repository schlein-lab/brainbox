

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional

class Lifetime(enum.Enum):
    FINITE = "finite"
    CHRONIC = "chronic"
    ONESHOT = "oneshot"

class LatencyClass(enum.Enum):
    REALTIME = "realtime"
    DEFERRABLE = "deferrable"
    FILLER = "filler"

class Slice(enum.Enum):
    PN_CRITICAL = "pn-critical"
    PN_INTERACTIVE = "pn-interactive"
    PN_BATCH = "pn-batch"

class SpendMode(enum.Enum):
    ON_DEMAND = "on_demand"
    CONTINUOUS = "continuous"

@dataclass(frozen=True)
class LlmSlotReservation:

    lease_name: str
    principal: str
    reserved: bool = True
    concurrency: int = 1
    priority_bypass: bool = True

@dataclass(frozen=True)
class RealtimeLaneJob:

    principal: str
    lifetime: Lifetime = Lifetime.CHRONIC
    latency_class: LatencyClass = LatencyClass.REALTIME
    slice: Slice = Slice.PN_INTERACTIVE
    spend: SpendMode = SpendMode.ON_DEMAND
    funding: str = "member-subsidized"
    llm_slot: Optional[LlmSlotReservation] = None

    first_earcon_ms: int = 300
    first_token_ms: int = 1000

    def to_admission(self) -> dict:

        d = {
            "principal": self.principal,
            "lifetime": self.lifetime.value,
            "latency_class": self.latency_class.value,
            "slice": self.slice.value,
            "spend": self.spend.value,
            "funding": self.funding,
            "budget": {
                "first_earcon_ms": self.first_earcon_ms,
                "first_token_ms": self.first_token_ms,
            },
        }
        if self.llm_slot is not None:
            d["llm_slot"] = {
                "lease_name": self.llm_slot.lease_name,
                "principal": self.llm_slot.principal,
                "reserved": self.llm_slot.reserved,
                "concurrency": self.llm_slot.concurrency,
                "priority_bypass": self.llm_slot.priority_bypass,
            }
        return d

    def yields_to(self, other_latency: LatencyClass) -> bool:

        if self.latency_class is not LatencyClass.REALTIME:
            return False
        return other_latency in (LatencyClass.DEFERRABLE, LatencyClass.FILLER)

def voice_lane_job(principal: str = "owner",
                   funding: str = "member-subsidized") -> RealtimeLaneJob:

    lease = LlmSlotReservation(
        lease_name=f"pn-llmd/voice/{principal}",
        principal=principal,
        reserved=True,
    )
    return RealtimeLaneJob(principal=principal, funding=funding, llm_slot=lease)
