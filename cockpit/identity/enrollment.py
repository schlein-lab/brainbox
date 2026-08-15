

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from . import errors
from .canonical import build_signing_string, key_id_from_pubkey
from .config import FederationPolicy, IdentityConfig
from .keys import load_public_from_b64url, verify_detached_b64url
from .registry import (
    KeyRecord,
    KeyRegistry,
    ROLE_MEMBER,
    ROLE_OWNER,
    ROLES,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REVOKED,
)

def _now_ms() -> int:
    return int(time.time() * 1000)

RESERVED_OWNER_PRINCIPAL = "owner"

@dataclass
class EnrollRequest:

    pubkey: str
    device_label: str
    principal: str
    role: str
    envelope: dict
    sig: str

class EnrollmentManager:

    def __init__(self, registry: KeyRegistry, config: Optional[IdentityConfig] = None):
        self.registry = registry
        self.config = config or IdentityConfig.default()

    def bootstrap_owner(self, pubkey_b64url: str, device_label: str) -> KeyRecord:

        if self.registry.any_active_owner():
            raise errors.AuthError(
                errors.ERR_ENROLL_CONFLICT,
                "bootstrap refused: an owner key is already active",
            )
        raw = load_public_from_b64url(pubkey_b64url)
        kid = key_id_from_pubkey(raw)
        if self.registry.has(kid):
            raise errors.AuthError(errors.ERR_ENROLL_CONFLICT, "key already enrolled")
        now = _now_ms()
        rec = KeyRecord(
            key_id=kid,
            principal="owner",
            role=ROLE_OWNER,
            pubkey=pubkey_b64url,
            device_label=device_label,
            status=STATUS_APPROVED,
            enrolled_at=now,
            approved_at=now,
            enrolled_by=None,
        )
        self.registry.upsert(rec)
        return rec

    def request(self, req: EnrollRequest) -> KeyRecord:

        if req.role not in ROLES:
            raise errors.AuthError(errors.ERR_BAD_REQUEST, f"unknown role {req.role!r}")
        if req.role == ROLE_OWNER:

            raise errors.AuthError(
                errors.ERR_BAD_REQUEST,
                "enroll.request may not self-claim the owner role",
            )
        if not req.principal:
            raise errors.AuthError(errors.ERR_BAD_REQUEST, "principal is required")

        if req.principal == RESERVED_OWNER_PRINCIPAL:
            raise errors.AuthError(
                errors.ERR_BAD_REQUEST,
                "the 'owner' principal is reserved for bootstrap and cannot be self-claimed",
            )

        raw = load_public_from_b64url(req.pubkey)
        kid = key_id_from_pubkey(raw)

        existing = self.registry.get(kid)
        if existing is not None:
            detail = {"key_id": kid, "state": existing.status}
            if existing.status == STATUS_REVOKED:
                raise errors.AuthError(
                    errors.ERR_ENROLL_CONFLICT,
                    "this key was revoked; revocation is permanent — use new key material",
                    detail,
                )
            raise errors.AuthError(
                errors.ERR_ENROLL_CONFLICT, "key already enrolled", detail,
            )

        env = req.envelope
        if not isinstance(env, dict):
            raise errors.AuthError(
                errors.ERR_BAD_REQUEST, "enroll.request envelope is required"
            )
        args = env.get("args", {})
        if not isinstance(args, dict):
            raise errors.AuthError(
                errors.ERR_BAD_REQUEST, "enroll.request args must be an object"
            )

        if env.get("type") != "req" or env.get("verb") != "enroll.request":
            raise errors.AuthError(
                errors.ERR_BAD_REQUEST,
                "envelope is not a type=req enroll.request",
                {"type": env.get("type"), "verb": env.get("verb")},
            )

        if env.get("principal") != req.principal:
            raise errors.AuthError(
                errors.ERR_BAD_REQUEST,
                "envelope principal does not match the claimed principal",
                {"envelope": env.get("principal"), "claim": req.principal},
            )
        if env.get("key_id") != kid:
            raise errors.AuthError(
                errors.ERR_BAD_REQUEST,
                "envelope key_id does not match the presented pubkey",
                {"envelope": env.get("key_id"), "derived": kid},
            )

        if args.get("pubkey") != req.pubkey:
            raise errors.AuthError(
                errors.ERR_BAD_REQUEST,
                "signed args.pubkey does not match the claimed pubkey",
            )
        if args.get("principal") != req.principal:
            raise errors.AuthError(
                errors.ERR_BAD_REQUEST,
                "signed args.principal does not match the claimed principal",
            )
        if args.get("role") != req.role:
            raise errors.AuthError(
                errors.ERR_BAD_REQUEST,
                "signed args.role does not match the claimed role",
                {"signed": args.get("role"), "claim": req.role},
            )

        try:
            signing_string = build_signing_string(
                type=env["type"],
                id=env["id"],
                ts=env["ts"],
                nonce=env["nonce"],
                principal=env["principal"],
                key_id=env["key_id"],
                funding=env["funding"],
                verb=env["verb"],
                args=args,
                contract=env.get("contract"),
            )
        except (KeyError, ValueError) as exc:
            raise errors.AuthError(
                errors.ERR_BAD_REQUEST, f"malformed enroll.request envelope: {exc}"
            ) from exc
        if not verify_detached_b64url(raw, req.sig, signing_string):
            raise errors.AuthError(
                errors.ERR_BAD_SIG, "enroll.request proof-of-possession failed"
            )

        squatting = bool(self.registry.active_keys_for(req.principal))

        now = _now_ms()
        rec = KeyRecord(
            key_id=kid,
            principal=req.principal,
            role=req.role,
            pubkey=req.pubkey,
            device_label=req.device_label,
            status=STATUS_PENDING,
            enrolled_at=now,
            claims_existing_principal=squatting,
        )
        self.registry.upsert(rec)
        return rec

    def approve(
        self,
        approver: "VerifiedRequest",
        key_id: str,
        *,
        expected_role: Optional[str] = None,
        approve_squatting_principal: bool = False,
    ) -> KeyRecord:

        self._require_owner(approver)
        rec = self._require_key(key_id)
        if expected_role is not None and rec.role != expected_role:
            raise errors.AuthError(
                errors.ERR_BAD_REQUEST,
                "requested role does not match the role the owner is confirming",
                {"key_id": key_id, "record_role": rec.role, "expected": expected_role},
            )
        if rec.status == STATUS_APPROVED:
            return rec
        if rec.status == STATUS_REVOKED:
            raise errors.AuthError(
                errors.ERR_ENROLL_CONFLICT,
                "cannot approve a revoked key; re-enroll instead",
                {"key_id": key_id},
            )
        if rec.claims_existing_principal and not approve_squatting_principal:
            raise errors.AuthError(
                errors.ERR_ENROLL_CONFLICT,
                "a DIFFERENT key claims an already-active principal; "
                "re-confirm with approve_squatting_principal=True if this is intended",
                {"key_id": key_id, "principal": rec.principal},
            )
        return self.registry.set_status(
            key_id, STATUS_APPROVED, approver_key_id=approver.key_id
        )

    def deny(self, approver: "VerifiedRequest", key_id: str) -> None:

        self._require_owner(approver)
        rec = self._require_key(key_id)
        if rec.status != STATUS_PENDING:
            raise errors.AuthError(
                errors.ERR_ENROLL_CONFLICT,
                "only a pending key can be denied",
                {"key_id": key_id, "state": rec.status},
            )

        with self.registry._lock:
            self.registry._records.pop(key_id, None)
            self.registry._save_locked()

    def revoke(self, actor: "VerifiedRequest", key_id: str) -> KeyRecord:

        rec = self._require_key(key_id)
        actor_rec = self.registry.get(actor.key_id)
        is_owner = actor_rec is not None and actor_rec.role == ROLE_OWNER
        if not is_owner and rec.principal != actor.principal:
            raise errors.AuthError(
                errors.ERR_NOT_OWNER,
                "only an owner may revoke another principal's key",
            )
        if rec.status == STATUS_REVOKED:
            return rec
        return self.registry.set_status(key_id, STATUS_REVOKED)

    def rotate(
        self, actor: "VerifiedRequest", old_key_id: str, new_pubkey_b64url: str
    ) -> KeyRecord:

        old = self._require_key(old_key_id)
        actor_rec = self.registry.get(actor.key_id)
        is_owner = actor_rec is not None and actor_rec.role == ROLE_OWNER
        if not is_owner and old.principal != actor.principal:
            raise errors.AuthError(
                errors.ERR_NOT_OWNER,
                "only an owner may rotate another principal's key",
            )
        if not old.active:
            raise errors.AuthError(
                errors.ERR_ENROLL_CONFLICT,
                "can only rotate from an active key",
                {"old_key_id": old_key_id, "state": old.status},
            )
        raw = load_public_from_b64url(new_pubkey_b64url)
        new_kid = key_id_from_pubkey(raw)
        if self.registry.has(new_kid):
            raise errors.AuthError(errors.ERR_ENROLL_CONFLICT, "new key already enrolled")
        now = _now_ms()
        new_rec = KeyRecord(
            key_id=new_kid,
            principal=old.principal,
            role=old.role,
            pubkey=new_pubkey_b64url,
            device_label=old.device_label + " (rotated)",
            status=STATUS_PENDING,
            enrolled_at=now,
            enrolled_by=actor.key_id,
        )
        self.registry.upsert(new_rec)
        self.registry.link_rotation(old_key_id, new_kid)
        return new_rec

    def delegate(
        self,
        delegator: "VerifiedRequest",
        pubkey_b64url: str,
        device_label: str,
        principal: str,
        role: str = ROLE_MEMBER,
    ) -> KeyRecord:

        if self.config.federation_policy != FederationPolicy.CHAIN_OF_TRUST:
            raise errors.AuthError(
                errors.ERR_DELEGATE_DISABLED,
                "enroll.delegate is disabled; policy=owner-approve (contract Q4 unanswered)",
            )
        if role == ROLE_OWNER:
            raise errors.AuthError(
                errors.ERR_BAD_REQUEST, "delegate may not mint an owner key"
            )
        if role not in ROLES:
            raise errors.AuthError(errors.ERR_BAD_REQUEST, f"unknown role {role!r}")
        if not principal:
            raise errors.AuthError(errors.ERR_BAD_REQUEST, "principal is required")
        if principal == RESERVED_OWNER_PRINCIPAL:

            raise errors.AuthError(
                errors.ERR_BAD_REQUEST,
                "the 'owner' principal is reserved and cannot be delegated",
            )

        delegator_rec = self.registry.get(delegator.key_id)
        if delegator_rec is None or not delegator_rec.active:
            raise errors.AuthError(
                errors.ERR_PRINCIPAL_MISMATCH, "delegator key is not active"
            )
        raw = load_public_from_b64url(pubkey_b64url)
        kid = key_id_from_pubkey(raw)
        if self.registry.has(kid):
            raise errors.AuthError(errors.ERR_ENROLL_CONFLICT, "key already enrolled")
        now = _now_ms()
        rec = KeyRecord(
            key_id=kid,
            principal=principal,
            role=role,
            pubkey=pubkey_b64url,
            device_label=device_label,
            status=STATUS_APPROVED,
            enrolled_at=now,
            approved_at=now,
            enrolled_by=delegator.key_id,
        )
        self.registry.upsert(rec)
        return rec

    def list(self, principal: Optional[str] = None) -> list[dict]:
        return [r.public_view() for r in self.registry.list(principal)]

    def _require_owner(self, actor: "VerifiedRequest") -> None:
        rec = self.registry.get(actor.key_id)
        if rec is None or rec.role != ROLE_OWNER or not rec.active:
            raise errors.AuthError(
                errors.ERR_NOT_OWNER, "this action requires an active owner key"
            )

    def _require_key(self, key_id: str) -> KeyRecord:
        rec = self.registry.get(key_id)
        if rec is None:
            raise errors.AuthError(
                errors.ERR_NOT_ENROLLED, "no such key", {"key_id": key_id}
            )
        return rec

from .verifier import VerifiedRequest
