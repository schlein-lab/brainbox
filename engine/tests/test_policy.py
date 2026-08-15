#!/usr/bin/env python3

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pnlib import rootkey
from pnlib.autonomy import levels as L
from pnlib.policy.model import (
    Policy, Rule, policy_hash, select_rule,
    EFFECT_ALLOW, EFFECT_DENY, EFFECT_REQUIRE_CEREMONY,
)
from pnlib.policy.sign import sign_policy, SignedPolicy, policy_signing_bytes
from pnlib.policy.verify import (
    verify_signed_policy, try_verify_signed_policy, verify_chain, PolicyVerifyError,
)
from pnlib.policy.store import PolicyStore, PolicyStoreError, Proposal
from pnlib.policy import engine
from relaylib import crypto

def _owner():
    return rootkey.generate_owner_keypair_offbox()

def _policy(*, version=1, prev=None, effective_time=0, rules=(), owner_caps=None):
    return Policy(version=version, effective_time=effective_time, rules=tuple(rules),
                  owner_caps=owner_caps or {}, prev_version_hash=prev)

def _signed(priv, pub, **kw):
    return sign_policy(owner_priv=priv, owner_pub=pub, policy=_policy(**kw))

def _tmpdb():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="pn-policy-test-")
    os.close(fd)
    os.unlink(path)
    return path

def test_sign_and_verify_roundtrip():
    priv, pub = _owner()
    sp = _signed(priv, pub, rules=[Rule(principal="a", task_type="t", effect=EFFECT_ALLOW)])
    pol = verify_signed_policy(sp, owner_pubkey=pub)
    assert pol.version == 1 and len(pol.rules) == 1
    ok, res = try_verify_signed_policy(sp, owner_pubkey=pub)
    assert ok and isinstance(res, Policy)

def test_unsigned_or_forged_signature_rejected():
    priv, pub = _owner()
    sp = _signed(priv, pub)

    forged = SignedPolicy(policy=sp.policy, signature=b"\x00" * 64,
                          root_pubkey_id=sp.root_pubkey_id)
    try:
        verify_signed_policy(forged, owner_pubkey=pub)
        assert False, "forged signature accepted"
    except PolicyVerifyError:
        pass

    bad_len = SignedPolicy(policy=sp.policy, signature=b"\x01" * 10,
                           root_pubkey_id=sp.root_pubkey_id)
    ok, _ = try_verify_signed_policy(bad_len, owner_pubkey=pub)
    assert not ok

def test_wrong_owner_key_rejected():
    priv, pub = _owner()
    attacker_priv, attacker_pub = _owner()
    sp = _signed(priv, pub)

    try:
        verify_signed_policy(sp, owner_pubkey=attacker_pub)
        assert False, "verified against the wrong owner key"
    except PolicyVerifyError:
        pass

    pol = _policy(rules=[Rule(effect=EFFECT_ALLOW)])
    att_sig = crypto.ed_sign(bytes(attacker_priv), policy_signing_bytes(pol))
    spoof = SignedPolicy(policy=pol, signature=att_sig,
                         root_pubkey_id=rootkey.owner_fingerprint(pubkey=pub))
    try:
        verify_signed_policy(spoof, owner_pubkey=pub)
        assert False, "spoofed root_pubkey_id with a wrong-key signature accepted"
    except PolicyVerifyError:
        pass

def test_tampered_body_rejected():
    priv, pub = _owner()
    sp = _signed(priv, pub, owner_caps={"a": 1})

    tampered = SignedPolicy(policy=_policy(owner_caps={"a": 5}),
                            signature=sp.signature, root_pubkey_id=sp.root_pubkey_id)
    try:
        verify_signed_policy(tampered, owner_pubkey=pub)
        assert False, "tampered (widened) body accepted under the old signature"
    except PolicyVerifyError:
        pass

def test_verify_chain_accepts_good_rejects_rollback():
    priv, pub = _owner()
    v1 = _signed(priv, pub, version=1, prev=None, rules=[Rule(effect=EFFECT_ALLOW)])
    v2 = _signed(priv, pub, version=2, prev=policy_hash(v1.policy),
                 rules=[Rule(effect=EFFECT_DENY)])

    pols = verify_chain([v1, v2], owner_pubkey=pub)
    assert [p.version for p in pols] == [1, 2]

    try:
        verify_chain([v2, v1], owner_pubkey=pub)
        assert False, "rolled-back / reordered chain accepted"
    except PolicyVerifyError:
        pass

    v2_bad = _signed(priv, pub, version=2, prev="pol:b2:" + "00" * 16,
                     rules=[Rule(effect=EFFECT_DENY)])
    try:
        verify_chain([v1, v2_bad], owner_pubkey=pub)
        assert False, "broken-link chain accepted"
    except PolicyVerifyError:
        pass

    v1_bad = _signed(priv, pub, version=1, prev="pol:b2:" + "11" * 16)
    try:
        verify_chain([v1_bad], owner_pubkey=pub)
        assert False, "genesis with a prev hash accepted"
    except PolicyVerifyError:
        pass

