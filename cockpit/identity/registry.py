

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

ROLE_OWNER = "owner"
ROLE_MEMBER = "member"
ROLE_GUEST = "guest"
ROLES = (ROLE_OWNER, ROLE_MEMBER, ROLE_GUEST)

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REVOKED = "revoked"
STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_REVOKED)

REGISTRY_SCHEMA = "brainarbeit-identity-registry/1"

def _now_ms() -> int:
    return int(time.time() * 1000)

@dataclass
class KeyRecord:
    key_id: str
    principal: str
    role: str
    pubkey: str
    device_label: str
    status: str
    enrolled_at: int
    approved_at: Optional[int] = None
    revoked_at: Optional[int] = None
    enrolled_by: Optional[str] = None
    rotated_from: Optional[str] = None
    rotated_to: Optional[str] = None

    claims_existing_principal: bool = False

    @property
    def approved(self) -> bool:

        return self.status == STATUS_APPROVED

    @property
    def active(self) -> bool:

        return self.status == STATUS_APPROVED

    def public_view(self) -> dict:

        return {
            "key_id": self.key_id,
            "principal": self.principal,
            "role": self.role,
            "device_label": self.device_label,
            "state": self.status,
            "approved": self.approved,

            "claims_existing_principal": bool(self.claims_existing_principal),
        }

class KeyRegistry:

    def __init__(self, path: Optional[str] = None):
        self._path = path
        self._lock = threading.RLock()
        self._records: dict[str, KeyRecord] = {}
        if path and os.path.exists(path):
            self._load()

    def _load(self) -> None:
        with open(self._path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("schema") != REGISTRY_SCHEMA:
            raise ValueError(
                f"unexpected registry schema {data.get('schema')!r}; refusing to load"
            )

        known_fields = set(KeyRecord.__dataclass_fields__.keys())
        recs = {}
        for r in data.get("keys", []):
            if not isinstance(r, dict):
                raise ValueError("registry key record must be an object")
            unknown = set(r.keys()) - known_fields
            if unknown:
                raise ValueError(
                    f"registry record has unknown field(s) {sorted(unknown)}; "
                    "refusing to load a possibly-tampered registry"
                )
            try:
                rec = KeyRecord(**r)
            except TypeError as exc:
                raise ValueError(
                    f"registry record is malformed: {exc}; refusing to load"
                ) from exc
            self._validate_record(rec)
            if rec.key_id in recs:
                raise ValueError(
                    f"duplicate key_id {rec.key_id!r} in registry; refusing to load"
                )
            recs[rec.key_id] = rec
        self._records = recs

    @staticmethod
    def _validate_record(rec: KeyRecord) -> None:

        if rec.role not in ROLES:
            raise ValueError(f"registry record has unknown role {rec.role!r}")
        if rec.status not in STATUSES:
            raise ValueError(f"registry record has unknown status {rec.status!r}")
        if not isinstance(rec.principal, str) or not rec.principal:
            raise ValueError("registry record has an empty/invalid principal")
        if not isinstance(rec.key_id, str) or not rec.key_id:
            raise ValueError("registry record has an empty/invalid key_id")

        from .canonical import key_id_from_pubkey
        from .keys import load_public_from_b64url

        try:
            derived = key_id_from_pubkey(load_public_from_b64url(rec.pubkey))
        except Exception as exc:
            raise ValueError(
                f"registry record has invalid pubkey material: {exc}"
            ) from exc
        if derived != rec.key_id:
            raise ValueError(
                f"registry record key_id {rec.key_id!r} does not match its pubkey "
                f"(derived {derived!r}); refusing to load a tampered record"
            )

    def _save_locked(self) -> None:
        if not self._path:
            return
        payload = {
            "schema": REGISTRY_SCHEMA,
            "keys": [asdict(r) for r in self._records.values()],
        }
        d = os.path.dirname(os.path.abspath(self._path)) or "."

        os.makedirs(d, mode=0o700, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".registry.", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def get(self, key_id: str) -> Optional[KeyRecord]:
        with self._lock:
            return self._records.get(key_id)

    def has(self, key_id: str) -> bool:
        with self._lock:
            return key_id in self._records

    def list(self, principal: Optional[str] = None) -> list[KeyRecord]:
        with self._lock:
            recs = list(self._records.values())
        if principal is not None:
            recs = [r for r in recs if r.principal == principal]
        return sorted(recs, key=lambda r: r.enrolled_at)

    def active_keys_for(self, principal: str) -> list[KeyRecord]:
        return [r for r in self.list(principal) if r.active]

    def any_active_owner(self) -> bool:

        with self._lock:
            return any(
                r.role == ROLE_OWNER and r.active for r in self._records.values()
            )

    def is_empty(self) -> bool:
        with self._lock:
            return not self._records

    def upsert(self, rec: KeyRecord) -> None:
        with self._lock:
            self._records[rec.key_id] = rec
            self._save_locked()

    def set_status(
        self,
        key_id: str,
        status: str,
        *,
        approver_key_id: Optional[str] = None,
        at_ms: Optional[int] = None,
    ) -> KeyRecord:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        with self._lock:
            rec = self._records[key_id]
            rec.status = status
            ts = at_ms if at_ms is not None else _now_ms()
            if status == STATUS_APPROVED:
                rec.approved_at = ts
                if approver_key_id is not None:
                    rec.enrolled_by = approver_key_id
            elif status == STATUS_REVOKED:
                rec.revoked_at = ts
            self._save_locked()
            return rec

    def link_rotation(self, old_key_id: str, new_key_id: str) -> None:
        with self._lock:
            if old_key_id in self._records:
                self._records[old_key_id].rotated_to = new_key_id
            if new_key_id in self._records:
                self._records[new_key_id].rotated_from = old_key_id
            self._save_locked()
