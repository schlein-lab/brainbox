
from __future__ import annotations

import json
import os
import time
from typing import Callable, List, Optional, Protocol

from pnlib.ledger import merkle
from pnlib.ledger.checkpoint import STH, log_id_for_pubkey, verify_sth

class WitnessAlarm(Exception):

    def __init__(self, kind: str, reason: str, sth: Optional[STH] = None) -> None:
        super().__init__(f"[{kind}] {reason}")
        self.kind = kind
        self.reason = reason
        self.sth = sth

class LedgerSource(Protocol):

    def get_sth(self) -> STH:

        ...

    def get_consistency_proof(self, first_size: int, second_size: int) -> List[bytes]:

        ...

KIND_BAD_SIGNATURE = "BAD_SIGNATURE"
KIND_TRUNCATION = "TRUNCATION"
KIND_INCONSISTENT = "INCONSISTENT"
KIND_LOG_ID_MISMATCH = "LOG_ID_MISMATCH"

class Witness:

    STATE_VERSION = 1

    def __init__(self, pinned_pubkey_hex: str, *, state_path: Optional[str] = None,
                 on_alarm: Optional[Callable[[dict], None]] = None) -> None:
        self.pinned_pubkey_hex = pinned_pubkey_hex.strip().lower()
        self.pinned_pub = bytes.fromhex(self.pinned_pubkey_hex)
        self.log_id = log_id_for_pubkey(self.pinned_pub)
        self.state_path = state_path
        self.on_alarm = on_alarm
        self.sths: List[dict] = []
        self.alarms: List[dict] = []
        if state_path and os.path.exists(state_path):
            self._load()

    @classmethod
    def enroll(cls, pinned_pubkey_hex: str, *, state_path: Optional[str] = None,
               on_alarm: Optional[Callable[[dict], None]] = None) -> "Witness":

        w = cls(pinned_pubkey_hex, state_path=state_path, on_alarm=on_alarm)
        if state_path and not os.path.exists(state_path):
            w._persist()
        return w

    @property
    def latest(self) -> Optional[STH]:

        return STH.from_dict(self.sths[-1]) if self.sths else None

    @property
    def size(self) -> int:
        return self.latest.tree_size if self.sths else 0

    def _load(self) -> None:
        with open(self.state_path) as f:
            d = json.load(f)
        if d.get("pinned_pubkey") != self.pinned_pubkey_hex or d.get("log_id") != self.log_id:

            raise WitnessAlarm(KIND_LOG_ID_MISMATCH,
                               "stored witness state was pinned to a different ledger key")
        self.sths = list(d.get("sths", []))
        self.alarms = list(d.get("alarms", []))

    def _persist(self) -> None:
        if not self.state_path:
            return
        tmp = self.state_path + ".tmp"
        d = {
            "version": self.STATE_VERSION,
            "pinned_pubkey": self.pinned_pubkey_hex,
            "log_id": self.log_id,
            "sths": self.sths,
            "alarms": self.alarms,
        }
        with open(tmp, "w") as f:
            json.dump(d, f, sort_keys=True, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state_path)

    def _raise_alarm(self, kind: str, reason: str, sth: Optional[STH] = None) -> None:
        rec = {
            "kind": kind,
            "reason": reason,
            "ts": time.time(),
            "witnessed_size": self.size,
            "sth": sth.to_dict() if sth is not None else None,
        }
        self.alarms.append(rec)
        self._persist()
        if self.on_alarm is not None:
            try:
                self.on_alarm(rec)
            except Exception:

                pass
        raise WitnessAlarm(kind, reason, sth)

    def poll(self, source: LedgerSource) -> STH:

        sth = source.get_sth()

        if sth.log_id != self.log_id:
            self._raise_alarm(KIND_LOG_ID_MISMATCH,
                              f"STH log_id {sth.log_id[:16]}... != pinned {self.log_id[:16]}...", sth)
        if not verify_sth(self.pinned_pub, sth):
            self._raise_alarm(KIND_BAD_SIGNATURE,
                              "STH signature does not verify under the pinned ledger key", sth)

        prev = self.latest
        if prev is None:

            self.sths.append(sth.to_dict())
            self._persist()
            return sth

        if sth.tree_size < prev.tree_size:
            self._raise_alarm(
                KIND_TRUNCATION,
                f"presented tree_size {sth.tree_size} < witnessed {prev.tree_size} "
                f"(append-only log cannot shrink -> tail truncated)", sth)

        proof = source.get_consistency_proof(prev.tree_size, sth.tree_size)
        if not merkle.verify_consistency(prev.tree_size, sth.tree_size, proof,
                                         prev.root_hash, sth.root_hash):
            self._raise_alarm(
                KIND_INCONSISTENT,
                f"consistency proof {prev.tree_size}->{sth.tree_size} does not reconstruct the "
                f"pinned root (history was rewritten)", sth)

        if sth.tree_size != prev.tree_size or sth.root_hash != prev.root_hash:
            self.sths.append(sth.to_dict())
            self._persist()
        return sth
