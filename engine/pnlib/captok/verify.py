
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from typing import Optional, Mapping

from relaylib import crypto
from pnlib import rootkey

from .model import (
    CapToken, Block, Caveat, CapTokError, DelegationChain,
    KIND_SCOPE, KIND_MEMBER, KIND_NUM_LEQ, KIND_TIME_LEQ, EXP_DIM,
    TYP_AUTHORITY, TYP_ATTENUATION, _SET_KINDS, _NUM_KINDS,
    block_signing_bytes, seal_transcript,
)

CAPTOK_BLOCK_DOMAIN = b"brainarbeit/captok/block/1"
CAPTOK_SEAL_DOMAIN = b"brainarbeit/captok/seal/1"
PROOF_CHALLENGE = b"brainarbeit/captok/proof-of-possession/1"

@dataclass(frozen=True)
class VerifiedGrant:

    root_pubkey_id: str
    delegation: DelegationChain
    scopes: Mapping[str, frozenset]
    members: Mapping[str, frozenset]
    num_caps: Mapping[str, int]
    time_caps: Mapping[str, int]
    exp: Optional[int]
    audience: Optional[str]
    max_depth: int
    depth: int

    @property
    def agent(self) -> Optional[str]:

        return self.delegation.agents[-1] if self.delegation.agents else None

def _valid_agent(a) -> bool:

    return isinstance(a, str) and bool(a)

class _FoldState:

    def __init__(self):
        self.sets: dict[tuple[str, str], frozenset] = {}
        self.nums: dict[tuple[str, str], int] = {}

    def apply(self, cav: Caveat):
        cav.validate()
        if cav.kind in _SET_KINDS:
            key = (cav.kind, cav.dim)
            new = frozenset(cav.values)
            if key in self.sets:
                prev = self.sets[key]
                if not new <= prev:
                    raise CapTokError(
                        f"widening rejected: {cav.kind} on {cav.dim!r} adds "
                        f"{sorted(new - prev)} not permitted by the parent {sorted(prev)}")
                self.sets[key] = new
            else:
                self.sets[key] = new
        else:
            key = (cav.kind, cav.dim)
            b = cav.bound
            if key in self.nums:
                if b > self.nums[key]:
                    raise CapTokError(
                        f"widening rejected: {cav.kind} on {cav.dim!r} raises the bound "
                        f"{self.nums[key]} -> {b}")
                self.nums[key] = b
            else:
                self.nums[key] = b

    def apply_block(self, block: Block):
        for cav in block.caveats():
            self.apply(cav)

def fold_caveats(blocks) -> _FoldState:

    st = _FoldState()
    for b in blocks:
        st.apply_block(b)
    return st

def _resolve_owner_pubkey(owner_pubkey, token: CapToken, *, path, config) -> bytes:

    if owner_pubkey is None:
        owner_pubkey = rootkey.load_owner_pubkey(path, config=config)
    if not isinstance(owner_pubkey, (bytes, bytearray)) or len(owner_pubkey) != 32:
        raise CapTokError("owner pubkey must be 32 raw bytes")
    owner_pubkey = bytes(owner_pubkey)
    fp = rootkey.owner_fingerprint(pubkey=owner_pubkey)
    if token.root_pubkey_id != fp:
        raise CapTokError(
            f"token is rooted at {token.root_pubkey_id!r}, not the pinned owner {fp!r}")
    return owner_pubkey

