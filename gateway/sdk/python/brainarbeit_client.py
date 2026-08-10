
from __future__ import annotations
import os, json, base64, socket, struct, hashlib, urllib.parse, urllib.request, urllib.error, ssl

try:

    from gateway.server.auth import twofactor as _totp
except Exception:
    import hmac as _hmac, time as _time, base64 as _b64, struct as _struct

    class _Totp:
        @staticmethod
        def code_at(secret_b32, ts=None):
            s = secret_b32.strip().replace(" ", "").upper()
            s += "=" * ((8 - len(s) % 8) % 8)
            key = _b64.b32decode(s, casefold=True)
            ctr = int((ts if ts is not None else _time.time()) // 30)
            mac = _hmac.new(key, _struct.pack(">Q", ctr), hashlib.sha1).digest()
            off = mac[-1] & 0x0F
            code = (_struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000
            return str(code).zfill(6)
    _totp = _Totp()

class BrainarbeitError(Exception):
    pass

class Brainarbeit:
    def __init__(self, base_url, *, device_did, durable_token,
                 totp_secret=None, totp_code=None):
        self.base = base_url.rstrip("/")
        self.did = device_did
        self.token = durable_token
        self.totp_secret = totp_secret
        self.totp_code = totp_code

    def _twofa(self):
        if self.totp_code:
            return self.totp_code
        if self.totp_secret:
            return _totp.code_at(self.totp_secret)
        return None

    def _headers(self, extra=None):
        h = {"Content-Type": "application/json",
             "Authorization": f"Bearer {self.did}.{self.token}"}
        code = self._twofa()
        if code:
            h["X-Brainarbeit-2FA"] = code
        if extra:
            h.update(extra)
        return h

    def _call(self, method, path, body=None, timeout=30):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers())
        ctx = ssl.create_default_context() if url.startswith("https") else None
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode() or "{}")
            except Exception:
                raise BrainarbeitError(f"HTTP {e.code}")

    def submit(self, task_type, *, params=None, attachments=None, path_refs=None,
               reply_to=None, needs_confirmation=None, tag=None, **extra):

        body = {"task_type": task_type, "params": params or {}}
        if attachments:
            body["attachments"] = [
                {"filename": fn, "content_b64": base64.b64encode(b).decode()}
                for fn, b in attachments]
        for k, v in (("path_refs", path_refs), ("reply_to", reply_to),
                     ("needs_confirmation", needs_confirmation), ("tag", tag)):
            if v is not None:
                body[k] = v
        body.update(extra)
        return self._call("POST", "/jobs", body)

    def approve(self, nonce):  return self._resolve(nonce, "approve")
    def reject(self, nonce):   return self._resolve(nonce, "reject")
    def deny(self, nonce):     return self._resolve(nonce, "deny")
    def revise(self, nonce, feedback): return self._resolve(nonce, "revise", feedback)

    def _resolve(self, nonce, decision, feedback=None):
        return self._call("POST", f"/approvals/{nonce}",
                          {"decision": decision, "feedback": feedback})

    def steer(self, job_id, input):
        return self._call("POST", f"/jobs/{job_id}/steer", {"input": input})

    def cancel(self, job_id):
        return self._call("POST", f"/jobs/{job_id}/cancel")

    def status(self, job_id):  return self._call("GET", f"/jobs/{job_id}/cvm")
    def job(self, job_id):     return self._call("GET", f"/jobs/{job_id}")
    def result(self, job_id):  return self._call("GET", f"/jobs/{job_id}/result")
    def log(self, job_id):     return self._call("GET", f"/jobs/{job_id}/log")
    def history(self, job_id): return self._call("GET", f"/jobs/{job_id}/history")
    def mine(self, state=None, limit=50):
        q = f"?limit={limit}" + (f"&state={state}" if state else "")
        return self._call("GET", "/jobs/mine" + q)
    def outputs(self, limit=200): return self._call("GET", f"/outputs?limit={limit}")
    def pending_approvals(self):  return self._call("GET", "/approvals")
    def engine_status(self):      return self._call("GET", "/engine/status")

    def register_webhook(self, url, topics=None):
        return self._call("POST", "/webhooks", {"url": url, "topics": topics})

    def stream(self, topics=None, after_id=None):

        u = urllib.parse.urlparse(self.base)
        scheme = "wss" if u.scheme == "https" else "ws"
        host, port = u.hostname, (u.port or (443 if scheme == "wss" else 80))
        path = (u.path or "") + "/stream"
        qs = []
        if topics:
            qs.append("topics=" + ",".join(topics))
        if after_id is not None:
            qs.append(f"after_id={after_id}")
        if qs:
            path += "?" + "&".join(qs)
        sock = socket.create_connection((host, port), timeout=10)
        if scheme == "wss":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        hdr = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
               f"Authorization: Bearer {self.did}.{self.token}\r\n")
        code = self._twofa()
        if code:
            hdr += f"X-Brainarbeit-2FA: {code}\r\n"
        hdr += "\r\n"
        sock.sendall(hdr.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += sock.recv(4096)
        rest = buf.split(b"\r\n\r\n", 1)[1]
        yield from _ws_frames(sock, rest)

def _ws_frames(sock, pre=b""):

    pending = pre

    def _recv(n):
        nonlocal pending
        while len(pending) < n:
            c = sock.recv(65536)
            if not c:
                return None
            pending += c
        out, pending = pending[:n], pending[n:]
        return out
    while True:
        hdr = _recv(2)
        if not hdr:
            break
        op = hdr[0] & 0x0F
        ln = hdr[1] & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", _recv(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", _recv(8))[0]
        payload = _recv(ln) if ln else b""
        if op == 0x8:
            break
        if op in (0x1, 0x2) and payload:
            try:
                yield json.loads(payload.decode())
            except ValueError:
                pass
