

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

import ed25519_backend as eb
from canonical import (
    CONTRACT_VERSION,
    b64u_decode,
    b64u_encode,
    key_id as _key_id,
    signing_string,
)
from registry import KeyRecord, Registry, RegistryError

SKEW_MS = 120_000
NONCE_RETENTION_MS = 2 * SKEW_MS

class AuthError(Exception):

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code

@dataclass
class VerifiedRequest:
    principal: str
    key_id: str
    verb: str
    args: dict
    funding: str
    id: str
    ts: int
    record: KeyRecord

def _now_ms() -> int:
    return int(time.time() * 1000)

class NonceCache:

    def __init__(self, retention_ms: int = NONCE_RETENTION_MS, clock=_now_ms):
        self._retention = retention_ms
        self._clock = clock
        self._seen: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def check_and_add(self, principal: str, nonce: str, now_ms: int) -> bool:

        with self._lock:
            self._sweep(now_ms)
            key = (principal, nonce)
            if key in self._seen:
                return False
            self._seen[key] = now_ms
            return True

    def _sweep(self, now_ms: int) -> None:
        cutoff = now_ms - self._retention
        if not self._seen:
            return
        stale = [k for k, t in self._seen.items() if t < cutoff]
        for k in stale:
            del self._seen[k]

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)

class Verifier:

    def __init__(self, registry: Registry, clock=_now_ms,
                 skew_ms: int = SKEW_MS,
                 supported_contracts=(CONTRACT_VERSION,)):
        self.registry = registry
        self._clock = clock
        self.skew_ms = skew_ms
        self._supported = tuple(supported_contracts)
        self.nonces = NonceCache(retention_ms=2 * skew_ms, clock=clock)

    def verify(self, env: dict) -> VerifiedRequest:

        if env.get("type") != "req":
            raise AuthError("ERR_BAD_SIG", "not a request envelope")
        for f in ("contract", "id", "ts", "nonce", "principal", "key_id",
                  "funding", "verb", "sig"):
            if f not in env:
                raise AuthError("ERR_BAD_SIG", f"missing envelope field {f!r}")

        if env["contract"] not in self._supported:
            raise AuthError("ERR_CONTRACT_MISMATCH",
                            f"unsupported contract {env['contract']!r}")

        principal = env["principal"]
        key_id = env["key_id"]

        try:
            record = self.registry.interlock(principal, key_id)
        except RegistryError as e:
            raise AuthError(e.code, str(e)) from e

        pub = record.pubkey()
        if _key_id(pub) != key_id:
            raise AuthError("ERR_BAD_SIG", "key_id does not match pinned public key")

        try:
            sig = b64u_decode(env["sig"])
        except Exception as e:
            raise AuthError("ERR_BAD_SIG", f"malformed signature encoding: {e}") from e
        try:
            msg = signing_string(env)
        except (KeyError, TypeError, ValueError) as e:
            raise AuthError("ERR_BAD_SIG", f"cannot build signing string: {e}") from e
        if not eb.verify(pub, sig, msg):
            raise AuthError("ERR_BAD_SIG", "signature does not verify")

        ts = env["ts"]
        if isinstance(ts, bool) or not isinstance(ts, int):
            raise AuthError("ERR_STALE", "ts must be an integer")
        now = self._clock()
        if abs(now - ts) > self.skew_ms:
            raise AuthError("ERR_STALE",
                            f"ts skew {abs(now - ts)}ms exceeds {self.skew_ms}ms")

        if not self.nonces.check_and_add(principal, env["nonce"], now):
            raise AuthError("ERR_REPLAY",
                            f"(principal={principal}, nonce) already seen")

        return VerifiedRequest(
            principal=principal,
            key_id=key_id,
            verb=env["verb"],
            args=env.get("args", {}) or {},
            funding=env["funding"],
            id=env["id"],
            ts=ts,
            record=record,
        )

def new_nonce() -> str:

    return b64u_encode(os.urandom(16))

def sign_request(priv: bytes, pub: bytes, *, principal: str, verb: str,
                 args: Optional[dict] = None,
                 funding: str = "member-subsidized",
                 req_id: Optional[str] = None,
                 ts: Optional[int] = None,
                 nonce: Optional[str] = None,
                 contract: str = CONTRACT_VERSION) -> dict:

    import uuid

    args = args if args is not None else {}
    env = {
        "contract": contract,
        "type": "req",
        "id": req_id or str(uuid.uuid4()),
        "ts": ts if ts is not None else _now_ms(),
        "nonce": nonce or new_nonce(),
        "principal": principal,
        "key_id": _key_id(pub),
        "funding": funding,
        "verb": verb,
        "args": args,
    }
    env["sig"] = b64u_encode(eb.sign(priv, signing_string(env)))
    return env
