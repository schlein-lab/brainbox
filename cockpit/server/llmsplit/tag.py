

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import FrozenSet, Iterable

class FundingKind(enum.Enum):

    MEMBER_SUBSIDIZED = "member-subsidized"
    BYO = "byo"

KNOWN_RESOURCES: FrozenSet[str] = frozenset(
    {"llm", "vpn", "egress", "cpu", "mem", "cas"}
)

CENTRAL_ONLY_RESOURCES: FrozenSet[str] = frozenset({"central_vpn_lane"})

class FundingParseError(ValueError):
    pass

@dataclass(frozen=True)
class FundingTag:

    kind: FundingKind
    brought: FrozenSet[str]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, FundingKind):
            raise TypeError("kind must be a FundingKind")
        if self.kind is FundingKind.MEMBER_SUBSIDIZED and self.brought:

            raise ValueError("member-subsidized tag must have an empty brought set")
        bad = set(self.brought) - KNOWN_RESOURCES
        if bad:
            raise ValueError(f"unknown brought resources: {sorted(bad)}")

    @property
    def is_byo(self) -> bool:
        return self.kind is FundingKind.BYO

    @property
    def is_member_subsidized(self) -> bool:
        return self.kind is FundingKind.MEMBER_SUBSIDIZED

    def brought_includes(self, resource: str) -> bool:

        return resource in self.brought

    def to_wire(self) -> str:

        if self.kind is FundingKind.MEMBER_SUBSIDIZED:
            return FundingKind.MEMBER_SUBSIDIZED.value
        return "byo:" + ",".join(sorted(self.brought))

    def __str__(self) -> str:
        return self.to_wire()

FAIL_CLOSED_TAG = FundingTag(kind=FundingKind.BYO, brought=frozenset())

def _clean_resources(tokens: Iterable[str]) -> FrozenSet[str]:

    out = set()
    for t in tokens:
        t = t.strip().lower()
        if not t:
            continue
        if t in KNOWN_RESOURCES:
            out.add(t)

    return frozenset(out)

def parse(raw: object) -> FundingTag:

    if not isinstance(raw, str):
        return FAIL_CLOSED_TAG
    s = raw.strip()
    if s == FundingKind.MEMBER_SUBSIDIZED.value:
        return FundingTag(kind=FundingKind.MEMBER_SUBSIDIZED, brought=frozenset())

    low = s.lower()
    if low == "byo" or low.startswith("byo:"):
        rest = s[4:] if ":" in s else ""
        brought = _clean_resources(rest.split(","))
        return FundingTag(kind=FundingKind.BYO, brought=brought)

    return FAIL_CLOSED_TAG

def parse_strict(raw: object) -> FundingTag:

    if not isinstance(raw, str):
        raise FundingParseError(f"funding tag not a string: {type(raw)!r}")
    s = raw.strip()
    if s == FundingKind.MEMBER_SUBSIDIZED.value:
        return FundingTag(kind=FundingKind.MEMBER_SUBSIDIZED, brought=frozenset())
    low = s.lower()
    if low == "byo" or low.startswith("byo:"):
        rest = s[4:] if ":" in s else ""
        return FundingTag(kind=FundingKind.BYO, brought=_clean_resources(rest.split(",")))
    raise FundingParseError(f"unrecognized funding tag: {raw!r}")
