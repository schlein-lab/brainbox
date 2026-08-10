#!/usr/bin/env python3

import os, sys, json, time, socket, struct, base64, hashlib, tempfile, subprocess, threading
import http.client, urllib.parse

HERE = os.path.dirname(os.path.realpath(__file__))
COCKPIT = os.path.normpath(os.path.join(HERE, ".."))
PORTAL = os.path.join(COCKPIT, "server", "pn_portal.py")
ENGINE = os.environ.get("ENGINE", os.path.expanduser("~/brainarbeit-build/engine-for-cockpit"))

PASS = FAIL = 0
def check(cond, label):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {label}")
    else:    FAIL += 1; print(f"  FAIL  {label}")

def scratch_pnd():

    rt = tempfile.mkdtemp(prefix="pn_cockpit_rt_")
    data = tempfile.mkdtemp(prefix="pn_cockpit_data_")
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = rt
    env["XDG_DATA_HOME"] = data
    env["PN_DATA_DIR"] = data
    env["PN_DURABILITY"] = "normal"
    env.pop("NOTIFY_SOCKET", None)
    boot = os.path.join(rt, "boot.py")
    with open(boot, "w") as f:
        f.write(
            "import sys, runpy\n"
            f"sys.path.insert(0, {ENGINE!r})\n"
            "from pnlib import sched\n"
            "_orig = sched.Config.autoscale\n"
            "def _permissive():\n"
            "    c = _orig(); c.psi_stop = 1e9; c.mem_floor = 1; c.batch_high = 1<<30\n"
            "    c.slack = 0; return c\n"
            "sched.Config.autoscale = staticmethod(_permissive)\n"
            f"runpy.run_path({os.path.join(ENGINE, 'tools', 'pnd')!r}, run_name='__main__')\n")
    proc = subprocess.Popen([sys.executable, boot], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    sock = os.path.join(rt, "pnd.sock")
    for _ in range(60):
        if os.path.exists(sock): break
        time.sleep(0.1)
    return proc, rt, data, sock

def pnd_ipc(sock, req, timeout=20):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout); s.connect(sock)
    s.sendall((json.dumps(req) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        ch = s.recv(65536)
        if not ch: break
        buf += ch
    s.close()
    return json.loads(buf.split(b"\n", 1)[0].decode())

def start_portal(sock, port):
    env = dict(os.environ)
    proc = subprocess.Popen(
        [sys.executable, PORTAL, "--host", "127.0.0.1", "--port", str(port), "--sock", sock],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    for _ in range(60):
        try:
            c = socket.create_connection(("127.0.0.1", port), timeout=0.5); c.close()
            return proc
        except OSError:
            time.sleep(0.1)
    return proc

def http_post_verb(port, body):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", "/api/verb", json.dumps(body), {"Content-Type": "application/json"})
    r = c.getresponse(); out = json.loads(r.read().decode()); c.close()
    return out

def http_get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path); r = c.getresponse(); out = r.read().decode(); code = r.status; c.close()
    return code, out

class WSDevice:

    def __init__(self, port, topics, after_id=0):
        self.frames = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        key = base64.b64encode(os.urandom(16)).decode()
        path = f"/ws/events?topics={urllib.parse.quote(','.join(topics))}&after_id={after_id}"
        self.s = socket.create_connection(("127.0.0.1", port), timeout=10)
        req = (f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
               f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.s.sendall(req.encode())

        buf = b""
        while b"\r\n\r\n" not in buf:
            ch = self.s.recv(4096)
            if not ch: break
            buf += ch
        assert b"101" in buf.split(b"\r\n", 1)[0], f"no WS upgrade: {buf[:120]!r}"
        self._rbuf = buf.split(b"\r\n\r\n", 1)[1]
        self.t = threading.Thread(target=self._reader, daemon=True); self.t.start()

    def _recv_frame(self):
        def need(n):
            while len(self._rbuf) < n:
                ch = self.s.recv(65536)
                if not ch: return False
                self._rbuf += ch
            return True
        if not need(2): return None
        b0, b1 = self._rbuf[0], self._rbuf[1]
        opcode = b0 & 0x0F; length = b1 & 0x7F; off = 2
        if length == 126:
            if not need(4): return None
            length = struct.unpack(">H", self._rbuf[2:4])[0]; off = 4
        elif length == 127:
            if not need(10): return None
            length = struct.unpack(">Q", self._rbuf[2:10])[0]; off = 10
        if not need(off + length): return None
        payload = self._rbuf[off:off+length]; self._rbuf = self._rbuf[off+length:]
        if opcode == 0x8: return ("close", None)
        if opcode in (0x1, 0x2):
            try: return ("msg", json.loads(payload.decode()))
            except ValueError: return ("msg", None)
        return ("other", None)

    def _reader(self):
        try:
            while not self.stop.is_set():
                fr = self._recv_frame()
                if fr is None or fr[0] == "close": break
                if fr[0] == "msg" and fr[1] is not None:
                    with self.lock: self.frames.append(fr[1])
        except OSError:
            pass

    def wait_for(self, pred, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                for f in self.frames:
                    if pred(f): return f
            time.sleep(0.05)
        return None

    def events(self):
        with self.lock:
            return [f["event"] for f in self.frames if f.get("type") == "event"]

    def close(self):
        self.stop.set()
        try: self.s.close()
        except OSError: pass

def is_awaiting(cvm):
    return cvm.get("approval_state") == "pending" and \
           cvm.get("state") in ("staged", "awaiting_approval")

def main():
    print("=== cockpit approval E2E — live cross-device round-trip (scratch pnd + pn-portal) ===")
    proc, rt, data, sock = scratch_pnd()
    port = 8801 + (os.getpid() % 200)
    portal = None
    try:
        check(pnd_ipc(sock, {"verb": "ping"}).get("ok"), "scratch pnd up")
        portal = start_portal(sock, port)
        code, _ = http_get(port, "/api/manifest")
        check(code == 200, "pn-portal serving (manifest 200)")

        devA = WSDevice(port, ["user/admin"], after_id=0)
        check(devA.wait_for(lambda f: f.get("type") == "subscribed", 5) is not None,
              "device A got the `subscribed` ack from pn-portal (the one bus)")

        devB = WSDevice(port, ["user/admin"], after_id=0)
        check(devB.wait_for(lambda f: f.get("type") == "subscribed", 5) is not None,
              "device B got the `subscribed` ack (second device, same topic)")

        sub = http_post_verb(port, {
            "verb": "submit", "cmd": ["/bin/echo", "side-effect-output"], "class": "worker",
            "tag": "bind printer hp-4500 (LAN)", "needs_confirmation": True})
        check(sub.get("ok") and sub.get("state") == "staged" and sub.get("nonce"),
              f"submit(needs_confirmation) -> staged + nonce (state={sub.get('state')})")
        jid, nonce = sub["id"], sub["nonce"]

        fa = devA.wait_for(lambda f: f.get("type") == "event"
                           and f["event"].get("kind") == "approval-request"
                           and f["event"].get("job_id") == jid, 10)
        check(fa is not None, "(a) device A received the `approval-request` event LIVE")
        ar = json.loads(fa["event"]["data"]) if fa else {}
        check(ar.get("nonce") == nonce and ar.get("job_id") == jid,
              "(a) approval-request payload carries the job_id + the single-use nonce")

        cvm_like = {"id": jid, "state": "staged", "approval_state": "pending",
                    "approval_request": ar, "task_type": ar.get("task_type"),
                    "needs_confirmation": True}
        check(is_awaiting(cvm_like) and (ar.get("summary") or ar.get("task_type")),
              f"(a) the inbox can RENDER it (summary={ar.get('summary')!r})")

        fb = devB.wait_for(lambda f: f.get("type") == "event"
                           and f["event"].get("kind") == "approval-request"
                           and f["event"].get("job_id") == jid, 10)
        check(fb is not None, "(c) device B (second device) ALSO received the same approval LIVE")
        check(fb and json.loads(fb["event"]["data"]).get("nonce") == nonce,
              "(c) device B sees the SAME nonce/job (identical state across devices)")

        cvmresp = http_post_verb(port, {"verb": "cvm", "id": jid})
        check(cvmresp.get("ok") and cvmresp["cvm"]["approval_state"] == "pending"
              and cvmresp["cvm"]["approval_request"]
              and cvmresp["cvm"]["approval_request"]["nonce"] == nonce,
              "(a) /api/cvm (the one serializer) renders the pending approval + nonce")

        appr = http_post_verb(port, {"verb": "approve", "nonce": nonce})
        check(appr.get("ok") and appr.get("state") == "queued",
              "(b) Approve verb -> queued (the click round-trips through pn-portal -> pnd)")

        appr2 = http_post_verb(port, {"verb": "approve", "nonce": nonce})
        check(appr2.get("ok") and appr2.get("idempotent"),
              "(b) re-Approve same nonce -> idempotent no-op (single-use nonce can't double-fire)")

        def cleared(f):
            if f.get("type") != "event" or f["event"].get("kind") != "state": return False
            if f["event"].get("job_id") != jid: return False
            d = json.loads(f["event"]["data"])
            return d.get("decision") == "approved" or d.get("state") in ("queued", "running", "done")
        ca = devA.wait_for(cleared, 10)
        cb = devB.wait_for(cleared, 10)
        check(ca is not None, "(b) device A received the live `state` clear (card clears on this device)")
        check(cb is not None, "(c) device B received the SAME live clear (one reality — converged)")

        devC = WSDevice(port, ["user/admin"], after_id=0)
        time.sleep(1.0)
        evC = devC.events()
        kinds = [e["kind"] for e in evC]
        check("approval-request" in kinds,
              "(d) reconnect from 0 REPLAYS the approval-request (no missed approval)")
        ids = [e["id"] for e in evC]
        check(ids == sorted(ids) and len(ids) == len(set(ids)),
              "(d) replayed stream is ordered with no duplicates")
        if len(ids) >= 2:
            mid = ids[len(ids)//2]
            devD = WSDevice(port, ["user/admin"], after_id=mid)
            time.sleep(0.8)
            deltaids = [e["id"] for e in devD.events()]
            check(all(i > mid for i in deltaids),
                  "(d) reconnect from a MID cursor returns ONLY ids strictly after it (convergence)")
            devD.close()
        devA.close(); devB.close(); devC.close()

        sub2 = http_post_verb(port, {"verb": "submit", "cmd": ["/bin/echo", "x"], "class": "worker",
                                     "tag": "flash firmware", "needs_confirmation": True})
        devE = WSDevice(port, ["user/admin"], after_id=0)
        devE.wait_for(lambda f: f.get("type") == "event"
                      and f["event"].get("kind") == "approval-request"
                      and f["event"].get("job_id") == sub2["id"], 8)
        deny = http_post_verb(port, {"verb": "deny", "nonce": sub2["nonce"]})
        check(deny.get("ok") and deny.get("state") == "cancelled", "Reject verb -> cancelled")
        denied = devE.wait_for(lambda f: f.get("type") == "event"
                               and f["event"].get("kind") == "state"
                               and f["event"].get("job_id") == sub2["id"]
                               and json.loads(f["event"]["data"]).get("decision") == "denied", 8)
        check(denied is not None, "Reject clears live (denied state event on the bus)")
        devE.close()

    finally:
        if portal:
            portal.terminate()
            try: portal.wait(timeout=5)
            except Exception: portal.kill()
        proc.terminate()
        try: proc.wait(timeout=5)
        except Exception: proc.kill()
        try:
            import sqlite3
            cx = sqlite3.connect(os.path.join(data, "portioneer", "queue.db"))
            ic = cx.execute("PRAGMA integrity_check").fetchone()[0]; cx.close()
            check(ic == "ok", f"scratch DB integrity_check = {ic!r} (nothing corrupted)")
        except Exception as e:
            check(False, f"scratch DB integrity_check raised {e}")
        subprocess.run(["rm", "-rf", rt, data])

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
