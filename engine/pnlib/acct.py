
from __future__ import annotations

import json
import math
import os
import sqlite3
import time

ALPHA = 0.2

FAIRSHARE_HALFLIFE_S = 86400.0

EWMA_DIMS = ("mem", "cpu_weight", "llm_weight")

SCHEMA = """
PRAGMA journal_mode=WAL;
-- per-task-type rolled aggregates: one row per task_type. The EWMAs are maintained
-- INCREMENTALLY by the writer (record_actual) so a reader only ever point-reads a row.
CREATE TABLE IF NOT EXISTS type_ewma (
  task_type   TEXT PRIMARY KEY,
  n           INTEGER NOT NULL DEFAULT 0,   -- samples folded in (0 = never seen)
  mem         REAL,                         -- EWMA peak RSS (MiB)
  cpu_weight  REAL,                         -- EWMA cpu-seconds -> a cpu_weight proxy (see note)
  llm_weight  REAL,                         -- EWMA actual LLM calls (the llm dimension)
  svc_s       REAL,                         -- EWMA wall-clock service time (seconds)
  updated_at  REAL
);
-- per-principal DECAYED usage for multifactor fair-share. `usage` is decayed to `updated_at`
-- on every fold, so a reader multiplies by the decay-since-updated_at to get the live value.
CREATE TABLE IF NOT EXISTS principal_usage (
  principal   TEXT PRIMARY KEY,
  usage       REAL NOT NULL DEFAULT 0.0,    -- decayed sum of (mem-sec + cpu-sec + llm-calls)
  updated_at  REAL
);
-- the durable cursor: the last queue.db job_events.id pn-acctd folded, so a restart resumes
-- exactly where it left off and never double-counts (idempotent catch-up).
CREATE TABLE IF NOT EXISTS acct_meta (k TEXT PRIMARY KEY, v TEXT);
-- optional append-only raw sample log (bounded by pn-acctd's GC) for sreport-style history.
CREATE TABLE IF NOT EXISTS job_actuals (
  job_id     INTEGER PRIMARY KEY,           -- one row per finished job (idempotent re-fold)
  task_type  TEXT,
  principal  TEXT,
  mem_peak   REAL,
  cpu_s      REAL,
  wall_s     REAL,
  llm_calls  REAL,
  llm_tokens REAL,
  ts         REAL
);
CREATE INDEX IF NOT EXISTS idx_actuals_type ON job_actuals(task_type, job_id);
"""

def default_path() -> str:

    from pnlib import STATE_DIR
    return os.path.join(STATE_DIR, "acct.db")

class AcctStore:
    def __init__(self, path: str | None = None):
        self.path = path or default_path()
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.cx = sqlite3.connect(self.path, timeout=8.0)
        self.cx.row_factory = sqlite3.Row
        self.cx.execute("PRAGMA busy_timeout=8000")
        self.cx.execute("PRAGMA synchronous=NORMAL")
        self.cx.executescript(SCHEMA)
        self.cx.commit()

        try:
            self.cx.execute("ALTER TABLE job_actuals ADD COLUMN llm_tokens REAL")
            self.cx.commit()
        except sqlite3.OperationalError:
            pass

    def close(self):
        try:
            self.cx.close()
        except Exception:
            pass

    def get_cursor(self) -> int:
        r = self.cx.execute("SELECT v FROM acct_meta WHERE k='cursor'").fetchone()
        try:
            return int(r["v"]) if r else 0
        except (TypeError, ValueError):
            return 0

    def set_cursor(self, cursor: int):
        self.cx.execute("INSERT INTO acct_meta(k,v) VALUES('cursor',?) "
                        "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(int(cursor)),))
        self.cx.commit()

    def already_recorded(self, job_id: int) -> bool:
        r = self.cx.execute("SELECT 1 FROM job_actuals WHERE job_id=?", (int(job_id),)).fetchone()
        return r is not None

    @staticmethod
    def _fold(old, sample):

        if sample is None:
            return old
        s = float(sample)
        if old is None:
            return s
        return (1.0 - ALPHA) * float(old) + ALPHA * s

    def record_actual(self, job_id, task_type, principal,
                      mem_peak=None, cpu_s=None, wall_s=None, llm_calls=None,
                      llm_tokens=None, now=None) -> bool:

        jid = int(job_id)
        if self.already_recorded(jid):
            return False
        now = now if now is not None else time.time()
        tt = task_type or "(raw)"

        self.cx.execute(
            "INSERT OR IGNORE INTO job_actuals"
            "(job_id,task_type,principal,mem_peak,cpu_s,wall_s,llm_calls,llm_tokens,ts)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (jid, tt, principal,
             _f(mem_peak), _f(cpu_s), _f(wall_s), _f(llm_calls), _f(llm_tokens), now))

        row = self.cx.execute("SELECT * FROM type_ewma WHERE task_type=?", (tt,)).fetchone()
        cur = dict(row) if row else {"n": 0, "mem": None, "cpu_weight": None,
                                     "llm_weight": None, "svc_s": None}
        mem = self._fold(cur["mem"], mem_peak)

        cpu = self._fold(cur["cpu_weight"], cpu_s)
        llm = self._fold(cur["llm_weight"], llm_calls)
        svc = self._fold(cur["svc_s"], wall_s)
        self.cx.execute(
            "INSERT INTO type_ewma(task_type,n,mem,cpu_weight,llm_weight,svc_s,updated_at)"
            " VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(task_type) DO UPDATE SET"
            " n=excluded.n, mem=excluded.mem, cpu_weight=excluded.cpu_weight,"
            " llm_weight=excluded.llm_weight, svc_s=excluded.svc_s, updated_at=excluded.updated_at",
            (tt, int(cur["n"]) + 1, mem, cpu, llm, svc, now))

        if principal:
            cost = (_f(mem_peak) or 0.0) * (_f(wall_s) or 0.0) / 60.0 \
                + (_f(cpu_s) or 0.0) + (_f(llm_calls) or 0.0)
            self._add_usage(principal, cost, now)
        self.cx.commit()
        return True

    def _add_usage(self, principal, cost, now):
        r = self.cx.execute("SELECT usage,updated_at FROM principal_usage WHERE principal=?",
                            (principal,)).fetchone()
        if r:
            decayed = _decay(r["usage"], r["updated_at"], now)
            usage = decayed + cost
        else:
            usage = cost
        self.cx.execute(
            "INSERT INTO principal_usage(principal,usage,updated_at) VALUES(?,?,?)"
            " ON CONFLICT(principal) DO UPDATE SET usage=excluded.usage, updated_at=excluded.updated_at",
            (principal, usage, now))

    def gc(self, keep=20000):

        self.cx.execute(
            "DELETE FROM job_actuals WHERE job_id NOT IN "
            "(SELECT job_id FROM job_actuals ORDER BY job_id DESC LIMIT ?)", (int(keep),))
        self.cx.commit()

