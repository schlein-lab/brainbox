#!/usr/bin/env python3

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pnlib import schema_bitemporal as bt

try:
    from pnlib import db as pndb
except Exception:
    pndb = None

def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="bitemporal_test_")
    os.close(fd)
    os.unlink(path)
    return path

def _cleanup(path):
    for p in (path, path + "-wal", path + "-shm"):
        try:
            os.unlink(p)
        except OSError:
            pass

def _colinfo(cx, table):
    return {r["name"]: r for r in cx.execute(f"PRAGMA table_info({table})")}

def test_migrate_fresh_db_no_error():
    path = _tmp_db()
    try:
        cx = sqlite3.connect(path)
        cx.row_factory = sqlite3.Row
        bt.migrate(cx)
        names = {r["name"] for r in cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "bitemporal_log" in names, names
        cx.close()
    finally:
        _cleanup(path)

def test_migrate_over_live_schema_no_error():
    if pndb is None:

        return
    path = _tmp_db()
    try:

        cx = pndb.connect(path)
        bt.migrate(cx)
        names = {r["name"] for r in cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "bitemporal_log" in names
        assert "jobs" in names
        cx.close()
    finally:
        _cleanup(path)

def test_invariant_columns_notnull():
    path = _tmp_db()
    try:
        cx = bt.connect(path)
        cols = _colinfo(cx, "bitemporal_log")
        for req in ("origin", "valid_time", "observed_time"):
            assert req in cols, f"{req} missing from bitemporal_log"
            assert cols[req]["notnull"] == 1, f"{req} must be NOT NULL (got {cols[req]['notnull']})"

        assert cols["seq"]["pk"] == 1, "seq must be the primary key"
        cx.close()
    finally:
        _cleanup(path)

def test_insert_without_origin_raises():
    path = _tmp_db()
    try:
        cx = bt.connect(path)
        raised = False
        try:
            cx.execute(
                "INSERT INTO bitemporal_log(valid_time,observed_time,payload_json) "
                "VALUES(?,?,?)", (1.0, 2.0, "{}"))
            cx.commit()
        except sqlite3.IntegrityError:
            raised = True
        assert raised, "INSERT without origin must raise IntegrityError (NOT NULL)"
        cx.close()
    finally:
        _cleanup(path)

def test_insert_bad_origin_raises():
    path = _tmp_db()
    try:
        cx = bt.connect(path)
        raised = False
        try:
            cx.execute(
                "INSERT INTO bitemporal_log(valid_time,observed_time,origin,payload_json) "
                "VALUES(?,?,?,?)", (1.0, 2.0, "robot", "{}"))
            cx.commit()
        except sqlite3.IntegrityError:
            raised = True
        assert raised, "origin='robot' must raise IntegrityError (CHECK human|agent|model)"
        cx.close()
    finally:
        _cleanup(path)

def test_insert_missing_times_raises():
    path = _tmp_db()
    try:
        cx = bt.connect(path)
        for cols_sql, vals in (
            ("observed_time,origin,payload_json", (2.0, "human", "{}")),
            ("valid_time,origin,payload_json", (1.0, "human", "{}")),
        ):
            raised = False
            try:
                cx.execute(f"INSERT INTO bitemporal_log({cols_sql}) "
                           f"VALUES({','.join('?' * len(vals))})", vals)
                cx.commit()
            except sqlite3.IntegrityError:
                raised = True
            assert raised, f"missing time column must raise (cols={cols_sql})"
        cx.close()
    finally:
        _cleanup(path)

def test_valid_append_all_origins_monotonic():
    path = _tmp_db()
    try:
        cx = bt.connect(path)
        seqs = []
        for i, origin in enumerate(bt.ORIGINS):
            s = bt.append(cx, stream="ledger", valid_time=float(i), observed_time=float(i) + 0.5,
                          origin=origin, payload_json='{"n":%d}' % i, actor="alice",
                          agent_id="brain-1", ledger_seq=None)
            seqs.append(s)
        assert seqs == sorted(seqs) and len(set(seqs)) == 3, f"seq must be monotonic/unique: {seqs}"
        n = cx.execute("SELECT COUNT(*) c FROM bitemporal_log").fetchone()["c"]
        assert n == 3, n

        raised = False
        try:
            bt.append(cx, stream="memory", valid_time=1.0, observed_time=1.0,
                      origin="nope", payload_json="{}")
        except ValueError:
            raised = True
        assert raised, "append() must reject an out-of-taxonomy origin"
        cx.close()
    finally:
        _cleanup(path)

def test_append_only_update_delete_forbidden():
    path = _tmp_db()
    try:
        cx = bt.connect(path)
        seq = bt.append(cx, stream="memory", valid_time=1.0, observed_time=2.0,
                        origin="agent", payload_json='{"fact":"x"}')
        upd = False
        try:
            cx.execute("UPDATE bitemporal_log SET payload_json='{\"fact\":\"y\"}' WHERE seq=?",
                       (seq,))
            cx.commit()
        except sqlite3.IntegrityError:
            upd = True
        assert upd, "UPDATE must be blocked (append-only)"
        dele = False
        try:
            cx.execute("DELETE FROM bitemporal_log WHERE seq=?", (seq,))
            cx.commit()
        except sqlite3.IntegrityError:
            dele = True
        assert dele, "DELETE must be blocked (append-only)"

        still = cx.execute("SELECT payload_json FROM bitemporal_log WHERE seq=?",
                           (seq,)).fetchone()["payload_json"]
        assert still == '{"fact":"x"}', still
        cx.close()
    finally:
        _cleanup(path)

def test_durability_pragmas_and_idempotent():
    path = _tmp_db()
    try:
        cx = bt.connect(path)
        jm = cx.execute("PRAGMA journal_mode").fetchone()[0]
        assert jm.lower() == "wal", f"journal_mode must be WAL, got {jm}"
        sync = cx.execute("PRAGMA synchronous").fetchone()[0]
        assert sync == 2, f"synchronous must be FULL (2), got {sync}"
        bt.migrate(cx)
        bt.migrate(cx)
        cx.close()
    finally:
        _cleanup(path)

_TESTS = [
    test_migrate_fresh_db_no_error,
    test_migrate_over_live_schema_no_error,
    test_invariant_columns_notnull,
    test_insert_without_origin_raises,
    test_insert_bad_origin_raises,
    test_insert_missing_times_raises,
    test_valid_append_all_origins_monotonic,
    test_append_only_update_delete_forbidden,
    test_durability_pragmas_and_idempotent,
]

def main():
    p = f = 0
    for t in _TESTS:
        try:
            t()
            p += 1
            print(f"  PASS  {t.__name__}")
        except Exception as e:
            f += 1
            print(f"  FAIL  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n=== {p} passed, {f} failed ===")
    sys.exit(1 if f else 0)

if __name__ == "__main__":
    main()
