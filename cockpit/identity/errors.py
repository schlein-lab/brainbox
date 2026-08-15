

from __future__ import annotations

ERR_CONTRACT_MISMATCH = "ERR_CONTRACT_MISMATCH"
ERR_NOT_ENROLLED = "ERR_NOT_ENROLLED"
ERR_NOT_APPROVED = "ERR_NOT_APPROVED"
ERR_PRINCIPAL_MISMATCH = "ERR_PRINCIPAL_MISMATCH"
ERR_BAD_SIG = "ERR_BAD_SIG"
ERR_STALE = "ERR_STALE"
ERR_REPLAY = "ERR_REPLAY"

ERR_ENROLL_CONFLICT = "ERR_ENROLL_CONFLICT"
ERR_ENROLL_DENIED = "ERR_ENROLL_DENIED"
ERR_DELEGATE_DISABLED = "ERR_DELEGATE_DISABLED"
ERR_NOT_OWNER = "ERR_NOT_OWNER"
ERR_BAD_REQUEST = "ERR_BAD_REQUEST"

class AuthError(Exception):

    def __init__(self, code: str, message: str = "", detail: dict | None = None):
        self.code = code
        self.message = message or code
        self.detail = detail or {}
        super().__init__(f"{code}: {self.message}")

    def to_error(self) -> dict:

        out = {"code": self.code, "message": self.message}
        if self.detail:
            out["detail"] = self.detail
        return out
