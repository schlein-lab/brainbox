
from __future__ import annotations
import os, hmac, hashlib, struct, json
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature, InvalidTag

_RAW = serialization.Encoding.Raw
_PUB = serialization.PublicFormat.Raw
_PRIV = serialization.PrivateFormat.Raw
_NOENC = serialization.NoEncryption()

PROTOCOL = b"Brainarbeit-relay/Noise_XXsig_25519_ChaChaPoly_BLAKE2s/1"

def gen_x25519():
    sk = X25519PrivateKey.generate()
    return (sk.private_bytes(_RAW, _PRIV, _NOENC),
            sk.public_key().public_bytes(_RAW, _PUB))

def gen_ed25519():
    sk = Ed25519PrivateKey.generate()
    return (sk.private_bytes(_RAW, _PRIV, _NOENC),
            sk.public_key().public_bytes(_RAW, _PUB))

def ed_sign(priv_raw: bytes, msg: bytes) -> bytes:
    return Ed25519PrivateKey.from_private_bytes(priv_raw).sign(msg)

def ed_verify(pub_raw: bytes, sig: bytes, msg: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(pub_raw).verify(sig, msg)
        return True
    except (InvalidSignature, ValueError):
        return False

def _ecdh(priv_raw: bytes, peer_pub_raw: bytes) -> bytes:

    if not isinstance(peer_pub_raw, (bytes, bytearray)) or len(peer_pub_raw) != 32:
        raise CryptoError("invalid peer X25519 point (wrong length)")
    try:
        return X25519PrivateKey.from_private_bytes(priv_raw).exchange(
            X25519PublicKey.from_public_bytes(bytes(peer_pub_raw)))
    except CryptoError:
        raise
    except Exception as e:
        raise CryptoError(f"X25519 ECDH failed on a malformed peer point: {type(e).__name__}")

def _need(buf: bytes, n: int, what: str) -> bytes:

    if not isinstance(buf, (bytes, bytearray)) or len(buf) < n:
        raise CryptoError(f"malformed handshake message: {what} truncated "
                          f"(need {n} bytes, have {len(buf) if buf is not None else 0})")
    return bytes(buf[:n])

def from_hex(s, what: str = "frame") -> bytes:

    if not isinstance(s, str):
        raise CryptoError(f"{what} is not a hex string")
    try:
        return bytes.fromhex(s)
    except ValueError:
        raise CryptoError(f"{what} is not valid hex")

def loads_json(b, what: str = "payload"):

    try:
        return json.loads(b.decode())
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, AttributeError):
        raise CryptoError(f"{what} is not valid UTF-8 JSON")

def did_for(pub_raw: bytes) -> str:

    return "did:key:b2:" + hashlib.blake2s(pub_raw, digest_size=16).hexdigest()

def rendezvous_topic(appliance_pub_raw: bytes) -> str:

    return "rz_" + hashlib.blake2s(b"rendezvous|" + appliance_pub_raw,
                                   digest_size=16).hexdigest()

def _hkdf(chaining: bytes, ikm: bytes, n: int):

    tmp = hmac.new(chaining, ikm, hashlib.blake2s).digest()
    outs, prev = [], b""
    for i in range(1, n + 1):
        prev = hmac.new(tmp, prev + bytes([i]), hashlib.blake2s).digest()
        outs.append(prev)
    return outs

class _Symmetric:

    def __init__(self):
        self.ck = hashlib.blake2s(PROTOCOL).digest()
        self.h = self.ck

    def mix_hash(self, data: bytes):
        self.h = hashlib.blake2s(self.h + data).digest()

    def mix_key(self, ikm: bytes):
        self.ck, k = _hkdf(self.ck, ikm, 2)
        return k

    def split(self):

        return _hkdf(self.ck, b"", 2)

@dataclass
class Session:

    k_send: bytes
    k_recv: bytes
    h: bytes
    peer_static_pub: bytes
    _n_send: int = 0
    _n_recv: int = 0

    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = struct.pack("<Q", self._n_send) + b"\0\0\0\0"
        self._n_send += 1
        return ChaCha20Poly1305(self.k_send).encrypt(nonce, plaintext, self.h)

    def decrypt(self, ct: bytes) -> bytes:
        nonce = struct.pack("<Q", self._n_recv) + b"\0\0\0\0"
        try:
            pt = ChaCha20Poly1305(self.k_recv).decrypt(nonce, ct, self.h)
        except InvalidTag:

            raise CryptoError("AEAD authentication failed (tampered or wrong key)")
        self._n_recv += 1
        return pt

class CryptoError(Exception):
    pass

class Handshake:
    def __init__(self, *, initiator: bool, static_x_priv: bytes, static_x_pub: bytes,
                 id_ed_priv: bytes, id_ed_pub: bytes):
        self.initiator = initiator
        self.sx_priv, self.sx_pub = static_x_priv, static_x_pub
        self.id_priv, self.id_pub = id_ed_priv, id_ed_pub
        self.sym = _Symmetric()
        self.ex_priv, self.ex_pub = gen_x25519()
        self.peer_ex_pub = None
        self.peer_sx_pub = None
        self.peer_id_pub = None
        self.sym.mix_hash(b"")

    def write_msg1(self) -> bytes:
        self.sym.mix_hash(self.ex_pub)
        return self.ex_pub

    def read_msg2(self, msg: bytes):

        if not isinstance(msg, (bytes, bytearray)) or len(msg) != 32 + 48 + 32 + 64:
            raise CryptoError("malformed handshake msg2 (wrong total length)")
        off = 0

        self.peer_ex_pub = _need(msg[off:], 32, "msg2.e"); off += 32
        self.sym.mix_hash(self.peer_ex_pub)

        self.sym.mix_key(_ecdh(self.ex_priv, self.peer_ex_pub))

        k = self.sym.ck
        enc_s = _need(msg[off:], 32 + 16, "msg2.s"); off += 32 + 16
        self.peer_sx_pub = self._dec_static(enc_s)

        self.sym.mix_key(_ecdh(self.ex_priv, self.peer_sx_pub))

        self.peer_id_pub = _need(msg[off:], 32, "msg2.id_pub"); off += 32
        sig = _need(msg[off:], 64, "msg2.sig"); off += 64
        if not ed_verify(self.peer_id_pub, sig, self._auth_blob(self.peer_sx_pub)):
            raise CryptoError("responder identity signature invalid (box cannot be authed)")

    def write_msg3(self) -> bytes:

        enc_s = self._enc_static(self.sx_pub)
        self.sym.mix_key(_ecdh(self.sx_priv, self.peer_ex_pub))
        sig = ed_sign(self.id_priv, self._auth_blob(self.sx_pub))
        self.sym.mix_hash(self.id_pub + sig)
        return enc_s + self.id_pub + sig

    def read_msg1(self, msg: bytes):

        if not isinstance(msg, (bytes, bytearray)) or len(msg) != 32:
            raise CryptoError("malformed handshake msg1 (expected a 32-byte ephemeral pubkey)")
        self.peer_ex_pub = bytes(msg[:32])
        self.sym.mix_hash(self.peer_ex_pub)

    def write_msg2(self) -> bytes:

        self.sym.mix_hash(self.ex_pub)

        self.sym.mix_key(_ecdh(self.ex_priv, self.peer_ex_pub))

        enc_s = self._enc_static(self.sx_pub)

        self.sym.mix_key(_ecdh(self.sx_priv, self.peer_ex_pub))
        sig = ed_sign(self.id_priv, self._auth_blob(self.sx_pub))
        return self.ex_pub + enc_s + self.id_pub + sig

    def read_msg3(self, msg: bytes):

        if not isinstance(msg, (bytes, bytearray)) or len(msg) != 48 + 32 + 64:
            raise CryptoError("malformed handshake msg3 (wrong total length)")
        off = 0
        enc_s = _need(msg[off:], 32 + 16, "msg3.s"); off += 32 + 16
        self.peer_sx_pub = self._dec_static(enc_s)

        self.sym.mix_key(_ecdh(self.ex_priv, self.peer_sx_pub))
        self.peer_id_pub = _need(msg[off:], 32, "msg3.id_pub"); off += 32
        sig = _need(msg[off:], 64, "msg3.sig"); off += 64
        if not ed_verify(self.peer_id_pub, sig, self._auth_blob(self.peer_sx_pub)):
            raise CryptoError("initiator identity signature invalid (device cannot be authed)")
        self.sym.mix_hash(self.peer_id_pub + sig)

    def _auth_blob(self, static_pub: bytes) -> bytes:

        return self.sym.h + static_pub

    def _enc_static(self, static_pub: bytes) -> bytes:

        ct = ChaCha20Poly1305(self.sym.ck).encrypt(b"\0" * 12, static_pub, self.sym.h)
        self.sym.mix_hash(ct)
        return ct

    def _dec_static(self, ct: bytes) -> bytes:

        if not isinstance(ct, (bytes, bytearray)) or len(ct) != 32 + 16:
            raise CryptoError("handshake static-key field has the wrong length")
        try:
            pt = ChaCha20Poly1305(self.sym.ck).decrypt(b"\0" * 12, bytes(ct), self.sym.h)
        except InvalidTag:
            raise CryptoError("handshake static-key decryption failed")
        self.sym.mix_hash(ct)
        return pt

    def session(self) -> Session:
        k1, k2 = self.sym.split()

        if self.initiator:
            return Session(k_send=k1, k_recv=k2, h=self.sym.h,
                           peer_static_pub=self.peer_sx_pub)
        return Session(k_send=k2, k_recv=k1, h=self.sym.h,
                       peer_static_pub=self.peer_sx_pub)
