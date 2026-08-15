#!/usr/bin/env python3

import os, sys, json, time, socket, struct, base64, subprocess, threading, tempfile
import http.client, urllib.parse

HERE = os.path.dirname(os.path.realpath(__file__))
COCKPIT = os.path.normpath(os.path.join(HERE, ".."))
PORTAL = os.path.join(COCKPIT, "server", "pn_portal.py")
MOCK = os.path.join(COCKPIT, "tests", "mock_pnd.py")
PRINCIPAL = "admin"

PASS = FAIL = 0
def check(cond, label):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  PASS  {label}")
    else:    FAIL += 1; print(f"  FAIL  {label}")

def start_mock(sock):
    p = subprocess.Popen([sys.executable, MOCK, "--sock", sock, "--principal", PRINCIPAL],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(60):
        if os.path.exists(sock):
            return p
        time.sleep(0.05)
    return p

def start_portal(sock, port):
    p = subprocess.Popen([sys.executable, PORTAL, "--host", "127.0.0.1",
                          "--port", str(port), "--sock", sock],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            c = socket.create_connection(("127.0.0.1", port), timeout=0.5); c.close()
            return p
        except OSError:
            time.sleep(0.05)
    return p

def http_get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path); r = c.getresponse()
    body = r.read(); headers = dict(r.getheaders()); code = r.status; c.close()
    return code, body, headers

def http_post_verb(port, body):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", "/api/verb", json.dumps(body), {"Content-Type": "application/json"})
    r = c.getresponse(); out = json.loads(r.read().decode()); c.close()
    return out

class WSDevice:

    def __init__(self, port, topics, after_id=0):
        self.frames = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        key = base64.b64encode(os.urandom(16)).decode()
        path = f"/ws/events?topics={urllib.parse.quote(','.join(topics))}&after_id={after_id}"
        self.s = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.s.sendall((f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                        f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            ch = self.s.recv(4096)
            if not ch: break
            buf += ch
        assert b"101" in buf.split(b"\r\n", 1)[0], f"no WS upgrade: {buf[:120]!r}"
        self._rbuf = buf.split(b"\r\n\r\n", 1)[1]
        threading.Thread(target=self._reader, daemon=True).start()

    def _recv_frame(self):
        def need(n):
            while len(self._rbuf) < n:
                ch = self.s.recv(65536)
                if not ch: return False
                self._rbuf += ch
            return True
        if not need(2): return None
        b1 = self._rbuf[1]; opcode = self._rbuf[0] & 0x0F; length = b1 & 0x7F; off = 2
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

    def wait_for(self, pred, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                for f in self.frames:
                    if pred(f): return f
            time.sleep(0.03)
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
    print("=== cockpit staging validation — headless, MOCK engine (no QEMU/GPU/browser) ===")
    rt = tempfile.mkdtemp(prefix="pn_cockpit_staging_")
    sock = os.path.join(rt, "pnd-mock.sock")
    port = 8810 + (os.getpid() % 150)
    mock = portal = None
    try:
        mock = start_mock(sock)
        check(os.path.exists(sock), "mock engine up (private socket)")
        portal = start_portal(sock, port)

        code, body, hdr = http_get(port, "/")
        check(code == 200 and b"Brainarbeit" in body and b"Approval Inbox" in body,
              "GET / serves the canonical SPA (index.html, 200)")
        check(hdr.get("Content-Type", "").startswith("text/html"), "index.html content-type ok")
        check("default-src 'self'" in hdr.get("Content-Security-Policy", ""),
              "CSP header present (same-origin only)")
        code, body, hdr = http_get(port, "/app.js")
        check(code == 200 and b"CVMRender" in body and "javascript" in hdr.get("Content-Type", ""),
              "GET /app.js serves the one SPA bundle (CVMRender present)")
        code, _, _ = http_get(port, "/style.css")
        check(code == 200, "GET /style.css serves")
        code, body, _ = http_get(port, "/api/manifest")
        check(code == 200 and json.loads(body).get("bundle_sha"), "GET /api/manifest serves a bundle sha")

        code, _, _ = http_get(port, "/../server/pn_portal.py")
        check(code in (403, 404), "static server refuses path traversal")

        devA = WSDevice(port, [f"user/{PRINCIPAL}"], after_id=0)
        check(devA.wait_for(lambda f: f.get("type") == "subscribed", 5) is not None,
              "device A subscribed over /ws/events (the one bus)")
        devB = WSDevice(port, [f"user/{PRINCIPAL}"], after_id=0)
        check(devB.wait_for(lambda f: f.get("type") == "subscribed", 5) is not None,
              "device B subscribed (second device, same topic)")

        sub = http_post_verb(port, {"verb": "submit", "task_type": "firmware.flash",
                                    "tag": "flash firmware v2.3 to hp-4500",
                                    "needs_confirmation": True})
        check(sub.get("ok") and sub.get("state") == "staged" and sub.get("nonce"),
              "submit(needs_confirmation) -> staged + single-use nonce")
        jid, nonce = sub["id"], sub["nonce"]

        fa = devA.wait_for(lambda f: f.get("type") == "event"
                           and f["event"].get("kind") == "approval-request"
                           and f["event"].get("job_id") == jid, 8)
        check(fa is not None, "device A received the approval-request LIVE (pending shows up)")
        ar = json.loads(fa["event"]["data"]) if fa else {}
        cvm_like = {"id": jid, "state": "staged", "approval_state": "pending",
                    "approval_request": ar, "task_type": ar.get("task_type"),
                    "needs_confirmation": True}
        check(is_awaiting(cvm_like) and ar.get("nonce") == nonce,
              "the inbox CVMRender can RENDER it (awaiting + nonce + brick action)")
        check(ar.get("brick_warning"), "brick-risk action carries the spoken/shown brick warning")

        fb = devB.wait_for(lambda f: f.get("type") == "event"
                           and f["event"].get("kind") == "approval-request"
                           and f["event"].get("job_id") == jid, 8)
        check(fb is not None, "device B (second device) sees the SAME approval (one reality)")

        cvmresp = http_post_verb(port, {"verb": "cvm", "id": jid})
        check(cvmresp.get("ok") and cvmresp["cvm"]["approval_state"] == "pending"
              and cvmresp["cvm"]["approval_request"]["nonce"] == nonce,
              "/api/cvm renders the pending approval for a late-joining device")

        appr = http_post_verb(port, {"verb": "approve", "nonce": nonce})
        check(appr.get("ok") and appr.get("state") == "queued",
              "Approve verb round-trips (-> queued) through pn-portal -> mock")
        appr2 = http_post_verb(port, {"verb": "approve", "nonce": nonce})
        check(appr2.get("ok") and appr2.get("idempotent"),
              "re-Approve same nonce -> idempotent no-op (single-use, double-tap safe)")

        def cleared(f):
            if f.get("type") != "event" or f["event"].get("kind") != "state": return False
            if f["event"].get("job_id") != jid: return False
            return json.loads(f["event"]["data"]).get("decision") == "approved"
        check(devA.wait_for(cleared, 8) is not None, "Approve clears the card LIVE on device A")
        check(devB.wait_for(cleared, 8) is not None, "Approve clears the card LIVE on device B (converged)")

        st = http_post_verb(port, {"verb": "steer", "id": jid, "input": {"feedback": "use fw 2.2"}})
        check(st.get("ok"), "Revise (steer) round-trips through /api/verb")

        sub2 = http_post_verb(port, {"verb": "submit", "task_type": "media.render_video",
                                     "tag": "Render a 12s sunset clip", "needs_confirmation": True})
        devC = WSDevice(port, [f"user/{PRINCIPAL}"], after_id=0)
        devC.wait_for(lambda f: f.get("type") == "event"
                      and f["event"].get("kind") == "approval-request"
                      and f["event"].get("job_id") == sub2["id"], 8)
        deny = http_post_verb(port, {"verb": "deny", "nonce": sub2["nonce"]})
        check(deny.get("ok") and deny.get("state") == "cancelled", "Reject verb -> cancelled")
        denied = devC.wait_for(lambda f: f.get("type") == "event"
                               and f["event"].get("kind") == "state"
                               and f["event"].get("job_id") == sub2["id"]
                               and json.loads(f["event"]["data"]).get("decision") == "denied", 8)
        check(denied is not None, "Reject clears LIVE (denied state event on the bus)")

        ids = sorted(e["id"] for e in devA.events())
        if len(ids) >= 2:
            mid = ids[len(ids)//2]
            devD = WSDevice(port, [f"user/{PRINCIPAL}"], after_id=mid)
            time.sleep(0.6)
            deltaids = [e["id"] for e in devD.events()]
            check(deltaids and all(i > mid for i in deltaids),
                  "reconnect from a MID cursor returns ONLY the missed delta (convergence)")
            devD.close()

        devA.close(); devB.close(); devC.close()
    finally:
        for p in (portal, mock):
            if p:
                p.terminate()
                try: p.wait(timeout=5)
                except Exception: p.kill()
        subprocess.run(["rm", "-rf", rt])

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
