
from __future__ import annotations
import os, json, time, hashlib
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat, PrivateFormat, NoEncryption)

REQ_INFO = b"brainarbeit/deaddrop/req/v1"
RES_INFO = b"brainarbeit/deaddrop/res/v1"
APPROQ_INFO = b"brainarbeit/deaddrop/approq/v1"
APGRANT_INFO = b"brainarbeit/deaddrop/apgrant/v1"

CELLREQ_INFO = b"brainarbeit/cellseal/req/v1"
CELLRES_INFO = b"brainarbeit/cellseal/res/v1"
CELL_HEADER_KEYS = ("v", "topic", "rid", "device_id_pub", "cell_eph_pub", "nonce", "ts",
                    "counter", "task_type", "totp")
CELLRES_HEADER_KEYS = ("v", "rid", "cell_eph_pub", "nonce")
HEADER_KEYS = ("v", "topic", "rid", "device_id_pub", "eph_pub", "nonce", "ts", "counter")
APPROVAL_HEADER_KEYS = ("v", "topic", "approval_id", "box_eph_pub", "nonce", "ts")
GRANT_HEADER_KEYS = ("v", "topic", "approval_id", "device_id_pub", "eph_pub", "nonce", "ts", "counter")

def canonical(obj) -> bytes:

    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")

