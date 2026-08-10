
from __future__ import annotations
import socket, json, time, hmac, hashlib, base64

def _bind_tag(bind: str | None) -> str:

    return hashlib.sha256(("bb-mediabind:" + (bind or "")).encode()).hexdigest()[:16]

def mint_ticket(secret: bytes, device_did: str, *, ttl_s=120, bind: str | None = None) -> str:

    exp = int(time.time()) + ttl_s
    payload = json.dumps({"did": device_did, "exp": exp, "b": _bind_tag(bind)},
                         separators=(",", ":")).encode()
    mac = hmac.new(secret, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + \
        base64.urlsafe_b64encode(mac).decode().rstrip("=")

def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * ((4 - len(s) % 4) % 4))

def verify_ticket(secret: bytes, ticket: str, *, bind: str | None = None) -> str | None:

    try:
        p_s, m_s = ticket.split(".", 1)
        payload, mac = _b64d(p_s), _b64d(m_s)
        if not hmac.compare_digest(mac, hmac.new(secret, payload, hashlib.sha256).digest()):
            return None
        obj = json.loads(payload.decode())
        if obj.get("exp", 0) < time.time():
            return None

        if not hmac.compare_digest(str(obj.get("b", "")), _bind_tag(bind)):
            return None
        return obj.get("did")
    except Exception:
        return None

def proxy_mjpeg_frames(screen_ws_url: str):

    import urllib.parse
    u = urllib.parse.urlparse(screen_ws_url)
    host, port = u.hostname, (u.port or 80)
    s = socket.create_connection((host, port), timeout=5)
    key = base64.b64encode(b"brainarbeit-gw-0").decode()
    s.sendall((f"GET {u.path or '/'} HTTP/1.1\r\nHost: {host}:{port}\r\n"
               f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())

    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            s.close()
            raise ConnectionError("upstream /ws/screen closed during handshake")
        buf += chunk
    rest = buf.split(b"\r\n\r\n", 1)[1]

    def _read_exact(n, pre=b""):
        b = pre
        while len(b) < n:
            c = s.recv(n - len(b))
            if not c:
                return None
            b += c
        return b

    pending = rest
    try:
        while True:
            hdr = _read_exact(2, pending); pending = b""
            if hdr is None:
                break
            b1 = hdr[1]
            ln = b1 & 0x7F
            if ln == 126:
                ext = _read_exact(2)
                ln = int.from_bytes(ext, "big")
            elif ln == 127:
                ext = _read_exact(8)
                ln = int.from_bytes(ext, "big")
            payload = _read_exact(ln) if ln else b""
            if payload is None:
                break
            if (hdr[0] & 0x0F) in (0x1, 0x2):
                yield payload
            elif (hdr[0] & 0x0F) == 0x8:
                break
    finally:
        try:
            s.close()
        except Exception:
            pass

class WebRtcSignaling:

    def __init__(self, cfg):
        self.cfg = cfg

    @property
    def available(self) -> bool:
        return bool(self.cfg.WEBRTC_SIGNAL)

    def info(self) -> dict:
        return {"available": self.available,
                "signal_url": self.cfg.WEBRTC_SIGNAL,
                "note": ("two-way WebRTC is built by a separate agent in phantom "
                         "(branch media-webrtc-bidirectional); the gateway exposes its signaling "
                         "here once GW_WEBRTC_SIGNAL points at the backend."),
                "kinds": ["offer", "answer", "ice-candidate"]}
