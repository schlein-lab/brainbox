

import os
import sqlite3

ORIGINS = ("human", "agent", "model")

_BITEMPORAL_TABLES = """
CREATE TABLE IF NOT EXISTS bitemporal_log (
  seq            INTEGER PRIMARY KEY AUTOINCREMENT,
  stream         TEXT    NOT NULL DEFAULT 'ledger',   -- 'ledger' (T2) | 'memory' (T7)
  valid_time     REAL    NOT NULL,                    -- when the fact is TRUE in the world
  observed_time  REAL    NOT NULL,                    -- when the system RECORDED it
  origin         TEXT    NOT NULL
                 CHECK (origin IN ('human','agent','model')),
  actor          TEXT,                                -- principal/human behind the statement
  agent_id       TEXT,                                -- which agent/brain authored it
  ledger_seq     INTEGER,                             -- cross-link to a T2 ledger row (T7 join)
  payload_json   TEXT
);

-- as-of queries scan by the two clocks; the ledger chain and memory recall both index them.
CREATE INDEX IF NOT EXISTS idx_bitemporal_valid    ON bitemporal_log(valid_time);
CREATE INDEX IF NOT EXISTS idx_bitemporal_observed ON bitemporal_log(observed_time);
CREATE INDEX IF NOT EXISTS idx_bitemporal_stream   ON bitemporal_log(stream, seq);
CREATE INDEX IF NOT EXISTS idx_bitemporal_origin   ON bitemporal_log(origin, seq);
-- the T7->T2 provenance join (a memory row back to its source ledger entry).
CREATE INDEX IF NOT EXISTS idx_bitemporal_ledger   ON bitemporal_log(ledger_seq);

-- APPEND-ONLY, enforced at the DB. A correction is a NEW row (later observed_time), never an
-- in-place edit — so the T2 Merkle chain and the T7 as-of history can never be silently rewritten.
CREATE TRIGGER IF NOT EXISTS trg_bitemporal_no_update
  BEFORE UPDATE ON bitemporal_log
BEGIN
  SELECT RAISE(ABORT, 'bitemporal_log is append-only: UPDATE forbidden (write a new row)');
END;
CREATE TRIGGER IF NOT EXISTS trg_bitemporal_no_delete
  BEFORE DELETE ON bitemporal_log
BEGIN
  SELECT RAISE(ABORT, 'bitemporal_log is append-only: DELETE forbidden (supersede, do not erase)');
END;
"""

def migrate(cx: sqlite3.Connection) -> None:

    cx.executescript(_BITEMPORAL_TABLES)
    cx.commit()

def connect(path: str, durability: str = "full") -> sqlite3.Connection:

    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    cx = sqlite3.connect(path, timeout=10, check_same_thread=False)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA busy_timeout=8000")
    cx.execute("PRAGMA synchronous=" + ("FULL" if durability.lower() == "full" else "NORMAL"))
    cx.execute("PRAGMA foreign_keys=ON")
    migrate(cx)
    return cx

def append(cx, *, stream, valid_time, observed_time, origin, payload_json,
           actor=None, agent_id=None, ledger_seq=None) -> int:

    if origin not in ORIGINS:
        raise ValueError(f"origin must be one of {ORIGINS}, got {origin!r}")
    cur = cx.execute(
        "INSERT INTO bitemporal_log"
        "(stream,valid_time,observed_time,origin,actor,agent_id,ledger_seq,payload_json) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (stream, valid_time, observed_time, origin, actor, agent_id, ledger_seq, payload_json),
    )
    cx.commit()
    return int(cur.lastrowid)
