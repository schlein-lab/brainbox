
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Optional

class CapTokError(Exception):
    pass

KIND_SCOPE = "scope"
KIND_MEMBER = "member"
KIND_NUM_LEQ = "num_leq"
KIND_TIME_LEQ = "time_leq"

_SET_KINDS = (KIND_SCOPE, KIND_MEMBER)
_NUM_KINDS = (KIND_NUM_LEQ, KIND_TIME_LEQ)
_ALL_KINDS = _SET_KINDS + _NUM_KINDS

EXP_DIM = "exp"

BLOCK_VERSION = 1
TYP_AUTHORITY = "authority"
TYP_ATTENUATION = "attenuation"

@dataclass(frozen=True)
class Caveat:

    kind: str
    dim: str
    values: tuple[str, ...] = ()
    bound: Optional[int] = None

    @staticmethod
    def scope(dim: str, values) -> "Caveat":
        return Caveat(KIND_SCOPE, dim, tuple(sorted({str(v) for v in values})), None)

    @staticmethod
    def member(dim: str, values) -> "Caveat":
        return Caveat(KIND_MEMBER, dim, tuple(sorted({str(v) for v in values})), None)

    @staticmethod
    def num_leq(dim: str, bound: int) -> "Caveat":
        return Caveat(KIND_NUM_LEQ, dim, (), int(bound))

    @staticmethod
    def time_leq(dim: str, bound: int) -> "Caveat":
        return Caveat(KIND_TIME_LEQ, dim, (), int(bound))

    def validate(self) -> "Caveat":
        if self.kind not in _ALL_KINDS:
            raise CapTokError(f"unknown caveat kind {self.kind!r}")
        if not isinstance(self.dim, str) or not self.dim:
            raise CapTokError("caveat dim must be a non-empty string")
        if self.kind in _SET_KINDS:
            if not isinstance(self.values, tuple) or any(not isinstance(v, str) for v in self.values):
                raise CapTokError(f"{self.kind} caveat needs a tuple[str] of values")
            if self.bound is not None:
                raise CapTokError(f"{self.kind} caveat must not carry a numeric bound")
        else:
            if not isinstance(self.bound, int) or isinstance(self.bound, bool):
                raise CapTokError(f"{self.kind} caveat needs an integer bound")
            if self.values:
                raise CapTokError(f"{self.kind} caveat must not carry a values set")
        return self

    def to_list(self) -> list:
        return [self.kind, self.dim, list(self.values), self.bound]

    @staticmethod
    def from_list(x) -> "Caveat":
        if not isinstance(x, list) or len(x) != 4:
            raise CapTokError("malformed caveat encoding")
        kind, dim, values, bound = x
        if not isinstance(values, list):
            raise CapTokError("malformed caveat values")
        return Caveat(kind, dim, tuple(values), bound).validate()

@dataclass(frozen=True)
class Block:

    body: dict
    next_pub: bytes
    sig: bytes

    @property
    def typ(self) -> str:
        return self.body.get("typ")

    @property
    def agent(self) -> Optional[str]:
        return self.body.get("agent")

    def caveats(self) -> tuple[Caveat, ...]:
        return tuple(Caveat.from_list(c) for c in self.body.get("caveats", []))

    def to_dict(self) -> dict:
        return {"body": self.body, "next_pub": self.next_pub.hex(), "sig": self.sig.hex()}

    @staticmethod
    def from_dict(d) -> "Block":
        if not isinstance(d, dict):
            raise CapTokError("malformed block")
        body = d.get("body")
        if not isinstance(body, dict):
            raise CapTokError("block body must be an object")
        return Block(body, _unhex(d.get("next_pub"), 32, "next_pub"),
                     _unhex(d.get("sig"), 64, "sig"))

@dataclass(frozen=True)
class DelegationChain:

    root_pubkey_id: str
    agents: tuple[str, ...]

    @property
    def depth(self) -> int:
        return max(0, len(self.agents) - 1)

