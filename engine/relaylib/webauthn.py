
import json, hashlib, hmac, base64, struct
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, utils as asym_utils
from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidSignature

def b64u_dec(s) -> bytes:
    if isinstance(s, bytes):
        s = s.decode()
    s = s.replace("-", "+").replace("_", "/")
    return base64.b64decode(s + "=" * (-len(s) % 4))

def b64u_enc(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def _cbor(b, i=0):

    ib = b[i]; major = ib >> 5; minor = ib & 0x1f; i += 1
    if minor < 24: val = minor
    elif minor == 24: val = b[i]; i += 1
    elif minor == 25: val = struct.unpack(">H", b[i:i+2])[0]; i += 2
    elif minor == 26: val = struct.unpack(">I", b[i:i+4])[0]; i += 4
    elif minor == 27: val = struct.unpack(">Q", b[i:i+8])[0]; i += 8
    else: raise ValueError("cbor: unsupported minor %d" % minor)
    if major == 0: return val, i
    if major == 1: return -1 - val, i
    if major == 2: return b[i:i+val], i+val
    if major == 3: return b[i:i+val].decode("utf-8"), i+val
    if major == 4:
        out = []
        for _ in range(val):
            v, i = _cbor(b, i); out.append(v)
        return out, i
    if major == 5:
        out = {}
        for _ in range(val):
            k, i = _cbor(b, i); v, i = _cbor(b, i); out[k] = v
        return out, i
    raise ValueError("cbor: unsupported major %d" % major)

def cbor_decode(b):
    v, _ = _cbor(b, 0)
    return v

COSE_KTY, COSE_ALG, COSE_CRV, COSE_X, COSE_Y = 1, 3, -1, -2, -3
ALG_ES256, ALG_EDDSA = -7, -8

def cose_to_public_key(cose: dict):
    kty = cose.get(COSE_KTY); alg = cose.get(COSE_ALG)
    if kty == 2 and alg == ALG_ES256:
        x = int.from_bytes(cose[COSE_X], "big"); y = int.from_bytes(cose[COSE_Y], "big")
        return ("es256", ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key())
    if kty == 1 and alg == ALG_EDDSA:
        return ("eddsa", ed25519.Ed25519PublicKey.from_public_bytes(cose[COSE_X]))
    raise ValueError("unsupported COSE key kty=%r alg=%r" % (kty, alg))

FLAG_UP, FLAG_UV, FLAG_AT = 0x01, 0x04, 0x40

def parse_auth_data(ad: bytes):
    rp_id_hash = ad[0:32]; flags = ad[32]; sign_count = struct.unpack(">I", ad[33:37])[0]
    out = {"rp_id_hash": rp_id_hash, "flags": flags, "sign_count": sign_count,
           "up": bool(flags & FLAG_UP), "uv": bool(flags & FLAG_UV), "at": bool(flags & FLAG_AT)}
    i = 37
    if flags & FLAG_AT:
        out["aaguid"] = ad[i:i+16]; i += 16
        cid_len = struct.unpack(">H", ad[i:i+2])[0]; i += 2
        out["credential_id"] = ad[i:i+cid_len]; i += cid_len
        cose, i = _cbor(ad, i)
        out["cose_key"] = cose
    return out

def challenge_for(action_digest: str, approval_id: str, nonce: bytes) -> str:

    h = hashlib.sha256()
    h.update(b"brainarbeit/webauthn/approval/v1\x00")
    h.update(action_digest.encode() + b"\x00" + approval_id.encode() + b"\x00" + nonce)
    return b64u_enc(h.digest())

def verify_registration(attestation_object_b64u, client_data_json_b64u, *, expected_challenge,
                        expected_origin, rp_id):

    cdj = b64u_dec(client_data_json_b64u); cd = json.loads(cdj)
    if cd.get("type") != "webauthn.create":
        raise ValueError("clientData.type != webauthn.create")
    if cd.get("challenge") != expected_challenge:
        raise ValueError("registration challenge mismatch")
    if cd.get("origin") != expected_origin:
        raise ValueError("origin mismatch: %r != %r" % (cd.get("origin"), expected_origin))
    att = cbor_decode(b64u_dec(attestation_object_b64u))
    fmt = att.get("fmt"); ad = parse_auth_data(att["authData"])
    if ad["rp_id_hash"] != hashlib.sha256(rp_id.encode()).digest():
        raise ValueError("rpIdHash mismatch")
    if not ad["up"]:
        raise ValueError("user-presence flag not set")
    if not ad["at"] or "cose_key" not in ad:
        raise ValueError("no attested credential data")
    kind, pub = cose_to_public_key(ad["cose_key"])

    att_verified = False
    stmt = att.get("attStmt") or {}
    if fmt == "packed" and "sig" in stmt and "x5c" not in stmt:
        client_hash = hashlib.sha256(cdj).digest()
        _verify_sig(kind, pub, att["authData"] + client_hash, stmt["sig"])
        att_verified = True
    return {
        "credential_id": b64u_enc(ad["credential_id"]),
        "cose_key": _cose_to_portable(ad["cose_key"]),
        "kind": kind,
        "aaguid": ad["aaguid"].hex(),
        "sign_count": ad["sign_count"],
        "uv": ad["uv"],
        "fmt": fmt,
        "attestation_verified": att_verified,
    }

def _cose_to_portable(cose: dict):

    out = {}
    for k, v in cose.items():
        out[str(k)] = v.hex() if isinstance(v, (bytes, bytearray)) else v
    return out

def _portable_to_pub(portable: dict):
    kty = portable.get("1"); alg = portable.get("3")
    if kty == 2 and alg == ALG_ES256:
        x = int.from_bytes(bytes.fromhex(portable["-2"]), "big")
        y = int.from_bytes(bytes.fromhex(portable["-3"]), "big")
        return ("es256", ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key())
    if kty == 1 and alg == ALG_EDDSA:
        return ("eddsa", ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(portable["-2"])))
    raise ValueError("unsupported stored COSE key")

