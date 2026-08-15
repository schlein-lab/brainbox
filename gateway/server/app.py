#!/usr/bin/env python3

from __future__ import annotations
import os, sys, json, socket, struct, base64, hashlib, threading, ssl, argparse, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.realpath(__file__))

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
    from gateway.server.config import Config, assert_safe_to_start
    from gateway.server.pnd_client import PndClient
    from gateway.server.auth.tokens import parse_credential, AuthError
    from gateway.server.auth.principal import Authenticator, RateLimited
    from gateway.server.adapters import submit as submit_adapter
    from gateway.server.adapters import media as media_adapter
    from gateway.server.adapters.webhooks import validate_url, WebhookError
else:
    from .config import Config, assert_safe_to_start
    from .pnd_client import PndClient
    from .auth.tokens import parse_credential, AuthError
    from .auth.principal import Authenticator, RateLimited
    from .adapters import submit as submit_adapter
    from .adapters import media as media_adapter
    from .adapters.webhooks import validate_url, WebhookError

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

READ_VERBS = {"job", "cvm", "result", "log", "events", "my-history", "list", "my-jobs",
              "my-outputs", "whereis", "group", "handoff", "pending", "status", "replay", "ping"}
CONTROL_VERBS = {"submit", "submit-dag", "approve", "reject", "revise", "deny", "steer", "cancel",
                 "egress-pending", "egress-approve", "egress-deny"}

def ws_accept_key(k): return base64.b64encode(hashlib.sha1((k + WS_GUID).encode()).digest()).decode()

def ws_send(conn, payload, opcode=0x1):
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    h = bytearray([0x80 | opcode])
    n = len(data)
    if n < 126:
        h.append(n)
    elif n < 65536:
        h.append(126); h += struct.pack(">H", n)
    else:
        h.append(127); h += struct.pack(">Q", n)
    conn.sendall(bytes(h) + data)

def ws_close(conn):
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
    op = hdr[0] & 0x0F
    masked = hdr[1] & 0x80
    ln = hdr[1] & 0x7F
    if ln == 126:
        ln = struct.unpack(">H", recvn(2))[0]
    elif ln == 127:
        ln = struct.unpack(">Q", recvn(8))[0]
    mask = recvn(4) if masked else b"\x00\x00\x00\x00"
    payload = recvn(ln) if ln else b""
    if payload is None:
        return None, None
    if masked:
        payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
    return op, payload

