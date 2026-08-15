

from __future__ import annotations

import hashlib
import os
from typing import Callable, Tuple

_b = 256
_q = 2 ** 255 - 19
_l = 2 ** 252 + 27742317777372353535851937790883648493

def _H(m: bytes) -> bytes:
    return hashlib.sha512(m).digest()

def _expmod(b: int, e: int, m: int) -> int:
    return pow(b, e, m)

def _inv(x: int) -> int:
    return _expmod(x, _q - 2, _q)

_d = -121665 * _inv(121666) % _q
_I = _expmod(2, (_q - 1) // 4, _q)

def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(_d * y * y + 1)
    x = _expmod(xx, (_q + 3) // 8, _q)
    if (x * x - xx) % _q != 0:
        x = (x * _I) % _q
    if x % 2 != 0:
        x = _q - x
    return x

_By = 4 * _inv(5) % _q
_Bx = _xrecover(_By)
_B = (_Bx % _q, _By % _q, 1, (_Bx * _By) % _q)

def _edwards_add(P, Q):
    (x1, y1, z1, t1) = P
    (x2, y2, z2, t2) = Q
    a = (y1 - x1) * (y2 - x2) % _q
    b = (y1 + x1) * (y2 + x2) % _q
    c = t1 * 2 * _d * t2 % _q
    dd = z1 * 2 * z2 % _q
    e = b - a
    f = dd - c
    g = dd + c
    h = b + a
    x3 = e * f
    y3 = g * h
    t3 = e * h
    z3 = f * g
    return (x3 % _q, y3 % _q, z3 % _q, t3 % _q)

def _scalarmult(P, e: int):
    if e == 0:
        return (0, 1, 1, 0)
    Q = _scalarmult(P, e // 2)
    Q = _edwards_add(Q, Q)
    if e & 1:
        Q = _edwards_add(Q, P)
    return Q

def _encodeint(y: int) -> bytes:
    return y.to_bytes(_b // 8, "little")

def _encodepoint(P) -> bytes:
    (x, y, z, t) = P
    zi = _inv(z)
    x = (x * zi) % _q
    y = (y * zi) % _q
    bits = [(y >> i) & 1 for i in range(_b - 1)] + [x & 1]
    return bytes(
        sum(bits[i * 8 + j] << j for j in range(8)) for i in range(_b // 8)
    )

def _bit(h: bytes, i: int) -> int:
    return (h[i // 8] >> (i % 8)) & 1

def _pure_pub_from_priv(sk: bytes) -> bytes:
    h = _H(sk)
    a = 2 ** (_b - 2) + sum(2 ** i * _bit(h, i) for i in range(3, _b - 2))
    A = _scalarmult(_B, a)
    return _encodepoint(A)

def _pure_sign(sk: bytes, m: bytes) -> bytes:
    h = _H(sk)
    a = 2 ** (_b - 2) + sum(2 ** i * _bit(h, i) for i in range(3, _b - 2))
    pub = _encodepoint(_scalarmult(_B, a))
    r = int.from_bytes(_H(h[_b // 8:_b // 4] + m), "little")
    R = _scalarmult(_B, r)
    Renc = _encodepoint(R)
    k = int.from_bytes(_H(Renc + pub + m), "little")
    s = (r + k * a) % _l
    return Renc + _encodeint(s)

def _decodeint(s: bytes) -> int:
    return int.from_bytes(s, "little")

def _decodepoint(s: bytes):
    y = int.from_bytes(s, "little") & ((1 << (_b - 1)) - 1)
    x = _xrecover(y)
    if x & 1 != _bit(s, _b - 1):
        x = _q - x
    P = (x % _q, y % _q, 1, (x * y) % _q)
    if not _isoncurve(P):
        raise ValueError("decoding point that is not on curve")
    return P

def _isoncurve(P) -> bool:
    (x, y, z, t) = P
    return (
        z % _q != 0
        and (x * y % _q) == (z * t % _q)
        and (y * y - x * x - z * z - _d * t * t) % _q == 0
    )

def _pure_verify(pub: bytes, sig: bytes, m: bytes) -> bool:
    if len(sig) != 64 or len(pub) != 32:
        return False
    try:
        R = _decodepoint(sig[:32])
        A = _decodepoint(pub)
    except Exception:
        return False
    s = _decodeint(sig[32:])
    if s >= _l:
        return False
    k = int.from_bytes(_H(sig[:32] + pub + m), "little")

    lhs = _encodepoint(_scalarmult(_B, s))
    rhs = _encodepoint(_edwards_add(R, _scalarmult(A, k)))
    return lhs == rhs

def _pure_keygen() -> Tuple[bytes, bytes]:
    sk = os.urandom(32)
    return sk, _pure_pub_from_priv(sk)

_HAVE_CRYPTO = False
try:
    from cryptography.hazmat.primitives.asymmetric import ed25519 as _ced
    from cryptography.exceptions import InvalidSignature as _InvalidSignature

    _HAVE_CRYPTO = True
except Exception:
    _HAVE_CRYPTO = False

def _crypto_keygen() -> Tuple[bytes, bytes]:
    priv = _ced.Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization as _ser

    raw_priv = priv.private_bytes(
        _ser.Encoding.Raw, _ser.PrivateFormat.Raw, _ser.NoEncryption()
    )
    raw_pub = priv.public_key().public_bytes(
        _ser.Encoding.Raw, _ser.PublicFormat.Raw
    )
    return raw_priv, raw_pub

def _crypto_pub_from_priv(sk: bytes) -> bytes:
    from cryptography.hazmat.primitives import serialization as _ser

    priv = _ced.Ed25519PrivateKey.from_private_bytes(sk)
    return priv.public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw)

def _crypto_sign(sk: bytes, m: bytes) -> bytes:
    return _ced.Ed25519PrivateKey.from_private_bytes(sk).sign(m)

def _crypto_verify(pub: bytes, sig: bytes, m: bytes) -> bool:
    try:
        _ced.Ed25519PublicKey.from_public_bytes(pub).verify(sig, m)
        return True
    except (_InvalidSignature, ValueError):
        return False

keygen: Callable[[], Tuple[bytes, bytes]]
pub_from_priv: Callable[[bytes], bytes]
sign: Callable[[bytes, bytes], bytes]
verify: Callable[[bytes, bytes, bytes], bool]
BACKEND: str

def use(name: str) -> str:

    global keygen, pub_from_priv, sign, verify, BACKEND
    if name == "cryptography":
        if not _HAVE_CRYPTO:
            raise RuntimeError("cryptography backend requested but not installed")
        keygen, pub_from_priv, sign, verify = (
            _crypto_keygen,
            _crypto_pub_from_priv,
            _crypto_sign,
            _crypto_verify,
        )
        BACKEND = "cryptography"
    elif name == "pure-python":
        keygen, pub_from_priv, sign, verify = (
            _pure_keygen,
            _pure_pub_from_priv,
            _pure_sign,
            _pure_verify,
        )
        BACKEND = "pure-python"
    else:
        raise ValueError(f"unknown backend {name!r}")
    return BACKEND

_pref = os.environ.get("BRAINARBEIT_ED25519_BACKEND")
if _pref:
    use(_pref)
elif _HAVE_CRYPTO:
    use("cryptography")
else:
    use("pure-python")
