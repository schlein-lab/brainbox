#!/usr/bin/env python3

import os, sys, json, socket, tempfile, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from pnlib import ipc

PASS, FAIL = 0, 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")

def _handler(req):
    return {"ok": True, "echo": req.get("msg"), "verb": req.get("verb")}

def _serve(path):
    srv = ipc.Server(_handler, path=path)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    for _ in range(50):
        if os.path.exists(path):
            break
        time.sleep(0.05)
    return srv

def _raw_send(path, payload: bytes, read=True, timeout=5):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(path)
    try:
        s.sendall(payload)
        if not read:
            return None
        buf = b""
        while b"\n" not in buf:
            ch = s.recv(65536)
            if not ch:
                break
            buf += ch
        return buf
    finally:
        s.close()

def test_constants():
    print("[a] IPC ceilings are configured")
    check(ipc.MAX_FRAME == (1 << 20), f"MAX_FRAME == 1 MiB ({ipc.MAX_FRAME})")
    check(8 <= ipc.MAX_INFLIGHT <= 256, f"MAX_INFLIGHT is a sane bound ({ipc.MAX_INFLIGHT})")

def test_oversize_frame_dropped(path, srv):
    print("[b] an oversize newline-free frame is dropped; the server survives + keeps serving")

    blob = b"A" * (ipc.MAX_FRAME + (1 << 16))
    dropped = False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(path)
    try:
        try:

            for _ in range(0, len(blob), 1 << 16):
                s.sendall(blob[:1 << 16])
            s.shutdown(socket.SHUT_WR)
            resp = s.recv(65536)
            dropped = (resp == b"")
        except (BrokenPipeError, ConnectionResetError, OSError):
            dropped = True
    finally:
        s.close()
    check(dropped, "oversize frame -> connection dropped (no response, no OOM)")

    raw = _raw_send(path, (json.dumps({"verb": "ping", "msg": "after-flood"}) + "\n").encode())
    ok = False
    try:
        ok = json.loads(raw.split(b"\n", 1)[0].decode()).get("echo") == "after-flood"
    except Exception:
        ok = False
    check(ok, "the server keeps serving a normal request after the flood (accept loop intact)")

def test_client_response_ceiling(path):
    print("[c] the client refuses an oversize/never-terminated response")

    srv_sock = path + ".big"
    if os.path.exists(srv_sock):
        os.unlink(srv_sock)
    ss = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    ss.bind(srv_sock)
    ss.listen(1)
    stop = {"v": False}

    def flood_server():
        try:
            conn, _ = ss.accept()
            conn.recv(65536)
            chunk = b"B" * (1 << 16)
            sent = 0
            while sent <= ipc.MAX_FRAME + (1 << 16) and not stop["v"]:
                try:
                    conn.sendall(chunk)
                except OSError:
                    break
                sent += len(chunk)
            try:
                conn.close()
            except OSError:
                pass
        except OSError:
            pass

    th = threading.Thread(target=flood_server, daemon=True)
    th.start()

    orig = ipc.sock_path
    ipc.sock_path = lambda: srv_sock
    try:
        resp = ipc.send_request({"verb": "ping"}, timeout=5)
    finally:
        ipc.sock_path = orig
        stop["v"] = True
        try:
            ss.close()
        except OSError:
            pass
        try:
            os.unlink(srv_sock)
        except OSError:
            pass
    check(not resp.get("ok") and "MAX_FRAME" in (resp.get("error") or ""),
          f"client rejects an oversize response ({resp})")

def test_normal_roundtrip(path):
    print("[d] a normal one-line RPC round-trips")
    raw = _raw_send(path, (json.dumps({"verb": "ping", "msg": "hello"}) + "\n").encode())
    ok = False
    try:
        ok = json.loads(raw.split(b"\n", 1)[0].decode()).get("echo") == "hello"
    except Exception:
        ok = False
    check(ok, "well-formed request -> well-formed response")

def test_slot_release(path, srv):
    print("[e] the admission semaphore is released after each request (no slot leak)")

    n = ipc.MAX_INFLIGHT + 8
    oks = 0
    for i in range(n):
        raw = _raw_send(path, (json.dumps({"verb": "ping", "msg": i}) + "\n").encode())
        try:
            if json.loads(raw.split(b"\n", 1)[0].decode()).get("echo") == i:
                oks += 1
        except Exception:
            pass
    check(oks == n, f"all {n} sequential requests served ({oks}/{n}) -> slots are freed")

def main():
    print("=== IPC hardening — frame ceilings + bounded accept loop ===")
    test_constants()
    d = tempfile.mkdtemp(prefix="pn_ipc_")
    path = os.path.join(d, "test.sock")
    srv = _serve(path)
    try:
        test_normal_roundtrip(path)
        test_oversize_frame_dropped(path, srv)
        test_client_response_ceiling(path)
        test_slot_release(path, srv)
    finally:
        try:
            srv._sock.close()
        except Exception:
            pass
        for p in (path, path + ".big"):
            try:
                os.unlink(p)
            except OSError:
                pass
        try:
            os.rmdir(d)
        except OSError:
            pass
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
