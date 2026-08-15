

import base64
import hashlib
import os
import random
import socket
import ssl
import struct
import time
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BIN = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

TERMINAL_CLOSE_CODES = (4001, 4003, 4004)

TRANSIENT_CLOSE_CODES = (4002,)

class Handshake:

    def __init__(self):
        self.status = None
        self.reason = ""
        self.headers = {}
        self.body = ""
        self.error = None
        self.sock = None

    @property
    def ok(self):
        return self.status == 101

    def describe(self):
        if self.error:
            return "transport error: %s" % self.error
        if self.ok:
            return "101 upgraded"
        body = self.body.strip().replace("\n", " ")[:70]
        return "HTTP %s %s%s" % (self.status, self.reason, (" -- " + body) if body else "")

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

def _connect(url, timeout):
    parts = urlsplit(url)
    secure = parts.scheme in ("wss", "https")
    port = parts.port or (443 if secure else 80)
    host = parts.hostname
    raw = socket.create_connection((host, port), timeout=timeout)
    if secure:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        raw = ctx.wrap_socket(raw, server_hostname=host)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    netloc = parts.netloc
    return raw, host, netloc, path

def _read_headers(sock, limit=65536):

    buf = b""
    while b"\r\n\r\n" not in buf and len(buf) < limit:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    return head, rest