def verify(token, *, owner_pubkey: Optional[bytes] = None, audience: Optional[str] = None,
           now: Optional[int] = None, path: Optional[str] = None,
           config: Optional[dict] = None) -> VerifiedGrant:

    if isinstance(token, str):
        token = CapToken.from_json(token)
    elif isinstance(token, dict):
        token = CapToken.from_dict(token)
    if not isinstance(token, CapToken):
        raise CapTokError("token must be a CapToken / JSON string / dict")
    if not token.blocks:
        raise CapTokError("token has no blocks")

    owner_pub = _resolve_owner_pubkey(owner_pubkey, token, path=path, config=config)

    auth = token.blocks[0]
    if auth.body.get("typ") != TYP_AUTHORITY:
        raise CapTokError("block 0 is not an authority block")
    if not _valid_agent(auth.body.get("agent")):
        raise CapTokError("authority block agent must be a non-empty string")
    msg0 = block_signing_bytes(auth.body, auth.next_pub)
    if not crypto.ed_verify(owner_pub, auth.sig, rootkey.domain_bind(rootkey.DOMAIN_MINT, msg0)):
        raise CapTokError("authority block signature is not the pinned owner's (root check failed)")

    prev_next_pub = auth.next_pub
    for i, blk in enumerate(token.blocks[1:], start=1):
        if blk.body.get("typ") != TYP_ATTENUATION:
            raise CapTokError(f"block {i} is not an attenuation block")
        if not _valid_agent(blk.body.get("agent")):
            raise CapTokError(f"block {i} agent must be a non-empty string")
        msg = block_signing_bytes(blk.body, blk.next_pub)
        if not crypto.ed_verify(prev_next_pub, blk.sig,
                                rootkey.domain_bind(CAPTOK_BLOCK_DOMAIN, msg)):
            raise CapTokError(f"block {i} signature does not chain to its predecessor (tampered "
                              f"/ reordered / spliced)")
        prev_next_pub = blk.next_pub

    _verify_proof(token, prev_next_pub)

    st = fold_caveats(token.blocks)

    eff_max_depth = _effective_max_depth(token.blocks)
    depth = token.declared_depth
    if depth > eff_max_depth:
        raise CapTokError(f"re-delegation depth {depth} exceeds the permitted ceiling {eff_max_depth}")

    tok_aud = auth.body.get("audience")
    if tok_aud is not None and audience != tok_aud:
        raise CapTokError(f"audience mismatch: token is bound to {tok_aud!r}, presented at {audience!r}")

    scopes = {dim: s for (k, dim), s in st.sets.items() if k == KIND_SCOPE}
    members = {dim: s for (k, dim), s in st.sets.items() if k == KIND_MEMBER}
    num_caps = {dim: v for (k, dim), v in st.nums.items() if k == KIND_NUM_LEQ}
    time_caps = {dim: v for (k, dim), v in st.nums.items() if k == KIND_TIME_LEQ}

    exp = time_caps.get(EXP_DIM)
    if exp is not None:
        t = int(now) if now is not None else int(_time.time())
        if t > exp:
            raise CapTokError(f"token expired (now {t} > exp {exp})")

    return VerifiedGrant(
        root_pubkey_id=token.root_pubkey_id,
        delegation=token.delegation_chain(),
        scopes=scopes, members=members, num_caps=num_caps, time_caps=time_caps,
        exp=exp, audience=tok_aud, max_depth=eff_max_depth, depth=depth,
    )

def try_verify(token, **kw):

    try:
        return True, verify(token, **kw)
    except CapTokError as e:
        return False, e

def _verify_proof(token: CapToken, last_next_pub: bytes):

    if token.sealed:
        if not crypto.ed_verify(last_next_pub, token.proof,
                                rootkey.domain_bind(CAPTOK_SEAL_DOMAIN, seal_transcript(token.blocks))):
            raise CapTokError("seal signature invalid (tampered or truncated)")
        return

    if not isinstance(token.proof, (bytes, bytearray)) or len(token.proof) != 32:
        raise CapTokError("unsealed proof must be a 32-byte private key")
    try:
        sig = crypto.ed_sign(bytes(token.proof), PROOF_CHALLENGE)
    except Exception:
        raise CapTokError("unsealed proof is not a valid private key")
    if not crypto.ed_verify(last_next_pub, sig, PROOF_CHALLENGE):
        raise CapTokError("proof does not match the last announced key (truncated / forged tail)")

def _effective_max_depth(blocks) -> int:

    auth = blocks[0]
    md = auth.body.get("max_depth")
    if not isinstance(md, int) or isinstance(md, bool) or md < 0:
        raise CapTokError("authority max_depth is malformed")
    for i, blk in enumerate(blocks[1:], start=1):
        bmd = blk.body.get("max_depth")
        if bmd is None:
            continue
        if not isinstance(bmd, int) or isinstance(bmd, bool) or bmd < 0:
            raise CapTokError(f"block {i} max_depth is malformed")
        if bmd > md:
            raise CapTokError(f"widening rejected: block {i} raises max_depth {md} -> {bmd}")
        md = bmd
    return md

__all__ = ["VerifiedGrant", "verify", "try_verify", "fold_caveats",
           "CAPTOK_BLOCK_DOMAIN", "CAPTOK_SEAL_DOMAIN", "PROOF_CHALLENGE"]
