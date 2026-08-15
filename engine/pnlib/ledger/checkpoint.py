
from __future__ import annotations

import hashlib
import json
import os
import struct
import time
from dataclasses import dataclass
from typing import Optional

from relaylib import crypto

STH_DOMAIN = b"brainarbeit/ledger/sth/1"

_ED_PUB_LEN = 32
_ED_PRIV_LEN = 32
_ROOT_LEN = 32

class LedgerKeyError(Exception):
    pass

def sth_signing_bytes(tree_size: int, root_hash: bytes, timestamp: float) -> bytes:

    if not isinstance(root_hash, (bytes, bytearray)) or len(root_hash) != _ROOT_LEN:
        raise LedgerKeyError("root_hash must be a 32-byte SHA-256 digest")
    if tree_size < 0:
        raise LedgerKeyError("tree_size must be non-negative")
    return (STH_DOMAIN
            + struct.pack(">Q", int(tree_size))
            + bytes(root_hash)
            + struct.pack(">d", float(timestamp)))

@dataclass(frozen=True)
class STH:

    tree_size: int
    root_hash: bytes
    timestamp: float
    signature: bytes
    log_id: str

    @property
    def root_hash_hex(self) -> str:
        return self.root_hash.hex()

    @property
    def signature_hex(self) -> str:
        return self.signature.hex()

    def to_dict(self) -> dict:
        return {
            "tree_size": self.tree_size,
            "root_hash": self.root_hash.hex(),
            "timestamp": self.timestamp,
            "signature": self.signature.hex(),
            "log_id": self.log_id,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict) -> "STH":
        return cls(
            tree_size=int(d["tree_size"]),
            root_hash=bytes.fromhex(d["root_hash"]),
            timestamp=float(d["timestamp"]),
            signature=bytes.fromhex(d["signature"]),
            log_id=str(d["log_id"]),
        )

    @classmethod
    def from_json(cls, s: str) -> "STH":
        return cls.from_dict(json.loads(s))

def log_id_for_pubkey(pub_raw: bytes) -> str:

    if not isinstance(pub_raw, (bytes, bytearray)) or len(pub_raw) != _ED_PUB_LEN:
        raise LedgerKeyError("ledger public key must be 32 raw bytes")
    return hashlib.sha256(bytes(pub_raw)).hexdigest()

class LedgerSigner:

    def __init__(self, priv_raw: bytes, pub_raw: bytes) -> None:
        if not (isinstance(priv_raw, (bytes, bytearray)) and len(priv_raw) == _ED_PRIV_LEN):
            raise LedgerKeyError("ledger private key must be 32 raw bytes")
        if not (isinstance(pub_raw, (bytes, bytearray)) and len(pub_raw) == _ED_PUB_LEN):
            raise LedgerKeyError("ledger public key must be 32 raw bytes")
        self._priv = bytes(priv_raw)
        self._pub = bytes(pub_raw)

    @classmethod
    def generate(cls) -> "LedgerSigner":
        priv, pub = crypto.gen_ed25519()
        return cls(priv, pub)

    @property
    def public_key(self) -> bytes:
        return self._pub

    @property
    def public_key_hex(self) -> str:
        return self._pub.hex()

    @property
    def log_id(self) -> str:
        return log_id_for_pubkey(self._pub)

    def save_private(self, path: str, cipher=None) -> None:

        blob = self._priv if cipher is None else cipher.encrypt(self._priv)
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, blob)
        finally:
            os.close(fd)

        with open(path + ".pub", "w") as f:
            f.write(self._pub.hex())

    @classmethod
    def load_private(cls, path: str, cipher=None) -> "LedgerSigner":

        if not os.path.exists(path):
            raise LedgerKeyError(f"ledger private key not found at {path}")
        with open(path, "rb") as f:
            blob = f.read()
        priv = blob if cipher is None else cipher.decrypt(blob)
        if len(priv) != _ED_PRIV_LEN:
            raise LedgerKeyError("stored ledger private key has the wrong length")
        pub = crypto.Ed25519PrivateKey.from_private_bytes(priv).public_key().public_bytes(
            crypto._RAW, crypto._PUB)
        return cls(priv, pub)

    def sign_sth(self, tree_size: int, root_hash: bytes,
                 timestamp: Optional[float] = None) -> STH:
        ts = time.time() if timestamp is None else float(timestamp)
        sig = crypto.ed_sign(self._priv, sth_signing_bytes(tree_size, root_hash, ts))
        return STH(tree_size=int(tree_size), root_hash=bytes(root_hash), timestamp=ts,
                   signature=sig, log_id=self.log_id)

def verify_sth(pub_raw: bytes, sth: STH) -> bool:

    try:
        if log_id_for_pubkey(pub_raw) != sth.log_id:
            return False
        msg = sth_signing_bytes(sth.tree_size, sth.root_hash, sth.timestamp)
    except LedgerKeyError:
        return False
    return crypto.ed_verify(bytes(pub_raw), sth.signature, msg)

def verify_sth_pinned(pinned_pubkey_hex: str, sth: STH) -> bool:

    try:
        pub = bytes.fromhex(pinned_pubkey_hex)
    except (ValueError, TypeError):
        return False
    return verify_sth(pub, sth)
