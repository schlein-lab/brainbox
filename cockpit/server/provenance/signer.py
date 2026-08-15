

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Protocol

class Signer(Protocol):
    def key_id(self) -> str: ...
    def sign(self, msg: bytes) -> bytes: ...
    def verify(self, msg: bytes, sig: bytes) -> bool: ...

class HmacSigner:

    ALG = "hmac-sha256"

    def __init__(self, key: bytes):
        if not isinstance(key, (bytes, bytearray)) or len(key) < 16:
            raise ValueError("HMAC ledger key must be >= 16 random bytes")
        self._key = bytes(key)

        self._kid = "hmac:" + hashlib.sha256(self._key).hexdigest()[:16]

    @classmethod
    def generate(cls) -> "HmacSigner":
        return cls(os.urandom(32))

    def key_id(self) -> str:
        return self._kid

    def sign(self, msg: bytes) -> bytes:
        return hmac.new(self._key, msg, hashlib.sha256).digest()

    def verify(self, msg: bytes, sig: bytes) -> bool:
        expected = hmac.new(self._key, msg, hashlib.sha256).digest()

        return hmac.compare_digest(expected, sig)

class Ed25519Signer:

    ALG = "ed25519"

    def __init__(self, seed: bytes | None = None, backend=None):
        if backend is None:
            backend = _pick_ed25519_backend()
        self._b = backend
        self._sk, self._pk = self._b.keypair(seed or os.urandom(32))
        self._kid = "ed25519:" + hashlib.sha256(self._pk).hexdigest()[:16]

    @classmethod
    def generate(cls) -> "Ed25519Signer":
        return cls(os.urandom(32))

    def public_key(self) -> bytes:
        return self._pk

    def key_id(self) -> str:
        return self._kid

    def sign(self, msg: bytes) -> bytes:
        return self._b.sign(self._sk, self._pk, msg)

    def verify(self, msg: bytes, sig: bytes) -> bool:
        try:
            return self._b.verify(self._pk, msg, sig)
        except Exception:
            return False

def _pick_ed25519_backend():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        return _CryptographyEd25519()
    except Exception:
        import _ed25519_pure

        return _ed25519_pure.PureEd25519()

class _CryptographyEd25519:
    def keypair(self, seed: bytes):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        sk = Ed25519PrivateKey.from_private_bytes(seed[:32])
        raw_sk = sk.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        raw_pk = sk.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return raw_sk, raw_pk

    def sign(self, sk: bytes, pk: bytes, msg: bytes) -> bytes:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        return Ed25519PrivateKey.from_private_bytes(sk).sign(msg)

    def verify(self, pk: bytes, msg: bytes, sig: bytes) -> bool:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        try:
            Ed25519PublicKey.from_public_bytes(pk).verify(sig, msg)
            return True
        except InvalidSignature:
            return False

def make_signer(alg: str = "hmac-sha256", *, key: bytes | None = None) -> Signer:

    if alg in ("hmac-sha256", "hmac"):
        return HmacSigner(key) if key is not None else HmacSigner.generate()
    if alg == "ed25519":
        return Ed25519Signer(seed=key)
    raise ValueError(f"unknown signer alg: {alg!r}")
