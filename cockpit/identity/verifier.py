

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Optional

from . import errors
from .canonical import b64url_decode, build_signing_string
from .config import IdentityConfig
from .keys import verify_detached_b64url
from .registry import KeyRegistry, STATUS_APPROVED, STATUS_PENDING, STATUS_REVOKED

def _now_ms() -> int:
    return int(time.time() * 1000)

_NONCE_MAX_LEN = 64
_NONCE_MIN_LEN = 8
_NONCE_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_="
)

def _valid_nonce_shape(nonce: str) -> bool:
    if not isinstance(nonce, str):
        return False
    if not (_NONCE_MIN_LEN <= len(nonce) <= _NONCE_MAX_LEN):
        return False
    return all(c in _NONCE_ALPHABET for c in nonce)

@dataclass(frozen=True)
class VerifiedRequest:

    principal: str
    role: str
    key_id: str
    verb: str
    funding: str
    args: Any
    id: str
    ts: int
    nonce: str

class NonceCache:

    def __init__(
        self,
        skew_ms: int,
        clock: Callable[[], int] = _now_ms,
        *,
        max_per_principal: int = 4096,
        max_total: int = 262_144,
    ):
        self._ttl = 2 * skew_ms
        self._clock = clock
        self._max_per_principal = max_per_principal
        self._max_total = max_total
        self._lock = threading.Lock()

        self._seen: dict[str, "OrderedDict[str, int]"] = {}
        self._total = 0

    def _expire_bucket(self, bucket: "OrderedDict[str, int]", now: int) -> None:

        while bucket:
            oldest_nonce, exp = next(iter(bucket.items()))
            if exp > now:
                break
            bucket.popitem(last=False)
            self._total -= 1

    def _evict_one_global(self) -> None:

        for principal in list(self._seen.keys()):
            bucket = self._seen[principal]
            if bucket:
                bucket.popitem(last=False)
                self._total -= 1
                if not bucket:
                    self._seen.pop(principal, None)
                return

    def check_and_record(self, principal: str, nonce: str) -> bool:

        with self._lock:
            now = self._clock()
            bucket = self._seen.get(principal)
            if bucket is None:
                bucket = OrderedDict()
                self._seen[principal] = bucket
            self._expire_bucket(bucket, now)
            exp = bucket.get(nonce)
            if exp is not None and exp > now:
                return False

            if nonce in bucket:

                del bucket[nonce]
                self._total -= 1
            while len(bucket) >= self._max_per_principal:
                bucket.popitem(last=False)
                self._total -= 1
            while self._total >= self._max_total:
                self._evict_one_global()

                bucket = self._seen.setdefault(principal, bucket)
            bucket[nonce] = now + self._ttl
            self._total += 1
            if not bucket:
                self._seen.pop(principal, None)
            return True

    def seen(self, principal: str, nonce: str) -> bool:
        with self._lock:
            now = self._clock()
            bucket = self._seen.get(principal, {})
            exp = bucket.get(nonce)
            return exp is not None and exp > now

