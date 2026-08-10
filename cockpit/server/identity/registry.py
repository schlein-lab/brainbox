

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, asdict
from typing import Optional

from canonical import key_id as _key_id, b64u_decode, b64u_encode

STATE_PENDING = "pending"
STATE_ACTIVE = "active"
STATE_REVOKED = "revoked"

DELEGATE_ENABLED_DEFAULT = False

class RegistryError(Exception):

    code = "ERR_REGISTRY"

class NotEnrolled(RegistryError):
    code = "ERR_NOT_ENROLLED"

class NotApproved(RegistryError):
    code = "ERR_NOT_APPROVED"

class PrincipalMismatch(RegistryError):
    code = "ERR_PRINCIPAL_MISMATCH"

class DelegateDisabled(RegistryError):
    code = "ERR_CAP_MISSING"

@dataclass
class KeyRecord:
    key_id: str
    principal: str
    device_label: str
    pubkey_b64u: str
    state: str
    approved: bool
    created_ts: int
    approved_ts: Optional[int] = None
    revoked_ts: Optional[int] = None
    enrolled_by: Optional[str] = None
    rotated_from: Optional[str] = None

    def pubkey(self) -> bytes:
        return b64u_decode(self.pubkey_b64u)

    def public_view(self) -> dict:

        return {
            "key_id": self.key_id,
            "principal": self.principal,
            "device_label": self.device_label,
            "state": self.state,
            "approved": self.approved,
            "created_ts": self.created_ts,
            "approved_ts": self.approved_ts,
            "revoked_ts": self.revoked_ts,
            "enrolled_by": self.enrolled_by,
            "rotated_from": self.rotated_from,
        }

def _now_ms() -> int:
    return int(time.time() * 1000)

