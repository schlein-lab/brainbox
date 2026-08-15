
from __future__ import annotations

import time as _time
from typing import Optional

from pnlib.a2a.card import (
    SignedCard, AgentCard, AgentCardError, issuer_id_for_pubkey, signature_ok,
)

class TrustError(Exception):
    pass

class A2ATrustStore:

    def __init__(self) -> None:
        self._issuers: dict[str, bytes] = {}

    def add_issuer(self, issuer_pub: bytes) -> str:
        if not isinstance(issuer_pub, (bytes, bytearray)) or len(issuer_pub) != 32:
            raise TrustError("issuer pubkey must be exactly 32 raw bytes")
        iid = issuer_id_for_pubkey(bytes(issuer_pub))
        self._issuers[iid] = bytes(issuer_pub)
        return iid

    def is_trusted_key(self, issuer_pub: bytes) -> bool:
        try:
            iid = issuer_id_for_pubkey(issuer_pub)
        except AgentCardError:
            return False
        stored = self._issuers.get(iid)
        return stored is not None and stored == bytes(issuer_pub)

    def is_trusted_id(self, issuer_id: str) -> bool:
        return issuer_id in self._issuers

def verify_card(signed: SignedCard, truststore: A2ATrustStore, *,
                audience: Optional[str] = None, now: Optional[int] = None) -> AgentCard:

    if not isinstance(signed, SignedCard):
        raise TrustError("expected a SignedCard")

    if not signature_ok(signed):
        raise TrustError("card signature is invalid (tampered, or issuer mismatch)")

    if not truststore.is_trusted_key(signed.issuer_pub):
        raise TrustError(
            f"card issuer {signed.card.issuer!r} is not a trusted issuer (handoff refused)")

    card = signed.card

    if card.not_after is not None:
        t = int(now if now is not None else _time.time())
        if t > int(card.not_after):
            raise TrustError(f"card expired (now {t} > not_after {card.not_after})")
    if card.audience is not None and audience is not None and card.audience != audience:
        raise TrustError(
            f"card audience mismatch: card is for {card.audience!r}, presented at {audience!r}")

    return card

def try_verify_card(signed: SignedCard, truststore: A2ATrustStore, **kw):

    try:
        return True, verify_card(signed, truststore, **kw)
    except TrustError as e:
        return False, e

__all__ = ["TrustError", "A2ATrustStore", "verify_card", "try_verify_card"]
