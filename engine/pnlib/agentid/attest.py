
from __future__ import annotations

import json
import time as _time
from dataclasses import dataclass, field
from typing import Optional

from relaylib import crypto
from pnlib import rootkey

AGENTID_ATTEST_DOMAIN = b"brainarbeit/agentid/attest/1"

GRANT_PRINCIPAL_DIM = "principal"
GRANT_JOB_DIM = "job"

class AttestationError(Exception):
    pass

@dataclass(frozen=True)
class Attestation:

    agent_id: str
    agent_pubkey: bytes
    parent_job: str
    principal: str
    issuer: str
    not_after: int
    issued_at: int
    nonce: str
    cap_root: Optional[str] = None
    sig: bytes = b""

    def _body(self) -> dict:
        return {
            "v": 1,
            "agent_id": self.agent_id,
            "agent_pubkey": self.agent_pubkey.hex(),
            "parent_job": self.parent_job,
            "principal": self.principal,
            "issuer": self.issuer,
            "not_after": int(self.not_after),
            "issued_at": int(self.issued_at),
            "nonce": self.nonce,
            "cap_root": self.cap_root,
        }

    def signing_bytes(self) -> bytes:
        canon = json.dumps(self._body(), sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
        return rootkey.domain_bind(AGENTID_ATTEST_DOMAIN, canon)

    def to_dict(self) -> dict:
        d = self._body()
        d["sig"] = self.sig.hex()
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)

    @staticmethod
    def from_dict(d) -> "Attestation":
        if not isinstance(d, dict):
            raise AttestationError("malformed attestation")
        try:
            return Attestation(
                agent_id=d["agent_id"],
                agent_pubkey=bytes.fromhex(d["agent_pubkey"]),
                parent_job=d["parent_job"],
                principal=d["principal"],
                issuer=d["issuer"],
                not_after=int(d["not_after"]),
                issued_at=int(d["issued_at"]),
                nonce=d["nonce"],
                cap_root=d.get("cap_root"),
                sig=bytes.fromhex(d.get("sig", "")),
            )
        except (KeyError, ValueError, TypeError) as e:
            raise AttestationError(f"malformed attestation field: {e}")

    @staticmethod
    def from_json(s) -> "Attestation":
        try:
            return Attestation.from_dict(json.loads(s))
        except (ValueError, TypeError):
            raise AttestationError("attestation is not valid JSON")

def attest(*, issuer_priv: bytes, issuer: str, agent_id: str, agent_pubkey: bytes,
           parent_job: str, principal: str, not_after: int,
           issued_at: Optional[int] = None, nonce: Optional[str] = None,
           cap_root: Optional[str] = None) -> Attestation:

    if not isinstance(issuer_priv, (bytes, bytearray)) or len(issuer_priv) != 32:
        raise AttestationError("issuer_priv must be a 32-byte Ed25519 private key")
    if not isinstance(agent_pubkey, (bytes, bytearray)) or len(agent_pubkey) != 32:
        raise AttestationError("agent_pubkey must be 32 raw bytes")
    for name, val in (("issuer", issuer), ("agent_id", agent_id),
                      ("parent_job", parent_job), ("principal", principal)):
        if not isinstance(val, str) or not val:
            raise AttestationError(f"{name} must be a non-empty string")
    if not isinstance(not_after, int) or isinstance(not_after, bool):
        raise AttestationError("not_after must be an integer unix timestamp")
    ia = int(issued_at if issued_at is not None else _time.time())
    nz = nonce if nonce is not None else crypto.gen_ed25519()[1][:8].hex()
    unsigned = Attestation(agent_id=agent_id, agent_pubkey=bytes(agent_pubkey),
                           parent_job=parent_job, principal=principal, issuer=issuer,
                           not_after=int(not_after), issued_at=ia, nonce=nz, cap_root=cap_root)
    sig = crypto.ed_sign(bytes(issuer_priv), unsigned.signing_bytes())

    return Attestation(agent_id=agent_id, agent_pubkey=bytes(agent_pubkey),
                       parent_job=parent_job, principal=principal, issuer=issuer,
                       not_after=int(not_after), issued_at=ia, nonce=nz, cap_root=cap_root, sig=sig)

