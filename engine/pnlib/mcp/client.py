
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pnlib.mcp.auth import (
    AuthorizationServer, AccessGrant, generate_code_verifier, code_challenge_for,
)

ALLOW_UNTRUSTED_SERVER_EXECUTION = False

class MCPClientError(Exception):
    pass

class UntrustedServerError(MCPClientError):
    pass

@dataclass(frozen=True)
class TrustedServer:
    audience: str
    note: str = ""

class TrustedServerRegistry:

    def __init__(self) -> None:
        self._servers: dict[str, TrustedServer] = {}

    def trust(self, audience: str, *, note: str = "") -> None:
        if not isinstance(audience, str) or not audience:
            raise MCPClientError("server audience must be a non-empty string")
        self._servers[audience] = TrustedServer(audience, note)

    def is_trusted(self, audience: str) -> bool:
        return audience in self._servers

    def __contains__(self, audience: str) -> bool:
        return self.is_trusted(audience)

class MCPClient:

    def __init__(self, trusted: TrustedServerRegistry, *,
                 authorizer: Optional[AuthorizationServer] = None) -> None:
        self.trusted = trusted
        self.authorizer = authorizer if authorizer is not None else AuthorizationServer()

    def _guard_trust(self, audience: str) -> None:
        if self.trusted.is_trusted(audience):
            return
        if ALLOW_UNTRUSTED_SERVER_EXECUTION:

            return
        raise UntrustedServerError(
            f"server {audience!r} is not trusted; refusing to contact it "
            f"(untrusted-server execution is disabled)")

    def connect(self, server, *, scope, now=None) -> AccessGrant:

        audience = getattr(server, "audience", None)
        if audience is None:
            raise MCPClientError("server object exposes no .audience")
        self._guard_trust(audience)
        verifier = generate_code_verifier()
        challenge = code_challenge_for(verifier, "S256")
        code = self.authorizer.authorize(challenge=challenge, audience=audience,
                                          scope=scope, method="S256", now=now)
        return self.authorizer.exchange(code=code, verifier=verifier, now=now)

    def call(self, server, tool_name: str, args: dict, *, lease, holder_agent_id: str,
             now=None):

        audience = getattr(server, "audience", None)
        if audience is None:
            raise MCPClientError("server object exposes no .audience")
        self._guard_trust(audience)
        if not hasattr(server, "call"):
            raise MCPClientError("server object exposes no .call(...)")
        return server.call(tool_name, args, lease=lease,
                           holder_agent_id=holder_agent_id, now=now)

__all__ = [
    "ALLOW_UNTRUSTED_SERVER_EXECUTION", "MCPClientError", "UntrustedServerError",
    "TrustedServer", "TrustedServerRegistry", "MCPClient",
]
