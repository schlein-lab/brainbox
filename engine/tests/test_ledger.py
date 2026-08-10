#!/usr/bin/env python3

import os
import random
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pnlib.ledger import merkle, checkpoint
from pnlib.ledger.store import LedgerStore, leaf_hash_for_row

def _h(s):
    return bytes.fromhex(s)

LEAF_INPUTS = [
    _h(""),
    _h("00"),
    _h("10"),
    _h("2021"),
    _h("3031"),
    _h("40414243"),
    _h("5051525354555657"),
    _h("606162636465666768696a6b6c6d6e6f"),
]
LEAVES = [merkle.hash_leaf(d) for d in LEAF_INPUTS]

ROOTS = {
    0: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    1: "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d",
    2: "fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125",
    3: "aeb6bcfe274b70a14fb067a5e5578264db0fa9b51af5e0ba159158f329e06e77",
    4: "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7",
    5: "4e3bbb1f7b478dcfe71fb631631519a3bca12c9aefca1612bfce4c13a86264d4",
    6: "76e67dadbcdf1e10e1b74ddc608abd2f98dfb16fbce75277b5232a127f2087ef",
    7: "ddb89be403809e325750d3d263cd78929c2942b7942a34b77e122c9594a74c8c",
    8: "5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328",
}

INCLUSION_VECTORS = [
    (1, 8, ["96a296d224f285c67bee93c30f8a309157f0daa35dc5b87e410b78630a09cfc7",
            "5f083f0a1a33ca076a95279832580db3e0ef4584bdff1f54c8a360f50de3031e",
            "6b47aaf29ee3c2af9af889bc1fb9254dabd31177f16232dd6aab035ca39bf6e4"]),
    (6, 8, ["bc1a0643b12e4d2d7c77918f44e0f4f79a838b6cf9ec5b5c283e1f4d88599e6b",
            "ca854ea128ed050b41b35ffc1b87b8eb2bde461e9e3b5596ece6b9d5975a0ae0",
            "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7"]),
    (3, 3, ["fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125"]),
    (2, 5, ["6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d",
            "5f083f0a1a33ca076a95279832580db3e0ef4584bdff1f54c8a360f50de3031e",
            "bc1a0643b12e4d2d7c77918f44e0f4f79a838b6cf9ec5b5c283e1f4d88599e6b"]),
]

CONSISTENCY_VECTORS = [
    (1, 1, []),
    (1, 8, ["96a296d224f285c67bee93c30f8a309157f0daa35dc5b87e410b78630a09cfc7",
            "5f083f0a1a33ca076a95279832580db3e0ef4584bdff1f54c8a360f50de3031e",
            "6b47aaf29ee3c2af9af889bc1fb9254dabd31177f16232dd6aab035ca39bf6e4"]),
    (6, 8, ["0ebc5d3437fbe2db158b9f126a1d118e308181031d0a949f8dededebc558ef6a",
            "ca854ea128ed050b41b35ffc1b87b8eb2bde461e9e3b5596ece6b9d5975a0ae0",
            "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7"]),
    (2, 5, ["5f083f0a1a33ca076a95279832580db3e0ef4584bdff1f54c8a360f50de3031e",
            "bc1a0643b12e4d2d7c77918f44e0f4f79a838b6cf9ec5b5c283e1f4d88599e6b"]),
]

def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="ledger_test_")
    os.close(fd)
    os.unlink(path)
    return path

def _cleanup(path):
    for p in (path, path + "-wal", path + "-shm"):
        try:
            os.unlink(p)
        except OSError:
            pass

def test_rfc6962_empty_root():
    assert merkle.merkle_tree_hash([]).hex() == ROOTS[0]
    assert merkle.EMPTY_ROOT.hex() == ROOTS[0]

def test_rfc6962_roots_sizes_1_to_8():

    tree = merkle.MerkleTree()
    for n in range(1, 9):
        tree.append(LEAVES[n - 1])
        expect = ROOTS[n]
        assert merkle.merkle_tree_hash(LEAVES[:n]).hex() == expect, f"MTH size {n}"
        assert tree.root().hex() == expect, f"incremental root size {n}"
        assert tree.root_at(n).hex() == expect, f"root_at size {n}"

def test_rfc6962_inclusion_proof_vectors():
    for leaf_1based, size, proof_hex in INCLUSION_VECTORS:
        idx = leaf_1based - 1
        proof = merkle.inclusion_proof(idx, LEAVES[:size])
        assert [p.hex() for p in proof] == proof_hex, \
            f"inclusion proof leaf={leaf_1based} size={size}: {[p.hex() for p in proof]}"

        root = _h(ROOTS[size])
        assert merkle.verify_inclusion(idx, size, LEAVES[idx], proof, root), \
            f"verify_inclusion leaf={leaf_1based} size={size}"

        assert not merkle.verify_inclusion(idx, size, LEAVES[idx], proof, _h(ROOTS[0]))

