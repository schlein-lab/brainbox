#!/usr/bin/env python3

import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pnlib import db, authz
import importlib.util
import importlib.machinery

_pnd_path = os.path.join(ROOT, "tools", "pnd")
_spec = importlib.util.spec_from_loader(
    "pnd_oracle", importlib.machinery.SourceFileLoader("pnd_oracle", _pnd_path))
pnd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pnd)

@pytest.fixture()
def cx():
    d = tempfile.mkdtemp(prefix="authz_test_")
    conn = db.connect(os.path.join(d, "queue.db"))

    prev = getattr(pnd, "CX", None)
    pnd.CX = conn
    yield conn
    pnd.CX = prev
    conn.close()

PRINCIPALS = {"admin": 1000, "brain": 4001, "lan-guest": 4002, "adapter": 4003}

VALID_PARAMS = {
    "echo.test": {"msg": "hi"},
    "sleep.test": {"s": "1"},
    "commission.build": {"goal": "ship"},
    "review.post": {"msg": "hi"},
    "deploy.irreversible": {"target": "prod"},
    "summary.notify": {"msg": "hi"},
    "net.discover": {"cidr": "10.0.0.0/24"},
    "repro.room": {"room": "r1"},
    "filler": {"room": "r1"},
    "spreadsheet.calc": {"sheet": "s1"},
    "service.start": {"svc": "web"},
}

def _oracle(req):

    try:
        pnd.authorize_submit(req)
        return "allow", None
    except pnd.AuthzError as e:
        return "deny", str(e)

def _decide(cx, principal, task_type=None, cmd=None, **kw):
    caps = db.caps_for(cx, principal)
    return authz.decide(caps=caps, task_type=task_type, cmd=cmd, principal=principal, cx=cx, **kw)

def _verdict(d):
    return ("allow", None) if d.allowed else ("deny", d.reason)

def test_typed_task_types_match_pnd_oracle(cx):

    task_types = sorted(r["name"] for r in cx.execute("SELECT name FROM task_types").fetchall())
    assert task_types, "seed produced no task_types"
    checked = 0
    for principal, uid in PRINCIPALS.items():
        for tt in task_types:
            req = {"_peer_uid": uid, "task_type": tt, "params": VALID_PARAMS.get(tt, {})}
            want = _oracle(req)
            got = _verdict(_decide(cx, principal, task_type=tt))
            assert got == want, f"{principal} x {tt}: authz={got} pnd={want}"
            checked += 1
    assert checked == len(PRINCIPALS) * len(task_types)

def test_unknown_task_type_matches_pnd_oracle(cx):

    for principal, uid in PRINCIPALS.items():
        req = {"_peer_uid": uid, "task_type": "does.not.exist", "params": {}}
        want = _oracle(req)
        got = _verdict(_decide(cx, principal, task_type="does.not.exist"))
        assert got == want, f"{principal} x unknown: authz={got} pnd={want}"

def test_raw_command_matches_pnd_oracle(cx):

    for principal, uid in PRINCIPALS.items():
        req = {"_peer_uid": uid, "cmd": ["/bin/echo", "hi"]}
        want = _oracle(req)
        got = _verdict(_decide(cx, principal, cmd=["/bin/echo", "hi"]))
        assert got == want, f"{principal} x raw: authz={got} pnd={want}"

def test_neither_task_type_nor_cmd_matches_pnd_oracle(cx):

    for principal, uid in PRINCIPALS.items():
        req = {"_peer_uid": uid}
        want = _oracle(req)
        got = _verdict(_decide(cx, principal))
        assert got == want, f"{principal} x neither: authz={got} pnd={want}"

def test_allow_and_deny_examples_are_actually_exercised(cx):

    assert _decide(cx, "admin", cmd=["/bin/echo", "x"]).allowed is True
    d = _decide(cx, "lan-guest", cmd=["/bin/echo", "x"])
    assert d.allowed is False
    assert d.reason == "principal 'lan-guest' may not submit raw commands (needs capability task.raw)"

    assert _decide(cx, "brain", task_type="echo.test").allowed is True
    d = _decide(cx, "brain", task_type="commission.build")
    assert d.allowed is False
    assert d.reason == "principal 'brain' may not run task_type 'commission.build'"

    d = _decide(cx, "admin", task_type="nope.nope")
    assert d.allowed is False and d.reason == "unknown task_type 'nope.nope'"

def test_flags_default_off(monkeypatch):
    for k in ("PN_CAP_ENFORCE", "PN_POLICY_MODE", "PN_AUTHZ_LEDGER"):
        monkeypatch.delenv(k, raising=False)
    assert authz.cap_enforce_enabled() is False
    assert authz.policy_mode() == "off"
    assert authz.ledger_enabled() is False

def test_seams_are_untouched_when_flags_off(cx, monkeypatch):

    for k in ("PN_CAP_ENFORCE", "PN_POLICY_MODE", "PN_AUTHZ_LEDGER"):
        monkeypatch.delenv(k, raising=False)

    calls = {"captoken": 0, "policy": 0, "ledger": 0}

    def cap_spy(token, caps):
        calls["captoken"] += 1
        return True

    def policy_spy(ctx):
        calls["policy"] += 1
        return True

    def ledger_spy(rec):
        calls["ledger"] += 1

    for principal, tt, cmd in [("brain", "echo.test", None),
                               ("lan-guest", "net.discover", None),
                               ("admin", None, ["/bin/echo", "hi"]),
                               ("lan-guest", None, ["/bin/echo", "hi"])]:
        d = _decide(cx, principal, task_type=tt, cmd=cmd,
                    captoken="TOKEN", captoken_verify=cap_spy,
                    policy_fn=policy_spy, ledger_sink=ledger_spy)
        assert d.cap_enforced is False
        assert d.policy_mode == "off"
        assert d.ledger_recorded is False
        assert d.diagnostics == {}

    assert calls == {"captoken": 0, "policy": 0, "ledger": 0}, f"a seam fired with flags off: {calls}"

def test_verdict_identical_with_seams_wired_but_flags_off(cx, monkeypatch):

    for k in ("PN_CAP_ENFORCE", "PN_POLICY_MODE", "PN_AUTHZ_LEDGER"):
        monkeypatch.delenv(k, raising=False)
    task_types = sorted(r["name"] for r in cx.execute("SELECT name FROM task_types").fetchall())
    for principal in PRINCIPALS:
        for tt in task_types + [None]:
            bare = _verdict(_decide(cx, principal, task_type=tt,
                                    cmd=(None if tt else ["/bin/echo", "x"])))
            wired = _verdict(_decide(cx, principal, task_type=tt,
                                     cmd=(None if tt else ["/bin/echo", "x"]),
                                     captoken="T", captoken_verify=lambda *a: False,
                                     policy_fn=lambda c: (False, "policy would deny"),
                                     ledger_sink=lambda r: r))
            assert bare == wired, f"{principal} x {tt}: seams wired changed verdict {bare} -> {wired}"

def test_context_mapping_form_matches_kwargs(cx):

    caps = db.caps_for(cx, "brain")
    a = authz.decide({"caps": caps, "task_type": "echo.test", "principal": "brain", "cx": cx})
    b = authz.decide(caps=caps, task_type="echo.test", principal="brain", cx=cx)
    assert _verdict(a) == _verdict(b) == ("allow", None)

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
