
from __future__ import annotations

import os
import base64
import hashlib
from dataclasses import dataclass
from typing import Callable, Optional

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives import serialization

RandSource = Callable[[int], bytes]

KEY_LEN = 32
_RAW = serialization.Encoding.Raw
_RAWPRIV = serialization.PrivateFormat.Raw
_RAWPUB = serialization.PublicFormat.Raw
_NOENC = serialization.NoEncryption()

class OverlayKeyError(Exception):
    pass

def b64(raw: bytes) -> str:
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != KEY_LEN:
        raise OverlayKeyError(f"key must be {KEY_LEN} raw bytes, got {len(raw) if raw is not None else None}")
    return base64.b64encode(bytes(raw)).decode("ascii")

def unb64(s: str) -> bytes:
    try:
        raw = base64.b64decode(s, validate=True)
    except Exception as e:
        raise OverlayKeyError(f"invalid base64 key: {type(e).__name__}")
    if len(raw) != KEY_LEN:
        raise OverlayKeyError(f"decoded key must be {KEY_LEN} bytes, got {len(raw)}")
    return raw

def derive_public(private_raw: bytes) -> bytes:

    if not isinstance(private_raw, (bytes, bytearray)) or len(private_raw) != KEY_LEN:
        raise OverlayKeyError(f"private key must be {KEY_LEN} raw bytes")
    try:
        sk = X25519PrivateKey.from_private_bytes(bytes(private_raw))
    except Exception as e:
        raise OverlayKeyError(f"invalid X25519 private key: {type(e).__name__}")
    return sk.public_key().public_bytes(_RAW, _RAWPUB)

def _fingerprint(raw: bytes) -> str:

    return hashlib.sha256(b"pn-overlay-fpr\x00" + raw).hexdigest()[:12]

@dataclass(frozen=True)
class WGKeypair:

    private: bytes
    public: bytes

    def __post_init__(self):
        if len(self.private) != KEY_LEN or len(self.public) != KEY_LEN:
            raise OverlayKeyError("WGKeypair requires 32-byte private and public")

    @property
    def private_b64(self) -> str:
        return b64(self.private)

    @property
    def public_b64(self) -> str:
        return b64(self.public)

    @property
    def secret_fpr(self) -> str:
        return _fingerprint(self.private)

    def verify_consistent(self) -> bool:

        return derive_public(self.private) == self.public

    def __repr__(self) -> str:
        return f"WGKeypair(public={self.public_b64!r}, private=<redacted fpr={self.secret_fpr}>)"

    __str__ = __repr__

    def public_only(self) -> "WGPublicKey":
        return WGPublicKey(self.public)

@dataclass(frozen=True)
class WGPublicKey:

    public: bytes

    def __post_init__(self):
        if len(self.public) != KEY_LEN:
            raise OverlayKeyError("WGPublicKey requires a 32-byte public key")

    @property
    def public_b64(self) -> str:
        return b64(self.public)

@dataclass(frozen=True)
class PresharedKey:

    raw: bytes

    def __post_init__(self):
        if len(self.raw) != KEY_LEN:
            raise OverlayKeyError("PresharedKey requires 32 bytes")

    @property
    def b64(self) -> str:
        return b64(self.raw)

    @property
    def fpr(self) -> str:
        return _fingerprint(self.raw)

    def __repr__(self) -> str:
        return f"PresharedKey(<redacted fpr={self.fpr}>)"

    __str__ = __repr__

def generate_keypair(rand: RandSource = os.urandom) -> WGKeypair:

    priv = rand(KEY_LEN)
    if not isinstance(priv, (bytes, bytearray)) or len(priv) != KEY_LEN:
        raise OverlayKeyError("random source must yield exactly 32 bytes")
    priv = bytes(priv)
    return WGKeypair(private=priv, public=derive_public(priv))

def generate_preshared(rand: RandSource = os.urandom) -> PresharedKey:

    raw = rand(KEY_LEN)
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != KEY_LEN:
        raise OverlayKeyError("random source must yield exactly 32 bytes")
    return PresharedKey(bytes(raw))

def keypair_from_private_b64(private_b64: str) -> WGKeypair:

    priv = unb64(private_b64)
    return WGKeypair(private=priv, public=derive_public(priv))

__all__ = [
    "WGKeypair", "WGPublicKey", "PresharedKey", "OverlayKeyError",
    "generate_keypair", "generate_preshared", "derive_public",
    "keypair_from_private_b64", "b64", "unb64", "KEY_LEN",
]
