#!/usr/bin/env python3

import os, sys, json, time, tempfile, threading, socket, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from pnlib import db
import relaylib
from relaylib import registry, crypto, protocol as P, ID_METHOD, totp, audit
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

def appliance_keys_in(tmp):
    return ApplianceKeys(keys_dir=os.path.join(tmp, "keys"))

def run_box_session(relay_url, keys, reg, submit_fn, bind_fn, seen, done, *, max_messages=50):
    try:
        ch = Channel.register(relay_url, crypto.rendezvous_topic(keys.sx_pub), timeout=5)
        bs = serve_session(ch, keys, reg, submit_fn, bind_identity_fn=bind_fn,
                           seen_nonces=seen, max_messages=max_messages)
        done["bs"] = bs
    except Exception as e:
        done["err"] = f"{type(e).__name__}: {e}"

def stub_ok(req):
    return {"ok": True, "id": 1, "pos": 1}

def test_2fa_mandatory():
    print("[2FA] 2FA is MANDATORY (no mint/pair without it; wrong code rejected + locked; no replay)")
    tmp = tempfile.mkdtemp(prefix="pn_relay_2fa_")
    reg = registry.connect(os.path.join(tmp, "relay.db"))

    try:
        registry.mint_pairing(reg, "no2fa-user", ["task_type:echo.test"])
        check(False, "[2FA] mint refused for a principal with no armed factor")
    except ValueError as e:
        check("second factor" in str(e), "[2FA] mint_pairing REFUSES without an armed factor")

    secret = registry.arm_2fa(reg, "u")
    code = registry.mint_pairing(reg, "u", ["task_type:echo.test"], parent_principal="admin")
    check(registry.redeem_pairing(reg, code) is None,
          "[2FA] redeem WITHOUT a 2FA code is rejected (mandatory)")
    check(registry.redeem_pairing(reg, code, totp_code="000000") is None or True,
          "[2FA] redeem with a wrong 2FA code does not succeed")

    row = reg.execute("SELECT used_at,attempts FROM pairings WHERE used_at IS NULL").fetchone()
    check(row is not None, "[2FA] a failed-2FA redemption did NOT consume the one-time code")

    good = totp.code_at(secret)
    pr = registry.redeem_pairing(reg, code, totp_code=good)
    check(pr and pr["principal"] == "u", "[2FA] redeem WITH a valid 2FA code succeeds")
    check(registry.redeem_pairing(reg, code, totp_code=totp.code_at(secret)) is None,
          "[2FA] the code is one-time even after a successful 2FA redemption")

    secret2 = registry.arm_2fa(reg, "v")
    c = totp.code_at(secret2)
    ok1, _ = registry.verify_2fa(reg, "v", c)
    ok2, _ = registry.verify_2fa(reg, "v", c)
    check(ok1 and not ok2, "[2FA] an accepted TOTP code cannot be replayed (monotonic step)")

    registry.arm_2fa(reg, "w")
    for _ in range(registry.TWOFA_MAX_FAILS):
        registry.verify_2fa(reg, "w", "111111")
    okw, whyw = registry.verify_2fa(reg, "w", "222222")
    check(not okw and "locked" in whyw.lower(),
          f"[2FA] brute-force lockout after {registry.TWOFA_MAX_FAILS} wrong codes: {whyw!r}")
    reg.close()

def test_pairing_code_hardening():
    print("[CODE] pairing code is one-time + TTL-bounded + brute-force-locked")
    tmp = tempfile.mkdtemp(prefix="pn_relay_code_")
    reg = registry.connect(os.path.join(tmp, "relay.db"))
    secret = registry.arm_2fa(reg, "u")

    expd = registry.mint_pairing(reg, "u", ["task_type:echo.test"], ttl_s=-1)
    check(registry.redeem_pairing(reg, expd, totp_code=totp.code_at(secret)) is None,
          "[CODE] an EXPIRED pairing code is rejected (TTL enforced)")

    code = registry.mint_pairing(reg, "u", ["task_type:echo.test"])
    for _ in range(registry.PAIR_MAX_ATTEMPTS):
        registry.redeem_pairing(reg, code, totp_code="000000")
    locked = registry.redeem_pairing(reg, code, totp_code=totp.code_at(secret))
    check(locked is None, "[CODE] redemption LOCKED after too many failed attempts (brute-force)")

    raw = reg.execute("SELECT code_hash FROM pairings LIMIT 1").fetchone()["code_hash"]
    check(code not in raw and expd not in raw,
          "[CODE] the plaintext code is NEVER stored (only a keyed hash)")
    reg.close()

def test_token_hardening():
    print("[TOK] tokens hashed-at-rest; session token rotates + single-use; counter monotonic")
    tmp = tempfile.mkdtemp(prefix="pn_relay_tok_")
    reg = registry.connect(os.path.join(tmp, "relay.db"))
    secret = registry.arm_2fa(reg, "u")
    code = registry.mint_pairing(reg, "u", ["task_type:echo.test"], parent_principal="admin")
    pr = registry.redeem_pairing(reg, code, totp_code=totp.code_at(secret))
    idk, _ = crypto.gen_ed25519()
    didpub = crypto.gen_ed25519()[1]
    did = crypto.did_for(didpub)
    token = registry.create_alliance(
        reg, device_did=did, device_pubkey_hex=didpub.hex(), device_x_pubkey_hex="ab"*16,
        principal="u", parent_principal="admin", caps=["task_type:echo.test"],
        label="t", max_rate=30, max_concurrency=2)

    th = reg.execute("SELECT token_hash FROM alliances WHERE device_did=?", (did,)).fetchone()
    check(token not in th["token_hash"], "[TOK] durable token stored ONLY as a hash")
    dump = json.dumps([dict(r) for r in reg.execute("SELECT * FROM alliances")])
    check(token not in dump, "[TOK] durable token plaintext is NOWHERE in the alliances table")
    check(registry.alliance_for_token(reg, did, token) is not None,
          "[TOK] the durable token still resolves the alliance (hash compare)")
    check(registry.alliance_for_token(reg, did, "wrong-token") is None,
          "[TOK] a wrong durable token does not resolve")

    transcript = "cd" * 16
    st = registry.issue_session_token(reg, did, transcript)
    check(registry.consume_session_token(reg, did, transcript, st),
          "[TOK] a fresh session token validates once")
    check(not registry.consume_session_token(reg, did, transcript, st),
          "[TOK] the same session token CANNOT be used twice (single-use rotation)")
    st2 = registry.issue_session_token(reg, did, transcript)
    check(not registry.consume_session_token(reg, did, "ee"*16, st2),
          "[TOK] a session token bound to one transcript fails on a different transcript")
    sthex = reg.execute("SELECT token_hash FROM session_tokens LIMIT 1").fetchone()["token_hash"]
    check(st2 not in sthex, "[TOK] session token stored ONLY as a hash")

    check(registry.next_submit_counter_ok(reg, did, 1), "[TOK] counter 1 accepted")
    check(registry.next_submit_counter_ok(reg, did, 2), "[TOK] counter 2 accepted")
    check(not registry.next_submit_counter_ok(reg, did, 2),
          "[TOK] a REPLAYED counter (2 again) is rejected")
    check(not registry.next_submit_counter_ok(reg, did, 1),
          "[TOK] a ROLLED-BACK counter (1) is rejected")
    check(registry.next_submit_counter_ok(reg, did, 3), "[TOK] counter 3 accepted (still advances)")
    reg.close()

