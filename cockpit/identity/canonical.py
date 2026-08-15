

from __future__ import annotations

import base64
import hashlib
import math
import struct
from typing import Any

CONTRACT_VERSION = "portal-contract/1"

ED25519_PUBLIC_LEN = 32
ED25519_SIG_LEN = 64

def b64url_encode(raw: bytes) -> str:

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

def b64url_decode(s: str) -> bytes:

    if not isinstance(s, str):
        raise ValueError("base64url input must be str")
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)

def key_id_from_pubkey(raw_pubkey: bytes) -> str:

    if len(raw_pubkey) != ED25519_PUBLIC_LEN:
        raise ValueError(
            f"ed25519 public key must be {ED25519_PUBLIC_LEN} bytes, got {len(raw_pubkey)}"
        )
    digest = hashlib.sha256(raw_pubkey).digest()[:16]
    return b64url_encode(digest)

def _jcs_number(n: float | int) -> str:

    if isinstance(n, bool):
        raise ValueError("bool must be serialized as true/false, not a number")
    if isinstance(n, int):
        return str(n)
    if not math.isfinite(n):
        raise ValueError("NaN and Infinity are not valid JSON numbers")
    if n == 0:

        return "0"

    if n == int(n) and abs(n) < 1e21:
        return str(int(n))
    r = repr(n)
    return r

def _jcs_string(s: str) -> str:

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

def _utf16_sort_key(s: str) -> tuple:

    b = s.encode("utf-16-be")
    return struct.unpack(f">{len(b) // 2}H", b)

def _jcs_serialize(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _jcs_string(value)
    if isinstance(value, (int, float)):
        return _jcs_number(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_jcs_serialize(v) for v in value) + "]"
    if isinstance(value, dict):
        for k in value:
            if not isinstance(k, str):
                raise ValueError("JCS object keys must be strings")
        items = sorted(value.items(), key=lambda kv: _utf16_sort_key(kv[0]))
        return (
            "{"
            + ",".join(_jcs_string(k) + ":" + _jcs_serialize(v) for k, v in items)
            + "}"
        )
    raise ValueError(f"value of type {type(value).__name__} is not JSON-serializable")

def jcs_canonical_bytes(value: Any) -> bytes:

    return _jcs_serialize(value).encode("utf-8")

def args_digest_hex(args: Any) -> str:

    return hashlib.sha256(jcs_canonical_bytes(args if args is not None else {})).hexdigest()

def build_signing_string(
    *,
    type: str,
    id: str,
    ts: int,
    nonce: str,
    principal: str,
    key_id: str,
    funding: str,
    verb: str,
    args: Any,
    contract: str = CONTRACT_VERSION,
) -> bytes:

    for name, val in (
        ("type", type),
        ("id", id),
        ("nonce", nonce),
        ("principal", principal),
        ("key_id", key_id),
        ("funding", funding),
        ("verb", verb),
        ("contract", contract),
    ):
        if not isinstance(val, str):
            raise ValueError(f"signing-string field '{name}' must be str, got {type_of(val)}")
        if "\n" in val:

            raise ValueError(f"signing-string field '{name}' must not contain a newline")
    if not isinstance(ts, int) or isinstance(ts, bool):
        raise ValueError("ts must be an int (ms epoch)")

    lines = [
        contract,
        type,
        id,
        str(ts),
        nonce,
        principal,
        key_id,
        funding,
        verb,
        args_digest_hex(args),
    ]
    return "\n".join(lines).encode("utf-8")

def type_of(v: Any) -> str:
    return type(v).__name__