class Handler(BaseHTTPRequestHandler):
    cfg: Config = None
    pnd: PndClient = None
    authn: Authenticator = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200, extra=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _err(self, msg, code, extra=None):
        return self._json({"ok": False, "error": msg}, code, extra)

    def _auth(self):

        try:
            cred = parse_credential(self.headers)
            return self.authn.authenticate(cred)
        except AuthError as e:
            self._err(str(e), 401, {"WWW-Authenticate": "Bearer",
                                    "X-Brainarbeit-Need-2FA": "1" if e.need_2fa else "0"})
            return None
        except RateLimited as e:
            self._err(str(e), 429)
            return None

    def _read_json_body(self):
        n = int(self.headers.get("Content-Length", "0"))
        try:
            return json.loads(self.rfile.read(n).decode() or "{}")
        except ValueError:
            return None

    def _p(self):
        return self.cfg.API_PREFIX

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        p = u.path
        pre = self._p()

        if p == f"{pre}/health":
            return self._json({"ok": True, "version": self.cfg.VERSION,
                               "engine": self.pnd.call({"verb": "ping"})})
        if p in (f"{pre}/openapi.yaml", "/openapi.yaml"):
            return self._serve_spec("openapi.yaml")
        if p in (f"{pre}/asyncapi.yaml", "/asyncapi.yaml"):
            return self._serve_spec("asyncapi.yaml")

        if p == f"{pre}/stream":
            return self._ws_stream(qs)
        if p == f"{pre}/media/screen":
            return self._ws_screen(qs)

        prin = self._auth()
        if prin is None:
            return

        if p == f"{pre}/jobs":
            return self._proxy({"verb": "list", "limit": _int(qs, "limit", 50)}, prin)
        if p == f"{pre}/jobs/mine":
            req = {"verb": "my-jobs", "limit": _int(qs, "limit", 50)}
            if "state" in qs:
                req["state"] = qs["state"][0]
            return self._proxy(req, prin)
        if p == f"{pre}/outputs":
            return self._proxy({"verb": "my-outputs", "limit": _int(qs, "limit", 200)}, prin)
        if p == f"{pre}/approvals":
            return self._proxy({"verb": "pending", "limit": _int(qs, "limit", 100)}, prin)
        if p == f"{pre}/engine/status":
            return self._proxy({"verb": "status"}, prin)
        if p == f"{pre}/egress":
            return self._proxy({"verb": "egress-pending"}, prin)
        if p == f"{pre}/stream/replay":
            topics = _topics(qs)
            return self._proxy({"verb": "replay", "topics": topics,
                                "after_id": _int(qs, "after_id", 0)}, prin)
        if p == f"{pre}/media/webrtc":
            sig = media_adapter.WebRtcSignaling(self.cfg)
            return self._json({"ok": True, **sig.info()},
                              200 if sig.available else 503)

        seg = p[len(pre):].strip("/").split("/")
        if len(seg) >= 2 and seg[0] == "jobs":
            jid = _to_int(seg[1])
            if jid is None:
                return self._err("bad job id", 400)
            sub = seg[2] if len(seg) > 2 else None
            vmap = {None: "job", "cvm": "cvm", "result": "result", "log": "log",
                    "events": "events", "history": "my-history", "whereis": "whereis",
                    "handoff": "handoff"}
            if sub in vmap:
                req = {"verb": vmap[sub], "id": jid}
                if sub in ("log",):
                    req["bytes"] = _int(qs, "bytes", 8000)
                if sub in ("events", "history"):
                    req["limit"] = _int(qs, "limit", 200)
                return self._proxy(req, prin)
        if len(seg) == 2 and seg[0] == "groups":
            return self._proxy({"verb": "group", "group_id": seg[1]}, prin)

        return self._err("not found", 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        pre = self._p()

        prin = self._auth()
        if prin is None:
            return

        body = self._read_json_body()
        if body is None:
            return self._err("bad json", 400)

        if p == f"{pre}/media/ticket":
            secret = self._media_secret(prin.device_did)
            bind = self._bind_for_did(prin.device_did)
            return self._json({"ok": True,
                               "ticket": media_adapter.mint_ticket(secret, prin.device_did,
                                                                   bind=bind)})

        if p == f"{pre}/jobs":
            try:
                return self._json(submit_adapter.submit(self.cfg, self.pnd, prin, body))
            except submit_adapter.SubmitError as e:
                return self._err(str(e), e.code)
        if p == f"{pre}/jobs/dag":
            req = {"verb": "submit-dag", "nodes": body.get("nodes"),
                   "group_id": body.get("group_id"), "parent_job": body.get("parent_job")}
            return self._proxy(req, prin)

        if p == f"{pre}/webhooks":
            try:
                url = validate_url(self.cfg, body.get("url", ""))
            except WebhookError as e:
                return self._err(str(e), e.code)

            secret = self._media_secret("webhook:" + prin.device_did)
            return self._json({"ok": True, "url": url,
                               "topics": body.get("topics") or [f"user/{prin.principal}"],
                               "signing_secret": secret.hex(),
                               "note": "verify deliveries with HMAC-SHA256 over the raw body "
                                       "against X-Brainarbeit-Signature"})

        seg = p[len(pre):].strip("/").split("/")
        if len(seg) == 2 and seg[0] == "approvals":
            nonce = seg[1]
            decision = (body.get("decision") or "").lower()
            if decision not in ("approve", "reject", "revise", "deny"):
                return self._err("decision must be approve|reject|revise|deny", 400)
            req = {"verb": decision, "nonce": nonce}
            if body.get("feedback") is not None:
                req["feedback"] = body["feedback"]
            return self._proxy(req, prin)

        if len(seg) == 3 and seg[0] == "jobs":
            jid = _to_int(seg[1])
            if jid is None:
                return self._err("bad job id", 400)
            if seg[2] == "steer":
                return self._proxy({"verb": "steer", "id": jid, "input": body.get("input")}, prin)
            if seg[2] == "cancel":
                return self._proxy({"verb": "cancel", "id": jid}, prin)

        if len(seg) == 2 and seg[0] == "egress" and seg[1] in ("approve", "deny"):
            return self._proxy({"verb": f"egress-{seg[1]}", "nonce": body.get("nonce")}, prin)

        return self._err("not found", 404)

    def _proxy(self, req, prin):
        verb = req.get("verb")
        if verb not in READ_VERBS | CONTROL_VERBS:
            return self._err(f"verb not exposed: {verb}", 403)
        return self._json(self.pnd.call(req, device_did=prin.device_did))

    def _ws_stream(self, qs):
        prin = self._ws_auth(qs)
        if prin is None:
            return
        topics = _topics(qs) or [f"user/{prin.principal}"]
        after = _int(qs, "after_id", None)
        if not self._ws_handshake():
            return
        conn = self.connection
        sub = {"verb": "subscribe", "topics": topics}
        if after is not None:
            sub["after_id"] = after
        psock, frames = self.pnd.stream(sub, device_did=prin.device_did)
        self._pump_text(conn, psock, frames)

    def _ws_screen(self, qs):
        prin = self._ws_auth(qs)
        if prin is None:
            return
        if not self._ws_handshake():
            return
        conn = self.connection
        try:
            gen = media_adapter.proxy_mjpeg_frames(self.cfg.SCREEN_WS)
        except Exception as e:
            ws_send(conn, json.dumps({"type": "error", "error": f"screen unavailable: {e}"}))
            ws_close(conn)
            self.close_connection = True
            return
        try:
            for jpeg in gen:
                ws_send(conn, jpeg, opcode=0x2)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            ws_close(conn)
            self.close_connection = True

    def _ws_auth(self, qs):
        ticket = (qs.get("ticket") or [None])[0]
        if ticket:

            did = None
            try:

                payload = ticket.split(".", 1)[0]
                import base64 as _b
                obj = json.loads(_b.urlsafe_b64decode(
                    payload + "=" * ((4 - len(payload) % 4) % 4)).decode())
                did = obj.get("did")
            except Exception:
                did = None

            bind = self._bind_for_did(did) if did else None
            if did and media_adapter.verify_ticket(self._media_secret(did), ticket, bind=bind) == did:
                from .auth.principal import Principal
                R = self.authn.R
                cx = R.connect(self.authn.relay_db)
                try:
                    al = R.get_alliance(cx, did)
                    if al and al.get("revoked_at") is None:
                        return Principal(device_did=did, principal=al["principal"],
                                         caps=R.caps_ceiling(cx, did), label=al.get("label"))
                finally:
                    cx.close()
            self._err("invalid or expired media ticket", 401)
            return None

        return self._auth()

    def _ws_handshake(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key or self.headers.get("Upgrade", "").lower() != "websocket":
            self.send_error(400, "expected websocket upgrade")
            return False
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", ws_accept_key(key))
        self.end_headers()
        return True

    def _pump_text(self, conn, psock, frames):
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                op, _ = ws_read_frame(conn)
                if op is None or op == 0x8:
                    break
            stop.set()
            try:
                psock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        threading.Thread(target=reader, daemon=True).start()
        try:
            for frame in frames:
                if stop.is_set():
                    break
                ws_send(conn, json.dumps(frame, separators=(",", ":")))
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            stop.set()
            try:
                psock.close()
            except OSError:
                pass
            ws_close(conn)
        self.close_connection = True

    def _media_secret(self, label: str) -> bytes:

        base = (self.cfg.SECRET or self.cfg.DEV_SECRET).encode()
        return hashlib.blake2s(label.encode(), key=base[:32].ljust(32, b"0"),
                               digest_size=32).digest()

    def _bind_for_did(self, did: str) -> str | None:

        R = self.authn.R
        cx = R.connect(self.authn.relay_db)
        try:
            al = R.get_alliance(cx, did)
            return al.get("token_hash") if al else None
        finally:
            try:
                cx.close()
            except Exception:
                pass

    def _serve_spec(self, name):
        path = os.path.normpath(os.path.join(_HERE, "..", "spec", name))
        try:
            body = open(path, "rb").read()
        except OSError:
            return self._err("spec not found", 404)
        self.send_response(200)
        self.send_header("Content-Type", "application/yaml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

def _int(qs, k, default):
    try:
        return int(qs[k][0])
    except (KeyError, ValueError, TypeError):
        return default

def _to_int(s):
    try:
        return int(s)
    except (ValueError, TypeError):
        return None

def _topics(qs):
    t = qs.get("topics", [])
    if len(t) == 1 and "," in t[0]:
        t = t[0].split(",")
    return t

def make_server(cfg: Config):
    Handler.cfg = cfg
    Handler.pnd = PndClient(cfg.PND_SOCK, broker_method=cfg.ID_METHOD)
    Handler.authn = Authenticator(cfg.RELAY_DB, require_2fa=cfg.REQUIRE_2FA)
    srv = ThreadingHTTPServer((cfg.HOST, cfg.PORT), Handler)
    if cfg.CERT and cfg.KEY:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cfg.CERT, cfg.KEY)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    return srv

def main():
    ap = argparse.ArgumentParser(description="Brainarbeit public API gateway")
    ap.add_argument("--host", default=Config.HOST)
    ap.add_argument("--port", type=int, default=Config.PORT)
    ap.add_argument("--sock", default=Config.PND_SOCK)
    ap.add_argument("--relay-db", default=Config.RELAY_DB)
    args = ap.parse_args()
    Config.HOST, Config.PORT = args.host, args.port
    Config.PND_SOCK, Config.RELAY_DB = args.sock, args.relay_db
    assert_safe_to_start(Config)
    srv = make_server(Config)
    scheme = "https" if Config.CERT else "http"
    print(f"brainarbeit-gateway {Config.VERSION} on {scheme}://{Config.HOST}:{Config.PORT}"
          f"  -> pnd {Config.PND_SOCK}  (2FA={'on' if Config.REQUIRE_2FA else 'OFF'})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