class Registry:

    def __init__(self, path: Optional[str] = None, clock=_now_ms,
                 delegate_enabled: bool = DELEGATE_ENABLED_DEFAULT):
        self._path = path
        self._clock = clock
        self._lock = threading.RLock()
        self._by_id: dict[str, KeyRecord] = {}
        self.delegate_enabled = delegate_enabled
        if path and os.path.exists(path):
            self._load()

    def _load(self) -> None:
        with open(self._path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self._by_id = {
            r["key_id"]: KeyRecord(**r) for r in data.get("keys", [])
        }
        self.delegate_enabled = data.get("delegate_enabled", self.delegate_enabled)

    def _persist(self) -> None:
        if not self._path:
            return
        payload = {
            "contract": "portal-contract/1",
            "delegate_enabled": self.delegate_enabled,
            "keys": [asdict(r) for r in self._by_id.values()],
        }
        d = os.path.dirname(os.path.abspath(self._path)) or "."
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".reg.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def get(self, key_id: str) -> Optional[KeyRecord]:
        with self._lock:
            return self._by_id.get(key_id)

    def interlock(self, principal: str, key_id: str) -> KeyRecord:

        with self._lock:
            rec = self._by_id.get(key_id)
            if rec is None:
                raise NotEnrolled(f"key_id {key_id!r} not in pinned registry")
            if rec.principal != principal:
                raise PrincipalMismatch(
                    f"key_id {key_id!r} is bound to principal {rec.principal!r}, "
                    f"not {principal!r}"
                )
            if not rec.approved or rec.state != STATE_ACTIVE:

                raise NotApproved(
                    f"key_id {key_id!r} state={rec.state} approved={rec.approved}"
                )
            return rec

    def list_keys(self, principal: Optional[str] = None) -> list[dict]:
        with self._lock:
            recs = self._by_id.values()
            if principal is not None:
                recs = [r for r in recs if r.principal == principal]
            return [r.public_view() for r in recs]

    def has_active_owner(self) -> bool:
        with self._lock:
            return any(
                r.principal == "owner" and r.approved and r.state == STATE_ACTIVE
                for r in self._by_id.values()
            )

    def enroll_request(self, pubkey: bytes, device_label: str, principal: str) -> KeyRecord:

        kid = _key_id(pubkey)
        with self._lock:
            existing = self._by_id.get(kid)
            if existing is not None:
                if existing.state == STATE_PENDING:
                    return existing
                raise RegistryError(
                    f"key_id {kid!r} already enrolled (state={existing.state})"
                )
            rec = KeyRecord(
                key_id=kid,
                principal=principal,
                device_label=device_label,
                pubkey_b64u=b64u_encode(pubkey),
                state=STATE_PENDING,
                approved=False,
                created_ts=self._clock(),
            )
            self._by_id[kid] = rec
            self._persist()
            return rec

    def bootstrap_owner(self, pubkey: bytes, device_label: str = "bootstrap-console") -> KeyRecord:

        with self._lock:
            if self.has_active_owner():
                raise RegistryError("bootstrap refused: an active owner already exists")
            kid = _key_id(pubkey)
            if kid in self._by_id:
                raise RegistryError(f"key_id {kid!r} already present")
            now = self._clock()
            rec = KeyRecord(
                key_id=kid,
                principal="owner",
                device_label=device_label,
                pubkey_b64u=b64u_encode(pubkey),
                state=STATE_ACTIVE,
                approved=True,
                created_ts=now,
                approved_ts=now,
                enrolled_by="bootstrap",
            )
            self._by_id[kid] = rec
            self._persist()
            return rec

    def approve(self, key_id: str, approver_key_id: Optional[str] = None) -> KeyRecord:

        with self._lock:
            rec = self._by_id.get(key_id)
            if rec is None:
                raise NotEnrolled(f"key_id {key_id!r} not enrolled")
            if rec.state == STATE_REVOKED:
                raise RegistryError(f"key_id {key_id!r} is revoked; cannot approve")
            if rec.state == STATE_ACTIVE and rec.approved:
                return rec
            rec.state = STATE_ACTIVE
            rec.approved = True
            rec.approved_ts = self._clock()
            rec.enrolled_by = approver_key_id or rec.enrolled_by
            self._persist()
            return rec

    def revoke(self, key_id: str) -> KeyRecord:

        with self._lock:
            rec = self._by_id.get(key_id)
            if rec is None:
                raise NotEnrolled(f"key_id {key_id!r} not enrolled")
            if rec.state == STATE_REVOKED:
                return rec
            rec.state = STATE_REVOKED
            rec.approved = False
            rec.revoked_ts = self._clock()
            self._persist()
            return rec

    def rotate(self, old_key_id: str, new_pubkey: bytes,
               device_label: Optional[str] = None) -> KeyRecord:

        with self._lock:
            old = self._by_id.get(old_key_id)
            if old is None:
                raise NotEnrolled(f"old key_id {old_key_id!r} not enrolled")
            if old.state != STATE_ACTIVE or not old.approved:
                raise NotApproved(f"old key_id {old_key_id!r} is not active/approved")
            new_kid = _key_id(new_pubkey)
            if new_kid in self._by_id:
                raise RegistryError(f"new key_id {new_kid!r} already present")
            rec = KeyRecord(
                key_id=new_kid,
                principal=old.principal,
                device_label=device_label or f"{old.device_label} (rotated)",
                pubkey_b64u=b64u_encode(new_pubkey),
                state=STATE_PENDING,
                approved=False,
                created_ts=self._clock(),
                rotated_from=old_key_id,
            )
            self._by_id[new_kid] = rec
            self._persist()
            return rec

    def delegate(self, pubkey: bytes, device_label: str, principal: str,
                 delegator_key_id: str) -> KeyRecord:

        with self._lock:
            if not self.delegate_enabled:
                raise DelegateDisabled(
                    "enroll.delegate is DISABLED (owner-open Q4, fail-closed); "
                    "use enroll.approve"
                )
            delegator = self._by_id.get(delegator_key_id)
            if delegator is None:
                raise NotEnrolled(f"delegator key_id {delegator_key_id!r} not enrolled")
            if delegator.state != STATE_ACTIVE or not delegator.approved:
                raise NotApproved("delegator key is not active/approved")
            if delegator.principal != principal:
                raise PrincipalMismatch(
                    "a key may only delegate a device to its OWN principal"
                )
            new_kid = _key_id(pubkey)
            if new_kid in self._by_id:
                raise RegistryError(f"key_id {new_kid!r} already present")
            now = self._clock()
            rec = KeyRecord(
                key_id=new_kid,
                principal=principal,
                device_label=device_label,
                pubkey_b64u=b64u_encode(pubkey),
                state=STATE_ACTIVE,
                approved=True,
                created_ts=now,
                approved_ts=now,
                enrolled_by=delegator_key_id,
            )
            self._by_id[new_kid] = rec
            self._persist()
            return rec
