
from __future__ import annotations

from typing import Optional

from relaylib import crypto
from pnlib import rootkey

from .model import (
    CapToken, Block, Caveat, CapTokError, EXP_DIM,
    make_authority_body, block_signing_bytes,
)

def mint(*, owner_priv: bytes, owner_pub: bytes, agent: str,
         audience: Optional[str], max_redelegation_depth: int,
         exp: Optional[int] = None, caveats=()) -> CapToken:

    if not isinstance(owner_priv, (bytes, bytearray)) or len(owner_priv) != 32:
        raise CapTokError("owner_priv must be a 32-byte Ed25519 private key")
    if not isinstance(owner_pub, (bytes, bytearray)) or len(owner_pub) != 32:
        raise CapTokError("owner_pub must be a 32-byte Ed25519 public key")

    cavs = [c.validate() for c in caveats]
    if exp is not None:
        if not isinstance(exp, int) or isinstance(exp, bool):
            raise CapTokError("exp must be an integer unix timestamp")

        cavs = [c for c in cavs if not (c.kind == "time_leq" and c.dim == EXP_DIM)]
        cavs.append(Caveat.time_leq(EXP_DIM, exp))

    body = make_authority_body(agent=agent, audience=audience,
                               max_depth=max_redelegation_depth, caveats=cavs)

    next_priv, next_pub = crypto.gen_ed25519()

    msg = block_signing_bytes(body, next_pub)

    sig = crypto.ed_sign(bytes(owner_priv), rootkey.domain_bind(rootkey.DOMAIN_MINT, msg))

    block = Block(body=body, next_pub=next_pub, sig=sig)
    return CapToken(
        root_pubkey_id=rootkey.owner_fingerprint(pubkey=bytes(owner_pub)),
        blocks=(block,),
        proof=next_priv,
        sealed=False,
    )

__all__ = ["mint"]