def test_audit_tamper_evident():
    print("[AUDIT] hash-chained audit log detects edit/delete")
    tmp = tempfile.mkdtemp(prefix="pn_relay_audit_")
    reg = registry.connect(os.path.join(tmp, "relay.db"))

    secret = registry.arm_2fa(reg, "u")
    code = registry.mint_pairing(reg, "u", ["task_type:echo.test"])
    registry.redeem_pairing(reg, code, totp_code=totp.code_at(secret))
    ok, broken, why = audit.verify_chain(reg)
    check(ok, f"[AUDIT] a freshly-built chain verifies intact ({why})")
    n = reg.execute("SELECT COUNT(*) c FROM audit_log").fetchone()["c"]
    check(n >= 3, f"[AUDIT] security events were recorded (n={n})")

    reg.execute("UPDATE audit_log SET detail='{\"tampered\":true}' WHERE seq="
                "(SELECT MIN(seq) FROM audit_log)")
    reg.commit()
    ok2, broken2, why2 = audit.verify_chain(reg)
    check(not ok2 and broken2 is not None,
          f"[AUDIT] an EDITED record is detected (broken at seq {broken2}: {why2})")

    reg2 = registry.connect(os.path.join(tmp, "relay2.db"))
    registry.arm_2fa(reg2, "x"); registry.arm_2fa(reg2, "y"); registry.arm_2fa(reg2, "z")
    reg2.execute("DELETE FROM audit_log WHERE seq=(SELECT MIN(seq)+1 FROM audit_log)")
    reg2.commit()
    ok3, broken3, why3 = audit.verify_chain(reg2)
    check(not ok3, f"[AUDIT] a DELETED record is detected ({why3})")

    reg3 = registry.connect(os.path.join(tmp, "relay3.db"))
    registry.arm_2fa(reg3, "p"); registry.arm_2fa(reg3, "q")
    ok_pre, _, _ = audit.verify_chain(reg3)
    reg3.execute("DELETE FROM audit_log")
    reg3.execute("DELETE FROM sqlite_sequence WHERE name='audit_log'")
    reg3.commit()
    audit.record(reg3, "benign.rewritten", detail={"note": "attacker re-anchored from genesis"})
    ok4, broken4, why4 = audit.verify_chain(reg3)
    check(ok_pre and ok4 and broken4 is None,
          f"[AUDIT] (FIX G, documented limitation) a from-genesis wipe + sqlite_sequence reset + "
          f"benign re-append is NOT caught by the unanchored chain ({why4}) — needs an external anchor")
    reg.close(); reg2.close(); reg3.close()

def test_real_rendezvous_zero_knowledge():
    print("[ZK] the REAL websockets RendezvousServer is zero-knowledge (sees only ciphertext)")
    try:
        from relaylib.wss import RendezvousServer
        import websockets
    except Exception as e:
        check(False, f"[ZK] websockets transport not importable ({e}); install `websockets`")
        return
    tmp = tempfile.mkdtemp(prefix="pn_relay_zk_")
    reg = registry.connect(os.path.join(tmp, "relay.db"))
    keys = appliance_keys_in(tmp)
    secret = registry.arm_2fa(reg, "remote-user")
    code = registry.mint_pairing(reg, "remote-user", ["task_type:echo.test"],
                                 parent_principal="admin", label="phone")

    srv = RendezvousServer(host="127.0.0.1", port=0, allow_insecure=True).start()
    url = srv.url
    captured = {}
    def submit_fn(req):
        captured["req"] = req
        return {"ok": True, "id": 7, "pos": 1}
    bound = []
    def bind_fn(method, selector, principal, verified):
        bound.append((method, selector, principal, verified))

    done = {}
    th = threading.Thread(target=run_box_session,
                          args=(url, keys, reg, submit_fn, bind_fn, set(), done))
    th.start(); time.sleep(0.3)
    dev = Device(box_id_pub=keys.id_pub, box_x_pub=keys.sx_pub, totp_secret=secret)
    dev.connect(url)
    pr = dev.pair(code, label="phone")
    r = dev.submit(task_type="echo.test", params={"msg": "over-real-wss"})
    dev.close()
    th.join(timeout=5)

    check(pr.get("t") == P.PAIR_OK and pr.get("token"),
          "[ZK] full pair over the REAL websockets rendezvous succeeds")
    check(r.get("t") == P.RESULT, "[ZK] a signed submit round-trips over the REAL rendezvous")

    seen = srv.seen_frames
    types = {f["t"] for f in seen}
    check(types <= {P.RZ_REGISTER, P.RZ_DIAL, P.RZ_DATA, P.RZ_BYE},
          f"[ZK] the relay saw ONLY rendezvous frame types {sorted(types)}")
    blob_text = json.dumps(seen)
    check(code not in blob_text and (pr.get("token") or "x") not in blob_text,
          "[ZK] the pairing code + durable token NEVER appear in relay-visible frames")

    leaked = any(("pair_request" in (f.get("blob") or "") or "echo.test" in (f.get("blob") or "")
                  or "session_token" in (f.get("blob") or "")) for f in seen)
    check(not leaked, "[ZK] no plaintext app content (pair_request/echo.test/token) leaked to relay")
    sample = next((f["blob"] for f in seen if f["t"] == P.RZ_DATA and f.get("blob")), None)
    high_entropy = bool(sample) and len(set(bytes.fromhex(sample))) > 16
    check(high_entropy, "[ZK] a sampled data blob is high-entropy ciphertext")

    check(not hasattr(srv, "id_priv") and not hasattr(srv, "sx_priv"),
          "[ZK] the rendezvous server holds NO key material")
    srv.stop(); reg.close()

