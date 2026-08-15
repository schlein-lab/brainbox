#!/usr/bin/env python3

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from relaylib import crypto
from pnlib import rootkey
from pnlib.agentid import (
    new_agent_identity, agent_id_for_pubkey, AgentIdentityError,
    migrate_identities, register_identity, load_identity,
    attest, verify_attestation, try_verify_attestation, attest_from_grant, AttestationError,
)
from pnlib.agentid.attest import GRANT_PRINCIPAL_DIM, GRANT_JOB_DIM
from pnlib.captok import mint as captok_mint, verify as captok_verify, Caveat

def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="agentid_test_")
    os.close(fd)
    os.unlink(path)
    return path

def _cleanup(path):
    for p in (path, path + "-wal", path + "-shm"):
        try:
            os.unlink(p)
        except OSError:
            pass

def test_identity_fresh_and_self_authenticating():
    a = new_agent_identity(parent_job="job-1", principal="alice", now=1000.0)
    b = new_agent_identity(parent_job="job-1", principal="alice", now=1000.0)

    assert a.privkey != b.privkey
    assert a.pubkey != b.pubkey
    assert a.agent_id != b.agent_id

    assert a.agent_id == agent_id_for_pubkey(a.pubkey)
    assert a.agent_id.startswith("agent:b2:")

def test_identity_signs_and_verifies():
    a = new_agent_identity(parent_job="job-1", principal="alice")
    sig = a.sign(b"hello")
    assert crypto.ed_verify(a.pubkey, sig, b"hello")
    assert not crypto.ed_verify(a.pubkey, sig, b"other")

    pub_only = a.drop_private()
    try:
        pub_only.sign(b"x")
        assert False, "dropped-private identity must not sign"
    except AgentIdentityError:
        pass

def test_bad_pubkey_len_rejected():
    for bad in (b"", b"\x00" * 31, b"\x00" * 33):
        try:
            agent_id_for_pubkey(bad)
            assert False, "short/long pubkey must be rejected"
        except AgentIdentityError:
            pass

def test_store_persists_public_only():
    path = _tmp_db()
    try:
        cx = sqlite3.connect(path)
        cx.row_factory = sqlite3.Row
        migrate_identities(cx)
        a = new_agent_identity(parent_job="job-42", principal="bob", now=2000.0)
        att = attest(issuer_priv=crypto.gen_ed25519()[0], issuer="bob",
                     agent_id=a.agent_id, agent_pubkey=a.pubkey, parent_job="job-42",
                     principal="bob", not_after=9999999999, issued_at=2000)
        register_identity(cx, a, attestation=att, now=2000.0)
        rec = load_identity(cx, a.agent_id)
        assert rec is not None
        assert rec["pubkey"] == a.pubkey.hex()
        assert rec["parent_job"] == "job-42" and rec["principal"] == "bob"
        assert rec["issuer"] == "bob" and rec["attestation_json"]

        assert "privkey" not in rec
        assert a.privkey.hex() not in " ".join(str(v) for v in rec.values())
        cx.close()
    finally:
        _cleanup(path)

def test_store_refuses_mismatched_pair():
    path = _tmp_db()
    try:
        cx = sqlite3.connect(path)
        cx.row_factory = sqlite3.Row
        migrate_identities(cx)
        a = new_agent_identity(parent_job="j", principal="p")

        from pnlib.agentid.identity import AgentIdentity
        forged = AgentIdentity("agent:b2:deadbeef", a.pubkey, a.privkey, "j", "p", 0.0)
        try:
            register_identity(cx, forged)
            assert False, "mismatched agent_id/pubkey must be refused"
        except AgentIdentityError:
            pass
        cx.close()
    finally:
        _cleanup(path)

def _issuer():
    priv, pub = crypto.gen_ed25519()
    return priv, pub

def test_attestation_offline_verify():
    ipriv, ipub = _issuer()
    a = new_agent_identity(parent_job="job-7", principal="carol")
    att = attest(issuer_priv=ipriv, issuer="carol", agent_id=a.agent_id, agent_pubkey=a.pubkey,
                 parent_job="job-7", principal="carol", not_after=2000, issued_at=1000)

    g = verify_attestation(att, issuer_pubkey=ipub, now=1500,
                           expect_agent_id=a.agent_id, expect_job="job-7",
                           expect_principal="carol", expect_issuer="carol",
                           expect_agent_pubkey=a.pubkey)
    assert g.agent_id == a.agent_id

    ok, g2 = try_verify_attestation(att.to_json(), issuer_pubkey=ipub, now=1500)
    assert ok and g2.parent_job == "job-7"

def test_wrong_parent_job_rejected_by_expectation():
    ipriv, ipub = _issuer()
    a = new_agent_identity(parent_job="job-7", principal="carol")
    att = attest(issuer_priv=ipriv, issuer="carol", agent_id=a.agent_id, agent_pubkey=a.pubkey,
                 parent_job="job-7", principal="carol", not_after=2000, issued_at=1000)
    ok, err = try_verify_attestation(att, issuer_pubkey=ipub, now=1500, expect_job="job-OTHER")
    assert not ok and isinstance(err, AttestationError)
    assert "parent_job mismatch" in str(err)

