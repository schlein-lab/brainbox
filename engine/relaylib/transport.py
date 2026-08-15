
from __future__ import annotations
import socket, threading

from relaylib import protocol as P

class MockRelay:
    def __init__(self, host="127.0.0.1", port=0):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((host, port))
        self._srv.listen(16)
        self.host, self.port = self._srv.getsockname()
        self._lock = threading.Lock()
        self._waiting = {}
        self._running = True
        self.seen_frames = []
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    @property
    def url(self):
        return f"mock://{self.host}:{self.port}"

    def stop(self):
        self._running = False
        try:
            self._srv.close()
        except OSError:
            pass

    def _serve(self):
        while self._running:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                break
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        try:
            f = P.read_frame(conn)
        except (OSError, ValueError):
            conn.close(); return
        if f is None:
            conn.close(); return
        self.seen_frames.append({k: f.get(k) for k in ("t", "rz", "blob")})
        t, topic = f.get("t"), f.get("rz")
        if t not in (P.RZ_REGISTER, P.RZ_DIAL) or not topic:
            conn.close(); return
        role = "box" if t == P.RZ_REGISTER else "device"
        peer = None
        with self._lock:
            waiter = self._waiting.get(topic)
            if waiter is None:
                self._waiting[topic] = (conn, role)
            elif waiter[1] == role:

                conn.close(); return
            else:
                self._waiting.pop(topic, None)
                peer = waiter[0]
        if peer is not None:
            self._pump(conn, peer)

    def _pump(self, a, b):
        def one_way(src, dst):
            try:
                while True:
                    f = P.read_frame(src)
                    if f is None:
                        break
                    self.seen_frames.append({k: f.get(k) for k in ("t", "rz", "blob")})
                    if f.get("t") == P.RZ_DATA:
                        dst.sendall(P.frame(f))
                    elif f.get("t") == P.RZ_BYE:
                        break
            except (OSError, ValueError):
                pass
            finally:
                for s in (src, dst):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
        t = threading.Thread(target=one_way, args=(b, a), daemon=True)
        t.start()
        one_way(a, b)
        t.join(timeout=1)

def _is_wss(url: str) -> bool:
    return url.startswith("wss://") or url.startswith("ws://")

class Channel:
    def __init__(self, sock, rz):
        self._sock = sock
        self.rz = rz

    @classmethod
    def connect(cls, url: str, timeout=5):
        if _is_wss(url):
            raise NotImplementedError(
                "wss:// is handled by relaylib.wss.WSSChannel via Channel.register/dial dispatch")
        if not url.startswith("mock://"):
            raise NotImplementedError(
                f"unknown transport for {url!r}; use mock:// (tests) or wss:// (production)")
        hostport = url[len("mock://"):]
        host, port = hostport.split(":")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, int(port)))
        return s

    @classmethod
    def register(cls, url, rz, timeout=5, **kw):

        if _is_wss(url):
            from relaylib.wss import WSSChannel
            return WSSChannel.register(url, rz, timeout=timeout, **kw)
        s = cls.connect(url, timeout)
        s.sendall(P.frame({"t": P.RZ_REGISTER, "rz": rz}))
        return cls(s, rz)

    @classmethod
    def dial(cls, url, rz, timeout=5, **kw):

        if _is_wss(url):
            from relaylib.wss import WSSChannel
            return WSSChannel.dial(url, rz, timeout=timeout, **kw)
        s = cls.connect(url, timeout)
        s.sendall(P.frame({"t": P.RZ_DIAL, "rz": rz}))
        return cls(s, rz)

    def send_blob(self, blob_hex: str):
        self._sock.sendall(P.frame({"t": P.RZ_DATA, "rz": self.rz, "blob": blob_hex}))

    def recv_blob(self, timeout=None):
        if timeout is not None:
            self._sock.settimeout(timeout)
        f = P.read_frame(self._sock)
        if f is None or f.get("t") != P.RZ_DATA:
            return None
        return f.get("blob")

    def close(self):
        try:
            self._sock.sendall(P.frame({"t": P.RZ_BYE, "rz": self.rz}))
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
