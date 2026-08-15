#!/usr/bin/env python3

import json
import os
import subprocess
import sqlite3
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pnlib.origin import Origin, OriginKind, origin as make_origin
from pnlib.ledger import merkle, checkpoint, verify as ledger_verify
from pnlib.ledger.store import LedgerStore
from pnlib.ledger.append import LedgerWriter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PN_LEDGER = os.path.join(ROOT, "tools", "pn-ledger")

def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="ledger_append_test_")
    os.close(fd)
    os.unlink(path)
    return path

def _cleanup(path):
    for p in (path, path + "-wal", path + "-shm", path + ".spool",
              path + ".spool.cursor", path + ".spool.cursor.tmp"):
        try:
            os.unlink(p)
        except OSError:
            pass

def _last_ledger_payload(store):
    row = store.cx.execute(
        "SELECT payload_json FROM bitemporal_log WHERE stream='ledger' ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    return json.loads(row["payload_json"])

class _FaultyStore:

    def __init__(self, real):
        self._real = real
        self.fail = False

    def append(self, *a, **k):
        if self.fail:
            raise RuntimeError("injected ledger write failure")
        return self._real.append(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)

class _GatedStore:

    def __init__(self, real):
        self._real = real
        self._gate = threading.Event()

    def release(self):
        self._gate.set()

    def append(self, *a, **k):
        self._gate.wait(timeout=10.0)
        return self._real.append(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)

def _agent(component="pnd", agent_id="brain-1", actor="alice"):
    return Origin.agent(component, agent_id=agent_id, actor=actor)

def test_origin_required_raises():
    path = _tmp_db()
    try:
        store = LedgerStore(path)
        w = LedgerWriter(store, path + ".spool", background=False)
        try:

            try:
                w.record({"event": "x"})
                assert False, "record() without origin must raise"
            except (TypeError, ValueError):
                pass

            try:
                w.record({"event": "x"}, origin=None)
                assert False, "record(origin=None) must raise"
            except (TypeError, ValueError):
                pass

            try:
                w.record({"event": "x"}, origin="agent")
                assert False, "record(origin=<str>) must raise"
            except TypeError:
                pass

            assert w.pending() == 0 and store.size == 0
        finally:
            w.close()
            store.close()
    finally:
        _cleanup(path)

def test_origin_closed_world_validation():

    try:
        Origin("wizard", "pnd")
        assert False, "bad origin kind must raise"
    except ValueError:
        pass

    for bad in ("", "   "):
        try:
            Origin("agent", bad)
            assert False, "empty component must raise"
        except ValueError:
            pass

    assert [k.value for k in OriginKind] == ["human", "agent", "model"]
    o = make_origin("model", "pn-llmd", agent_id="brain-2")
    assert o.kind is OriginKind.MODEL and o.component == "pn-llmd"

    assert Origin.from_dict(o.to_dict()) == o

def test_write_failure_spools_then_flushes():
    path = _tmp_db()
    try:
        real = LedgerStore(path)
        faulty = _FaultyStore(real)
        w = LedgerWriter(faulty, path + ".spool", background=False)
        try:
            faulty.fail = True

            rcpt = w.record({"event": "job_done", "n": 0}, origin=_agent())
            assert rcpt.durable is True, "entry must be durably spooled before record() returns"

            assert w.pending() == 1
            assert real.size == 0, "the ledger write failed, so nothing was applied yet"

            assert os.path.exists(path + ".spool")

            faulty.fail = False
            applied = w.flush()
            assert applied == 1 and w.pending() == 0
            assert real.size == 1, "the entry must be applied after the fault clears"
            payload = _last_ledger_payload(real)
            assert payload["event"] == {"event": "job_done", "n": 0}
            assert payload["origin"]["component"] == "pnd"

            root = real.root()
            assert merkle.verify_inclusion(0, real.size, real.leaf_hash(0),
                                           real.inclusion_proof(0), root)
        finally:
            w.close()
            real.close()
    finally:
        _cleanup(path)

def test_no_loss_across_writer_restart():
    path = _tmp_db()
    try:
        real = LedgerStore(path)
        faulty = _FaultyStore(real)
        faulty.fail = True
        w1 = LedgerWriter(faulty, path + ".spool", background=False)
        for i in range(3):
            w1.record({"event": "e", "n": i}, origin=_agent())
        assert w1.pending() == 3 and real.size == 0
        w1.close()

        w2 = LedgerWriter(real, path + ".spool", background=False)
        try:
            assert real.size == 3, "restart must recover every spooled entry (no loss)"
            assert w2.pending() == 0

            rows = real.cx.execute(
                "SELECT payload_json FROM bitemporal_log WHERE stream='ledger' ORDER BY seq ASC"
            ).fetchall()
            ns = [json.loads(r["payload_json"])["event"]["n"] for r in rows]
            assert ns == [0, 1, 2]
        finally:
            w2.close()
            real.close()
    finally:
        _cleanup(path)

def test_record_is_non_blocking_async():
    path = _tmp_db()
    try:
        real = LedgerStore(path)
        gated = _GatedStore(real)
        w = LedgerWriter(gated, path + ".spool", background=True, flush_interval=0.01)
        try:

            t0 = time.time()
            rcpt = w.record({"event": "async", "n": 0}, origin=_agent())
            dt = time.time() - t0
            assert rcpt.durable and dt < 1.0, f"record() must not block on the apply (took {dt:.3f}s)"

            assert real.size == 0, "apply must not have run yet while the gate is closed"

            gated.release()
            deadline = time.time() + 5.0
            while real.size < 1 and time.time() < deadline:
                time.sleep(0.01)
            assert real.size == 1, "the async worker must apply the entry once unblocked"
        finally:
            w.close()
            real.close()
    finally:
        _cleanup(path)

def test_n_appends_sth_and_selfcheck():
    path = _tmp_db()
    N = 25
    try:
        store = LedgerStore(path)
        w = LedgerWriter(store, path + ".spool", background=False)
        signer = checkpoint.LedgerSigner.generate()
        try:
            for i in range(N):
                w.record({"event": "tick", "n": i},
                         origin=_agent(agent_id=f"brain-{i % 3}"),
                         valid_time=1000.0 + i, observed_time=2000.0 + i)
            assert w.pending() == 0 and store.size == N

            root = store.root()
            sth = signer.sign_sth(store.size, root)
            assert checkpoint.verify_sth(signer.public_key, sth)

            for i in range(store.size):
                proof = store.inclusion_proof(i)
                assert merkle.verify_inclusion(i, store.size, store.leaf_hash(i), proof, root), \
                    f"inclusion proof failed for leaf {i}"

            rep = ledger_verify.self_check(store, pinned_sths=[sth], pubkey=signer.public_key)
            assert rep.ok, f"self-check must pass: {rep.summary()} errors={rep.errors}"
            assert rep.leaves_checked == N and rep.sths_checked == 1
            assert rep.consistency_pairs_checked >= 1
        finally:
            w.close()
            store.close()
    finally:
        _cleanup(path)

def test_selfcheck_detects_tamper():
    path = _tmp_db()
    try:
        store = LedgerStore(path)
        w = LedgerWriter(store, path + ".spool", background=False)
        signer = checkpoint.LedgerSigner.generate()
        for i in range(6):
            w.record({"event": "e", "n": i}, origin=_agent(),
                     valid_time=float(i), observed_time=float(i))
        w.flush()
        w.close()
        sth = signer.sign_sth(store.size, store.root())

        ok, reason = ledger_verify.verify_against_sth(store, sth, signer.public_key)
        assert ok, reason
        store.close()

        raw = sqlite3.connect(path)
        raw.execute("DROP TRIGGER IF EXISTS trg_bitemporal_no_update")
        tseq = raw.execute("SELECT seq FROM bitemporal_log WHERE stream='ledger' "
                           "ORDER BY seq ASC LIMIT 1 OFFSET 2").fetchone()[0]
        raw.execute("UPDATE bitemporal_log SET payload_json=? WHERE seq=?",
                    ('{"HACKED":true}', tseq))
        raw.commit()
        raw.close()

        store2 = LedgerStore(path)
        try:

            ok, reason = ledger_verify.verify_against_sth(store2, sth, signer.public_key)
            assert not ok and "MISMATCH" in reason, f"tamper must be detected: {reason}"
            rep = ledger_verify.self_check(store2, pinned_sths=[sth], pubkey=signer.public_key)
            assert not rep.ok, "self_check must FAIL against the pinned STH after tampering"
        finally:
            store2.close()
    finally:
        _cleanup(path)

def _cli(*args):
    return subprocess.run([sys.executable, PN_LEDGER, *args],
                          capture_output=True, text=True, timeout=60)

def test_cli_end_to_end():
    path = _tmp_db()
    try:

        r = _cli("append", "--db", path, "--origin-kind", "agent", "--component", "pnd",
                 "--agent-id", "brain-1", "--event", '{"event":"a","n":0}', "--json")
        assert r.returncode == 0, f"append failed: {r.stderr}"
        out0 = json.loads(r.stdout.strip())
        assert out0["ok"] and out0["merkle_index"] == 0 and out0["size"] == 1

        r = _cli("append", "--db", path, "--origin-kind", "human", "--component", "portal",
                 "--actor", "bob", "--event-text", "hello", "--json")
        assert r.returncode == 0, f"append 2 failed: {r.stderr}"
        out1 = json.loads(r.stdout.strip())
        assert out1["size"] == 2 and out1["merkle_index"] == 1

        r = _cli("head", "--db", path, "--json")
        assert r.returncode == 0
        head = json.loads(r.stdout.strip())
        assert head["size"] == 2 and not head["empty"]

        r = _cli("prove-inclusion", "--db", path, "--index", "0", "--json")
        assert r.returncode == 0, f"prove-inclusion failed: {r.stderr}"
        pi = json.loads(r.stdout.strip())
        assert pi["ok"] and pi["index"] == 0 and pi["size"] == 2
        assert merkle.verify_inclusion(0, 2, bytes.fromhex(pi["leaf_hash"]),
                                       [bytes.fromhex(h) for h in pi["proof"]],
                                       bytes.fromhex(pi["root"]))

        r = _cli("prove-consistency", "--db", path, "--first", "1", "--second", "2", "--json")
        assert r.returncode == 0, f"prove-consistency failed: {r.stderr}"
        pc = json.loads(r.stdout.strip())
        assert pc["ok"]

        r = _cli("verify", "--db", path, "--json")
        assert r.returncode == 0, f"verify failed: {r.stderr}"
        vr = json.loads(r.stdout.strip())
        assert vr["ok"] and vr["size"] == 2 and vr["leaves_checked"] == 2

        keypath = path + ".key"
        try:
            r = _cli("keygen", "--key", keypath, "--json")
            assert r.returncode == 0, f"keygen failed: {r.stderr}"
            r = _cli("head", "--db", path, "--sign-key", keypath)
            assert r.returncode == 0, f"signed head failed: {r.stderr}"
            sth = checkpoint.STH.from_json(r.stdout.strip())
            with open(keypath + ".pub") as f:
                pub = bytes.fromhex(f.read().strip())
            assert checkpoint.verify_sth(pub, sth) and sth.tree_size == 2

            sthfile = path + ".sth.json"
            with open(sthfile, "w") as f:
                f.write(sth.to_json())
            r = _cli("verify", "--db", path, "--pubkey", keypath + ".pub", "--sth", sthfile, "--json")
            assert r.returncode == 0, f"verify w/ STH failed: {r.stderr}"
            vr = json.loads(r.stdout.strip())
            assert vr["ok"] and vr["sths_checked"] == 1
        finally:
            for p in (keypath, keypath + ".pub", path + ".sth.json"):
                try:
                    os.unlink(p)
                except OSError:
                    pass
    finally:
        _cleanup(path)

_TESTS = [
    test_origin_required_raises,
    test_origin_closed_world_validation,
    test_write_failure_spools_then_flushes,
    test_no_loss_across_writer_restart,
    test_record_is_non_blocking_async,
    test_n_appends_sth_and_selfcheck,
    test_selfcheck_detects_tamper,
    test_cli_end_to_end,
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
