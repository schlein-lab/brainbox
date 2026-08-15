#!/usr/bin/env python3

import argparse, base64, hashlib, json, os, socket, ssl, struct, sys, time, urllib.parse

class WS:
    MAX_FRAME = 8 << 20
    MAX_MSG   = 16 << 20
    def __init__(self, url, headers=None, insecure=False):
        self.url = url
        u = url.split("://", 1)
        self.secure = u[0] == "wss"
        rest = u[1]
        hostport, _, path = rest.partition("/")
        self.path = "/" + path
        if ":" in hostport:
            self.host, port = hostport.rsplit(":", 1); self.port = int(port)
        else:
            self.host = hostport; self.port = 443 if self.secure else 80
        self.headers = headers or {}
        self.insecure = insecure
        self.sock = None

    def connect(self):
        raw = socket.create_connection((self.host, self.port), timeout=15)
        if self.secure:
            ctx = ssl.create_default_context()
            if self.insecure:
                ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
            raw = ctx.wrap_socket(raw, server_hostname=self.host)
        self.sock = raw
        key = base64.b64encode(os.urandom(16)).decode()
        lines = ["GET %s HTTP/1.1" % self.path, "Host: %s:%d" % (self.host, self.port),
                 "Upgrade: websocket", "Connection: Upgrade",
                 "Sec-WebSocket-Key: %s" % key, "Sec-WebSocket-Version: 13"]
        for k, v in self.headers.items():
            lines.append("%s: %s" % (k, v))
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            d = self.sock.recv(4096)
            if not d:
                raise IOError("handshake closed")
            resp += d
        if b" 101 " not in resp.split(b"\r\n", 1)[0]:
            raise IOError("handshake failed: %s" % resp.split(b"\r\n", 1)[0].decode("latin1"))
        self._buf = resp.split(b"\r\n\r\n", 1)[1]

    def _recv_exact(self, n):
        while len(self._buf) < n:
            d = self.sock.recv(65536)
            if not d:
                raise IOError("closed")
            self._buf += d
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv(self):

        data = b""
        while True:
            b0, b1 = self._recv_exact(2)
            fin = b0 & 0x80
            opcode = b0 & 0x0F
            masked = b1 & 0x80
            ln = b1 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._recv_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._recv_exact(8))[0]
            if ln > self.MAX_FRAME:
                raise IOError("frame too large: %d" % ln)
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(ln)
            if masked:
                payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
            if opcode == 0x8:
                raise IOError("server close")
            if opcode == 0x9:
                self._send(0xA, payload); continue
            if opcode == 0xA:
                continue
            if opcode == 0x0:
                data += payload
            else:
                data = payload
            if len(data) > self.MAX_MSG:
                raise IOError("message too large: %d" % len(data))
            if fin:
                return data

    def _send(self, opcode, data=b""):
        b0 = 0x80 | opcode
        n = len(data)
        hdr = bytes([b0])
        mask = os.urandom(4)
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        self.sock.sendall(hdr + mask + masked)

    def send_text(self, s):
        self._send(0x1, s.encode())

    def close(self):
        try:
            self._send(0x8); self.sock.close()
        except Exception:
            pass

class Injector:
    def __init__(self, dry=False):
        self.dry = dry
        self.win = (os.name == "nt") and not dry
        if self.win:
            import ctypes
            from ctypes import wintypes
            self.ctypes = ctypes; self.wintypes = wintypes
            self.user32 = ctypes.windll.user32
            self._build_structs()
            self.SW = self.user32.GetSystemMetrics(0) or 1
            self.SH = self.user32.GetSystemMetrics(1) or 1

    def _build_structs(self):
        import ctypes
        from ctypes import wintypes
        ULONG_PTR = ctypes.POINTER(ctypes.c_ulong)

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class _U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        self.MOUSEINPUT, self.KEYBDINPUT, self.INPUT = MOUSEINPUT, KEYBDINPUT, INPUT

    def _send_mouse(self, dx=0, dy=0, flags=0, data=0):
        inp = self.INPUT(); inp.type = 0
        inp.u.mi = self.MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, None)
        self.user32.SendInput(1, self.ctypes.byref(inp), self.ctypes.sizeof(inp))

    def _send_key(self, vk=0, scan=0, flags=0):
        inp = self.INPUT(); inp.type = 1
        inp.u.ki = self.KEYBDINPUT(vk, scan, flags, 0, None)
        self.user32.SendInput(1, self.ctypes.byref(inp), self.ctypes.sizeof(inp))

    _BTN = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}

    def handle(self, ev):
        t = ev.get("t")
        if self.dry or not self.win:
            print("  [dry] %s" % ev); return
        if t == "move":
            x = int(max(0.0, min(1.0, ev.get("x", 0))) * 65535)
            y = int(max(0.0, min(1.0, ev.get("y", 0))) * 65535)
            self._send_mouse(x, y, 0x8000 | 0x0001)
        elif t == "moverel":
            self._send_mouse(int(ev.get("dx", 0)), int(ev.get("dy", 0)), 0x0001)
        elif t == "button":
            down, up = self._BTN.get(ev.get("btn", "left"), self._BTN["left"])
            self._send_mouse(0, 0, down if ev.get("down", True) else up)
        elif t == "click":
            down, up = self._BTN.get(ev.get("btn", "left"), self._BTN["left"])
            self._send_mouse(0, 0, down); self._send_mouse(0, 0, up)
        elif t == "scroll":
            if ev.get("dy"):
                self._send_mouse(0, 0, 0x0800, int(ev["dy"]))
            if ev.get("dx"):
                self._send_mouse(0, 0, 0x01000, int(ev["dx"]))
        elif t == "key":
            self._send_key(int(ev.get("vk", 0)), 0, 0 if ev.get("down", True) else 0x0002)
        elif t == "text":
            units = str(ev.get("s", ""))[:4096].encode("utf-16-le")
            for i in range(0, len(units), 2):
                code = units[i] | (units[i + 1] << 8)
                self._send_key(0, code, 0x0004)
                self._send_key(0, code, 0x0004 | 0x0002)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--box", required=True)
    ap.add_argument("--agent", default=socket.gethostname())
    ap.add_argument("--key", default=""); ap.add_argument("--token", default="")
    ap.add_argument("--insecure", action="store_true"); ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    q = "agent=" + urllib.parse.quote(a.agent, safe="")
    url = a.box.rstrip("/") + "/ws/devinput?" + q
    headers = {}
    if a.key:
        headers["X-API-Key"] = a.key
    if a.token:
        headers["X-PP-Token"] = a.token
    inj = Injector(dry=a.dry)
    print("Brainbox input agent '%s' -> %s  (%s)" % (a.agent, a.box, "DRY" if a.dry else ("Windows" if inj.win else "no-inject")))
    backoff = 1
    while True:
        try:
            ws = WS(url, headers=headers, insecure=a.insecure)
            ws.connect()
            print("connected."); backoff = 1
            while True:
                msg = ws.recv()
                try:
                    obj = json.loads(msg.decode("utf-8"))
                except Exception:
                    continue
                for ev in obj.get("events", []):
                    try:
                        inj.handle(ev)
                    except Exception as e:
                        print("  inject error:", e)
        except KeyboardInterrupt:
            print("bye"); return
        except Exception as e:
            print("disconnected (%s); retry in %ds" % (e, backoff))
            time.sleep(backoff); backoff = min(15, backoff * 2)

if __name__ == "__main__":
    main()
