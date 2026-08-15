#!/usr/bin/env python3

import os, sys, json, socket, time, subprocess, re

BASE = os.environ.get("ANTHROPIC_BASE_URL", "http://127.0.0.1:8088")
REAL = os.environ.get("PN_CLAUDE_REAL", "")
POLL_S = float(os.environ.get("PN_SHIM_POLL_S", "0.3") or 0.3)
_ONCE = os.environ.get("PN_SHIM_ONCE", "") == "1"

_m = re.match(r"http://([^:/]+):(\d+)", BASE)
HOST, PORT = (_m.group(1), int(_m.group(2))) if _m else ("127.0.0.1", 8088)

def _ctl(op, turn_id, timeout=4.0):

    body = json.dumps({"op": op, "turn_id": turn_id}).encode()
    req = (b"POST /pn/ctl HTTP/1.1\r\nHost: pn\r\nContent-Type: application/json\r\n"
           b"Content-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
    try:
        s = socket.create_connection((HOST, PORT), timeout=timeout)
        s.sendall(req)
        buf = b""
        clen = None
        while True:
            if b"\r\n\r\n" in buf:
                head, _, rest = buf.partition(b"\r\n\r\n")
                if clen is None:
                    clen = 0
                    for l in head.split(b"\r\n"):
                        if l.lower().startswith(b"content-length:"):
                            clen = int(l.split(b":", 1)[1].strip())
                if len(rest) >= clen:
                    s.close()
                    return json.loads(rest[:clen].decode("utf-8", "replace") or "{}")
            d = s.recv(65536)
            if not d:
                break
            buf += d
        s.close()
        head, _, rest = buf.partition(b"\r\n\r\n")
        return json.loads(rest.decode("utf-8", "replace") or "{}")
    except Exception:
        return None

def _find_real():
    if REAL and os.path.exists(REAL):
        return REAL
    for c in ("/opt/claude/claude", "/bin/claude.real", "/usr/bin/claude"):
        if os.path.exists(c):
            return c
    return "/bin/claude"

def _banner(msg):

    sys.stdout.write(("\n" if _ONCE else "\r") + msg + ("\n" if _ONCE else "\x1b[K"))
    sys.stdout.flush()

def main():
    argv = sys.argv[1:]
    real = _find_real()
    turn_id = "shim:%s:%d:%d" % (socket.gethostname(), os.getpid(), time.time_ns())

    r = _ctl("acquire", turn_id)
    if r is None or not r.get("ok"):

        _banner("⚠ Governance-Steuerkanal nicht erreichbar — Anfrage läuft ohne sichtbare "
                "Warteschlange (die Box governt sie weiterhin am Broker).")
        sys.stdout.write("\n")
        return os.execvp(real, [real] + argv)

    granted = bool(r.get("granted"))
    t0 = time.time()
    last = None
    if not granted:
        while True:
            pos = int(r.get("position", -1)); waiting = int(r.get("waiting", 0))
            slots = int(r.get("slots", 0)); in_use = int(r.get("in_use", 0))
            line = ("⏳ in Warteschlange — Position %d (deine Anfrage wartet, bis die Box dranlässt; "
                    "%d belegt / %d Plätze, %d wartend)…" % (pos, in_use, slots, waiting))
            if line != last:
                _banner(line); last = line
            time.sleep(POLL_S)
            r = _ctl("poll", turn_id)
            if r is None or not r.get("ok"):
                _banner("⚠ Governance-Steuerkanal verloren — Anfrage läuft weiter (Broker governt).")
                sys.stdout.write("\n")
                return os.execvp(real, [real] + argv)
            if r.get("granted"):
                break

    waited = time.time() - t0
    _banner("▶ dran — läuft…%s" % (" (nach %.1fs Warteschlange)" % waited if waited > 0.3 else ""))
    sys.stdout.write("\n")
    sys.stdout.flush()

    rc = 127
    try:
        rc = subprocess.call([real] + argv)
    finally:
        _ctl("done", turn_id)
    return rc

if __name__ == "__main__":
    sys.exit(main() or 0)
