
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from pnlib.origin import Origin, OriginKind
from pnlib.a2a.card import SignedCard, AgentCard
from pnlib.a2a.trust import A2ATrustStore, verify_card, TrustError

class HandoffRefused(Exception):
    pass

@dataclass(frozen=True)
class HandoffResult:

    peer_agent_id: str
    response: object
    provenance: Origin

class LocalMockPeer:

    def __init__(self, signed_card: SignedCard, handler: Callable[[object], object]) -> None:
        self.signed_card = signed_card
        self._handler = handler

    @property
    def card(self) -> AgentCard:
        return self.signed_card.card

    def handle(self, task) -> object:
        return self._handler(task)

def handoff(peer: LocalMockPeer, task, *, truststore: A2ATrustStore,
            audience: Optional[str] = None, component: str = "pn-a2a",
            now: Optional[int] = None) -> HandoffResult:

    signed = getattr(peer, "signed_card", None)
    if not isinstance(signed, SignedCard):
        raise HandoffRefused("peer exposes no signed card")
    try:
        card = verify_card(signed, truststore, audience=audience, now=now)
    except TrustError as e:
        raise HandoffRefused(f"peer card not trusted: {e}")

    response = peer.handle(task)

    provenance = Origin.agent(component, agent_id=card.agent_id)

    assert provenance.kind == OriginKind.AGENT
    return HandoffResult(peer_agent_id=card.agent_id, response=response, provenance=provenance)

__all__ = ["HandoffRefused", "HandoffResult", "LocalMockPeer", "handoff"]
