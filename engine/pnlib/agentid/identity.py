
from __future__ import annotations

import hashlib
import json
import sqlite3
import time as _time
from dataclasses import dataclass
from typing import Optional

from relaylib import crypto

class AgentIdentityError(Exception):
    pass

_AGENT_FP_DOMAIN = b"brainarbeit/agentid/pub/1"

def agent_id_for_pubkey(pubkey: bytes) -> str:

    if not isinstance(pubkey, (bytes, bytearray)) or len(pubkey) != 32:
        raise AgentIdentityError("agent pubkey must be exactly 32 raw bytes")
    h = hashlib.blake2s(_AGENT_FP_DOMAIN + bytes(pubkey), digest_size=16).hexdigest()
    return "agent:b2:" + h

@dataclass(frozen=True)
class AgentIdentity:

    agent_id: str
    pubkey: bytes
    privkey: bytes
    parent_job: str
    principal: str
    created_at: float

    def sign(self, msg: bytes) -> bytes:
        if not isinstance(self.privkey, (bytes, bytearray)) or len(self.privkey) != 32:
            raise AgentIdentityError("agent private key is not available (ephemeral / dropped)")
        return crypto.ed_sign(bytes(self.privkey), msg)

    def public_record(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "pubkey": self.pubkey.hex(),
            "parent_job": self.parent_job,
            "principal": self.principal,
            "created_at": self.created_at,
        }

    def drop_private(self) -> "AgentIdentity":

        return AgentIdentity(self.agent_id, self.pubkey, b"", self.parent_job,
                             self.principal, self.created_at)

def new_agent_identity(*, parent_job: str, principal: str,
                       now: Optional[float] = None) -> AgentIdentity:

    if not isinstance(parent_job, str) or not parent_job:
        raise AgentIdentityError("parent_job must be a non-empty string")
    if not isinstance(principal, str) or not principal:
        raise AgentIdentityError("principal must be a non-empty string")
    priv, pub = crypto.gen_ed25519()
    return AgentIdentity(
        agent_id=agent_id_for_pubkey(pub),
        pubkey=pub, privkey=priv,
        parent_job=parent_job, principal=principal,
        created_at=float(now if now is not None else _time.time()),
    )

_IDENTITY_TABLE = """
CREATE TABLE IF NOT EXISTS agent_identities (
  agent_id          TEXT PRIMARY KEY,          -- self-authenticating fingerprint of the pubkey
  pubkey            TEXT NOT NULL,             -- hex Ed25519 PUBLIC key (private half NEVER stored)
  parent_job        TEXT NOT NULL,             -- the job this agent was spawned for
  principal         TEXT NOT NULL,             -- whom it acts on behalf of
  issuer            TEXT,                       -- who attested it (parent agent id / principal)
  not_after         INTEGER,                    -- attestation expiry (unix seconds), if attested
  created_at        REAL NOT NULL,
  registered_at     REAL NOT NULL,
  attestation_json  TEXT                        -- the signed attestation blob (attest.py), if any
);
CREATE INDEX IF NOT EXISTS idx_agent_identities_job    ON agent_identities(parent_job);
CREATE INDEX IF NOT EXISTS idx_agent_identities_princ  ON agent_identities(principal);
"""

def migrate(cx: sqlite3.Connection) -> None:

    cx.executescript(_IDENTITY_TABLE)
    cx.commit()

def register_identity(cx: sqlite3.Connection, identity: AgentIdentity, *,
                      attestation=None, now: Optional[float] = None) -> None:

    if not isinstance(identity, AgentIdentity):
        raise AgentIdentityError("register_identity expects an AgentIdentity")

    if identity.agent_id != agent_id_for_pubkey(identity.pubkey):
        raise AgentIdentityError("agent_id does not match its pubkey (refusing to register)")
    issuer = None
    not_after = None
    att_json = None
    if attestation is not None:
        issuer = getattr(attestation, "issuer", None)
        not_after = getattr(attestation, "not_after", None)
        att_json = attestation.to_json() if hasattr(attestation, "to_json") else json.dumps(attestation)
    cx.execute(
        "INSERT OR REPLACE INTO agent_identities"
        "(agent_id,pubkey,parent_job,principal,issuer,not_after,created_at,registered_at,attestation_json)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (identity.agent_id, identity.pubkey.hex(), identity.parent_job, identity.principal,
         issuer, not_after, identity.created_at,
         float(now if now is not None else _time.time()), att_json),
    )
    cx.commit()

def load_identity(cx: sqlite3.Connection, agent_id: str) -> Optional[dict]:

    row = cx.execute("SELECT * FROM agent_identities WHERE agent_id=?", (agent_id,)).fetchone()
    if row is None:
        return None
    return dict(row)

__all__ = [
    "AgentIdentity", "AgentIdentityError", "new_agent_identity", "agent_id_for_pubkey",
    "migrate", "register_identity", "load_identity",
]
