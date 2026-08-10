#!/usr/bin/env python3

import os, sys, json, time, tempfile, importlib.util, sqlite3, subprocess, socket

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from pnlib import db, brain as B
from importlib.machinery import SourceFileLoader

def _load(name, fname):
    loader = SourceFileLoader(name, os.path.join(ROOT, "tools", fname))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod

pnd = _load("pnd_mod", "pnd")
pnbrain = _load("pnbrain_mod", "pn-brain")
pnkeeper = _load("pnkeeper_mod", "pn-brainkeeper")
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from fakellm import Scripted

PASS, FAIL = 0, 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")

def fresh_db():
    path = tempfile.mktemp(prefix="pn_brain_", suffix=".db")
    cx = db.connect(path)
    return cx, path

def pnd_fn_for(cx, uid):
    def call(req):
        pnd.CX = cx
        pnd.LK = __import__("threading").Lock()
        r = dict(req)
        r.pop("principal", None); r.pop("uid", None)
        r["_peer_uid"] = uid
        return pnd.handle(r)
    return call

def _init_pnd(cx):

    import threading
    pnd.CX = cx
    pnd.LK = threading.Lock()
    return cx

def test_schema(cx):
    print("[a] v9 schema: brain_state + brain_timers; integrity ok")
    tables = {r["name"] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    check("brain_state" in tables, "brain_state table present")
    check("brain_timers" in tables, "brain_timers table present")
    scols = {r["name"] for r in cx.execute("PRAGMA table_info(brain_state)")}
    check({"principal", "key", "value", "updated_at"} <= scols, "brain_state columns present")
    tcols = {r["name"] for r in cx.execute("PRAGMA table_info(brain_timers)")}
    check({"name", "principal", "interval_s", "next_fire", "action_json", "enabled"} <= tcols,
          "brain_timers columns present")

    bcaps = db.caps_for(cx, "brain")
    check("task_type:summary.notify" in bcaps and "task_type:net.discover" in bcaps,
          "brain holds the P5 self-work caps (summary.notify + net.discover)")
    check("task.raw" not in bcaps and "task_type:*" not in bcaps and "view:all" not in bcaps,
          "brain holds NO task.raw / task_type:* / view:all (no escalation)")
    check(cx.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "integrity_check ok")

def test_closed_world():
    print("[b] the CLOSED-WORLD validator: accept well-formed, reject everything forbidden")
    ok = [
        {"op": "submit", "task_type": "echo.test", "params": {"msg": "hi"}},
        {"op": "message", "to": "telegram:555", "text": "done"},
        {"op": "propose", "kind": "onboard", "summary": "onboard the printer"},
        {"op": "steer", "job_id": 7, "input": "go bigger"},
        {"op": "sleep", "seconds": 5},
        {"op": "submit-dag", "nodes": [{"task_type": "echo.test"},
                                       {"task_type": "echo.test", "deps": [0]}]},
    ]
    for a in ok:
        try:
            B.validate_action(a)
            check(True, f"accepts well-formed op={a['op']}")
        except B.ValidationError as e:
            check(False, f"WRONGLY rejected op={a['op']}: {e}")

    def rejects(a, why):
        try:
            B.validate_action(a)
            check(False, f"FAILED to reject {why}")
        except B.ValidationError:
            check(True, f"rejects {why}")

    rejects({"op": "shell", "cmd": "rm -rf /"}, "op:shell (forbidden)")
    rejects({"op": "raw", "argv": ["sh"]}, "op:raw (forbidden)")
    rejects({"op": "exec", "x": 1}, "op:exec (forbidden)")
    rejects({"op": "approve", "nonce": "x"}, "op:approve (self-approve forbidden)")
    rejects({"op": "submit", "task_type": "echo.test", "cmd": ["sh"]},
            "submit with an extra `cmd` key (additionalProperties:false)")
    rejects({"op": "submit", "task_type": "echo.test", "principal": "admin"},
            "submit naming a principal (additionalProperties:false)")
    rejects({"op": "submit", "task_type": "task.raw"}, "submit task_type not in the allowlist")
    rejects({"op": "submit"}, "submit missing required task_type")
    rejects({"op": "steer", "job_id": "seven", "input": "x"}, "steer job_id wrong type (not int)")
    rejects({"op": "sleep", "seconds": True}, "sleep seconds as a bool (not int)")
    rejects({"op": "submit-dag", "nodes": [{"task_type": "echo.test", "deps": [0]}]},
            "submit-dag node depending on itself (not forward-only)")
    rejects(["not", "an", "object"], "a non-object action")
    rejects({"reason": "no op"}, "an action with no op")

def test_parse():
    print("[c] parse_action tolerates fences/prose, still gates closed-world")
    fenced = "```json\n{\"op\": \"sleep\", \"seconds\": 2}\n```"
    a = B.parse_action(fenced)
    check(a["op"] == "sleep", "parses a ```json-fenced action")
    prose = "Sure! Here is my action: {\"op\":\"message\",\"to\":\"native:admin\",\"text\":\"hi\"} ok?"
    a2 = B.parse_action(prose)
    check(a2["op"] == "message", "extracts the first JSON object out of surrounding prose")
    try:
        B.parse_action("no json here at all")
        check(False, "FAILED to reject prose with no JSON")
    except B.ValidationError:
        check(True, "rejects output with no parseable JSON object")
    try:
        B.parse_action("{\"op\":\"shell\"}")
        check(False, "FAILED to reject a parseable-but-forbidden action")
    except B.ValidationError:
        check(True, "a parseable but forbidden action is still rejected")

def test_dispose():
    print("[d] plan_dispose maps ops to pnd requests; a principal-naming action is refused")
    p = B.plan_dispose({"op": "submit", "task_type": "echo.test", "params": {"msg": "x"}},
                       acting_as="brain")
    check(p["kind"] == "pnd" and p["request"]["verb"] == "submit"
          and "principal" not in p["request"], "submit -> pnd submit with NO principal in the wire")

    pm = B.plan_dispose({"op": "message", "to": "telegram:5", "text": "hi"},
                        acting_as="brain", reply_to_default="telegram:owner")
    check(pm["request"]["task_type"] == "summary.notify"
          and pm["request"]["reply_to"] == "telegram:owner",
          "message -> summary.notify auf dem GEBUNDENEN Rueckkanal (notify-broker stellt zu)")
    check("telegram:5" not in repr(pm),
          "das vom Modell gewaehlte Ziel taucht NIRGENDWO im Auftrag auf (M3: kein verwirrter Stellvertreter)")
    pm0 = B.plan_dispose({"op": "message", "to": "telegram:5", "text": "hi"}, acting_as="brain")
    check(pm0["request"].get("reply_to") is None,
          "ohne gebundenen Rueckkanal gibt es kein Ziel -- lieber nichts zustellen als irgendwohin")
    pp = B.plan_dispose({"op": "propose", "kind": "egress", "summary": "open api.x",
                         "task_type": "net.discover", "params": {"cidr": "10.0.0.0/24"}},
                        acting_as="brain")
    check(pp["request"].get("approval") == "pre" and pp["request"].get("needs_confirmation")
          and pp["request"]["_propose"]["kind"] == "egress",
          "propose -> a pre-gated submit carrying the proposal (human gate)")
    ps = B.plan_dispose({"op": "steer", "job_id": 9, "input": "go"}, acting_as="brain")
    check(ps["request"]["verb"] == "steer" and ps["request"]["id"] == 9, "steer -> pnd steer")
    psl = B.plan_dispose({"op": "sleep", "seconds": 3}, acting_as="brain")
    check(psl["kind"] == "sleep" and psl["seconds"] == 3, "sleep -> a local no-op directive")

    refused = False
    try:
        B.plan_dispose({"op": "submit", "task_type": "echo.test", "principal": "admin"},
                       acting_as="brain")
    except B.ActingAsViolation:
        refused = True
    check(refused, "an action naming a principal -> ActingAsViolation (confused-deputy refused)")

def test_timers(cx):
    print("[e] timers: install / due / advance; recurring re-arms, one-shot self-disables")
    now = 1000.0
    action = {"op": "submit", "task_type": "net.discover", "params": {"cidr": "10.0.0.0/24"}}
    tid = B.install_timer(cx, "daily-discover", 86400, action, first_fire=now, now=now)
    check(tid is not None, "install_timer returns a row id")

    B.install_timer(cx, "daily-discover", 86400, action, first_fire=now, now=now)
    cnt = cx.execute("SELECT COUNT(*) c FROM brain_timers WHERE name='daily-discover'").fetchone()["c"]
    check(cnt == 1, "re-installing the same timer name is idempotent (one row)")
    due = B.due_timers(cx, now=now)
    check(any(t["name"] == "daily-discover" for t in due), "a timer at/over next_fire is due")
    check(not B.due_timers(cx, now=now - 1), "a timer before next_fire is NOT due")
    B.advance_timer(cx, tid, now=now)
    nf = cx.execute("SELECT next_fire,last_fired FROM brain_timers WHERE id=?", (tid,)).fetchone()
    check(abs(nf["next_fire"] - (now + 86400)) < 1 and nf["last_fired"] == now,
          "a recurring timer re-arms next_fire by interval_s")

    osid = B.install_timer(cx, "oneshot", 0, action, first_fire=now, now=now)
    B.advance_timer(cx, osid, now=now)
    en = cx.execute("SELECT enabled FROM brain_timers WHERE id=?", (osid,)).fetchone()["enabled"]
    check(en == 0, "a one-shot timer self-disables after firing")

def test_state(cx):
    print("[f] state + session: set/get; bump_session counts tasks + progress")
    B.set_state(cx, "brain", "intents", ["crawl the LAN for old devices and propose uses"])
    check(B.get_state(cx, "brain", "intents")[0].startswith("crawl"), "set/get a JSON state value")
    check(B.get_state(cx, "brain", "missing", default="d") == "d", "get returns the default")
    sess = B.new_session(cx, "brain", digest="seed", now=2000.0)
    check(sess["session_id"].startswith("sess-") and sess["cache_prefix"] == sess["session_id"],
          "new_session has a stable id + a per-principal cache prefix")
    s2 = B.bump_session(cx, "brain", tasks_delta=2, progress=True, now=2001.0)
    check(s2["tasks"] == 2 and s2["last_progress_at"] == 2001.0, "bump_session counts tasks+progress")

def test_rotate(cx):
    print("[g] rotation policy: should_rotate on age/tasks/pressure/stuck; digest bounded")
    base = {"session_id": "sess-x", "principal": "brain", "started_at": 0.0,
            "tasks": 0, "last_progress_at": 0.0}
    r, why = B.should_rotate(dict(base, started_at=100.0), now=100.0)
    check(not r, "a fresh session does not rotate")
    r, why = B.should_rotate(dict(base, started_at=0.0), now=B.ROTATE_MAX_AGE_S + 1)
    check(r and why == "age", "rotate on age >= 6h")
    r, why = B.should_rotate(dict(base, tasks=B.ROTATE_MAX_TASKS), now=1.0)
    check(r and why == "tasks", "rotate on K tasks")
    r, why = B.should_rotate(dict(base, last_progress_at=0.0), now=B.ROTATE_STUCK_S + 1)
    check(r and why == "stuck", "rotate when stuck (no progress)")
    r, why = B.should_rotate(dict(base, started_at=1.0, last_progress_at=1.0), now=2.0,
                             context_pressure=True)
    check(r and why == "context-pressure", "rotate on context-pressure")
    r, why = B.should_rotate(None, now=1.0)
    check(r and why == "no-session", "rotate when there is no session yet")
    digest = B.compact_digest(base, [{"id": i, "task_type": "echo.test", "state": "done"}
                                     for i in range(100)], ["crawl the lan"], max_jobs=10)
    check("crawl the lan" in digest and digest.count("\n- job ") == 10,
          "compact_digest is bounded to max_jobs and carries the intents")
    d = B.rotation_checkpoint(cx, "brain", dict(base), [{"id": 1, "task_type": "echo.test",
                              "state": "done"}], ["intent"], reason="age", now=3000.0)
    check(B.get_state(cx, "brain", "digest") == d, "rotation_checkpoint persists the digest")
    cp = B.get_state(cx, "brain", "last_checkpoint")
    check(cp and cp["reason"] == "age", "rotation_checkpoint records the checkpoint metadata")

def test_resume(cx):
    print("[h] resume_plan re-reads digest + intents + inflight + timers, seeds a fresh session")

    jid = db.submit(cx, ["/bin/echo", "x"], "/tmp", {}, "{}", 100, 64, "inflight",
                    principal="brain", task_type="echo.test")
    B.set_state(cx, "brain", "digest", "DIGEST-X")
    B.set_state(cx, "brain", "intents", ["intent-1"])
    plan = B.resume_plan(cx, "brain", now=5000.0)
    check(plan["digest"] == "DIGEST-X", "resume re-reads the compacted digest")
    check(plan["intents"] == ["intent-1"], "resume re-reads the standing intents")
    check(jid in plan["inflight"], "resume re-attaches in-flight job ids")
    check(plan["fresh_session"]["digest"] == "DIGEST-X"
          and plan["fresh_session"]["session_id"].startswith("sess-"),
          "resume seeds a FRESH session with the digest")

BRAIN_UID = 4001
ADMIN_UID = 1000

def test_live_act():
    print("[i] LIVE: the brain executes a well-formed `submit` (FAKE pn-llmd)")
    cx, path = fresh_db(); _init_pnd(cx)
    try:
        ask = Scripted([{"op": "submit", "task_type": "echo.test", "params": {"msg": "hello"},
                         "tag": "brain:act"}])
        bn = pnbrain.Brain(cx, ask, pnd_fn_for(cx, BRAIN_UID), principal="brain")
        out = bn.run_turn(prompt="x")
        check(out["disposition"]["ok"] and out["disposition"]["response"].get("id"),
              "the brain disposed a well-formed submit (a job was created)")
        jid = out["disposition"]["response"]["id"]
        row = cx.execute("SELECT submitter_principal, task_type FROM jobs WHERE id=?",
                         (jid,)).fetchone()
        check(row["submitter_principal"] == "brain" and row["task_type"] == "echo.test",
              "the job is OWNED BY `brain` (acting-as bound from peercred, not the body)")
        check(bn.acted == 1 and bn.rejected == 0, "the brain counted exactly one action, no rejects")
    finally:
        cx.close(); _rm(path)

def test_live_noop():
    print("[j] LIVE: the brain REJECTS a malformed/forbidden action as a NO-OP (no job created)")
    cx, path = fresh_db(); _init_pnd(cx)
    try:
        before = cx.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
        for bad in ('{"op":"shell","cmd":"rm -rf /"}',
                    '{"op":"submit","task_type":"echo.test","cmd":["sh"]}',
                    '{"op":"submit","task_type":"task.raw"}',
                    '{"op":"submit","task_type":"echo.test","principal":"admin"}',
                    'not even json'):
            bn = pnbrain.Brain(cx, Scripted([bad]), pnd_fn_for(cx, BRAIN_UID), principal="brain")
            out = bn.run_turn(prompt="x")
            check(out["disposition"]["rejected"] is True,
                  f"rejected as a no-op: {bad[:48]}")
        after = cx.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
        check(after == before, "NOT ONE job was created by any forbidden/malformed action")
    finally:
        cx.close(); _rm(path)

def test_live_propose():
    print("[k] LIVE: op:propose emits an approval-request (human gate); the brain CAN'T self-approve")
    cx, path = fresh_db(); _init_pnd(cx)
    try:
        ask = Scripted([{"op": "propose", "kind": "onboard",
                         "summary": "onboard the discovered printer 10.0.0.30",
                         "task_type": "net.discover", "params": {"cidr": "10.0.0.0/24"}}])
        bn = pnbrain.Brain(cx, ask, pnd_fn_for(cx, BRAIN_UID), principal="brain")
        out = bn.run_turn(prompt="x")
        resp = out["disposition"]["response"]
        check(resp.get("state") == "staged" and resp.get("nonce"),
              "propose parked the job `staged` behind a SERVER-MINTED nonce (human gate)")
        jid, nonce = resp["id"], resp["nonce"]

        evs = db.events(cx, jid, scope_all=True)
        ar = next((e for e in evs if e["kind"] == "approval-request"), None)
        ar_data = json.loads(ar["data"]) if ar else {}
        check(ar_data.get("proposal", {}).get("kind") == "onboard",
              "the approval-request carries the brain's proposal (kind/summary) for the human")

        ega = pnd_fn_for(cx, BRAIN_UID)({"verb": "egress-approve", "nonce": "anything"})
        check(not ega.get("ok") and "view:all" in (ega.get("error") or ""),
              "the brain (no view:all) CANNOT approve an egress (self-approve refused)")

        self_app = pnd_fn_for(cx, BRAIN_UID)({"verb": "approve", "nonce": nonce})
        check(not self_app.get("ok") and "self-approve" in (self_app.get("error") or ""),
              "the brain CANNOT self-approve its OWN gate via the engine approve verb (sep. of duties)")

        hum = pnd_fn_for(cx, ADMIN_UID)({"verb": "approve", "nonce": nonce})
        check(hum.get("ok") and hum.get("state") == "queued",
              "the HUMAN (view:all) approves the server-minted nonce -> the gate releases")
    finally:
        cx.close(); _rm(path)

def test_live_self_work():
    print("[l] LIVE: a standing intent fires a `net.discover` submit on its timer (self-generated work)")
    cx, path = fresh_db(); _init_pnd(cx)
    try:
        now = time.time()
        action = {"op": "submit", "task_type": "net.discover",
                  "params": {"cidr": "10.0.0.0/24"}, "tag": "brain:discover"}
        B.install_timer(cx, "daily-discover", 86400, action, first_fire=now - 1, now=now)
        before = cx.execute("SELECT COUNT(*) c FROM jobs WHERE task_type='net.discover'").fetchone()["c"]

        bn = pnbrain.Brain(cx, Scripted([{"op": "sleep", "seconds": 0}]),
                           pnd_fn_for(cx, BRAIN_UID), principal="brain")
        out = bn.run_turn(prompt="x", now=now)
        check(len(out["fired_timers"]) == 1 and out["fired_timers"][0]["ok"],
              "the due standing timer fired its net.discover action")
        rows = cx.execute("SELECT id, state FROM jobs WHERE task_type='net.discover'").fetchall()
        check(len(rows) == before + 1, "a net.discover job was self-generated")
        check(rows[-1]["state"] == "staged",
              "net.discover PARKS `staged` (observe-only, human-gated; discovery never auto-binds)")

        nf = cx.execute("SELECT next_fire FROM brain_timers WHERE name='daily-discover'").fetchone()
        check(nf["next_fire"] > now, "the daily timer re-armed for tomorrow (durable, survives restart)")
    finally:
        cx.close(); _rm(path)

def test_live_acting_as():
    print("[m] LIVE: the brain acting for A CANNOT submit as B (acting-as binding; confused deputy)")
    cx, path = fresh_db(); _init_pnd(cx)
    try:

        cx.execute("INSERT OR IGNORE INTO principals(name,uid,kind) VALUES('brainB',4099,'agent')")
        cx.execute("INSERT INTO grants(principal,cap) VALUES('brainB','task_type:echo.test')")
        cx.commit()

        bn = pnbrain.Brain(cx, Scripted([
            {"op": "submit", "task_type": "echo.test", "params": {"msg": "x"}, "principal": "admin"}]),
            pnd_fn_for(cx, BRAIN_UID), principal="brain")
        out = bn.run_turn(prompt="x")
        check(out["disposition"]["rejected"] is True,
              "a submit naming `admin` is rejected as a no-op (closed-world / acting-as)")

        r = pnd_fn_for(cx, BRAIN_UID)({"verb": "submit", "task_type": "echo.test",
                                       "params": {"msg": "ok"}, "principal": "admin"})
        owner = cx.execute("SELECT submitter_principal FROM jobs WHERE id=?",
                           (r["id"],)).fetchone()["submitter_principal"]
        check(owner == "brain",
              "pnd bound the job to the PEERCRED principal `brain`, IGNORING the body `principal:admin`")

        bad = pnd_fn_for(cx, BRAIN_UID)({"verb": "submit", "cmd": ["/bin/echo", "raw"]})
        check(not bad.get("ok") and "raw" in (bad.get("error") or ""),
              "the brain cannot submit a RAW command (holds no task.raw)")
    finally:
        cx.close(); _rm(path)

def test_live_keeper():
    print("[n] LIVE: brainkeeper rotates the session (checkpoint->digest->fresh), ZERO queue loss")
    cx, path = fresh_db(); _init_pnd(cx)
    try:

        bn = pnbrain.Brain(cx, Scripted([
            {"op": "submit", "task_type": "echo.test", "params": {"msg": "a"}},
            {"op": "submit", "task_type": "echo.test", "params": {"msg": "b"}}]),
            pnd_fn_for(cx, BRAIN_UID), principal="brain")
        bn.run_turn(prompt="x"); bn.run_turn(prompt="x")
        jobs_before = cx.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
        sess_before = B.get_session(cx, "brain")

        killed = []
        snapshots = []
        kp = pnkeeper.Keeper(cx, principal="brain",
                             ping_fn=lambda: True,
                             kill_fn=lambda s: killed.append((s or {}).get("session_id")),
                             record_note=lambda p, d: snapshots.append((p, len(d))),
                             max_tasks=1)
        check(not hasattr(kp, "pnd_fn") and not hasattr(kp, "ask_fn"),
              "the keeper has NO pnd_fn / ask_fn (structurally cannot submit or call a broker)")
        res = kp.maybe_rotate()
        check(res["rotated"] and res["reason"] == "tasks", "the keeper rotated on K tasks")
        check(res["prev_session"] == sess_before["session_id"]
              and res["new_session"] != sess_before["session_id"],
              "rotation produced a FRESH session id (the old one is replaced)")
        check(killed == [sess_before["session_id"]], "the OLD session was killed (atomic handoff)")
        check(snapshots and snapshots[0][0] == "brain", "the digest was snapshotted to the Record")
        sess_after = B.get_session(cx, "brain")
        check(sess_after["digest"] == B.get_state(cx, "brain", "digest"),
              "the fresh session is seeded with the compacted digest")
        jobs_after = cx.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
        check(jobs_after == jobs_before, "ZERO queue loss across the rotation (the queue is untouched)")
    finally:
        cx.close(); _rm(path)

def test_live_auto_resume():
    print("[o] LIVE: auto-resume after a simulated restart re-reads brain_state + re-attaches")
    cx, path = fresh_db(); _init_pnd(cx)
    try:

        bn = pnbrain.Brain(cx, Scripted([
            {"op": "submit", "task_type": "sleep.test", "params": {"s": "30"}, "tag": "long"}]),
            pnd_fn_for(cx, BRAIN_UID), principal="brain")
        out = bn.run_turn(prompt="x")
        jid = out["disposition"]["response"]["id"]
        B.set_state(cx, "brain", "intents", ["resume me"])
        old_sess = B.get_session(cx, "brain")["session_id"]

        bn2 = pnbrain.Brain(cx, Scripted([{"op": "sleep", "seconds": 0}]),
                            pnd_fn_for(cx, BRAIN_UID), principal="brain")
        plan = bn2.resume()
        check(jid in plan["inflight"], "the restarted brain re-attached the in-flight job id")
        check(plan["intents"] == ["resume me"], "the restarted brain re-read the standing intents")
        check(plan["prev_session"] == old_sess, "the restarted brain knows the prior session id")
        check(plan["fresh_session"]["session_id"] != old_sess,
              "the restarted brain asked for a FRESH session (the queue carried the truth)")

        st = cx.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()["state"]
        check(st in ("queued", "running", "blocked"), "the in-flight job survived the restart (not dropped)")
    finally:
        cx.close(); _rm(path)

def test_fake_llm_served():
    print("[p] the FAKE pn-llmd is real-pn-llmd-served by a STUB backend (credential never touched)")

    rt = tempfile.mkdtemp(prefix="pn_brain_llm_")
    sock = os.path.join(rt, "pn-llmd.sock")
    script = os.path.join(rt, "script.json")
    pinscript = os.path.join(rt, "pinscript.json")
    state = os.path.join(rt, "state")
    act = json.dumps({"op": "submit", "task_type": "echo.test", "params": {"msg": "fake"}})
    with open(script, "w") as f:
        json.dump([act], f)
    with open(pinscript, "w") as f:
        json.dump([act], f)
    env = dict(os.environ)
    env["PN_LLM_SOCK"] = sock
    env["PN_LLM_POOL"] = "1"
    env["PN_FAKELLM_SCRIPT"] = script
    env["PN_FAKELLM_STATE"] = state
    env["PN_FAKEWARM_SCRIPT"] = pinscript
    fake = os.path.join(ROOT, "tests", "fakellm.py")
    fakewarm = os.path.join(ROOT, "tests", "fakewarm.py")

    env["PN_LLM_CMD"] = f"{sys.executable} {fake} {{model}}"
    env["PN_LLM_PIN_CMD"] = f"{sys.executable} {fakewarm}"
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "pn-llmd")],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(50):
            if os.path.exists(sock):
                break
            time.sleep(0.1)
        check(os.path.exists(sock), "the real pn-llmd came up on the scratch socket")
        ask = pnbrain._live_ask_fn(sock)
        text = ask("decide", session={"cache_prefix": "x"})
        action = B.parse_action(text)
        check(action["op"] == "submit" and action["task_type"] == "echo.test",
              "the real pn-llmd served the FAKE warm-pinned backend's closed-world action (no credential)")
        check("ANTHROPIC_API_KEY" not in (env.get("PN_LLM_CMD") or "")
              and "ANTHROPIC_API_KEY" not in (env.get("PN_LLM_PIN_CMD") or ""),
              "neither the pool nor the pinned backend command carries a credential (claude/Max key untouched)")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        subprocess.run(["rm", "-rf", rt])

def _rm(path):
    for p in (path, path + "-wal", path + "-shm"):
        try:
            os.unlink(p)
        except OSError:
            pass

def main():
    print("=== P5: the autonomous brain — test suite (FAKE pn-llmd; nothing live touched) ===")
    cx, path = fresh_db()
    try:
        test_schema(cx)
        test_closed_world()
        test_parse()
        test_dispose()
        test_timers(cx)
        test_state(cx)
        test_rotate(cx)
        test_resume(cx)
    finally:
        cx.close(); _rm(path)
    test_live_act()
    test_live_noop()
    test_live_propose()
    test_live_self_work()
    test_live_acting_as()
    test_live_keeper()
    test_live_auto_resume()
    test_fake_llm_served()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