def _hkdf(ss: bytes, salt: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(ss)

def _xpub(priv: X25519PrivateKey) -> bytes:
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

def _edpub(priv: Ed25519PrivateKey) -> bytes:
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

def _topic(label: bytes, pub: bytes) -> str:
    return hashlib.blake2s(label + b"|" + pub, digest_size=16).hexdigest()

def inbox_topic(box_x_pub: bytes) -> str:
    return _topic(b"brainarbeit-deaddrop-inbox", box_x_pub)

def approvals_topic(device_x_pub: bytes) -> str:
    return _topic(b"brainarbeit-deaddrop-approvals", device_x_pub)

def grants_topic(box_x_pub: bytes) -> str:
    return _topic(b"brainarbeit-deaddrop-grants", box_x_pub)

def action_digest(action) -> str:

    return hashlib.sha256(canonical(action)).hexdigest()

def seal_request(box_x_pub, device_id_priv, topic, job, counter,
                 ts=None, rid=None, eph_priv=None, nonce=None):

    box_x_pub = bytes.fromhex(box_x_pub) if isinstance(box_x_pub, str) else box_x_pub
    idp = Ed25519PrivateKey.from_private_bytes(device_id_priv)
    device_id_pub = _edpub(idp)
    eph = (X25519PrivateKey.from_private_bytes(eph_priv) if eph_priv
           else X25519PrivateKey.generate())
    ss = eph.exchange(X25519PublicKey.from_public_bytes(box_x_pub))
    k_req = _hkdf(ss, box_x_pub, REQ_INFO)
    nonce = nonce or os.urandom(12)
    header = {"v": 1, "topic": topic, "rid": rid or os.urandom(16).hex(),
              "device_id_pub": device_id_pub.hex(), "eph_pub": _xpub(eph).hex(),
              "nonce": nonce.hex(), "ts": int(ts if ts is not None else time.time()),
              "counter": int(counter)}
    aad = canonical(header)
    ct = ChaCha20Poly1305(k_req).encrypt(nonce, canonical(job), aad)
    sig = idp.sign(aad + ct)
    env = dict(header); env["ct"] = ct.hex(); env["sig"] = sig.hex()
    return env, ss

class BadEnvelope(Exception):
    pass

def open_request(box_x_priv, box_x_pub, env):

    box_x_pub = bytes.fromhex(box_x_pub) if isinstance(box_x_pub, str) else box_x_pub
    try:
        header = {k: env[k] for k in HEADER_KEYS}
    except KeyError as e:
        raise BadEnvelope("missing field %s" % e)
    aad = canonical(header)
    try:
        ct = bytes.fromhex(env["ct"]); sig = bytes.fromhex(env["sig"])
        dev_pub = bytes.fromhex(env["device_id_pub"])
        Ed25519PublicKey.from_public_bytes(dev_pub).verify(sig, aad + ct)
    except Exception:
        raise BadEnvelope("signature verify failed")
    try:
        eph_pub = bytes.fromhex(env["eph_pub"])
        ss = X25519PrivateKey.from_private_bytes(box_x_priv).exchange(
            X25519PublicKey.from_public_bytes(eph_pub))
        k_req = _hkdf(ss, box_x_pub, REQ_INFO)
        pt = ChaCha20Poly1305(k_req).decrypt(bytes.fromhex(env["nonce"]), ct, aad)
        job = json.loads(pt)
    except Exception:
        raise BadEnvelope("decrypt failed")
    return env["device_id_pub"], job, ss, header

def seal_result(ss, box_x_pub, box_id_priv, rid, result, nonce=None):

    box_x_pub = bytes.fromhex(box_x_pub) if isinstance(box_x_pub, str) else box_x_pub
    k_res = _hkdf(ss, box_x_pub, RES_INFO)
    nonce = nonce or os.urandom(12)
    aad2 = canonical({"v": 1, "rid": rid, "nonce": nonce.hex()})
    ct2 = ChaCha20Poly1305(k_res).encrypt(nonce, canonical(result), aad2)
    box_sig = Ed25519PrivateKey.from_private_bytes(box_id_priv).sign(aad2 + ct2)
    return {"v": 1, "rid": rid, "nonce": nonce.hex(), "ct": ct2.hex(), "box_sig": box_sig.hex()}

def open_result(ss, box_x_pub, box_id_pub, renv):

    box_x_pub = bytes.fromhex(box_x_pub) if isinstance(box_x_pub, str) else box_x_pub
    box_id_pub = bytes.fromhex(box_id_pub) if isinstance(box_id_pub, str) else box_id_pub
    aad2 = canonical({"v": 1, "rid": renv["rid"], "nonce": renv["nonce"]})
    ct2 = bytes.fromhex(renv["ct"]); box_sig = bytes.fromhex(renv["box_sig"])
    Ed25519PublicKey.from_public_bytes(box_id_pub).verify(box_sig, aad2 + ct2)
    k_res = _hkdf(ss, box_x_pub, RES_INFO)
    return json.loads(ChaCha20Poly1305(k_res).decrypt(bytes.fromhex(renv["nonce"]), ct2, aad2))

def seal_approval_request(device_x_pub, box_id_priv, topic, approval,
                          box_eph_priv=None, nonce=None, ts=None):
    device_x_pub = bytes.fromhex(device_x_pub) if isinstance(device_x_pub, str) else device_x_pub
    beph = (X25519PrivateKey.from_private_bytes(box_eph_priv) if box_eph_priv
            else X25519PrivateKey.generate())
    ss = beph.exchange(X25519PublicKey.from_public_bytes(device_x_pub))
    k = _hkdf(ss, device_x_pub, APPROQ_INFO)
    nonce = nonce or os.urandom(12)
    header = {"v": 1, "topic": topic, "approval_id": approval["approval_id"],
              "box_eph_pub": _xpub(beph).hex(), "nonce": nonce.hex(),
              "ts": int(ts if ts is not None else time.time())}
    aad = canonical(header)
    ct = ChaCha20Poly1305(k).encrypt(nonce, canonical(approval), aad)
    box_sig = Ed25519PrivateKey.from_private_bytes(box_id_priv).sign(aad + ct)
    env = dict(header); env["ct"] = ct.hex(); env["box_sig"] = box_sig.hex()
    return env

def open_approval_request(device_x_priv, box_id_pub, env):

    box_id_pub = bytes.fromhex(box_id_pub) if isinstance(box_id_pub, str) else box_id_pub
    try:
        header = {k: env[k] for k in APPROVAL_HEADER_KEYS}
    except KeyError as e:
        raise BadEnvelope("missing field %s" % e)
    aad = canonical(header)
    try:
        ct = bytes.fromhex(env["ct"]); box_sig = bytes.fromhex(env["box_sig"])
        Ed25519PublicKey.from_public_bytes(box_id_pub).verify(box_sig, aad + ct)
    except Exception:
        raise BadEnvelope("box signature verify failed")
    try:
        dxpriv = X25519PrivateKey.from_private_bytes(device_x_priv)
        dx_pub = dxpriv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        ss = dxpriv.exchange(X25519PublicKey.from_public_bytes(bytes.fromhex(env["box_eph_pub"])))
        k = _hkdf(ss, dx_pub, APPROQ_INFO)
        pt = ChaCha20Poly1305(k).decrypt(bytes.fromhex(env["nonce"]), ct, aad)
        return json.loads(pt)
    except Exception:
        raise BadEnvelope("decrypt failed")

def seal_approval_grant(box_x_pub, device_id_priv, topic, grant, counter,
                        eph_priv=None, nonce=None, ts=None):
    box_x_pub = bytes.fromhex(box_x_pub) if isinstance(box_x_pub, str) else box_x_pub
    idp = Ed25519PrivateKey.from_private_bytes(device_id_priv)
    eph = (X25519PrivateKey.from_private_bytes(eph_priv) if eph_priv
           else X25519PrivateKey.generate())
    ss = eph.exchange(X25519PublicKey.from_public_bytes(box_x_pub))
    k = _hkdf(ss, box_x_pub, APGRANT_INFO)
    nonce = nonce or os.urandom(12)
    header = {"v": 1, "topic": topic, "approval_id": grant["approval_id"],
              "device_id_pub": _edpub(idp).hex(), "eph_pub": _xpub(eph).hex(),
              "nonce": nonce.hex(), "ts": int(ts if ts is not None else time.time()),
              "counter": int(counter)}
    aad = canonical(header)
    ct = ChaCha20Poly1305(k).encrypt(nonce, canonical(grant), aad)
    sig = idp.sign(aad + ct)
    env = dict(header); env["ct"] = ct.hex(); env["sig"] = sig.hex()
    return env

def open_approval_grant(box_x_priv, box_x_pub, env):

    box_x_pub = bytes.fromhex(box_x_pub) if isinstance(box_x_pub, str) else box_x_pub
    try:
        header = {k: env[k] for k in GRANT_HEADER_KEYS}
    except KeyError as e:
        raise BadEnvelope("missing field %s" % e)
    aad = canonical(header)
    try:
        ct = bytes.fromhex(env["ct"]); sig = bytes.fromhex(env["sig"])
        dev_pub = bytes.fromhex(env["device_id_pub"])
        Ed25519PublicKey.from_public_bytes(dev_pub).verify(sig, aad + ct)
    except Exception:
        raise BadEnvelope("signature verify failed")
    try:
        ss = X25519PrivateKey.from_private_bytes(box_x_priv).exchange(
            X25519PublicKey.from_public_bytes(bytes.fromhex(env["eph_pub"])))
        k = _hkdf(ss, box_x_pub, APGRANT_INFO)
        pt = ChaCha20Poly1305(k).decrypt(bytes.fromhex(env["nonce"]), ct, aad)
        return env["device_id_pub"], json.loads(pt)
    except Exception:
        raise BadEnvelope("decrypt failed")

def gen_cell_keys():

    cx = X25519PrivateKey.generate()
    ci = Ed25519PrivateKey.generate()
    return (cx.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()), _xpub(cx),
            ci.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()), _edpub(ci))

