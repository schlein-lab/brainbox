
from __future__ import annotations
import struct, selectors

HELLO_VNC = 0x01
FB_UPDATE = 0x02
INPUT_PTR = 0x10
INPUT_KEY = 0x11
CTRL      = 0x20
BYE       = 0x2f

MAX_RECORD = (1 << 24) - 1

def encode(rtype: int, payload: bytes) -> bytes:
    if len(payload) > MAX_RECORD:
        raise ValueError("live record too large")
    return struct.pack(">B", rtype) + struct.pack(">I", len(payload))[1:] + payload

def decode(buf: bytes):

    out, i, n = [], 0, len(buf)
    while n - i >= 4:
        rtype = buf[i]
        length = (buf[i + 1] << 16) | (buf[i + 2] << 8) | buf[i + 3]
        if n - i - 4 < length:
            break
        out.append((rtype, buf[i + 4:i + 4 + length]))
        i += 4 + length
    return out, buf[i:]

def bridge(sock_a, sock_b, *, chunk=65536, idle_timeout=120.0):

    sel = selectors.DefaultSelector()
    sel.register(sock_a, selectors.EVENT_READ, sock_b)
    sel.register(sock_b, selectors.EVENT_READ, sock_a)
    try:
        while True:
            events = sel.select(timeout=idle_timeout)
            if not events:
                break
            for key, _ in events:
                src, dst = key.fileobj, key.data
                data = src.recv(chunk)
                if not data:
                    return
                dst.sendall(data)
    finally:
        sel.close()
