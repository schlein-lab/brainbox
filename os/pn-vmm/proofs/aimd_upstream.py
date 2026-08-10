#!/usr/bin/env python3

import json, os, socket, sys, threading, time

HOST = "127.0.0.1"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9761
CTL_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 9762
CAP = int(os.environ.get("CAP", "8"))
SERVICE_S = float(os.environ.get("SERVICE_S", "0.35"))

_active = 0
_lk = threading.Lock()
_stats = {"ok": 0, "r429": 0, "max_active": 0}

def _read_request(c):
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = c.recv(65536)
        if not d:
            return None
        buf += d
    head, _, rest = buf.partition(b"\r\n\r\n")
    need = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            need = int(line.split(b":", 1)[1].strip())
    while len(rest) < need:
        d = c.recv(65536)
        if not d:
            break
        rest += d
    return head

def _resp(c, status, body):
    c.sendall(("HTTP/1.1 %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\n"
               "Connection: close\r\n\r\n" % (status, len(body))).encode() + body)

def handle(c):
    global _active
    try:
        if _read_request(c) is None:
            return
        with _lk:
            _active += 1
            me = _active
            if me > _stats["max_active"]:
                _stats["max_active"] = me
        try:
            if me > CAP:
                with _lk:
                    _stats["r429"] += 1
                _resp(c, "429 Too Many Requests",
                      json.dumps({"type": "error", "error": {"type": "rate_limit_error",
                                                             "message": "stub over capacity"}}).encode())
                return
            time.sleep(SERVICE_S)
            with _lk:
                _stats["ok"] += 1
            _resp(c, "200 OK",
                  json.dumps({"id": "msg_stub", "type": "message",
                              "usage": {"input_tokens": 10, "output_tokens": 20}}).encode())
        finally:
            with _lk:
                _active -= 1
    except OSError:
        pass
    finally:
        try:
            c.close()
        except OSError:
            pass

def ctl():
    global CAP
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((HOST, CTL_PORT)); s.listen(4)
    while True:
        c, _ = s.accept()
        try:
            line = c.recv(256).decode().strip()
            if line.startswith("CAP "):
                CAP = int(line.split()[1])
                c.sendall(b"OK\n")
            elif line == "STATS":
                with _lk:
                    c.sendall((json.dumps({**_stats, "cap": CAP}) + "\n").encode())
        except Exception:
            pass
        finally:
            c.close()

threading.Thread(target=ctl, daemon=True).start()
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind((HOST, PORT)); srv.listen(64)
print("aimd_upstream on %s:%d ctl:%d CAP=%d service=%.2fs" % (HOST, PORT, CTL_PORT, CAP, SERVICE_S), flush=True)
while True:
    conn, _ = srv.accept()
    threading.Thread(target=handle, args=(conn,), daemon=True).start()
