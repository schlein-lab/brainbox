#!/usr/bin/env python3

import os, sys, json, tempfile, importlib.util, socket

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from pnlib import discoverrun as DR, discover as D, db, brain as B
from importlib.machinery import SourceFileLoader

PASS = FAIL = 0

TEST_CIDR = os.environ.get("PN_TEST_CIDR", "10.10.0.0/24")

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {label}")
    else:
        FAIL += 1; print(f"  FAIL  {label}")

def _load(name, fname):
    loader = SourceFileLoader(name, os.path.join(ROOT, "tools", fname))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod

def _store_dir():
    return tempfile.mkdtemp(prefix="pn_discover_")

def test_mock_fingerprints():
    print("[a] run_discovery(mock): realistic fingerprints, classified, scanned_live=False")
    sd = _store_dir()
    rep = DR.run_discovery(own_cidrs=[TEST_CIDR], mode="mock",
                           store_path=os.path.join(sd, "devices.db"))
    check(rep["ok"] and rep["scanned"] == 4, "four realistic devices fingerprinted")
    check(rep["scanned_live"] is False, "scanned_live is False (the real network was NOT touched)")
    classes = {c["device_class"] for c in rep["candidates"]}
    check({"printer", "nas"} <= classes, "classified a printer + a NAS (vendor/class via OUI+hints)")
    nas = next(c for c in rep["candidates"] if c["device_class"] == "nas")
    check(any("SMB1" in r for r in nas["risks"]), "the NAS carries an SMB1 (wormable) risk note")
    check(any(c["risks"] for c in rep["candidates"]), "risk notes are produced")

def test_never_scans():
    print("[b] never-scan guard: socket.socket blows up for the whole pass; mock completes anyway")
    sd = _store_dir()
    orig = socket.socket

    def _boom(*a, **k):
        raise AssertionError("net.discover mock runner tried to open a SOCKET (forbidden!)")

    socket.socket = _boom
    try:
        rep = DR.run_discovery(own_cidrs=["10.0.0.0/24"], mode="mock",
                               store_path=os.path.join(sd, "devices.db"))
        check(rep["ok"] and rep["scanned"] == 4, "the mock pass completed with ZERO socket opens")
    except AssertionError as e:
        check(False, f"the mock runner opened a socket: {e}")
    finally:
        socket.socket = orig

def test_rescan_diff():
    print("[c] re-scan diff: idempotent (no new on a repeat); a change is reported; SEPARATE db")
    sd = _store_dir()
    sp = os.path.join(sd, "devices.db")
    r1 = DR.run_discovery(own_cidrs=[TEST_CIDR], mode="mock", store_path=sp)
    check(len(r1["diff"]["new"]) == 4, "first pass: all four are NEW")
    r2 = DR.run_discovery(own_cidrs=[TEST_CIDR], mode="mock", store_path=sp)
    check(r2["diff"]["new"] == [] and r2["proposals"] == [],
          "second identical pass: NO new candidates, NO re-proposals (idempotent)")
    check(os.path.exists(sp) and not sp.endswith("queue.db"),
          "the discovery store is a SEPARATE db (never queue.db)")

    obs = DR.mock_observations([TEST_CIDR])
    obs[2]["services"].append({"scheme": "telnet", "port": 23, "banner": "now telnet open"})
    profiles = [D.classify(o) for o in obs]
    store = D.DiscoveryStore(sp)
    try:
        diff = store.rescan_diff(profiles)
    finally:
        store.close()
    check(len(diff["changed"]) >= 1, "a device that gained a transport is reported CHANGED")