class RequestVerifier:

    def __init__(
        self,
        registry: KeyRegistry,
        config: Optional[IdentityConfig] = None,
        clock: Callable[[], int] = _now_ms,
    ):
        self.registry = registry
        self.config = config or IdentityConfig.default()
        self._clock = clock
        self._nonces = NonceCache(self.config.skew_ms, clock=clock)

        self._verify_lock = threading.Lock()

    def verify(self, envelope: dict) -> VerifiedRequest:

        with self._verify_lock:
            return self._verify_locked(envelope)

    def _verify_locked(self, envelope: dict) -> VerifiedRequest:
        self._require_fields(envelope)

        contract = envelope["contract"]
        etype = envelope["type"]
        rid = envelope["id"]
        ts = envelope["ts"]
        nonce = envelope["nonce"]
        principal = envelope["principal"]
        key_id = envelope["key_id"]
        funding = envelope["funding"]
        verb = envelope["verb"]
        args = envelope.get("args", {})
        sig = envelope["sig"]

        if contract != self.config.contract_version:
            raise errors.AuthError(
                errors.ERR_CONTRACT_MISMATCH,
                f"unsupported contract {contract!r}",
                {"expected": self.config.contract_version},
            )
        if etype != "req":

            raise errors.AuthError(
                errors.ERR_BAD_REQUEST, f"verifier only handles type=req, got {etype!r}"
            )

        rec = self.registry.get(key_id)
        if rec is None:
            raise errors.AuthError(
                errors.ERR_NOT_ENROLLED, "signing key is not enrolled",
                {"key_id": key_id},
            )
        if rec.status == STATUS_PENDING:
            raise errors.AuthError(
                errors.ERR_NOT_APPROVED, "signing key is pending owner approval",
                {"key_id": key_id},
            )
        if rec.status == STATUS_REVOKED:

            raise errors.AuthError(
                errors.ERR_PRINCIPAL_MISMATCH, "signing key is revoked",
                {"key_id": key_id},
            )
        if rec.status != STATUS_APPROVED:
            raise errors.AuthError(
                errors.ERR_NOT_APPROVED, "signing key is not active",
                {"key_id": key_id, "state": rec.status},
            )

        if rec.principal != principal:
            raise errors.AuthError(
                errors.ERR_PRINCIPAL_MISMATCH,
                "declared principal does not match the signing key's principal",
                {"declared": principal, "bound": rec.principal, "key_id": key_id},
            )

        try:
            raw_pub = b64url_decode(rec.pubkey)
        except Exception as exc:
            raise errors.AuthError(
                errors.ERR_BAD_SIG, "corrupt pinned public key", {"key_id": key_id}
            ) from exc
        try:
            signing_string = build_signing_string(
                type=etype,
                id=rid,
                ts=ts,
                nonce=nonce,
                principal=principal,
                key_id=key_id,
                funding=funding,
                verb=verb,
                args=args,
                contract=contract,
            )
        except ValueError as exc:
            raise errors.AuthError(errors.ERR_BAD_REQUEST, str(exc)) from exc
        if not verify_detached_b64url(raw_pub, sig, signing_string):
            raise errors.AuthError(
                errors.ERR_BAD_SIG, "signature does not verify over the canonical repr"
            )

        now = self._clock()
        if abs(now - ts) > self.config.skew_ms:
            raise errors.AuthError(
                errors.ERR_STALE,
                "timestamp outside the skew window",
                {"now_ms": now, "ts": ts, "skew_ms": self.config.skew_ms},
            )

        if not self._nonces.check_and_record(principal, nonce):
            raise errors.AuthError(
                errors.ERR_REPLAY, "nonce already seen for this principal",
                {"principal": principal},
            )

        return VerifiedRequest(
            principal=rec.principal,
            role=rec.role,
            key_id=key_id,
            verb=verb,
            funding=funding,
            args=args,
            id=rid,
            ts=ts,
            nonce=nonce,
        )

    @staticmethod
    def _require_fields(envelope: dict) -> None:
        if not isinstance(envelope, dict):
            raise errors.AuthError(errors.ERR_BAD_REQUEST, "envelope must be an object")
        required = (
            "contract", "type", "id", "ts", "nonce",
            "principal", "key_id", "funding", "verb", "sig",
        )
        missing = [f for f in required if f not in envelope]
        if missing:
            raise errors.AuthError(
                errors.ERR_BAD_REQUEST,
                f"envelope missing required field(s): {', '.join(missing)}",
            )
        if not isinstance(envelope["ts"], int) or isinstance(envelope["ts"], bool):
            raise errors.AuthError(errors.ERR_BAD_REQUEST, "ts must be an int (ms epoch)")

        if not _valid_nonce_shape(envelope["nonce"]):
            raise errors.AuthError(
                errors.ERR_BAD_REQUEST,
                "nonce must be a short base64url string (§1.2)",
            )
