#!/usr/bin/env python3

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pnlib import schema_bitemporal as bt
from pnlib.memory.store import MemoryStore, StubEmbedder

def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="memory_test_")
    os.close(fd)
    os.unlink(path)
    return path

def _cleanup(path):
    for p in (path, path + "-wal", path + "-shm"):
        try:
            os.unlink(p)
        except OSError:
            pass

def _store():
    path = _tmp_db()
    return MemoryStore(path, dim=64), path

def test_roundtrip_recall_nearest():
    st, path = _store()
    try:
        st.remember("the queue scheduler leases cpu cores fairly", origin="human")
        st.remember("firefox rendering pipeline uses software gl", origin="human")
        st.remember("backups run nightly to the nas over ssh", origin="agent")
        hits = st.recall("cpu core scheduler fairness lease", k=3)
        assert hits, "expected at least one recall hit"
        assert "queue scheduler" in hits[0]["text"], f"nearest wrong: {hits[0]['text']!r}"
        assert hits[0]["distance"] <= hits[-1]["distance"], "hits must be distance-sorted"
    finally:
        st.close(); _cleanup(path)

def test_anticollapse_excludes_model_by_default():
    st, path = _store()
    try:

        st.remember("solar inverter efficiency ratings measured across the datasheet range",
                    origin="human")
        st.remember("solar inverter efficiency", origin="model")
        q = "solar inverter efficiency"

        default_hits = st.recall(q, k=5)
        origins = {h["origin"] for h in default_hits}
        assert "model" not in origins, f"anti-collapse breached: {origins}"
        assert any(h["origin"] == "human" for h in default_hits), "human fact should survive"

        opened = st.recall(q, k=5, include_model=True)
        assert any(h["origin"] == "model" for h in opened), "include_model=True must admit model rows"

        assert opened[0]["origin"] == "model", f"expected model nearest, got {opened[0]['origin']}"
    finally:
        st.close(); _cleanup(path)

def test_anticollapse_filter_present_in_sql():

    st, path = _store()
    try:
        st.remember("model-authored guess about disk failure rates", origin="model")
        assert st.recall("disk failure rates", k=5) == [], "model-only recall must be empty by default"
        assert len(st.recall("disk failure rates", k=5, include_model=True)) == 1

        assert st.count(include_model=True) == 1
        assert st.count(include_model=False) == 0
    finally:
        st.close(); _cleanup(path)

def test_remember_without_origin_is_typeerror():
    st, path = _store()
    try:
        try:
            st.remember("a fact with no provenance")
            raise AssertionError("remember() without origin must raise TypeError")
        except TypeError:
            pass
    finally:
        st.close(); _cleanup(path)

def test_remember_bad_origin_is_valueerror():
    st, path = _store()
    try:
        try:
            st.remember("a fact", origin="robot")
            raise AssertionError("out-of-taxonomy origin must raise ValueError")
        except ValueError:
            pass
    finally:
        st.close(); _cleanup(path)

def test_model_origin_flagged_in_row():
    st, path = _store()
    try:
        seq = st.remember("model output", origin="model", agent_id="brain-A")
        row = st.cx.execute(
            "SELECT stream, origin, agent_id FROM bitemporal_log WHERE seq=?", (seq,)).fetchone()
        assert row["stream"] == "memory"
        assert row["origin"] == "model", "model provenance must be recorded at write"
        assert row["agent_id"] == "brain-A"
    finally:
        st.close(); _cleanup(path)

def test_backend_degrades_to_flat_and_stub_embedder():
    st, path = _store()
    try:

        assert st.vec_backend == "flat", f"expected flat fallback, got {st.vec_backend!r}"

        names = {r[0] for r in st.cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "memory_vec_flat" in names, names

        assert getattr(st.embedder, "is_stub", False) is True
        assert isinstance(st.embedder, StubEmbedder)

        v = st.embedder.embed("some tokens here")
        assert len(v) == st.dim
        norm = sum(x * x for x in v) ** 0.5
        assert abs(norm - 1.0) < 1e-6, f"stub embedding not unit-normalized: {norm}"
    finally:
        st.close(); _cleanup(path)

def test_observed_as_of_and_append_only():
    st, path = _store()
    try:
        s1 = st.remember("early fact about the ledger", origin="human", observed_time=1000.0)
        st.remember("later fact about the ledger", origin="human", observed_time=2000.0)

        seen = st.recall("ledger fact", k=5, observed_as_of=1500.0)
        seqs = {h["seq"] for h in seen}
        assert s1 in seqs, "early row must be visible as-of 1500"
        assert all(h["observed_time"] <= 1500.0 for h in seen), "as-of must bound observed_time"

        for verb in ("UPDATE bitemporal_log SET origin='human' WHERE seq=?",
                     "DELETE FROM bitemporal_log WHERE seq=?"):
            try:
                st.cx.execute(verb, (s1,))
                raise AssertionError(f"append-only breached: {verb!r} did not raise")
            except sqlite3.Error:
                pass
    finally:
        st.close(); _cleanup(path)

_TESTS = [
    test_roundtrip_recall_nearest,
    test_anticollapse_excludes_model_by_default,
    test_anticollapse_filter_present_in_sql,
    test_remember_without_origin_is_typeerror,
    test_remember_bad_origin_is_valueerror,
    test_model_origin_flagged_in_row,
    test_backend_degrades_to_flat_and_stub_embedder,
    test_observed_as_of_and_append_only,
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
