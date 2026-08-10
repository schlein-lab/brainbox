
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time as _time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

class LeaseError(Exception):
    pass

def new_revocation_id(lease_id: str) -> str:

    salt = os.urandom(8)
    h = hashlib.blake2s(b"brainarbeit/lease/rev/1|" + lease_id.encode() + b"|" + salt,
                        digest_size=16).hexdigest()
    return "rev:lease:" + h

@dataclass(frozen=True)
class Lease:

    lease_id: str
    cred_ref: str
    scope: frozenset
    holder_agent_id: str
    revocation_id: str
    issued_at: float
    expires_at: float
    max_uses: int = 1
    cap_chain_ids: Tuple[str, ...] = ()

    @property
    def ttl(self) -> float:
        return self.expires_at - self.issued_at

    def is_expired(self, now: Optional[float] = None) -> bool:
        t = float(now if now is not None else _time.time())
        return t > self.expires_at

    def to_row(self) -> dict:
        return {
            "lease_id": self.lease_id,
            "cred_ref": self.cred_ref,
            "scope_json": json.dumps(sorted(self.scope)),
            "holder_agent_id": self.holder_agent_id,
            "revocation_id": self.revocation_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "max_uses": int(self.max_uses),
            "cap_chain_json": json.dumps(list(self.cap_chain_ids)),
        }

    @staticmethod
    def from_row(row) -> "Lease":
        return Lease(
            lease_id=row["lease_id"], cred_ref=row["cred_ref"],
            scope=frozenset(json.loads(row["scope_json"])),
            holder_agent_id=row["holder_agent_id"], revocation_id=row["revocation_id"],
            issued_at=row["issued_at"], expires_at=row["expires_at"],
            max_uses=int(row["max_uses"]),
            cap_chain_ids=tuple(json.loads(row["cap_chain_json"] or "[]")),
        )

_LEASE_TABLE = """
CREATE TABLE IF NOT EXISTS leases (
  lease_id         TEXT PRIMARY KEY,
  cred_ref         TEXT NOT NULL,
  scope_json       TEXT NOT NULL,
  holder_agent_id  TEXT NOT NULL,
  revocation_id    TEXT NOT NULL,
  issued_at        REAL NOT NULL,
  expires_at       REAL NOT NULL,
  max_uses         INTEGER NOT NULL DEFAULT 1,
  uses             INTEGER NOT NULL DEFAULT 0,   -- redemptions so far (mutated on redeem)
  cap_chain_json   TEXT NOT NULL DEFAULT '[]'    -- backing capability chain revocation ids
);
CREATE INDEX IF NOT EXISTS idx_leases_holder ON leases(holder_agent_id);
CREATE INDEX IF NOT EXISTS idx_leases_rev    ON leases(revocation_id);
"""

class LeaseStore:

    def __init__(self, cx: sqlite3.Connection) -> None:
        self.cx = cx
        self.migrate(cx)

    @staticmethod
    def migrate(cx: sqlite3.Connection) -> None:
        cx.executescript(_LEASE_TABLE)
        cx.commit()

    def insert(self, lease: Lease) -> None:
        r = lease.to_row()
        self.cx.execute(
            "INSERT INTO leases"
            "(lease_id,cred_ref,scope_json,holder_agent_id,revocation_id,issued_at,expires_at,"
            "max_uses,uses,cap_chain_json) VALUES(?,?,?,?,?,?,?,?,0,?)",
            (r["lease_id"], r["cred_ref"], r["scope_json"], r["holder_agent_id"],
             r["revocation_id"], r["issued_at"], r["expires_at"], r["max_uses"],
             r["cap_chain_json"]))
        self.cx.commit()

    def get(self, lease_id: str) -> Optional[Tuple[Lease, int]]:

        row = self.cx.execute("SELECT * FROM leases WHERE lease_id=?", (lease_id,)).fetchone()
        if row is None:
            return None
        return Lease.from_row(row), int(row["uses"])

    def try_consume_use(self, lease_id: str) -> bool:

        cur = self.cx.execute(
            "UPDATE leases SET uses = uses + 1 WHERE lease_id=? AND uses < max_uses", (lease_id,))
        self.cx.commit()
        return cur.rowcount == 1

__all__ = ["Lease", "LeaseError", "LeaseStore", "new_revocation_id"]