def test_store_apply_and_ledger_sink():
    priv, pub = _owner()
    recorded = []
    path = _tmpdb()
    try:
        st = PolicyStore(path, owner_pubkey=pub, ledger_sink=recorded.append)
        v1 = _signed(priv, pub, version=1, prev=None, rules=[Rule(effect=EFFECT_ALLOW)])
        st.apply_signed(v1)
        v2 = _signed(priv, pub, version=2, prev=policy_hash(v1.policy),
                     rules=[Rule(effect=EFFECT_DENY)])
        st.apply_signed(v2)
        assert st.current().policy.version == 2
        assert [sp.policy.version for sp in st.history()] == [1, 2]

        assert len(recorded) == 2
        assert recorded[0]["type"] == "policy.version.applied" and recorded[1]["version"] == 2
        st.close()
    finally:
        os.path.exists(path) and os.unlink(path)

def test_store_rejects_rollback_and_keeps_last_good():
    priv, pub = _owner()
    path = _tmpdb()
    try:
        st = PolicyStore(path, owner_pubkey=pub)
        v1 = _signed(priv, pub, version=1, prev=None,
                     rules=[Rule(effect=EFFECT_ALLOW)], owner_caps={"a": 5})
        v2 = _signed(priv, pub, version=2, prev=policy_hash(v1.policy),
                     rules=[Rule(effect=EFFECT_DENY)], owner_caps={"a": 0})
        st.apply_signed(v1)
        st.apply_signed(v2)

        try:
            st.apply_signed(v1)
            assert False, "store accepted a rolled-back old signed policy"
        except PolicyStoreError:
            pass

        v1b = _signed(priv, pub, version=2, prev=policy_hash(v1.policy),
                      rules=[Rule(effect=EFFECT_ALLOW)])
        try:
            st.apply_signed(v1b)
            assert False, "store accepted a duplicate-version policy"
        except PolicyStoreError:
            pass

        assert st.current().policy.version == 2
        assert st.current().policy.owner_caps.get("a") == 0
        st.close()
    finally:
        os.path.exists(path) and os.unlink(path)

def test_store_rejects_bad_signature_and_keeps_last_good():
    priv, pub = _owner()
    attacker_priv, _ = _owner()
    path = _tmpdb()
    try:
        st = PolicyStore(path, owner_pubkey=pub)
        v1 = _signed(priv, pub, version=1, prev=None, rules=[Rule(effect=EFFECT_ALLOW)])
        st.apply_signed(v1)

        forged_pol = _policy(version=2, prev=policy_hash(v1.policy),
                             rules=[Rule(effect=EFFECT_ALLOW)])
        forged = SignedPolicy(policy=forged_pol,
                              signature=crypto.ed_sign(bytes(attacker_priv),
                                                       policy_signing_bytes(forged_pol)),
                              root_pubkey_id=v1.root_pubkey_id)
        try:
            st.apply_signed(forged)
            assert False, "store accepted a wrong-key-signed policy"
        except PolicyStoreError:
            pass
        assert st.current().policy.version == 1
        st.close()
    finally:
        os.path.exists(path) and os.unlink(path)

def test_propose_records_only_and_cannot_activate():
    priv, pub = _owner()
    path = _tmpdb()
    try:
        st = PolicyStore(path, owner_pubkey=pub)

        proposed = _policy(version=1, rules=[Rule(effect=EFFECT_ALLOW)], owner_caps={"a": 5})
        prop = st.propose(proposed, advisor="prio-advisor", note="looks fine to me")
        assert isinstance(prop, Proposal) and prop.advisor == "prio-advisor"

        assert [p.id for p in st.proposals()] == [prop.id]

        assert st.current() is None, "propose() must not activate any policy"

        for meth in ("activate", "promote", "activate_proposal", "promote_proposal",
                     "apply_proposal", "accept_proposal"):
            assert not hasattr(st, meth), f"store exposes an illicit activation method: {meth}"

        assert not hasattr(prop, "signature")
        try:
            st.apply_signed(prop)
            assert False, "apply_signed accepted an unsigned Proposal"
        except PolicyStoreError:
            pass
        assert st.current() is None
        st.close()
    finally:
        os.path.exists(path) and os.unlink(path)