def test_rfc6962_consistency_proof_vectors():
    for first, second, proof_hex in CONSISTENCY_VECTORS:
        proof = merkle.consistency_proof(first, LEAVES[:second])
        assert [p.hex() for p in proof] == proof_hex, \
            f"consistency proof {first}->{second}: {[p.hex() for p in proof]}"
        assert merkle.verify_consistency(first, second, proof, _h(ROOTS[first]),
                                         _h(ROOTS[second])), f"verify_consistency {first}->{second}"

def test_property_inclusion_all_leaves():
    rng = random.Random(6962)
    for _ in range(40):
        n = rng.randint(1, 64)
        leaves = [merkle.hash_leaf(bytes([rng.randint(0, 255) for _ in range(rng.randint(0, 20))]))
                  for _ in range(n)]
        tree = merkle.MerkleTree.from_leaves(leaves)
        root = tree.root()
        assert root == merkle.merkle_tree_hash(leaves)
        for i in range(n):
            proof = tree.inclusion_proof(i)
            assert merkle.verify_inclusion(i, n, leaves[i], proof, root), f"leaf {i}/{n}"

            bad = bytes([leaves[i][0] ^ 0xFF]) + leaves[i][1:]
            assert not merkle.verify_inclusion(i, n, bad, proof, root)

def test_property_consistency_all_size_pairs():
    rng = random.Random(20250705)
    for _ in range(20):
        n = rng.randint(1, 40)
        leaves = [merkle.hash_leaf(bytes([rng.randint(0, 255)]) * rng.randint(1, 8))
                  for _ in range(n)]
        tree = merkle.MerkleTree.from_leaves(leaves)
        roots = {m: merkle.merkle_tree_hash(leaves[:m]) for m in range(1, n + 1)}
        for m in range(1, n + 1):
            for k in range(m, n + 1):
                proof = tree.consistency_proof(m, k)
                assert merkle.verify_consistency(m, k, proof, roots[m], roots[k]), \
                    f"consistency {m}->{k} (n={n})"

        if n >= 2:
            m, k = 1, n
            proof = tree.consistency_proof(m, k)
            bad_first = bytes([roots[m][0] ^ 0xFF]) + roots[m][1:]
            assert not merkle.verify_consistency(m, k, proof, bad_first, roots[k])

def test_sth_sign_and_verify_pinned():
    signer = checkpoint.LedgerSigner.generate()
    root = merkle.merkle_tree_hash(LEAVES[:5])
    sth = signer.sign_sth(5, root, timestamp=1751700000.0)
    assert sth.tree_size == 5 and sth.root_hash == root

    assert checkpoint.verify_sth(signer.public_key, sth)
    assert checkpoint.verify_sth_pinned(signer.public_key_hex, sth)

    other = checkpoint.LedgerSigner.generate()
    assert not checkpoint.verify_sth(other.public_key, sth)

    spoofed = checkpoint.STH(sth.tree_size, sth.root_hash, sth.timestamp, sth.signature,
                             log_id=other.log_id)
    assert not checkpoint.verify_sth(signer.public_key, spoofed)

def test_sth_tamper_fields_rejected():
    signer = checkpoint.LedgerSigner.generate()
    root = merkle.merkle_tree_hash(LEAVES[:8])
    sth = signer.sign_sth(8, root, timestamp=1751700000.0)

    bad_root = checkpoint.STH(8, bytes([root[0] ^ 0x01]) + root[1:], sth.timestamp,
                              sth.signature, sth.log_id)
    assert not checkpoint.verify_sth(signer.public_key, bad_root)
    bad_size = checkpoint.STH(7, root, sth.timestamp, sth.signature, sth.log_id)
    assert not checkpoint.verify_sth(signer.public_key, bad_size)
    bad_ts = checkpoint.STH(8, root, sth.timestamp + 1.0, sth.signature, sth.log_id)
    assert not checkpoint.verify_sth(signer.public_key, bad_ts)

    assert checkpoint.verify_sth(signer.public_key, checkpoint.STH.from_json(sth.to_json()))

def test_sth_key_save_load_roundtrip():
    path = _tmp_db() + ".key"
    try:
        signer = checkpoint.LedgerSigner.generate()
        signer.save_private(path)
        assert (os.stat(path).st_mode & 0o777) == 0o600, "private key file must be 0600"
        loaded = checkpoint.LedgerSigner.load_private(path)
        assert loaded.public_key == signer.public_key
        root = merkle.merkle_tree_hash(LEAVES[:3])
        sth = loaded.sign_sth(3, root, timestamp=1.0)
        assert checkpoint.verify_sth(signer.public_key, sth)
    finally:
        for p in (path, path + ".pub"):
            try:
                os.unlink(p)
            except OSError:
                pass