def attest_from_grant(*, issuer_priv: bytes, grant, issuer: str, agent_id: str, agent_pubkey: bytes,
                      parent_job: str, principal: str, not_after: int,
                      issued_at: Optional[int] = None, nonce: Optional[str] = None) -> Attestation:

    eff_agent = getattr(grant, "agent", None)
    if eff_agent is not None:

        if issuer != eff_agent:
            raise AttestationError(
                f"issuer {issuer!r} is not the grant's effective agent {eff_agent!r}")
    members = dict(getattr(grant, "members", {}) or {})
    princ_set = members.get(GRANT_PRINCIPAL_DIM)
    if princ_set is not None and principal not in princ_set:
        raise AttestationError(
            f"grant does not authorise delegating for principal {principal!r}")
    job_set = members.get(GRANT_JOB_DIM)
    scopes = dict(getattr(grant, "scopes", {}) or {})
    job_scope = scopes.get(GRANT_JOB_DIM)
    if job_set is not None and parent_job not in job_set:
        raise AttestationError(f"grant does not authorise job {parent_job!r}")
    if job_scope is not None and parent_job not in job_scope:
        raise AttestationError(f"grant does not authorise job {parent_job!r}")

    g_exp = getattr(grant, "exp", None)
    if g_exp is not None:
        not_after = min(int(not_after), int(g_exp))
    cap_root = getattr(grant, "root_pubkey_id", None)
    return attest(issuer_priv=issuer_priv, issuer=issuer, agent_id=agent_id,
                  agent_pubkey=agent_pubkey, parent_job=parent_job, principal=principal,
                  not_after=not_after, issued_at=issued_at, nonce=nonce, cap_root=cap_root)

def verify_attestation(att, *, issuer_pubkey: bytes, now: Optional[int] = None,
                       expect_agent_id: Optional[str] = None,
                       expect_agent_pubkey: Optional[bytes] = None,
                       expect_job: Optional[str] = None,
                       expect_principal: Optional[str] = None,
                       expect_issuer: Optional[str] = None) -> Attestation:

    if isinstance(att, str):
        att = Attestation.from_json(att)
    elif isinstance(att, dict):
        att = Attestation.from_dict(att)
    if not isinstance(att, Attestation):
        raise AttestationError("att must be an Attestation / JSON string / dict")
    if not isinstance(issuer_pubkey, (bytes, bytearray)) or len(issuer_pubkey) != 32:
        raise AttestationError("issuer_pubkey must be 32 raw bytes")

    if not crypto.ed_verify(bytes(issuer_pubkey), att.sig, att.signing_bytes()):
        raise AttestationError("attestation signature invalid (tampered or wrong issuer key)")

    from .identity import agent_id_for_pubkey
    if agent_id_for_pubkey(att.agent_pubkey) != att.agent_id:
        raise AttestationError("agent_id does not match the attested agent_pubkey")

    t = int(now) if now is not None else int(_time.time())
    if t > att.not_after:
        raise AttestationError(f"attestation expired (now {t} > not_after {att.not_after})")

    if expect_agent_id is not None and expect_agent_id != att.agent_id:
        raise AttestationError(
            f"agent_id mismatch: attested {att.agent_id!r}, expected {expect_agent_id!r}")
    if expect_agent_pubkey is not None and bytes(expect_agent_pubkey) != att.agent_pubkey:
        raise AttestationError("agent_pubkey mismatch: the signing agent is not the attested one")
    if expect_job is not None and expect_job != att.parent_job:
        raise AttestationError(
            f"parent_job mismatch: attested {att.parent_job!r}, expected {expect_job!r}")
    if expect_principal is not None and expect_principal != att.principal:
        raise AttestationError(
            f"principal mismatch: attested {att.principal!r}, expected {expect_principal!r}")
    if expect_issuer is not None and expect_issuer != att.issuer:
        raise AttestationError(
            f"issuer mismatch: attested {att.issuer!r}, expected {expect_issuer!r}")
    return att

def try_verify_attestation(att, **kw):

    try:
        return True, verify_attestation(att, **kw)
    except AttestationError as e:
        return False, e

__all__ = [
    "Attestation", "AttestationError", "attest", "attest_from_grant",
    "verify_attestation", "try_verify_attestation", "AGENTID_ATTEST_DOMAIN",
    "GRANT_PRINCIPAL_DIM", "GRANT_JOB_DIM",
]
