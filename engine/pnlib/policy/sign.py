
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from relaylib import crypto
from pnlib import rootkey

from .model import Policy, PolicyModelError, canonical_bytes, policy_hash

def policy_signing_bytes(policy: Policy) -> bytes:

    return rootkey.domain_bind(rootkey.DOMAIN_POLICY, canonical_bytes(policy))

@dataclass(frozen=True)
class SignedPolicy:

    policy: Policy
    signature: bytes
    root_pubkey_id: str

    @property
    def version(self) -> int:
        return self.policy.version

    @property
    def prev_version_hash(self) -> Optional[str]:
        return self.policy.prev_version_hash

    def policy_hash(self) -> str:
        return policy_hash(self.policy)

    def to_dict(self) -> dict:
        return {
            "policy": self.policy.to_dict(),
            "signature": self.signature.hex(),
            "root_pubkey_id": self.root_pubkey_id,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def from_dict(d) -> "SignedPolicy":
        if not isinstance(d, dict):
            raise PolicyModelError("malformed signed policy")
        pol = Policy.from_dict(d.get("policy"))
        sig = d.get("signature")
        if not isinstance(sig, str):
            raise PolicyModelError("signed policy signature must be a hex string")
        try:
            sig_raw = bytes.fromhex(sig)
        except ValueError:
            raise PolicyModelError("signed policy signature is not valid hex")
        rid = d.get("root_pubkey_id")
        if not isinstance(rid, str) or not rid:
            raise PolicyModelError("signed policy missing root_pubkey_id")
        return SignedPolicy(policy=pol, signature=sig_raw, root_pubkey_id=rid)

    @staticmethod
    def from_json(s) -> "SignedPolicy":
        try:
            d = json.loads(s)
        except (ValueError, TypeError):
            raise PolicyModelError("signed policy is not valid JSON")
        return SignedPolicy.from_dict(d)

def sign_policy(*, owner_priv: bytes, owner_pub: bytes, policy: Policy) -> SignedPolicy:

    if not isinstance(owner_priv, (bytes, bytearray)) or len(owner_priv) != 32:
        raise PolicyModelError("owner_priv must be a 32-byte Ed25519 private key")
    if not isinstance(owner_pub, (bytes, bytearray)) or len(owner_pub) != 32:
        raise PolicyModelError("owner_pub must be a 32-byte Ed25519 public key")
    policy.validate()
    sig = crypto.ed_sign(bytes(owner_priv), policy_signing_bytes(policy))
    return SignedPolicy(policy=policy, signature=sig,
                        root_pubkey_id=rootkey.owner_fingerprint(pubkey=bytes(owner_pub)))

__all__ = ["SignedPolicy", "sign_policy", "policy_signing_bytes"]
