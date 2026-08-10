

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from tag import FundingKind, FundingTag

ERR_NO_SUBSIDY = "ERR_NO_SUBSIDY"
BYO_EXHAUSTED_MSG = "BYO capacity exhausted, provide more"

class Pool(enum.Enum):

    CENTRAL = "central"
    BYO = "byo"

@dataclass(frozen=True)
class Decision:

    granted: bool
    pool: Optional[Pool] = None
    code: Optional[str] = None
    message: str = ""

    @staticmethod
    def grant(pool: Pool) -> "Decision":
        return Decision(granted=True, pool=pool, code=None, message="")

    @staticmethod
    def deny(code: str, message: str) -> "Decision":
        return Decision(granted=False, pool=None, code=code, message=message)

def _no_subsidy() -> Decision:
    return Decision.deny(ERR_NO_SUBSIDY, BYO_EXHAUSTED_MSG)

def authorize_llm_call(
    *,
    tag: FundingTag,
    byo_remaining_calls: int = 0,
    central_has_capacity: bool = True,
) -> Decision:

    if tag.is_member_subsidized:

        return Decision.grant(Pool.CENTRAL)

    if not tag.brought_includes("llm"):

        return _no_subsidy()
    if byo_remaining_calls <= 0:

        return _no_subsidy()
    return Decision.grant(Pool.BYO)

def authorize_egress(
    *,
    tag: FundingTag,
    byo_egress_remaining_bytes: int = 0,
) -> Decision:

    if tag.is_member_subsidized:
        return Decision.grant(Pool.CENTRAL)
    if not tag.brought_includes("egress"):
        return _no_subsidy()
    if byo_egress_remaining_bytes <= 0:
        return _no_subsidy()
    return Decision.grant(Pool.BYO)

def central_vpn_lane_allowed(tag: FundingTag) -> Decision:

    if tag.is_member_subsidized:
        return Decision.grant(Pool.CENTRAL)

    return _no_subsidy()

def authorize(
    *,
    pool: Pool,
    tag: FundingTag,
    resource: str,
    byo_remaining: int = 0,
    central_has_capacity: bool = True,
) -> Decision:

    if pool is Pool.CENTRAL and tag.is_byo:
        return _no_subsidy()

    if resource == "llm":
        return authorize_llm_call(
            tag=tag,
            byo_remaining_calls=byo_remaining,
            central_has_capacity=central_has_capacity,
        )
    if resource == "egress":
        return authorize_egress(tag=tag, byo_egress_remaining_bytes=byo_remaining)
    if resource in ("vpn", "central_vpn_lane"):
        return central_vpn_lane_allowed(tag)

    if tag.is_member_subsidized and pool is Pool.CENTRAL:

        return _no_subsidy()
    return _no_subsidy()
