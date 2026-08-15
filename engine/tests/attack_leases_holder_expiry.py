#!/usr/bin/env python3

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pnlib import rootkey
from pnlib.captok import mint as captok_mint, Caveat
from pnlib.captok.revoke import RevocationRegistry
from pnlib.leases import (
    LeaseStore, DurableVault, LeasedVaultBroker, LeaseDenied, CRED_SCOPE_DIM,
)

AUD = "box-1"
CRED = "API_KEY"
SECRET = "sk-live-do-not-log-1234567890"
BOUND = "agent:VICTIM"

def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="attack_leases_")
    os.close(fd)
    os.unlink(path)
    return path

def _cleanup(path):
    for p in (path, path + "-wal", path + "-shm"):
        try:
            os.unlink(p)
        except OSError:
            pass

def _env(trusted_time):

    path = _tmp_db()
    cx = sqlite3.connect(path)
    cx.row_factory = sqlite3.Row
    opriv, opub = rootkey.generate_owner_keypair_offbox()
    token = captok_mint(owner_priv=opriv, owner_pub=opub, agent="alice", audience=AUD,
                        max_redelegation_depth=0, exp=None,
                        caveats=[Caveat.scope(CRED_SCOPE_DIM, (CRED,))])
    vault = DurableVault()
    vault.put(CRED, SECRET)
    store = LeaseStore(cx)
    broker = LeasedVaultBroker(vault, store, owner_pubkey=opub,
                               revocations=RevocationRegistry(), audience=AUD,
                               clock=lambda: float(trusted_time))
    return broker, token, cx, path

def _expect_denied(fn, what):
    try:
        val = fn()
    except LeaseDenied as e:
        print("  HELD  ", what, "->", e)
        return
    raise AssertionError(f"OLD BREAK REPRODUCED: {what} was NOT denied, revealed {val!r}")

def test_control_correct_holder_live_succeeds():
    broker, token, cx, path = _env(trusted_time=1005.0)
    try:
        lease = broker.mint_lease(token, cred_ref=CRED, holder_agent_id=BOUND, ttl=60, now=1000.0)
        val = broker.cred_for(lease, holder_agent_id=BOUND)
        assert val == SECRET, val
        print("  OK    control: correct holder + live lease reveals the secret")
    finally:
        cx.close(); _cleanup(path)

def test_a3_omit_holder_denied():
    broker, token, cx, path = _env(trusted_time=1005.0)
    try:
        lease = broker.mint_lease(token, cred_ref=CRED, holder_agent_id=BOUND, ttl=60, now=1000.0)

        _expect_denied(lambda: broker.cred_for(lease), "(a) omit holder (default-None bypass)")
        _expect_denied(lambda: broker.cred_for(lease, now=1001.0),
                       "(a) omit holder + supply now")
    finally:
        cx.close(); _cleanup(path)

def test_b_wrong_holder_denied():
    broker, token, cx, path = _env(trusted_time=1005.0)
    try:
        lease = broker.mint_lease(token, cred_ref=CRED, holder_agent_id=BOUND, ttl=60, now=1000.0)
        _expect_denied(lambda: broker.cred_for(lease, holder_agent_id="agent:INTRUDER"),
                       "(b) wrong holder 'agent:INTRUDER'")
    finally:
        cx.close(); _cleanup(path)

def test_c_earlier_now_cannot_defeat_expiry():

    broker, token, cx, path = _env(trusted_time=1020.0)
    try:
        lease = broker.mint_lease(token, cred_ref=CRED, holder_agent_id=BOUND, ttl=10, now=1000.0)

        _expect_denied(lambda: broker.cred_for(lease, holder_agent_id=BOUND, now=1005.0),
                       "(c) earlier now=1005 vs real expiry 1010 @ trusted 1020")
        _expect_denied(lambda: broker.cred_for(lease, holder_agent_id=BOUND, now=0.0),
                       "(c) now=0 (maximally early) vs real expiry 1010 @ trusted 1020")
    finally:
        cx.close(); _cleanup(path)

def test_d_exactly_at_expiry_denied():
    broker, token, cx, path = _env(trusted_time=1010.0)
    try:
        lease = broker.mint_lease(token, cred_ref=CRED, holder_agent_id=BOUND, ttl=10, now=1000.0)

        _expect_denied(lambda: broker.cred_for(lease, holder_agent_id=BOUND),
                       "(d) redeem exactly at expiry (trusted==expires_at==1010)")
    finally:
        cx.close(); _cleanup(path)

_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    passed = failed = 0
    for t in _TESTS:
        print(f"[{t.__name__}]")
        try:
            t()
            passed += 1
        except AssertionError as e:
            failed += 1
            print("  FAIL  ", e)
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
