#!/usr/bin/env python3

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pnlib import rootkey
from pnlib.captok import mint as captok_mint, attenuate as captok_attenuate, Caveat
from pnlib.captok.model import CapToken
from pnlib.captok.revoke import RevocationRegistry
from pnlib.leases import (
    Lease, LeaseStore, DurableVault, LeasedVaultBroker, LeaseDenied, CRED_SCOPE_DIM,
)

AUD = "box-1"
CRED = "API_KEY"
SECRET = "sk-live-do-not-log-1234567890"

def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="leases_test_")
    os.close(fd)
    os.unlink(path)
    return path

def _cleanup(path):
    for p in (path, path + "-wal", path + "-shm"):
        try:
            os.unlink(p)
        except OSError:
            pass

def _owner():
    return rootkey.generate_owner_keypair_offbox()

def _mint_root(opriv, opub, *, creds=(CRED,), max_depth=0, exp=None):
    return captok_mint(owner_priv=opriv, owner_pub=opub, agent="alice", audience=AUD,
                       max_redelegation_depth=max_depth, exp=exp,
                       caveats=[Caveat.scope(CRED_SCOPE_DIM, creds)])

def _fresh_env(*, creds=(CRED,), max_depth=0, exp=None):

    path = _tmp_db()
    cx = sqlite3.connect(path)
    cx.row_factory = sqlite3.Row
    opriv, opub = _owner()
    token = _mint_root(opriv, opub, creds=creds, max_depth=max_depth, exp=exp)
    vault = DurableVault()
    vault.put(CRED, SECRET)
    store = LeaseStore(cx)
    broker = LeasedVaultBroker(vault, store, owner_pubkey=opub,
                               revocations=RevocationRegistry(), audience=AUD,
                               _trust_redeem_now=True)
    return broker, token, cx, path

def test_lease_e2e_single_use():
    broker, token, cx, path = _fresh_env()
    try:
        lease = broker.mint_lease(token, cred_ref=CRED, holder_agent_id="agent:X",
                                  ttl=60, now=1000.0)
        assert isinstance(lease, Lease)
        assert lease.cred_ref == CRED and CRED in lease.scope

        val = broker.cred_for(lease, holder_agent_id="agent:X", now=1001.0)
        assert val == SECRET

        try:
            broker.cred_for(lease, holder_agent_id="agent:X", now=1002.0)
            assert False, "single-use lease must deny the second redemption"
        except LeaseDenied as e:
            assert "exhausted" in str(e)
        cx.close()
    finally:
        _cleanup(path)

def test_ttl_expiry_denies():
    broker, token, cx, path = _fresh_env()
    try:
        lease = broker.mint_lease(token, cred_ref=CRED, holder_agent_id="agent:X",
                                  ttl=10, now=1000.0)
        try:
            broker.cred_for(lease, holder_agent_id="agent:X", now=1011.0)
            assert False, "an expired lease must be denied"
        except LeaseDenied as e:
            assert "expired" in str(e)
        cx.close()
    finally:
        _cleanup(path)

def test_revoke_mid_lease_denies_next():
    broker, token, cx, path = _fresh_env()
    try:

        lease = broker.mint_lease(token, cred_ref=CRED, holder_agent_id="agent:X",
                                  ttl=600, max_uses=5, now=1000.0)
        assert broker.cred_for(lease, holder_agent_id="agent:X", now=1001.0) == SECRET

        broker.revoke_lease(lease, reason="agent finished")
        try:
            broker.cred_for(lease, holder_agent_id="agent:X", now=1002.0)
            assert False, "a revoked lease must deny the next cred_for"
        except LeaseDenied as e:
            assert "revoked" in str(e)
        cx.close()
    finally:
        _cleanup(path)

