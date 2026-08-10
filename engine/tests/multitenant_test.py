#!/usr/bin/env python3

import os, sys, json, time, tempfile, importlib.util, socket, subprocess
os.environ.setdefault("PN_DISPATCH_BACKEND", "systemd")
from importlib.machinery import SourceFileLoader

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from pnlib import db
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pn_voraussetzung import live_moeglich

_loader = SourceFileLoader("pnd_mod", os.path.join(ROOT, "tools", "pnd"))
_spec = importlib.util.spec_from_loader("pnd_mod", _loader)
pnd = importlib.util.module_from_spec(_spec)
_loader.exec_module(pnd)

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
    path = tempfile.mktemp(prefix="pn_mt_", suffix=".db")
    cx = db.connect(path)

    for name, uid in (("alice", 5001), ("bob", 5002)):
        cx.execute("INSERT OR IGNORE INTO principals(name,uid,kind,note) VALUES(?,?,?,?)",
                   (name, uid, "user", f"test tenant {name}"))

    for principal, cap in (("alice", "task_type:echo.test"),
                           ("bob", "task_type:echo.test"), ("bob", "task_type:sleep.test")):
        if not cx.execute("SELECT 1 FROM grants WHERE principal=? AND cap=?",
                          (principal, cap)).fetchone():
            cx.execute("INSERT INTO grants(principal,cap) VALUES(?,?)", (principal, cap))
    cx.commit()

    db.bind_identity(cx, "telegram-id", "111", "alice", verified=0)
    db.bind_identity(cx, "telegram-id", "222", "bob", verified=0)
    return cx, path

def test_cross_tenant_accessor(cx):
    print("[a] cross-tenant read/cancel denial at the DB accessor")

    ja = db.submit(cx, ["/bin/echo", "a"], "/tmp", {}, "{}", 100, 64, "tag-a",
                   principal="alice", task_type="echo.test")
    jb = db.submit(cx, ["/bin/echo", "b"], "/tmp", {}, "{}", 100, 64, "tag-b",
                   principal="bob", task_type="echo.test")
    db.add_event(cx, jb, "checkpoint", "bob-secret")
    check(ja != jb and abs(ja - jb) == 1, f"ids are sequential/enumerable ({ja},{jb})")

    check(db.get(cx, jb, principal="alice") is None,
          "alice get(bob's id) -> None (cross-tenant read denied)")

    check(db.get(cx, ja, principal="alice") is not None,
          "alice get(her own id) -> row (owner allowed)")

    check(db.events(cx, jb, principal="alice") == [],
          "alice events(bob's id) -> [] (cross-tenant events denied)")
    check(len(db.events(cx, jb, principal="bob")) == 1,
          "bob events(his own id) -> his event")

    alice_list = db.list_recent(cx, principal="alice")
    check(all(j["id"] != jb for j in alice_list) and any(j["id"] == ja for j in alice_list),
          "alice list shows only her jobs (no bob)")

    admin_view = db.list_recent(cx, scope_all=True)
    ids = {j["id"] for j in admin_view}
    check(ja in ids and jb in ids, "admin list (scope_all) sees BOTH jobs")
    check(db.get(cx, jb, scope_all=True) is not None,
          "admin get(bob's id, scope_all) -> row (view:all bypass)")
    check(db.events(cx, jb, scope_all=True), "admin events(bob's id, scope_all) -> bob's event")

    check({j["id"] for j in db.jobs_for_principal(cx, "alice")} == {ja},
          "jobs_for_principal('alice') == {alice's job}")
    return ja, jb

def _req(uid, **kw):

    r = dict(kw)
    r.pop("principal", None)
    r.pop("uid", None)
    r["_peer_uid"] = uid
    return r

def test_rce_dead(cx):
    print("[b] portal raw-cmd RCE is dead (broker can never land raw /bin/bash -lc)")
    pnd.CX = cx
    os.environ["PND_BROKER_UIDS"] = "4003"

    raised = None
    try:
        pnd.authorize_submit(_req(4003, cmd=["/bin/bash", "-lc", "id > /tmp/pwned"],
                                  _method="telegram-id", _selector="111"))
    except pnd.AuthzError as e:
        raised = str(e)
    check(raised is not None and "raw" in raised.lower(),
          f"broker raw-cmd on behalf of alice REJECTED ({raised!r})")

    raised2 = None
    try:
        pnd.authorize_submit(_req(4003, cmd=["/bin/bash", "-lc", "id"]))
    except pnd.AuthzError as e:
        raised2 = str(e)
    check(raised2 is not None and "raw" in raised2.lower(),
          f"adapter direct raw-cmd REJECTED ({raised2!r})")

    raised3 = None
    try:
        pnd.authorize_submit(_req(5001, cmd=["/bin/bash", "-lc", "id"]))
    except pnd.AuthzError as e:
        raised3 = str(e)
    check(raised3 is not None and "raw" in raised3.lower(),
          f"alice direct raw-cmd REJECTED ({raised3!r})")

