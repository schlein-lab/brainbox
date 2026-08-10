#!/usr/bin/env python3

import json
import http.client
import os
import secrets
import socket
import ssl
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

UPSTREAM = os.environ.get("SHIM_UPSTREAM", "https://127.0.0.1:8077").rstrip("/")
KEY = os.environ.get("SHIM_KEY", "")
CA = os.environ.get("SHIM_CA", "")
PORT = int(os.environ.get("SHIM_PORT", "8078"))
PRIMARY_DEADLINE = float(os.environ.get("VOICE_PRIMARY_DEADLINE", "12"))
BUSY_TEXT = os.environ.get(
    "VOICE_BUSY_TEXT",
    "Die Brainbox ist gerade voll ausgelastet. Gerätebefehle funktionieren weiter — "
    "für inhaltliche Antworten frag mich bitte in ein paar Minuten noch einmal.")
STREAM_TIMEOUT = 300.0

_ctx = ssl.create_default_context(cafile=CA) if CA and os.path.exists(CA) else ssl.create_default_context()

HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "host", "authorization"}

_last_fallback: dict = {"at": None, "reason": None}

def _log(msg: str) -> None:
    print(f"{time.strftime('%FT%T')} {msg}", flush=True)

class PortalUnavailable(Exception):
    pass

SITZUNGSBAHN = os.environ.get("VOICE_SESSION_LANE", "1") not in ("0", "false", "no")

def _portal_connect(timeout: float):
    u = urlsplit(UPSTREAM)
    if u.scheme == "https":
        return http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout, context=_ctx)
    return http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)

def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()

class Gateway(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send_json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _begin_chunked(self, status: int, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _chunk(self, data: bytes) -> None:
        if data:
            self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()

    def _end_chunked(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _portal_request(self, body: bytes | None, timeout: float):

        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
        headers["Authorization"] = "Bearer " + KEY
        u = urlsplit(UPSTREAM)
        headers["Host"] = u.netloc
        conn = _portal_connect(timeout)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except (socket.timeout, TimeoutError, OSError, http.client.HTTPException) as e:
            try:
                conn.close()
            except Exception:
                pass
            raise PortalUnavailable(f"{type(e).__name__}: {e}")
        if resp.status in (429, 500, 502, 503, 504):
            detail = resp.read(300)
            conn.close()
            raise PortalUnavailable(f"HTTP {resp.status}: {detail[:200]!r}")
        return conn, resp

    def _proxy_stream_out(self, conn, resp, first: bytes) -> None:

        self.send_response(resp.status)
        for k, v in resp.getheaders():
            lk = k.lower()
            if lk in ("connection", "keep-alive", "transfer-encoding", "content-length"):
                continue
            self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            self._chunk(first)
            conn.sock.settimeout(STREAM_TIMEOUT)
            while True:
                chunk = resp.read1(8192)
                if not chunk:
                    break
                self._chunk(chunk)
            self._end_chunked()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            conn.close()

    @staticmethod
    def _absage_text(reason: str) -> str:

        r = (reason or "").lower()
        if "timeout" in r or "timed out" in r or "deadline" in r:

            return ("Die Sitzung denkt noch. Deine Frage ist angekommen — ich sage dir die Antwort "
                    "an, sobald sie da ist.")
        if "connection refused" in r or "connectionrefused" in r or "econnrefused" in r:
            return "Das Portal startet gerade neu. Frag mich bitte gleich noch einmal."
        if "http 401" in r or "http 403" in r:
            return "Mir fehlt gerade das Recht, in deine Sitzung zu schreiben."
        if "http 5" in r:
            return "Das Portal hat einen Fehler gemeldet. Ich habe nichts von der Sitzung bekommen."
        if "leeren speak" in r or "kein json" in r:
            return "Die Sitzung hat nichts zurueckgegeben. Frag mich bitte noch einmal."
        return BUSY_TEXT

    def _fallback_messages(self, req: dict, reason: str) -> None:
        _last_fallback.update(at=time.strftime("%FT%T"), reason=reason[:200])
        _log(f"ABSAGE ({reason[:120]})")
        model_name = "gateway/busy"
        BUSY = self._absage_text(reason)
        if not req.get("stream"):
            self._send_json(200, {
                "id": "msg_gateway_busy", "type": "message", "role": "assistant",
                "model": model_name,
                "content": [{"type": "text", "text": BUSY}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": len(BUSY) // 4}})
            return
        self._begin_chunked(200, "text/event-stream; charset=utf-8")
        self._chunk(_sse("message_start", {
            "type": "message_start",
            "message": {"id": "msg_gateway_busy", "type": "message", "role": "assistant",
                        "model": model_name, "content": [], "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0}}}))
        self._chunk(_sse("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}}))
        self._chunk(_sse("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": BUSY}}))
        self._chunk(_sse("content_block_stop", {"type": "content_block_stop", "index": 0}))
        self._chunk(_sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": len(BUSY) // 4}}))
        self._chunk(_sse("message_stop", {"type": "message_stop"}))
        self._end_chunked()

    @staticmethod
    def _letzter_nutzertext(req: dict) -> str:

        for m in reversed(req.get("messages") or []):
            if m.get("role") != "user":
                continue
            c = m.get("content")
            if isinstance(c, str):
                return c.strip()
            if isinstance(c, list):
                teile = [b.get("text", "") for b in c
                         if isinstance(b, dict) and b.get("type") == "text"]
                if any(teile):
                    return " ".join(t for t in teile if t).strip()
        return ""

    def _sitzung_fragen(self, text: str, timeout: float) -> str:

        nutz = json.dumps({"text": text, "channel": "nabu"}, ensure_ascii=False).encode()
        u = urlsplit(UPSTREAM)
        conn = _portal_connect(timeout)
        try:
            conn.request("POST", "/api/voice", body=nutz, headers={
                "Authorization": "Bearer " + KEY, "Content-Type": "application/json",
                "Content-Length": str(len(nutz)), "Host": u.netloc})
            resp = conn.getresponse()
            roh = resp.read()
        except (socket.timeout, TimeoutError, OSError, http.client.HTTPException) as e:
            try:
                conn.close()
            except Exception:
                pass
            raise PortalUnavailable(f"voice-lane {type(e).__name__}: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass
        if resp.status != 200:
            raise PortalUnavailable(f"voice-lane HTTP {resp.status}: {roh[:160]!r}")
        try:
            d = json.loads(roh or b"{}")
        except json.JSONDecodeError:
            raise PortalUnavailable(f"voice-lane kein JSON: {roh[:160]!r}")
        gesprochen = (d.get("speak") or "").strip()
        if not gesprochen:

            raise PortalUnavailable("voice-lane lieferte leeren speak-Text")
        return gesprochen

    def _antwort_ausliefern(self, req: dict, text: str, model_name: str) -> None:

        if not req.get("stream"):
            self._send_json(200, {
                "id": "msg_voice_" + secrets.token_hex(8), "type": "message", "role": "assistant",
                "model": model_name, "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": max(1, len(text) // 4)}})
            return
        mid = "msg_voice_" + secrets.token_hex(8)
        self._begin_chunked(200, "text/event-stream; charset=utf-8")
        self._chunk(_sse("message_start", {
            "type": "message_start",
            "message": {"id": mid, "type": "message", "role": "assistant",
                        "model": model_name, "content": [], "stop_reason": None,
                        "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}}))
        self._chunk(_sse("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}}))
        self._chunk(_sse("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": text}}))
        self._chunk(_sse("content_block_stop", {"type": "content_block_stop", "index": 0}))
        self._chunk(_sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": max(1, len(text) // 4)}}))
        self._chunk(_sse("message_stop", {"type": "message_stop"}))
        self._end_chunked()

    def _handle_messages(self, body: bytes) -> None:
        try:
            req = json.loads(body or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"type": "error", "error": {"type": "invalid_request_error",
                                                             "message": "invalid json"}})
            return

        if SITZUNGSBAHN:
            text = self._letzter_nutzertext(req)
            if text:
                try:
                    gesprochen = self._sitzung_fragen(text, PRIMARY_DEADLINE)
                    self._antwort_ausliefern(req, gesprochen, "brainbox/voice-session")
                    return
                except PortalUnavailable as e:

                    self._fallback_messages(req, str(e))
                    return
        try:
            conn, resp = self._portal_request(body, PRIMARY_DEADLINE)
            try:
                first = resp.read1(65536)
            except (socket.timeout, TimeoutError, OSError) as e:
                conn.close()
                raise PortalUnavailable(f"first-byte {type(e).__name__}")

            head = first[:2048]
            if (not head) or b'"type":"error"' in head.replace(b" ", b"") or head.lstrip().startswith(b'{"error"'):
                conn.close()
                raise PortalUnavailable(f"early error body: {head[:120]!r}")
            self._proxy_stream_out(conn, resp, first)
        except PortalUnavailable as e:
            self._fallback_messages(req, str(e))

    def _handle_models(self) -> None:
        try:
            conn, resp = self._portal_request(None, min(PRIMARY_DEADLINE, 6))
            data = resp.read()
            conn.close()
            self.send_response(resp.status)
            ct = resp.getheader("Content-Type", "application/json")
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except PortalUnavailable:

            self._send_json(200, {"data": [
                {"type": "model", "id": "haiku", "display_name": "Brainbox (haiku)",
                 "created_at": "2026-01-01T00:00:00Z"}],
                "has_more": False, "first_id": "haiku", "last_id": "haiku"})

    def _handle_health(self) -> None:

        self._send_json(200, {
            "ok": True, "primary_deadline_s": PRIMARY_DEADLINE,
            "last_fallback": _last_fallback})

    def _handle_status(self) -> None:
        portal_ok = False
        try:
            conn, resp = self._portal_request_path("/v1/models", 4)
            portal_ok = resp.status == 200
            conn.close()
        except Exception:
            pass
        self._send_json(200, {
            "portal": portal_ok, "primary_deadline_s": PRIMARY_DEADLINE,
            "last_fallback": _last_fallback})

    def _portal_request_path(self, path: str, timeout: float):

        conn = _portal_connect(timeout)
        try:
            conn.request("GET", path, headers={"Authorization": "Bearer " + KEY})
            return conn, conn.getresponse()
        except Exception as e:
            try:
                conn.close()
            except Exception:
                pass
            raise PortalUnavailable(str(e))

    def _forward_generic(self, body: bytes | None) -> None:
        try:
            conn, resp = self._portal_request(body, 30)
        except PortalUnavailable as e:
            self._send_json(502, {"error": f"portal unavailable: {e}"})
            return
        data = resp.read()
        conn.close()
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() in ("connection", "keep-alive", "transfer-encoding", "content-length"):
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        if self.path == "/gw/health":
            self._handle_health()
        elif self.path == "/gw/status":
            self._handle_status()
        elif self.path.startswith("/v1/messages") and self.command == "POST":
            self._handle_messages(body or b"")
        elif self.path.startswith("/v1/models") and self.command == "GET":
            self._handle_models()
        else:
            self._forward_generic(body)

    def do_GET(self):
        self._dispatch()

    do_POST = do_PUT = do_DELETE = do_GET

if __name__ == "__main__":
    if not KEY:
        print("SHIM_KEY fehlt", file=sys.stderr)
        sys.exit(1)
    _log(f"voice-gateway :{PORT} -> {UPSTREAM} | deadline={PRIMARY_DEADLINE}s "
         f"(busy-Degradation, kein Schatten-LLM)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Gateway).serve_forever()
