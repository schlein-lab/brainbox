
from __future__ import annotations
import os
import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes, serialization

_CURVE = ec.SECP256R1()
_INFO = b"brainarbeit-zkchannel-v1"

def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")

def _b64d(s: str) -> bytes:
    return base64.b64decode(s)

def _raw_pub(pubkey) -> bytes:
    return pubkey.public_bytes(serialization.Encoding.X962,
                               serialization.PublicFormat.UncompressedPoint)

def _load_pub(raw: bytes):
    return ec.EllipticCurvePublicKey.from_encoded_point(_CURVE, raw)

def _derive(shared: bytes, salt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=_INFO).derive(shared)

def gen_ephemeral():

    priv = ec.generate_private_key(_CURVE)
    return priv, _b64e(_raw_pub(priv.public_key()))

def open_sealed(priv, sealed: dict) -> bytes:

    epk = _b64d(sealed["epk"])
    peer = _load_pub(epk)
    shared = priv.exchange(ec.ECDH(), peer)
    my_pub_raw = _raw_pub(priv.public_key())
    key = _derive(shared, my_pub_raw + epk)
    return AESGCM(key).decrypt(_b64d(sealed["iv"]), _b64d(sealed["ct"]), None)

def seal_to(peer_pub_raw_b64: str, plaintext: bytes) -> dict:

    peer_raw = _b64d(peer_pub_raw_b64)
    peer = _load_pub(peer_raw)
    eph = ec.generate_private_key(_CURVE)
    shared = eph.exchange(ec.ECDH(), peer)
    epk_raw = _raw_pub(eph.public_key())
    key = _derive(shared, peer_raw + epk_raw)
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, plaintext, None)
    return {"epk": _b64e(epk_raw), "iv": _b64e(iv), "ct": _b64e(ct)}

if __name__ == "__main__":
    import sys, json
    if len(sys.argv) >= 4 and sys.argv[1] == "seal":
        print(json.dumps(seal_to(sys.argv[2], sys.argv[3].encode("utf-8"))))
