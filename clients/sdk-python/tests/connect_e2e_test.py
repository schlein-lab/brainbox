#!/usr/bin/env python3

import os, sys, time, threading, tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
ENGINE = os.environ.get("PN_ENGINE") or os.path.normpath(os.path.join(ROOT, "..", "..", "engine"))
sys.path.insert(0, ENGINE)
COCKPIT = os.path.normpath(os.path.join(ROOT, "..", "..", "cockpit"))
sys.path.insert(0, COCKPIT)

from connectlib.client import ConnectClient
from connectlib.keystore import Keystore
from connectlib.transports import LanTransport, RelayTransport
from connectlib import contract
from connectlib.voice import utterance_to_verb

from mock.mock_pnd import MockPnd
from mock.mock_lan import MockLanServer

PASS, FAIL, SKIP = [], [], []

def relay_preflight():

    try:
        import importlib
        import relaylib
        import relaylib.totp as _totp
        from relaylib import registry as _registry
    except Exception as e:
        return False, f"relaylib import failed: {e!r}"
    if not hasattr(_registry, "arm_2fa"):
        return False, "relaylib.registry has no arm_2fa (needs the relay-hardened-2fa strand)"
    if not hasattr(_totp, "code_at"):
        return False, "relaylib.totp has no code_at (needs the relay-hardened-2fa strand)"
    return True, ""

def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not cond else ""))

def skip(name, reason=""):
    SKIP.append(name)
    print(f"  [SKIP] {name}" + (f"  ({reason})" if reason else ""))

