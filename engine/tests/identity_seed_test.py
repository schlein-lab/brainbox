#!/usr/bin/env python3

from __future__ import annotations
import os, sys, tempfile
import importlib.util, importlib.machinery

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from pnlib import db

_pnd_path = os.path.join(ROOT, "tools", "pnd")
_spec = importlib.util.spec_from_loader("pnd_ident", importlib.machinery.SourceFileLoader("pnd_ident", _pnd_path))
pnd = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(pnd)

ADMIN_UID, GUEST_UID = 1000, 4002
PASS = FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {label}")
    else:
        FAIL += 1; print(f"  FAIL {label}")

cx = db.connect(os.path.join(tempfile.mkdtemp(), "queue.db"))
pnd.CX = cx

check("task_type:commission.build" in db.caps_for(cx, "adapter"),
      "adapter relay allowlist includes task_type:commission.build")
check("task.raw" not in db.caps_for(cx, "adapter") and "task_type:*" not in db.caps_for(cx, "adapter"),
      "adapter still holds NO wildcard (raw exec stays impossible for tenants)")

r = pnd.handle({"verb": "admin-ensure-principal", "_peer_uid": ADMIN_UID, "name": "web:bob"})
check(r.get("ok") and r.get("principal") == "web:bob", "admin-ensure-principal web:bob -> ok")
check(db.principal_for_uid is not None and any(
    row["name"] == "web:bob" for row in cx.execute("SELECT name FROM principals")),
    "principal web:bob exists")

pnd.handle({"verb": "admin-ensure-principal", "_peer_uid": ADMIN_UID, "name": "web:bob"})
n = cx.execute("SELECT COUNT(*) c FROM principals WHERE name='web:bob'").fetchone()["c"]
check(n == 1, "admin-ensure-principal idempotent (one row)")

for v, extra in (("admin-ensure-principal", {"name": "web:evil"}),
                 ("admin-grant", {"target_principal": "web:bob", "cap": "task_type:echo.test"}),
                 ("admin-bind-identity", {"method": "web-session", "selector": "web:bob", "target_principal": "web:bob"})):
    rr = pnd.handle({"verb": v, "_peer_uid": GUEST_UID, **extra})
    check(not rr.get("ok") and "view:all" in rr.get("error", ""), f"{v} REFUSED for non-admin")
check(not any(row["name"] == "web:evil" for row in cx.execute("SELECT name FROM principals")),
      "non-admin seeding had NO effect (web:evil not created)")

r = pnd.handle({"verb": "admin-grant", "_peer_uid": ADMIN_UID, "target_principal": "web:bob", "cap": "task_type:commission.build"})
check(r.get("ok"), "admin-grant task_type:commission.build -> ok")
check("task_type:commission.build" in db.caps_for(cx, "web:bob"), "web:bob now holds commission.build")
for bad in ("task.raw", "task_type:*", "view:all"):
    rr = pnd.handle({"verb": "admin-grant", "_peer_uid": ADMIN_UID, "target_principal": "web:bob", "cap": bad})
    check(not rr.get("ok") and "wildcard" in rr.get("error", ""), f"admin-grant {bad} REFUSED (wildcard)")
check(not (db.WILDCARD_CAPS & db.caps_for(cx, "web:bob")), "web:bob holds NO wildcard cap")

pnd.handle({"verb": "admin-grant", "_peer_uid": ADMIN_UID, "target_principal": "web:bob", "cap": "task_type:commission.build"})
gc = cx.execute("SELECT COUNT(*) c FROM grants WHERE principal='web:bob' AND cap='task_type:commission.build'").fetchone()["c"]
check(gc == 1, "admin-grant idempotent (one grant row)")

r = pnd.handle({"verb": "admin-bind-identity", "_peer_uid": ADMIN_UID,
                "method": "web-session", "selector": "web:bob", "target_principal": "web:bob", "verified": 1})
check(r.get("ok"), "admin-bind-identity web-session:web:bob -> web:bob ok")
resolved, verified = db.resolve_identity(cx, "web-session", "web:bob")
check(resolved == "web:bob", "resolve_identity(web-session, web:bob) -> web:bob")

eff = db.caps_for(cx, "adapter") & db.caps_for(cx, "web:bob")
check(eff == {"task_type:commission.build"},
      "effective (adapter ∩ web:bob) = ONLY commission.build (no raw, no admin)")

check("requires 'name'" in pnd.handle({"verb": "admin-ensure-principal", "_peer_uid": ADMIN_UID}).get("error", ""),
      "admin-ensure-principal without name -> error")
check("requires" in pnd.handle({"verb": "admin-grant", "_peer_uid": ADMIN_UID, "target_principal": "x"}).get("error", ""),
      "admin-grant without cap -> error")

print(f"\n=== identity_seed_test: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
