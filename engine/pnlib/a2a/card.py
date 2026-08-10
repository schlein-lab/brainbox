
from __future__ import annotations

import hashlib
import json
import time as _time
from dataclasses import dataclass, field
from typing import Optional

from relaylib import crypto
from pnlib import rootkey

class AgentCardError(Exception):
    pass

A2A_CARD_DOMAIN = b"brainarbeit/a2a/card/1"
_ISSUER_FP_DOMAIN = b"brainarbeit/a2a/issuer/1"

def issuer_id_for_pubkey(pubkey: bytes) -> str:

    if not isinstance(pubkey, (bytes, bytearray)) or len(pubkey) != 32:
        raise AgentCardError("issuer pubkey must be exactly 32 raw bytes")
    h = hashlib.blake2s(_ISSUER_FP_DOMAIN + bytes(pubkey), digest_size=16).hexdigest()
    return "issuer:b2:" + h

@dataclass(frozen=True)
class AgentCard:

    agent_id: str
    name: str
    capabilities: tuple[str, ...]
    issuer: str
    audience: Optional[str] = None
    not_after: Optional[int] = None
    issued_at: Optional[int] = None

    def canonical_bytes(self) -> bytes:

        body = {
            "agent_id": self.agent_id,
            "name": self.name,
            "capabilities": list(self.capabilities),
            "issuer": self.issuer,
            "audience": self.audience,
            "not_after": self.not_after,
            "issued_at": self.issued_at,
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id, "name": self.name,
            "capabilities": list(self.capabilities), "issuer": self.issuer,
            "audience": self.audience, "not_after": self.not_after,
            "issued_at": self.issued_at,
        }

    @staticmethod
    def from_dict(d) -> "AgentCard":
        if not isinstance(d, dict):
            raise AgentCardError("card must be an object")
        try:
            return AgentCard(
                agent_id=d["agent_id"], name=d["name"],
                capabilities=tuple(d.get("capabilities", ())),
                issuer=d["issuer"], audience=d.get("audience"),
                not_after=d.get("not_after"), issued_at=d.get("issued_at"))
        except KeyError as e:
            raise AgentCardError(f"card missing field {e}")

@dataclass(frozen=True)
class SignedCard:

    card: AgentCard
    issuer_pub: bytes
    sig: bytes

    def to_dict(self) -> dict:
        return {"card": self.card.to_dict(), "issuer_pub": self.issuer_pub.hex(),
                "sig": self.sig.hex()}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def from_dict(d) -> "SignedCard":
        if not isinstance(d, dict):
            raise AgentCardError("signed card must be an object")
        try:
            issuer_pub = bytes.fromhex(d["issuer_pub"])
            sig = bytes.fromhex(d["sig"])
        except (KeyError, ValueError):
            raise AgentCardError("signed card has malformed issuer_pub / sig")
        if len(issuer_pub) != 32:
            raise AgentCardError("issuer_pub must be 32 bytes")
        if len(sig) != 64:
            raise AgentCardError("sig must be 64 bytes")
        return SignedCard(AgentCard.from_dict(d.get("card")), issuer_pub, sig)

    @staticmethod
    def from_json(s) -> "SignedCard":
        try:
            return SignedCard.from_dict(json.loads(s))
        except (ValueError, TypeError):
            raise AgentCardError("signed card is not valid JSON")

def make_card(*, agent_id: str, name: str, capabilities, issuer_pub: bytes,
              audience: Optional[str] = None, ttl: Optional[int] = None,
              now: Optional[int] = None) -> AgentCard:

    t = int(now if now is not None else _time.time())
    not_after = (t + int(ttl)) if ttl is not None else None
    return AgentCard(
        agent_id=agent_id, name=name, capabilities=tuple(capabilities),
        issuer=issuer_id_for_pubkey(issuer_pub), audience=audience,
        not_after=not_after, issued_at=t)

def sign_card(card: AgentCard, *, issuer_priv: bytes, issuer_pub: bytes) -> SignedCard:

    if not isinstance(issuer_priv, (bytes, bytearray)) or len(issuer_priv) != 32:
        raise AgentCardError("issuer_priv must be a 32-byte Ed25519 private key")
    if not isinstance(issuer_pub, (bytes, bytearray)) or len(issuer_pub) != 32:
        raise AgentCardError("issuer_pub must be a 32-byte Ed25519 public key")
    if card.issuer != issuer_id_for_pubkey(issuer_pub):
        raise AgentCardError("card issuer fingerprint does not match the signing key")
    sig = crypto.ed_sign(bytes(issuer_priv),
                         rootkey.domain_bind(A2A_CARD_DOMAIN, card.canonical_bytes()))
    return SignedCard(card=card, issuer_pub=bytes(issuer_pub), sig=sig)

def signature_ok(signed: SignedCard) -> bool:

    if not isinstance(signed, SignedCard):
        return False
    if signed.card.issuer != issuer_id_for_pubkey(signed.issuer_pub):
        return False
    return crypto.ed_verify(bytes(signed.issuer_pub), bytes(signed.sig),
                            rootkey.domain_bind(A2A_CARD_DOMAIN, signed.card.canonical_bytes()))

__all__ = [
    "AgentCardError", "A2A_CARD_DOMAIN", "issuer_id_for_pubkey",
    "AgentCard", "SignedCard", "make_card", "sign_card", "signature_ok",
]
