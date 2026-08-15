
from __future__ import annotations
from dataclasses import dataclass

from .tokens import Credential, AuthError
from . import twofactor as _totp

@dataclass
class Principal:
    device_did: str
    principal: str
    caps: set
    label: str | None = None

def _load_registry():
    try:
        from relaylib import registry as R
        return R
    except Exception:
        from . import registry_shim as R
        return R

class Authenticator:

    def __init__(self, relay_db: str, *, require_2fa: bool = True):
        self.relay_db = relay_db
        self.require_2fa = require_2fa
        self.R = _load_registry()

    def _cx(self):
        return self.R.connect(self.relay_db)

    def authenticate(self, cred: Credential) -> Principal:
        cx = self._cx()
        try:

            al = self.R.alliance_for_token(cx, cred.device_did, cred.durable_token)
            if not al:
                raise AuthError("invalid credential (unknown device, bad token, or revoked)")
            principal = al["principal"]

            if self.require_2fa:
                if not self.R.has_2fa(cx, principal):
                    raise AuthError(f"principal {principal} has no armed second factor; "
                                    "2FA is mandatory for off-LAN/API access", need_2fa=True)
                if not cred.twofa_code:
                    raise AuthError("missing X-Brainarbeit-2FA code (2FA is mandatory)",
                                    need_2fa=True)
                ok, reason = self.R.verify_2fa(cx, principal, cred.twofa_code)
                if not ok:
                    raise AuthError(f"2FA rejected: {reason}", need_2fa=True)

            ok, reason = self.R.check_and_record_rate(cx, cred.device_did)
            if not ok:
                raise RateLimited(reason)

            caps = self.R.caps_ceiling(cx, cred.device_did)
            return Principal(device_did=cred.device_did, principal=principal,
                             caps=caps, label=al.get("label"))
        finally:
            try:
                cx.close()
            except Exception:
                pass

class RateLimited(Exception):
    pass
