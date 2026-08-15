
from __future__ import annotations
import re, hashlib, hmac, os

_SALT = os.urandom(16)

_PATTERNS = [

    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9\-_]{8,}")),

    ("sk-key",    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{14,}\b")),

    ("stripe",    re.compile(r"\b(?:sk|rk|pk)_live_[A-Za-z0-9]{16,}\b")),

    ("google",    re.compile(r"\bAIza[A-Za-z0-9_\-]{30,}\b")),

    ("slack",     re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),

    ("telegram",  re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")),

    ("github",    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("github-pat",re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),

    ("aws",       re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),

    ("bearer",    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]{20,}=*")),

    ("pem",       re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
                             r".*?-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
                             re.DOTALL)),

    ("env-secret",re.compile(r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CRED)[A-Z0-9_]*)\s*[=:]\s*"
                             r"(['\"]?)([^\s'\"]{8,})\2")),
]

_known: dict[str, int] = {}

def register_secret(value: str | None) -> None:

    if not value or len(value) < 6:
        return
    h = hmac.new(_SALT, value.encode(), hashlib.sha256).hexdigest()
    _known[h] = len(value)

def _mask(kind: str, n: int) -> str:
    return f"‹{kind}:redacted×{n}›"

def redact(text: str) -> str:

    if not text:
        return text
    out = text

    if _known:

        def _tok_sub(m):
            tok = m.group(0)
            h = hmac.new(_SALT, tok.encode(), hashlib.sha256).hexdigest()
            if h in _known:
                return _mask("known", _known[h])
            return tok

        out = re.sub(r"[A-Za-z0-9\-._~+/=:]{6,}", _tok_sub, out)

    for kind, pat in _PATTERNS:
        if kind == "env-secret":
            out = pat.sub(lambda m: f"{m.group(1)}{'=' if '=' in m.group(0) else ':'}"
                                    f"{_mask('env', len(m.group(3)))}", out)
        else:
            out = pat.sub(lambda m, k=kind: _mask(k, len(m.group(0))), out)
    return out

def redact_obj(obj):

    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    return obj
