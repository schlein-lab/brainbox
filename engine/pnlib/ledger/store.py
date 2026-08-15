
from __future__ import annotations

import json
import struct
import time
from typing import List, Optional, Tuple

from pnlib import schema_bitemporal as bt
from pnlib.ledger import merkle

_ENTRY_DOMAIN = b"brainarbeit/ledger/entry/1"

_LEDGER_STREAM = "ledger"

def _enc_field(b: bytes) -> bytes:

    return struct.pack(">I", len(b)) + b

def canonical_entry(*, seq: int, stream: str, valid_time: float, observed_time: float,
                    origin: str, actor: Optional[str], agent_id: Optional[str],
                    ledger_seq: Optional[int], payload_json: Optional[str]) -> bytes:

    parts = [
        _ENTRY_DOMAIN,
        struct.pack(">q", int(seq)),
        stream.encode("utf-8"),
        struct.pack(">d", float(valid_time)),
        struct.pack(">d", float(observed_time)),
        origin.encode("utf-8"),
        (actor or "").encode("utf-8"),
        (agent_id or "").encode("utf-8"),
        struct.pack(">q", -1 if ledger_seq is None else int(ledger_seq)),
        (payload_json or "").encode("utf-8"),
    ]
    return b"".join(_enc_field(p) for p in parts)

def leaf_hash_for_row(row) -> bytes:

    return merkle.hash_leaf(canonical_entry(
        seq=row["seq"], stream=row["stream"], valid_time=row["valid_time"],
        observed_time=row["observed_time"], origin=row["origin"], actor=row["actor"],
        agent_id=row["agent_id"], ledger_seq=row["ledger_seq"], payload_json=row["payload_json"]))

class LedgerStore:

    def __init__(self, path: str, durability: str = "full") -> None:

        self.cx = bt.connect(path, durability=durability)
        self._tree = merkle.MerkleTree()
        self._seqs: List[int] = []
        self._rebuild()

    def _rebuild(self) -> None:

        self._tree = merkle.MerkleTree()
        self._seqs = []
        for row in self.cx.execute(
                "SELECT * FROM bitemporal_log WHERE stream=? ORDER BY seq ASC",
                (_LEDGER_STREAM,)):
            self._tree.append(leaf_hash_for_row(row))
            self._seqs.append(int(row["seq"]))

    def append(self, payload, *, origin: str, actor: Optional[str] = None,
               agent_id: Optional[str] = None, valid_time: Optional[float] = None,
               observed_time: Optional[float] = None,
               ledger_seq: Optional[int] = None) -> Tuple[int, int, bytes]:

        now = time.time()
        vt = now if valid_time is None else float(valid_time)
        ot = now if observed_time is None else float(observed_time)
        if isinstance(payload, (bytes, bytearray)):
            payload_json = bytes(payload).decode("utf-8")
        elif isinstance(payload, str):
            payload_json = payload
        else:
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False)

        seq = bt.append(self.cx, stream=_LEDGER_STREAM, valid_time=vt, observed_time=ot,
                        origin=origin, payload_json=payload_json, actor=actor,
                        agent_id=agent_id, ledger_seq=ledger_seq)

        row = self.cx.execute("SELECT * FROM bitemporal_log WHERE seq=?", (seq,)).fetchone()
        lh = leaf_hash_for_row(row)
        index = self._tree.append(lh)
        self._seqs.append(seq)
        return seq, index, lh

    @property
    def size(self) -> int:
        return self._tree.size

    def root(self) -> bytes:
        return self._tree.root()

    def root_at(self, size: int) -> bytes:
        return self._tree.root_at(size)

    def leaf_hash(self, index: int) -> bytes:
        return self._tree.leaf(index)

    def index_of_seq(self, seq: int) -> int:

        return self._seqs.index(int(seq))

    def leaf_hashes(self, up_to_size: Optional[int] = None) -> List[bytes]:
        n = self._tree.size if up_to_size is None else up_to_size
        return [self._tree.leaf(i) for i in range(n)]

    def inclusion_proof(self, index: int, size: Optional[int] = None) -> List[bytes]:
        return self._tree.inclusion_proof(index, size)

    def consistency_proof(self, first: int, second: Optional[int] = None) -> List[bytes]:
        return self._tree.consistency_proof(first, second)

    def recompute_tree_from_store(self) -> merkle.MerkleTree:

        t = merkle.MerkleTree()
        for row in self.cx.execute(
                "SELECT * FROM bitemporal_log WHERE stream=? ORDER BY seq ASC",
                (_LEDGER_STREAM,)):
            t.append(leaf_hash_for_row(row))
        return t

    def close(self) -> None:
        try:
            self.cx.close()
        except Exception:
            pass
