
from __future__ import annotations

import time as _time
import uuid
from typing import Callable, Dict, Optional, Tuple

from pnlib import secrets as _secrets
from pnlib.captok import verify as _captok_verify_fn
from pnlib.captok.model import CapToken, CapTokError
from pnlib.captok.revoke import RevocationRegistry, chain_revocation_ids

from .lease import Lease, LeaseError, LeaseStore, new_revocation_id

CRED_SCOPE_DIM = "cred"

HOLDER_MEMBER_DIM = "holder"

class LeaseDenied(Exception):
    pass

class DurableVault:

    def __init__(self) -> None:
        self._sealed: Dict[str, Tuple[bytes, dict]] = {}

    def put(self, cred_ref: str, value: str) -> None:

        if not isinstance(cred_ref, str) or not cred_ref:
            raise LeaseError("cred_ref must be a non-empty string")
        if not isinstance(value, str) or value == "":
            raise LeaseError("credential value must be a non-empty string")
        blob, meta = _secrets.seal(value.encode())
        self._sealed[cred_ref] = (blob, meta)

    def has(self, cred_ref: str) -> bool:
        return cred_ref in self._sealed

    def _reveal(self, cred_ref: str) -> str:

        if cred_ref not in self._sealed:
            raise LeaseDenied(f"no such credential {cred_ref!r} in the vault")
        blob, meta = self._sealed[cred_ref]
        return _secrets.unseal(blob, meta).decode()

class LeasedVaultBroker:

    def __init__(self, vault: DurableVault, store: LeaseStore, *,
                 owner_pubkey: bytes, revocations: Optional[RevocationRegistry] = None,
                 audience: Optional[str] = None,
                 clock: Optional[Callable[[], float]] = None,
                 _trust_redeem_now: bool = False) -> None:
        self.vault = vault
        self.store = store
        self.owner_pubkey = bytes(owner_pubkey)
        self.revocations = revocations if revocations is not None else RevocationRegistry()
        self.audience = audience

        self._clock = clock if clock is not None else _time.time

        self._trust_redeem_now = bool(_trust_redeem_now)

    def mint_lease(self, captoken, *, cred_ref: str, holder_agent_id: str,
                   ttl: float, max_uses: int = 1, now: Optional[float] = None) -> Lease:

        t = float(now if now is not None else _time.time())
        if isinstance(captoken, str):
            captoken = CapToken.from_json(captoken)
        elif isinstance(captoken, dict):
            captoken = CapToken.from_dict(captoken)
        if not isinstance(captoken, CapToken):
            raise LeaseDenied("captoken must be a CapToken / JSON / dict")

        try:
            grant = _captok_verify_fn(captoken, owner_pubkey=self.owner_pubkey,
                                      audience=self.audience, now=int(t))
        except CapTokError as e:
            raise LeaseDenied(f"capability token rejected: {e}")

        if self.revocations.is_token_revoked(captoken):
            raise LeaseDenied("capability token (or an ancestor in its chain) has been revoked")

        allowed = grant.scopes.get(CRED_SCOPE_DIM)
        if allowed is None or cred_ref not in allowed:
            raise LeaseDenied(
                f"capability does not authorise cred_ref {cred_ref!r} "
                f"(scope {CRED_SCOPE_DIM}={sorted(allowed) if allowed else '∅'})")

        holders = grant.members.get(HOLDER_MEMBER_DIM)
        if holders is not None and holder_agent_id not in holders:
            raise LeaseDenied(f"capability does not authorise holder {holder_agent_id!r}")

        if not self.vault.has(cred_ref):
            raise LeaseDenied(f"no such credential {cred_ref!r} in the vault")

        expires_at = t + float(ttl)
        if grant.exp is not None:
            expires_at = min(expires_at, float(grant.exp))
        if expires_at <= t:
            raise LeaseDenied("capability is already expired; refusing to mint a dead lease")

        lease_id = "lease:" + uuid.uuid4().hex
        lease = Lease(
            lease_id=lease_id, cred_ref=cred_ref,
            scope=frozenset(allowed), holder_agent_id=holder_agent_id,
            revocation_id=new_revocation_id(lease_id),
            issued_at=t, expires_at=expires_at, max_uses=int(max_uses),
            cap_chain_ids=tuple(chain_revocation_ids(captoken)),
        )
        self.store.insert(lease)
        return lease

    def cred_for(self, lease, *, holder_agent_id: Optional[str] = None,
                 now: Optional[float] = None) -> str:

        if holder_agent_id is None:
            raise LeaseDenied("holder identity is required to redeem a lease (fail closed)")

        if self._trust_redeem_now and now is not None:
            t = float(now)
        else:
            t = float(self._clock())
        lease_id = lease.lease_id if isinstance(lease, Lease) else str(lease)

        got = self.store.get(lease_id)
        if got is None:
            raise LeaseDenied(f"no such lease {lease_id!r}")
        lease_row, _uses = got

        if holder_agent_id != lease_row.holder_agent_id:
            raise LeaseDenied("holder mismatch: this agent may not redeem this lease")

        if t >= float(lease_row.expires_at):
            raise LeaseDenied("lease has expired")

        if self.revocations.is_revoked(lease_row.revocation_id):
            raise LeaseDenied("lease has been revoked")
        for rid in lease_row.cap_chain_ids:
            if self.revocations.is_revoked(rid):
                raise LeaseDenied("backing capability has been revoked")

        if not self.store.try_consume_use(lease_id):
            raise LeaseDenied("lease use budget exhausted (single-use already redeemed)")

        return self.vault._reveal(lease_row.cred_ref)

    def revoke_lease(self, lease, *, reason: Optional[str] = None) -> str:

        rid = lease.revocation_id if isinstance(lease, Lease) else str(lease)
        self.revocations.revoke(rid, reason=reason)
        return rid

    def revoke_capability_prefix(self, captoken, depth: int, *,
                                 reason: Optional[str] = None) -> str:

        if isinstance(captoken, (str, dict)):
            captoken = (CapToken.from_json(captoken) if isinstance(captoken, str)
                        else CapToken.from_dict(captoken))
        return self.revocations.revoke_token_prefix(captoken, depth, reason=reason)

__all__ = [
    "DurableVault", "LeasedVaultBroker", "LeaseDenied",
    "CRED_SCOPE_DIM", "HOLDER_MEMBER_DIM",
]
