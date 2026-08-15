
import os, time, threading

DATA_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "brainbox-portal")
DEFAULT_PRINCIPAL = "owner"

CFG_PATH = os.path.join(os.path.expanduser("~"), ".config", "brainbox-portal", "config.json")

SITE_CONF = "/etc/brainbox/site.conf"
_OWNER_NAME_DEFAULT = "Owner"

def _uid_safe(uid):
    import re
    return re.sub(r"[^A-Za-z0-9_.-]", "", str(uid or "owner"))[:64] or "owner"

def configure(data_dir=None, uid_safe=None, default_principal=None, cfg_path=None, site_conf=None):

    global DATA_DIR, USERS_DB, _uid_safe, DEFAULT_PRINCIPAL, CFG_PATH, SITE_CONF
    if data_dir is not None: DATA_DIR = data_dir
    if uid_safe is not None: _uid_safe = uid_safe
    if default_principal is not None: DEFAULT_PRINCIPAL = default_principal
    if cfg_path is not None: CFG_PATH = cfg_path
    if site_conf is not None: SITE_CONF = site_conf
    USERS_DB = os.path.join(DATA_DIR, "users.db")

USERS_DB = os.path.join(DATA_DIR, "users.db")
_USERS_LOCK = threading.Lock()
_RESERVED_UIDS = {"__system__"}

_RESERVED_PREFIXES = ("llmoauth",)

_SSO_HOOK = None
def set_sso_hook(fn):

    global _SSO_HOOK
    _SSO_HOOK = fn

def _sso_sync(uid, password):
    fn = _SSO_HOOK
    if fn is None or not password:
        return
    try:
        fn(uid, password)
    except Exception:
        pass

def _is_reserved_uid(uid):
    u = _uid_safe(uid)
    return u in _RESERVED_UIDS or any(u.startswith(p) for p in _RESERVED_PREFIXES)