def seal_cell_request(cell_x_pub, device_id_priv, topic, task_type, inner, counter,
                      ts=None, rid=None, eph_priv=None, nonce=None, totp=""):

    cell_x_pub = bytes.fromhex(cell_x_pub) if isinstance(cell_x_pub, str) else cell_x_pub
    idp = Ed25519PrivateKey.from_private_bytes(device_id_priv)
    eph = (X25519PrivateKey.from_private_bytes(eph_priv) if eph_priv else X25519PrivateKey.generate())
    ss = eph.exchange(X25519PublicKey.from_public_bytes(cell_x_pub))
    k = _hkdf(ss, cell_x_pub, CELLREQ_INFO)
    nonce = nonce or os.urandom(12)
    header = {"v": 2, "topic": topic, "rid": rid or os.urandom(16).hex(),
              "device_id_pub": _edpub(idp).hex(), "cell_eph_pub": _xpub(eph).hex(),
              "nonce": nonce.hex(), "ts": int(ts if ts is not None else time.time()),
              "counter": int(counter), "task_type": task_type, "totp": totp}
    aad = canonical(header)
    ct = ChaCha20Poly1305(k).encrypt(nonce, canonical(inner), aad)
    sig = idp.sign(aad + ct)
    env = dict(header); env["ct"] = ct.hex(); env["sig"] = sig.hex()
    return env

