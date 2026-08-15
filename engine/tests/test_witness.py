#!/usr/bin/env python3

import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SDK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "clients", "sdk-python")
sys.path.insert(0, _SDK)

from pnlib.ledger import checkpoint
from pnlib.ledger.store import LedgerStore

import witness as W

def _tmp(suffix=".db"):
    fd, path = tempfile.mkstemp(prefix="witness_test_", suffix=suffix)
    os.close(fd)
    os.unlink(path)
    return path

def _cleanup(*paths):
    for p in paths:
        for ext in ("", "-wal", "-shm", ".tmp"):
            try:
                os.unlink(p + ext)
            except OSError:
                pass

class _StoreSource:

    def __init__(self, store, signer):
        self.store = store
        self.signer = signer

    def get_sth(self):
        return self.signer.sign_sth(self.store.size, self.store.root())

    def get_consistency_proof(self, first_size, second_size):
        return self.store.consistency_proof(first_size, second_size)

def _append(store, n, start=0):
    for i in range(start, start + n):
        store.append({"event": "e", "n": i}, origin="agent", actor="tester", agent_id="a1",
                     valid_time=float(i), observed_time=float(i))

def _snapshot(store, store_path, dst):

    store.cx.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    store.cx.commit()
    shutil.copy(store_path, dst)

def _pinned_witness(pubkey_hex, clean_source):

    w = W.Witness.enroll(pubkey_hex)
    w.poll(clean_source)
    return w

def _tamper_raw(path, fn):

    raw = sqlite3.connect(path)
    raw.execute("DROP TRIGGER IF EXISTS trg_bitemporal_no_update")
    raw.execute("DROP TRIGGER IF EXISTS trg_bitemporal_no_delete")
    fn(raw)
    raw.commit()
    raw.close()

def _ledger_seq_at(path, position):

    raw = sqlite3.connect(path)
    row = raw.execute("SELECT seq FROM bitemporal_log WHERE stream='ledger' ORDER BY seq ASC "
                      "LIMIT 1 OFFSET ?", (position,)).fetchone()
    raw.close()
    return row[0]

def test_no_false_alarm_over_1000_appends():
    path = _tmp()
    statep = _tmp(".state.json")
    try:
        store = LedgerStore(path)
        signer = checkpoint.LedgerSigner.generate()
        source = _StoreSource(store, signer)
        w = W.Witness.enroll(signer.public_key_hex, state_path=statep)

        total = 0
        batches = [1, 1, 2, 3, 5, 8, 13, 1, 21, 34, 55, 89, 144, 1, 233, 377, 1, 4, 7]

        while sum(batches) <= 1000:
            batches.append(50)
        for b in batches:
            _append(store, b, start=total)
            total += b
            sth = w.poll(source)
            assert sth.tree_size == store.size == total, (sth.tree_size, store.size, total)

        assert total > 1000, f"must exercise 1000+ appends, got {total}"
        assert w.alarms == [], f"clean head must raise ZERO alarms, got {w.alarms}"
        assert w.size == total

        chain_len = len(w.sths)
        w.poll(source)
        assert len(w.sths) == chain_len and w.alarms == []

        w2 = W.Witness(signer.public_key_hex, state_path=statep)
        assert w2.size == total and w2.latest.root_hash == w.latest.root_hash

        _append(store, 10, start=total)
        total += 10
        w2.poll(source)
        assert w2.size == total and w2.alarms == []

        store.close()
    finally:
        _cleanup(path, statep)

def _build_clean(path, n=12):
    store = LedgerStore(path)
    signer = checkpoint.LedgerSigner.generate()
    _append(store, n)
    return store, signer

def test_tamper_1_mutate_historical_row():

    path = _tmp(); copy = _tmp()
    store = None
    try:
        store, signer = _build_clean(path, 12)
        clean_src = _StoreSource(store, signer)
        w = _pinned_witness(signer.public_key_hex, clean_src)
        assert w.size == 12 and w.alarms == []

        _snapshot(store, path, copy)
        tseq = _ledger_seq_at(copy, 3)
        _tamper_raw(copy, lambda cx: cx.execute(
            "UPDATE bitemporal_log SET payload_json=? WHERE seq=?", ('{"HACKED":true}', tseq)))

        store2 = LedgerStore(copy)
        try:
            assert store2.size == 12, "mutation must not change row count"
            src2 = _StoreSource(store2, signer)
            raised = None
            try:
                w.poll(src2)
            except W.WitnessAlarm as a:
                raised = a
            assert raised is not None, "MUTATE must raise a WitnessAlarm"
            assert raised.kind == W.KIND_INCONSISTENT, raised.kind
            assert w.alarms and w.alarms[-1]["kind"] == W.KIND_INCONSISTENT
        finally:
            store2.close()
    finally:
        if store:
            store.close()
        _cleanup(path, copy)