def test_proposals():
    print("[d] proposals: approval-request payloads (auto_bind:false) -> valid op:propose actions")
    sd = _store_dir()
    rep = DR.run_discovery(own_cidrs=[TEST_CIDR], mode="mock",
                           store_path=os.path.join(sd, "devices.db"))
    check(len(rep["proposals"]) == 4, "one proposal per new candidate")
    p = rep["proposals"][0]
    check(p.get("auto_bind") is False, "the proposal NEVER auto-binds (observe-only)")
    check("requires human approval" in json.dumps(p), "the proposal states onboarding needs a human")
    actions = DR.proposals_to_actions(rep)
    check(len(actions) == 4 and all(a["op"] == "propose" for a in actions),
          "proposals -> op:propose actions")

    ok = True
    for a in actions:
        try:
            B.validate_action(a)
        except B.ValidationError:
            ok = False
    check(ok, "every emitted proposal action is closed-world VALID (the brain will accept it)")

def test_live_off_cidr_refused():
    print("[e] live mode is OWN-CIDR guarded: an off-CIDR target is REFUSED (no off-LAN scan)")
    sd = _store_dir()
    refused = False
    try:
        DR.run_discovery(own_cidrs=[TEST_CIDR], mode="live",
                         live_targets=[{"ip": "8.8.8.8", "ports": [{"scheme": "snmp", "port": 161}]}],
                         store_path=os.path.join(sd, "devices.db"))
    except D.OutOfScope:
        refused = True
    check(refused, "an off-CIDR live target is refused (assert_own_cidr; a misconfig cannot scan)")

def test_brain_integration():
    print("[f] BRAIN: report -> consume_discovery_report -> each device PARKS staged (human gate)")
    pnd = _load("pnd_disc", "pnd")
    pnbrain = _load("pnbrain_disc", "pn-brain")
    import threading
    path = tempfile.mktemp(prefix="pn_disc_brain_", suffix=".db")
    cx = db.connect(path)
    pnd.CX = cx
    pnd.LK = threading.Lock()

    def pnd_fn(req):
        pnd.CX = cx
        r = dict(req)
        r.pop("principal", None); r.pop("uid", None)
        r["_peer_uid"] = _brain_uid(cx)
        return pnd.handle(r)

    sd = _store_dir()
    rep = DR.run_discovery(own_cidrs=[TEST_CIDR], mode="mock",
                           store_path=os.path.join(sd, "devices.db"))
    bn = pnbrain.Brain(cx, lambda *a, **k: "{}", pnd_fn, principal="brain")
    disps = bn.consume_discovery_report(rep)
    check(len(disps) == 4 and all(d["ok"] for d in disps),
          "the brain disposed one op:propose per discovered device")

    staged = [r["id"] for r in cx.execute(
        "SELECT id FROM jobs WHERE submitter_principal='brain' AND state='staged'").fetchall()]
    check(len(staged) == 4, "every device proposal parked `staged` behind the human gate")

    not_run = [r["id"] for r in cx.execute(
        "SELECT id FROM jobs WHERE submitter_principal='brain' AND state IN "
        "('running','done','queued')").fetchall()]
    check(not_run == [], "NO discovered device auto-ran/auto-bound (it waits for the human gate)")

    jid = staged[0]
    evs = db.events(cx, jid, scope_all=True)
    ar = next((e for e in evs if e["kind"] == "approval-request"), None)
    ar_data = json.loads(ar["data"]) if ar and ar["data"] else {}
    check(ar_data.get("proposal", {}).get("kind") == "onboard",
          "the approval-request carries the device-onboard proposal for the human reviewer")

    ega = pnd_fn({"verb": "egress-approve", "nonce": "anything"})
    check(not ega.get("ok") and "view:all" in (ega.get("error") or ""),
          "the brain (no view:all) CANNOT self-approve a destructive bind/egress (structural)")
    cx.close()
    for p in (path, path + "-wal", path + "-shm"):
        try:
            os.unlink(p)
        except OSError:
            pass

def _brain_uid(cx):

    r = cx.execute("SELECT uid FROM principals WHERE name='brain'").fetchone()
    return r["uid"] if r else os.getuid()

def main():
    print("=== P5 net.discover fingerprint runner (MOCK scan; the live LAN is NEVER touched) ===")
    test_mock_fingerprints()
    test_never_scans()
    test_rescan_diff()
    test_proposals()
    test_live_off_cidr_refused()
    test_brain_integration()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