def test_engine_permit_deny_require_ceremony():
    priv, pub = _owner()
    sp = _signed(priv, pub, rules=[
        Rule(principal="a", task_type="read-thing", resource="*", effect=EFFECT_ALLOW),
        Rule(principal="a", task_type="deploy", resource="*",
             effect=EFFECT_REQUIRE_CEREMONY, ceremony=L.CEREMONY_APPROVE),
        Rule(principal="a", task_type="rm-rf", resource="*", effect=EFFECT_DENY),
    ], owner_caps={"a": 5})

    d = engine.decide(("a",), {"task_type": "read-thing"}, L.L1_READ, sp, owner_pubkey=pub, now=0)
    assert d.effect == engine.PERMIT and d.ceremony == L.CEREMONY_NONE, d.reason

    d2 = engine.decide(("a",), {"task_type": "read-thing"}, L.L3_SUPERVISED, sp,
                       owner_pubkey=pub, now=0)
    assert d2.effect == engine.REQUIRE_CEREMONY and d2.ceremony == L.CEREMONY_APPROVE, d2.reason

    d3 = engine.decide(("a",), {"task_type": "deploy"}, L.L2_ASSIST, sp, owner_pubkey=pub, now=0)
    assert d3.effect == engine.REQUIRE_CEREMONY and d3.ceremony == L.CEREMONY_APPROVE

    d4 = engine.decide(("a",), {"task_type": "rm-rf"}, L.L1_READ, sp, owner_pubkey=pub, now=0)
    assert d4.effect == engine.DENY, d4.reason

    d5 = engine.decide(("stranger",), {"task_type": "read-thing"}, L.L1_READ, sp,
                       owner_pubkey=pub, now=0)
    assert d5.effect == engine.DENY and not d5.fail_safe

def test_engine_fail_safe_on_bad_signature_never_fail_open():
    priv, pub = _owner()
    attacker_priv, _ = _owner()
    pol = _policy(rules=[Rule(principal="a", effect=EFFECT_ALLOW)], owner_caps={"a": 5})

    bad = SignedPolicy(policy=pol,
                       signature=crypto.ed_sign(bytes(attacker_priv), policy_signing_bytes(pol)),
                       root_pubkey_id=rootkey.owner_fingerprint(pubkey=pub))

    d = engine.decide(("a",), {"task_type": "deploy"}, L.L5_FULL_AUTO, bad, owner_pubkey=pub, now=0)
    assert d.effect == engine.DENY and d.fail_safe, d.reason
    assert d.effective_level <= engine.FAIL_SAFE_MAX_LEVEL

    d2 = engine.decide(("a",), {"task_type": "deploy"}, L.L1_READ, bad, owner_pubkey=pub, now=0)
    assert d2.effect == engine.PERMIT and d2.fail_safe and d2.effective_level == L.L1_READ

    d3 = engine.decide(("a",), {"task_type": "deploy"}, L.L5_FULL_AUTO, "not-a-policy",
                       owner_pubkey=pub, now=0)
    assert d3.effect == engine.DENY and d3.fail_safe

def test_engine_fail_safe_on_not_yet_effective_policy():
    priv, pub = _owner()
    sp = _signed(priv, pub, effective_time=10_000,
                 rules=[Rule(principal="a", effect=EFFECT_ALLOW)], owner_caps={"a": 5})

    d = engine.decide(("a",), {"task_type": "deploy"}, L.L4_DELEGATED, sp, owner_pubkey=pub, now=0)
    assert d.effect == engine.DENY and d.fail_safe, d.reason

    d2 = engine.decide(("a",), {"task_type": "deploy"}, L.L4_DELEGATED, sp, owner_pubkey=pub,
                       now=20_000)
    assert d2.permitted and not d2.fail_safe

def test_engine_invalid_requested_level_and_missing_principal_fail_safe():
    priv, pub = _owner()
    sp = _signed(priv, pub, rules=[Rule(effect=EFFECT_ALLOW)])
    d = engine.decide(("a",), {"task_type": "t"}, 99, sp, owner_pubkey=pub, now=0)
    assert d.effect == engine.DENY and d.fail_safe
    d2 = engine.decide(None, {"task_type": "t"}, L.L1_READ, sp, owner_pubkey=pub, now=0)
    assert d2.fail_safe

def test_select_rule_specificity():
    p = _policy(rules=[
        Rule(principal="*", task_type="*", resource="*", effect=EFFECT_ALLOW),
        Rule(principal="a", task_type="*", resource="*", effect=EFFECT_DENY),
    ])

    r = select_rule(p, "a", "t", "res")
    assert r.effect == EFFECT_DENY

    r2 = select_rule(p, "b", "t", "res")
    assert r2.effect == EFFECT_ALLOW

def _run_standalone() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"  FAIL  {t.__name__}: {e!r}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(_run_standalone())
