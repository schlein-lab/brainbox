
import os, json, time, threading, hashlib, secrets, tempfile, re

_W = None
_W_ERR = None
try:
    import importlib.util as _ilu
    _here = os.path.dirname(os.path.realpath(__file__))
    _root = os.path.dirname(os.path.dirname(_here))
    for _cand in (
        os.path.join(_root, "engine", "relaylib", "webauthn.py"),
        os.path.join(_here, "portal_webauthn.py"),
    ):
        if os.path.exists(_cand):
            _spec = _ilu.spec_from_file_location("portal_webauthn_core", _cand)
            _W = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_W)
            break
    if _W is None:
        _W_ERR = "webauthn.py nicht gefunden"
except Exception as _e:
    _W = None; _W_ERR = "%s" % _e

def available() -> bool:

    return _W is not None

RP_ID = (os.environ.get("PN_PORTAL_RP_ID") or "brainbox.local").strip().lower()
RP_NAME = os.environ.get("PN_PORTAL_RP_NAME") or "BrainBox"

def _default_origins():
    base = RP_ID
    outs = ["https://%s" % base, "https://%s:8077" % base,
            "https://%s:8078" % base, "https://%s:8079" % base]
    extra = os.environ.get("PN_PORTAL_ORIGINS") or ""
    for o in extra.split(","):
        o = o.strip().rstrip("/")
        if o:
            outs.append(o)

    seen = set(); res = []
    for o in outs:
        if o not in seen:
            seen.add(o); res.append(o)
    return res

ALLOWED_ORIGINS = _default_origins()

def _match_origin(client_data_json_b64u):

    try:
        cdj = _W.b64u_dec(client_data_json_b64u)
        origin = (json.loads(cdj).get("origin") or "").rstrip("/")
    except Exception:
        return None
    return origin if origin in ALLOWED_ORIGINS else None

def _base_dir():
    d = os.environ.get("PN_PORTAL_PASSKEY_DIR")
    if not d:
        d = os.path.join(os.path.expanduser("~"), ".local", "share", "brainbox-portal", "passkeys")
    return d

def _safe(principal) -> str:
    s = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(principal or "unknown"))
    return s[:80] or "unknown"

def _path(principal) -> str:
    return os.path.join(_base_dir(), _safe(principal) + ".json")

def _index_path() -> str:
    return os.path.join(_base_dir(), "_index.json")

_LK = threading.RLock()

def _atomic_write(path, obj):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except Exception:
        pass
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-pk-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

