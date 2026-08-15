

from __future__ import annotations

import base64
import hashlib
import math
from typing import Any

CONTRACT_VERSION = "portal-contract/1"

def b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

def b64u_decode(txt: str) -> bytes:
    pad = "=" * (-len(txt) % 4)
    return base64.urlsafe_b64decode(txt + pad)

def key_id(pubkey: bytes) -> str:
    if len(pubkey) != 32:
        raise ValueError("ed25519 public key must be 32 bytes")
    digest = hashlib.sha256(pubkey).digest()[:16]
    kid = b64u_encode(digest)
    assert len(kid) == 22, f"key_id must be 22 chars, got {len(kid)}"
    return kid

def _canon_string(s: str) -> str:

    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 0x20:
            out.append("\\u%04x" % o)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)

def _canon_number(n: Any) -> str:
    if isinstance(n, bool):
        raise TypeError("bool is not a JSON number")
    if isinstance(n, int):
        return str(n)
    if isinstance(n, float):
        if math.isnan(n) or math.isinf(n):
            raise ValueError("NaN/Infinity are not valid JSON numbers (JCS §3.2.2.3)")
        if n == 0.0:
            return "0"
        mag = abs(n)

        if mag >= 1e21 or mag < 1e-6:
            raise ValueError(
                "JCS exponential-form numbers unsupported in Phase 1 "
                "(reject rather than mis-canonicalize); see TODO(jcs-exp)"
            )
        if n == int(n):
            return str(int(n))
        return repr(n)
    raise TypeError(f"not a JSON number: {n!r}")

def jcs_canonical_bytes(obj: Any) -> bytes:

    out: list[str] = []
    _emit(obj, out)
    return "".join(out).encode("utf-8")

def _emit(obj: Any, out: list) -> None:
    if obj is None:
        out.append("null")
    elif obj is True:
        out.append("true")
    elif obj is False:
        out.append("false")
    elif isinstance(obj, str):
        out.append(_canon_string(obj))
    elif isinstance(obj, (int, float)):
        out.append(_canon_number(obj))
    elif isinstance(obj, (list, tuple)):
        out.append("[")
        for i, v in enumerate(obj):
            if i:
                out.append(",")
            _emit(v, out)
        out.append("]")
    elif isinstance(obj, dict):

        items = sorted(obj.items(), key=lambda kv: _utf16_key(kv[0]))
        out.append("{")
        for i, (k, v) in enumerate(items):
            if not isinstance(k, str):
                raise TypeError("JSON object keys must be strings")
            if i:
                out.append(",")
            out.append(_canon_string(k))
            out.append(":")
            _emit(v, out)
        out.append("}")
    else:
        raise TypeError(f"not JSON-serializable for JCS: {type(obj).__name__}")

def _utf16_key(s: str):

    return s.encode("utf-16-be")

def args_digest(args: Any) -> str:

    return hashlib.sha256(jcs_canonical_bytes(args if args is not None else {})).hexdigest()

_SIGN_FIELDS = ("type", "id", "ts", "nonce", "principal", "key_id", "funding", "verb")

def signing_string(env: dict) -> bytes:

    lines = [CONTRACT_VERSION]
    for f in _SIGN_FIELDS:
        if f not in env:
            raise KeyError(f"envelope missing required signed field: {f!r}")
        v = env[f]
        if f == "ts":
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError("ts must be an integer (ms epoch)")
            lines.append(str(v))
        else:
            if not isinstance(v, str):
                raise TypeError(f"signed field {f!r} must be a string")
            if "\n" in v:
                raise ValueError(f"signed field {f!r} must not contain newline")
            lines.append(v)
    lines.append(args_digest(env.get("args", {})))
    return "\n".join(lines).encode("utf-8")