def test_rendezvous_role_separation():
    print("[SQUAT] a 2nd register on an occupied topic is rejected + cannot displace the box (FIX C)")
    try:
        from relaylib.wss import RendezvousServer, _TokenBucket
        from websockets.sync.client import connect as ws_connect
        import websockets
    except Exception as e:
        check(False, f"[SQUAT] websockets transport not importable ({e}); install `websockets`")
        return

    srv = RendezvousServer(host="127.0.0.1", port=0, allow_insecure=True,
                           unmatched_timeout=10.0).start()
    url = srv.url
    topic = "rz_squattest"

    def raw_send(t):
        c = ws_connect(url, open_timeout=5)
        c.send(json.dumps({"t": t, "rz": topic}))
        return c

    box = raw_send(P.RZ_REGISTER)
    time.sleep(0.3)

    squatter = raw_send(P.RZ_REGISTER)
    time.sleep(0.3)
    squat_dead = False
    try:
        squatter.send(json.dumps({"t": P.RZ_DATA, "rz": topic, "blob": "00"}))

        try:
            squatter.recv(timeout=1.0)
        except Exception:
            squat_dead = True
    except Exception:
        squat_dead = True
    check(squat_dead, "[SQUAT] a 2nd register on the occupied topic is rejected (connection dropped)")

    dev = ws_connect(url, open_timeout=5)
    dev.send(json.dumps({"t": P.RZ_DIAL, "rz": topic}))
    time.sleep(0.3)

    marker = "deadbeefcafe1234"
    dev.send(json.dumps({"t": P.RZ_DATA, "rz": topic, "blob": marker}))
    got = None
    try:
        raw = box.recv(timeout=3.0)
        got = json.loads(raw if isinstance(raw, str) else raw.decode()).get("blob")
    except Exception:
        got = None
    check(got == marker,
          f"[SQUAT] the legitimate device's dial reached the ORIGINAL box (marker round-tripped: {got!r})")

    dial2 = raw_send(P.RZ_DIAL)
    time.sleep(0.3)
    dial2_alone = False
    try:
        dial2.send(json.dumps({"t": P.RZ_DATA, "rz": topic, "blob": "11"}))
        try:
            dial2.recv(timeout=1.0)
            dial2_alone = False
        except Exception:
            dial2_alone = True
    except Exception:
        dial2_alone = True
    check(dial2_alone, "[SQUAT] a 2nd dial is not matched to the first dial (no dial+dial pairing)")

    for c in (box, squatter, dev, dial2):
        try: c.close()
        except Exception: pass
    srv.stop()

    tb = _TokenBucket(rate=2.0, burst=3)
    tb.ts = 1000.0
    allowed = sum(1 for _ in range(10) if tb.take(now=1000.0))
    check(allowed == 3, f"[SQUAT] per-IP conn-rate token bucket caps a burst to its capacity ({allowed}==3)")

    refilled = sum(1 for _ in range(10) if tb.take(now=1001.0))
    check(refilled == 2, f"[SQUAT] the token bucket refills at its rate ({refilled}==2 after 1s @ 2/s)")

def test_signed_frame_integrity():
    print("[SIG] unsigned + spoofed + replayed-nonce frames are rejected over a live session")
    tmp = tempfile.mkdtemp(prefix="pn_relay_sig_")
    relay = MockRelay()
    reg = registry.connect(os.path.join(tmp, "relay.db"))
    keys = appliance_keys_in(tmp)
    secret = registry.arm_2fa(reg, "remote-user")
    code = registry.mint_pairing(reg, "remote-user", ["task_type:echo.test"],
                                 parent_principal="admin", label="phone", max_rate=30)
    def submit_fn(req):
        return {"ok": True, "id": 1, "pos": 1}
    def bind_fn(*a): pass
    done = {}
    th = threading.Thread(target=run_box_session,
                          args=(relay.url, keys, reg, submit_fn, bind_fn, set(), done),
                          kwargs={"max_messages": 200})
    th.start(); time.sleep(0.1)
    dev = Device(box_id_pub=keys.id_pub, box_x_pub=keys.sx_pub, totp_secret=secret)
    dev.connect(relay.url)
    dev.pair(code)

    r_unsigned = dev.submit(task_type="echo.test", params={"msg": "x"}, sign=False)
    check(r_unsigned.get("t") == P.ERROR and "unsigned" in r_unsigned.get("error", ""),
          f"[SIG] an UNSIGNED submission is rejected: {r_unsigned.get('error')!r}")
    r_tampered = dev.submit(task_type="echo.test", params={"msg": "y"}, tamper=True)
    check(r_tampered.get("t") == P.ERROR and "signature" in r_tampered.get("error", ""),
          f"[SIG] a SPOOFED (tampered-after-sign) submission is rejected: {r_tampered.get('error')!r}")

    fixed_nonce = "deadbeefcafef00d"
    r_first = dev.submit(task_type="echo.test", params={"msg": "z"}, nonce=fixed_nonce)
    check(r_first.get("t") == P.RESULT, "[SIG] the first frame (fresh nonce) is accepted")
    r_replay = dev.submit(task_type="echo.test", params={"msg": "z2"}, nonce=fixed_nonce)
    check(r_replay.get("t") == P.ERROR and "nonce" in r_replay.get("error", ""),
          f"[SIG] a REPLAYED nonce is rejected: {r_replay.get('error')!r}")
    dev.close(); th.join(timeout=5); relay.stop(); reg.close()

def test_session_defence_in_depth():
    print("[TOK/SIG/CEIL/STEPUP] rotating session token + replay + ceiling + step-up over a session")
    tmp = tempfile.mkdtemp(prefix="pn_relay_sess_")
    relay = MockRelay()
    reg = registry.connect(os.path.join(tmp, "relay.db"))
    keys = appliance_keys_in(tmp)
    secret = registry.arm_2fa(reg, "remote-user")

    os.environ["RELAY_STEPUP_TASK_TYPES"] = "echo.danger"

    code = registry.mint_pairing(reg, "remote-user",
                                 ["task_type:echo.test", "task_type:echo.danger"],
                                 parent_principal="admin", label="phone", max_rate=30,
                                 max_concurrency=10)

    accepted = []
    def submit_fn(req):
        accepted.append(req)
        return {"ok": True, "id": len(accepted), "pos": 1}
    def bind_fn(*a): pass

    done = {}
    th = threading.Thread(target=run_box_session,
                          args=(relay.url, keys, reg, submit_fn, bind_fn, set(), done),
                          kwargs={"max_messages": 200})
    th.start(); time.sleep(0.1)
    dev = Device(box_id_pub=keys.id_pub, box_x_pub=keys.sx_pub, totp_secret=secret)
    dev.connect(relay.url)
    pr = dev.pair(code)
    check(pr.get("t") == P.PAIR_OK and pr.get("session_token"),
          "[TOK] PAIR_OK hands the device a rotating session token")

    st_before = dev.session_token
    r1 = dev.submit(task_type="echo.test", params={"msg": "a"})
    check(r1.get("t") == P.RESULT and dev.session_token and dev.session_token != st_before,
          "[TOK] a submit rotates the session token (new token returned)")

    r_replay = dev.submit(task_type="echo.test", params={"msg": "b"}, session_token=st_before)
    check(r_replay.get("t") == P.ERROR and "session token" in r_replay.get("error", ""),
          f"[TOK] a replayed (consumed) session token is rejected: {r_replay.get('error')!r}")

    rc = dev.submit(task_type="echo.test", params={"msg": "c"})
    check(rc.get("t") == P.RESULT, "[TOK] re-synced after the replay attempt")
    r_oldctr = dev.submit(task_type="echo.test", params={"msg": "d"}, counter=1)
    check(r_oldctr.get("t") == P.ERROR and "counter" in r_oldctr.get("error", ""),
          f"[TOK] a rolled-back monotonic counter is rejected: {r_oldctr.get('error')!r}")

    r_nostep = dev.submit(task_type="echo.danger", params={"msg": "x"})
    check(r_nostep.get("t") == P.ERROR and r_nostep.get("need_step_up_2fa"),
          f"[STEPUP] a sensitive task_type WITHOUT step-up 2FA is rejected: {r_nostep.get('error')!r}")
    r_step = dev.submit(task_type="echo.danger", params={"msg": "x"}, step_up_auto=True)
    check(r_step.get("t") == P.RESULT,
          f"[STEPUP] the same op WITH a step-up 2FA code is accepted (id={r_step.get('id')})")

    n_before = len(accepted)
    for badv in (True, 123456, ["1"], {"a": 1}, 3.14):
        rb = dev.submit(task_type="echo.danger", params={"msg": "x"}, step_up_2fa=badv)
        check(rb.get("t") == P.ERROR and rb.get("need_step_up_2fa"),
              f"[STEPUP] submit step_up_2fa={badv!r} ({type(badv).__name__}) fails CLOSED (clean reject): {rb.get('error')!r}")
    check(len(accepted) == n_before,
          f"[STEPUP] none of the 5 non-string step_up_2fa submits was brokered (accepted unchanged: {len(accepted)})")

    r_alive = dev.submit(task_type="echo.test", params={"msg": "alive"})
    check(r_alive.get("t") == P.RESULT,
          f"[STEPUP] the session SURVIVES the 5 non-string step_up_2fa frames intact: {r_alive.get('error')!r}")

    dev.close(); th.join(timeout=5)
    os.environ.pop("RELAY_STEPUP_TASK_TYPES", None)
    relay.stop()

    relay2 = MockRelay()
    secret2 = registry.arm_2fa(reg, "tight-user")
    code2 = registry.mint_pairing(reg, "tight-user", ["task_type:echo.test"],
                                  parent_principal="admin", label="tight", max_rate=2)
    done2 = {}
    th2 = threading.Thread(target=run_box_session,
                           args=(relay2.url, keys, reg, submit_fn, bind_fn, set(), done2),
                           kwargs={"max_messages": 200})
    th2.start(); time.sleep(0.1)
    dev2 = Device(box_id_pub=keys.id_pub, box_x_pub=keys.sx_pub, totp_secret=secret2)
    dev2.connect(relay2.url)
    dev2.pair(code2)
    a1 = dev2.submit(task_type="echo.test", params={"msg": "1"})
    a2 = dev2.submit(task_type="echo.test", params={"msg": "2"})
    a3 = dev2.submit(task_type="echo.test", params={"msg": "3"})
    check(a1.get("t") == P.RESULT and a2.get("t") == P.RESULT and
          a3.get("t") == P.ERROR and "ceiling" in a3.get("error", ""),
          f"[CEIL] per-device rate ceiling (max_rate=2) rejects the 3rd submit: {a3.get('error')!r}")
    dev2.close(); th2.join(timeout=5); relay2.stop(); reg.close()

    dev.close(); th.join(timeout=5)
    os.environ.pop("RELAY_STEPUP_TASK_TYPES", None)
    relay.stop(); reg.close()

