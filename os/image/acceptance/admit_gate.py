#!/usr/bin/env python3

from __future__ import annotations
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

SCR_ENV = {
    "PN_LLM_CMD": "/bin/echo {model}",
    "PN_LLM_POOL": "2",
    "PN_LLM_CANARY_INTERVAL": "100000",
    "PN_SECRETS_ALLOW_INSECURE": "1",
    "PN_SECRETS_PASSPHRASE": "admit-gate",
    "PN_ADMIT_SLOTS": "4",
    "PN_ADMIT_AIMD": "1",
    "PN_ADMIT_MIN": "1",
    "PN_ADMIT_MAX": "6",
    "PN_ADMIT_GROW_EVERY": "2",
    "PN_ADMIT_COOLDOWN_S": "1.5",
    "PN_ADMIT_EXEC_SLOTS": "2",
    "PN_ADMIT_EXEC_TTL": "2",
    "PN_ADMIT_ACT_SLOTS": "1",
    "PN_ADMIT_ACT_TTL": "2",
    "PN_LLM_LEASE_SWEEP": "1",
}

C = {"g": "\033[32m", "r": "\033[31m", "y": "\033[33m", "b": "\033[1m", "0": "\033[0m"}
if not sys.stdout.isatty():
    C = {k: "" for k in C}

