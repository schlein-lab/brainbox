#!/usr/bin/env python3

import socket, json, sys, time, threading, math

SOCK = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 6
POOL = int(sys.argv[3]) if len(sys.argv) > 3 else 2
SLEEP = float(sys.argv[4]) if len(sys.argv) > 4 else 2.0

res = {}
def ask(i):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(120); s.connect(SOCK)
    s.sendall((json.dumps({"verb": "ask", "prompt": f"q{i}"}) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        c = s.recv(65536)
        if not c: break
        buf += c
    res[i] = json.loads(buf.split(b"\n", 1)[0]); s.close()

t0 = time.time()
ths = [threading.Thread(target=ask, args=(i,)) for i in range(N)]
[t.start() for t in ths]; [t.join() for t in ths]
dt = time.time() - t0
ok = sum(1 for r in res.values() if r.get("ok"))
waves = math.ceil(N / POOL)
expect = waves * SLEEP
serialized = dt >= expect * 0.8
print(f"N={N} pool={POOL} backend_sleep={SLEEP}s -> wall={dt:.1f}s (expect ~{expect:.0f}s for {waves} waves)")
print(f"ok={ok}/{N}  serialized(backpressure)={serialized}")
sys.exit(0 if (ok == N and serialized) else 1)
