
from __future__ import annotations
import hmac, hashlib, struct, base64, secrets, time

STEP_S = 30
DIGITS = 6
ALGO = "SHA1"
_DIGEST = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}

def gen_secret(nbytes: int = 20) -> str:

    if nbytes < 20:
        raise ValueError("TOTP secret must be >= 160 bits (20 bytes)")
    return base64.b32encode(secrets.token_bytes(nbytes)).decode().rstrip("=")

def _b32decode(secret_b32: str) -> bytes:
    s = secret_b32.strip().replace(" ", "").upper()
    s += "=" * ((8 - len(s) % 8) % 8)
    return base64.b32decode(s, casefold=True)

def counter_at(ts: float | None = None, step_s: int = STEP_S) -> int:

    return int((ts if ts is not None else time.time()) // step_s)

def _hotp(secret: bytes, counter: int, digits: int = DIGITS, algo: str = ALGO) -> str:

    mac = hmac.new(secret, struct.pack(">Q", counter), _DIGEST[algo]).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)

def code_at(secret_b32: str, ts: float | None = None, *, digits: int = DIGITS,
            step_s: int = STEP_S, algo: str = ALGO) -> str:

    return _hotp(_b32decode(secret_b32), counter_at(ts, step_s), digits, algo)

def verify(secret_b32: str, code: str, *, ts: float | None = None, window: int = 1,
           last_counter: int = -1, digits: int = DIGITS, step_s: int = STEP_S,
           algo: str = ALGO) -> tuple[bool, int]:

    if not isinstance(code, str) or not code.isdigit() or len(code) != digits:
        return (False, last_counter)
    try:
        secret = _b32decode(secret_b32)
    except Exception:
        return (False, last_counter)
    now_ctr = counter_at(ts, step_s)

    for c in range(now_ctr - window, now_ctr + window + 1):
        if c <= last_counter:
            continue
        if hmac.compare_digest(_hotp(secret, c, digits, algo), code):
            return (True, c)
    return (False, last_counter)

def provisioning_uri(secret_b32: str, principal: str, *, issuer: str = "Brainarbeit",
                     digits: int = DIGITS, step_s: int = STEP_S, algo: str = ALGO) -> str:

    from urllib.parse import quote
    label = quote(f"{issuer}:{principal}")
    q = (f"secret={secret_b32}&issuer={quote(issuer)}&algorithm={algo}"
         f"&digits={digits}&period={step_s}")
    return f"otpauth://totp/{label}?{q}"

class SecondFactor:
    kind = "none"
    def verify(self, code: str, *, last_counter: int = -1) -> tuple[bool, int]:
        raise NotImplementedError

class TotpFactor(SecondFactor):
    kind = "totp"
    def __init__(self, secret_b32: str):
        self.secret = secret_b32
    def verify(self, code: str, *, last_counter: int = -1) -> tuple[bool, int]:
        return verify(self.secret, code, last_counter=last_counter)
