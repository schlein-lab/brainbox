
from __future__ import annotations
import os, sqlite3, time, json, secrets, hashlib

from relaylib import totp as _totp
from relaylib import audit as _audit

SCHEMA = """
CREATE TABLE IF NOT EXISTS principals_2fa (
  principal        TEXT PRIMARY KEY,
  factor_kind      TEXT NOT NULL DEFAULT 'totp',  -- 'totp' now; 'webauthn' slots in later
  secret_hash      TEXT,                          -- hash of the TOTP secret (tamper detection)
  secret_enc       TEXT,                          -- TOTP secret wrapped at rest (peppered)
  last_counter     INTEGER NOT NULL DEFAULT -1,   -- highest accepted TOTP step (anti-replay)
  fail_count       INTEGER NOT NULL DEFAULT 0,    -- consecutive failures (brute-force lockout)
  locked_until     REAL NOT NULL DEFAULT 0,       -- 2FA lockout expiry (epoch)
  armed_at         REAL,                          -- when 2FA was enrolled for this principal
  enabled          INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS pairings (
  code_hash   TEXT PRIMARY KEY,          -- keyed-hash(code); the plaintext code is never stored
  principal   TEXT NOT NULL,             -- the human/agent the device will be bound TO
  parent_principal TEXT,                 -- who minted it (for the bounded blast-radius audit)
  caps        TEXT NOT NULL,             -- JSON list: the cap CEILING this device may request
  label       TEXT,                      -- human label for the device ("Chris iPhone")
  max_rate    INTEGER NOT NULL DEFAULT 30,    -- per-device submits / window (ceiling)
  max_concurrency INTEGER NOT NULL DEFAULT 2, -- per-device in-flight jobs (ceiling)
  require_2fa INTEGER NOT NULL DEFAULT 1,      -- 2FA is MANDATORY (cannot be minted as 0)
  attempts    INTEGER NOT NULL DEFAULT 0,      -- failed redemption attempts (brute-force limiter)
  locked_until REAL NOT NULL DEFAULT 0,        -- redemption lockout expiry
  created_at  REAL NOT NULL,
  expires_at  REAL NOT NULL,             -- the OOB code is short-lived; the ALLIANCE is durable
  used_at     REAL                       -- one-time: set when redeemed, then never reusable
);
CREATE TABLE IF NOT EXISTS alliances (
  device_did       TEXT PRIMARY KEY,     -- did:key derived from the device identity pubkey
  device_pubkey    TEXT NOT NULL,        -- hex of the device Ed25519 IDENTITY pubkey (32B)
  device_x_pubkey  TEXT,                 -- hex of the device X25519 STATIC pubkey (E2E)
  principal        TEXT NOT NULL,        -- the bound human/agent principal
  parent_principal TEXT,                 -- who authorised this device (blast-radius audit)
  caps             TEXT NOT NULL,        -- JSON list: cap ceiling for this device (<= owner)
  label            TEXT,
  max_rate         INTEGER NOT NULL DEFAULT 30,
  max_concurrency  INTEGER NOT NULL DEFAULT 2,
  token_hash       TEXT NOT NULL,        -- keyed-hash(durable token); plaintext never stored
  submit_counter   INTEGER NOT NULL DEFAULT 0,  -- monotonic; replay/rollback detection
  issued_at        REAL NOT NULL,
  last_seen        REAL,
  revoked_at       REAL                  -- NOT NULL => alliance dead, all submits rejected
);
CREATE TABLE IF NOT EXISTS session_tokens (
  token_hash  TEXT PRIMARY KEY,         -- keyed-hash(session token); plaintext never stored
  device_did  TEXT NOT NULL,
  transcript  TEXT NOT NULL,            -- hex of the handshake transcript hash it is bound to
  issued_at   REAL NOT NULL,
  expires_at  REAL NOT NULL,            -- SHORT-LIVED (default 120s); rotated on every use
  used_at     REAL                      -- single-use: consumed on rotation
);
CREATE INDEX IF NOT EXISTS idx_session_did ON session_tokens(device_did, expires_at);
CREATE TABLE IF NOT EXISTS rate_log (
  device_did TEXT NOT NULL,
  ts         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate ON rate_log(device_did, ts);
CREATE TABLE IF NOT EXISTS device_stepup_2fa (
  -- FIX D: step-up 2FA failure state PER DEVICE, kept STRICTLY SEPARATE from the principal-wide
  -- principals_2fa.fail_count/locked_until (which is now the REDEEM/ENROLL path only). A device
  -- spamming wrong step-up codes locks/auto-revokes ITSELF — it can NEVER lock the human's factor
  -- nor block a clean new pairing (anti-griefing).
  device_did   TEXT PRIMARY KEY,
  principal    TEXT NOT NULL,
  fail_count   INTEGER NOT NULL DEFAULT 0,   -- consecutive step-up failures for THIS device
  locked_until REAL NOT NULL DEFAULT 0,      -- step-up lockout expiry for THIS device (epoch)
  total_fails  INTEGER NOT NULL DEFAULT 0    -- lifetime step-up failures (louder-audit trigger)
);
CREATE INDEX IF NOT EXISTS idx_stepup_principal ON device_stepup_2fa(principal);
CREATE TABLE IF NOT EXISTS webauthn_credentials (
  -- Forderung 6: a phone-bound FIDO2 passkey that authorizes APPROVAL decisions (replaces the typed
  -- step-up TOTP on the approval path). We store ONLY the credential PUBLIC key + counter — never any
  -- biometric (WebAuthn: the finger just unlocks a device key locally). One credential per device
  -- (device_did), bound to the human `principal`.
  credential_id TEXT PRIMARY KEY,       -- base64url credential id (from create())
  principal     TEXT NOT NULL,          -- the human this passkey authorizes decisions FOR
  device_did    TEXT,                   -- the paired device it was enrolled from (OOB-fingerprint bound)
  cose_key      TEXT NOT NULL,          -- JSON portable COSE public key (hex byte fields)
  kind          TEXT NOT NULL,          -- 'es256' | 'eddsa'
  aaguid        TEXT,                   -- authenticator model id (hex)
  sign_count    INTEGER NOT NULL DEFAULT 0,  -- last accepted signature counter (B4 replay)
  fmt           TEXT,                   -- attestation format seen at registration
  att_verified  INTEGER NOT NULL DEFAULT 0,  -- self-attestation signature checked (B3)
  created_at    REAL NOT NULL,
  enabled       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_webauthn_principal ON webauthn_credentials(principal);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

RATE_WINDOW_S = 60.0

_REVOKE_OBSERVERS: list = []

def on_revoke(fn) -> None:

    if fn not in _REVOKE_OBSERVERS:
        _REVOKE_OBSERVERS.append(fn)

def _notify_revoked(device_did: str) -> None:
    for fn in list(_REVOKE_OBSERVERS):
        try:
            fn(device_did)
        except Exception:
            pass

PAIR_MAX_ATTEMPTS = 5
PAIR_LOCKOUT_S = 900
TWOFA_MAX_FAILS = 5
TWOFA_LOCKOUT_S = 900

STEPUP_MAX_FAILS = 5
STEPUP_LOCKOUT_S = 900
STEPUP_AUTOREVOKE_FAILS = 15

SESSION_TTL_S = 120.0

def _pepper(cx) -> bytes:

    r = cx.execute("SELECT v FROM meta WHERE k='pepper'").fetchone()
    if r:
        return bytes.fromhex(r["v"])
    p = secrets.token_bytes(32)
    cx.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('pepper',?)", (p.hex(),))
    cx.commit()
    return p

def _hk(cx, s: str) -> str:

    return hashlib.blake2b(s.encode(), key=_pepper(cx), digest_size=32).hexdigest()

def _eq(a: str, b: str) -> bool:
    return secrets.compare_digest(a, b)

def connect(path: str | None = None) -> sqlite3.Connection:
    from relaylib import RELAY_DB
    path = path or RELAY_DB
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cx = sqlite3.connect(path, timeout=10, check_same_thread=False)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA synchronous=FULL")
    cx.executescript(SCHEMA)
    cx.commit()
    _audit.init(cx)
    _pepper(cx)
    try:

        os.chmod(path, 0o660)
    except OSError:
        pass
    return cx

def arm_2fa(cx, principal: str, *, factor_kind: str = "totp", secret_b32: str | None = None) -> str:

    if factor_kind != "totp":
        raise ValueError(f"unsupported factor_kind {factor_kind!r} (only 'totp' in this build; "
                         "WebAuthn slots in via the SecondFactor abstraction)")
    secret = secret_b32 or _totp.gen_secret()
    now = time.time()

    cx.execute(
        "INSERT INTO principals_2fa(principal,factor_kind,secret_hash,secret_enc,last_counter,"
        "fail_count,locked_until,armed_at,enabled) VALUES(?,?,?,?,-1,0,0,?,1) "
        "ON CONFLICT(principal) DO UPDATE SET factor_kind=excluded.factor_kind,"
        "secret_hash=excluded.secret_hash,secret_enc=excluded.secret_enc,last_counter=-1,"
        "fail_count=0,locked_until=0,armed_at=excluded.armed_at,enabled=1",
        (principal, factor_kind, _hk(cx, secret), _wrap_secret(cx, secret), now))
    cx.commit()
    _audit_safe(cx, "twofa.arm", principal=principal, detail={"factor": factor_kind})
    return secret

def _wrap_secret(cx, secret: str) -> str:

    p = _pepper(cx)
    raw = secret.encode()
    ks = b""
    i = 0
    while len(ks) < len(raw):
        ks += hashlib.blake2b(p + i.to_bytes(4, "big"), digest_size=64).digest()
        i += 1
    return bytes(a ^ b for a, b in zip(raw, ks)).hex()

def _unwrap_secret(cx, enc_hex: str) -> str:
    p = _pepper(cx)
    raw = bytes.fromhex(enc_hex)
    ks = b""
    i = 0
    while len(ks) < len(raw):
        ks += hashlib.blake2b(p + i.to_bytes(4, "big"), digest_size=64).digest()
        i += 1
    return bytes(a ^ b for a, b in zip(raw, ks)).decode()

def has_2fa(cx, principal: str) -> bool:

    r = cx.execute("SELECT enabled FROM principals_2fa WHERE principal=?", (principal,)).fetchone()
    return bool(r and r["enabled"])

def _check_2fa_code(cx, principal: str, code: str, *, ts=None, allow_step_reuse: bool = False) -> tuple[bool, str]:

    r = cx.execute("SELECT * FROM principals_2fa WHERE principal=?", (principal,)).fetchone()
    if not r or not r["enabled"]:
        return (False, "two-factor is not armed for this principal (pairing is blocked)")
    secret = _unwrap_secret(cx, r["secret_enc"])

    lc = -1 if allow_step_reuse else r["last_counter"]
    ok, accepted = _totp.verify(secret, code, ts=ts, last_counter=lc)
    if ok:
        if not allow_step_reuse:

            cx.execute("UPDATE principals_2fa SET last_counter=? WHERE principal=?",
                       (accepted, principal))
            cx.commit()
        return (True, "ok")
    return (False, "two-factor code rejected")

def verify_2fa(cx, principal: str, code: str) -> tuple[bool, str]:

    r = cx.execute("SELECT * FROM principals_2fa WHERE principal=?", (principal,)).fetchone()
    if not r or not r["enabled"]:

        _audit_safe(cx, "twofa.fail", principal=principal, detail={"reason": "no factor armed"})
        return (False, "two-factor is not armed for this principal (pairing is blocked)")
    now = time.time()
    if r["locked_until"] and now < r["locked_until"]:
        _audit_safe(cx, "twofa.locked", principal=principal, detail={"until": r["locked_until"]})
        return (False, "two-factor is temporarily locked (too many failed attempts)")
    ok, why = _check_2fa_code(cx, principal, code)
    if ok:
        cx.execute("UPDATE principals_2fa SET fail_count=0, locked_until=0 WHERE principal=?",
                   (principal,))
        cx.commit()
        _audit_safe(cx, "twofa.ok", principal=principal)
        return (True, "ok")

    fails = r["fail_count"] + 1
    locked = r["locked_until"]
    if fails >= TWOFA_MAX_FAILS:
        locked = now + TWOFA_LOCKOUT_S
        fails = 0
    cx.execute("UPDATE principals_2fa SET fail_count=?, locked_until=? WHERE principal=?",
               (fails, locked, principal))
    cx.commit()
    _audit_safe(cx, "twofa.fail", principal=principal,
                detail={"locked": bool(locked and now < locked), "path": "redeem"})
    return (False, "two-factor code rejected")

def verify_stepup_2fa(cx, principal: str, device_did: str, code: str, *, ts=None, allow_step_reuse: bool = False) -> tuple[bool, str]:

    now = time.time()
    sr = cx.execute("SELECT * FROM device_stepup_2fa WHERE device_did=?", (device_did,)).fetchone()

    if sr and sr["locked_until"] and now < sr["locked_until"]:
        _audit_safe(cx, "stepup.locked", device_did=device_did, principal=principal,
                    detail={"until": sr["locked_until"]})
        return (False, "step-up is temporarily locked for this device (too many failed attempts)")
    ok, why = _check_2fa_code(cx, principal, code, ts=ts, allow_step_reuse=allow_step_reuse)
    if ok:
        cx.execute(
            "INSERT INTO device_stepup_2fa(device_did,principal,fail_count,locked_until,total_fails)"
            " VALUES(?,?,0,0,COALESCE((SELECT total_fails FROM device_stepup_2fa WHERE device_did=?),0))"
            " ON CONFLICT(device_did) DO UPDATE SET fail_count=0, locked_until=0",
            (device_did, principal, device_did))
        cx.commit()
        _audit_safe(cx, "stepup.ok", device_did=device_did, principal=principal)
        return (True, "ok")

    fails = (sr["fail_count"] if sr else 0) + 1
    total = (sr["total_fails"] if sr else 0) + 1
    locked = sr["locked_until"] if sr else 0
    if fails >= STEPUP_MAX_FAILS:
        locked = now + STEPUP_LOCKOUT_S
        fails = 0
    cx.execute(
        "INSERT INTO device_stepup_2fa(device_did,principal,fail_count,locked_until,total_fails)"
        " VALUES(?,?,?,?,?) ON CONFLICT(device_did) DO UPDATE SET principal=excluded.principal,"
        " fail_count=excluded.fail_count, locked_until=excluded.locked_until,"
        " total_fails=excluded.total_fails",
        (device_did, principal, fails, locked, total))
    cx.commit()

    _audit_safe(cx, "stepup.fail", device_did=device_did, principal=principal,
                detail={"locked": bool(locked and now < locked), "total_fails": total,
                        "path": "stepup"})
    if total >= STEPUP_AUTOREVOKE_FAILS:

        revoke(cx, device_did)
        _audit_safe(cx, "stepup.autorevoke", device_did=device_did, principal=principal,
                    detail={"total_fails": total})
        return (False, "step-up failed too many times; this device has been auto-revoked")
    return (False, "step-up two-factor code rejected")

def arm_webauthn(cx, principal: str, record: dict, *, device_did: str | None = None) -> str:

    now = time.time()
    cx.execute(
        "INSERT INTO webauthn_credentials(credential_id,principal,device_did,cose_key,kind,aaguid,"
        "sign_count,fmt,att_verified,created_at,enabled) VALUES(?,?,?,?,?,?,?,?,?,?,1) "
        "ON CONFLICT(credential_id) DO UPDATE SET principal=excluded.principal,"
        "device_did=excluded.device_did,cose_key=excluded.cose_key,kind=excluded.kind,"
        "aaguid=excluded.aaguid,sign_count=excluded.sign_count,fmt=excluded.fmt,"
        "att_verified=excluded.att_verified,enabled=1",
        (record["credential_id"], principal, device_did, json.dumps(record["cose_key"]),
         record["kind"], record.get("aaguid"), int(record.get("sign_count", 0)),
         record.get("fmt"), 1 if record.get("attestation_verified") else 0, now))
    cx.commit()
    _audit_safe(cx, "webauthn.arm", principal=principal, device_did=device_did,
                detail={"kind": record["kind"], "fmt": record.get("fmt"),
                        "att_verified": bool(record.get("attestation_verified"))})
    return record["credential_id"]

def list_webauthn(cx, principal: str, *, device_did: str | None = None) -> list:

    if device_did:
        rows = cx.execute("SELECT * FROM webauthn_credentials WHERE principal=? AND device_did=? "
                          "AND enabled=1", (principal, device_did)).fetchall()
    else:
        rows = cx.execute("SELECT * FROM webauthn_credentials WHERE principal=? AND enabled=1",
                          (principal,)).fetchall()
    return [dict(r) for r in rows]

def has_webauthn(cx, principal: str, *, device_did: str | None = None) -> bool:
    return len(list_webauthn(cx, principal, device_did=device_did)) > 0

def verify_stepup_webauthn(cx, principal: str, device_did: str, assertion: dict, *,
                           expected_challenge: str, origin: str, rp_id: str,
                           require_uv: bool = True) -> tuple[bool, str]:

    from relaylib import webauthn as _wa
    now = time.time()
    sr = cx.execute("SELECT * FROM device_stepup_2fa WHERE device_did=?", (device_did,)).fetchone()
    if sr and sr["locked_until"] and now < sr["locked_until"]:
        _audit_safe(cx, "stepup.locked", device_did=device_did, principal=principal,
                    detail={"until": sr["locked_until"], "path": "webauthn"})
        return (False, "step-up is temporarily locked for this device (too many failed attempts)")

    cred_id = assertion.get("id") or assertion.get("credential_id")
    row = None
    if cred_id:
        row = cx.execute("SELECT * FROM webauthn_credentials WHERE credential_id=? AND principal=? "
                         "AND enabled=1", (cred_id, principal)).fetchone()
    ok = False; why = "passkey assertion rejected"
    if row is not None:
        stored = {"cose_key": json.loads(row["cose_key"]), "kind": row["kind"]}
        try:
            res = _wa.verify_assertion(
                stored, assertion["authenticatorData"], assertion["clientDataJSON"],
                assertion["signature"], expected_challenge=expected_challenge,
                expected_origin=origin, rp_id=rp_id, last_counter=int(row["sign_count"]),
                require_uv=require_uv)
            ok = bool(res.get("ok"))
            if ok:
                cx.execute("UPDATE webauthn_credentials SET sign_count=? WHERE credential_id=?",
                           (int(res["new_counter"]), cred_id))
        except Exception as e:
            why = "passkey: %s" % str(e)[:80]
    else:
        why = "no such passkey for this principal"

    if ok:
        cx.execute(
            "INSERT INTO device_stepup_2fa(device_did,principal,fail_count,locked_until,total_fails)"
            " VALUES(?,?,0,0,COALESCE((SELECT total_fails FROM device_stepup_2fa WHERE device_did=?),0))"
            " ON CONFLICT(device_did) DO UPDATE SET fail_count=0, locked_until=0",
            (device_did, principal, device_did))
        cx.commit()
        _audit_safe(cx, "stepup.ok", device_did=device_did, principal=principal,
                    detail={"path": "webauthn"})
        return (True, "ok")

    fails = (sr["fail_count"] if sr else 0) + 1
    total = (sr["total_fails"] if sr else 0) + 1
    locked = sr["locked_until"] if sr else 0
    if fails >= STEPUP_MAX_FAILS:
        locked = now + STEPUP_LOCKOUT_S; fails = 0
    cx.execute(
        "INSERT INTO device_stepup_2fa(device_did,principal,fail_count,locked_until,total_fails)"
        " VALUES(?,?,?,?,?) ON CONFLICT(device_did) DO UPDATE SET principal=excluded.principal,"
        " fail_count=excluded.fail_count, locked_until=excluded.locked_until,"
        " total_fails=excluded.total_fails",
        (device_did, principal, fails, locked, total))
    cx.commit()
    _audit_safe(cx, "stepup.fail", device_did=device_did, principal=principal,
                detail={"locked": bool(locked and now < locked), "total_fails": total, "path": "webauthn"})
    if total >= STEPUP_AUTOREVOKE_FAILS:
        revoke(cx, device_did)
        _audit_safe(cx, "stepup.autorevoke", device_did=device_did, principal=principal,
                    detail={"total_fails": total, "path": "webauthn"})
        return (False, "step-up failed too many times; this device has been auto-revoked")
    return (False, why)

def unlock_2fa(cx, principal: str, *, device_did: str | None = None) -> bool:

    if device_did:
        cur = cx.execute("UPDATE device_stepup_2fa SET fail_count=0, locked_until=0 "
                         "WHERE device_did=?", (device_did,))
        cx.commit()
        _audit_safe(cx, "stepup.unlock", device_did=device_did, principal=principal)
        return cur.rowcount > 0
    cur = cx.execute("UPDATE principals_2fa SET fail_count=0, locked_until=0 WHERE principal=?",
                     (principal,))
    cx.commit()
    _audit_safe(cx, "twofa.unlock", principal=principal)
    return cur.rowcount > 0

def mint_pairing(cx, principal, caps, *, parent_principal=None, label=None,
                 ttl_s=900, max_rate=30, max_concurrency=2, require_2fa=True) -> str:

    if not require_2fa:
        raise ValueError("2FA is MANDATORY for the off-LAN relay; cannot mint with require_2fa=0")
    if not has_2fa(cx, principal):
        raise ValueError(
            f"principal {principal!r} has no armed second factor; arm 2FA first "
            "(pn-pair --arm-2fa / registry.arm_2fa). 2FA is the minimum bar for the relay.")
    code = secrets.token_urlsafe(9)
    now = time.time()
    cx.execute(
        "INSERT INTO pairings(code_hash,principal,parent_principal,caps,label,max_rate,"
        "max_concurrency,require_2fa,attempts,locked_until,created_at,expires_at) "
        "VALUES(?,?,?,?,?,?,?,1,0,0,?,?)",
        (_hk(cx, code), principal, parent_principal, json.dumps(sorted(caps)), label,
         int(max_rate), int(max_concurrency), now, now + ttl_s))
    cx.commit()
    _audit_safe(cx, "pair.mint", principal=principal,
                detail={"parent": parent_principal, "caps": sorted(caps), "label": label,
                        "ttl_s": ttl_s})
    return code

def _pairing_row(cx, code):
    return cx.execute("SELECT * FROM pairings WHERE code_hash=?", (_hk(cx, code),)).fetchone()

def redeem_pairing(cx, code, *, totp_code: str | None = None) -> dict | None:

    r = _pairing_row(cx, code)
    if not r:
        return None
    now = time.time()
    if r["locked_until"] and now < r["locked_until"]:
        _audit_safe(cx, "pair.locked", principal=r["principal"])
        return None
    if r["used_at"] is not None:
        return None
    if now > r["expires_at"]:
        return None

    ok2fa, why = (False, "no 2FA code supplied") if not totp_code else verify_2fa(
        cx, r["principal"], totp_code)
    if not ok2fa:
        attempts = r["attempts"] + 1
        locked = now + PAIR_LOCKOUT_S if attempts >= PAIR_MAX_ATTEMPTS else 0
        cx.execute("UPDATE pairings SET attempts=?, locked_until=? WHERE code_hash=?",
                   (attempts, locked, r["code_hash"]))
        cx.commit()
        _audit_safe(cx, "pair.fail", principal=r["principal"],
                    detail={"reason": why, "attempts": attempts, "locked": bool(locked)})
        return None
    cx.execute("UPDATE pairings SET used_at=? WHERE code_hash=?", (now, r["code_hash"]))
    cx.commit()
    _audit_safe(cx, "pair.redeem", principal=r["principal"],
                detail={"caps": json.loads(r["caps"]), "label": r["label"]})
    return dict(r)

def create_alliance(cx, *, device_did, device_pubkey_hex, device_x_pubkey_hex, principal,
                    parent_principal, caps, label, max_rate, max_concurrency) -> str:

    token = secrets.token_urlsafe(24)
    now = time.time()
    cx.execute(
        "INSERT INTO alliances(device_did,device_pubkey,device_x_pubkey,principal,"
        "parent_principal,caps,label,max_rate,max_concurrency,token_hash,submit_counter,"
        "issued_at,last_seen,revoked_at) VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?,NULL) "
        "ON CONFLICT(device_did) DO UPDATE SET device_pubkey=excluded.device_pubkey,"
        "device_x_pubkey=excluded.device_x_pubkey,principal=excluded.principal,"
        "parent_principal=excluded.parent_principal,caps=excluded.caps,label=excluded.label,"
        "max_rate=excluded.max_rate,max_concurrency=excluded.max_concurrency,"
        "token_hash=excluded.token_hash,submit_counter=0,issued_at=excluded.issued_at,"
        "revoked_at=NULL",
        (device_did, device_pubkey_hex, device_x_pubkey_hex, principal, parent_principal,
         json.dumps(sorted(caps)), label, int(max_rate), int(max_concurrency), _hk(cx, token),
         now, now))
    cx.commit()
    _audit_safe(cx, "alliance.create", device_did=device_did, principal=principal,
                detail={"parent": parent_principal, "caps": sorted(caps), "label": label})
    return token

def get_alliance(cx, device_did) -> dict | None:
    r = cx.execute("SELECT * FROM alliances WHERE device_did=?", (device_did,)).fetchone()
    return dict(r) if r else None

def alliance_for_token(cx, device_did, token) -> dict | None:

    r = get_alliance(cx, device_did)
    if not r:
        return None
    if r["revoked_at"] is not None:
        _audit_safe(cx, "hello.revoked", device_did=device_did, principal=r["principal"])
        return None
    if not _eq(r["token_hash"], _hk(cx, token)):
        _audit_safe(cx, "hello.badtoken", device_did=device_did)
        return None
    cx.execute("UPDATE alliances SET last_seen=? WHERE device_did=?",
               (time.time(), device_did))
    cx.commit()
    return r

def is_active(cx, device_did) -> bool:
    r = get_alliance(cx, device_did)
    return bool(r and r["revoked_at"] is None)

def revoke(cx, device_did) -> bool:

    r = get_alliance(cx, device_did)
    if not r or r["revoked_at"] is not None:
        return False
    cx.execute("UPDATE alliances SET revoked_at=? WHERE device_did=?",
               (time.time(), device_did))
    cx.execute("DELETE FROM session_tokens WHERE device_did=?", (device_did,))
    cx.execute("DELETE FROM device_stepup_2fa WHERE device_did=?", (device_did,))
    cx.commit()
    _notify_revoked(device_did)
    _audit_safe(cx, "revoke", device_did=device_did, principal=r["principal"])
    return True

def list_alliances(cx, principal=None) -> list:
    if principal:
        rows = cx.execute("SELECT * FROM alliances WHERE principal=? ORDER BY issued_at DESC",
                          (principal,)).fetchall()
    else:
        rows = cx.execute("SELECT * FROM alliances ORDER BY issued_at DESC").fetchall()
    return [dict(r) for r in rows]

def issue_session_token(cx, device_did, transcript_hex, *, ttl_s=SESSION_TTL_S) -> str:

    tok = secrets.token_urlsafe(18)
    now = time.time()
    cx.execute("DELETE FROM session_tokens WHERE expires_at < ?", (now,))
    cx.execute("INSERT INTO session_tokens(token_hash,device_did,transcript,issued_at,expires_at,"
               "used_at) VALUES(?,?,?,?,?,NULL)",
               (_hk(cx, tok), device_did, transcript_hex, now, now + ttl_s))
    cx.commit()
    return tok

def consume_session_token(cx, device_did, transcript_hex, token) -> bool:

    now = time.time()
    r = cx.execute("SELECT * FROM session_tokens WHERE token_hash=?",
                   (_hk(cx, token),)).fetchone()
    if not r:
        return False
    if r["used_at"] is not None or now > r["expires_at"]:
        return False
    if r["device_did"] != device_did or not _eq(r["transcript"], transcript_hex):
        return False
    cx.execute("UPDATE session_tokens SET used_at=? WHERE token_hash=?", (now, r["token_hash"]))
    cx.commit()
    return True

def next_submit_counter_ok(cx, device_did, counter: int) -> bool:

    r = get_alliance(cx, device_did)
    if not r:
        return False
    if not isinstance(counter, int) or counter <= r["submit_counter"]:
        return False
    cx.execute("UPDATE alliances SET submit_counter=? WHERE device_did=?", (counter, device_did))
    cx.commit()
    return True

def check_and_record_rate(cx, device_did) -> tuple[bool, str]:

    al = get_alliance(cx, device_did)
    if not al:
        return False, "no alliance"
    now = time.time()
    cx.execute("DELETE FROM rate_log WHERE ts < ?", (now - RATE_WINDOW_S,))
    n = cx.execute("SELECT COUNT(*) c FROM rate_log WHERE device_did=? AND ts>=?",
                   (device_did, now - RATE_WINDOW_S)).fetchone()["c"]
    if n >= al["max_rate"]:
        cx.commit()
        _audit_safe(cx, "rate.ceiling", device_did=device_did, principal=al["principal"],
                    detail={"max_rate": al["max_rate"], "window_s": int(RATE_WINDOW_S)})
        return False, f"per-device rate ceiling exceeded ({al['max_rate']}/{int(RATE_WINDOW_S)}s)"
    cx.execute("INSERT INTO rate_log(device_did,ts) VALUES(?,?)", (device_did, now))
    cx.commit()
    return True, "ok"

def caps_ceiling(cx, device_did) -> set:

    al = get_alliance(cx, device_did)
    if not al:
        return set()
    return set(json.loads(al["caps"]))

def _audit_safe(cx, event, **kw):
    try:
        _audit.record(cx, event, **kw)
    except Exception:
        pass
