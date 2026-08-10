#!/usr/bin/env python3

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pnlib import rootkey
from pnlib.autonomy import levels as L
from pnlib.autonomy import (
    Oversight, OversightSession, OversightError,
    ROLE_OPERATOR, ROLE_APPROVER, ROLE_OBSERVER,
)
from pnlib.policy.model import Policy, Rule, EFFECT_ALLOW
from pnlib.policy.sign import sign_policy
from pnlib.policy import engine

_EXPECTED_ALLOWED = {
    0: {L.AC_OBSERVE, L.AC_DRY_RUN},
    1: {L.AC_OBSERVE, L.AC_DRY_RUN, L.AC_READ, L.AC_PROPOSE},
    2: {L.AC_OBSERVE, L.AC_DRY_RUN, L.AC_READ, L.AC_PROPOSE, L.AC_WRITE_REVERSIBLE},
    3: {L.AC_OBSERVE, L.AC_DRY_RUN, L.AC_READ, L.AC_PROPOSE, L.AC_WRITE_REVERSIBLE,
        L.AC_EXECUTE_REVERSIBLE},
    4: {L.AC_OBSERVE, L.AC_DRY_RUN, L.AC_READ, L.AC_PROPOSE, L.AC_WRITE_REVERSIBLE,
        L.AC_EXECUTE_REVERSIBLE, L.AC_WRITE_IRREVERSIBLE, L.AC_EXTERNAL_EFFECT},
    5: {L.AC_OBSERVE, L.AC_DRY_RUN, L.AC_READ, L.AC_PROPOSE, L.AC_WRITE_REVERSIBLE,
        L.AC_EXECUTE_REVERSIBLE, L.AC_WRITE_IRREVERSIBLE, L.AC_EXTERNAL_EFFECT, L.AC_PRIVILEGED},
}
_EXPECTED_CEREMONY = {0: L.CEREMONY_NONE, 1: L.CEREMONY_NONE, 2: L.CEREMONY_NOTIFY,
                      3: L.CEREMONY_APPROVE, 4: L.CEREMONY_APPROVE, 5: L.CEREMONY_ENVELOPE}

def test_level_gating_table_is_exact():
    for lvl in L.LEVELS:
        assert set(L.allowed_classes(lvl)) == _EXPECTED_ALLOWED[lvl], \
            f"L{lvl} allowed set mismatch: {set(L.allowed_classes(lvl))}"
        assert L.required_ceremony(lvl) == _EXPECTED_CEREMONY[lvl], f"L{lvl} ceremony mismatch"

    assert set(L.LEVELS) == {0, 1, 2, 3, 4, 5}

def test_levels_are_cumulative_and_monotone():
    for lvl in range(1, 6):
        lower = L.allowed_classes(lvl - 1)
        higher = L.allowed_classes(lvl)
        assert lower < higher, f"L{lvl} must strictly extend L{lvl-1}"

        assert set(higher) - set(lower) == set(L.added_classes(lvl))

    for lvl in range(1, 6):
        assert L.required_ceremony(lvl) >= L.required_ceremony(lvl - 1)

def test_permits_and_min_level_for():
    assert L.permits(0, L.AC_OBSERVE) and not L.permits(0, L.AC_READ)
    assert not L.permits(4, L.AC_PRIVILEGED) and L.permits(5, L.AC_PRIVILEGED)
    assert L.min_level_for(L.AC_PRIVILEGED) == 5
    assert L.min_level_for(L.AC_WRITE_REVERSIBLE) == 2
    assert L.min_level_for(L.AC_OBSERVE) == 0

    assert set(L.READ_ONLY_CLASSES) == set(L.allowed_classes(L.FAIL_SAFE_MAX_LEVEL))

def test_invalid_level_and_action_class_fail_closed():
    for bad in (-1, 6, 2.0, True, "3", None):
        try:
            L.require_level(bad)
            assert False, f"require_level accepted {bad!r}"
        except L.AutonomyError:
            pass
    try:
        L.permits(3, "teleport")
        assert False, "unknown action class accepted"
    except L.AutonomyError:
        pass

def test_clamp_level_only_lowers_with_audit_notes():
    eff, notes = L.clamp_level(5, [("owner-cap", 3), ("rule-max-level", None)])
    assert eff == 3 and any("owner-cap" in n for n in notes)

    eff2, notes2 = L.clamp_level(2, [("owner-cap", 5)])
    assert eff2 == 2 and notes2 == ()

    eff3, notes3 = L.clamp_level(5, [("owner-cap", 4), ("rule-max-level", 2)])
    assert eff3 == 2 and len(notes3) >= 1

def test_strictest_ceremony():
    assert L.strictest_ceremony(L.CEREMONY_NONE, L.CEREMONY_APPROVE) == L.CEREMONY_APPROVE
    assert L.strictest_ceremony(L.CEREMONY_NOTIFY, L.CEREMONY_ENVELOPE) == L.CEREMONY_ENVELOPE
    assert L.strictest_ceremony(L.CEREMONY_NONE, L.CEREMONY_NONE) == L.CEREMONY_NONE