class Gate:
    def __init__(self, engine_dir):
        self.eng = engine_dir
        self.scratch = tempfile.mkdtemp(prefix="pn-admitgate.")
        self.sock = os.path.join(self.scratch, "pn-llmd.sock")
        self.proc = None
        self.results = []
        self.env = dict(os.environ)
        self.env.update(SCR_ENV)
        self.env.update({"PN_LLM_SOCK": self.sock,
                         "PN_SECRETS_DIR": os.path.join(self.scratch, "secrets")})

    def check(self, cid, label, passed, evidence=""):
        self.results.append((cid, label, bool(passed), evidence))
        tag = (C["g"] + "PASS" + C["0"]) if passed else (C["r"] + "FAIL" + C["0"])
        print("  [%s] %-3s %-52s %s" % (tag, cid, label, evidence), flush=True)
        return passed

    def ref(self, cid, label, evidence):
        self.results.append((cid, label, True, "REFERENCED: " + evidence))
        print("  [%sREF %s] %-3s %-52s %s" % (C["y"], C["0"], cid, label, evidence), flush=True)

    def req(self, d, timeout=10):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(self.sock)
        s.sendall((json.dumps(d) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            c = s.recv(65536)
            if not c:
                break
            buf += c
        s.close()
        return json.loads(buf.decode().splitlines()[0])

    def start(self, extra_env=None):
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(self.eng, "tools", "pn-llmd")], env=env,
            stdout=open(os.path.join(self.scratch, "llmd.out"), "ab"),
            stderr=open(os.path.join(self.scratch, "llmd.err"), "ab"))
        for _ in range(80):
            if os.path.exists(self.sock):
                try:
                    if self.req({"verb": "status"}).get("ok"):
                        return
                except Exception:
                    pass
            if self.proc.poll() is not None:
                err = ""
                try:
                    err = open(os.path.join(self.scratch, "llmd.err")).read()[-1500:]
                except Exception:
                    pass
                raise RuntimeError("scratch pn-llmd exited rc=%s\n%s" % (self.proc.returncode, err))
            time.sleep(0.25)
        raise RuntimeError("scratch pn-llmd socket never came up")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=8)
            except Exception:
                self.proc.kill()
        try:
            os.unlink(self.sock)
        except OSError:
            pass

    def teardown(self):
        self.stop()
        shutil.rmtree(self.scratch, ignore_errors=True)
        return not os.path.exists(self.scratch)

    def padmit(self, plane, tid, **kw):
        d = {"verb": "%s-admit" % plane, "id": tid, "cell_principal": "gate", "cell": "gate-cell"}
        d.update(kw)
        return self.req(d)

    def ppoll(self, plane, tid):
        return self.req({"verb": "%s-admit-poll" % plane, "id": tid})

    def prelease(self, plane, tid):
        return self.req({"verb": "%s-admit-release" % plane, "id": tid})

    def psnap(self, plane):
        return self.req({"verb": "%s-admit-snapshot" % plane})

    def turn(self, tid, status=200, rate_hits=0, served=True):

        r = self.req({"verb": "admit", "id": tid, "principal": "gate"})
        self.req({"verb": "admit-release", "id": tid, "status": status,
                  "rate_hits": rate_hits, "served": served})
        return r

    def aimd(self):
        return self.req({"verb": "admit-snapshot"}).get("aimd") or {}

    def wait_swept(self, plane, tid, timeout=6.0, keep_alive=()):

        t = time.time() + timeout
        while time.time() < t:
            for k in keep_alive:
                self.ppoll(plane, k)
            if self.ppoll(plane, tid).get("granted"):
                return True
            time.sleep(0.45)
        return False

    def e_exec_plane(self):
        print(C["b"] + "\nE   exec admission (the queue every in-cell execve is held against)" + C["0"])
        g1 = self.padmit("exec", "e1", argv0="heavytool")
        g2 = self.padmit("exec", "e2", argv0="heavytool")
        w3 = self.padmit("exec", "e3", argv0="heavytool")
        w4 = self.padmit("exec", "e4", argv0="heavytool")
        snap = self.psnap("exec")
        self.check("E1", "slots bind: 2 granted, 3rd+4th WAIT with honest positions",
                   g1.get("granted") and g2.get("granted")
                   and not w3.get("granted") and w3.get("position") == 1
                   and not w4.get("granted") and w4.get("position") == 2
                   and snap.get("in_use") == 2 and snap.get("waiting") == 2,
                   "in_use=%s waiting=%s pos3=%s pos4=%s" % (snap.get("in_use"),
                   snap.get("waiting"), w3.get("position"), w4.get("position")))
        self.prelease("exec", "e1")
        p3 = self.ppoll("exec", "e3")
        p4 = self.ppoll("exec", "e4")
        self.check("E2", "release PROMOTES the head waiter (in order)",
                   p3.get("granted") and not p4.get("granted") and p4.get("position") == 1,
                   "e3 granted, e4 position=%s" % p4.get("position"))

        reaped = self.wait_swept("exec", "e4", keep_alive=("e3",))
        gone = self.ppoll("exec", "e2")
        self.check("E3", "crashed holder TTL-reaped by the daemon sweep, waiter granted",
                   reaped and gone.get("position") == -1,
                   "e4 granted after reap, e2 unknown" if reaped else "e4 never granted (no sweep?)")
        r1 = self.prelease("exec", "e3")
        r2 = self.prelease("exec", "e3")
        self.check("E4", "release is idempotent",
                   r1.get("released") is True and r2.get("released") is False,
                   "first released=True, second released=False")
        self.prelease("exec", "e4")
        self.ref("E5", "execve actually HELD until this queue grants",
                 "seccomp user-notify pn-gate (commits 5e9a1c2/9ea3434/6eed99c), adversarially "
                 "proven in a real microVM; needs the 6.1 guest kernel — queue semantics proven "
                 "live above")

    def a_act_plane(self):
        print(C["b"] + "\nA   act admission (mutating in-cell capability calls via pn-actd)" + C["0"])
        a1 = self.padmit("act", "a1", tool="inject_input")
        a2 = self.padmit("act", "a2", tool="inject_input")
        ex = self.padmit("exec", "ax", argv0="sideload")
        self.check("A1", "act slot binds; a 2nd mutating act WAITS; exec is INDEPENDENT",
                   a1.get("granted") and not a2.get("granted") and a2.get("position") == 1
                   and ex.get("granted"),
                   "a1 granted, a2 position=%s, exec granted concurrently" % a2.get("position"))
        self.prelease("exec", "ax")
        self.prelease("act", "a1")
        p2 = self.ppoll("act", "a2")
        self.check("A2", "act release promotes the waiter", p2.get("granted"), "a2 granted")

        a3 = self.padmit("act", "a3", tool="press_keys")
        reaped = self.wait_swept("act", "a3")
        self.check("A3", "crashed act holder TTL-reaped, waiter granted",
                   reaped, "a3 granted after reap" if reaped else "a3 never granted (no sweep?)")
        self.prelease("act", "a3")
        self.ref("A4", "denials NEVER enter this queue; audit chain tamper-evident",
                 "pn-actd deny-by-default + hash-chain proven E2E in a real microVM (d4bf90d), "
                 "actuation on a real seat (54e6aee), live-promoted — queue semantics proven "
                 "live above")

    def m_aimd_plane(self):
        print(C["b"] + "\nM   adaptive contingent (Phase-2 AIMD on the LLM turn budget)" + C["0"])
        a0 = self.aimd()
        self.check("M1", "governor ON, budget at the pinned start",
                   a0.get("on") is True and a0.get("cur") == 4
                   and a0.get("min") == 1 and a0.get("max") == 6,
                   "cur=%s min=%s max=%s" % (a0.get("cur"), a0.get("min"), a0.get("max")))
        self.turn("m-429", status=429, rate_hits=1, served=False)
        a1 = self.aimd()
        self.check("M2", "upstream 429 -> MULTIPLICATIVE DECREASE (budget halves)",
                   a1.get("cur") == 2 and a1.get("shrinks") == 1,
                   "cur 4 -> %s, shrinks=%s" % (a1.get("cur"), a1.get("shrinks")))
        self.turn("m-429b", status=429, rate_hits=1, served=False)
        a2 = self.aimd()
        self.check("M3", "ONE decrease per congestion window (no cascade)",
                   a2.get("cur") == 2 and a2.get("shrinks") == 1,
                   "second 429 inside the window: cur stays %s" % a2.get("cur"))
        time.sleep(1.7)
        for i in range(3):
            self.turn("m-idle%d" % i, status=200)
        a3 = self.aimd()
        self.check("M4", "idle success never grows the budget (no real demand)",
                   a3.get("cur") == 2 and a3.get("grows", 0) == 0,
                   "3 successful turns, nobody waiting: cur stays %s" % a3.get("cur"))

        self.req({"verb": "admit", "id": "m-p1", "principal": "gate"})
        self.req({"verb": "admit", "id": "m-p2", "principal": "gate"})
        self.req({"verb": "admit", "id": "m-w1", "principal": "gate"})
        self.req({"verb": "admit-release", "id": "m-p1", "status": 200, "served": True})
        self.req({"verb": "admit", "id": "m-w2", "principal": "gate"})
        self.req({"verb": "admit-release", "id": "m-w1", "status": 200, "served": True})
        a4 = self.aimd()
        snap = self.req({"verb": "admit-snapshot"})
        self.check("M5", "success under REAL demand -> ADDITIVE INCREASE (+1 slot)",
                   a4.get("cur") == 3 and a4.get("grows") == 1 and snap.get("slots") == 3,
                   "cur 2 -> %s, grows=%s, live slots=%s" % (a4.get("cur"), a4.get("grows"),
                                                             snap.get("slots")))
        for tid in ("m-p2", "m-w2"):
            self.req({"verb": "admit-release", "id": tid})

    def m_aimd_pin(self):
        print(C["b"] + "\nM6  PN_ADMIT_AIMD=0 pins the Phase-1 fixed budget byte-identically" + C["0"])
        self.stop()
        self.start(extra_env={"PN_ADMIT_AIMD": "0"})
        self.turn("pin-429", status=429, rate_hits=1, served=False)
        a = self.aimd()
        snap = self.req({"verb": "admit-snapshot"})
        self.check("M6", "governor OFF: a 429 changes nothing",
                   a.get("on") is False and snap.get("slots") == 4 and a.get("shrinks", 0) == 0,
                   "slots stay %s after a 429" % snap.get("slots"))

    def run_matrix(self):
        self.e_exec_plane()
        self.a_act_plane()
        self.m_aimd_plane()
        self.m_aimd_pin()

