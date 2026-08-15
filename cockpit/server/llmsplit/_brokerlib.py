

from __future__ import annotations

_HAVE_PHASE6 = False
try:
    import broker_rules as _br
    import tag as _tag

    FundingTag = _tag.FundingTag
    FundingKind = _tag.FundingKind
    parse = _tag.parse
    Pool = _br.Pool
    Decision = _br.Decision
    authorize_llm_call = _br.authorize_llm_call
    ERR_NO_SUBSIDY = _br.ERR_NO_SUBSIDY
    BYO_EXHAUSTED_MSG = _br.BYO_EXHAUSTED_MSG
    _HAVE_PHASE6 = True
except Exception:
    _HAVE_PHASE6 = False

if not _HAVE_PHASE6:

    import enum
    from dataclasses import dataclass
    from typing import FrozenSet, Optional

    ERR_NO_SUBSIDY = "ERR_NO_SUBSIDY"
    BYO_EXHAUSTED_MSG = "BYO capacity exhausted, provide more"

    KNOWN_RESOURCES: FrozenSet[str] = frozenset(
        {"llm", "vpn", "egress", "cpu", "mem", "cas"}
    )

    class FundingKind(enum.Enum):
        MEMBER_SUBSIDIZED = "member-subsidized"
        BYO = "byo"

    @dataclass(frozen=True)
    class FundingTag:
        kind: FundingKind
        brought: FrozenSet[str]

        def __post_init__(self) -> None:
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

    _FAIL_CLOSED_TAG = FundingTag(kind=FundingKind.BYO, brought=frozenset())

    def _clean(tokens) -> FrozenSet[str]:
        out = set()
        for t in tokens:
            t = t.strip().lower()
            if t and t in KNOWN_RESOURCES:
                out.add(t)
        return frozenset(out)

    def parse(raw: object) -> FundingTag:
        if not isinstance(raw, str):
            return _FAIL_CLOSED_TAG
        s = raw.strip()
        if s == FundingKind.MEMBER_SUBSIDIZED.value:
            return FundingTag(kind=FundingKind.MEMBER_SUBSIDIZED, brought=frozenset())
        low = s.lower()
        if low == "byo" or low.startswith("byo:"):
            rest = s[4:] if ":" in s else ""
            return FundingTag(kind=FundingKind.BYO, brought=_clean(rest.split(",")))
        return _FAIL_CLOSED_TAG

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
