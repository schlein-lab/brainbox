
from __future__ import annotations
import json, os, socket, struct, threading

class MockLanServer:
    def __init__(self, pnd, sock_path: str, uid_principal: dict | None = None,
                 default_principal="owner"):
        self.pnd = pnd
        self.sock_path = sock_path
        self.uid_principal = uid_principal or {}
        self.default_principal = default_principal
        self._srv = None
        self._stop = threading.Event()

    def _principal_for(self, conn) -> str:
        try:
            creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", creds)
        except OSError:
            uid = None
        return self.uid_principal.get(uid, self.default_principal)

    def start(self):
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(self.sock_path)
        os.chmod(self.sock_path, 0o600)
        s.listen(64)
        self._srv = s
        threading.Thread(target=self._serve, daemon=True).start()
        return self

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        principal = self._principal_for(conn)
        try:
            buf = b""
            conn.settimeout(30)
            while b"\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buf += chunk
            line, _ = buf.split(b"\n", 1)
            req = json.loads(line.decode())

            for k in ("principal", "uid", "_peer_uid"):
                req.pop(k, None)
            if req.get("verb") == "subscribe":
                return self._stream(conn, req, principal)
            resp = self.pnd.handle(req, principal=principal)
            conn.sendall((json.dumps(resp) + "\n").encode())
        except Exception as e:
            try:
                conn.sendall((json.dumps({"ok": False, "error": str(e)}) + "\n").encode())
            except OSError:
                pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _stream(self, conn, req, principal):
        stop = threading.Event()

        def send(frame):
            conn.sendall((json.dumps(frame, separators=(",", ":")) + "\n").encode())

        def reader():
            try:
                while not stop.is_set():
                    if not conn.recv(4096):
                        break
            except OSError:
                pass
            stop.set()
        threading.Thread(target=reader, daemon=True).start()
        try:
            self.pnd.stream_subscribe(req.get("topics", []), principal,
                                      req.get("after_id"), send, stop)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            stop.set()

    def stop(self):
        self._stop.set()
        try:
            self._srv.close()
        except (OSError, AttributeError):
            pass
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass
