
from __future__ import annotations
import socket, json

class PndClient:
    def __init__(self, sock_path: str, broker_method: str = "device-channel"):
        self.sock_path = sock_path
        self.broker_method = broker_method

    def _connect(self, timeout):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(self.sock_path)
        return s

    def _stamp(self, req: dict, device_did: str | None):

        req = dict(req)
        req.pop("principal", None)
        req.pop("uid", None)
        req.pop("_peer_uid", None)
        if device_did is not None:
            req["_method"] = self.broker_method
            req["_selector"] = device_did
        return req

    def call(self, req: dict, *, device_did: str | None = None, timeout=15) -> dict:

        req = self._stamp(req, device_did)
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

    def stream(self, req: dict, *, device_did: str | None = None):

        req = self._stamp(req, device_did)
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