def test_tampered_parent_job_breaks_signature():
    ipriv, ipub = _issuer()
    a = new_agent_identity(parent_job="job-7", principal="carol")
    att = attest(issuer_priv=ipriv, issuer="carol", agent_id=a.agent_id, agent_pubkey=a.pubkey,
                 parent_job="job-7", principal="carol", not_after=2000, issued_at=1000)

    forged = att.to_dict()
    forged["parent_job"] = "job-OTHER"
    ok, err = try_verify_attestation(forged, issuer_pubkey=ipub, now=1500)
    assert not ok and "signature invalid" in str(err)

def test_expired_attestation_rejected():
    ipriv, ipub = _issuer()
    a = new_agent_identity(parent_job="job-7", principal="carol")
    att = attest(issuer_priv=ipriv, issuer="carol", agent_id=a.agent_id, agent_pubkey=a.pubkey,
                 parent_job="job-7", principal="carol", not_after=2000, issued_at=1000)
    ok, err = try_verify_attestation(att, issuer_pubkey=ipub, now=2001)
    assert not ok and "expired" in str(err)

def test_wrong_issuer_key_rejected():
    ipriv, ipub = _issuer()
    _, wrong_pub = _issuer()
    a = new_agent_identity(parent_job="job-7", principal="carol")
    att = attest(issuer_priv=ipriv, issuer="carol", agent_id=a.agent_id, agent_pubkey=a.pubkey,
                 parent_job="job-7", principal="carol", not_after=2000, issued_at=1000)
    ok, err = try_verify_attestation(att, issuer_pubkey=wrong_pub, now=1500)
    assert not ok and "signature invalid" in str(err)

def test_agent_pubkey_mismatch_rejected():
    ipriv, ipub = _issuer()
    a = new_agent_identity(parent_job="job-7", principal="carol")
    other = new_agent_identity(parent_job="job-7", principal="carol")
    att = attest(issuer_priv=ipriv, issuer="carol", agent_id=a.agent_id, agent_pubkey=a.pubkey,
                 parent_job="job-7", principal="carol", not_after=2000, issued_at=1000)

    ok, err = try_verify_attestation(att, issuer_pubkey=ipub, now=1500,
                                     expect_agent_pubkey=other.pubkey)
    assert not ok and "agent_pubkey mismatch" in str(err)

def _grant_for(*, agent, principals, jobs, exp=None):
    opriv, opub = rootkey.generate_owner_keypair_offbox()
    cavs = [Caveat.member(GRANT_PRINCIPAL_DIM, principals),
            Caveat.member(GRANT_JOB_DIM, jobs)]
    tok = captok_mint(owner_priv=opriv, owner_pub=opub, agent=agent, audience="box-1",
                      max_redelegation_depth=0, exp=exp, caveats=cavs)
    grant = captok_verify(tok, owner_pubkey=opub, audience="box-1", now=1000)
    return grant

def test_attest_from_grant_ok_and_scope_denied():
    ipriv, ipub = _issuer()
    a = new_agent_identity(parent_job="job-A", principal="dave")
    grant = _grant_for(agent="dave", principals=["dave"], jobs=["job-A", "job-B"], exp=5000)

    att = attest_from_grant(issuer_priv=ipriv, grant=grant, issuer="dave",
                            agent_id=a.agent_id, agent_pubkey=a.pubkey,
                            parent_job="job-A", principal="dave", not_after=9999999999)
    g = verify_attestation(att, issuer_pubkey=ipub, now=1500)
    assert g.parent_job == "job-A" and g.cap_root == grant.root_pubkey_id

    assert att.not_after == 5000

    try:
        attest_from_grant(issuer_priv=ipriv, grant=grant, issuer="dave",
                          agent_id=a.agent_id, agent_pubkey=a.pubkey,
                          parent_job="job-Z", principal="dave", not_after=4000)
        assert False, "grant does not cover job-Z; must refuse"
    except AttestationError as e:
        assert "job" in str(e).lower()

def test_attest_from_grant_wrong_issuer_refused():
    ipriv, _ = _issuer()
    a = new_agent_identity(parent_job="job-A", principal="dave")
    grant = _grant_for(agent="dave", principals=["dave"], jobs=["job-A"])
    try:
        attest_from_grant(issuer_priv=ipriv, grant=grant, issuer="mallory",
                          agent_id=a.agent_id, agent_pubkey=a.pubkey,
                          parent_job="job-A", principal="dave", not_after=4000)
        assert False, "issuer is not the grant's agent; must refuse"
    except AttestationError as e:
        assert "effective agent" in str(e)

_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]

def main():
    p = f = 0
    for t in _TESTS:
        try:
            t()
            p += 1
            print(f"  PASS  {t.__name__}")
        except Exception as e:
            f += 1
            import traceback
            print(f"  FAIL  {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n=== {p} passed, {f} failed ===")
    sys.exit(1 if f else 0)

if __name__ == "__main__":
    main()
