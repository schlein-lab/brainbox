#!/usr/bin/env python3

import os
import sys
import importlib.util
import importlib.machinery

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
_path = os.path.join(ROOT, "tools", "pn-brokerd")
_spec = importlib.util.spec_from_loader("pn_brokerd", importlib.machinery.SourceFileLoader("pn_brokerd", _path))
bd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bd)

PASS = FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {label}")
    else:
        FAIL += 1; print(f"  FAIL {label}")

ok, out = bd._policy({"verb": "my-jobs", "_selector": "web:bob"})
check(ok and out["verb"] == "my-jobs", "user verb my-jobs admitted")
ok, out = bd._policy({"verb": "submit", "_selector": "web:bob", "task_type": "commission.build"})
check(ok and out.get("task_type") == "commission.build", "submit admitted + payload preserved")
for v in ("admin-pause", "admin-kill-user", "admin-set-prio", "admin-message", "admin-ensure-principal"):
    ok, out = bd._policy({"verb": v, "_selector": "web:bob"})
    check(not ok, f"admin verb {v} REJECTED via broker")
ok, out = bd._policy({"verb": "list", "_selector": "web:bob"})
check(not ok, "cross-tenant `list` (admin-only) REJECTED via broker")
ok, out = bd._policy({"verb": "frobnicate", "_selector": "web:bob"})
check(not ok, "unknown verb REJECTED")

for sel in ("web:bob", "web:owner", "web:a", "web:u_1.2-3"):
    ok, _ = bd._policy({"verb": "my-jobs", "_selector": sel})
    check(ok, f"valid selector {sel} admitted")
for sel in ("admin", "brain", "adapter", "web:", "web:Bob", "web:../x", "web:bob;rm", "device:1",
            "web:" + "a" * 100, "", None, 4003):
    ok, out = bd._policy({"verb": "my-jobs", "_selector": sel})
    check(not ok, f"bad selector {sel!r} REJECTED")

ok, out = bd._policy({"verb": "submit", "_selector": "web:bob", "_method": "device-channel"})
check(ok and out["_method"] == "web-session", "_method forced to web-session (portal's device-channel dropped)")

ok, out = bd._policy({"verb": "submit", "_selector": "web:bob",
                      "_ceiling_caps": ["task_type:*"], "_peer_uid": 1000})
check(ok and "_ceiling_caps" not in out and "_peer_uid" not in out,
      "_ceiling_caps + _peer_uid stripped (portal can't widen or spoof peer)")

ok, out = bd._policy(["not", "a", "dict"])
check(not ok, "non-dict request REJECTED")

check(not any(v.startswith("admin") for v in bd.ALLOWED_VERBS), "ALLOWED_VERBS has no admin-* verb")

print(f"\n=== brokerd_policy_test: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