def load(principal) -> dict:

    with _LK:
        try:
            with open(_path(principal), encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("principal", str(principal))
                data.setdefault("biometric_confirm", False)
                data.setdefault("credentials", [])
                return data
        except FileNotFoundError:
            pass
        except Exception:
            pass
    return {"principal": str(principal), "biometric_confirm": False, "credentials": []}

def save(principal, data):
    with _LK:
        _atomic_write(_path(principal), data)

def _index_load() -> dict:
    with _LK:
        try:
            with open(_index_path(), encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

def _index_set(cred_id, principal):
    with _LK:
        idx = _index_load()
        idx[cred_id] = str(principal)
        _atomic_write(_index_path(), idx)

def _index_drop(cred_id):
    with _LK:
        idx = _index_load()
        if cred_id in idx:
            idx.pop(cred_id, None)
            _atomic_write(_index_path(), idx)

def principal_for_credential(cred_id):
    return _index_load().get(cred_id)

def has_passkey(principal) -> bool:
    return bool(load(principal).get("credentials"))

def biometric_enabled(principal) -> bool:
    d = load(principal)
    return bool(d.get("biometric_confirm")) and bool(d.get("credentials"))

def set_biometric(principal, on: bool):
    d = load(principal)
    d["biometric_confirm"] = bool(on)
    save(principal, d)
    return d["biometric_confirm"]

def list_public(principal):

    d = load(principal)
    out = []
    for c in d.get("credentials", []):
        out.append({"id": c.get("credential_id"), "label": c.get("label") or "Passkey",
                    "kind": c.get("kind"), "created": c.get("created"),
                    "last_used": c.get("last_used"), "uv": c.get("uv", True)})
    return {"biometric_confirm": bool(d.get("biometric_confirm")), "credentials": out,
            "rp_id": RP_ID}

def remove(principal, cred_id) -> bool:
    d = load(principal)
    before = len(d.get("credentials", []))
    d["credentials"] = [c for c in d.get("credentials", []) if c.get("credential_id") != cred_id]
    if len(d["credentials"]) != before:
        save(principal, d)
        _index_drop(cred_id)
        return True
    return False

_CHALLENGES = {}
_CH_TTL = 300.0

def _chal_put(purpose, principal=None):
    cid = secrets.token_urlsafe(16)
    challenge = _W.b64u_enc(secrets.token_bytes(32))
    now = time.time()
    with _LK:

        for k in [k for k, v in _CHALLENGES.items() if v.get("exp", 0) < now]:
            _CHALLENGES.pop(k, None)
        if len(_CHALLENGES) > 512:
            for k in list(_CHALLENGES)[:256]:
                _CHALLENGES.pop(k, None)
        _CHALLENGES[cid] = {"challenge": challenge, "purpose": purpose,
                            "principal": (str(principal) if principal else None),
                            "exp": now + _CH_TTL}
    return cid, challenge

def _chal_take(cid, purpose):
    with _LK:
        rec = _CHALLENGES.pop(cid, None)
    if not rec or rec.get("purpose") != purpose or rec.get("exp", 0) < time.time():
        return None
    return rec

def registration_options(principal, display_name=None):

    if not available():
        return None
    cid, challenge = _chal_put("register", principal)
    user_handle = hashlib.sha256(("brainbox/user/" + str(principal)).encode()).digest()[:16]
    exclude = [{"type": "public-key", "id": c.get("credential_id")}
               for c in load(principal).get("credentials", [])]
    return {
        "cid": cid,
        "publicKey": {
            "rp": {"id": RP_ID, "name": RP_NAME},
            "user": {"id": _W.b64u_enc(user_handle),
                     "name": str(principal),
                     "displayName": display_name or str(principal)},
            "challenge": challenge,
            "pubKeyCredParams": [{"type": "public-key", "alg": -7},
                                 {"type": "public-key", "alg": -8}],
            "timeout": 120000,
            "attestation": "none",
            "excludeCredentials": exclude,
            "authenticatorSelection": {"residentKey": "preferred",
                                       "userVerification": "required"},
        },
    }

def verify_and_store_registration(principal, cid, resp, label=None):

    if not available():
        return False, "webauthn-core-fehlt"
    rec = _chal_take(cid, "register")
    if not rec:
        return False, "challenge-abgelaufen"
    resp = resp or {}
    r = resp.get("response") or {}
    ao = r.get("attestationObject"); cdj = r.get("clientDataJSON")
    if not ao or not cdj:
        return False, "unvollstaendige-antwort"
    origin = _match_origin(cdj)
    if not origin:
        return False, "origin-nicht-erlaubt"
    try:
        cred = _W.verify_registration(ao, cdj, expected_challenge=rec["challenge"],
                                      expected_origin=origin, rp_id=RP_ID)
    except Exception as e:
        return False, "verify: %s" % e
    d = load(principal)

    cred_id = cred["credential_id"]
    d["credentials"] = [c for c in d.get("credentials", []) if c.get("credential_id") != cred_id]
    entry = {
        "credential_id": cred_id,
        "cose_key": cred["cose_key"],
        "kind": cred["kind"],
        "aaguid": cred.get("aaguid"),
        "sign_count": cred.get("sign_count", 0),
        "last_counter": cred.get("sign_count", 0),
        "uv": cred.get("uv", True),
        "fmt": cred.get("fmt"),
        "label": (str(label)[:48] if label else "Passkey"),
        "created": int(time.time()),
        "last_used": None,
    }
    d["credentials"].append(entry)
    save(principal, d)
    _index_set(cred_id, principal)
    return True, {"credential_id": cred_id, "label": entry["label"], "kind": entry["kind"]}

def login_options(principal=None):

    if not available():
        return None
    cid, challenge = _chal_put("login", principal)
    allow = []
    if principal:
        allow = [{"type": "public-key", "id": c.get("credential_id")}
                 for c in load(principal).get("credentials", [])]
    return {"cid": cid,
            "publicKey": {"challenge": challenge, "rpId": RP_ID, "timeout": 120000,
                          "userVerification": "required", "allowCredentials": allow}}

def _do_assertion(principal, resp, expected_challenge):

    resp = resp or {}
    cred_id = resp.get("id") or resp.get("rawId")
    r = resp.get("response") or {}
    ad = r.get("authenticatorData"); cdj = r.get("clientDataJSON"); sig = r.get("signature")
    if not (cred_id and ad and cdj and sig):
        return False, "unvollstaendige-antwort"
    origin = _match_origin(cdj)
    if not origin:
        return False, "origin-nicht-erlaubt"
    d = load(principal)
    stored = next((c for c in d.get("credentials", []) if c.get("credential_id") == cred_id), None)
    if not stored:
        return False, "unbekannter-credential"
    try:
        res = _W.verify_assertion(stored, ad, cdj, sig, expected_challenge=expected_challenge,
                                  expected_origin=origin, rp_id=RP_ID,
                                  last_counter=int(stored.get("last_counter", -1)),
                                  require_uv=True)
    except Exception as e:
        return False, "verify: %s" % e
    stored["last_counter"] = int(res.get("new_counter", stored.get("last_counter", 0)))
    stored["last_used"] = int(time.time())
    save(principal, d)
    return True, {"credential_id": cred_id, "label": stored.get("label"), "uv": res.get("uv")}

def verify_login(cid, resp):

    if not available():
        return False, "webauthn-core-fehlt"
    rec = _chal_take(cid, "login")
    if not rec:
        return False, "challenge-abgelaufen"
    resp = resp or {}
    cred_id = resp.get("id") or resp.get("rawId")
    principal = rec.get("principal") or principal_for_credential(cred_id)
    if not principal:
        return False, "kein-konto-fuer-passkey"
    ok, info = _do_assertion(principal, resp, rec["challenge"])
    if not ok:
        return False, info
    return True, principal

def ceremony_challenge(principal, re_id, action_digest):

    if not available():
        return None
    nonce = secrets.token_bytes(16)
    challenge = _W.challenge_for(str(action_digest), str(re_id), nonce)
    allow = [{"type": "public-key", "id": c.get("credential_id")}
             for c in load(principal).get("credentials", [])]
    return {"challenge": challenge, "nonce_hex": nonce.hex(), "rpId": RP_ID,
            "allowCredentials": allow, "userVerification": "required"}

def verify_ceremony(principal, resp, expected_challenge):

    if not available():
        return False, "webauthn-core-fehlt"
    if not biometric_enabled(principal):
        return False, "biometrie-nicht-aktiviert"
    return _do_assertion(principal, resp, expected_challenge)
