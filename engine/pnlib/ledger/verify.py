
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from pnlib.ledger import merkle, checkpoint

@dataclass
class SelfCheckReport:
    ok: bool = True
    size: int = 0
    root_hex: str = ""
    leaves_checked: int = 0
    consistency_pairs_checked: int = 0
    sths_checked: int = 0
    errors: List[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def summary(self) -> str:
        head = "OK" if self.ok else "FAILED"
        return (f"ledger self-check {head}: size={self.size} root={self.root_hex[:16]}... "
                f"leaves={self.leaves_checked} consistency_pairs={self.consistency_pairs_checked} "
                f"sths={self.sths_checked} errors={len(self.errors)}")

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "size": self.size, "root": self.root_hex,
            "leaves_checked": self.leaves_checked,
            "consistency_pairs_checked": self.consistency_pairs_checked,
            "sths_checked": self.sths_checked, "errors": list(self.errors),
        }

def verify_inclusion_all(store, size: Optional[int] = None,
                         indices: Optional[Sequence[int]] = None) -> tuple:

    n = store.size if size is None else int(size)
    if n < 0 or n > store.size:
        return False, 0, None
    root = store.root_at(n)
    idxs = range(n) if indices is None else [i for i in indices if 0 <= i < n]
    checked = 0
    for i in idxs:
        proof = store.inclusion_proof(i, n)
        if not merkle.verify_inclusion(i, n, store.leaf_hash(i), proof, root):
            return False, checked, i
        checked += 1
    return True, checked, None

def verify_consistency_chain(store, sizes: Sequence[int]) -> tuple:

    clean = sorted({s for s in sizes if 0 < s <= store.size})
    pairs = 0
    for a, b in zip(clean, clean[1:]):
        ra, rb = store.root_at(a), store.root_at(b)
        proof = store.consistency_proof(a, b)
        if not merkle.verify_consistency(a, b, proof, ra, rb):
            return False, pairs, (a, b)
        pairs += 1
    return True, pairs, None

def verify_against_sth(store, sth: "checkpoint.STH", pubkey: bytes) -> tuple:

    if not checkpoint.verify_sth(pubkey, sth):
        return False, "STH signature/log_id does not verify under the pinned pubkey"
    if sth.tree_size > store.size:
        return False, f"STH tree_size {sth.tree_size} exceeds current store size {store.size}"
    got = store.root_at(sth.tree_size)
    if got != sth.root_hash:
        return False, (f"root MISMATCH at size {sth.tree_size}: store={got.hex()[:16]}... "
                       f"pinned={sth.root_hash_hex[:16]}... (historical tamper?)")
    return True, ""

def self_check(store, *, pinned_sths: Optional[Sequence] = None, pubkey: Optional[bytes] = None,
               consistency_sizes: Optional[Sequence[int]] = None,
               sample_indices: Optional[Sequence[int]] = None) -> SelfCheckReport:

    rep = SelfCheckReport(size=store.size, root_hex=store.root().hex())

    ok, checked, bad = verify_inclusion_all(store, indices=sample_indices)
    rep.leaves_checked = checked
    if not ok:
        rep.fail(f"inclusion proof failed for leaf index {bad}")

    if consistency_sizes is None:

        n = store.size
        cand = {1, n}
        s = 1
        while s < n:
            cand.add(s)
            s <<= 1
        cand.add(max(1, n // 2))
        consistency_sizes = sorted(x for x in cand if 0 < x <= n)
    ok, pairs, badpair = verify_consistency_chain(store, consistency_sizes)
    rep.consistency_pairs_checked = pairs
    if not ok:
        rep.fail(f"consistency proof failed for size pair {badpair}")

    if pinned_sths:
        if pubkey is None:
            rep.fail("pinned STHs supplied but no pubkey to verify them against")
        else:
            for sth in pinned_sths:
                ok, reason = verify_against_sth(store, sth, pubkey)
                rep.sths_checked += 1
                if not ok:
                    rep.fail(f"STH(size={getattr(sth, 'tree_size', '?')}): {reason}")

    return rep