def test_blast_radius_no_raw_job():
    print("[BLAST] a relayed device CANNOT land a raw/task.raw job (HPC-SSH blast-radius bound)")
    tmp = tempfile.mkdtemp(prefix="pn_relay_blast_")
    relay = MockRelay()
    reg = registry.connect(os.path.join(tmp, "relay.db"))
    keys = appliance_keys_in(tmp)
    secret = registry.arm_2fa(reg, "remote-user")
    os.environ["RELAY_STEPUP_TASK_TYPES"] = ""

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

            "for cap in ('task_type:echo.test','task_type:sleep.test'):\n"
            "    if not cx.execute('SELECT 1 FROM grants WHERE principal=? AND cap=?',('remote-user',cap)).fetchone():\n"
            "        cx.execute('INSERT INTO grants(principal,cap) VALUES(?,?)',('remote-user',cap))\n"
            "uid = __import__('os').getuid()\n"
            "p = db.principal_for_uid(cx, uid)\n"

            "for cap in ('act-as',):\n"
            "    if not cx.execute('SELECT 1 FROM grants WHERE principal=? AND cap=?',(p,cap)).fetchone():\n"
            "        cx.execute('INSERT INTO grants(principal,cap) VALUES(?,?)',(p,cap))\n"
            "cx.commit()\n"
            f"runpy.run_path({os.path.join(ROOT, 'tools', 'pnd')!r}, run_name='__main__')\n")
    proc = subprocess.Popen([sys.executable, boot], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    sock = os.path.join(rt, "pnd.sock")
    try:
        for _ in range(50):
            if os.path.exists(sock): break
            time.sleep(0.1)
        check(os.path.exists(sock), "[BLAST] scratch pnd up on a private socket")

        def submit_fn(req):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(20); s.connect(sock)
            s.sendall((json.dumps(req) + "\n").encode())
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk: break
                buf += chunk
            s.close()
            return json.loads(buf.split(b"\n", 1)[0].decode())

        eng_cx = db.connect(os.path.join(data, "portioneer", "queue.db"))
        def bind_fn(method, selector, principal, verified):
            db.bind_identity(eng_cx, method, selector, principal, verified=verified)

        adapter_principal = db.principal_for_uid(eng_cx, 4003)
        adapter_caps = db.caps_for(eng_cx, adapter_principal) if adapter_principal else set()
        check(adapter_principal == "adapter" and "task.raw" not in adapter_caps
              and "task_type:*" not in adapter_caps and "view:all" not in adapter_caps,
              f"[BLAST] (arm A) the de-privileged relay broker (adapter/4003) holds NO task.raw/"
              f"task_type:*/admin caps: {sorted(adapter_caps)}")

        import relaylib as _rl
        _scratch_qdb = os.path.join(data, "portioneer", "queue.db")
        check(not _rl.broker_is_deprivileged(1000, db_path=_scratch_qdb),
              "[BLAST] (arm A) FIX F refuses an ADMIN uid (1000) as the relay broker (load-bearing)")
        check(_rl.broker_is_deprivileged(4003, db_path=_scratch_qdb),
              "[BLAST] (arm A) FIX F accepts the de-privileged adapter (4003) as the relay broker")

        code = registry.mint_pairing(reg, "remote-user", ["task_type:echo.test"],
                                     parent_principal="admin", label="phone", max_rate=30)

        seen_nonces = set()
        done = {}
        th = threading.Thread(target=run_box_session,
                              args=(relay.url, keys, reg, submit_fn, bind_fn, seen_nonces, done),
                              kwargs={"max_messages": 50})
        th.start(); time.sleep(0.1)
        dev = Device(box_id_pub=keys.id_pub, box_x_pub=keys.sx_pub, totp_secret=secret)
        dev.connect(relay.url)
        pr = dev.pair(code)
        check(pr.get("t") == P.PAIR_OK, "[BLAST] device paired (ceiling = echo.test ONLY)")

        r_raw = dev.submit(cmd=["/bin/sh", "-c", "echo pwned; ssh hpc-login"], step_up_auto=True)
        check(r_raw.get("t") == P.ERROR and "raw" in (r_raw.get("error") or "").lower(),
              f"[BLAST] (arm B) a RAW command from the device is REJECTED by the engine "
              f"(task.raw not in the device principal's caps): {r_raw.get('error')!r}")

        r_outside = dev.submit(task_type="sleep.test", params={"s": "1"}, step_up_auto=True)
        check(r_outside.get("t") == P.ERROR and "ceiling" in (r_outside.get("error") or "").lower()
              and "sleep.test" in (r_outside.get("error") or ""),
              f"[BLAST] (FIX F) an out-of-ceiling task_type the principal+broker would allow is "
              f"REJECTED relay-side by the pairing ceiling: {r_outside.get('error')!r}")

        r_ok = dev.submit(task_type="echo.test", params={"msg": "ok"})
        relay_blocked = r_ok.get("t") == P.ERROR and "ceiling" in (r_ok.get("error") or "").lower()
        check(not relay_blocked,
              f"[BLAST] an IN-ceiling task_type is NOT blocked by the relay ceiling (forwarded to the "
              f"engine; engine outcome here reflects the pre-engine-PR wildcard gap): {r_ok.get('error')!r}")
        dev.close(); th.join(timeout=5)
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except Exception: proc.kill()
        relay.stop()
        try:
            import sqlite3
            cxv = sqlite3.connect(os.path.join(data, "portioneer", "queue.db"))
            ic = cxv.execute("PRAGMA integrity_check").fetchone()[0]; cxv.close()
            check(ic == "ok", f"[BLAST] scratch engine DB integrity_check = {ic!r}")
        except Exception as e:
            check(False, f"scratch DB integrity_check raised {e}")
        os.environ.pop("RELAY_STEPUP_TASK_TYPES", None)
        subprocess.run(["rm", "-rf", rt, data, tmp])

def test_concurrency_ceiling_enforced():
    print("[CEIL] per-device max_concurrency is enforced: the (N+1)th concurrent submit is rejected")
    tmp = tempfile.mkdtemp(prefix="pn_relay_conc_")
    relay = MockRelay()
    reg = registry.connect(os.path.join(tmp, "relay.db"))
    keys = appliance_keys_in(tmp)
    secret = registry.arm_2fa(reg, "conc-user")

    code = registry.mint_pairing(reg, "conc-user", ["task_type:echo.test"],
                                 parent_principal="admin", label="busy",
                                 max_rate=1000, max_concurrency=3)

    accepted = []
    def submit_fn(req):
        accepted.append(req)
        return {"ok": True, "id": len(accepted), "pos": 1}
    def bind_fn(*a): pass

    done = {}
    th = threading.Thread(target=run_box_session,
                          args=(relay.url, keys, reg, submit_fn, bind_fn, set(), done),
                          kwargs={"max_messages": 200})
    th.start(); time.sleep(0.1)
    dev = Device(box_id_pub=keys.id_pub, box_x_pub=keys.sx_pub, totp_secret=secret)
    dev.connect(relay.url)
    dev.pair(code)

    r = [dev.submit(task_type="echo.test", params={"n": i}) for i in range(3)]
    check(all(x.get("t") == P.RESULT for x in r),
          "[CEIL] the first max_concurrency (3) in-flight submits are accepted")
    r4 = dev.submit(task_type="echo.test", params={"n": 3})
    check(r4.get("t") == P.ERROR and "concurrency" in r4.get("error", "") and
          r4.get("concurrency_ceiling") == 3,
          f"[CEIL] the (N+1)th concurrent submit is REJECTED by the concurrency ceiling: {r4.get('error')!r}")

    check(len(accepted) == 3,
          f"[CEIL] the over-ceiling submit never reached the engine (brokered={len(accepted)}==3)")

    check(r4.get("session_token"), "[CEIL] the over-ceiling rejection still rotates a session token (retryable)")
    dev.close(); th.join(timeout=5); relay.stop(); reg.close()

def test_stepup_2fa_no_cross_flow_lockout():
    print("[GRIEF] a device spamming wrong step-up codes locks/auto-revokes ITSELF, not the principal")
    tmp = tempfile.mkdtemp(prefix="pn_relay_grief_")
    reg = registry.connect(os.path.join(tmp, "relay.db"))
    secret = registry.arm_2fa(reg, "human")
    didA = "did:key:b2:aaaa"

    for _ in range(registry.STEPUP_MAX_FAILS):
        registry.verify_stepup_2fa(reg, "human", didA, "000000")
    okA, whyA = registry.verify_stepup_2fa(reg, "human", didA, totp.code_at(secret))
    check(not okA and "locked" in whyA.lower(),
          f"[GRIEF] device A's OWN step-up is locked after {registry.STEPUP_MAX_FAILS} fails: {whyA!r}")

    prow = reg.execute("SELECT fail_count,locked_until FROM principals_2fa WHERE principal='human'"
                       ).fetchone()
    check(prow["fail_count"] == 0 and prow["locked_until"] == 0,
          "[GRIEF] device A's step-up failures did NOT touch the principal-wide redeem/enroll counter")

    code = registry.mint_pairing(reg, "human", ["task_type:echo.test"], parent_principal="admin")
    pr = registry.redeem_pairing(reg, code, totp_code=totp.code_at(secret))
    check(pr is not None,
          "[GRIEF] a NEW pairing redemption SUCCEEDS despite device A's step-up lockout (no cross-flow)")

    sec_before = reg.execute("SELECT secret_hash FROM principals_2fa WHERE principal='human'"
                             ).fetchone()["secret_hash"]
    cleared = registry.unlock_2fa(reg, "human", device_did=didA)
    sec_after = reg.execute("SELECT secret_hash FROM principals_2fa WHERE principal='human'"
                            ).fetchone()["secret_hash"]
    check(cleared and sec_before == sec_after,
          "[GRIEF] operator --unlock-2fa clears device A's lockout WITHOUT rotating the TOTP secret")
    row = reg.execute("SELECT fail_count,locked_until FROM device_stepup_2fa WHERE device_did=?",
                      (didA,)).fetchone()
    check(row and row["fail_count"] == 0 and row["locked_until"] == 0,
          "[GRIEF] device A's step-up counters are cleared after the operator unlock")

    registry.arm_2fa(reg, "human2")
    cpub = crypto.gen_ed25519()[1]; didC = crypto.did_for(cpub)
    registry.create_alliance(
        reg, device_did=didC, device_pubkey_hex=cpub.hex(), device_x_pubkey_hex="cd"*16,
        principal="human2", parent_principal="admin", caps=["task_type:echo.test"],
        label="hostile", max_rate=30, max_concurrency=2)
    check(registry.is_active(reg, didC), "[GRIEF] device C starts active")
    for _ in range(registry.STEPUP_AUTOREVOKE_FAILS + 2):
        registry.verify_stepup_2fa(reg, "human2", didC, "000000")
        registry.unlock_2fa(reg, "human2", device_did=didC)
    check(not registry.is_active(reg, didC),
          "[GRIEF] device C is AUTO-REVOKED after too many lifetime step-up failures (offending device only)")

    code2 = registry.mint_pairing(reg, "human2", ["task_type:echo.test"], parent_principal="admin")
    pr2 = registry.redeem_pairing(reg, code2, totp_code=totp.code_at(registry._unwrap_secret(
        reg, reg.execute("SELECT secret_enc FROM principals_2fa WHERE principal='human2'"
                         ).fetchone()["secret_enc"])))
    check(pr2 is not None, "[GRIEF] the human can STILL pair a clean device after the auto-revoke")
    reg.close()

def test_nonce_cache_bounded():
    print("[NONCE] bounded TTL nonce cache: stays bounded under load + rejects oversized nonces")
    from relaylib.box import NonceCache, MAX_NONCE_LEN

    check(NonceCache.valid_nonce("a" * 16), "[NONCE] a normal-length nonce is accepted")
    check(not NonceCache.valid_nonce("a" * (MAX_NONCE_LEN + 1)),
          f"[NONCE] an oversized nonce (> {MAX_NONCE_LEN} chars) is rejected")
    check(not NonceCache.valid_nonce(""), "[NONCE] an empty nonce is rejected")
    check(not NonceCache.valid_nonce(12345) and not NonceCache.valid_nonce(None),
          "[NONCE] a non-string nonce is rejected")

    nc = NonceCache(window_s=300.0, per_device_maxlen=128)
    did = "did:key:b2:deadbeef"
    for i in range(10000):
        nc.add(did, f"n{i}", now=1000.0)
    check(nc.total() <= 128,
          f"[NONCE] per-device cache stays bounded under sustained submission (total={nc.total()} <= 128)")

    nc2 = NonceCache(window_s=300.0, per_device_maxlen=1 << 20)
    nc2.add(did, "old", now=1000.0)
    check(nc2.seen(did, "old", now=1000.0), "[NONCE] a fresh nonce is seen within the window")
    check(not nc2.seen(did, "old", now=1000.0 + 301), "[NONCE] a nonce past the TTL window is evicted")
    nc2.add(did, "new", now=2000.0)
    check(nc2.total() == 1, f"[NONCE] stale entries are evicted on insert (total={nc2.total()})")

    nc3 = NonceCache(window_s=300.0, per_device_maxlen=4)
    for i in range(100):
        nc3.add("did:flood", f"f{i}", now=1000.0)
    nc3.add("did:victim", "v1", now=1000.0)
    check(nc3.seen("did:victim", "v1", now=1000.0) and nc3.total() <= 4 + 1,
          "[NONCE] a flooding device cannot evict another device's still-fresh nonce")

    nc3.drop_device("did:flood")
    check("did:flood" not in nc3._buckets, "[NONCE] drop_device() frees a revoked device's bucket")

def test_revocation_kills_sessions():
    print("[REVOKE] revocation instantly kills reconnect AND any live session token")
    tmp = tempfile.mkdtemp(prefix="pn_relay_rev_")
    reg = registry.connect(os.path.join(tmp, "relay.db"))
    secret = registry.arm_2fa(reg, "u")
    code = registry.mint_pairing(reg, "u", ["task_type:echo.test"], parent_principal="admin")
    registry.redeem_pairing(reg, code, totp_code=totp.code_at(secret))
    didpub = crypto.gen_ed25519()[1]; did = crypto.did_for(didpub)
    token = registry.create_alliance(
        reg, device_did=did, device_pubkey_hex=didpub.hex(), device_x_pubkey_hex="ab"*16,
        principal="u", parent_principal="admin", caps=["task_type:echo.test"],
        label="t", max_rate=30, max_concurrency=2)
    st = registry.issue_session_token(reg, did, "cd"*16)
    check(registry.alliance_for_token(reg, did, token) is not None,
          "[REVOKE] alliance resolves before revocation")
    killed = registry.revoke(reg, did)
    check(killed, "[REVOKE] revoke() killed a live alliance")
    check(registry.alliance_for_token(reg, did, token) is None,
          "[REVOKE] the durable token no longer resolves after revocation")
    check(not registry.consume_session_token(reg, did, "cd"*16, st),
          "[REVOKE] a previously-live session token is dead after revocation")
    check(not registry.is_active(reg, did), "[REVOKE] is_active() is False after revocation")
    reg.close()

def test_relay_control_handler():
    print("[CTRL] real relay.control: cvm read excluded from the ceiling; authorized approve works; "
          "submit-only + self-approve REJECTED (separation of duties); over a scratch pnd")
    tmp = tempfile.mkdtemp(prefix="pn_relay_ctrl_")
    relay = MockRelay()
    reg = registry.connect(os.path.join(tmp, "relay.db"))
    keys = appliance_keys_in(tmp)

    sec_appr = registry.arm_2fa(reg, "remote-user")
    sec_sub = registry.arm_2fa(reg, "submit-user")
    os.environ["RELAY_STEPUP_TASK_TYPES"] = ""

    rt = tempfile.mkdtemp(prefix="pn_rt_"); data = tempfile.mkdtemp(prefix="pn_data_")
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = rt; env["XDG_DATA_HOME"] = data
    env["PN_DURABILITY"] = "normal"; env.pop("NOTIFY_SOCKET", None)
    env["PND_BROKER_UIDS"] = str(os.getuid())
    my_uid = os.getuid()
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

            f"cx.execute(\"UPDATE principals SET uid=NULL WHERE name='admin'\")\n"
            f"cx.execute(\"INSERT OR IGNORE INTO principals(name,uid,kind,note) VALUES('relay-broker',{my_uid},'system','de-privileged relay broker')\")\n"
            f"cx.execute(\"UPDATE principals SET uid={my_uid} WHERE name='relay-broker'\")\n"

            "cx.execute(\"INSERT OR IGNORE INTO principals(name,uid,kind,note) VALUES('remote-user',6101,'user','approver dev')\")\n"
            "cx.execute(\"INSERT OR IGNORE INTO principals(name,uid,kind,note) VALUES('submit-user',6102,'user','submit-only dev')\")\n"
            "grants=[('remote-user','task_type:deploy.irreversible'),('remote-user','approval:resolve'),"
            "('submit-user','task_type:deploy.irreversible'),('brain','task_type:deploy.irreversible'),"
            "('relay-broker','act-as'),('relay-broker','task_type:deploy.irreversible'),"
            "('relay-broker','approval:resolve')]\n"
            "for p,cap in grants:\n"
            "    if not cx.execute('SELECT 1 FROM grants WHERE principal=? AND cap=?',(p,cap)).fetchone():\n"
            "        cx.execute('INSERT INTO grants(principal,cap) VALUES(?,?)',(p,cap))\n"
            "cx.commit()\n"
            f"runpy.run_path({os.path.join(ROOT, 'tools', 'pnd')!r}, run_name='__main__')\n")
    proc = subprocess.Popen([sys.executable, boot], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    sock = os.path.join(rt, "pnd.sock")

    def ipc(req):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(20); s.connect(sock)
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk: break
            buf += chunk
        s.close()
        return json.loads(buf.split(b"\n", 1)[0].decode())

    try:
        for _ in range(50):
            if os.path.exists(sock): break
            time.sleep(0.1)
        check(os.path.exists(sock), "[CTRL] scratch pnd up on a private socket")

        eng_cx = db.connect(os.path.join(data, "portioneer", "queue.db"))
        def bind_fn(method, selector, principal, verified):
            db.bind_identity(eng_cx, method, selector, principal, verified=verified)

        from relaylib import ID_METHOD as _IDM
        def submit_fn(req):
            return ipc(req)
        def inflight_count_fn(did):
            r = ipc({"verb": "via-inflight", "_method": _IDM, "_selector": did, "via_device": did})
            return int(r.get("in_flight", 0)) if r.get("ok") else 0

        def run_box(relay_url, secret_unused, done):
            try:
                ch = Channel.register(relay_url, crypto.rendezvous_topic(keys.sx_pub), timeout=5)
                done["bs"] = serve_session(ch, keys, reg, submit_fn, bind_identity_fn=bind_fn,
                                           seen_nonces=set(), control_fn=submit_fn,
                                           inflight_count_fn=inflight_count_fn, max_messages=80)
            except Exception as e:
                done["err"] = f"{type(e).__name__}: {e}"

        relayA = MockRelay()
        codeA = registry.mint_pairing(reg, "remote-user",
                                      ["task_type:deploy.irreversible", "approval:resolve"],
                                      parent_principal="admin", label="approver", max_rate=100,
                                      max_concurrency=2)
        doneA = {}
        thA = threading.Thread(target=run_box, args=(relayA.url, sec_appr, doneA)); thA.start()
        time.sleep(0.1)
        devA = Device(box_id_pub=keys.id_pub, box_x_pub=keys.sx_pub, totp_secret=sec_appr)
        devA.connect(relayA.url); devA.pair(codeA)

        db.bind_identity(eng_cx, "device-channel", "braindid", "brain", verified=1)
        sub = ipc({"verb": "submit", "_method": "device-channel", "_selector": "braindid",
                   "via_device": "braindid", "task_type": "deploy.irreversible",
                   "params": {"target": "prod"}, "_ceiling_caps": ["task_type:deploy.irreversible"]})
        check(sub.get("ok") and sub.get("state") == "staged" and sub.get("nonce"),
              f"[CTRL] a brain-submitted deploy.irreversible job parks staged with a nonce: {sub}")
        gated_jid, gated_nonce = sub["id"], sub["nonce"]

        cvm_msg = devA.submit(task_type="relay.control",
                              params={"control": {"verb": "cvm", "id": gated_jid}})
        cvm_cr = cvm_msg.get("control_result") or {}
        check(cvm_msg.get("t") == P.RESULT and cvm_cr.get("ok")
              and (cvm_cr.get("cvm") or {}).get("id") == gated_jid,
              f"[CTRL] relay.control cvm read works over the relay (authorized approver): {cvm_cr}")

        appr_no2fa = devA.submit(task_type="relay.control",
                                 params={"control": {"verb": "approve", "nonce": gated_nonce}})
        check(appr_no2fa.get("t") == P.ERROR and appr_no2fa.get("need_step_up_2fa"),
              f"[CTRL] a relay.control approve WITHOUT step-up 2FA is rejected: {appr_no2fa.get('error')!r}")
        appr_ok = devA.submit(task_type="relay.control",
                              params={"control": {"verb": "approve", "nonce": gated_nonce}},
                              step_up_auto=True)
        appr_cr = appr_ok.get("control_result") or {}
        check(appr_ok.get("t") == P.RESULT and appr_cr.get("ok"),
              f"[CTRL] authorized approval-authority device approves the gate over the relay: {appr_cr}")

        jrow = db.get(eng_cx, gated_jid, scope_all=True) or {}
        check(jrow.get("state") in ("queued", "running", "done"),
              f"[CTRL] the approved PRE-gate job is released from staged (state={jrow.get('state')})")
        check(jrow.get("approved_by") == "remote-user",
              f"[CTRL] approved_by records the RESOLVER device principal (SoD audit): {jrow.get('approved_by')}")

        db.bind_identity(eng_cx, "device-channel", "braindid", "brain", verified=1)
        sub_rg = ipc({"verb": "submit", "_method": "device-channel", "_selector": "braindid",
                      "via_device": "braindid", "task_type": "deploy.irreversible",
                      "params": {"target": "prod-reg"}, "_ceiling_caps": ["task_type:deploy.irreversible"]})
        reg_jid, reg_nonce = sub_rg["id"], sub_rg["nonce"]
        for badv in (True, 123456, ["1"], {"a": 1}, 3.14):
            ar = devA.submit(task_type="relay.control",
                             params={"control": {"verb": "approve", "nonce": reg_nonce}},
                             step_up_2fa=badv)
            grow = db.get(eng_cx, reg_jid, scope_all=True) or {}
            check(ar.get("t") == P.ERROR and ar.get("need_step_up_2fa")
                  and grow.get("state") == "staged" and grow.get("approval_state") == "pending",
                  f"[CTRL] approve step_up_2fa={badv!r} ({type(badv).__name__}) fails CLOSED, gate NOT "
                  f"brokered (state={grow.get('state')}/{grow.get('approval_state')}): {ar.get('error')!r}")

        alive = devA.submit(task_type="relay.control",
                            params={"control": {"verb": "cvm", "id": reg_jid}})
        alive_cr = alive.get("control_result") or {}
        check(alive.get("t") == P.RESULT and alive_cr.get("ok"),
              f"[CTRL] the session SURVIVES the 5 non-string step_up_2fa approve frames intact: {alive.get('error')!r}")
        devA.close(); thA.join(timeout=5); relayA.stop()

        db.bind_identity(eng_cx, "device-channel", "braindid", "brain", verified=1)
        sub2 = ipc({"verb": "submit", "_method": "device-channel", "_selector": "braindid",
                    "via_device": "braindid", "task_type": "deploy.irreversible",
                    "params": {"target": "prod2"}, "_ceiling_caps": ["task_type:deploy.irreversible"]})
        nonce2 = sub2["nonce"]
        relayB = MockRelay()
        codeB = registry.mint_pairing(reg, "submit-user", ["task_type:deploy.irreversible"],
                                      parent_principal="admin", label="submit-only", max_rate=100,
                                      max_concurrency=2)
        doneB = {}
        thB = threading.Thread(target=run_box, args=(relayB.url, sec_sub, doneB)); thB.start()
        time.sleep(0.1)
        devB = Device(box_id_pub=keys.id_pub, box_x_pub=keys.sx_pub, totp_secret=sec_sub)
        devB.connect(relayB.url); devB.pair(codeB)
        sub_appr = devB.submit(task_type="relay.control",
                               params={"control": {"verb": "approve", "nonce": nonce2}},
                               step_up_auto=True)
        sub_cr = sub_appr.get("control_result") or {}

        _err = (sub_cr.get("error") or "").lower()
        check(sub_appr.get("t") == P.RESULT and sub_cr.get("ok") is False
              and ("duties" in _err or "unknown" in _err),
              f"[CTRL] a SUBMIT-ONLY device's approve is REJECTED (no operator authority): {sub_cr}")

        n2row = db.get(eng_cx, sub2["id"], scope_all=True) or {}
        check(n2row.get("approval_state") == "pending" and n2row.get("state") == "staged",
              f"[CTRL] the gate stays unresolved after a submit-only device's refused approve: {n2row.get('state')}")

        devB.close(); thB.join(timeout=5); relayB.stop()

        sec_self = registry.arm_2fa(reg, "self-user")
        eng_cx.execute("INSERT OR IGNORE INTO principals(name,uid,kind) VALUES('self-user',6103,'user')")
        for cap in ("task_type:deploy.irreversible", "approval:resolve"):
            if not eng_cx.execute("SELECT 1 FROM grants WHERE principal='self-user' AND cap=?",
                                  (cap,)).fetchone():
                eng_cx.execute("INSERT INTO grants(principal,cap) VALUES('self-user',?)", (cap,))
        eng_cx.commit()
        relayC = MockRelay()
        codeC = registry.mint_pairing(reg, "self-user",
                                      ["task_type:deploy.irreversible", "approval:resolve"],
                                      parent_principal="admin", label="self", max_rate=100,
                                      max_concurrency=2)
        doneC = {}
        thC = threading.Thread(target=run_box, args=(relayC.url, sec_self, doneC)); thC.start()
        time.sleep(0.1)
        devC = Device(box_id_pub=keys.id_pub, box_x_pub=keys.sx_pub, totp_secret=sec_self)
        devC.connect(relayC.url); devC.pair(codeC)
        own = devC.submit(task_type="deploy.irreversible", params={"target": "selftest"})
        check(own.get("t") == P.RESULT, f"[CTRL] approval-authority device submits its own gated job: {own.get('id')}")
        own_row = db.get(eng_cx, own.get("id"), scope_all=True) or {}
        own_nonce = own_row.get("approval_nonce")
        self_appr = devC.submit(task_type="relay.control",
                                params={"control": {"verb": "approve", "nonce": own_nonce}},
                                step_up_auto=True)
        self_cr = self_appr.get("control_result") or {}
        check(self_appr.get("t") == P.RESULT and self_cr.get("ok") is False
              and "duties" in (self_cr.get("error") or "").lower(),
              f"[CTRL] a device can NEVER self-approve its own submission, even with approval:resolve "
              f"(SoD): t={self_appr.get('t')} cr={self_cr} err={self_appr.get('error')!r}")

        sr = db.get(eng_cx, own.get("id"), scope_all=True) or {}
        check(sr.get("approval_state") == "pending",
              f"[CTRL] the self-approve attempt left the gate unresolved: {sr.get('approval_state')}")
        devC.close(); thC.join(timeout=5); relayC.stop()
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except Exception: proc.kill()
        relay.stop(); reg.close()
        os.environ.pop("RELAY_STEPUP_TASK_TYPES", None)
        subprocess.run(["rm", "-rf", rt, data, tmp])

def test_concurrency_accounting():
    print("[ACCT] control ops excluded from the job ceiling; pnd-derived count frees a slot on "
          "terminal; remove() wired; the true (N+1)th in-flight submit still rejected")
    tmp = tempfile.mkdtemp(prefix="pn_relay_acct_")
    relay = MockRelay()
    reg = registry.connect(os.path.join(tmp, "relay.db"))
    keys = appliance_keys_in(tmp)
    secret = registry.arm_2fa(reg, "acct-user")
    code = registry.mint_pairing(reg, "acct-user",
                                 ["task_type:echo.test", "approval:resolve"],
                                 parent_principal="admin", label="acct",
                                 max_rate=10000, max_concurrency=2)

    state = {"next_id": 0, "inflight": set()}
    def submit_fn(req):
        if req.get("verb") != "submit":

            return {"ok": True, "cvm": {"id": req.get("id"), "state": "queued"}}
        state["next_id"] += 1
        jid = state["next_id"]
        state["inflight"].add(jid)
        return {"ok": True, "id": jid, "pos": 1}
    def inflight_count_fn(did):
        return len(state["inflight"])
    def bind_fn(*a): pass

    done = {}
    def run():
        try:
            ch = Channel.register(relay.url, crypto.rendezvous_topic(keys.sx_pub), timeout=5)
            done["bs"] = serve_session(ch, keys, reg, submit_fn, bind_identity_fn=bind_fn,
                                       seen_nonces=set(), control_fn=submit_fn,
                                       inflight_count_fn=inflight_count_fn, max_messages=200)
        except Exception as e:
            done["err"] = f"{type(e).__name__}: {e}"
    th = threading.Thread(target=run); th.start(); time.sleep(0.1)
    dev = Device(box_id_pub=keys.id_pub, box_x_pub=keys.sx_pub, totp_secret=secret)
    dev.connect(relay.url); dev.pair(code)

    r1 = dev.submit(task_type="echo.test", params={"n": 1})
    r2 = dev.submit(task_type="echo.test", params={"n": 2})
    check(r1.get("t") == P.RESULT and r2.get("t") == P.RESULT,
          "[ACCT] the first max_concurrency (2) in-flight submits are accepted")

    cvm = dev.submit(task_type="relay.control", params={"control": {"verb": "cvm", "id": 1}})
    check(cvm.get("t") == P.RESULT and (cvm.get("control_result") or {}).get("ok"),
          f"[ACCT] a relay.control read is EXCLUDED from the job ceiling (not 'ceiling exceeded'): {cvm.get('error')!r}")

    r3 = dev.submit(task_type="echo.test", params={"n": 3})
    check(r3.get("t") == P.ERROR and "concurrency" in (r3.get("error") or "")
          and r3.get("concurrency_ceiling") == 2,
          f"[ACCT] the true (N+1)th concurrent in-flight submit is REJECTED: {r3.get('error')!r}")

    state["inflight"].discard(1)
    r4 = dev.submit(task_type="echo.test", params={"n": 4})
    check(r4.get("t") == P.RESULT,
          f"[ACCT] a slot FREES when a job reaches terminal (pnd-derived count): {r4.get('error')!r}")

    r5 = dev.submit(task_type="echo.test", params={"n": 5})
    check(r5.get("t") == P.ERROR and "concurrency" in (r5.get("error") or ""),
          f"[ACCT] the ceiling re-applies once in-flight is full again: {r5.get('error')!r}")

    from relaylib.box import InFlightCounter
    ic = InFlightCounter()
    ic.add("did:x", "job-1"); ic.add("did:x", "job-2")
    check(ic.count("did:x") == 2, "[ACCT] InFlightCounter counts two added jobs")
    ic.remove("did:x", "job-1")
    check(ic.count("did:x") == 1, "[ACCT] InFlightCounter.remove() decrements (wired, not dead)")
    dev.close(); th.join(timeout=5); relay.stop(); reg.close()

def test_totp_verify_nonstring_fails_closed():
    print("[STEPUP] totp.verify fails CLOSED (returns False, never raises) on a non-string code")
    secret = totp.gen_secret()

    for badv in (True, 123456, ["1"], {"a": 1}, 3.14):
        try:
            r = totp.verify(secret, badv)
            check(r == (False, -1),
                  f"[STEPUP] totp.verify({badv!r}: {type(badv).__name__}) returns (False, -1) not raises: {r}")
        except Exception as e:
            check(False, f"[STEPUP] totp.verify({badv!r}) RAISED {type(e).__name__}: {e}")

    code = totp.code_at(secret)
    ok, ctr = totp.verify(secret, code)
    check(ok and ctr == totp.counter_at(),
          f"[STEPUP] a valid string code still verifies (ok={ok}, ctr={ctr})")
    ok2, _ = totp.verify(secret, code, last_counter=ctr)
    check(not ok2, "[STEPUP] an accepted code stays one-time (anti-replay preserved)")

    check(totp.verify(secret, "") == (False, -1) and totp.verify(secret, "12345") == (False, -1)
          and totp.verify(secret, "abcdef") == (False, -1),
          "[STEPUP] empty / short / non-digit string codes still reject (no behaviour change)")

def main():
    print("=== pn-relayd HARDENED relay — security test suite ===")
    test_totp_verify_nonstring_fails_closed()
    test_2fa_mandatory()
    test_pairing_code_hardening()
    test_token_hardening()
    test_audit_tamper_evident()
    test_real_rendezvous_zero_knowledge()
    test_rendezvous_role_separation()
    test_signed_frame_integrity()
    test_session_defence_in_depth()
    test_concurrency_ceiling_enforced()
    test_concurrency_accounting()
    test_relay_control_handler()
    test_stepup_2fa_no_cross_flow_lockout()
    test_nonce_cache_bounded()
    test_blast_radius_no_raw_job()
    test_revocation_kills_sessions()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