def box_verify(env):

    try:
        header = {k: env[k] for k in CELL_HEADER_KEYS}
    except KeyError as e:
        raise BadEnvelope("missing field %s" % e)
    aad = canonical(header)
    try:
        ct = bytes.fromhex(env["ct"]); sig = bytes.fromhex(env["sig"])
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(env["device_id_pub"])).verify(sig, aad + ct)
    except Exception:
        raise BadEnvelope("signature verify failed")
    return header

def cell_open(cell_x_priv, cell_x_pub, env):

    cell_x_pub = bytes.fromhex(cell_x_pub) if isinstance(cell_x_pub, str) else cell_x_pub
    header = {k: env[k] for k in CELL_HEADER_KEYS}
    aad = canonical(header)
    try:
        ss = X25519PrivateKey.from_private_bytes(cell_x_priv).exchange(
            X25519PublicKey.from_public_bytes(bytes.fromhex(env["cell_eph_pub"])))
        k = _hkdf(ss, cell_x_pub, CELLREQ_INFO)
        return json.loads(ChaCha20Poly1305(k).decrypt(bytes.fromhex(env["nonce"]), bytes.fromhex(env["ct"]), aad))
    except Exception:
        raise BadEnvelope("cell decrypt failed")

def cell_seal_result(device_x_pub, cell_id_priv, rid, result, eph_priv=None, nonce=None):

    device_x_pub = bytes.fromhex(device_x_pub) if isinstance(device_x_pub, str) else device_x_pub
    eph = (X25519PrivateKey.from_private_bytes(eph_priv) if eph_priv else X25519PrivateKey.generate())
    ss = eph.exchange(X25519PublicKey.from_public_bytes(device_x_pub))
    k = _hkdf(ss, device_x_pub, CELLRES_INFO)
    nonce = nonce or os.urandom(12)
    header = {"v": 2, "rid": rid, "cell_eph_pub": _xpub(eph).hex(), "nonce": nonce.hex()}
    aad = canonical(header)
    ct = ChaCha20Poly1305(k).encrypt(nonce, canonical(result), aad)
    cell_sig = Ed25519PrivateKey.from_private_bytes(cell_id_priv).sign(aad + ct)
    env = dict(header); env["ct"] = ct.hex(); env["cell_sig"] = cell_sig.hex()
    return env

def device_open_result(device_x_priv, device_x_pub, cell_id_pub, env):

    device_x_pub = bytes.fromhex(device_x_pub) if isinstance(device_x_pub, str) else device_x_pub
    cell_id_pub = bytes.fromhex(cell_id_pub) if isinstance(cell_id_pub, str) else cell_id_pub
    header = {k: env[k] for k in CELLRES_HEADER_KEYS}
    aad = canonical(header)
    ct = bytes.fromhex(env["ct"]); cell_sig = bytes.fromhex(env["cell_sig"])
    Ed25519PublicKey.from_public_bytes(cell_id_pub).verify(cell_sig, aad + ct)
    ss = X25519PrivateKey.from_private_bytes(device_x_priv).exchange(
        X25519PublicKey.from_public_bytes(bytes.fromhex(env["cell_eph_pub"])))
    k = _hkdf(ss, device_x_pub, CELLRES_INFO)
    return json.loads(ChaCha20Poly1305(k).decrypt(bytes.fromhex(env["nonce"]), ct, aad))
