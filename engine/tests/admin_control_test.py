#!/usr/bin/env python3

from __future__ import annotations
import os, sys, json, tempfile
import importlib.util, importlib.machinery

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from pnlib import db
from pnlib.profile import ResourceProfile

PASS = FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {label}")
    else:
        FAIL += 1; print(f"  FAIL {label}")

def section(name):
    print(f"\n== {name} ==")

_pnd_path = os.path.join(ROOT, "tools", "pnd")
_spec = importlib.util.spec_from_loader(
    "pnd_admin", importlib.machinery.SourceFileLoader("pnd_admin", _pnd_path))
pnd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pnd)

ADMIN_UID, GUEST_UID = 1000, 4002
PROF = ResourceProfile(mem=200, timeout_s=100)

def fresh_db():
    tmp = tempfile.mkdtemp()
    cx = db.connect(os.path.join(tmp, "queue.db"))
    pnd.CX = cx
    return cx

section("db: v12 migration — principal_policy table present after connect()")
cx = fresh_db()
tables = {r["name"] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}
check("principal_policy" in tables, "principal_policy table created by connect()/migrate")
pcols = {r["name"] for r in cx.execute("PRAGMA table_info(principal_policy)")}
check({"principal", "prio_bias", "quota_bytes", "quota_inodes", "updated_at", "updated_by"} <= pcols,
      "principal_policy has the v12 columns")

section("db: get_meta / set_meta round-trip + admission_paused persistence")
check(db.get_meta(cx, "nope") is None, "absent key -> None")
check(db.get_meta(cx, "nope", "def") == "def", "absent key -> default")
db.set_meta(cx, "admission_paused", "1")
check(db.get_meta(cx, "admission_paused") == "1", "set_meta then get_meta round-trip")
db.set_meta(cx, "admission_paused", "0")
check(db.get_meta(cx, "admission_paused") == "0", "set_meta upserts (overwrites) an existing key")

db.set_meta(cx, "admission_paused", "1")
seeded = (db.get_meta(cx, "admission_paused", "0") == "1")
check(seeded is True, "durable admission_paused='1' re-seeds the pause latch (survives restart)")
db.set_meta(cx, "admission_paused", "0")

section("db: get_policy / set_policy — defaults, round-trip, PARTIAL update")
default = db.get_policy(cx, "someone-new")
check(default["prio_bias"] == 0 and default["quota_bytes"] is None and default["quota_inodes"] is None,
      "absent policy -> prio_bias 0, no quota (the default = untouched behaviour)")
p = db.set_policy(cx, "lan-guest", prio_bias=-25, updated_by="admin")
check(p["prio_bias"] == -25 and p["updated_by"] == "admin" and p["updated_at"] is not None,
      "set_policy writes prio_bias + stamps updated_by/updated_at")

p2 = db.set_policy(cx, "lan-guest", quota_bytes=1_000_000, quota_inodes=500, updated_by="admin")
check(p2["quota_bytes"] == 1_000_000 and p2["quota_inodes"] == 500 and p2["prio_bias"] == -25,
      "set_policy is a PARTIAL update (quota write keeps the earlier prio_bias)")
p3 = db.set_policy(cx, "lan-guest", prio_bias=10, updated_by="admin")
check(p3["prio_bias"] == 10 and p3["quota_bytes"] == 1_000_000,
      "and a later prio write keeps the earlier quota")

section("submit: per-user PRIORITY BIAS applied to the stored prio (LOWER = sooner)")
cx = fresh_db()

def submit_admin_raw():

    r = pnd.handle({"verb": "submit", "_peer_uid": ADMIN_UID, "class": "compute",
                    "cmd": ["/bin/true"]})
    assert r.get("ok"), r
    return r["id"]

KLASSE = 100
jid = submit_admin_raw()
BASIS = db.get(cx, jid, scope_all=True)["prio"]
FS = BASIS - KLASSE
check(0 <= BASIS <= 300, "ohne Regel: Vorrang liegt im gueltigen Bereich (Ausgangswert %d, Fairshare %+d)" % (BASIS, FS))

def erwartet(bias):

    return max(0, min(300, max(0, min(300, KLASSE + bias)) + FS))

