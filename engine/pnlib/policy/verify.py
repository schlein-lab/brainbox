
from __future__ import annotations

from typing import List, Optional

from relaylib import crypto
from pnlib import rootkey

from .model import Policy, policy_hash
from .sign import SignedPolicy, policy_signing_bytes

class PolicyVerifyError(Exception):
    pass

def _resolve_owner_pubkey(owner_pubkey, signed: SignedPolicy, *, path, config) -> bytes:

    if owner_pubkey is None:
        try:
            owner_pubkey = rootkey.load_owner_pubkey(path, config=config)
        except rootkey.OwnerKeyError as e:
            raise PolicyVerifyError(f"owner key not available: {e}")
    if not isinstance(owner_pubkey, (bytes, bytearray)) or len(owner_pubkey) != 32:
        raise PolicyVerifyError("owner pubkey must be 32 raw bytes")
    owner_pubkey = bytes(owner_pubkey)
    fp = rootkey.owner_fingerprint(pubkey=owner_pubkey)
    if signed.root_pubkey_id != fp:
        raise PolicyVerifyError(
            f"policy is rooted at {signed.root_pubkey_id!r}, not the pinned owner {fp!r}")
    return owner_pubkey

def verify_signed_policy(signed, *, owner_pubkey: Optional[bytes] = None,
                         path: Optional[str] = None, config: Optional[dict] = None) -> Policy:

    try:
        if isinstance(signed, str):
            signed = SignedPolicy.from_json(signed)
        elif isinstance(signed, dict):
            signed = SignedPolicy.from_dict(signed)
    except Exception as e:
        raise PolicyVerifyError(f"malformed signed policy: {e}")
    if not isinstance(signed, SignedPolicy):
        raise PolicyVerifyError("expected a SignedPolicy / JSON string / dict")

    owner_pub = _resolve_owner_pubkey(owner_pubkey, signed, path=path, config=config)

    try:
        signed.policy.validate()
        msg = policy_signing_bytes(signed.policy)
    except Exception as e:
        raise PolicyVerifyError(f"policy is not well-formed: {e}")

    if not isinstance(signed.signature, (bytes, bytearray)) or len(signed.signature) != 64:
        raise PolicyVerifyError("policy signature must be 64 bytes")
    if not crypto.ed_verify(owner_pub, bytes(signed.signature), msg):
        raise PolicyVerifyError("policy signature is not the pinned owner's (unsigned/forged/tampered)")
    return signed.policy

def try_verify_signed_policy(signed, **kw):

    try:
        return True, verify_signed_policy(signed, **kw)
    except PolicyVerifyError as e:
        return False, e

def verify_chain(signed_list: List, *, owner_pubkey: Optional[bytes] = None,
                 path: Optional[str] = None, config: Optional[dict] = None) -> List[Policy]:

    if not isinstance(signed_list, (list, tuple)):
        raise PolicyVerifyError("verify_chain expects a list of signed policies")
    verified: List[Policy] = []
    prev_policy: Optional[Policy] = None
    prev_version: Optional[int] = None
    for i, s in enumerate(signed_list):
        pol = verify_signed_policy(s, owner_pubkey=owner_pubkey, path=path, config=config)
        if prev_policy is None:
            if pol.prev_version_hash is not None:
                raise PolicyVerifyError(
                    f"genesis policy (index 0) must have prev_version_hash=None, "
                    f"got {pol.prev_version_hash!r}")
        else:
            if pol.version <= prev_version:
                raise PolicyVerifyError(
                    f"version chain not strictly increasing at index {i}: "
                    f"{prev_version} -> {pol.version}")
            expect = policy_hash(prev_policy)
            if pol.prev_version_hash != expect:
                raise PolicyVerifyError(
                    f"broken version chain at index {i}: prev_version_hash "
                    f"{pol.prev_version_hash!r} != predecessor hash {expect!r} "
                    f"(rollback / reorder / splice rejected)")
        verified.append(pol)
        prev_policy = pol
        prev_version = pol.version
    return verified

__all__ = ["PolicyVerifyError", "verify_signed_policy", "try_verify_signed_policy", "verify_chain"]
