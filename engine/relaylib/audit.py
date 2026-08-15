
from __future__ import annotations
import sqlite3, hashlib, json, time, os, threading

try:
    import fcntl as _fcntl
except Exception:
    _fcntl = None

GENESIS = "0" * 64

_APPEND_LOCK = threading.Lock()

_LOCK_FDS: dict[str, int] = {}
_LOCK_FDS_GUARD = threading.Lock()

def _lock_path_for(cx) -> str | None:

    try:
        row = cx.execute("PRAGMA database_list").fetchone()

        dbfile = row[2] if row is not None else None
    except Exception:
        dbfile = None
    if not dbfile:
        return None
    return dbfile + ".audit.lock"

def _lock_fd(path: str) -> int:
    with _LOCK_FDS_GUARD:
        fd = _LOCK_FDS.get(path)
        if fd is None:

            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            _LOCK_FDS[path] = fd
        return fd

class _AppendGuard:

    def __init__(self, cx):
        self._lock_path = _lock_path_for(cx)
        self._fd = None

    def __enter__(self):
        _APPEND_LOCK.acquire()
        if _fcntl is not None and self._lock_path:
            try:
                self._fd = _lock_fd(self._lock_path)
                _fcntl.flock(self._fd, _fcntl.LOCK_EX)
            except Exception:
                self._fd = None
        return self

    def __exit__(self, *exc):
        if self._fd is not None:
            try:
                _fcntl.flock(self._fd, _fcntl.LOCK_UN)
            except Exception:
                pass
        _APPEND_LOCK.release()
        return False

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,  -- strictly monotonic; gaps reveal deletion
  ts         REAL NOT NULL,
  event      TEXT NOT NULL,        -- e.g. 'pair.redeem', 'twofa.fail', 'submit.broker', 'revoke'
  device_did TEXT,                 -- the device this concerns, when applicable
  principal  TEXT,                 -- the human principal this concerns, when applicable
  detail     TEXT NOT NULL,        -- JSON: redacted, NEVER secrets/tokens/codes/plaintext
  prev_hash  TEXT NOT NULL,        -- sha256 of the previous record (chain link)
  rec_hash   TEXT NOT NULL         -- sha256(prev_hash || canonical(this record)) — the chain
);
CREATE INDEX IF NOT EXISTS idx_audit_did ON audit_log(device_did, seq);
CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_log(event, seq);
"""

_COMMIT_KEYS = ("ts", "event", "device_did", "principal", "detail", "prev_hash")

def init(cx: sqlite3.Connection):
    cx.executescript(SCHEMA)
    cx.commit()

def _canonical(rec: dict) -> bytes:
    return json.dumps({k: rec.get(k) for k in _COMMIT_KEYS},
                      separators=(",", ":"), sort_keys=True).encode()

def _hash(prev_hash: str, rec: dict) -> str:
    return hashlib.sha256(prev_hash.encode() + _canonical(rec)).hexdigest()

def _last(cx) -> tuple[int, str]:
    r = cx.execute("SELECT seq, rec_hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    if not r:
        return (0, GENESIS)
    return (r["seq"], r["rec_hash"])

_FORBIDDEN = {"token", "code", "secret", "totp_secret", "proof", "sig", "blob",
              "plaintext", "password", "private", "priv"}

def _redact(detail: dict | None) -> dict:
    d = dict(detail or {})
    for k in list(d):
        if k.lower() in _FORBIDDEN or any(f in k.lower() for f in _FORBIDDEN):
            d[k] = "<redacted>"
    return d

def record(cx, event: str, *, device_did: str | None = None, principal: str | None = None,
           detail: dict | None = None) -> int:

    init(cx)
    with _AppendGuard(cx):

        try:
            cx.commit()
        except Exception:
            pass
        _, prev = _last(cx)

        rec = {"ts": time.time(), "event": event, "device_did": device_did,
               "principal": principal, "detail": json.dumps(_redact(detail), sort_keys=True),
               "prev_hash": prev}
        rh = _hash(prev, rec)
        cur = cx.execute(
            "INSERT INTO audit_log(ts,event,device_did,principal,detail,prev_hash,rec_hash) "
            "VALUES(?,?,?,?,?,?,?)",
            (rec["ts"], rec["event"], rec["device_did"], rec["principal"], rec["detail"], prev, rh))
        cx.commit()
        return cur.lastrowid

def verify_chain(cx) -> tuple[bool, int | None, str]:

    init(cx)
    prev = GENESIS
    expect_seq = None
    for row in cx.execute("SELECT * FROM audit_log ORDER BY seq ASC"):
        if expect_seq is None:
            expect_seq = row["seq"]
        elif row["seq"] != expect_seq:
            return (False, expect_seq, f"sequence gap (deleted record before seq {row['seq']})")
        rec = {k: row[k] for k in _COMMIT_KEYS}
        if row["prev_hash"] != prev:
            return (False, row["seq"], "prev_hash does not chain to the previous record")
        if _hash(prev, rec) != row["rec_hash"]:
            return (False, row["seq"], "record hash mismatch (content was modified)")
        prev = row["rec_hash"]
        expect_seq += 1
    return (True, None, "chain intact")

def tail(cx, limit: int = 50, *, device_did: str | None = None, event: str | None = None) -> list:
    init(cx)
    q = "SELECT seq,ts,event,device_did,principal,detail FROM audit_log"
    cond, args = [], []
    if device_did:
        cond.append("device_did=?"); args.append(device_did)
    if event:
        cond.append("event=?"); args.append(event)
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY seq DESC LIMIT ?"; args.append(limit)
    rows = cx.execute(q, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["detail"] = json.loads(d["detail"])
        except Exception:
            pass
        out.append(d)
    return out