def _owner():
    return rootkey.generate_owner_keypair_offbox()

def _signed(priv, pub, *, version=1, prev=None, effective_time=0, rules=(), owner_caps=None):
    pol = Policy(version=version, effective_time=effective_time, rules=tuple(rules),
                 owner_caps=owner_caps or {}, prev_version_hash=prev)
    return sign_policy(owner_priv=priv, owner_pub=pub, policy=pol)

def test_owner_cap_clamps_requested_level_with_audit_note():
    priv, pub = _owner()

    sp = _signed(priv, pub,
                 rules=[Rule(principal="agent-x", task_type="deploy", resource="*",
                             effect=EFFECT_ALLOW)],
                 owner_caps={"agent-x": L.L3_SUPERVISED})
    job = {"task_type": "deploy", "resource": "svc"}
    d = engine.decide(("agent-x",), job, L.L5_FULL_AUTO, sp, owner_pubkey=pub, now=0)
    assert d.permitted, d.reason
    assert d.effective_level == L.L3_SUPERVISED, f"expected clamp to L3, got L{d.effective_level}"
    assert any("owner-cap" in n for n in d.audit_notes), d.audit_notes

    assert d.ceremony == L.required_ceremony(L.L3_SUPERVISED) == L.CEREMONY_APPROVE

    d2 = engine.decide(("agent-x",), job, L.L2_ASSIST, sp, owner_pubkey=pub, now=0)
    assert d2.effective_level == L.L2_ASSIST and not any("owner-cap" in n for n in d2.audit_notes)

def test_oversight_no_self_approve_via_higher_stage():

    ov = Oversight(job_id="j-h4", operator="agentX", approver="humanA", observer="obs")
    sess = OversightSession(ov)

    try:
        sess.approve("wire_funds", approver="agentX", stage=L.CEREMONY_ENVELOPE)
        assert False, "operator self-approve at ENVELOPE stage must be rejected"
    except OversightError:
        pass

    sess.approve("wire_funds", approver="humanA", stage=L.CEREMONY_ENVELOPE)
    assert not sess.is_satisfied("wire_funds", L.CEREMONY_APPROVE), \
        "an ENVELOPE record must not satisfy an APPROVE gate"

    sess.approve("wire_funds", approver="humanA", stage=L.CEREMONY_APPROVE)
    assert sess.is_satisfied("wire_funds", L.CEREMONY_APPROVE)

def test_oversight_roles_and_separation_of_duties():
    ov = Oversight(job_id="job-1", operator="op", approver="boss", observer="watch")
    assert ov.role_of("op") == ROLE_OPERATOR
    assert ov.role_of("boss") == ROLE_APPROVER
    assert ov.role_of("watch") == ROLE_OBSERVER
    assert ov.role_of("stranger") is None
    assert ov.separation_ok()

    bad = Oversight(job_id="job-2", operator="op", approver="op", observer="watch")
    assert not bad.separation_ok()
    ok, why = bad.ceremony_satisfiable(L.CEREMONY_APPROVE)
    assert not ok and "separation" in why.lower()

def test_ceremony_satisfiability():
    full = Oversight(job_id="j", operator="op", approver="boss", observer="watch")
    assert full.ceremony_satisfiable(L.CEREMONY_NONE)[0]
    assert full.ceremony_satisfiable(L.CEREMONY_NOTIFY)[0]
    assert full.ceremony_satisfiable(L.CEREMONY_APPROVE)[0]
    assert full.ceremony_satisfiable(L.CEREMONY_ENVELOPE)[0]

    no_appr = Oversight(job_id="j", operator="op", observer="watch")
    assert not no_appr.ceremony_satisfiable(L.CEREMONY_APPROVE)[0]
    no_obs = Oversight(job_id="j", operator="op", approver="boss")
    assert not no_obs.ceremony_satisfiable(L.CEREMONY_NOTIFY)[0]
    assert not no_obs.ceremony_satisfiable(L.CEREMONY_ENVELOPE)[0]

def test_oversight_session_records_approvals_and_enforces_rules():
    ov = Oversight(job_id="job-9", operator="op", approver="boss", observer="watch")
    sess = OversightSession(ov)
    assert not sess.is_satisfied("delete-x", L.CEREMONY_APPROVE)
    rec = sess.approve("delete-x", approver="boss", stage=L.CEREMONY_APPROVE)
    assert rec.approver == "boss" and rec.action == "delete-x"
    assert sess.is_satisfied("delete-x", L.CEREMONY_APPROVE)
    assert not sess.is_satisfied("other-action", L.CEREMONY_APPROVE)

    try:
        sess.approve("delete-y", approver="op", stage=L.CEREMONY_APPROVE)
        assert False, "operator approved its own action"
    except OversightError:
        pass

    try:
        sess.approve("delete-z", approver="rando", stage=L.CEREMONY_APPROVE)
        assert False, "unconfigured approver accepted"
    except OversightError:
        pass

    assert sess.is_satisfied("anything", L.CEREMONY_NONE)
    assert sess.is_satisfied("anything", L.CEREMONY_ENVELOPE)

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
