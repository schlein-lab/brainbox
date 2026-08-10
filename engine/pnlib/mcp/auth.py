
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time as _time
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Optional

from pnlib.leases.lease import Lease, LeaseStore, new_revocation_id
from pnlib.captok.revoke import RevocationRegistry

class MCPAuthError(Exception):
    pass

SCOPE_AUD_PREFIX = "mcp:aud:"
SCOPE_TOOL_PREFIX = "mcp:tool:"

def aud_scope(audience: str) -> str:
    if not isinstance(audience, str) or not audience:
        raise MCPAuthError("audience must be a non-empty string")
    return SCOPE_AUD_PREFIX + audience

def tool_scope(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise MCPAuthError("tool name must be a non-empty string")
    return SCOPE_TOOL_PREFIX + name

def generate_code_verifier(nbytes: int = 48) -> str:

    raw = os.urandom(max(32, int(nbytes)))
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

def code_challenge_for(verifier: str, method: str = "S256") -> str:

    if not isinstance(verifier, str) or not verifier:
        raise MCPAuthError("code_verifier must be a non-empty string")
    if method == "plain":
        return verifier
    if method != "S256":
        raise MCPAuthError(f"unsupported PKCE method {method!r}")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

def verify_pkce(verifier: str, challenge: str, method: str = "S256") -> bool:

    try:
        recomputed = code_challenge_for(verifier, method)
    except MCPAuthError:
        return False
    return hmac.compare_digest(recomputed, challenge or "")

@dataclass(frozen=True)
class AccessGrant:

    session_id: str
    audience: str
    scope: frozenset
    issued_at: float
    expires_at: float

    def is_expired(self, now: Optional[float] = None) -> bool:
        t = float(now if now is not None else _time.time())
        return t > self.expires_at

@dataclass
class _PendingAuth:
    challenge: str
    method: str
    audience: str
    scope: frozenset
    expires_at: float
    used: bool = False

class AuthorizationServer:

    def __init__(self, *, code_ttl: float = 120.0, session_ttl: float = 900.0) -> None:
        self.code_ttl = float(code_ttl)
        self.session_ttl = float(session_ttl)
        self._codes: dict[str, _PendingAuth] = {}

    def authorize(self, *, challenge: str, audience: str, scope: Iterable[str],
                  method: str = "S256", now: Optional[float] = None) -> str:
        if method not in ("S256", "plain"):
            raise MCPAuthError(f"unsupported PKCE method {method!r}")
        if not isinstance(challenge, str) or not challenge:
            raise MCPAuthError("code_challenge is required")
        t = float(now if now is not None else _time.time())
        code = "authz:" + uuid.uuid4().hex
        self._codes[code] = _PendingAuth(challenge=challenge, method=method, audience=audience,
                                         scope=frozenset(scope), expires_at=t + self.code_ttl)
        return code

    def exchange(self, *, code: str, verifier: str, now: Optional[float] = None) -> AccessGrant:
        t = float(now if now is not None else _time.time())
        pend = self._codes.get(code)
        if pend is None:
            raise MCPAuthError("unknown or already-redeemed authorization code")
        if pend.used:
            raise MCPAuthError("authorization code already redeemed (single-use)")
        if t > pend.expires_at:
            raise MCPAuthError("authorization code expired")
        if not verify_pkce(verifier, pend.challenge, pend.method):

            pend.used = True
            raise MCPAuthError("PKCE verification failed (verifier does not match challenge)")
        pend.used = True
        return AccessGrant(session_id="sess:" + uuid.uuid4().hex, audience=pend.audience,
                           scope=pend.scope, issued_at=t, expires_at=t + self.session_ttl)

@dataclass(frozen=True)
class VerifiedCall:

    lease_id: str
    holder_agent_id: str
    server_audience: str
    tool_name: str
    scope: frozenset

def mint_call_lease(store: LeaseStore, *, holder_agent_id: str, server_audience: str,
                    tool_names: Iterable[str], ttl: float, max_uses: int = 1,
                    now: Optional[float] = None, extra_scope: Iterable[str] = ()) -> Lease:

    t = float(now if now is not None else _time.time())
    scope = {aud_scope(server_audience)}
    for n in tool_names:
        scope.add(tool_scope(n))
    scope.update(extra_scope)
    lease_id = "lease:mcp:" + uuid.uuid4().hex
    lease = Lease(
        lease_id=lease_id, cred_ref="mcp:call:" + server_audience,
        scope=frozenset(scope), holder_agent_id=holder_agent_id,
        revocation_id=new_revocation_id(lease_id),
        issued_at=t, expires_at=t + float(ttl), max_uses=int(max_uses),
    )
    store.insert(lease)
    return lease

class LeaseVerifier:

    def __init__(self, store: LeaseStore, *, revocations: Optional[RevocationRegistry] = None) -> None:
        self.store = store
        self.revocations = revocations if revocations is not None else RevocationRegistry()

    def verify(self, lease, *, holder_agent_id: str, server_audience: str, tool_name: str,
               now: Optional[float] = None) -> VerifiedCall:
        t = float(now if now is not None else _time.time())
        lease_id = lease.lease_id if isinstance(lease, Lease) else str(lease)

        got = self.store.get(lease_id)
        if got is None:
            raise MCPAuthError(f"no such lease {lease_id!r} (no ambient authority — a call needs a lease)")
        row, _uses = got

        if holder_agent_id != row.holder_agent_id:
            raise MCPAuthError("holder mismatch: this agent may not use this lease")

        if row.is_expired(t):
            raise MCPAuthError("lease has expired")

        if self.revocations.is_revoked(row.revocation_id):
            raise MCPAuthError("lease has been revoked")
        for rid in row.cap_chain_ids:
            if self.revocations.is_revoked(rid):
                raise MCPAuthError("backing capability has been revoked")

        want_aud = aud_scope(server_audience)
        if want_aud not in row.scope:
            raise MCPAuthError(
                f"lease is not bound to audience {server_audience!r} "
                f"(scope lacks {want_aud!r}; present an {server_audience}-scoped sub-lease)")

        if tool_scope(tool_name) not in row.scope and (SCOPE_TOOL_PREFIX + "*") not in row.scope:
            raise MCPAuthError(
                f"lease does not authorise tool {tool_name!r} "
                f"(scope lacks {tool_scope(tool_name)!r})")

        return VerifiedCall(lease_id=lease_id, holder_agent_id=holder_agent_id,
                            server_audience=server_audience, tool_name=tool_name,
                            scope=row.scope)

__all__ = [
    "MCPAuthError", "SCOPE_AUD_PREFIX", "SCOPE_TOOL_PREFIX", "aud_scope", "tool_scope",
    "generate_code_verifier", "code_challenge_for", "verify_pkce",
    "AccessGrant", "AuthorizationServer",
    "VerifiedCall", "mint_call_lease", "LeaseVerifier",
]