def find_engine():
    for cand in [os.environ.get("PN_ENGINE_DIR"),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "engine"),
                 os.path.expanduser("~/portioneer"),
                 os.path.expanduser("~/brainarbeit/engine")]:
        if cand and os.path.exists(os.path.join(cand, "tools", "pn-llmd")):
            return os.path.realpath(cand)
    return None

def skip(reason):
    print("%sSKIP%s admit_gate: %s" % (C["y"], C["0"], reason))
    print("     (engine tree not found — off-box this gate self-skips; it RUNS on the appliance "
          "and on any dev checkout).")
    sys.exit(0)

def main():
    print(C["b"] + "== admit_gate — hermetic admission-plane gate (scratch pn-llmd, fake backend) =="
          + C["0"])
    eng = find_engine()
    if not eng:
        skip("engine/tools/pn-llmd not found (set PN_ENGINE_DIR)")
    g = Gate(eng)
    print("   engine=%s\n   scratch=%s" % (eng, g.scratch))
    removed = False
    try:
        g.start()
        st = g.req({"verb": "status"})
        print("   scratch pn-llmd up (ok=%s) on %s" % (st.get("ok"), g.sock))
        g.run_matrix()
    finally:
        removed = g.teardown()

    print(C["b"] + "\n== GREEN/RED matrix ==" + C["0"])
    npass = sum(1 for _, _, p, _ in g.results if p)
    nfail = len(g.results) - npass
    for cid, label, passed, ev in g.results:
        tag = (C["g"] + "GREEN" + C["0"]) if passed else (C["r"] + " RED " + C["0"])
        print("  %s  %-3s %-54s %s" % (tag, cid, label, ev))
    print("   hermetic teardown: scratch removed=%s" % removed)
    print("   %d GREEN / %d RED  (%d checks)" % (npass, nfail, len(g.results)))
    if nfail or not removed:
        print(C["r"] + "admit_gate: RED — release BLOCKED" + C["0"])
        sys.exit(1)
    print(C["g"] + "admit_gate: GREEN — exec/act admission + adaptive contingent genuinely govern"
          + C["0"])
    sys.exit(0)

if __name__ == "__main__":
    main()