db.set_policy(cx, "admin", prio_bias=-30, updated_by="admin")
jid = submit_admin_raw()
row = db.get(cx, jid, scope_all=True)
check(row["prio"] == erwartet(-30), "prio_bias -30 -> Vorrang um 30 gesenkt (%d)" % erwartet(-30))
check(ResourceProfile.from_json(row["profile"]).prio == erwartet(-30),
      "derselbe Wert steht auch im Profil-JSON")

db.set_policy(cx, "admin", prio_bias=-500, updated_by="admin")
check(db.get(cx, submit_admin_raw(), scope_all=True)["prio"] == 0, "Zuschlag klammert unten bei 0")

db.set_policy(cx, "admin", prio_bias=400, updated_by="admin")
check(db.get(cx, submit_admin_raw(), scope_all=True)["prio"] == erwartet(400),
      "Zuschlag klammert oben bei 300 (%d nach Fairshare)" % erwartet(400))

section("verbs: view:all gate (defense-in-depth) refuses a non-admin caller")
cx = fresh_db()
for v in ("admin-pause", "admin-resume", "admin-queue-status", "admin-set-prio",
          "admin-set-quota", "admin-kill-user", "admin-message"):
    r = pnd.handle({"verb": v, "_peer_uid": GUEST_UID})
    check(r == {"ok": False, "error": "requires admin (view:all)"}, f"{v}: non-admin refused")

section("verbs: admin-pause / admin-resume / admin-queue-status")
cx = fresh_db()
pnd._ADMISSION_PAUSED = False
r = pnd.handle({"verb": "admin-pause", "_peer_uid": ADMIN_UID})
check(r.get("ok") and r["paused"] is True, "admin-pause returns paused=True")
check(pnd._ADMISSION_PAUSED is True, "admin-pause flips the live module-global (takes effect this tick)")
check(db.get_meta(cx, "admission_paused") == "1", "admin-pause persists the durable meta flag")
check(db.get_meta(cx, "admission_paused_by") == "admin", "admin-pause records paused_by=admin")

st = pnd.handle({"verb": "admin-queue-status", "_peer_uid": ADMIN_UID})
check(st.get("ok") and st["paused"] is True and st["paused_by"] == "admin"
      and isinstance(st["paused_at"], float) and isinstance(st["counts"], dict),
      "admin-queue-status reports {paused, paused_by, paused_at, counts}")

r = pnd.handle({"verb": "admin-resume", "_peer_uid": ADMIN_UID})
check(r.get("ok") and r["paused"] is False and pnd._ADMISSION_PAUSED is False,
      "admin-resume clears the latch (live + returns paused=False)")
check(db.get_meta(cx, "admission_paused") == "0", "admin-resume persists admission_paused=0")

section("verbs: admin-set-prio / admin-set-quota (persist onto principal_policy)")
cx = fresh_db()
r = pnd.handle({"verb": "admin-set-prio", "_peer_uid": ADMIN_UID,
                "target_principal": "lan-guest", "prio_bias": -15})
check(r.get("ok") and r["prio_bias"] == -15, "admin-set-prio returns the written bias")
check(db.get_policy(cx, "lan-guest")["prio_bias"] == -15, "admin-set-prio persisted prio_bias")
r = pnd.handle({"verb": "admin-set-quota", "_peer_uid": ADMIN_UID,
                "target_principal": "lan-guest", "quota_bytes": 2_000_000})
check(r.get("ok") and r["quota_bytes"] == 2_000_000, "admin-set-quota returns the written quota")
pol = db.get_policy(cx, "lan-guest")
check(pol["quota_bytes"] == 2_000_000 and pol["prio_bias"] == -15,
      "admin-set-quota did NOT clobber the earlier prio_bias (partial update through the verb)")

check(pnd.handle({"verb": "admin-set-prio", "_peer_uid": ADMIN_UID})["error"].startswith(
      "admin-set-prio requires"), "admin-set-prio without principal -> error")
check("must be an integer" in pnd.handle(
      {"verb": "admin-set-prio", "_peer_uid": ADMIN_UID, "target_principal": "x", "prio_bias": "hi"})["error"],
      "admin-set-prio non-int bias -> error")
check("requires quota" in pnd.handle(
      {"verb": "admin-set-quota", "_peer_uid": ADMIN_UID, "target_principal": "x"})["error"],
      "admin-set-quota with no quota field -> error")