def test_intersection(cx):
    print("[c] on-behalf-of caps = intersect(adapter, submitter); no amplification")
    pnd.CX = cx
    os.environ["PND_BROKER_UIDS"] = "4003"

    argv, ctx = pnd.authorize_submit(_req(4003, task_type="echo.test",
                                          params={"msg": "hi"},
                                          _method="telegram-id", _selector="111"))
    check(ctx["principal"] == "alice" and ctx["task_type"] == "echo.test"
          and argv == ["/bin/echo", "hi"],
          "broker->alice echo.test resolves to alice, vetted argv built")
    check(ctx["is_broker"] and ctx["via_method"] == "telegram-id",
          "provenance recorded (is_broker, via_method=telegram-id)")

    denied = None
    try:
        pnd.authorize_submit(_req(4003, task_type="sleep.test", params={"s": "1"},
                                  _method="telegram-id", _selector="111"))
    except pnd.AuthzError as e:
        denied = str(e)
    check(denied is not None,
          f"broker cannot grant alice sleep.test (she lacks it) ({denied!r})")

    argv2, ctx2 = pnd.authorize_submit(_req(4003, task_type="sleep.test", params={"s": "1"},
                                            _method="telegram-id", _selector="222"))
    check(ctx2["principal"] == "bob" and argv2 == ["/bin/sleep", "1"],
          "broker->bob sleep.test allowed (bob holds the cap)")

    check("act-as" not in ctx["caps"] and "task.raw" not in ctx["caps"],
          "resolved caps exclude the adapter's act-as / admin caps")

    argv3, ctx3 = pnd.authorize_submit(_req(4003, task_type="echo.test", params={"msg": "x"},
                                            _method="telegram-id", _selector="999"))
    check(ctx3["principal"] == "lan-guest",
          "unmatched selector falls back to lan-guest, never admin")

    spoof = None
    try:
        pnd.authorize_submit(_req(5001, task_type="echo.test", params={"msg": "x"},
                                  _method="telegram-id", _selector="222"))
    except pnd.AuthzError as e:
        spoof = str(e)
    check(spoof is not None and "broker" in spoof.lower(),
          f"non-broker uid cannot use the broker path ({spoof!r})")

