
from __future__ import annotations

import hashlib
import sqlite3
import time as _time
from typing import Iterable, List, Optional

from .model import CapToken, Block, CapTokError

_REV_DOMAIN = b"brainarbeit/captok/revoke/1"

def block_revocation_id(block: Block) -> str:

    if not isinstance(block, Block):
        raise CapTokError("block_revocation_id expects a Block")
    h = hashlib.blake2s(_REV_DOMAIN + bytes(block.next_pub) + bytes(block.sig),
                        digest_size=16).hexdigest()
    return "rev:b2:" + h

def chain_revocation_ids(token: CapToken) -> List[str]:

    if not isinstance(token, CapToken):
        raise CapTokError("chain_revocation_ids expects a CapToken")
    return [block_revocation_id(b) for b in token.blocks]

def prefix_revocation_id(token: CapToken, depth: int) -> str:

    if not isinstance(token, CapToken):
        raise CapTokError("prefix_revocation_id expects a CapToken")
    if not isinstance(depth, int) or depth < 0 or depth >= len(token.blocks):
        raise CapTokError(f"depth {depth} is out of range for a {len(token.blocks)}-block token")
    return block_revocation_id(token.blocks[depth])

_REVOCATION_TABLE = """
CREATE TABLE IF NOT EXISTS captok_revocations (
  revocation_id  TEXT PRIMARY KEY,   -- a per-block id OR any external id (e.g. a lease id)
  reason         TEXT,
  revoked_at     REAL NOT NULL
);
"""

class RevocationRegistry:

    def __init__(self, cx: Optional[sqlite3.Connection] = None) -> None:
        self.cx = cx
        self._mem: set[str] = set()
        if cx is not None:
            self.migrate(cx)
            for row in cx.execute("SELECT revocation_id FROM captok_revocations"):
                self._mem.add(row[0])

    @staticmethod
    def migrate(cx: sqlite3.Connection) -> None:

        cx.executescript(_REVOCATION_TABLE)
        cx.commit()

    def revoke(self, revocation_id: str, *, reason: Optional[str] = None,
               now: Optional[float] = None) -> None:

        if not isinstance(revocation_id, str) or not revocation_id:
            raise CapTokError("revocation_id must be a non-empty string")
        self._mem.add(revocation_id)
        if self.cx is not None:
            self.cx.execute(
                "INSERT OR IGNORE INTO captok_revocations(revocation_id,reason,revoked_at) VALUES(?,?,?)",
                (revocation_id, reason, float(now if now is not None else _time.time())))
            self.cx.commit()

    def revoke_ids(self, ids: Iterable[str], *, reason: Optional[str] = None) -> None:
        for i in ids:
            self.revoke(i, reason=reason)

    def is_revoked(self, revocation_id: str) -> bool:
        return revocation_id in self._mem

    def revoke_block(self, block: Block, *, reason: Optional[str] = None) -> str:

        rid = block_revocation_id(block)
        self.revoke(rid, reason=reason)
        return rid

    def revoke_token_prefix(self, token: CapToken, depth: int, *,
                            reason: Optional[str] = None) -> str:

        rid = prefix_revocation_id(token, depth)
        self.revoke(rid, reason=reason)
        return rid

    def revoke_token(self, token: CapToken, *, reason: Optional[str] = None) -> str:

        return self.revoke_token_prefix(token, len(token.blocks) - 1, reason=reason)

    def is_token_revoked(self, token: CapToken) -> bool:

        return any(self.is_revoked(rid) for rid in chain_revocation_ids(token))

__all__ = [
    "block_revocation_id", "chain_revocation_ids", "prefix_revocation_id",
    "RevocationRegistry",
]
