#!/usr/bin/env python3

import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pnlib.memory.store import MemoryStore
from pnlib.memory.anchored import (
    AnchoredMemory, LedgerWriterSink, NoOpLedgerSink, default_sink,
)

def _tmp_path(tag):
    fd, path = tempfile.mkstemp(suffix=".db", prefix=f"memq_{tag}_")
    os.close(fd)
    os.unlink(path)
    return path

def _cleanup(*paths):
    for base in paths:
        for p in (base, base + "-wal", base + "-shm", base + ".spool", base + ".spool.cursor"):
            try:
                os.unlink(p)
            except OSError:
                pass

def _anchored(with_ledger):

    mem = _tmp_path("mem")
    if with_ledger:
        led = _tmp_path("led")
        sink = LedgerWriterSink.open(led)
        am = AnchoredMemory(MemoryStore(mem, dim=64), sink)
        return am, (mem, led)
    am = AnchoredMemory(MemoryStore(mem, dim=64), NoOpLedgerSink())
    return am, (mem,)

def test_ranked_recall_returns_nearest():
    am, paths = _anchored(with_ledger=False)
    try:
        am.remember("the queue scheduler leases cpu cores fairly", origin="human")
        am.remember("firefox rendering pipeline uses software gl", origin="human")
        am.remember("backups run nightly to the nas over ssh", origin="agent")
        hits = am.recall("cpu core scheduler fairness lease", k=3)
        assert hits, "expected at least one recall hit"
        assert "queue scheduler" in hits[0]["text"], f"nearest wrong: {hits[0]['text']!r}"

        dists = [h["distance"] for h in hits]
        assert dists == sorted(dists), f"recall not distance-ranked: {dists}"
    finally:
        am.close(); _cleanup(*paths)

def test_every_row_anchored_when_ledger_present():
    am, paths = _anchored(with_ledger=True)
    try:
        assert am.sink_present is True, "LedgerWriterSink must report present"
        facts = [
            ("the scheduler leases cpu cores fairly", "human"),
            ("software gl paints the first firefox frame", "agent"),
            ("nightly backups stream to the nas", "agent"),
        ]
        results = [am.remember(t, origin=o) for t, o in facts]

        for r in results:
            assert r.origin in ("human", "agent"), r.origin
            assert r.ledger_seq is not None, "anchored write must return a ledger_seq"
            assert r.anchored is True

        rows = am.store.cx.execute(
            "SELECT seq, origin, ledger_seq FROM bitemporal_log WHERE stream='memory'").fetchall()
        assert len(rows) == len(facts), f"expected {len(facts)} memory rows, got {len(rows)}"
        for row in rows:
            assert row["origin"] is not None, "memory row missing origin"
            assert row["ledger_seq"] is not None, f"memory row seq={row['seq']} not anchored"

        led_cx = am.sink._store.cx
        for r in results:
            lrow = led_cx.execute(
                "SELECT stream, origin, payload_json FROM bitemporal_log WHERE seq=?",
                (r.ledger_seq,)).fetchone()
            assert lrow is not None, f"ledger_seq {r.ledger_seq} points at no ledger row"
            assert lrow["stream"] == "ledger", f"cross-link is not a ledger row: {lrow['stream']}"
            env = json.loads(lrow["payload_json"])
            assert env["event"]["kind"] == "memory.remember", f"unexpected leaf event: {env}"
            assert env["origin"]["component"] == "pn-memory"
    finally:
        am.close(); _cleanup(*paths)

def test_noop_sink_leaves_ledger_seq_null():
    am, paths = _anchored(with_ledger=False)
    try:
        assert am.sink_present is False, "NoOpLedgerSink must report NOT present"
        assert isinstance(default_sink(None), NoOpLedgerSink), "no path -> no-op sink"
        r = am.remember("an un-anchored but stamped memory", origin="human")
        assert r.origin == "human"
        assert r.ledger_seq is None and r.anchored is False, "no-op sink must not anchor"
        row = am.store.cx.execute(
            "SELECT origin, ledger_seq FROM bitemporal_log WHERE seq=?", (r.seq,)).fetchone()
        assert row["origin"] == "human", "origin must still be stamped without a ledger"
        assert row["ledger_seq"] is None, "no-op sink must leave ledger_seq NULL"
    finally:
        am.close(); _cleanup(*paths)

def test_anticollapse_excludes_model_by_default():
    am, paths = _anchored(with_ledger=True)
    try:

        am.remember("solar inverter efficiency ratings measured across the datasheet range",
                    origin="human")
        am.remember("solar inverter efficiency", origin="model")
        q = "solar inverter efficiency"

        default_hits = am.recall(q, k=5)
        origins = {h["origin"] for h in default_hits}
        assert "model" not in origins, f"anti-collapse breached in feedback query: {origins}"
        assert any(h["origin"] == "human" for h in default_hits), "human fact should survive"

        opened = am.recall(q, k=5, include_model=True)
        assert any(h["origin"] == "model" for h in opened), "include_model=True must admit model rows"
        assert opened[0]["origin"] == "model", (
            f"expected model NEAREST (proving the default filter did real work), got {opened[0]['origin']}")

        model_hit = next(h for h in opened if h["origin"] == "model")
        assert model_hit["ledger_seq"] is not None, "even a model memory must be anchored"
    finally:
        am.close(); _cleanup(*paths)

def test_as_of_returns_state_at_past_observed_time():
    am, paths = _anchored(with_ledger=False)
    try:
        r_early = am.remember("early fact about the ledger", origin="human", observed_time=1000.0)
        am.remember("later fact about the ledger", origin="human", observed_time=2000.0)

        seen = am.as_of("ledger fact", observed_time=1500.0, k=5)
        seqs = {h["seq"] for h in seen}
        assert r_early.seq in seqs, "the early row must be visible as-of t=1500"
        assert all(h["observed_time"] <= 1500.0 for h in seen), "as-of must bound observed_time"

        assert all("later fact" not in (h["text"] or "") for h in seen), "future row leaked into as-of"

        both = am.as_of("ledger fact", observed_time=9999999999.0, k=5)
        assert len(both) == 2, f"as-of now should see both rows, saw {len(both)}"
    finally:
        am.close(); _cleanup(*paths)

_TESTS = [
    test_ranked_recall_returns_nearest,
    test_every_row_anchored_when_ledger_present,
    test_noop_sink_leaves_ledger_seq_null,
    test_anticollapse_excludes_model_by_default,
    test_as_of_returns_state_at_past_observed_time,
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
