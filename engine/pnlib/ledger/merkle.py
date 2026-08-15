
from __future__ import annotations

import hashlib
from typing import List, Optional

_LEAF_PREFIX = b"\x00"
_NODE_PREFIX = b"\x01"

EMPTY_ROOT = hashlib.sha256(b"").digest()

def hash_leaf(entry: bytes) -> bytes:

    return hashlib.sha256(_LEAF_PREFIX + entry).digest()

def hash_children(left: bytes, right: bytes) -> bytes:

    return hashlib.sha256(_NODE_PREFIX + left + right).digest()

def _largest_power_of_two_less_than(n: int) -> int:

    if n < 2:
        raise ValueError("split point undefined for n < 2")
    k = 1
    while (k << 1) < n:
        k <<= 1
    return k

def merkle_tree_hash(leaves: List[bytes]) -> bytes:

    n = len(leaves)
    if n == 0:
        return EMPTY_ROOT
    if n == 1:
        return leaves[0]
    k = _largest_power_of_two_less_than(n)
    return hash_children(merkle_tree_hash(leaves[:k]), merkle_tree_hash(leaves[k:]))

def inclusion_proof(index: int, leaves: List[bytes]) -> List[bytes]:

    n = len(leaves)
    if not (0 <= index < n):
        raise ValueError(f"index {index} out of range for tree size {n}")
    if n == 1:
        return []
    k = _largest_power_of_two_less_than(n)
    if index < k:
        return inclusion_proof(index, leaves[:k]) + [merkle_tree_hash(leaves[k:])]
    return inclusion_proof(index - k, leaves[k:]) + [merkle_tree_hash(leaves[:k])]

def consistency_proof(first: int, leaves: List[bytes]) -> List[bytes]:

    n = len(leaves)
    if not (0 < first <= n):
        raise ValueError(f"first {first} out of range for tree size {n}")
    if first == n:
        return []
    return _sub_consistency(first, leaves, True)

def _sub_consistency(m: int, leaves: List[bytes], b: bool) -> List[bytes]:
    n = len(leaves)
    if m == n:

        return [] if b else [merkle_tree_hash(leaves)]
    k = _largest_power_of_two_less_than(n)
    if m <= k:
        return _sub_consistency(m, leaves[:k], b) + [merkle_tree_hash(leaves[k:])]
    return _sub_consistency(m - k, leaves[k:], False) + [merkle_tree_hash(leaves[:k])]

def verify_inclusion(leaf_index: int, tree_size: int, leaf_hash: bytes,
                     proof: List[bytes], root: bytes) -> bool:

    if leaf_index < 0 or tree_size <= 0 or leaf_index >= tree_size:
        return False
    fn, sn = leaf_index, tree_size - 1
    r = leaf_hash
    for p in proof:
        if sn == 0:
            return False
        if (fn & 1) or (fn == sn):
            r = hash_children(p, r)
            if not (fn & 1):
                while not (fn & 1) and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            r = hash_children(r, p)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == root

def verify_consistency(first: int, second: int, proof: List[bytes],
                       first_root: bytes, second_root: bytes) -> bool:

    if first < 0 or second < 0 or first > second:
        return False
    if first == 0:

        return len(proof) == 0
    if first == second:

        return len(proof) == 0 and first_root == second_root

    path = list(proof)

    if (first & (first - 1)) == 0:
        path = [first_root] + path

    fn, sn = first - 1, second - 1
    while fn & 1:
        fn >>= 1
        sn >>= 1

    if not path:
        return False
    fr = sr = path[0]
    for c in path[1:]:
        if sn == 0:
            return False
        if (fn & 1) or (fn == sn):
            fr = hash_children(c, fr)
            sr = hash_children(c, sr)
            if not (fn & 1):
                while not (fn & 1) and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            sr = hash_children(sr, c)
        fn >>= 1
        sn >>= 1

    return fr == first_root and sr == second_root and sn == 0

class MerkleTree:

    def __init__(self) -> None:
        self._leaves: List[bytes] = []
        self._fringe: List[tuple] = []

    @classmethod
    def from_leaves(cls, leaves: List[bytes]) -> "MerkleTree":
        t = cls()
        for lh in leaves:
            t.append(lh)
        return t

    def append(self, leaf_hash: bytes) -> int:

        idx = len(self._leaves)
        self._leaves.append(leaf_hash)
        carry, level = leaf_hash, 0
        while self._fringe and self._fringe[-1][0] == level:
            left = self._fringe.pop()[1]
            carry = hash_children(left, carry)
            level += 1
        self._fringe.append((level, carry))
        return idx

    @property
    def size(self) -> int:
        return len(self._leaves)

    def leaf(self, index: int) -> bytes:
        return self._leaves[index]

    def root(self) -> bytes:

        if not self._fringe:
            return EMPTY_ROOT
        it = reversed(self._fringe)
        acc = next(it)[1]
        for _, h in it:
            acc = hash_children(h, acc)
        return acc

    def root_at(self, size: int) -> bytes:

        if not (0 <= size <= len(self._leaves)):
            raise ValueError(f"size {size} out of range 0..{len(self._leaves)}")
        return merkle_tree_hash(self._leaves[:size])

    def inclusion_proof(self, index: int, size: Optional[int] = None) -> List[bytes]:
        if size is None:
            size = len(self._leaves)
        return inclusion_proof(index, self._leaves[:size])

    def consistency_proof(self, first: int, second: Optional[int] = None) -> List[bytes]:
        if second is None:
            second = len(self._leaves)
        return consistency_proof(first, self._leaves[:second])