def _verify_sig(kind, pub, message, signature):
    if kind == "es256":
        pub.verify(signature, message, ec.ECDSA(hashes.SHA256()))
    elif kind == "eddsa":
        pub.verify(signature, message)
    else:
        raise ValueError("unknown key kind %r" % kind)

def verify_assertion(stored_credential, authenticator_data_b64u, client_data_json_b64u,
                     signature_b64u, *, expected_challenge, expected_origin, rp_id,
                     last_counter=-1, require_uv=True):

    cdj = b64u_dec(client_data_json_b64u); cd = json.loads(cdj)
    if cd.get("type") != "webauthn.get":
        raise ValueError("clientData.type != webauthn.get")
    if cd.get("challenge") != expected_challenge:
        raise ValueError("assertion challenge mismatch (not the pending action)")
    if cd.get("origin") != expected_origin:
        raise ValueError("origin mismatch")
    ad_raw = b64u_dec(authenticator_data_b64u); ad = parse_auth_data(ad_raw)
    if ad["rp_id_hash"] != hashlib.sha256(rp_id.encode()).digest():
        raise ValueError("rpIdHash mismatch")
    if not ad["up"]:
        raise ValueError("user-presence flag not set")
    if require_uv and not ad["uv"]:
        raise ValueError("user-verification (fingerprint) flag not set")
    kind, pub = _portable_to_pub(stored_credential["cose_key"])
    client_hash = hashlib.sha256(cdj).digest()
    try:
        _verify_sig(kind, pub, ad_raw + client_hash, b64u_dec(signature_b64u))
    except InvalidSignature:
        raise ValueError("assertion signature invalid")

    new_counter = ad["sign_count"]
    if (new_counter != 0 or last_counter > 0) and new_counter <= last_counter:
        raise ValueError("signature counter did not increase (replay): %d <= %d" % (new_counter, last_counter))
    return {"ok": True, "new_counter": new_counter, "uv": ad["uv"]}
