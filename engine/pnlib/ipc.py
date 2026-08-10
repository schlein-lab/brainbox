
from __future__ import annotations
import os, socket, json, struct, threading, grp

RAW_LIVENESS = {b"ping": b"pong\n", b"canary": b"ok\n"}

MAX_FRAME = 1 << 20

MAX_INFLIGHT = 64

MAX_BROKER_INFLIGHT = int(os.environ.get("PND_MAX_BROKER_INFLIGHT", "16"))

MAX_BROKER_INFLIGHT_PER_UID = int(os.environ.get("PND_MAX_BROKER_INFLIGHT_PER_UID", "8"))

def sock_path() -> str:
    rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(rt, "pnd.sock")

DEFAULT_BROKER_GROUP = "pnbroker"

def broker_sock_path() -> str | None:

    p = os.environ.get("PND_BROKER_SOCK", "").strip()
    return p or None

def broker_group() -> str:
    return os.environ.get("PND_BROKER_GROUP", DEFAULT_BROKER_GROUP).strip() or DEFAULT_BROKER_GROUP

def send_request(req: dict, timeout=15, path: str | None = None) -> dict:

    p = path or sock_path()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(p)
    try:
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
            if len(buf) > MAX_FRAME:

                return {"ok": False, "error": "response exceeds MAX_FRAME"}
        line = buf.split(b"\n", 1)[0]
        return json.loads(line.decode()) if line else {"ok": False, "error": "empty"}
    finally:
        s.close()

class Server:

    def __init__(self, handler, path=None):
        self.handler = handler
        self.path = path or sock_path()
        self._sock = None

        self._extra_socks = []

        self._slots = threading.Semaphore(MAX_INFLIGHT)

        self._broker_slots = threading.Semaphore(MAX_BROKER_INFLIGHT)

        self._broker_lock = threading.Lock()
        self._broker_uid_counts: dict[int, int] = {}

    def _bind(self, path, mode=0o600, group=None):

        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        s.bind(path)
        if group:
            try:
                gid = grp.getgrnam(group).gr_gid
                os.chown(path, -1, gid)
            except (KeyError, PermissionError, OSError) as e:

                import sys as _sys
                _sys.stderr.write(f"pnd ipc: broker socket group '{group}' not applied ({e}); "
                                  f"leaving {path} uid-only (0600)\n")
                mode = 0o600
        os.chmod(path, mode)
        s.listen(128)
        return s

    def also_listen(self, path, mode=0o660, group=None):

        s = self._bind(path, mode=mode, group=group)
        self._extra_socks.append(s)
        threading.Thread(target=self._accept_loop, args=(s, self._broker_slots),
                         kwargs={"broker": True}, daemon=True).start()
        return s

    def _accept_loop(self, s, slots, broker=False):

        while True:
            try:
                conn, _ = s.accept()
            except OSError:
                break

            if not slots.acquire(blocking=False):
                try:
                    conn.close()
                except Exception:
                    pass
                continue

            try:
                threading.Thread(target=self._handle, args=(conn, slots),
                                 kwargs={"broker": broker}, daemon=True).start()
            except RuntimeError:
                slots.release()
                try:
                    conn.close()
                except Exception:
                    pass
                continue

    def serve_forever(self):
        self._sock = self._bind(self.path, mode=0o600, group=None)
        self._accept_loop(self._sock, self._slots, broker=False)

    def _handle(self, conn, slots, broker=False):

        counted_uid = None
        try:

            conn.settimeout(5)

            try:
                creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                        struct.calcsize("3i"))
                _pid, peer_uid, _gid = struct.unpack("3i", creds)
            except OSError:
                peer_uid = None

            if broker and peer_uid is None:
                return

            if broker:
                with self._broker_lock:
                    cur = self._broker_uid_counts.get(peer_uid, 0)
                    if cur >= MAX_BROKER_INFLIGHT_PER_UID:
                        return
                    self._broker_uid_counts[peer_uid] = cur + 1
                    counted_uid = peer_uid
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buf += chunk
                if len(buf) > MAX_FRAME:

                    return
            first = buf.split(b"\n", 1)[0]

            reply = RAW_LIVENESS.get(first.strip())
            if reply is not None:
                try:
                    conn.sendall(reply)
                except OSError:
                    pass
                return
            req = json.loads(first.decode())

            if isinstance(req, dict):
                req.pop("principal", None)
                req.pop("uid", None)
                req["_peer_uid"] = peer_uid
            try:
                resp = self.handler(req)
            except Exception as e:
                resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}

            if isinstance(resp, dict) and callable(resp.get("_stream")):
                conn.settimeout(None)
                try:
                    resp["_stream"](conn, peer_uid)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                return
            conn.sendall((json.dumps(resp) + "\n").encode())
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

            if counted_uid is not None:
                with self._broker_lock:
                    n = self._broker_uid_counts.get(counted_uid, 0) - 1
                    if n > 0:
                        self._broker_uid_counts[counted_uid] = n
                    else:
                        self._broker_uid_counts.pop(counted_uid, None)

            slots.release()
