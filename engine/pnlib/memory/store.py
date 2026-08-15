
from __future__ import annotations

import hashlib
import json
import math
import struct
import time
from array import array
from typing import Dict, List, Optional, Sequence, Tuple

from pnlib import schema_bitemporal as bt

_MEMORY_STREAM = "memory"
_DEFAULT_DIM = 64

class StubEmbedder:

    is_stub = True
    model_id = "stub/feature-hash/1"

    def __init__(self, dim: int = _DEFAULT_DIM) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = int(dim)

    @staticmethod
    def _tokens(text: str) -> List[str]:
        return [t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if t]

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        for tok in self._tokens(text):
            h = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "big") % self.dim
            sign = 1.0 if (h[4] & 1) else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0.0:
            return vec
        return [x / norm for x in vec]

def _cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 1.0
    return 1.0 - (dot / (na * nb))

def _try_load_sqlite_vec(cx) -> bool:

    try:
        import sqlite_vec
    except Exception:
        return False
    try:
        cx.enable_load_extension(True)
    except Exception:
        return False
    try:
        sqlite_vec.load(cx)
    except Exception:
        return False
    finally:
        try:
            cx.enable_load_extension(False)
        except Exception:
            pass

    try:
        cx.execute("SELECT vec_version()")
        return True
    except Exception:
        return False

class MemoryStore:

    def __init__(self, path: str, *, dim: int = _DEFAULT_DIM,
                 embedder: Optional[StubEmbedder] = None, durability: str = "full") -> None:
        self.cx = bt.connect(path, durability=durability)
        self.embedder = embedder or StubEmbedder(dim=dim)
        self.dim = int(self.embedder.dim)
        self.vec_backend = "sqlite-vec" if _try_load_sqlite_vec(self.cx) else "flat"
        self._init_index()

    def _init_index(self) -> None:
        if self.vec_backend == "sqlite-vec":

            self.cx.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0("
                f"embedding float[{self.dim}])")
        else:

            self.cx.execute(
                "CREATE TABLE IF NOT EXISTS memory_vec_flat("
                "seq INTEGER PRIMARY KEY, dim INTEGER NOT NULL, embedding BLOB NOT NULL)")
        self.cx.commit()

    @staticmethod
    def _pack(vec: Sequence[float]) -> bytes:
        return array("f", vec).tobytes()

    @staticmethod
    def _unpack(blob: bytes) -> List[float]:
        a = array("f")
        a.frombytes(blob)
        return list(a)

    def _index_put(self, seq: int, vec: Sequence[float]) -> None:
        if self.vec_backend == "sqlite-vec":
            import sqlite_vec
            self.cx.execute(
                "INSERT INTO memory_vec(rowid, embedding) VALUES(?, ?)",
                (int(seq), sqlite_vec.serialize_float32(list(vec))))
        else:
            self.cx.execute(
                "INSERT INTO memory_vec_flat(seq, dim, embedding) VALUES(?, ?, ?)",
                (int(seq), self.dim, self._pack(vec)))
        self.cx.commit()

    def remember(self, text: str, *, origin: str, actor: Optional[str] = None,
                 agent_id: Optional[str] = None, valid_time: Optional[float] = None,
                 observed_time: Optional[float] = None, ledger_seq: Optional[int] = None,
                 metadata: Optional[Dict] = None) -> int:

        if origin not in bt.ORIGINS:
            raise ValueError(f"origin must be one of {bt.ORIGINS}, got {origin!r}")
        now = time.time()
        vt = now if valid_time is None else float(valid_time)
        ot = now if observed_time is None else float(observed_time)
        vec = self.embedder.embed(text)
        payload = {"text": text, "meta": metadata or {}, "embed_model": self.embedder.model_id}
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        seq = bt.append(self.cx, stream=_MEMORY_STREAM, valid_time=vt, observed_time=ot,
                        origin=origin, payload_json=payload_json, actor=actor,
                        agent_id=agent_id, ledger_seq=ledger_seq)
        self._index_put(seq, vec)
        return seq

    def _candidates(self, qv: Sequence[float], limit: int) -> List[Tuple[int, float]]:

        if self.vec_backend == "sqlite-vec":
            import sqlite_vec
            rows = self.cx.execute(
                "SELECT rowid AS seq, distance FROM memory_vec "
                "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (sqlite_vec.serialize_float32(list(qv)), int(limit))).fetchall()
            return [(int(r["seq"]), float(r["distance"])) for r in rows]
        out: List[Tuple[int, float]] = []
        for r in self.cx.execute("SELECT seq, embedding FROM memory_vec_flat"):
            out.append((int(r["seq"]), _cosine_distance(qv, self._unpack(r["embedding"]))))
        out.sort(key=lambda t: t[1])
        return out[:limit]

    def recall(self, query_text: str, *, k: int = 5, include_model: bool = False,
               observed_as_of: Optional[float] = None) -> List[Dict]:

        qv = self.embedder.embed(query_text)

        cand = self._candidates(qv, limit=max(k * 8, k + 16))
        if not cand:
            return []
        dist = {seq: d for seq, d in cand}
        placeholders = ",".join("?" * len(dist))
        sql = (f"SELECT seq, valid_time, observed_time, origin, actor, agent_id, ledger_seq, "
               f"payload_json FROM bitemporal_log "
               f"WHERE stream='{_MEMORY_STREAM}' AND seq IN ({placeholders})")
        params: List = list(dist.keys())
        if not include_model:
            sql += " AND origin != 'model'"
        if observed_as_of is not None:
            sql += " AND observed_time <= ?"
            params.append(float(observed_as_of))
        rows = self.cx.execute(sql, params).fetchall()
        hits: List[Dict] = []
        for r in rows:
            payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
            hits.append({
                "seq": int(r["seq"]),
                "distance": dist[int(r["seq"])],
                "origin": r["origin"],
                "text": payload.get("text"),
                "meta": payload.get("meta", {}),
                "valid_time": r["valid_time"],
                "observed_time": r["observed_time"],
                "actor": r["actor"],
                "agent_id": r["agent_id"],
                "ledger_seq": r["ledger_seq"],
            })
        hits.sort(key=lambda h: h["distance"])
        return hits[:k]

    def count(self, *, include_model: bool = True) -> int:
        sql = f"SELECT COUNT(*) FROM bitemporal_log WHERE stream='{_MEMORY_STREAM}'"
        if not include_model:
            sql += " AND origin != 'model'"
        return int(self.cx.execute(sql).fetchone()[0])

    def close(self) -> None:
        try:
            self.cx.close()
        except Exception:
            pass
