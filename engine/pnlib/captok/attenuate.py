
from __future__ import annotations

from typing import Optional

from relaylib import crypto
from pnlib import rootkey

from .model import (
    CapToken, Block, Caveat, CapTokError,
    make_attenuation_body, block_signing_bytes, seal_transcript,
)
from .verify import (
    fold_caveats, _effective_max_depth,
    CAPTOK_BLOCK_DOMAIN, CAPTOK_SEAL_DOMAIN,
)

def attenuate(token: CapToken, *, agent: str, caveats=(),
              max_redelegation_depth: Optional[int] = None) -> CapToken:

    if not isinstance(token, CapToken):
        raise CapTokError("attenuate expects a CapToken")
    if token.sealed:
        raise CapTokError("cannot attenuate a sealed token")
    if not isinstance(token.proof, (bytes, bytearray)) or len(token.proof) != 32:
        raise CapTokError("unsealed token proof must be the 32-byte ratchet private key")

    cavs = [c.validate() for c in caveats]

    st = fold_caveats(token.blocks)
    for c in cavs:
        st.apply(c)

    ceiling = _effective_max_depth(token.blocks)
    if max_redelegation_depth is not None:
        if (not isinstance(max_redelegation_depth, int) or isinstance(max_redelegation_depth, bool)
                or max_redelegation_depth < 0):
            raise CapTokError("max_redelegation_depth must be a non-negative integer or None")
        if max_redelegation_depth > ceiling:
            raise CapTokError(
                f"cannot raise max_depth {ceiling} -> {max_redelegation_depth}")
        ceiling = max_redelegation_depth
    new_depth = token.declared_depth + 1
    if new_depth > ceiling:
        raise CapTokError(f"attenuation would exceed the depth ceiling {ceiling} (hop {new_depth})")

    return _append_block(token, agent=agent, caveats=cavs,
                         max_depth=max_redelegation_depth)

def seal(token: CapToken) -> CapToken:

    if not isinstance(token, CapToken):
        raise CapTokError("seal expects a CapToken")
    if token.sealed:
        return token
    if not isinstance(token.proof, (bytes, bytearray)) or len(token.proof) != 32:
        raise CapTokError("unsealed token proof must be the 32-byte ratchet private key")
    sig = crypto.ed_sign(bytes(token.proof),
                         rootkey.domain_bind(CAPTOK_SEAL_DOMAIN, seal_transcript(token.blocks)))
    return CapToken(root_pubkey_id=token.root_pubkey_id, blocks=token.blocks,
                    proof=sig, sealed=True)

def _append_block(token: CapToken, *, agent: str, caveats, max_depth) -> CapToken:

    body = make_attenuation_body(agent=agent, max_depth=max_depth, caveats=caveats)
    new_priv, new_pub = crypto.gen_ed25519()
    msg = block_signing_bytes(body, new_pub)
    sig = crypto.ed_sign(bytes(token.proof), rootkey.domain_bind(CAPTOK_BLOCK_DOMAIN, msg))
    block = Block(body=body, next_pub=new_pub, sig=sig)
    return CapToken(root_pubkey_id=token.root_pubkey_id,
                    blocks=token.blocks + (block,), proof=new_priv, sealed=False)

def _attenuate_unchecked(token: CapToken, *, agent: str, caveats=(),
                         max_redelegation_depth: Optional[int] = None) -> CapToken:

    if token.sealed or not isinstance(token.proof, (bytes, bytearray)) or len(token.proof) != 32:
        raise CapTokError("can only extend an unsealed token")
    return _append_block(token, agent=agent, caveats=[c.validate() for c in caveats],
                         max_depth=max_redelegation_depth)

__all__ = ["attenuate", "seal"]
