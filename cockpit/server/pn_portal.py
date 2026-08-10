#!/usr/bin/env python3

from __future__ import annotations
import os, json, socket, struct, base64, hashlib, threading, ssl, argparse, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
HERE = os.path.dirname(os.path.realpath(__file__))
WEB_DIR = os.path.normpath(os.path.join(HERE, "..", "web"))
WS_MAX_PAYLOAD = 8 << 20

class Pnd:
    def __init__(self, sock_path):
        self.sock_path = sock_path

    def _connect(self, timeout):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(self.sock_path)
        return s

    def call(self, req, timeout=15):

        s = self._connect(timeout)
        try:
            s.sendall((json.dumps(req) + "\n").encode())
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
            line = buf.split(b"\n", 1)[0]
            return json.loads(line.decode()) if line else {"ok": False, "error": "empty"}
        finally:
            s.close()

    def stream(self, req):

        s = self._connect(timeout=None)
        s.settimeout(None)
        s.sendall((json.dumps(req) + "\n").encode())

        def frames():
            buf = b""
            while True:
                try:
                    chunk = s.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        try:
                            yield json.loads(line.decode())
                        except ValueError:
                            pass
        return s, frames()

def ws_accept_key(client_key: str) -> str:
    return base64.b64encode(hashlib.sha1((client_key + WS_GUID).encode()).digest()).decode()

def ws_send_text(conn, payload: str):
    data = payload.encode("utf-8")
    header = bytearray([0x81])
    n = len(data)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126); header += struct.pack(">H", n)
    else:
        header.append(127); header += struct.pack(">Q", n)
    conn.sendall(bytes(header) + data)

def ws_send_close(conn):
    try:
        conn.sendall(bytes([0x88, 0x00]))
    except OSError:
        pass

def ws_read_frame(conn):

    def recvn(n):
        out = b""
        while len(out) < n:
            ch = conn.recv(n - len(out))
            if not ch:
                return None
            out += ch
        return out
    hdr = recvn(2)
    if not hdr:
        return None, None
    b0, b1 = hdr[0], hdr[1]
    opcode = b0 & 0x0F
    masked = b1 & 0x80
    length = b1 & 0x7F
    if length == 126:
        ext = recvn(2)
        if not ext:
            return None, None
        length = struct.unpack(">H", ext)[0]
    elif length == 127:
        ext = recvn(8)
        if not ext:
            return None, None
        length = struct.unpack(">Q", ext)[0]
    if length > WS_MAX_PAYLOAD:
        return None, None
    mask = recvn(4) if masked else b"\x00\x00\x00\x00"
    if mask is None:
        return None, None
    payload = recvn(length) if length else b""
    if payload is None and length:
        return None, None
    payload = payload or b""
    if masked:
        payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
    return opcode, payload

class Handler(BaseHTTPRequestHandler):
    pnd: Pnd = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _serve_file(self, rel):
        path = os.path.normpath(os.path.join(WEB_DIR, rel.lstrip("/")))
        if not path.startswith(WEB_DIR) or not os.path.isfile(path):
            self.send_error(404); return
        ctype = ("text/html; charset=utf-8" if path.endswith(".html")
                 else "application/javascript" if path.endswith(".js")
                 else "text/css" if path.endswith(".css")
                 else "application/octet-stream")
        body = open(path, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))

        self.send_header("Content-Security-Policy",
                         "default-src 'self'; connect-src 'self' ws: wss:; "
                         "style-src 'self' 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if u.path == "/ws/events":
            return self._ws_events(qs)
        if u.path == "/api/cvm":
            jid = qs.get("id", [None])[0]
            try:
                jid = int(jid)
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "bad id"}, 400)
            return self._json(self.pnd.call({"verb": "cvm", "id": jid}))
        if u.path == "/api/replay":
            topics = qs.get("topics", [])
            if len(topics) == 1 and "," in topics[0]:
                topics = topics[0].split(",")
            after = int(qs.get("after", ["0"])[0])
            return self._json(self.pnd.call(
                {"verb": "replay", "topics": topics, "after_id": after}))
        if u.path == "/api/manifest":

            try:
                sha = hashlib.sha256(
                    open(os.path.join(WEB_DIR, "app.js"), "rb").read()).hexdigest()[:16]
            except OSError:
                sha = "unknown"
            return self._json({"ok": True, "bundle_sha": sha, "min_shell": "1.0.0"})
        if u.path in ("/", "/index.html"):
            return self._serve_file("index.html")
        return self._serve_file(u.path)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/api/verb":
            return self.send_error(404)
        length = int(self.headers.get("Content-Length", "0"))
        try:
            req = json.loads(self.rfile.read(length).decode() or "{}")
        except ValueError:
            return self._json({"ok": False, "error": "bad json"}, 400)
        verb = req.get("verb")

        ALLOWED = {"approve", "deny", "steer", "submit", "cancel", "job", "list",
                   "my-jobs", "status", "log", "cvm", "replay", "ping"}
        if verb not in ALLOWED:
            return self._json({"ok": False, "error": f"verb not allowed: {verb}"}, 403)

        req.pop("principal", None)
        req.pop("uid", None)
        req.pop("_peer_uid", None)
        return self._json(self.pnd.call(req))

    def _ws_events(self, qs):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key or self.headers.get("Upgrade", "").lower() != "websocket":
            return self.send_error(400, "expected websocket upgrade")
        topics = qs.get("topics", [])
        if len(topics) == 1 and "," in topics[0]:
            topics = topics[0].split(",")
        after = qs.get("after_id", [None])[0]
        after = int(after) if (after is not None and after != "") else None

        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", ws_accept_key(key))
        self.end_headers()
        conn = self.connection

        sub_req = {"verb": "subscribe", "topics": topics}
        if after is not None:
            sub_req["after_id"] = after
        psock, frames = self.pnd.stream(sub_req)

        stop = threading.Event()

        def client_reader():
            while not stop.is_set():
                op, _payload = ws_read_frame(conn)
                if op is None or op == 0x8:
                    break
            stop.set()
            try:
                psock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        tr = threading.Thread(target=client_reader, daemon=True)
        tr.start()

        try:
            for frame in frames:
                if stop.is_set():
                    break
                ws_send_text(conn, json.dumps(frame, separators=(",", ":")))
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            stop.set()
            try:
                psock.close()
            except OSError:
                pass
            ws_send_close(conn)

        self.close_connection = True

def make_server(host, port, sock_path, certfile=None, keyfile=None):
    srv = ThreadingHTTPServer((host, port), Handler)
    Handler.pnd = Pnd(sock_path)
    if certfile and keyfile:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile, keyfile)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    return srv

def main():
    ap = argparse.ArgumentParser(description="pn-portal — cockpit contract adapter")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--sock", default=os.environ.get("PN_SOCK")
                    or os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
                                    "pnd.sock"),
                    help="path to the pnd unix socket (the one bus)")
    ap.add_argument("--cert", default=None)
    ap.add_argument("--key", default=None)
    args = ap.parse_args()
    srv = make_server(args.host, args.port, args.sock, args.cert, args.key)
    scheme = "https" if args.cert else "http"
    print(f"pn-portal on {scheme}://{args.host}:{args.port}  -> pnd {args.sock}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
