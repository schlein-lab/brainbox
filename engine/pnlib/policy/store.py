
from __future__ import annotations

import sqlite3
import time as _time
from dataclasses import dataclass
from typing import Callable, List, Optional

from .model import Policy, policy_hash
from .sign import SignedPolicy
from .verify import verify_signed_policy, PolicyVerifyError

class PolicyStoreError(Exception):
    pass

def _normalize_sink(sink) -> Optional[Callable[[dict], None]]:

    if sink is None:
        return None
    if callable(sink):
        return sink
    for name in ("record", "append", "write"):
        fn = getattr(sink, name, None)
        if callable(fn):
            return lambda event, _fn=fn: _fn(event)
    raise PolicyStoreError("ledger sink must be a callable or expose .record/.append/.write")

@dataclass(frozen=True)
class Proposal:

    id: int
    advisor: str
    created_at: float
    policy: Policy
    note: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "advisor": self.advisor, "created_at": self.created_at,
                "policy": self.policy.to_dict(), "note": self.note}

class PolicyStore:

    def __init__(self, path: str, *, owner_pubkey: Optional[bytes] = None,
                 config: Optional[dict] = None, ledger_sink=None) -> None:
        self._path = path
        self._owner_pubkey = owner_pubkey
        self._config = config
        self._ledger = _normalize_sink(ledger_sink)
        self.cx = sqlite3.connect(path)
        self.cx.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self.cx.executescript(
            """
            CREATE TABLE IF NOT EXISTS policy_versions (
                version      INTEGER PRIMARY KEY,
                prev_hash    TEXT,
                self_hash    TEXT NOT NULL,
                effective_time INTEGER NOT NULL,
                blob         TEXT NOT NULL,   -- the SignedPolicy JSON
                applied_at   REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS policy_proposals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                advisor     TEXT NOT NULL,
                created_at  REAL NOT NULL,
                blob        TEXT NOT NULL,   -- the UNSIGNED Policy JSON
                note        TEXT NOT NULL DEFAULT ''
            );
            """
        )
        self.cx.commit()

    def current(self) -> Optional[SignedPolicy]:

        row = self.cx.execute(
            "SELECT blob FROM policy_versions ORDER BY version DESC LIMIT 1").fetchone()
        return None if row is None else SignedPolicy.from_json(row["blob"])

    def current_policy(self) -> Optional[Policy]:
        sp = self.current()
        return None if sp is None else sp.policy

    def history(self) -> List[SignedPolicy]:
        return [SignedPolicy.from_json(r["blob"])
                for r in self.cx.execute(
                    "SELECT blob FROM policy_versions ORDER BY version ASC")]

    def apply_signed(self, signed) -> Policy:

        if isinstance(signed, (str, dict)):
            signed = SignedPolicy.from_dict(signed) if isinstance(signed, dict) \
                else SignedPolicy.from_json(signed)
        if not isinstance(signed, SignedPolicy):
            raise PolicyStoreError("apply_signed requires a SignedPolicy (a Proposal is not signed)")

        try:
            policy = verify_signed_policy(signed, owner_pubkey=self._owner_pubkey,
                                          config=self._config)
        except PolicyVerifyError as e:
            raise PolicyStoreError(f"rejected: signature verification failed ({e})")

        head = self.current()
        if head is None:
            if policy.prev_version_hash is not None:
                raise PolicyStoreError(
                    "rejected: genesis policy must have prev_version_hash=None")
        else:
            if policy.version <= head.policy.version:
                raise PolicyStoreError(
                    f"rejected: replay/rollback — version {policy.version} is not greater than "
                    f"current head {head.policy.version}")
            expect = policy_hash(head.policy)
            if policy.prev_version_hash != expect:
                raise PolicyStoreError(
                    f"rejected: prev_version_hash {policy.prev_version_hash!r} does not join the "
                    f"current head hash {expect!r}")

        self_hash = policy_hash(policy)
        applied_at = _time.time()
        try:
            self.cx.execute(
                "INSERT INTO policy_versions (version, prev_hash, self_hash, effective_time, "
                "blob, applied_at) VALUES (?,?,?,?,?,?)",
                (policy.version, policy.prev_version_hash, self_hash, policy.effective_time,
                 signed.to_json(), applied_at))
            self.cx.commit()
        except sqlite3.IntegrityError as e:
            raise PolicyStoreError(f"rejected: append-only violation ({e})")

        if self._ledger is not None:
            try:
                self._ledger({
                    "type": "policy.version.applied",
                    "version": policy.version,
                    "self_hash": self_hash,
                    "prev_version_hash": policy.prev_version_hash,
                    "effective_time": policy.effective_time,
                    "root_pubkey_id": signed.root_pubkey_id,
                    "applied_at": applied_at,
                })
            except Exception:
                pass
        return policy

    def propose(self, policy, *, advisor: str, note: str = "") -> Proposal:

        if not isinstance(policy, Policy):
            raise PolicyStoreError("propose expects an (unsigned) Policy")
        if not isinstance(advisor, str) or not advisor:
            raise PolicyStoreError("advisor must be a non-empty string")
        policy.validate()
        created_at = _time.time()
        cur = self.cx.execute(
            "INSERT INTO policy_proposals (advisor, created_at, blob, note) VALUES (?,?,?,?)",
            (advisor, created_at, policy.to_json(), note))
        self.cx.commit()
        return Proposal(id=int(cur.lastrowid), advisor=advisor, created_at=created_at,
                        policy=policy, note=note)

    def proposals(self) -> List[Proposal]:
        out = []
        for r in self.cx.execute(
                "SELECT id, advisor, created_at, blob, note FROM policy_proposals ORDER BY id ASC"):
            out.append(Proposal(id=int(r["id"]), advisor=r["advisor"],
                                created_at=float(r["created_at"]),
                                policy=Policy.from_json(r["blob"]), note=r["note"]))
        return out

    def close(self) -> None:
        try:
            self.cx.close()
        except Exception:
            pass

__all__ = ["PolicyStore", "PolicyStoreError", "Proposal"]
