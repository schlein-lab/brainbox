#!/usr/bin/env python3

import os, sys, json, time, tempfile, threading, importlib.util, socket, subprocess
from importlib.machinery import SourceFileLoader

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from pnlib import db
import relaylib
from relaylib import registry, crypto, protocol as P, ID_METHOD, totp
from relaylib.keys import ApplianceKeys
from relaylib.transport import MockRelay, Channel
from relaylib.serve import serve_session
from relaylib.device import Device

PASS, FAIL = 0, 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {label}")
    else:
        FAIL += 1; print(f"  FAIL  {label}")

def fresh_engine_db():

    path = tempfile.mktemp(prefix="pn_relay_eng_", suffix=".db")
    cx = db.connect(path)
    cx.execute("INSERT OR IGNORE INTO principals(name,uid,kind,note) VALUES(?,?,?,?)",
               ("remote-user", 6001, "user", "off-LAN device owner (echo only)"))
    if not cx.execute("SELECT 1 FROM grants WHERE principal=? AND cap=?",
                      ("remote-user", "task_type:echo.test")).fetchone():
        cx.execute("INSERT INTO grants(principal,cap) VALUES(?,?)",
                   ("remote-user", "task_type:echo.test"))
    cx.commit()
    return cx, path

def appliance_keys_in(tmp):
    return ApplianceKeys(keys_dir=os.path.join(tmp, "keys"))

def run_box_session(relay_url, keys, reg, submit_fn, bind_fn, seen, done):

    try:
        ch = Channel.register(relay_url, crypto.rendezvous_topic(keys.sx_pub), timeout=5)
        bs = serve_session(ch, keys, reg, submit_fn, bind_identity_fn=bind_fn,
                           seen_nonces=seen, max_messages=50)
        done["bs"] = bs
    except Exception as e:
        done["err"] = f"{type(e).__name__}: {e}"

def test_pair_reconnect_and_zk():
    print("[1/2/4] pairing -> durable token; reconnect via stored token; relay is zero-knowledge")
    tmp = tempfile.mkdtemp(prefix="pn_relay_")
    relay = MockRelay()
    reg = registry.connect(os.path.join(tmp, "relay.db"))
    keys = appliance_keys_in(tmp)
    eng_cx, eng_path = fresh_engine_db()

    bound = []
    def bind_fn(method, selector, principal, verified):
        db.bind_identity(eng_cx, method, selector, principal, verified=verified)
        bound.append((method, selector, principal, verified))

    totp_secret = registry.arm_2fa(reg, "remote-user")

    code = registry.mint_pairing(reg, "remote-user", ["task_type:echo.test"],
                                 parent_principal="admin", label="test-phone",
                                 max_rate=30, max_concurrency=2)
    captured = {}
    def submit_fn(req):
        captured["req"] = req
        return {"ok": True, "id": 1, "pos": 1}

    done = {}
    th = threading.Thread(target=run_box_session,
                          args=(relay.url, keys, reg, submit_fn, bind_fn, set(), done))
    th.start(); time.sleep(0.1)
    dev = Device(box_id_pub=keys.id_pub, box_x_pub=keys.sx_pub, totp_secret=totp_secret)
    dev.connect(relay.url)
    pr = dev.pair(code, label="test-phone")
    th.join(timeout=5)

    check(pr.get("t") == P.PAIR_OK and pr.get("token"),
          f"[1] pairing returns a durable token (principal={pr.get('principal')})")
    token = pr.get("token")
    al = registry.get_alliance(reg, dev.did)
    check(al is not None and al["revoked_at"] is None and al["principal"] == "remote-user",
          "[1] durable alliance stored, active, bound to remote-user")
    check((ID_METHOD, dev.did, "remote-user", 1) in bound,
          "[1] device-channel identity bound in the ENGINE table (server-side resolution)")

    check(registry.redeem_pairing(reg, code) is None, "[1] pairing code is one-time (re-redeem None)")

    seen = relay.seen_frames
    types = {f["t"] for f in seen}
    has_payload_text = any(
        (f.get("blob") and _looks_like_plaintext(f["blob"])) for f in seen)
    check(types <= {P.RZ_REGISTER, P.RZ_DIAL, P.RZ_DATA, P.RZ_BYE},
          f"[4] relay saw ONLY rendezvous frame types {sorted(types)}")
    check(not has_payload_text,
          "[4] relay never saw plaintext (all data blobs are opaque ciphertext)")

    sample = next((f["blob"] for f in seen if f["t"] == P.RZ_DATA and f.get("blob")), None)
    check(sample is not None and _is_opaque(sample),
          "[4] a sampled data blob is high-entropy ciphertext (not JSON, not the code/token)")
    check(code not in json.dumps(seen) and (token or "x") not in json.dumps(seen),
          "[4] the pairing code and durable token NEVER appear in relay-visible frames")

    relay2 = MockRelay()
    done2 = {}
    th2 = threading.Thread(target=run_box_session,
                           args=(relay2.url, keys, reg, submit_fn, bind_fn, set(), done2))
    th2.start(); time.sleep(0.1)
    dev2 = Device(box_id_pub=keys.id_pub, box_x_pub=keys.sx_pub,
                  id_priv=dev.id_priv, id_pub=dev.id_pub,
                  sx_priv=dev.sx_priv, sx_pub=dev.sx_pub,
                  totp_secret=totp_secret)
    dev2.connect(relay2.url)
    dev2.token = token
    hr = dev2.hello()
    th2.join(timeout=5)
    check(hr.get("t") == P.HELLO_OK and hr.get("principal") == "remote-user",
          "[2] reconnect with STORED token succeeds (no re-login, no password)")

    relay.stop(); relay2.stop()
    eng_cx.close()
    for p in (eng_path, eng_path + "-wal", eng_path + "-shm"):
        try: os.unlink(p)
        except OSError: pass
    return tmp

