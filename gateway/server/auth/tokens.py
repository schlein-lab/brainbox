
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class Credential:
    device_did: str
    durable_token: str
    twofa_code: str | None

class AuthError(Exception):

    def __init__(self, msg: str, *, need_2fa: bool = False):
        super().__init__(msg)
        self.need_2fa = need_2fa

def parse_credential(headers) -> Credential:

    authz = (headers.get("Authorization") or "").strip()
    if not authz.lower().startswith("bearer "):
        raise AuthError("missing or malformed Authorization: Bearer header")
    bearer = authz[7:].strip()
    if "." not in bearer:
        raise AuthError("bearer must be '<device_did>.<durable_token>'")
    did, _, token = bearer.partition(".")
    did, token = did.strip(), token.strip()
    if not did or not token:
        raise AuthError("bearer must be '<device_did>.<durable_token>'")
    code = (headers.get("X-Brainarbeit-2FA") or headers.get("X-Brainarbeit-2fa") or "").strip() or None
    return Credential(device_did=did, durable_token=token, twofa_code=code)
