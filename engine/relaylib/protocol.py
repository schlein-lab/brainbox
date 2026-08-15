
from __future__ import annotations
import json, struct, time

RZ_REGISTER = "register"
RZ_DIAL = "dial"
RZ_DATA = "data"
RZ_BYE = "bye"

PAIR_REQUEST = "pair_request"
PAIR_OK = "pair_ok"
HELLO = "hello"
HELLO_OK = "hello_ok"
SUBMIT = "submit"
RESULT = "result"
ERROR = "error"
SESSION = "session"

MAX_FRAME = 1 << 20

def frame(obj: dict) -> bytes:
    body = json.dumps(obj, separators=(",", ":")).encode()
    if len(body) > MAX_FRAME:
        raise ValueError("frame too large")
    return struct.pack(">I", len(body)) + body

def read_frame(sock) -> dict | None:

    hdr = _recvn(sock, 4)
    if hdr is None:
        return None
    (n,) = struct.unpack(">I", hdr)
    if n > MAX_FRAME:
        raise ValueError("frame length exceeds cap")
    body = _recvn(sock, n)
    if body is None:
        return None
    return json.loads(body.decode())

def _recvn(sock, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf

def submit_payload(*, task_type=None, params=None, cmd=None, cls="worker",
                   nonce=None, ts=None, counter=None, session_token=None) -> dict:

    p = {"t": SUBMIT}
    if task_type is not None:
        p["task_type"] = task_type
        p["params"] = params or {}
    if cmd is not None:
        p["cmd"] = cmd
    p["class"] = cls
    p["nonce"] = nonce
    p["ts"] = ts if ts is not None else time.time()
    if counter is not None:
        p["counter"] = counter
    if session_token is not None:
        p["session_token"] = session_token
    return p

def signing_bytes(payload: dict) -> bytes:

    canon = {k: payload[k] for k in
             ("t", "task_type", "params", "cmd", "class", "nonce", "ts", "counter",
              "session_token") if k in payload}
    return json.dumps(canon, separators=(",", ":"), sort_keys=True).encode()