def test_integrity(cx, path):
    print("[d] PRAGMA integrity_check")
    r = cx.execute("PRAGMA integrity_check").fetchone()[0]
    check(r == "ok", f"integrity_check = {r!r}")

    cols = {row["name"] for row in cx.execute("PRAGMA table_info(jobs)")}
    check({"submitter_principal", "via_device", "via_method"} <= cols,
          "v4 columns present (submitter_principal/via_device/via_method)")
    tabs = {r["name"] for r in cx.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    check("identities" in tabs, "identities table present")
    idx = {r["name"] for r in cx.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    check("idx_principal" in idx, "idx_principal index present")

def test_legacy_migration():
    print("[d2] legacy NULL-principal rows migrate to admin")

    path = tempfile.mktemp(prefix="pn_legacy_", suffix=".db")
    import sqlite3
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, cmd TEXT NOT NULL,"
                " cwd TEXT NOT NULL, env TEXT, profile TEXT NOT NULL, prio INTEGER NOT NULL"
                " DEFAULT 100, mem_estimate INTEGER NOT NULL DEFAULT 256, state TEXT NOT NULL"
                " DEFAULT 'queued', scope_unit TEXT, exit_code INTEGER, log_path TEXT,"
                " client_tag TEXT, submitted_at REAL NOT NULL, started_at REAL,"
                " finished_at REAL, principal TEXT)")
    raw.execute("INSERT INTO jobs(cmd,cwd,env,profile,submitted_at,principal)"
                " VALUES('[\"x\"]','/tmp','{}','{}',1.0,'brain')")
    raw.execute("INSERT INTO jobs(cmd,cwd,env,profile,submitted_at,principal)"
                " VALUES('[\"y\"]','/tmp','{}','{}',2.0,NULL)")
    raw.commit(); raw.close()
    cx = db.connect(path)
    rows = {r["id"]: r["submitter_principal"]
            for r in cx.execute("SELECT id, submitter_principal FROM jobs")}
    check(rows.get(1) == "brain", "row with principal -> submitter_principal carried forward")
    check(rows.get(2) == "admin", "legacy NULL-principal row -> assigned to admin")
    cx.close(); os.unlink(path)

def test_canary_e2e():

    if not live_moeglich('test_canary_e2e'):
        return
    print("[e] canary: admin templated dispatch+complete + raw retained (live IPC, scratch pnd)")
    rt = tempfile.mkdtemp(prefix="pn_rt_")
    data = tempfile.mkdtemp(prefix="pn_data_")
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = rt
    env["XDG_DATA_HOME"] = data
    env["PN_DURABILITY"] = "normal"
    env.pop("NOTIFY_SOCKET", None)

    boot = os.path.join(rt, "boot.py")
    with open(boot, "w") as f:
        f.write(
            "import sys, runpy\n"
            f"sys.path.insert(0, {ROOT!r})\n"
            "from pnlib import sched\n"
            "_orig = sched.Config.autoscale\n"
            "def _permissive():\n"
            "    c = _orig(); c.psi_stop = 1e9; c.mem_floor = 1; c.batch_high = 1<<30\n"
            "    c.slack = 0; return c\n"
            "sched.Config.autoscale = staticmethod(_permissive)\n"
            f"runpy.run_path({os.path.join(ROOT, 'tools', 'pnd')!r}, run_name='__main__')\n")
    proc = subprocess.Popen([sys.executable, boot],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    sock = os.path.join(rt, "pnd.sock")
    try:
        for _ in range(50):
            if os.path.exists(sock):
                break
            time.sleep(0.1)
        check(os.path.exists(sock), f"scratch pnd socket up at {sock}")

        def ipc(req, timeout=20):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout); s.connect(sock)
            s.sendall((json.dumps(req) + "\n").encode())
            buf = b""
            while b"\n" not in buf:
                ch = s.recv(65536)
                if not ch:
                    break
                buf += ch
            s.close()
            return json.loads(buf.split(b"\n", 1)[0].decode())

        r = ipc({"verb": "ping"})
        check(r.get("ok"), "ping ok")

        r = ipc({"verb": "submit", "task_type": "echo.test", "params": {"msg": "canary-ok"},
                 "class": "worker"})
        check(r.get("ok") and r.get("id"), f"admin echo.test submit ok (id={r.get('id')})")
        jid = r["id"]
        state = None
        for _ in range(100):
            jr = ipc({"verb": "job", "id": jid})
            if jr.get("ok"):
                state = jr["job"]["state"]
                if state in ("done", "failed", "cancelled", "timeout"):
                    break
            time.sleep(0.2)
        check(state == "done", f"admin echo.test completed (state={state})")
        lg = ipc({"verb": "log", "id": jid})
        check("canary-ok" in (lg.get("stdout") or ""), "echo.test produced expected output")

        r = ipc({"verb": "submit", "cmd": ["/bin/echo", "raw-retained"], "class": "worker"})
        check(r.get("ok"), f"admin raw cmd accepted (task.raw retained) id={r.get('id')}")
        rid = r["id"]
        rstate = None
        for _ in range(100):
            jr = ipc({"verb": "job", "id": rid})
            if jr.get("ok"):
                rstate = jr["job"]["state"]
                if rstate in ("done", "failed", "cancelled", "timeout"):
                    break
            time.sleep(0.2)
        check(rstate == "done", f"admin raw job completed (state={rstate})")

        lr = ipc({"verb": "list"})
        check(lr.get("ok") and len(lr["jobs"]) >= 2, "admin list returns its jobs")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

        try:
            import sqlite3
            cxv = sqlite3.connect(os.path.join(data, "portioneer", "queue.db"))
            ic = cxv.execute("PRAGMA integrity_check").fetchone()[0]
            cxv.close()
            check(ic == "ok", f"scratch DB integrity_check = {ic!r}")
        except Exception as e:
            check(False, f"scratch DB integrity_check raised {e}")
        subprocess.run(["rm", "-rf", rt, data])

def test_template_single_pass(cx):
    print("[f] template substitution is single-pass (no cross-param placeholder bleed)")
    pnd.CX = cx

    cx.execute("INSERT OR REPLACE INTO task_types"
               "(name,cmd_template,params_schema,isolation_tier,needs_confirmation,klass,approval)"
               " VALUES('twop.test','[\"/bin/echo\",\"{a}\",\"{b}\"]','{\"a\":\"str\",\"b\":\"str\"}',"
               "NULL,0,NULL,'none')")
    cx.execute("INSERT OR IGNORE INTO grants(principal,cap) VALUES('alice','task_type:twop.test')")
    cx.commit()

    argv, ctx = pnd.authorize_submit(_req(5001, task_type="twop.test",
                                          params={"a": "{b}", "b": "PWNED"}))
    check(argv == ["/bin/echo", "{b}", "PWNED"],
          f"a's value '{{b}}' stays LITERAL (no bleed); got {argv!r}")

    argv2, _ = pnd.authorize_submit(_req(5001, task_type="twop.test",
                                         params={"a": "AA", "b": "{a}"}))
    check(argv2 == ["/bin/echo", "AA", "{a}"],
          f"b's value '{{a}}' stays LITERAL too; got {argv2!r}")

def test_broker_failclosed(cx):
    print("[g] broker fail-closed: an over-privileged broker is rejected structurally")
    pnd.CX = cx
    os.environ["PND_BROKER_UIDS"] = "4003,4099"

    cx.execute("INSERT OR IGNORE INTO principals(name,uid,kind) VALUES('badbroker',4099,'system')")
    for capv in ("act-as", "task.raw"):
        cx.execute("INSERT OR IGNORE INTO grants(principal,cap) VALUES('badbroker',?)", (capv,))
    cx.commit()
    raised = None
    try:
        pnd._resolve_principal(_req(4099, task_type="echo.test",
                                    _method="telegram-id", _selector="111"))
    except pnd.AuthzError as e:
        raised = str(e)
    check(raised is not None and "de-privileged" in raised,
          f"broker holding task.raw REJECTED fail-closed ({raised!r})")

    _argv, ctx = pnd.authorize_submit(_req(4003, task_type="echo.test", params={"msg": "ok"},
                                           _method="telegram-id", _selector="111"))
    check(ctx["principal"] == "alice" and ctx["is_broker"],
          "the legitimately de-privileged adapter broker still resolves normally")

def test_ceiling_intersection(cx):
    print("[h] broker ceiling: a narrower _ceiling_caps reduces eff; a wildcard ceiling is rejected")
    pnd.CX = cx
    os.environ["PND_BROKER_UIDS"] = "4003"

    ctx_full = pnd._resolve_principal(_req(4003, _method="telegram-id", _selector="222"))
    check({"task_type:echo.test", "task_type:sleep.test"} <= ctx_full["caps"],
          f"no ceiling -> eff = relay ∩ bob = both task caps ({sorted(ctx_full['caps'])})")

    ctx_narrow = pnd._resolve_principal(_req(4003, _method="telegram-id", _selector="222",
                                             _ceiling_caps=["task_type:echo.test"]))
    check(ctx_narrow["caps"] == {"task_type:echo.test"},
          f"_ceiling_caps narrows eff to the ceiling ∩ (relay ∩ bob) ({sorted(ctx_narrow['caps'])})")

    raised = None
    try:
        pnd._resolve_principal(_req(4003, _method="telegram-id", _selector="222",
                                    _ceiling_caps=["task_type:*"]))
    except pnd.AuthzError as e:
        raised = str(e)
    check(raised is not None and "wildcard" in raised,
          f"a wildcard ceiling is REJECTED (can never widen) ({raised!r})")

def test_group_submit_isolation(cx):
    print("[i] submit-time group isolation: joining another tenant's group_id is refused")
    pnd.CX = cx

    ja = db.submit(cx, ["/bin/echo", "a"], "/tmp", {}, "{}", 100, 64, "grp-a",
                   principal="alice", task_type="echo.test", group_id="wf-mt")
    raised = None
    try:
        pnd.validate_group_ownership({"principal": "bob", "caps": {"task_type:echo.test"}}, "wf-mt")
    except pnd.AuthzError as e:
        raised = str(e)
    check(raised is not None and "another tenant" in raised,
          f"bob joining alice's group_id REJECTED ({raised!r})")

    try:
        pnd.validate_group_ownership({"principal": "alice", "caps": {"task_type:echo.test"}}, "wf-mt")
        pnd.validate_group_ownership({"principal": "admin", "caps": {"view:all"}}, "wf-mt")
        ok = True
    except pnd.AuthzError:
        ok = False
    check(ok, "the group OWNER and admin(view:all) may add to the group")

def main():
    print("=== P2 multi-tenant identity foundation — test suite ===")
    cx, path = fresh_db()
    try:
        test_cross_tenant_accessor(cx)
        test_rce_dead(cx)
        test_intersection(cx)
        test_template_single_pass(cx)
        test_broker_failclosed(cx)
        test_ceiling_intersection(cx)
        test_group_submit_isolation(cx)
        test_integrity(cx, path)
    finally:
        cx.close()
        for p in (path, path + "-wal", path + "-shm"):
            try:
                os.unlink(p)
            except OSError:
                pass
    test_legacy_migration()
    test_canary_e2e()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
