
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from pnlib.overlay.peer import PeerRegistry, Peer, PeerError
from pnlib.overlay.keys import WGKeypair, PresharedKey, generate_keypair, generate_preshared
import os

class RecoveryError(Exception):
    pass

@dataclass(frozen=True)
class RevocationRecord:

    peer_name: str
    old_public_key_b64: str
    revoked_allowed_ips: Tuple[str, ...]
    at: float

@dataclass
class RecoveryResult:
    peer: Peer
    new_keypair: WGKeypair
    revocation: RevocationRecord
    new_psk: Optional[PresharedKey] = None

def recover_peer(registry: PeerRegistry,
                 keystore: Dict[str, WGKeypair],
                 name: str,
                 rand=os.urandom,
                 rotate_psk: bool = False,
                 new_allowed_ips: Optional[List[str]] = None,
                 new_endpoint: Optional[str] = None,
                 update_endpoint: bool = False,
                 clock: Callable[[], float] = time.time) -> RecoveryResult:

    old = registry.get(name)
    old_pub = old.public_key_b64
    old_ips = old.allowed_ips

    new_kp = generate_keypair(rand)
    new_psk_obj: Optional[PresharedKey] = generate_preshared(rand) if rotate_psk else None
    new_psk_b64 = new_psk_obj.b64 if new_psk_obj is not None else None

    registry.rotate_peer(name, new_kp.public_b64, new_psk_b64=new_psk_b64)

    if new_allowed_ips is not None:
        registry.set_allowed_ips(name, new_allowed_ips)
    if update_endpoint:
        registry.set_endpoint(name, new_endpoint)

    keystore[name] = new_kp

    revoked_ips = old_ips if new_allowed_ips is not None else ()
    rec = RevocationRecord(
        peer_name=name,
        old_public_key_b64=old_pub,
        revoked_allowed_ips=tuple(revoked_ips),
        at=clock(),
    )
    return RecoveryResult(peer=registry.get(name), new_keypair=new_kp,
                          revocation=rec, new_psk=new_psk_obj)

__all__ = ["recover_peer", "RecoveryResult", "RevocationRecord", "RecoveryError"]