def _users_conn():
    import sqlite3
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(USERS_DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        uid TEXT PRIMARY KEY, name TEXT, email TEXT, pw_hash TEXT, pw_salt TEXT,
        role TEXT DEFAULT 'user', status TEXT DEFAULT 'active', created REAL,
        email_verified INTEGER DEFAULT 0, pw_algo TEXT DEFAULT '', pw_changed REAL DEFAULT 0)""")
    for _col, _ddl in (("email_verified", "INTEGER DEFAULT 0"),
                       ("pw_algo", "TEXT DEFAULT ''"),
                       ("pw_changed", "REAL DEFAULT 0"),
                       ("email_optout", "INTEGER DEFAULT 0"),

                       ("last_login", "REAL DEFAULT 0"),
                       ("approved_by", "TEXT DEFAULT ''"),
                       ("approved_at", "REAL DEFAULT 0"),
                       ("auth_source", "TEXT DEFAULT 'local'"),
                       ("birthdate", "TEXT DEFAULT ''")):
        try:
            c.execute("ALTER TABLE users ADD COLUMN %s %s" % (_col, _ddl))
        except Exception:
            pass
    c.execute("""CREATE TABLE IF NOT EXISTS auth_tokens(
        token TEXT PRIMARY KEY, uid TEXT, kind TEXT, email TEXT,
        created REAL, expires REAL, used INTEGER DEFAULT 0)""")

    c.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
    return c

PW_ALGO = "scrypt-16384-8-1-32"
_PW_PARAMS = {"scrypt-16384-8-1-32": dict(n=16384, r=8, p=1, dklen=32)}

def _pw_hash(password, salt_hex, algo=None):
    import hashlib
    params = _PW_PARAMS.get(algo or PW_ALGO) or _PW_PARAMS[PW_ALGO]
    return hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), **params).hex()

def _meta_get(c, k):
    r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else None

def _meta_set(c, k, v):
    c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, v))

def _birthdate_ok(bd):

    if bd is None or bd == "":
        return True
    import re, datetime
    s = str(bd)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return False
    try:
        datetime.date.fromisoformat(s)
        return True
    except ValueError:
        return False

def user_create(uid, password, name=None, email=None, role=None, birthdate=None):

    import secrets as _secrets
    uid = _uid_safe(uid)
    if _is_reserved_uid(uid):
        raise ValueError("reserved uid")
    if not _birthdate_ok(birthdate):
        raise ValueError("bad birthdate")
    salt = _secrets.token_hex(16)
    now = time.time()
    with _USERS_LOCK:
        c = _users_conn()
        try:
            c.execute(
                "INSERT INTO users(uid,name,email,pw_hash,pw_salt,role,status,created,email_verified,"
                "                  pw_algo,pw_changed,birthdate)"
                " VALUES(?,?,?,?,?,?,'active',?,0,?,?,?)"
                " ON CONFLICT(uid) DO UPDATE SET"
                "   pw_hash=excluded.pw_hash, pw_salt=excluded.pw_salt,"
                "   pw_algo=excluded.pw_algo, pw_changed=excluded.pw_changed, status='active',"
                "   name=COALESCE(?,users.name), email=COALESCE(?,users.email),"
                "   role=COALESCE(?,users.role), birthdate=COALESCE(?,users.birthdate)",
                (uid, name or uid, email or "", _pw_hash(password, salt), salt, role or "user", now,
                 PW_ALGO, now, birthdate or "", name, email, role, birthdate))
            c.commit()
        finally:
            c.close()
    _sso_sync(uid, password)

    try:
        import pn_governed as _png
        _png.ensure_web_principal(uid)
    except Exception:
        pass
    return uid

def user_get(uid):
    with _USERS_LOCK:
        c = _users_conn()
        try:
            r = c.execute("SELECT uid,name,email,pw_hash,pw_salt,role,status,pw_algo,pw_changed,"
                          "email_verified,email_optout,birthdate"
                          " FROM users WHERE uid=?", (_uid_safe(uid),)).fetchone()
        finally:
            c.close()
    if not r:
        return None
    return {"uid": r[0], "name": r[1], "email": r[2], "pw_hash": r[3], "pw_salt": r[4],
            "role": r[5], "status": r[6], "pw_algo": r[7] or PW_ALGO, "pw_changed": r[8] or 0,
            "email_verified": int(r[9] or 0), "email_optout": int(r[10] or 0),
            "birthdate": r[11] or ""}

_DUMMY_SALT = "00" * 16

def user_verify(uid, password):
    import hmac
    u = None if _is_reserved_uid(uid) else user_get(uid)
    if not u or u["status"] != "active" or not u["pw_hash"] or not password:
        if password:
            _pw_hash(password, _DUMMY_SALT)
        return False
    return hmac.compare_digest(_pw_hash(password, u["pw_salt"], u["pw_algo"]), u["pw_hash"])

def user_pw_ok(uid, password):

    import hmac
    u = None if _is_reserved_uid(uid) else user_get(uid)
    if not u or not u.get("pw_hash") or not password:
        if password:
            _pw_hash(password, _DUMMY_SALT)
        return False
    return hmac.compare_digest(_pw_hash(password, u["pw_salt"], u["pw_algo"]), u["pw_hash"])

def _resolve_login_uid(identifier):

    raw = (identifier or "").strip()
    if not raw:
        return None
    uid = _uid_safe(raw)
    clean = (raw == uid) and not _is_reserved_uid(uid)
    if clean and user_get(uid):
        return uid
    owner = user_get(DEFAULT_PRINCIPAL)
    if owner:
        nm = (owner.get("name") or "").strip()
        if nm and raw.casefold() == nm.casefold():
            return DEFAULT_PRINCIPAL
    if clean:
        return uid
    return None

def user_list():
    with _USERS_LOCK:
        c = _users_conn()
        try:
            rows = c.execute("SELECT uid,name,email,role,status,created,email_verified,email_optout,"
                             "last_login,auth_source,birthdate"
                             " FROM users ORDER BY created").fetchall()
        finally:
            c.close()
    return [{"uid": r[0], "name": r[1], "email": r[2], "role": r[3], "status": r[4], "created": r[5],
             "email_verified": int(r[6] or 0), "email_optout": int(r[7] or 0),
             "last_login": r[8] or 0, "auth_source": r[9] or "local",
             "birthdate": r[10] or ""} for r in rows]

def user_touch_login(uid):

    try:
        with _USERS_LOCK:
            c = _users_conn()
            try:
                c.execute("UPDATE users SET last_login=? WHERE uid=?", (time.time(), _uid_safe(uid)))
                c.commit()
            finally:
                c.close()
    except Exception:
        pass

def user_mark_approved(uid, by):

    try:
        with _USERS_LOCK:
            c = _users_conn()
            try:
                c.execute("UPDATE users SET approved_by=?, approved_at=? WHERE uid=?",
                          (str(by or "")[:60], time.time(), _uid_safe(uid)))
                c.commit()
            finally:
                c.close()
    except Exception:
        pass

def user_update(uid, name=None, email=None, role=None, status=None, email_optout=None,
                birthdate=None):

    uid = _uid_safe(uid)
    sets, args = [], []
    if name is not None:
        sets.append("name=?"); args.append(str(name)[:120])
    if email is not None:
        sets.append("email=?"); args.append(str(email).strip()[:200])
        sets.append("email_verified=0")
    if role is not None:
        if role not in ("owner", "admin", "user", "guest", "kid"):
            return {"ok": False, "error": "bad role"}
        sets.append("role=?"); args.append(role)
    if birthdate is not None:
        if not _birthdate_ok(birthdate):
            return {"ok": False, "error": "Geburtsdatum: leer oder JJJJ-MM-TT (z. B. 2018-05-01)"}
        sets.append("birthdate=?"); args.append(str(birthdate))
    if status is not None:
        if status not in ("active", "disabled", "pending"):
            return {"ok": False, "error": "bad status"}
        sets.append("status=?"); args.append(status)
    if email_optout is not None:
        sets.append("email_optout=?"); args.append(1 if email_optout else 0)
    if not sets:
        return {"ok": False, "error": "nothing to update"}
    with _USERS_LOCK:
        c = _users_conn()
        try:
            cur = c.execute("UPDATE users SET %s WHERE uid=?" % ",".join(sets), (*args, uid))
            c.commit()
        finally:
            c.close()

    if status is not None and status != "active" and cur.rowcount:
        session_revoke_all(uid, "status=" + status)
    return {"ok": bool(cur.rowcount), "updated": cur.rowcount}

_REVOKE_LOCK = threading.Lock()
_REVOKE_CACHE = {"key": None, "v": {}}

def _revoke_path():

    return os.path.join(DATA_DIR, "session_revocations.json")

def session_revocations():

    import json as _json
    p = _revoke_path()
    try:
        st = os.stat(p); key = (st.st_mtime_ns, st.st_size)
    except OSError:
        _REVOKE_CACHE["key"], _REVOKE_CACHE["v"] = None, {}
        return {}
    if key != _REVOKE_CACHE["key"]:
        try:
            with open(p) as fh:
                d = _json.load(fh)
            v = {str(k): float(x) for k, x in d.items()} if isinstance(d, dict) else {}
        except Exception:
            return _REVOKE_CACHE["v"]

        _REVOKE_CACHE["key"], _REVOKE_CACHE["v"] = key, v
    return _REVOKE_CACHE["v"]

def session_revoked_at(uid):

    try:
        return float(session_revocations().get(_uid_safe(uid), 0.0) or 0.0)
    except Exception:
        return 0.0

def session_revoke_all(uid, reason=""):

    import json as _json
    uid = _uid_safe(uid)
    now = time.time()
    with _REVOKE_LOCK:
        p = _revoke_path()
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            try:
                with open(p) as fh:
                    cur = _json.load(fh)
                if not isinstance(cur, dict):
                    cur = {}
            except Exception:
                cur = {}
            cur[uid] = now
            tmp = p + ".tmp"
            with open(tmp, "w") as fh:
                _json.dump(cur, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, p)
            _REVOKE_CACHE["key"] = None
            return True
        except Exception:
            return False

def user_delete(uid):

    uid = _uid_safe(uid)
    u = user_get(uid)
    if not u:
        return {"ok": False, "error": "unbekanntes Konto"}
    if u.get("role") == "owner":
        return {"ok": False, "error": "owner ist nicht loeschbar"}
    with _USERS_LOCK:
        c = _users_conn()
        try:
            c.execute("DELETE FROM users WHERE uid=?", (uid,))
            c.commit()
        finally:
            c.close()

    revoked = session_revoke_all(uid, "user_delete")
    return {"ok": True, "sessions_revoked": revoked}

def user_set_password(uid, password):

    import secrets as _secrets
    salt = _secrets.token_hex(16)
    with _USERS_LOCK:
        c = _users_conn()
        try:
            cur = c.execute("UPDATE users SET pw_hash=?, pw_salt=?, pw_algo=?, pw_changed=?,"
                            " status='active' WHERE uid=?",
                            (_pw_hash(password, salt), salt, PW_ALGO, time.time(), _uid_safe(uid)))
            c.commit(); _changed = cur.rowcount > 0
        finally:
            c.close()
    if _changed:

        session_revoke_all(uid, "password_change")
        _sso_sync(uid, password)
    return _changed

def user_get_by_email(email):

    email = (email or "").strip().lower()
    if not email:
        return None
    with _USERS_LOCK:
        c = _users_conn()
        try:
            r = c.execute("SELECT uid FROM users WHERE lower(email)=? AND status!='deleted'", (email,)).fetchone()
        finally:
            c.close()
    return r[0] if r else None

def user_set_email_verified(uid, val=1):
    with _USERS_LOCK:
        c = _users_conn()
        try:
            c.execute("UPDATE users SET email_verified=? WHERE uid=?", (1 if val else 0, _uid_safe(uid)))
            c.commit()
        finally:
            c.close()

def auth_token_new(uid, kind, email="", ttl=3600):

    import secrets as _secrets
    tok = _secrets.token_urlsafe(32); now = time.time()
    with _USERS_LOCK:
        c = _users_conn()
        try:
            c.execute("INSERT OR REPLACE INTO auth_tokens(token,uid,kind,email,created,expires,used)"
                      " VALUES(?,?,?,?,?,?,0)", (tok, _uid_safe(uid), kind, email, now, now + ttl))
            c.commit()
        finally:
            c.close()
    return tok

def auth_token_consume(tok, kind):

    if not tok:
        return None
    now = time.time()
    with _USERS_LOCK:
        c = _users_conn()
        try:
            r = c.execute("SELECT uid,kind,expires,used FROM auth_tokens WHERE token=?", (tok,)).fetchone()
            if not r or r[1] != kind or r[3] or (r[2] or 0) < now:
                return None
            c.execute("UPDATE auth_tokens SET used=1 WHERE token=?", (tok,)); c.commit()
            return r[0]
        finally:
            c.close()

_OWNER_SEED_STAMP = None

def _cfg_pin():

    import json
    try:
        with open(CFG_PATH) as f:
            v = json.load(f)
        return (v.get("pin") or None) if isinstance(v, dict) else None
    except Exception:
        return None

def _site_conf_get(key):

    import shlex
    try:
        for ln in open(SITE_CONF):
            s = ln.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            if k.strip() != key:
                continue
            try:
                toks = shlex.split(v, posix=True)
            except ValueError:
                toks = [v.strip().strip(chr(34) + chr(39))]
            val = (toks[0] if toks else "").strip()
            return val or None
    except Exception:
        pass
    return None

def _cfg_owner_name():

    import json
    try:
        with open(CFG_PATH) as f:
            v = json.load(f)
        if isinstance(v, dict):
            n = (v.get("owner_name") or "").strip()
            if n:
                return n
    except Exception:
        pass
    return _site_conf_get("OWNER_NAME")

def _owner_display_name():

    return _cfg_owner_name() or _OWNER_NAME_DEFAULT

def _pin_fingerprint(c, pin):

    import secrets as _secrets
    salt = _meta_get(c, "owner_seed_salt")
    if not salt:
        salt = _secrets.token_hex(16); _meta_set(c, "owner_seed_salt", salt)
    return _pw_hash(pin, salt)

def _ensure_owner_user(pin=None):

    pin = _cfg_pin() or pin
    if not pin:
        return

    try:
        st = os.stat(CFG_PATH); stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    global _OWNER_SEED_STAMP
    owner = user_get(DEFAULT_PRINCIPAL)
    if stamp is not None and stamp == _OWNER_SEED_STAMP and owner is not None:
        return
    _OWNER_SEED_STAMP = stamp
    if owner is None:
        user_create(DEFAULT_PRINCIPAL, pin, name=_owner_display_name(), role="owner")
    elif not user_verify(DEFAULT_PRINCIPAL, pin):
        with _USERS_LOCK:
            c = _users_conn()
            try:
                prev = _meta_get(c, "owner_seed_fp")
                cur = _pin_fingerprint(c, pin)
                reseed = prev is not None and prev != cur
                c.commit()
            finally:
                c.close()
        if reseed:
            user_create(DEFAULT_PRINCIPAL, pin, name=_owner_display_name(), role="owner")
    _ensure_owner_name()
    _record_owner_seed(pin)

def _ensure_owner_name():

    name = _cfg_owner_name()
    if not name or name == _OWNER_NAME_DEFAULT:
        return
    with _USERS_LOCK:
        c = _users_conn()
        try:
            c.execute("UPDATE users SET name=? WHERE uid=?"
                      "   AND (name IS NULL OR name='' OR name=?)",
                      (name, _uid_safe(DEFAULT_PRINCIPAL), _OWNER_NAME_DEFAULT))
            c.commit()
        finally:
            c.close()

def _record_owner_seed(pin):

    with _USERS_LOCK:
        c = _users_conn()
        try:
            _meta_set(c, "owner_seed_fp", _pin_fingerprint(c, pin)); c.commit()
        finally:
            c.close()

_LOGIN_WINDOW = 300
_LOGIN_MAX_FAILS = 5
_LOGIN_MAX_KEYS = 4096
_LOGIN_FAILS = {}
_LOGIN_LOCK = threading.Lock()

def _login_sweep(now):

    for k in [k for k, v in _LOGIN_FAILS.items() if not v or now - v[-1] >= _LOGIN_WINDOW]:
        _LOGIN_FAILS.pop(k, None)
    if len(_LOGIN_FAILS) > _LOGIN_MAX_KEYS:
        for k, _ in sorted(_LOGIN_FAILS.items(), key=lambda kv: kv[1][-1])[:len(_LOGIN_FAILS) - _LOGIN_MAX_KEYS]:
            _LOGIN_FAILS.pop(k, None)

def _login_locked(key):
    now = time.time()
    with _LOGIN_LOCK:
        fails = [t for t in _LOGIN_FAILS.get(key, []) if now - t < _LOGIN_WINDOW]
        if fails:
            _LOGIN_FAILS[key] = fails
        else:
            _LOGIN_FAILS.pop(key, None)
        return len(fails) >= _LOGIN_MAX_FAILS
def _login_fail(key):
    now = time.time()
    with _LOGIN_LOCK:
        _LOGIN_FAILS.setdefault(key, []).append(now)
        del _LOGIN_FAILS[key][:-_LOGIN_MAX_FAILS]
        _login_sweep(now)
def _login_ok(key):
    with _LOGIN_LOCK:
        _LOGIN_FAILS.pop(key, None)
