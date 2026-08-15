

from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .canonical import (
    ED25519_PUBLIC_LEN,
    ED25519_SIG_LEN,
    b64url_decode,
    b64url_encode,
    key_id_from_pubkey,
)

class SigningKey:

    def __init__(self, priv: Ed25519PrivateKey):
        self._priv = priv

    @classmethod
    def generate(cls) -> "SigningKey":
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_raw(cls, raw32: bytes) -> "SigningKey":
        return cls(Ed25519PrivateKey.from_private_bytes(raw32))

    def raw_private(self) -> bytes:
        from cryptography.hazmat.primitives import serialization

        return self._priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def raw_public(self) -> bytes:

        return self._priv.public_key().public_bytes_raw()

    def pubkey_b64url(self) -> str:

        return b64url_encode(self.raw_public())

    def key_id(self) -> str:
        return key_id_from_pubkey(self.raw_public())

    def sign(self, message: bytes) -> bytes:

        return self._priv.sign(message)

    def sign_b64url(self, message: bytes) -> str:

        return b64url_encode(self.sign(message))

def load_public_from_b64url(pubkey_b64url: str) -> bytes:

    raw = b64url_decode(pubkey_b64url)
    if len(raw) != ED25519_PUBLIC_LEN:
        raise ValueError(
            f"ed25519 public key must be {ED25519_PUBLIC_LEN} bytes, got {len(raw)}"
        )

    Ed25519PublicKey.from_public_bytes(raw)
    return raw

def verify_detached(raw_pubkey: bytes, signature: bytes, message: bytes) -> bool:

    if len(raw_pubkey) != ED25519_PUBLIC_LEN:
        raise ValueError("bad public key length")
    if len(signature) != ED25519_SIG_LEN:
        return False
    pub = Ed25519PublicKey.from_public_bytes(raw_pubkey)
    try:
        pub.verify(signature, message)
        return True
    except InvalidSignature:
        return False

def verify_detached_b64url(raw_pubkey: bytes, sig_b64url: str, message: bytes) -> bool:
    try:
        sig = b64url_decode(sig_b64url)
    except Exception:
        return False
    return verify_detached(raw_pubkey, sig, message)