def test_cascade_revoke_chain_prefix():
    path = _tmp_db()
    cx = sqlite3.connect(path)
    cx.row_factory = sqlite3.Row
    try:
        opriv, opub = _owner()

        root = _mint_root(opriv, opub, creds=(CRED,), max_depth=2)
        child = captok_attenuate(root, agent="team")
        grand = captok_attenuate(child, agent="worker")

        vault = DurableVault(); vault.put(CRED, SECRET)
        store = LeaseStore(cx)
        broker = LeasedVaultBroker(vault, store, owner_pubkey=opub,
                                   revocations=RevocationRegistry(), audience=AUD,
                                   _trust_redeem_now=True)

        l_grand = broker.mint_lease(grand, cred_ref=CRED, holder_agent_id="agent:W",
                                    ttl=600, now=1000.0)
        assert broker.cred_for(l_grand, holder_agent_id="agent:W", now=1001.0) == SECRET
        l_child = broker.mint_lease(child, cred_ref=CRED, holder_agent_id="agent:T",
                                    ttl=600, now=1000.0)
        assert broker.cred_for(l_child, holder_agent_id="agent:T", now=1001.0) == SECRET

        pre = broker.mint_lease(grand, cred_ref=CRED, holder_agent_id="agent:W",
                                ttl=600, now=1000.0)

        broker.revoke_capability_prefix(grand, depth=1, reason="team compromised")

        for desc, name in ((grand, "grandchild"), (child, "child")):
            try:
                broker.mint_lease(desc, cred_ref=CRED, holder_agent_id="agent:W",
                                  ttl=600, now=1002.0)
                assert False, f"{name} must be denied after prefix revocation"
            except LeaseDenied as e:
                assert "revoked" in str(e)

        try:
            broker.cred_for(pre, holder_agent_id="agent:W", now=1003.0)
            assert False, "pre-minted descendant lease must be denied after cap revoke"
        except LeaseDenied as e:
            assert "revoked" in str(e)

        l_root = broker.mint_lease(root, cred_ref=CRED, holder_agent_id="agent:A",
                                   ttl=600, now=1004.0)
        assert broker.cred_for(l_root, holder_agent_id="agent:A", now=1005.0) == SECRET
        cx.close()
    finally:
        _cleanup(path)

def test_scope_does_not_cover_cred_denied():
    broker, token, cx, path = _fresh_env(creds=(CRED,))
    try:
        broker.vault.put("OTHER", "another-secret")
        try:
            broker.mint_lease(token, cred_ref="OTHER", holder_agent_id="agent:X",
                              ttl=60, now=1000.0)
            assert False, "capability scope does not name OTHER; must deny"
        except LeaseDenied as e:
            assert "authorise" in str(e) or "scope" in str(e)
        cx.close()
    finally:
        _cleanup(path)

def test_wrong_holder_denied():
    broker, token, cx, path = _fresh_env()
    try:
        lease = broker.mint_lease(token, cred_ref=CRED, holder_agent_id="agent:X",
                                  ttl=60, now=1000.0)
        try:
            broker.cred_for(lease, holder_agent_id="agent:INTRUDER", now=1001.0)
            assert False, "a different holder must not redeem the lease"
        except LeaseDenied as e:
            assert "holder mismatch" in str(e)
        cx.close()
    finally:
        _cleanup(path)

def test_tampered_token_denied():
    broker, token, cx, path = _fresh_env()
    try:
        d = token.to_dict()

        sig = bytearray.fromhex(d["blocks"][0]["sig"])
        sig[0] ^= 0xFF
        d["blocks"][0]["sig"] = bytes(sig).hex()
        forged = CapToken.from_dict(d)
        try:
            broker.mint_lease(forged, cred_ref=CRED, holder_agent_id="agent:X",
                              ttl=60, now=1000.0)
            assert False, "a tampered capability token must be rejected"
        except LeaseDenied as e:
            assert "rejected" in str(e)
        cx.close()
    finally:
        _cleanup(path)

def test_expired_capability_denied():
    broker, token, cx, path = _fresh_env(exp=1000)
    try:
        try:
            broker.mint_lease(token, cred_ref=CRED, holder_agent_id="agent:X",
                              ttl=60, now=2000.0)
            assert False, "an expired capability must be rejected at mint"
        except LeaseDenied as e:
            assert "rejected" in str(e) or "expired" in str(e)
        cx.close()
    finally:
        _cleanup(path)

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