def test_tamper_2_delete_row():

    path = _tmp(); copy = _tmp()
    store = None
    try:
        store, signer = _build_clean(path, 12)
        w = _pinned_witness(signer.public_key_hex, _StoreSource(store, signer))
        assert w.size == 12

        _snapshot(store, path, copy)
        tseq = _ledger_seq_at(copy, 3)
        _tamper_raw(copy, lambda cx: cx.execute(
            "DELETE FROM bitemporal_log WHERE seq=?", (tseq,)))

        store2 = LedgerStore(copy)
        try:
            assert store2.size == 11, "deletion must drop one row"
            _append(store2, 1, start=999)
            assert store2.size == 12
            src2 = _StoreSource(store2, signer)
            raised = None
            try:
                w.poll(src2)
            except W.WitnessAlarm as a:
                raised = a
            assert raised is not None, "DELETE must raise a WitnessAlarm"
            assert raised.kind == W.KIND_INCONSISTENT, raised.kind
        finally:
            store2.close()
    finally:
        if store:
            store.close()
        _cleanup(path, copy)

def test_tamper_3_fork_split_view():

    path = _tmp(); copy = _tmp()
    store = None
    try:
        store, signer = _build_clean(path, 12)
        w = _pinned_witness(signer.public_key_hex, _StoreSource(store, signer))
        assert w.size == 12

        _snapshot(store, path, copy)

        tseq = _ledger_seq_at(copy, 6)
        _tamper_raw(copy, lambda cx: cx.execute(
            "UPDATE bitemporal_log SET payload_json=? WHERE seq=?", ('{"FORK":true}', tseq)))

        store2 = LedgerStore(copy)
        try:
            _append(store2, 1, start=500)
            assert store2.size == 13
            src2 = _StoreSource(store2, signer)
            proof = src2.get_consistency_proof(12, 13)
            assert len(proof) > 0, "expected a non-trivial consistency proof for 12->13"
            raised = None
            try:
                w.poll(src2)
            except W.WitnessAlarm as a:
                raised = a
            assert raised is not None, "FORK must raise a WitnessAlarm"
            assert raised.kind == W.KIND_INCONSISTENT, raised.kind
        finally:
            store2.close()
    finally:
        if store:
            store.close()
        _cleanup(path, copy)

def test_tamper_4_truncate_tail():

    path = _tmp(); copy = _tmp()
    store = None
    try:
        store, signer = _build_clean(path, 12)
        w = _pinned_witness(signer.public_key_hex, _StoreSource(store, signer))
        assert w.size == 12

        _snapshot(store, path, copy)

        seqs = [_ledger_seq_at(copy, p) for p in (8, 9, 10, 11)]
        _tamper_raw(copy, lambda cx: [cx.execute(
            "DELETE FROM bitemporal_log WHERE seq=?", (s,)) for s in seqs])

        store2 = LedgerStore(copy)
        try:
            assert store2.size == 8, "tail truncation must shrink the head"
            src2 = _StoreSource(store2, signer)
            raised = None
            try:
                w.poll(src2)
            except W.WitnessAlarm as a:
                raised = a
            assert raised is not None, "TRUNCATE must raise a WitnessAlarm"
            assert raised.kind == W.KIND_TRUNCATION, raised.kind
        finally:
            store2.close()
    finally:
        if store:
            store.close()
        _cleanup(path, copy)

def test_bad_signature_and_wrong_log_rejected():

    path = _tmp()
    store = None
    try:
        store, signer = _build_clean(path, 5)
        good = _StoreSource(store, signer)
        w = _pinned_witness(signer.public_key_hex, good)

        imposter = checkpoint.LedgerSigner.generate()
        bad = _StoreSource(store, imposter)
        raised = None
        try:
            w.poll(bad)
        except W.WitnessAlarm as a:
            raised = a
        assert raised is not None and raised.kind == W.KIND_LOG_ID_MISMATCH, raised
    finally:
        if store:
            store.close()
        _cleanup(path)

_TESTS = [
    test_no_false_alarm_over_1000_appends,
    test_tamper_1_mutate_historical_row,
    test_tamper_2_delete_row,
    test_tamper_3_fork_split_view,
    test_tamper_4_truncate_tail,
    test_bad_signature_and_wrong_log_rejected,
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
            import traceback
            print(f"  FAIL  {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n=== {p} passed, {f} failed ===")
    sys.exit(1 if f else 0)

if __name__ == "__main__":
    main()