def _looks_like_plaintext(blob_hex):
    try:
        raw = bytes.fromhex(blob_hex)
    except ValueError:
        return False
    return b'"t":' in raw or b"pair_request" in raw or b"submit" in raw

def _is_opaque(blob_hex):
    try:
        raw = bytes.fromhex(blob_hex)
    except ValueError:
        return False

    try:
        json.loads(raw.decode())
        return False
    except Exception:
        pass
    return len(set(raw)) > 16

def test_signed_submit_attenuated_and_security():
    print("[3/5/6] live signed submit lands attenuated; revocation kills; spoof/unsigned rejected")
    tmp = tempfile.mkdtemp(prefix="pn_relay_live_")
    relay = MockRelay()
    reg = registry.connect(os.path.join(tmp, "relay.db"))
    keys = appliance_keys_in(tmp)

    rt = tempfile.mkdtemp(prefix="pn_rt_"); data = tempfile.mkdtemp(prefix="pn_data_")
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = rt; env["XDG_DATA_HOME"] = data
    env["PN_DURABILITY"] = "normal"; env.pop("NOTIFY_SOCKET", None)
    env["PND_BROKER_UIDS"] = str(os.getuid())
    boot = os.path.join(rt, "boot.py")

    with open(boot, "w") as f:
        f.write(
            "import sys, runpy\n"
            f"sys.path.insert(0, {ROOT!r})\n"
            "from pnlib import sched, db, DB_PATH\n"
            "_orig = sched.Config.autoscale\n"
            "def _permissive():\n"
            "    c=_orig(); c.psi_stop=1e9; c.mem_floor=1; c.batch_high=1<<30; c.slack=0; return c\n"
            "sched.Config.autoscale = staticmethod(_permissive)\n"
            "cx = db.connect(DB_PATH)\n"
            "cx.execute(\"INSERT OR IGNORE INTO principals(name,uid,kind,note) VALUES('remote-user',6001,'user','dev owner')\")\n"
            "for cap in ('task_type:echo.test',):\n"
            "    if not cx.execute('SELECT 1 FROM grants WHERE principal=? AND cap=?',('remote-user',cap)).fetchone():\n"
            "        cx.execute('INSERT INTO grants(principal,cap) VALUES(?,?)',('remote-user',cap))\n"
            "# A relay broker MUST be structurally de-privileged (engine fail-closed): it may hold\n"
            "# NEITHER task.raw NOR task_type:* NOR view:all, or pnd refuses the assertion outright.\n"
            "# The seeded admin (this process's uid) holds all three, so we cannot relay AS admin —\n"
            "# instead we RE-MAP this uid to a dedicated, de-privileged 'relay-broker' principal that\n"
            "# holds ONLY act-as + a NARROW relay allowlist (mirroring the seeded adapter). Attenuation\n"
            "# is enforced by the relay∩submitter intersection, never by an over-privileged broker.\n"
            "uid = __import__('os').getuid()\n"
            "cx.execute('UPDATE principals SET uid=NULL WHERE name=\"admin\"')\n"
            "cx.execute(\"INSERT OR IGNORE INTO principals(name,uid,kind,note) VALUES('relay-broker',?,'system','de-privileged relay broker')\", (uid,))\n"
            "cx.execute('UPDATE principals SET uid=? WHERE name=\"relay-broker\"', (uid,))\n"
            "for cap in ('act-as','task_type:echo.test'):\n"
            "    if not cx.execute('SELECT 1 FROM grants WHERE principal=? AND cap=?',('relay-broker',cap)).fetchone():\n"
            "        cx.execute('INSERT INTO grants(principal,cap) VALUES(?,?)',('relay-broker',cap))\n"
            "cx.commit()\n"
            f"runpy.run_path({os.path.join(ROOT, 'tools', 'pnd')!r}, run_name='__main__')\n")
    proc = subprocess.Popen([sys.executable, boot], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    sock = os.path.join(rt, "pnd.sock")
    try:
        for _ in range(50):
            if os.path.exists(sock): break
            time.sleep(0.1)
        check(os.path.exists(sock), "[3] scratch pnd up on private socket")

        def submit_fn(req):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(20); s.connect(sock)
            s.sendall((json.dumps(req) + "\n").encode())
            buf = b""
            while b"\n" not in buf:
                ch = s.recv(65536)
                if not ch: break
                buf += ch
            s.close()
            return json.loads(buf.split(b"\n", 1)[0].decode())

        eng_cx = db.connect(os.path.join(data, "portioneer", "queue.db"))
        def bind_fn(method, selector, principal, verified):
            db.bind_identity(eng_cx, method, selector, principal, verified=verified)

        totp_secret = registry.arm_2fa(reg, "remote-user")
        code = registry.mint_pairing(reg, "remote-user", ["task_type:echo.test"],
                                     parent_principal="admin", label="phone", max_rate=30)

        seen_nonces = set()
        done = {}
        th = threading.Thread(target=run_box_session,
                              args=(relay.url, keys, reg, submit_fn, bind_fn, seen_nonces, done))
        th.start(); time.sleep(0.1)
        dev = Device(box_id_pub=keys.id_pub, box_x_pub=keys.sx_pub, totp_secret=totp_secret)
        dev.connect(relay.url)
        pr = dev.pair(code)

        r_ok = dev.submit(task_type="echo.test", params={"msg": "from-phone"})
        r_denied = dev.submit(task_type="sleep.test", params={"s": "1"})
        r_unsigned = dev.submit(task_type="echo.test", params={"msg": "x"}, sign=False)
        r_tampered = dev.submit(task_type="echo.test", params={"msg": "y"}, tamper=True)
        dev.close()
        th.join(timeout=5)

        check(pr.get("t") == P.PAIR_OK, "[3] device paired")
        check(r_ok.get("t") == P.RESULT and r_ok.get("id"),
              f"[3] signed echo.test submission accepted (job id={r_ok.get('id')})")

        time.sleep(0.5)
        jid = r_ok.get("id")

        jrow = {"job": db.get(eng_cx, jid, scope_all=True) or {}}
        owner = jrow.get("job", {}).get("submitter_principal")
        via = jrow.get("job", {}).get("via_device")
        vmethod = jrow.get("job", {}).get("via_method")
        check(owner == "remote-user",
              f"[3] job submitter_principal == remote-user (box-verified identity), got {owner!r}")
        check(via == dev.did and vmethod == ID_METHOD,
              f"[3] provenance recorded (via_device=did, via_method={vmethod!r})")

        check(r_denied.get("t") == P.ERROR and "sleep.test" in r_denied.get("error", ""),
              f"[3] sleep.test REJECTED for the device (attenuated, not admin caps): "
              f"{r_denied.get('error')!r}")

        check(r_unsigned.get("t") == P.ERROR and "unsigned" in r_unsigned.get("error", ""),
              f"[6] unsigned submission REJECTED: {r_unsigned.get('error')!r}")
        check(r_tampered.get("t") == P.ERROR and "signature" in r_tampered.get("error", ""),
              f"[6] spoofed (tampered) submission REJECTED: {r_tampered.get('error')!r}")

        killed = registry.revoke(reg, dev.did)
        check(killed, "[5] revoke() killed the alliance")
        done2 = {}
        relay3 = MockRelay()
        th3 = threading.Thread(target=run_box_session,
                               args=(relay3.url, keys, reg, submit_fn, bind_fn, seen_nonces, done2))
        th3.start(); time.sleep(0.1)
        dev3 = Device(box_id_pub=keys.id_pub, box_x_pub=keys.sx_pub,
                      id_priv=dev.id_priv, id_pub=dev.id_pub,
                      sx_priv=dev.sx_priv, sx_pub=dev.sx_pub,
                      totp_secret=totp_secret)
        dev3.connect(relay3.url)
        dev3.token = pr["token"]
        hr = dev3.hello()
        dev3.close()
        th3.join(timeout=5)
        relay3.stop()
        check(hr.get("t") == P.ERROR and "revoked" in hr.get("error", "").lower(),
              f"[5] post-revocation reconnect REJECTED: {hr.get('error')!r}")

    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except Exception: proc.kill()
        relay.stop()
        try:
            import sqlite3
            cxv = sqlite3.connect(os.path.join(data, "portioneer", "queue.db"))
            ic = cxv.execute("PRAGMA integrity_check").fetchone()[0]; cxv.close()
            check(ic == "ok", f"[3] scratch engine DB integrity_check = {ic!r}")
        except Exception as e:
            check(False, f"scratch DB integrity_check raised {e}")
        subprocess.run(["rm", "-rf", rt, data, tmp])

def test_disabled_by_default():
    print("[gate] disabled-by-default + production-enable gating")
    saved = {k: os.environ.get(k) for k in
             ("RELAY_ENABLED", "RELAY_URL", "RELAY_ACK_BLAST_RADIUS")}
    try:
        for k in saved: os.environ.pop(k, None)
        check(relaylib.enabled() is False, "[gate] relay DISABLED by default (RELAY_ENABLED unset)")
        reasons = []
        check(relaylib.is_production_ready(reasons) is False,
              "[gate] not production-ready by default")
        check(any("RELAY_ENABLED" in r for r in reasons), "[gate] cites the master gate")

        os.environ["RELAY_ENABLED"] = "1"; os.environ["RELAY_URL"] = "mock://127.0.0.1:1"
        reasons = []
        check(relaylib.is_production_ready(reasons) is False,
              "[gate] enabled+mock:// still NOT production-ready (mock is tests-only)")
        check(any("mock" in r for r in reasons), "[gate] cites mock:// is not production")

        os.environ["RELAY_URL"] = "wss://relay.example/rz"
        os.environ["RELAY_ACK_BLAST_RADIUS"] = "1"
        check(relaylib.is_production_ready([]) is True,
              "[gate] wss:// + operator blast-radius ack -> production-ready")
    finally:
        for k, v in saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v

def main():
    print("=== pn-relayd off-LAN relay strand — test suite ===")
    test_disabled_by_default()
    test_pair_reconnect_and_zk()
    test_signed_submit_attenuated_and_security()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