section("verbs: admin-kill-user cancels EXACTLY that principal's non-terminal jobs")
cx = fresh_db()

stopped = []
_orig_stop = pnd.slc.stop_unit
pnd.slc.stop_unit = lambda jid: stopped.append(jid)
try:
    def mk(principal, tag, deps=None):
        return db.submit(cx, ["/bin/true"], "/tmp", {}, PROF.to_json(), 100, 200, tag,
                         principal=principal, submitter_principal=principal, deps=deps)
    a_q = mk("admin", "A-queued")
    a_b = mk("admin", "A-blocked", deps=[a_q])
    a_r = mk("admin", "A-running")
    db.mark_running(cx, a_r, "pn-job-%d.service" % a_r, "/dev/null")
    g_q = mk("lan-guest", "G-queued")
    a_done = mk("admin", "A-done")
    db.mark_terminal(cx, a_done, "done", 0)

    live_ids = {j["id"] for j in db.nonterminal_for_principal(cx, "admin")}
    check(live_ids == {a_q, a_b, a_r},
          "nonterminal_for_principal(admin) = the 3 non-terminal admin jobs (excludes done + guest)")

    r = pnd.handle({"verb": "admin-kill-user", "_peer_uid": ADMIN_UID, "target_principal": "admin"})
    check(r.get("ok") and r["count"] == 3 and sorted(r["cancelled"]) == sorted([a_q, a_b, a_r]),
          "admin-kill-user returns {cancelled:[3 ids], count:3}")
    check(all(db.get(cx, j, scope_all=True)["state"] == "cancelled" for j in (a_q, a_b, a_r)),
          "all 3 admin jobs are now cancelled")
    check(stopped == [a_r], "the RUNNING job went through slc.stop_unit (cancel mechanics reused)")
    check(db.get(cx, g_q, scope_all=True)["state"] == "queued",
          "the OTHER tenant's job is untouched (still queued)")
    check(db.get(cx, a_done, scope_all=True)["state"] == "done",
          "the already-terminal admin job is left alone (not re-cancelled)")

    r0 = pnd.handle({"verb": "admin-kill-user", "_peer_uid": ADMIN_UID, "target_principal": "lan-guest"})
    check(r0.get("ok") and r0["count"] == 1 and r0["cancelled"] == [g_q],
          "admin-kill-user(lan-guest) cancels only that tenant's one job")
finally:
    pnd.slc.stop_unit = _orig_stop

section("verbs: admin-message publishes a notify event on the TARGET's user topic")
cx = fresh_db()
r = pnd.handle({"verb": "admin-message", "_peer_uid": ADMIN_UID, "target": "lan-guest",
                "text": "box paused for maintenance", "severity": "warn"})
check(r.get("ok") and r["target"] == "lan-guest" and r["severity"] == "warn",
      "admin-message returns ok + target + severity")
rows = list(cx.execute(
    "SELECT job_id,kind,topic,data FROM job_events WHERE topic=? ORDER BY id",
    ("user/lan-guest",)))
check(len(rows) == 1 and rows[0]["kind"] == "notify",
      "exactly one `notify` event landed on user/lan-guest")
payload = json.loads(rows[0]["data"])
check(payload.get("kind") == "admin-message" and payload.get("text") == "box paused for maintenance"
      and payload.get("severity") == "warn" and payload.get("from") == "admin",
      "payload = {kind:admin-message, text, severity, from:caller}")
check(rows[0]["job_id"] == 0, "admin-message uses the job_id=0 sentinel (not tied to any job)")

other = list(cx.execute("SELECT 1 FROM job_events WHERE topic='user/admin'"))
check(other == [], "admin-message did NOT publish on the sender's own user/admin topic")

pnd.handle({"verb": "admin-message", "_peer_uid": ADMIN_UID, "target": "lan-guest", "text": "hi"})
last = list(cx.execute("SELECT data FROM job_events WHERE topic='user/lan-guest' ORDER BY id DESC LIMIT 1"))
check(json.loads(last[0]["data"]).get("severity") == "info", "severity defaults to 'info' when omitted")
check("requires 'target' and 'text'" in pnd.handle(
      {"verb": "admin-message", "_peer_uid": ADMIN_UID, "target": "lan-guest"})["error"],
      "admin-message without text -> error")

print(f"\n=== admin_control_test: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
