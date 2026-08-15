#!/usr/bin/env python3

import json
import os
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from pnlib import transport as T
from pnlib import discover as D
from pnlib import devmaint as M

PASS = FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")

class MockSNMP(threading.Thread):

    def __init__(self, descr="MockOS v1.0 legacy router"):
        super().__init__(daemon=True)
        self.descr = descr
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self._stop = False

    @staticmethod
    def _tlv(tag, val):
        return bytes([tag, len(val)]) + val

    def _response(self, request_id=1):
        oid = T.SNMPDriver._encode_oid(T.SNMPDriver._SYSDESCR_OID)
        val = self._tlv(0x04, self.descr.encode())
        varbind = self._tlv(0x30, oid + val)
        vlist = self._tlv(0x30, varbind)
        pdu = self._tlv(0xA2,
                        self._tlv(0x02, bytes([request_id])) +
                        self._tlv(0x02, b"\x00") + self._tlv(0x02, b"\x00") + vlist)
        return self._tlv(0x30, self._tlv(0x02, b"\x01") + self._tlv(0x04, b"public") + pdu)

    def run(self):
        self.sock.settimeout(0.3)
        while not self._stop:
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            self.sock.sendto(self._response(), addr)

    def stop(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass

class MockTCP(threading.Thread):

    def __init__(self, handler):
        super().__init__(daemon=True)
        self.handler = handler
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._stop = False

    def run(self):
        self.sock.settimeout(0.3)
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            conn.settimeout(2)
            data = conn.recv(8192)
            conn.sendall(self.handler(data))
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def stop(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass

def _smb_handler(data):
    shares = [{"name": "PUBLIC", "type": "disk"}, {"name": "scans", "type": "disk"},
              {"name": "IPC$", "type": "ipc"}]
    return (json.dumps(shares) + "\n").encode()

def _ipp_handler(data):
    attrs = {"printer-make-and-model": "Brother HL-2270DW",
             "printer-state": "idle", "printer-firmware-version": "1.20"}
    body = b"\x01\x01\x00\x00\x00\x00\x00\x01\x03"
    return (b"HTTP/1.1 200 OK\r\nContent-Type: application/ipp\r\n\r\n" + body
            + b"\nIPP-ATTRS " + json.dumps(attrs).encode() + b"\n")

def test_registry():
    print("[a] transport registry: real drivers + stub matrix; non-read-only refused")
    reg = T.default_registry()
    check(set(reg.real_schemes()) == {"smb", "ipp", "snmp"}, "3 REAL read-only drivers: smb/ipp/snmp")
    stubs = set(reg.stub_schemes())
    check({"nfs", "ftp", "webdav", "mqtt", "rtsp", "tftp", "rf433", "ir", "bt", "zigbee"} <= stubs,
          "STUB matrix covers nfs/ftp/webdav/mqtt/rtsp/tftp + rf/ir/bt/zigbee")

    class Bad(T.TransportDriver):
        scheme = "evil"
        readonly = False
    try:
        reg.register(Bad())
        check(False, "non-read-only driver refused")
    except ValueError:
        check(True, "non-read-only driver refused at registration (observe-only invariant)")

    try:
        reg.get("tftp").probe("127.0.0.1")
        check(False, "stub probe raises")
    except T.DriverUnavailable:
        check(True, "a stub driver raises DriverUnavailable when invoked (documented stub)")

def test_real_drivers():
    print("[b] REAL drivers read a MOCK device (no live LAN)")
    snmp = MockSNMP("MockOS v1.0 legacy router")
    smb = MockTCP(_smb_handler)
    ipp = MockTCP(_ipp_handler)
    for m in (snmp, smb, ipp):
        m.start()
    time.sleep(0.2)
    try:
        reg = T.default_registry()
        pr = reg.probe("snmp", "127.0.0.1", snmp.port)
        check(pr.ok and "legacy router" in pr.data.get("sysDescr", ""),
              f"SNMP sysDescr GET read from mock: {pr.data.get('sysDescr')!r}")
        pr = reg.probe("smb", "127.0.0.1", smb.port)
        names = [s["name"] for s in pr.data.get("shares", [])]
        check(pr.ok and "scans" in names, f"SMB share LIST read from mock: {names}")
        pr = reg.probe("ipp", "127.0.0.1", ipp.port)
        model = pr.data.get("printer", {}).get("printer-make-and-model")
        check(pr.ok and model == "Brother HL-2270DW",
              f"IPP Get-Printer-Attributes read from mock: {model!r}")
    finally:
        for m in (snmp, smb, ipp):
            m.stop()

def test_composition():
    print("[c] capability composition (emergent superpowers)")

    comp = T.compose_capabilities(["bt"])
    check("voice-endpoint" in comp, "mic+speaker (bt) composes a voice-endpoint")
    comp = T.compose_capabilities(["ipp"])
    check("print-to-task" in comp and "autonomous-maintenance" in comp,
          "a printer (ipp) composes print-to-task + autonomous-maintenance")
    comp = T.compose_capabilities(["rf433"])
    check("input-cannon" in comp, "a 433 sensor composes an input-cannon")
    comp = T.compose_capabilities(["smb"])
    check("drop-zone-task" in comp, "a writable share (smb) composes a drop-zone-task")

def _targets(snmp_port, smb_port, ipp_port):
    return {
        "own_cidrs": ["127.0.0.0/8"],
        "targets": [
            {"ip": "127.0.0.1", "mac": "00:80:77:AA:BB:CC", "hostname": "printer1",
             "ports": [{"scheme": "ipp", "port": ipp_port}, {"scheme": "snmp", "port": snmp_port}]},
            {"ip": "127.0.0.1", "mac": "00:24:21:11:22:33", "hostname": "oldnas",
             "ports": [{"scheme": "smb", "port": smb_port}], "smb1": True},
        ],
    }

def test_discovery_guards():
    print("[d] discovery is observe-only + own-CIDR + rate-limited; off-CIDR REFUSED")

    try:
        D.discover([{"ip": "8.8.8.8", "ports": [{"scheme": "snmp", "port": 161}]}],
                   ["127.0.0.0/8"])
        check(False, "off-CIDR refused")
    except D.OutOfScope:
        check(True, "an off-CIDR target is REFUSED (own-LAN only — never scan off-LAN)")

    lim = D.RateLimiter(rate_per_sec=1000)
    lim.acquire(sleep=lambda s: None)
    lim.acquire(sleep=lambda s: None)
    check(lim.probes == 2, "rate limiter accounts every probe (gentle, rate-limited)")

def test_discover_and_propose():
    print("[e] discovery finds a mock device and PROPOSES (never auto-binds)")
    snmp = MockSNMP("Brother HL-2270DW printer")
    smb = MockTCP(_smb_handler)
    ipp = MockTCP(_ipp_handler)
    for m in (snmp, smb, ipp):
        m.start()
    time.sleep(0.2)
    try:
        doc = _targets(snmp.port, smb.port, ipp.port)
        profiles = D.discover(doc["targets"], doc["own_cidrs"], rate_per_sec=1000)
        check(len(profiles) == 2, "discovery returned 2 classified candidate profiles")
        printer = next(p for p in profiles if p["device_class"] == "printer")
        check(printer["vendor"] == "Brother (printer)", "printer vendor resolved via OUI")
        check("autonomous-maintenance" in printer["capabilities"],
              "printer composes autonomous-maintenance capability")
        nas = next(p for p in profiles if p["mac"].startswith("00:24:21"))
        check(any("SMB1" in r for r in nas["risks"]),
              "the SMB1 NAS carries an SMB1 wormable RISK note")

        prop = D.propose_payload(printer)
        check(prop["auto_bind"] is False, "the proposal does NOT auto-bind (auto_bind=False)")
        check(prop["action"]["task_type"] == "device.onboard",
              "the proposal's action is the GATED device.onboard task_type")
        def _has_nonce_field(o):
            if isinstance(o, dict):
                return ("nonce" in o) or any(_has_nonce_field(v) for v in o.values())
            if isinstance(o, list):
                return any(_has_nonce_field(v) for v in o)
            return False
        check(not _has_nonce_field(prop),
              "the proposal carries NO consent-nonce FIELD (discovery cannot mint one — nor can the brain)")
    finally:
        for m in (snmp, smb, ipp):
            m.stop()

def test_rescan_diff():
    print("[f] re-scan DIFF: new / unchanged / changed against the OWN store")
    tmp = tempfile.mkdtemp(prefix="pn_dev_")
    os.environ["PN_DEVICE_DIR"] = tmp
    store = D.DiscoveryStore(D.store_path(tmp))
    p1 = D.classify({"ip": "127.0.0.1", "mac": "02:00:00:00:00:12", "hostname": "a",
                     "services": [{"scheme": "ipp", "port": 631}]})
    p2 = D.classify({"ip": "127.0.0.2", "mac": "02:00:00:00:00:13", "hostname": "b",
                     "services": [{"scheme": "smb", "port": 445}]})
    d1 = store.rescan_diff([p1])
    check(d1["new"] == [D.fingerprint_key(p1)] and not d1["changed"], "first scan: device is `new`")
    d2 = store.rescan_diff([p1])
    check(d2["changed"] == [] and D.fingerprint_key(p1) in d2["all"], "re-scan same: `unchanged`")

    p1b = D.classify({"ip": "127.0.0.1", "mac": "02:00:00:00:00:12", "hostname": "a",
                      "services": [{"scheme": "ipp", "port": 631}, {"scheme": "snmp", "port": 161}]})
    d3 = store.rescan_diff([p1b])
    check(d3["changed"] == [D.fingerprint_key(p1b)], "re-scan with a new transport: `changed`")

    d4 = store.rescan_diff([p1b, p2])
    check(D.fingerprint_key(p2) in d4["new"], "a newly-appearing device shows up as `new`")
    store.close()

def test_cve_audit():
    print("[g] CVE/health audit is READ-ONLY + autonomous (no consent)")
    profile = {"ip": "127.0.0.1", "device_class": "router", "vendor": "OpenWrt",
               "product": "openwrt", "firmware_version": "21.02.0"}
    a = M.cve_audit(profile, "21.02.0")
    check(a["read_only"] is True and a["tier"] == M.TIER_AUTONOMOUS,
          "audit is read_only + autonomous tier (no consent needed)")
    check(a["needs_update"] and any(f["id"] == "CVE-2023-9999" for f in a["cve_findings"]),
          "audit flags the applicable CVE for the old version")
    a2 = M.cve_audit(profile, "23.05.0")
    check(not a2["needs_update"], "a patched version has no applicable findings")

class ConsentBurn:

    def __init__(self):
        self.valid = set()
    def mint(self):
        import secrets
        n = secrets.token_urlsafe(18)
        self.valid.add(n)
        return n
    def verify(self, nonce):
        if nonce in self.valid:
            self.valid.discard(nonce)
            return True
        return False

def _profile_needs_update():
    return {"ip": "127.0.0.1", "mac": "02:00:00:00:00:14", "device_class": "router",
            "vendor": "openwrt", "product": "openwrt", "firmware_version": "21.02.0",
            "firmware_image": "OPENWRT-OLD"}

def test_refuse_without_consent():
    print("[h] firmware flash REFUSED without a valid consent nonce; the brain can't mint it")
    burn = ConsentBurn()
    dag = M.MaintenanceDAG(burn.verify, mode="green")

    import secrets
    forged = secrets.token_urlsafe(18)
    res = dag.run(_profile_needs_update(), patch={"fix": "auth-bypass"},
                  current_image=b"OPENWRT-OLD", current_version="21.02.0",
                  consent_nonce=forged, brick_warning_ack=True)
    check(res["outcome"] == "write-refused" and "consent" in res["reason"].lower(),
          "a brain-FORGED nonce fails the verifier -> write REFUSED (brain cannot mint consent)")

    dag2 = M.MaintenanceDAG(burn.verify, mode="green")
    res2 = dag2.run(_profile_needs_update(), patch={"fix": "x"}, current_image=b"OPENWRT-OLD",
                    current_version="21.02.0", consent_nonce=None, brick_warning_ack=True)
    check(res2["outcome"] == "write-refused", "no consent nonce -> write refused")

    dag3 = M.MaintenanceDAG(burn.verify, mode="green")
    good = burn.mint()
    res3 = dag3.run(_profile_needs_update(), patch={"fix": "x"}, current_image=b"OPENWRT-OLD",
                    current_version="21.02.0", consent_nonce=good, brick_warning_ack=False)
    check(res3["outcome"] == "write-refused",
          "valid nonce but brick-warning NOT acknowledged -> write refused")

def test_emulate_first():
    print("[i] EMULATE-FIRST runs before any (emulated) write; bad emulation blocks the write")
    burn = ConsentBurn()
    dag = M.MaintenanceDAG(burn.verify, mode="green")
    nonce = burn.mint()
    res = dag.run(_profile_needs_update(), patch={"fix": "x"}, current_image=b"OPENWRT-OLD",
                  current_version="21.02.0", consent_nonce=nonce, brick_warning_ack=True,
                  emulate_healthy=False)
    check(res["outcome"] == "emulation-failed", "a bad emulation BLOCKS the write (emulate-first)")
    check(dag.stage_index("emulate") >= 0 and dag.stage_index("write") < 0,
          "emulate ran; write never reached (emulate gates the write)")

def test_backup_precedes_write():
    print("[j] BACKUP precedes the write; protected + replicated; unverified backup blocks write")
    burn = ConsentBurn()
    dag = M.MaintenanceDAG(burn.verify, mode="green")
    nonce = burn.mint()
    tmp = tempfile.mkdtemp(prefix="pn_bk_")
    res = dag.run(_profile_needs_update(), patch={"fix": "x"}, current_image=b"OPENWRT-OLD-IMAGE",
                  current_version="21.02.0", consent_nonce=nonce, brick_warning_ack=True,
                  backup_path=os.path.join(tmp, "fw.bak"), replicate=True)
    check(res["ok"], "happy path completes with a backup")
    bk = res["backup"]
    check(bk["protected"] and bk["verified"], "backup is PROTECTED + verified (not GC-eligible)")
    check(bk["replicated"], "backup is off-box REPLICATED")
    check(dag.stage_index("backup") < dag.stage_index("write"), "backup PRECEDES the write")

    fe = M.FlashEngine(burn.verify, mode="green")
    bad_backup = M.Backup("dev", b"img")
    bad_backup.verified = False
    try:
        fe.flash(descriptor={"artifact_sha256": "x"}, emulation={"ok": True}, backup=bad_backup,
                 consent_nonce=burn.mint(), brick_warning_ack=True)
        check(False, "unverified backup refused")
    except M.BackupRequired:
        check(True, "a write with an UNVERIFIED backup is refused (mandatory backup)")

def test_rollback():
    print("[k] ROLLBACK fires automatically on a simulated bad post-flash health gate")
    burn = ConsentBurn()
    dag = M.MaintenanceDAG(burn.verify, mode="green")
    nonce = burn.mint()
    res = dag.run(_profile_needs_update(), patch={"fix": "x"}, current_image=b"OPENWRT-OLD",
                  current_version="21.02.0", consent_nonce=nonce, brick_warning_ack=True,
                  post_health_healthy=False)
    check(res["outcome"] == "rolled-back" and res["rollback"]["rolled_back"],
          "a failed health gate triggers an automatic ROLLBACK")
    check(dag.stage_index("write") < dag.stage_index("rollback"),
          "rollback runs AFTER the (emulated) write, on the failed verify")

def test_ordering_proof():
    print("[l] ordering PROOF: emulate + backup + consent ALL precede write")
    burn = ConsentBurn()
    dag = M.MaintenanceDAG(burn.verify, mode="green")
    nonce = burn.mint()
    dag.run(_profile_needs_update(), patch={"fix": "x"}, current_image=b"OPENWRT-OLD",
            current_version="21.02.0", consent_nonce=nonce, brick_warning_ack=True)
    proof = dag.ordering_ok()
    check(proof["emulate_before_write"], "emulate BEFORE write")
    check(proof["backup_before_write"], "backup BEFORE write")
    check(proof["consent_before_write"], "consent BEFORE write")

def test_abandonware():
    print("[m] abandonware mode: generate + recompile + SIGN + STAGE (dry-run)")
    profile = {"ip": "127.0.0.1", "device_class": "router", "vendor": "openwrt",
               "product": "openwrt", "firmware_version": "21.02.0"}
    res = M.abandonware_update(profile, {"patch": "backport-CVE-2023-9999-fix"}, target_arch="mips")
    check(res["generated"] and res["recompiled"] and res["cross_arch"] == "mips",
          "an update is GENERATED + cross-arch recompiled (emulated qemu-user)")
    check(res["signed"] and res["staged"] and res["flashed"] is False,
          "the staged artifact is SIGNED + staged (dry-run; flashed=False)")

    ok = M.verify_signature(res["artifact_sha256"], res["signature"])
    check(ok, "the generated firmware's signature verifies (supply-chain trust)")
    bad = M.verify_signature("0" * 64, res["signature"])
    check(not bad, "a tampered artifact FAILS signature verification")

def test_red_mode():
    print("[o] red/container mode: firmware ops UNAVAILABLE")
    check(M.firmware_permitted("green") and not M.firmware_permitted("red"),
          "firmware permitted only in green; refused in red")
    burn = ConsentBurn()
    dag = M.MaintenanceDAG(burn.verify, mode="red")
    nonce = burn.mint()
    res = dag.run(_profile_needs_update(), patch={"fix": "x"}, current_image=b"OPENWRT-OLD",
                  current_version="21.02.0", consent_nonce=nonce, brick_warning_ack=True)
    check(res["outcome"] == "firmware-unavailable",
          "in red mode the firmware tail is refused (no write attempted)")

    fe = M.FlashEngine(burn.verify, mode="red")
    bk = M.Backup("d", b"img"); bk.verify()
    try:
        fe.flash(descriptor={"artifact_sha256": "x"}, emulation={"ok": True}, backup=bk,
                 consent_nonce=burn.mint(), brick_warning_ack=True)
        check(False, "red FlashEngine refused")
    except M.FirmwareUnavailable:
        check(True, "FlashEngine refuses firmware ops in red mode (defence in depth)")

def test_happy_path():
    print("[p] full happy-path DAG with a VALID consent nonce -> flashed-and-verified (EMULATED)")
    burn = ConsentBurn()
    dag = M.MaintenanceDAG(burn.verify, mode="green")
    nonce = burn.mint()
    res = dag.run(_profile_needs_update(), patch={"fix": "auth-bypass"}, current_image=b"OPENWRT-OLD",
                  current_version="21.02.0", consent_nonce=nonce, brick_warning_ack=True,
                  config_fix={"set": {"telnet": "off"}})
    check(res["ok"] and res["outcome"].startswith("flashed-and-verified"),
          "valid consent + emulate-first + backup -> flashed-and-verified (EMULATED)")
    check(res["write"]["real_write"] is False and res["write"]["emulated_write"] is True,
          "the write is EMULATED (real_write=False) — NO real device I/O")
    check(any(e["stage"] == "config_fix" and e["info"].get("reversible") for e in res["trace"]),
          "the reversible config fix ran autonomously (logged)")

    check(not burn.verify(nonce), "the consent nonce is SINGLE-USE (burned; cannot be replayed)")

def main():
    print("=== P9 device-empowerment + maintenance/firmware DAG (isolated, mocked) ===\n")
    test_registry()
    test_real_drivers()
    test_composition()
    test_discovery_guards()
    test_discover_and_propose()
    test_rescan_diff()
    test_cve_audit()
    test_refuse_without_consent()
    test_emulate_first()
    test_backup_precedes_write()
    test_rollback()
    test_ordering_proof()
    test_abandonware()
    test_red_mode()
    test_happy_path()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
