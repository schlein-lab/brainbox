
from __future__ import annotations
import os, sqlite3, time, json, secrets, hashlib

from . import twofactor as _totp

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS principals_2fa (
  principal     TEXT PRIMARY KEY,
  factor_kind   TEXT NOT NULL DEFAULT 'totp',
  secret_b32    TEXT NOT NULL,
  last_counter  INTEGER NOT NULL DEFAULT -1,
  fail_count    INTEGER NOT NULL DEFAULT 0,
  locked_until  REAL NOT NULL DEFAULT 0,
  armed_at      REAL,
  enabled       INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS alliances (
  device_did       TEXT PRIMARY KEY,
  principal        TEXT NOT NULL,
  parent_principal TEXT,
  caps             TEXT NOT NULL,
  label            TEXT,
  max_rate         INTEGER NOT NULL DEFAULT 30,
  max_concurrency  INTEGER NOT NULL DEFAULT 2,
  token_hash       TEXT NOT NULL,
  issued_at        REAL NOT NULL,
  last_seen        REAL,
  revoked_at       REAL
);
CREATE TABLE IF NOT EXISTS rate_log (device_did TEXT NOT NULL, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_rate ON rate_log(device_did, ts);
"""

RATE_WINDOW_S = 60.0
TWOFA_MAX_FAILS = 5
TWOFA_LOCKOUT_S = 900

def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or ":memory:"
    if path != ":memory:":
        os.makedirs(os.path.dirname(path), exist_ok=True)
    cx = sqlite3.connect(path, timeout=10, check_same_thread=False)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA synchronous=FULL")
    cx.executescript(SCHEMA)
    cx.commit()
    if path != ":memory:":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return cx

def _pepper(cx) -> bytes:
    r = cx.execute("SELECT v FROM meta WHERE k='pepper'").fetchone()
    if r:
        return bytes.fromhex(r["v"])
    p = secrets.token_bytes(32)
    cx.execute("INSERT INTO meta(k,v) VALUES('pepper',?)", (p.hex(),))
    cx.commit()
    return p

def _hk(cx, s: str) -> str:

    return hashlib.blake2s(s.encode(), key=_pepper(cx), digest_size=16).hexdigest()

def _eq(a: str, b: str) -> bool:
    return secrets.compare_digest(a, b)

def arm_2fa(cx, principal: str, *, factor_kind: str = "totp", secret_b32: str | None = None) -> str:
    if factor_kind != "totp":
        raise ValueError("only 'totp' in this shim")
    secret = secret_b32 or _totp.gen_secret()
    now = time.time()
    cx.execute(
        "INSERT INTO principals_2fa(principal,factor_kind,secret_b32,last_counter,fail_count,"
        "locked_until,armed_at,enabled) VALUES(?,?,?,-1,0,0,?,1) "
        "ON CONFLICT(principal) DO UPDATE SET factor_kind=excluded.factor_kind,"
        "secret_b32=excluded.secret_b32,last_counter=-1,fail_count=0,locked_until=0,"
        "armed_at=excluded.armed_at,enabled=1",
        (principal, factor_kind, secret, now))
    cx.commit()
    return secret

def has_2fa(cx, principal: str) -> bool:
    r = cx.execute("SELECT enabled FROM principals_2fa WHERE principal=?", (principal,)).fetchone()
    return bool(r and r["enabled"])

def verify_2fa(cx, principal: str, code: str) -> tuple[bool, str]:
    r = cx.execute("SELECT * FROM principals_2fa WHERE principal=?", (principal,)).fetchone()
    if not r or not r["enabled"]:
        return False, "no armed factor"
    now = time.time()
    if r["locked_until"] and now < r["locked_until"]:
        return False, "2FA locked (too many failures); try later"
    ok, accepted = _totp.verify(r["secret_b32"], code, last_counter=r["last_counter"])
    if ok:
        cx.execute("UPDATE principals_2fa SET last_counter=?, fail_count=0, locked_until=0 "
                   "WHERE principal=?", (accepted, principal))
        cx.commit()
        return True, "ok"
    fails = r["fail_count"] + 1
    locked = now + TWOFA_LOCKOUT_S if fails >= TWOFA_MAX_FAILS else 0
    cx.execute("UPDATE principals_2fa SET fail_count=?, locked_until=? WHERE principal=?",
               (0 if locked else fails, locked, principal))
    cx.commit()
    return False, ("locked after too many failures" if locked else "invalid code")

def create_alliance(cx, *, device_did, principal, parent_principal=None, caps, label=None,
                    max_rate=30, max_concurrency=2) -> str:
    token = secrets.token_urlsafe(24)
    now = time.time()
    cx.execute(
        "INSERT INTO alliances(device_did,principal,parent_principal,caps,label,max_rate,"
        "max_concurrency,token_hash,issued_at,last_seen,revoked_at) VALUES(?,?,?,?,?,?,?,?,?,?,NULL) "
        "ON CONFLICT(device_did) DO UPDATE SET principal=excluded.principal,"
        "parent_principal=excluded.parent_principal,caps=excluded.caps,label=excluded.label,"
        "max_rate=excluded.max_rate,max_concurrency=excluded.max_concurrency,"
        "token_hash=excluded.token_hash,issued_at=excluded.issued_at,revoked_at=NULL",
        (device_did, principal, parent_principal, json.dumps(sorted(caps)), label,
         int(max_rate), int(max_concurrency), _hk(cx, token), now, now))
    cx.commit()
    return token

def get_alliance(cx, device_did) -> dict | None:
    r = cx.execute("SELECT * FROM alliances WHERE device_did=?", (device_did,)).fetchone()
    return dict(r) if r else None

def alliance_for_token(cx, device_did, token) -> dict | None:
    r = get_alliance(cx, device_did)
    if not r or r["revoked_at"] is not None:
        return None
    if not _eq(r["token_hash"], _hk(cx, token)):
        return None
    cx.execute("UPDATE alliances SET last_seen=? WHERE device_did=?", (time.time(), device_did))
    cx.commit()
    return r

def revoke(cx, device_did) -> bool:
    r = get_alliance(cx, device_did)
    if not r or r["revoked_at"] is not None:
        return False
    cx.execute("UPDATE alliances SET revoked_at=? WHERE device_did=?", (time.time(), device_did))
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
        return False, f"per-device rate ceiling exceeded ({al['max_rate']}/{int(RATE_WINDOW_S)}s)"
    cx.execute("INSERT INTO rate_log(device_did,ts) VALUES(?,?)", (device_did, now))
    cx.commit()
    return True, "ok"

def caps_ceiling(cx, device_did) -> set:
    al = get_alliance(cx, device_did)
    return set(json.loads(al["caps"])) if al else set()