def test_ledger_store_append_prove_and_rebuild():
    path = _tmp_db()
    try:
        store = LedgerStore(path)
        assert store.size == 0 and store.root() == merkle.EMPTY_ROOT
        signer = checkpoint.LedgerSigner.generate()
        seqs = []
        for i in range(12):
            seq, idx, lh = store.append(
                {"event": "job_done", "n": i}, origin="agent", actor="alice",
                agent_id="brain-1", valid_time=1000.0 + i, observed_time=2000.0 + i)
            assert idx == i
            seqs.append(seq)

        root = store.root()
        sth = signer.sign_sth(store.size, root)
        assert checkpoint.verify_sth(signer.public_key, sth)
        for i in range(store.size):
            proof = store.inclusion_proof(i)
            assert merkle.verify_inclusion(i, store.size, store.leaf_hash(i), proof, root)

        assert store.index_of_seq(seqs[7]) == 7
        store.close()

        store2 = LedgerStore(path)
        assert store2.size == 12
        assert store2.root() == root, "rebuilt root must match"
        assert checkpoint.verify_sth(signer.public_key, sth)
        store2.close()
    finally:
        _cleanup(path)

def test_ledger_store_ledger_stream_isolated_from_memory():

    path = _tmp_db()
    try:
        store = LedgerStore(path)
        from pnlib import schema_bitemporal as bt

        store.append({"e": 0}, origin="human", actor="alice")
        bt.append(store.cx, stream="memory", valid_time=1.0, observed_time=1.0,
                  origin="model", payload_json='{"fact":"x"}')
        store.append({"e": 1}, origin="human", actor="alice")
        assert store.size == 2, "memory rows must not count as ledger leaves"
        store2 = LedgerStore(path)
        assert store2.size == 2 and store2.root() == store.root()
        store.close()
        store2.close()
    finally:
        _cleanup(path)

def test_tamper_historical_row_breaks_consistency():

    path = _tmp_db()
    try:
        store = LedgerStore(path)
        signer = checkpoint.LedgerSigner.generate()
        for i in range(5):
            store.append({"event": "e", "n": i}, origin="agent", actor="alice", agent_id="b1",
                         valid_time=float(i), observed_time=float(i))
        s1 = store.size
        root_s1 = store.root()
        sth1 = signer.sign_sth(s1, root_s1)
        assert checkpoint.verify_sth(signer.public_key, sth1)

        for i in range(5, 9):
            store.append({"event": "e", "n": i}, origin="agent", actor="alice", agent_id="b1",
                         valid_time=float(i), observed_time=float(i))
        s2 = store.size
        root_s2 = store.root()

        good_proof = store.consistency_proof(s1, s2)
        assert merkle.verify_consistency(s1, s2, good_proof, root_s1, root_s2), \
            "honest consistency proof must verify before tampering"
        store.close()

        raw = sqlite3.connect(path)
        raw.execute("DROP TRIGGER IF EXISTS trg_bitemporal_no_update")
        target_seq = raw.execute(
            "SELECT seq FROM bitemporal_log WHERE stream='ledger' ORDER BY seq ASC "
            "LIMIT 1 OFFSET 2").fetchone()[0]
        raw.execute("UPDATE bitemporal_log SET payload_json=? WHERE seq=?",
                    ('{"event":"e","n":2,"HACKED":true}', target_seq))
        raw.commit()
        raw.close()

        store2 = LedgerStore(path)
        assert store2.size == s2
        root_s1_tampered = store2.root_at(s1)
        root_s2_tampered = store2.root()

        tampered_proof = store2.consistency_proof(s1, s2)
        assert merkle.verify_consistency(s1, s2, tampered_proof,
                                         root_s1_tampered, root_s2_tampered), \
            "tampered tree is internally consistent (expected)"

        assert root_s1_tampered != root_s1, "tampering a historical row MUST move the s1 root"
        assert sth1.root_hash == root_s1

        assert not merkle.verify_consistency(s1, s2, tampered_proof, sth1.root_hash,
                                             root_s2_tampered), \
            "consistency proof MUST FAIL against the pinned pre-tamper root (M5 tamper evidence)"
        assert not merkle.verify_consistency(s1, s2, good_proof, sth1.root_hash,
                                             root_s2_tampered), \
            "the old honest proof also fails to reconcile the pinned root with tampered history"
        store2.close()
    finally:
        _cleanup(path)

_TESTS = [
    test_rfc6962_empty_root,
    test_rfc6962_roots_sizes_1_to_8,
    test_rfc6962_inclusion_proof_vectors,
    test_rfc6962_consistency_proof_vectors,
    test_property_inclusion_all_leaves,
    test_property_consistency_all_size_pairs,
    test_sth_sign_and_verify_pinned,
    test_sth_tamper_fields_rejected,
    test_sth_key_save_load_roundtrip,
    test_ledger_store_append_prove_and_rebuild,
    test_ledger_store_ledger_stream_isolated_from_memory,
    test_tamper_historical_row_breaks_consistency,
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
