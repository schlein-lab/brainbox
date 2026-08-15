

import base64
import json
import os
import struct
import threading
import time
import urllib.error
import urllib.request

DATA_DIR = os.environ.get("PN_PORTAL_DATA",
                          os.path.expanduser("~/.local/share/brainbox-portal"))
VAPID_PATH = os.path.join(DATA_DIR, "webpush-vapid.json")
ABO_PATH = os.path.join(DATA_DIR, "webpush-abos.json")

_LOCK = threading.Lock()

VAPID_SUB = os.environ.get("PN_WEBPUSH_SUB", "mailto:owner@brainbox.local")

def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")

def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

def _vapid_laden():
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    with _LOCK:
        if os.path.exists(VAPID_PATH):
            d = json.load(open(VAPID_PATH, encoding="utf-8"))
            priv = serialization.load_pem_private_key(d["private_pem"].encode(), password=None)
            return priv, d["public_b64u"]
        priv = ec.generate_private_key(ec.SECP256R1())
        pub_punkt = priv.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        d = {
            "private_pem": priv.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()).decode(),
            "public_b64u": _b64u(pub_punkt),
            "erzeugt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        os.makedirs(DATA_DIR, exist_ok=True)
        fd = os.open(VAPID_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f)
        return priv, d["public_b64u"]

def vapid_public_key() -> str:

    return _vapid_laden()[1]

def _abos_lesen():
    try:
        return json.load(open(ABO_PATH, encoding="utf-8"))
    except Exception:
        return {}

def _abos_schreiben(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = ABO_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, ABO_PATH)

def abo_anlegen(principal: str, sub: dict) -> int:

    if not (isinstance(sub, dict) and sub.get("endpoint", "").startswith("https://")
            and isinstance(sub.get("keys"), dict)
            and sub["keys"].get("p256dh") and sub["keys"].get("auth")):
        raise ValueError("kein vollstaendiges Push-Abo (endpoint/keys.p256dh/keys.auth)")
    with _LOCK:
        d = _abos_lesen()
        liste = [a for a in d.get(principal, []) if a.get("endpoint") != sub["endpoint"]]
        liste.append({"endpoint": sub["endpoint"], "keys": sub["keys"],
                      "seit": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        d[principal] = liste
        _abos_schreiben(d)
        return len(liste)

def abo_entfernen(principal: str, endpoint: str) -> int:
    with _LOCK:
        d = _abos_lesen()
        liste = [a for a in d.get(principal, []) if a.get("endpoint") != endpoint]
        if liste:
            d[principal] = liste
        else:
            d.pop(principal, None)
        _abos_schreiben(d)
        return len(liste)

def abos_von(principal: str):
    return list(_abos_lesen().get(principal, []))

def verschluesseln(sub: dict, klartext: bytes) -> bytes:

    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    browser_pub = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), _b64u_dec(sub["keys"]["p256dh"]))
    auth = _b64u_dec(sub["keys"]["auth"])

    eph = ec.generate_private_key(ec.SECP256R1())
    eph_pub = eph.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    ecdh = eph.exchange(ec.ECDH(), browser_pub)

    browser_punkt = _b64u_dec(sub["keys"]["p256dh"])
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth,
               info=b"WebPush: info\x00" + browser_punkt + eph_pub).derive(ecdh)
    salt = os.urandom(16)
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt,
               info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt,
                 info=b"Content-Encoding: nonce\x00").derive(ikm)

    ct = AESGCM(cek).encrypt(nonce, klartext + b"\x02", None)
    kopf = salt + struct.pack(">I", 4096) + bytes([len(eph_pub)]) + eph_pub
    return kopf + ct

def _vapid_jwt(endpoint: str) -> str:

    from urllib.parse import urlsplit
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    priv, _ = _vapid_laden()
    teile = urlsplit(endpoint)
    aud = "%s://%s" % (teile.scheme, teile.netloc)
    kopf = _b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())

    rumpf = _b64u(json.dumps({"aud": aud, "exp": int(time.time()) + 12 * 3600,
                              "sub": VAPID_SUB}).encode())
    der = priv.sign((kopf + "." + rumpf).encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    sig = _b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return kopf + "." + rumpf + "." + sig

def senden(sub: dict, meldung: dict, *, ttl_s: int = 3600, urlopen=None) -> str:

    urlopen = urlopen or urllib.request.urlopen
    try:
        koerper = verschluesseln(sub, json.dumps(meldung).encode())
        req = urllib.request.Request(sub["endpoint"], data=koerper, method="POST")
        req.add_header("TTL", str(ttl_s))
        req.add_header("Content-Encoding", "aes128gcm")
        req.add_header("Content-Type", "application/octet-stream")
        req.add_header("Urgency", "high")
        req.add_header("Authorization",
                       "vapid t=%s, k=%s" % (_vapid_jwt(sub["endpoint"]), vapid_public_key()))
        with urlopen(req, timeout=15) as r:
            code = getattr(r, "status", 200)
        return "zugestellt" if 200 <= code < 300 else "fehler:http-%s" % code
    except urllib.error.HTTPError as e:

        if e.code in (404, 410):
            return "abo-tot"
        return "fehler:http-%s" % e.code
    except Exception as e:
        return "fehler:%s" % str(e)[:80]

def push_melden(principal: str, meldung: dict, *, urlopen=None) -> dict:

    ergebnis = {"zugestellt": 0, "abo_tot": 0, "fehler": 0}
    for sub in abos_von(principal):
        aus = senden(sub, meldung, urlopen=urlopen)
        if aus == "zugestellt":
            ergebnis["zugestellt"] += 1
        elif aus == "abo-tot":
            ergebnis["abo_tot"] += 1
            abo_entfernen(principal, sub["endpoint"])
        else:
            ergebnis["fehler"] += 1
    return ergebnis