def handshake(url, cookie=None, timeout=8.0, origin=None, extra_headers=None):

    hs = Handshake()
    try:
        sock, host, netloc, path = _connect(url, timeout)
    except Exception as e:
        hs.error = "%s: %s" % (type(e).__name__, e)
        return hs
    sock.settimeout(timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    lines = [
        "GET %s HTTP/1.1" % path,
        "Host: %s" % netloc,
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Key: %s" % key,
        "Sec-WebSocket-Version: 13",
        "User-Agent: brainbox-acceptance/1",
    ]
    if origin:
        lines.append("Origin: %s" % origin)
    if cookie:
        lines.append("Cookie: %s" % cookie)
    for k, v in (extra_headers or {}).items():
        lines.append("%s: %s" % (k, v))
    req = ("\r\n".join(lines) + "\r\n\r\n").encode()
    try:
        sock.sendall(req)
        head, rest = _read_headers(sock)
    except Exception as e:
        hs.error = "%s: %s" % (type(e).__name__, e)
        try:
            sock.close()
        except Exception:
            pass
        return hs
    if not head:
        hs.error = "server closed before responding to upgrade"
        try:
            sock.close()
        except Exception:
            pass
        return hs
    text = head.decode("latin-1")
    first, _, rest_head = text.partition("\r\n")
    bits = first.split(" ", 2)
    try:
        hs.status = int(bits[1])
    except (IndexError, ValueError):
        hs.status = -1
    hs.reason = bits[2] if len(bits) > 2 else ""
    for line in rest_head.split("\r\n"):
        if ":" in line:
            k, _, v = line.partition(":")
            hs.headers[k.strip().lower()] = v.strip()
    if hs.status == 101:
        expect = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        got = hs.headers.get("sec-websocket-accept", "")
        if got and got != expect:
            hs.error = "bad Sec-WebSocket-Accept (broken upgrade)"
            hs.status = -1
            try:
                sock.close()
            except Exception:
                pass
            return hs
        hs.sock = sock
        hs._leftover = rest
        return hs

    body = rest
    try:
        sock.settimeout(1.0)
        for _ in range(4):
            more = sock.recv(2048)
            if not more:
                break
            body += more
            if len(body) > 4096:
                break
    except Exception:
        pass
    hs.body = body.decode("latin-1", "replace")
    try:
        sock.close()
    except Exception:
        pass
    return hs

def _mask_frame(opcode, payload=b""):
    mask = os.urandom(4)
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", n)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return head + mask + masked

class Frame:
    def __init__(self, opcode, payload):
        self.opcode = opcode
        self.payload = payload

def _recv_exact(sock, n, buf):

    while len(buf) < n:
        chunk = sock.recv(max(4096, n - len(buf)))
        if not chunk:
            return None, buf
        buf += chunk
    return buf[:n], buf[n:]

def read_frame(sock, buf):

    head, buf = _recv_exact(sock, 2, buf)
    if head is None:
        return None, buf
    b0, b1 = head[0], head[1]
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    ln = b1 & 0x7F
    if ln == 126:
        ext, buf = _recv_exact(sock, 2, buf)
        if ext is None:
            return None, buf
        ln = struct.unpack(">H", ext)[0]
    elif ln == 127:
        ext, buf = _recv_exact(sock, 8, buf)
        if ext is None:
            return None, buf
        ln = struct.unpack(">Q", ext)[0]
    mask = b""
    if masked:
        mask, buf = _recv_exact(sock, 4, buf)
        if mask is None:
            return None, buf
    payload = b""
    if ln:
        payload, buf = _recv_exact(sock, ln, buf)
        if payload is None:
            return None, buf
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return Frame(opcode, payload), buf

def hold(hs, seconds, on_frame=None, meta=None):

    sock = hs.sock
    buf = getattr(hs, "_leftover", b"")
    deadline = time.time() + seconds
    frames = 0
    sock.settimeout(1.0)
    while time.time() < deadline:
        try:
            fr, buf = read_frame(sock, buf)
        except socket.timeout:
            continue
        except Exception as e:
            if meta is not None:
                meta["close_code"] = None
            return False, frames, "read error: %s" % e
        if fr is None:
            if meta is not None:
                meta["close_code"] = None
            return False, frames, "peer closed (EOF)"
        frames += 1
        if fr.opcode == OP_CLOSE:
            code = ""
            ccode = None
            if len(fr.payload) >= 2:
                ccode = struct.unpack(">H", fr.payload[:2])[0]
                code = str(ccode)
                extra = fr.payload[2:].decode("utf-8", "replace")[:40]
                if extra:
                    code += " " + extra
            if meta is not None:
                meta["close_code"] = ccode
            return False, frames, "server sent CLOSE %s" % code
        if fr.opcode == OP_PING:
            try:
                sock.sendall(_mask_frame(OP_PONG, fr.payload))
            except Exception:
                return False, frames, "pong failed"
        if on_frame:
            on_frame(fr)
    return True, frames, "held %ds" % seconds

def send_text(hs, text):
    hs.sock.sendall(_mask_frame(OP_TEXT, text.encode()))

def count_opens(url, seconds, cookie=None, origin=None, max_opens=64):

    end = time.time() + seconds
    opens = 0
    stayed = False
    reason = ""
    pre = None
    attempt = 0
    stopped_terminal = False
    last_code = None
    while time.time() < end and opens < max_opens:
        hs = handshake(url, cookie=cookie, origin=origin, timeout=6.0)
        if pre is None:
            pre = hs.status if hs.status is not None else -1
        if not hs.ok:
            reason = hs.describe()
            if hs.error:

                attempt += 1
                delay = min(8.0, 0.25 * (2 ** (attempt - 1))) * (0.85 + random.random() * 0.30)
                if time.time() + delay >= end:
                    break
                time.sleep(delay)
                continue

            stopped_terminal = True
            break
        opens += 1
        remaining = max(0.0, end - time.time())
        meta = {}
        stayed, _frames, reason = hold(hs, remaining, meta=meta)
        hs.close()
        if stayed:
            break
        last_code = meta.get("close_code")
        if last_code in TERMINAL_CLOSE_CODES:

            stopped_terminal = True
            break

        attempt += 1
        delay = min(8.0, 0.25 * (2 ** (attempt - 1)))
        delay *= (0.85 + random.random() * 0.30)
        if time.time() + delay >= end:
            break
        time.sleep(delay)
    return {
        "opens": opens,
        "stayed": stayed,
        "last_reason": reason,
        "prehandshake_status": pre,
        "stopped_terminal": stopped_terminal,
        "last_close_code": last_code,
    }
