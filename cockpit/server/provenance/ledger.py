

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional

from signer import Signer, make_signer

CHAIN_GENESIS = "0" * 64

@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    ts: int
    run_id: str
    principal: str
    verb: str
    args_digest: str
    chain_prev: str
    key_id: str
    artifact_hash: Optional[str] = None
    meta: dict = field(default_factory=dict)
    entry_hash: str = ""
    sig: str = ""

    def signed_body(self) -> bytes:

        body = {
            "seq": self.seq,
            "ts": self.ts,
            "run_id": self.run_id,
            "principal": self.principal,
            "verb": self.verb,
            "args_digest": self.args_digest,
            "artifact_hash": self.artifact_hash,
            "meta": self.meta,
            "chain_prev": self.chain_prev,
            "key_id": self.key_id,
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def compute_hash(self) -> str:
        return hashlib.sha256(self.signed_body()).hexdigest()

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def from_json_line(line: str) -> "LedgerEntry":
        d = json.loads(line)
        return LedgerEntry(**d)

class LedgerError(Exception):
    pass

class ChainVerificationError(LedgerError):
    def __init__(self, seq: int, reason: str):
        self.seq = seq
        self.reason = reason
        super().__init__(f"ledger chain broken at seq={seq}: {reason}")

class ProvenanceLedger:

    def __init__(self, signer: Optional[Signer] = None, path: Optional[str] = None):
        self._signer = signer or make_signer("hmac-sha256")
        self._entries: list[LedgerEntry] = []
        self._lock = threading.Lock()
        self._path = path
        if path and os.path.exists(path):
            self._load(path)

    def append(
        self,
        *,
        run_id: str,
        principal: str,
        verb: str,
        args_digest: str,
        artifact_hash: Optional[str] = None,
        meta: Optional[dict] = None,
        ts: Optional[int] = None,
    ) -> LedgerEntry:

        meta = dict(meta or {})
        _reject_secret_meta(meta)
        with self._lock:
            seq = len(self._entries)
            prev_hash = self._entries[-1].entry_hash if self._entries else CHAIN_GENESIS
            entry = LedgerEntry(
                seq=seq,
                ts=ts if ts is not None else int(time.time() * 1000),
                run_id=run_id,
                principal=principal,
                verb=verb,
                args_digest=args_digest,
                artifact_hash=artifact_hash,
                meta=meta,
                chain_prev=prev_hash,
                key_id=self._signer.key_id(),
            )
            body = entry.signed_body()
            entry_hash = hashlib.sha256(body).hexdigest()
            sig = self._signer.sign(body).hex()
            sealed = LedgerEntry(
                **{**asdict(entry), "entry_hash": entry_hash, "sig": sig}
            )
            self._entries.append(sealed)
            if self._path:
                self._append_line(sealed)
            return sealed

    def verify_chain(self, *, entries: Optional[Iterable[LedgerEntry]] = None) -> bool:

        seq_expected = 0
        prev_hash = CHAIN_GENESIS
        for e in (entries if entries is not None else self._entries):

            if e.seq != seq_expected:
                raise ChainVerificationError(e.seq, f"expected seq {seq_expected}")

            if e.chain_prev != prev_hash:
                raise ChainVerificationError(
                    e.seq, f"chain_prev {e.chain_prev[:12]}.. != prev entry_hash {prev_hash[:12]}.."
                )

            recomputed = e.compute_hash()
            if e.entry_hash != recomputed:
                raise ChainVerificationError(
                    e.seq, f"entry_hash {e.entry_hash[:12]}.. != recomputed {recomputed[:12]}.. (body edited)"
                )

            try:
                sig_bytes = bytes.fromhex(e.sig)
            except ValueError:
                raise ChainVerificationError(e.seq, "sig is not valid hex")
            if not self._signer.verify(e.signed_body(), sig_bytes):
                raise ChainVerificationError(e.seq, "signature does not verify (forged/tampered)")
            prev_hash = e.entry_hash
            seq_expected += 1
        return True

    def provenance(self, artifact_hash: str) -> Optional[dict]:

        for e in reversed(self._entries):
            if e.artifact_hash == artifact_hash:
                return {
                    "ledger_seq": e.seq,
                    "hash": e.artifact_hash,
                    "sig": e.sig,
                    "produced_by": e.run_id,
                    "ts": e.ts,
                    "chain_prev": e.chain_prev,
                    "principal": e.principal,
                    "verb": e.verb,
                }
        return None

    def tail(self, since_seq: int = 0) -> list[dict]:

        return [asdict(e) for e in self._entries if e.seq >= since_seq]

    def head_hash(self) -> str:
        return self._entries[-1].entry_hash if self._entries else CHAIN_GENESIS

    def __len__(self) -> int:
        return len(self._entries)

    def _append_line(self, entry: LedgerEntry) -> None:

        with open(self._path, "a", encoding="utf-8") as f:
            f.write(entry.to_json_line() + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _load(self, path: str) -> None:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self._entries.append(LedgerEntry.from_json_line(line))

        self.verify_chain()

_SECRET_KEY_HINTS = ("secret", "password", "passwd", "token", "api_key", "apikey", "private_key", "credential")

def _reject_secret_meta(meta: dict) -> None:

    for k in meta:
        kl = str(k).lower()
        if any(h in kl for h in _SECRET_KEY_HINTS):
            raise LedgerError(
                f"refusing to persist secret-bearing meta key {k!r} in the provenance ledger (invariant §9.2)"
            )