def wait_for(pred, timeout=5.0, interval=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return False

def test_lan(pnd):
    print("\n== LAN / on-VM path (peercred identity) ==")
    rt = os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir())
    sock = os.path.join(tempfile.mkdtemp(), "pnd.sock")
    srv = MockLanServer(pnd, sock, uid_principal={os.getuid(): "owner"}).start()
    try:
        ks = Keystore(path=tempfile.mkdtemp())

        c1 = ConnectClient("home", keystore=ks, transport=LanTransport(sock), principal="owner")
        c2 = ConnectClient("home", keystore=Keystore(path=tempfile.mkdtemp()),
                           transport=LanTransport(sock), principal="owner")
        ok("LAN connect (no login)", c1.connect().get("ok"))
        c1.watch(); c2.watch()
        time.sleep(0.3)

        r = c1.type("summarise today")
        ok("type -> submit accepted", r.get("ok"))
        jid = r["id"]
        ok("plain task reaches both devices",
           wait_for(lambda: jid in c1.reality.jobs and jid in c2.reality.jobs))

        r2 = c1.attach("review this", paths=["/srv/share/report.pdf"])
        jid2 = r2["id"]
        ok("confirm task stages approval on device1",
           wait_for(lambda: any(c["id"] == jid2 for c in c1.pending_approvals())))
        ok("confirm task stages approval on device2 (one reality)",
           wait_for(lambda: any(c["id"] == jid2 for c in c2.pending_approvals())))
        cvm = next(c for c in c1.pending_approvals() if c["id"] == jid2)
        nonce = contract.nonce_of(cvm)
        ok("approval carries a server-minted nonce", bool(nonce))

        ar = c1.approve(cvm)
        ok("approve round-trips", ar.get("ok"))
        ok("approval clears on device1", wait_for(lambda: not any(c["id"] == jid2 for c in c1.pending_approvals())))
        ok("approval clears on device2 (cross-device clear)",
           wait_for(lambda: not any(c["id"] == jid2 for c in c2.pending_approvals())))

        ar2 = c1.transport.call(contract.approve(nonce))
        ok("idempotent re-approve is a safe no-op", ar2.get("ok"))

        cursor = c1.reality.last_event_id
        rep = c1.transport.call(contract.replay("owner", cursor))
        ok("replay after cursor returns only the delta",
           rep.get("ok") and all(e["event_id"] > cursor for e in rep["events"]))

        TOKEN = "DURABLE-SECRET-should-never-hit-the-wire"
        sent_bytes = {"buf": b""}
        real_sendall = None
        import socket as _socket

        class _SpySock(_socket.socket):
            def sendall(self, data, *a, **k):
                sent_bytes["buf"] += data
                return super().sendall(data, *a, **k)

        orig_connect = LanTransport._connect

        def _spy_connect(self, timeout):
            s = _SpySock(_socket.AF_UNIX, _socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(self.sock_path)
            return s

        LanTransport._connect = _spy_connect
        try:

            c1.transport.call({"verb": "cvm", "id": jid, "token": TOKEN, "principal": "owner"})
        finally:
            LanTransport._connect = orig_connect
        wire = sent_bytes["buf"].decode(errors="replace")
        ok("LAN wire carries NO durable token (peercred, never a URL/body token)", TOKEN not in wire,
           detail=wire)
        ok("LAN wire carries NO client-supplied principal (server-resolved)",
           '"principal"' not in wire, detail=wire)

        c1.disconnect(); c2.disconnect()
    finally:
        srv.stop()

RELAY_TESTS = [
    "2FA armed for principal", "OOB pairing code minted",
    "pair WITHOUT 2FA is rejected", "pair with WRONG 2FA is rejected",
    "pair with CORRECT 2FA -> PAIR_OK + durable token",
    "reconnect with stored token (no re-login)",
    "off-LAN SIGNED submit accepted by the box+pnd",
    "off-LAN confirm task stages on the LAN device too",
    "off-LAN client can read the CVM (relay.control broker)",
    "submit-only device approve is REJECTED (separation of duties)",
    "approval-authority device paired (--control tier)",
    "off-LAN approve round-trips (approval-authority device)",
    "off-LAN approve clears on the LAN device (cross-transport one reality)",
    "revoke at the box succeeds", "post-revoke submit rejected at the box",
]

def test_relay(pnd):
    print("\n== Relay / off-LAN path (REAL relaylib: handshake + mandatory 2FA + signed submit) ==")
    ready, reason = relay_preflight()
    if not ready:
        print(f"  relaylib (with totp/arm_2fa) not found at PN_ENGINE={ENGINE}; off-LAN 2FA suite "
              f"SKIPPED — needs an engine checkout containing the relay-hardened-2fa strand.")
        print(f"  reason: {reason}")
        for name in RELAY_TESTS:
            skip(name, "off-LAN 2FA suite needs relay-hardened relaylib on PN_ENGINE")
        return

    from mock.mock_box import MockBox
    box = MockBox(pnd).serve_forever()
    time.sleep(0.2)
    import relaylib.totp as totp

    secret = box.arm_2fa("owner")
    ok("2FA armed for principal", bool(secret))
    code = box.mint_pairing("owner", ["task_type:intake.message", "task_type:intake.attach"],
                            label="Chris laptop")
    ok("OOB pairing code minted", bool(code))

    ks = Keystore(path=tempfile.mkdtemp())
    ks.save_box_keys("home", relay_url=box.relay_url,
                     appliance_id_pubkey=box.appliance_id_pub_hex,
                     appliance_x_pubkey=box.appliance_x_pub_hex)

    def fresh_client():
        dk = ks.device_identity("home")
        tr = RelayTransport(relay_url=box.relay_url,
                            appliance_id_pub_hex=box.appliance_id_pub_hex,
                            appliance_x_pub_hex=box.appliance_x_pub_hex, device_keys=dk)
        return ConnectClient("home", keystore=ks, transport=tr)

    c = fresh_client()
    r_no = c.transport.pair(code, label="x", totp_code=None)
    ok("pair WITHOUT 2FA is rejected", r_no.get("t") == "error" and r_no.get("need_2fa"),
       detail=str(r_no))
    c.transport.close()

    c = fresh_client()
    r_bad = c.transport.pair(code, label="x", totp_code="000000")
    ok("pair with WRONG 2FA is rejected", r_bad.get("t") == "error")
    c.transport.close()

    c = fresh_client()
    good_code = totp.code_at(secret, time.time() + totp.STEP_S)
    r_ok = c.pair(code, totp_code=good_code, label="Chris laptop")
    ok("pair with CORRECT 2FA -> PAIR_OK + durable token",
       r_ok.get("t") == "pair_ok" and ks.is_paired("home"), detail=str(r_ok))
    paired_did = c.transport.did
    c.transport.close()

    c2 = fresh_client()
    st = c2.connect()
    ok("reconnect with stored token (no re-login)", st.get("ok") and st.get("mode") == "relay",
       detail=str(st))

    r = c2.type("from the road")
    ok("off-LAN SIGNED submit accepted by the box+pnd", r.get("ok"), detail=str(r))

    sock = os.path.join(tempfile.mkdtemp(), "pnd.sock")
    srv = MockLanServer(pnd, sock, uid_principal={os.getuid(): "owner"}).start()
    lan = ConnectClient("lan", keystore=Keystore(path=tempfile.mkdtemp()),
                        transport=LanTransport(sock), principal="owner")
    lan.connect(); lan.watch(); time.sleep(0.3)
    ra = c2.attach("approve me off-LAN", paths=["/srv/x"])
    jid = ra["id"]
    ok("off-LAN confirm task stages on the LAN device too",
       wait_for(lambda: any(cc["id"] == jid for cc in lan.pending_approvals())), detail="cross-transport")

    cvm_resp = c2.transport.call(contract.cvm_request(jid))
    nonce = (cvm_resp.get("cvm") or {}).get("approval_request", {}).get("nonce")
    ok("off-LAN client can read the CVM (relay.control broker)", bool(nonce), detail=str(cvm_resp))

    def pair_device(box_label, who, caps, *, control=False):
        sec = box.arm_2fa(who)
        all_caps = list(caps) + (["approval:resolve"] if control else [])
        code_ = box.mint_pairing(who, all_caps, label=f"{who} device")
        ks_ = Keystore(path=tempfile.mkdtemp())
        ks_.save_box_keys(box_label, relay_url=box.relay_url,
                          appliance_id_pubkey=box.appliance_id_pub_hex,
                          appliance_x_pubkey=box.appliance_x_pub_hex)
        dkk = ks_.device_identity(box_label)
        trr = RelayTransport(relay_url=box.relay_url,
                             appliance_id_pub_hex=box.appliance_id_pub_hex,
                             appliance_x_pub_hex=box.appliance_x_pub_hex, device_keys=dkk)
        cc = ConnectClient(box_label, keystore=ks_, transport=trr)
        pr_ = cc.pair(code_, totp_code=totp.code_at(sec), label=f"{who} device")
        cc.transport._dev.totp_secret = sec
        return cc, sec, pr_

    guest, _gsec, gpr = pair_device("guest-relay", "guest2", ["task_type:intake.message"])
    dec_self = (guest.transport.call({"verb": "approve", "nonce": nonce, "step_up": True})
                if (nonce and gpr.get("t") == "pair_ok") else {"ok": True})
    ok("submit-only device approve is REJECTED (separation of duties)",
       dec_self.get("ok") is False and "separation of duties" in (dec_self.get("error") or ""),
       detail=str(dec_self))
    guest.disconnect()

    pnd.grant_approval_authority("approver")
    appr, _asec, appr_pr = pair_device("ops", "approver", ["task_type:intake.message"], control=True)
    ok("approval-authority device paired (--control tier)",
       appr_pr.get("t") == "pair_ok", detail=str(appr_pr))
    dec = (appr.transport.call({"verb": "approve", "nonce": nonce, "step_up": True})
           if (nonce and appr_pr.get("t") == "pair_ok") else {})
    ok("off-LAN approve round-trips (approval-authority device)", dec.get("ok"), detail=str(dec))
    ok("off-LAN approve clears on the LAN device (cross-transport one reality)",
       wait_for(lambda: not any(cc["id"] == jid for cc in lan.pending_approvals())))
    appr.disconnect(); lan.disconnect(); srv.stop()

    ok("revoke at the box succeeds", box.revoke(paired_did))
    r_rev = c2.type("should be rejected after revoke")
    ok("post-revoke submit rejected at the box",
       (r_rev.get("ok") is False) or (r_rev.get("t") == "error"), detail=str(r_rev))
    c2.disconnect()
    box.stop()

def test_voice_and_parity(pnd):
    print("\n== Voice intake + spoken decisions + render parity ==")

    from connectlib.voice import VoiceIntake
    vi = VoiceIntake()
    ok("voice transcribes to text", vi.to_text("book a table for two") == "book a table for two")

    ok("'approve' -> approve", utterance_to_verb("approve")["verb"] == "approve")
    ok("'no, do not' -> deny (deny-before-approve safety)",
       utterance_to_verb("no, do not approve that")["verb"] == "deny")
    d = utterance_to_verb("revise: use the other address")
    ok("'revise ...' -> steer with feedback", d["verb"] == "steer" and "other address" in d["feedback"])

    try:
        from adapters import cvm_render as ck
        sample = {"id": 7, "state": "awaiting_approval", "approval_state": "pending",
                  "task_type": "send.email",
                  "approval_request": {"job_id": 7, "nonce": "n", "task_type": "send.email",
                                       "summary": "Reply to Acme",
                                       "action": "send the drafted email", "digest": "Dear Acme,"}}
        ok("title parity", contract.title(sample) == ck.title(sample))
        ok("action_line parity", contract.action_line(sample) == ck.action_line(sample))
        ok("digest parity", contract.digest(sample) == ck.digest(sample))
        ok("is_awaiting parity", contract.is_awaiting(sample) == ck.is_awaiting(sample))
        ok("approval_summary parity", contract.approval_summary(sample) == ck.approval_summary(sample))
        ok("nonce_of parity", contract.nonce_of(sample) == ck.nonce_of(sample))
    except Exception as e:
        ok("cockpit cvm_render importable for parity", False, detail=repr(e))

def main():
    print("connect-client E2E against the LOCAL/MOCK engine (no live services)")
    pnd = MockPnd()
    test_lan(pnd)
    test_relay(MockPnd())
    test_voice_and_parity(pnd)
    print(f"\n==== {len(PASS)} passed, {len(FAIL)} failed, {len(SKIP)} skipped ====")
    if SKIP:
        print(f"SKIPPED ({len(SKIP)}): off-LAN 2FA suite — needs the relay-hardened-2fa relaylib on "
              f"PN_ENGINE (the committed engine submodule and current engine main do NOT satisfy it).")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))

    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