class AcctReader:
    def __init__(self, path: str | None = None, busy_timeout_ms: int = 100):
        self.path = path or default_path()
        self.busy_timeout_ms = int(busy_timeout_ms)

    def _connect(self):
        try:
            cx = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=0.1)
        except sqlite3.OperationalError:
            cx = sqlite3.connect(self.path, timeout=0.1)
        cx.row_factory = sqlite3.Row
        cx.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        return cx

    def type_estimate(self, task_type) -> dict:

        if not task_type:
            return {}
        try:
            cx = self._connect()
            try:
                r = cx.execute(
                    "SELECT n,mem,cpu_weight,llm_weight FROM type_ewma WHERE task_type=?",
                    (task_type,)).fetchone()
            finally:
                cx.close()
        except Exception:
            return {}
        if not r or not r["n"]:
            return {}
        out = {}
        for dim in EWMA_DIMS:
            v = r[dim]
            if v is not None:

                out[dim] = int(round(float(v)))
        return out

    def type_service_time(self, task_type) -> float | None:

        if not task_type:
            return None
        try:
            cx = self._connect()
            try:
                r = cx.execute("SELECT n,svc_s FROM type_ewma WHERE task_type=?",
                               (task_type,)).fetchone()
            finally:
                cx.close()
        except Exception:
            return None
        if not r or not r["n"] or r["svc_s"] is None:
            return None
        return float(r["svc_s"])

    def all_service_times(self) -> dict:

        try:
            cx = self._connect()
            try:
                rows = cx.execute("SELECT task_type,svc_s FROM type_ewma "
                                  "WHERE n>0 AND svc_s IS NOT NULL").fetchall()
            finally:
                cx.close()
        except Exception:
            return {}
        return {r["task_type"]: float(r["svc_s"]) for r in rows}

    def global_service_time(self) -> float | None:

        try:
            cx = self._connect()
            try:
                r = cx.execute("SELECT SUM(n*svc_s) AS num, SUM(n) AS den FROM type_ewma "
                               "WHERE n>0 AND svc_s IS NOT NULL AND svc_s>0").fetchone()
            finally:
                cx.close()
        except Exception:
            return None
        if not r or not r["den"]:
            return None
        return float(r["num"]) / float(r["den"])

    def principal_usage(self, principal, now=None) -> float:

        if not principal:
            return 0.0
        now = now if now is not None else time.time()
        try:
            cx = self._connect()
            try:
                r = cx.execute("SELECT usage,updated_at FROM principal_usage WHERE principal=?",
                               (principal,)).fetchone()
            finally:
                cx.close()
        except Exception:
            return 0.0
        if not r:
            return 0.0
        return _decay(r["usage"], r["updated_at"], now)

def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None

def _decay(usage, updated_at, now) -> float:

    try:
        u = float(usage)
        dt = max(0.0, float(now) - float(updated_at))
    except (TypeError, ValueError):
        return 0.0
    if u <= 0.0:
        return 0.0
    return u * math.pow(0.5, dt / FAIRSHARE_HALFLIFE_S)
