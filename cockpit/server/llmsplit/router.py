

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Protocol

from _brokerlib import (
    Decision,
    FundingTag,
    Pool,
    authorize_llm_call,
    parse as parse_funding,
    ERR_NO_SUBSIDY,
    BYO_EXHAUSTED_MSG,
)

class NoSubsidyError(Exception):

    def __init__(self, message: str = BYO_EXHAUSTED_MSG, code: str = ERR_NO_SUBSIDY):
        super().__init__(message)
        self.code = code
        self.message = message

class RoutingError(Exception):
    pass

class CentralPool(Protocol):

    def submit(self, *, principal: str, prompt: object) -> object:

        ...

class ByoSession(Protocol):

    @property
    def principal(self) -> str: ...

    def remaining_calls(self) -> int:

        ...

    def charge(self, n: int = 1) -> None:

        ...

    def submit(self, *, prompt: object) -> object:

        ...

class ByoSessionProvider(Protocol):

    def session_for(self, principal: str, tag: FundingTag) -> ByoSession:

        ...

@dataclass(frozen=True)
class RouteResult:

    pool: Pool
    principal: str
    completion: object

class LlmRouter:

    def __init__(
        self,
        *,
        central: CentralPool,
        byo_provider: ByoSessionProvider,
        central_has_capacity: Callable[[], bool] = lambda: True,
    ) -> None:
        self._central = central
        self._byo_provider = byo_provider

        self._central_has_capacity = central_has_capacity

    def route(
        self,
        *,
        principal: str,
        funding: object,
        prompt: object,
    ) -> RouteResult:

        tag = funding if isinstance(funding, FundingTag) else parse_funding(funding)

        if tag.is_member_subsidized:
            return self._route_member(principal=principal, prompt=prompt)
        return self._route_byo(principal=principal, tag=tag, prompt=prompt)

    def _route_member(self, *, principal: str, prompt: object) -> RouteResult:
        decision = authorize_llm_call(
            tag=_MEMBER_TAG,
            central_has_capacity=self._central_has_capacity(),
        )

        if not decision.granted or decision.pool is not Pool.CENTRAL:
            raise RoutingError(
                "member request did not resolve to a central grant — "
                "broker_rules invariant violated"
            )
        completion = self._central.submit(principal=principal, prompt=prompt)
        return RouteResult(pool=Pool.CENTRAL, principal=principal, completion=completion)

    def _route_byo(
        self, *, principal: str, tag: FundingTag, prompt: object
    ) -> RouteResult:

        try:
            session = self._byo_provider.session_for(principal, tag)
        except (KeyError, LookupError):
            raise NoSubsidyError()

        if session.principal != principal:
            raise RoutingError(
                f"byo provider returned session for {session.principal!r} "
                f"when routing for {principal!r} — cross-principal leak"
            )

        remaining = session.remaining_calls()
        decision: Decision = authorize_llm_call(
            tag=tag,
            byo_remaining_calls=remaining,

        )

        if not decision.granted:

            raise NoSubsidyError(message=decision.message or BYO_EXHAUSTED_MSG,
                                 code=decision.code or ERR_NO_SUBSIDY)

        if decision.pool is not Pool.BYO:

            raise RoutingError(
                "byo request resolved to a non-BYO grant — broker_rules "
                "invariant violated"
            )

        completion = session.submit(prompt=prompt)

        session.charge(1)
        return RouteResult(pool=Pool.BYO, principal=principal, completion=completion)

_MEMBER_TAG = parse_funding("member-subsidized")

