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

class RecordingHandler:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, req):
        with self.lock:
            self.calls.append(req)
        verb = req.get("verb") if isinstance(req, dict) else None
        if verb == "admin_secret":

            return {"ok": True, "secret": "SHOULD-NEVER-LEAK-VIA-RAW"}
        return {"ok": True, "echo": req.get("msg"), "verb": verb, "peer_uid": req.get("_peer_uid")}

    def call_count(self):
        with self.lock:
            return len(self.calls)

def _serve(path, handler, broker_path=None):
    srv = ipc.Server(handler, path=path)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    for _ in range(50):
        if os.path.exists(path):
            break
        time.sleep(0.05)
    if broker_path:

        srv.also_listen(broker_path, mode=0o600, group=None)
        for _ in range(50):
            if os.path.exists(broker_path):
                break
            time.sleep(0.05)
    return srv

def _probe(path, msg: bytes, want: bytes, timeout=2):

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    ok = False
    reply = b""
    try:
        s.connect(path)
        s.sendall(msg)
        reply = s.recv(31)
        ok = reply.startswith(want)
    finally:
        s.close()
    return ok, reply

def _json_rpc(path, req: dict, timeout=2):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            ch = s.recv(65536)
            if not ch:
                break
            buf += ch
        return json.loads(buf.split(b"\n", 1)[0].decode()) if buf else None
    finally:
        s.close()

def test_ping_pong(path):
    print("[1] ping -> pong (L1 liveness, exactly pn-init's strncmp)")
    ok, reply = _probe(path, b"ping\n", b"pong")
    check(ok, f"ping -> reply starts with 'pong' (got {reply!r})")

def test_canary_ok(path):
    print("[2] canary -> ok (L2 canary)")
    ok, reply = _probe(path, b"canary\n", b"ok")
    check(ok, f"canary -> reply starts with 'ok' (got {reply!r})")

def test_raw_never_reaches_handler(path, handler):
    print("[3] the raw probe NEVER invokes the JSON handler (no state change / no authz dispatch)")
    before = handler.call_count()
    _probe(path, b"ping\n", b"pong")
    _probe(path, b"canary\n", b"ok")
    after = handler.call_count()
    check(after == before, f"handler call count unchanged across 2 raw probes ({before} -> {after})")

def test_no_bypass(path, handler):
    print("[4] a raw (non-token) line cannot reach the handler via the raw path (no verb bypass)")

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2)
    leaked = False
    got_liveness = False
    try:
        s.connect(path)
        s.sendall(b"admin_secret\n")
        reply = s.recv(4096)
        got_liveness = reply.startswith(b"pong") or reply.startswith(b"ok\n")
        leaked = b"SHOULD-NEVER-LEAK-VIA-RAW" in reply
    except Exception:
        pass
    finally:
        s.close()
    check(not got_liveness, "a non-token raw line does NOT get a pong/ok liveness reply")
    check(not leaked, "a raw line can NOT reach the privileged verb / leak its secret")

    before = handler.call_count()
    _probe(path, b"ping\n", b"pong")
    check(handler.call_count() == before, "raw 'ping' did not dispatch as a JSON verb to the handler")

def test_mixed_json(path, handler):
    print("[5] JSON RPC round-trips on the SAME socket, interleaved with raw probes")
    r1 = _json_rpc(path, {"verb": "status", "msg": "before"})
    check(r1 and r1.get("echo") == "before", f"JSON works BEFORE a raw probe ({r1})")
    ok, _ = _probe(path, b"ping\n", b"pong")
    check(ok, "raw probe between JSON calls still gets pong")
    r2 = _json_rpc(path, {"verb": "status", "msg": "after"})
    check(r2 and r2.get("echo") == "after", f"JSON works AFTER a raw probe ({r2})")

    check(r2 is not None and "peer_uid" in r2, "JSON path still stamps the attested peer uid")

def test_no_wedge(path, handler):
    print("[6] a slow/partial probe client cannot wedge the handler (others keep being served)")

    stall = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stall.settimeout(10)
    stall.connect(path)
    stall.sendall(b"pi")
    results = {"ok": 0, "n": 0}
    lock = threading.Lock()

    def worker(i):
        try:
            if i % 2 == 0:
                ok, _ = _probe(path, b"ping\n", b"pong")
            else:
                r = _json_rpc(path, {"verb": "status", "msg": i})
                ok = bool(r and r.get("echo") == i)
        except Exception:
            ok = False
        with lock:
            results["n"] += 1
            if ok:
                results["ok"] += 1

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(40)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    elapsed = time.time() - t0
    try:
        stall.close()
    except OSError:
        pass
    check(results["ok"] == 40, f"all 40 concurrent clients served while a probe stalled "
                               f"({results['ok']}/40)")
    check(elapsed < 5, f"served concurrently, not serialized behind the stalled client "
                       f"({elapsed:.2f}s)")

def test_no_leak(path):
    print("[7] a probe emits EXACTLY the constant token reply and nothing else (no data disclosure)")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2)
    payload = b""
    try:
        s.connect(path)
        s.sendall(b"ping\n")

        while True:
            ch = s.recv(4096)
            if not ch:
                break
            payload += ch
    except socket.timeout:
        pass
    finally:
        s.close()
    check(payload == b"pong\n", f"ping reply is EXACTLY b'pong\\n', no trailing/echoed data ({payload!r})")

def test_broker_socket_liveness(broker_path, handler):
    print("[8] the broker (second) listener ALSO answers the raw liveness probe")
    ok_p, rp = _probe(broker_path, b"ping\n", b"pong")
    ok_c, rc = _probe(broker_path, b"canary\n", b"ok")
    check(ok_p, f"broker socket ping -> pong (got {rp!r})")
    check(ok_c, f"broker socket canary -> ok (got {rc!r})")

    before = handler.call_count()
    _probe(broker_path, b"ping\n", b"pong")
    check(handler.call_count() == before, "broker raw probe did not dispatch to the handler")

def main():
    print("=== pn-init RAW liveness contract — pnd answers ping->pong / canary->ok ===")
    d = tempfile.mkdtemp(prefix="pn_live_")
    path = os.path.join(d, "pnd.sock")
    broker_path = os.path.join(d, "pnd-broker.sock")
    handler = RecordingHandler()
    srv = _serve(path, handler, broker_path=broker_path)
    try:
        test_ping_pong(path)
        test_canary_ok(path)
        test_raw_never_reaches_handler(path, handler)
        test_no_bypass(path, handler)
        test_mixed_json(path, handler)
        test_no_wedge(path, handler)
        test_no_leak(path)
        test_broker_socket_liveness(broker_path, handler)
    finally:
        try:
            srv._sock.close()
        except Exception:
            pass
        for s in getattr(srv, "_extra_socks", []):
            try:
                s.close()
            except Exception:
                pass
        for p in (path, broker_path):
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