@dataclass(frozen=True)
class CapToken:

    root_pubkey_id: str
    blocks: tuple[Block, ...]
    proof: bytes
    sealed: bool = False

    @property
    def authority(self) -> Block:
        if not self.blocks:
            raise CapTokError("token has no authority block")
        return self.blocks[0]

    @property
    def audience(self) -> Optional[str]:
        return self.authority.body.get("audience")

    @property
    def declared_depth(self) -> int:

        return max(0, len(self.blocks) - 1)

    def agent_chain(self) -> tuple[str, ...]:
        return tuple(b.agent for b in self.blocks)

    def delegation_chain(self) -> DelegationChain:
        return DelegationChain(self.root_pubkey_id, self.agent_chain())

    def to_dict(self) -> dict:
        return {
            "v": BLOCK_VERSION,
            "root_pubkey_id": self.root_pubkey_id,
            "blocks": [b.to_dict() for b in self.blocks],
            "proof": self.proof.hex(),
            "sealed": bool(self.sealed),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)

    @staticmethod
    def from_dict(d) -> "CapToken":
        if not isinstance(d, dict):
            raise CapTokError("malformed token")
        blocks = d.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            raise CapTokError("token must have at least one block")
        sealed = bool(d.get("sealed"))
        proof = _unhex(d.get("proof"), 64 if sealed else 32, "proof")
        rid = d.get("root_pubkey_id")
        if not isinstance(rid, str) or not rid:
            raise CapTokError("token missing root_pubkey_id")
        return CapToken(rid, tuple(Block.from_dict(b) for b in blocks), proof, sealed)

    @staticmethod
    def from_json(s) -> "CapToken":
        try:
            d = json.loads(s)
        except (ValueError, TypeError):
            raise CapTokError("token is not valid JSON")
        return CapToken.from_dict(d)

def canonical_body(body: dict) -> bytes:

    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")

def block_signing_bytes(body: dict, next_pub: bytes) -> bytes:

    if not isinstance(next_pub, (bytes, bytearray)) or len(next_pub) != 32:
        raise CapTokError("next_pub must be 32 bytes")
    return canonical_body(body) + b"|captok-next|" + bytes(next_pub)

def seal_transcript(blocks) -> bytes:

    out = bytearray(b"captok-seal|")
    for b in blocks:
        sb = block_signing_bytes(b.body, b.next_pub)
        out += len(sb).to_bytes(4, "big") + sb
        out += len(b.sig).to_bytes(2, "big") + bytes(b.sig)
    return bytes(out)

def _unhex(s, n: int, what: str) -> bytes:
    if not isinstance(s, str):
        raise CapTokError(f"{what} must be a hex string")
    try:
        raw = bytes.fromhex(s)
    except ValueError:
        raise CapTokError(f"{what} is not valid hex")
    if len(raw) != n:
        raise CapTokError(f"{what} has wrong length (need {n}, got {len(raw)})")
    return raw

def make_authority_body(*, agent: str, audience: Optional[str], max_depth: int,
                        caveats) -> dict:

    if not isinstance(agent, str) or not agent:
        raise CapTokError("authority agent must be a non-empty string")
    if audience is not None and (not isinstance(audience, str) or not audience):
        raise CapTokError("audience must be a non-empty string or None")
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 0:
        raise CapTokError("max_depth must be a non-negative integer")
    cavs = [c.validate().to_list() for c in caveats]
    return {"v": BLOCK_VERSION, "typ": TYP_AUTHORITY, "agent": agent,
            "audience": audience, "max_depth": max_depth, "caveats": cavs}

def make_attenuation_body(*, agent: str, max_depth: Optional[int], caveats) -> dict:

    if not isinstance(agent, str) or not agent:
        raise CapTokError("attenuation agent must be a non-empty string")
    if max_depth is not None and (not isinstance(max_depth, int) or isinstance(max_depth, bool)
                                  or max_depth < 0):
        raise CapTokError("max_depth must be a non-negative integer or None")
    cavs = [c.validate().to_list() for c in caveats]
    return {"v": BLOCK_VERSION, "typ": TYP_ATTENUATION, "agent": agent,
            "max_depth": max_depth, "caveats": cavs}

__all__ = [
    "CapTokError", "Caveat", "Block", "CapToken", "DelegationChain",
    "KIND_SCOPE", "KIND_MEMBER", "KIND_NUM_LEQ", "KIND_TIME_LEQ", "EXP_DIM",
    "TYP_AUTHORITY", "TYP_ATTENUATION", "BLOCK_VERSION",
    "canonical_body", "block_signing_bytes", "seal_transcript",
    "make_authority_body", "make_attenuation_body", "replace", "field",
]
