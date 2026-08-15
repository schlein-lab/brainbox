#!/usr/bin/env python3

import json, socket, sys, threading, time

BROKER = ("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 9763)
CTL = ("127.0.0.1", int(sys.argv[2]) if len(sys.argv) > 2 else 9762)
LLMD_SOCK = sys.argv[3] if len(sys.argv) > 3 else "/tmp/pntest-aimd/llmd.sock"
BODY = json.dumps({"model": "claude-opus-4-8", "max_tokens": 16,
                   "messages": [{"role": "user", "content": "ping"}]}).encode()

stop = threading.Event()
lock = threading.Lock()
M = {"ok": 0, "s429": 0, "other": 0, "lat": []}

def llmd(verb):
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); c.settimeout(5)
        c.connect(LLMD_SOCK); c.sendall((json.dumps({"verb": verb}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            d = c.recv(65536)
            if not d:
                break
            buf += d
        c.close()
        return json.loads(buf.split(b"\n", 1)[0].decode())
    except Exception as e:
        return {"ok": False, "error": str(e)}

def set_cap(n):
    c = socket.create_connection(CTL, 5); c.sendall(("CAP %d\n" % n).encode()); c.recv(16); c.close()

def one_turn():
    t0 = time.time()
    try:
        c = socket.create_connection(BROKER, timeout=90)
        req = ("POST /v1/messages HTTP/1.1\r\nHost: api.anthropic.com\r\n"
               "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(BODY)).encode() + BODY
        c.sendall(req)
        buf = b""
        while True:
            d = c.recv(65536)
            if not d:
                break
            buf += d
        c.close()
        status = int(buf.split(b" ", 2)[1]) if buf.startswith(b"HTTP/") else 0
    except Exception:
        status = -1
    dt = time.time() - t0
    with lock:
        M["lat"].append(dt)
        if status == 200:
            M["ok"] += 1
        elif status == 429:
            M["s429"] += 1
        else:
            M["other"] += 1

def client_loop():
    while not stop.is_set():
        one_turn()

def phase(name, seconds, nclients, cap=None, sample=None):
    if cap is not None:
        set_cap(cap)
    with lock:
        M["ok"] = M["s429"] = M["other"] = 0; M["lat"] = []
    threads = [threading.Thread(target=client_loop, daemon=True) for _ in range(nclients)]
    stop.clear()
    for t in threads:
        t.start()
    slots_series, wait_series = [], []
    t0 = time.time()
    while time.time() - t0 < seconds:
        s = llmd("admit-snapshot")
        if s.get("ok"):
            slots_series.append(s.get("slots"))
            wait_series.append(s.get("waiting"))
            if sample:
                sample(s)
        time.sleep(1.0)
    stop.set()
    for t in threads:
        t.join(timeout=95)
    with lock:
        lat = sorted(M["lat"]) or [0]
        res = {"phase": name, "ok": M["ok"], "s429": M["s429"], "other": M["other"],
               "lat_p50": round(lat[len(lat) // 2], 2), "lat_max": round(lat[-1], 2),
               "slots_min": min(slots_series or [0]), "slots_max": max(slots_series or [0]),
               "slots_last": (slots_series or [0])[-1],
               "slots_late_mean": round(sum(slots_series[-8:]) / max(1, len(slots_series[-8:])), 1),
               "wait_max": max(wait_series or [0])}
    print(json.dumps(res), flush=True)
    return res

print("== settle: sanity turn through the whole chain ==", flush=True)
one_turn()
print(json.dumps({"sanity": dict(M)}), flush=True)
snap0 = llmd("admit-snapshot")
print("start snapshot:", json.dumps({k: snap0.get(k) for k in ("slots", "aimd")}), flush=True)

p1 = phase("P1 growth CAP=8, 10 clients", 45, 10, cap=8)
p2 = phase("P2 backpressure CAP=2", 25, 10, cap=2)
p3 = phase("P3 recovery CAP=10", 45, 10, cap=10)

checks = {
    "P1: slots grew from 2 under demand (max>=6)": p1["slots_max"] >= 6,
    "P1: late-mean slots >= 5 (no artificial ceiling)": p1["slots_late_mean"] >= 5,
    "P1: real throughput (>=120 turns in 45s)": p1["ok"] >= 120,
    "P2: multiplicative shrink bit (min<=3)": p2["slots_min"] <= 3,
    "P2: queue holds honestly under saturation (wait_max>0)": p2["wait_max"] > 0,
    "P3: recovery grew again (max>=6)": p3["slots_max"] >= 6,
    "AIMD probing overhead sane (<12% 429 across all)": (p1["s429"] + p2["s429"] + p3["s429"])
        < 0.12 * max(1, p1["ok"] + p2["ok"] + p3["ok"] + p1["s429"] + p2["s429"] + p3["s429"]),
}
print("\n===== VERDICT =====", flush=True)
for k, v in checks.items():
    print("  [%s] %s" % ("PASS" if v else "FAIL", k), flush=True)
print("  RESULT:", "AIMD_PROVEN" if all(checks.values()) else "NEEDS_LOOK", flush=True)
