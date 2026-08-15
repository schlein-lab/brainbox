#!/usr/bin/env python3

import os, re, sys, json, socket, time, subprocess, shlex

BASE = os.environ.get("ANTHROPIC_BASE_URL", "http://127.0.0.1:8088")
POLL_S = float(os.environ.get("PN_BANNER_POLL_S", "0.2") or 0.2)
TMUX = shlex.split(os.environ.get("PN_TMUX", "tmux"))
_m = re.match(r"http://([^:/]+):(\d+)", BASE)
HOST, PORT = (_m.group(1), int(_m.group(2))) if _m else ("127.0.0.1", 8088)

IDLE_STYLE = "bg=colour236,fg=colour245"
WAIT_STYLE = "bg=colour130,fg=colour231,bold"
RUN_STYLE = "bg=colour28,fg=colour231,bold"

def _cellwait(timeout=3.0):
    body = b'{"op":"cellwait"}'
    req = (b"POST /pn/ctl HTTP/1.1\r\nHost: pn\r\nContent-Type: application/json\r\n"
           b"Content-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
    try:
        s = socket.create_connection((HOST, PORT), timeout=timeout)
        s.sendall(req)
        buf = b""
        while b"\r\n\r\n" not in buf:
            d = s.recv(65536)
            if not d:
                break
            buf += d
        _h, _, rest = buf.partition(b"\r\n\r\n")
        clen = 0
        for l in _h.split(b"\r\n"):
            if l.lower().startswith(b"content-length:"):
                clen = int(l.split(b":", 1)[1].strip())
        while len(rest) < clen:
            d = s.recv(65536)
            if not d:
                break
            rest += d
        s.close()
        return json.loads(rest[:clen].decode("utf-8", "replace") or "{}")
    except Exception:
        return None

def _tmux(*args):
    try:
        subprocess.run([*TMUX, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       stdin=subprocess.DEVNULL, timeout=3)
        return True
    except Exception:
        return False

def _set(status, style):
    _tmux("set", "-g", "status", "on")
    _tmux("set", "-g", "status-right-length", "160")
    _tmux("set", "-g", "status-left", "")
    _tmux("set", "-g", "status-style", style)
    _tmux("set", "-g", "status-right", status)

def _singleton():

    pf = "/tmp/pn_repl_banner.pid"
    try:
        if os.path.exists(pf):
            old = int(open(pf).read().strip() or "0")
            if old and os.path.exists("/proc/%d" % old):
                sys.exit(0)
    except Exception:
        pass
    try:
        open(pf, "w").write(str(os.getpid()))
    except Exception:
        pass

def main():
    _singleton()

    last = None
    while True:
        w = _cellwait()
        if w is None:
            state, text, style = "down", " ⚠ Governance-Steuerkanal … ", IDLE_STYLE
        elif w.get("held") and int(w.get("position", 0)) > 0:
            n = int(w["position"]); c = int(w.get("count", 1))
            extra = (" (+%d)" % (c - 1)) if c > 1 else ""
            kind = w.get("kind") or "llm"
            if kind == "exec":

                nm = (w.get("name") or "Programm")
                state = "execwait:%s:%d%s" % (nm, n, extra)
                text = " ⏳ exec %s · Position %d%s — Start wartet, bis die Box dranlässt " % (nm, n, extra)
            else:
                state = "wait:%d%s" % (n, extra)
                text = " ⏳ Warteschlange · Position %d%s — deine Anfrage wartet, bis die Box dranlässt " % (n, extra)
            style = WAIT_STYLE
        elif w.get("granted_recent"):
            state, text, style = "run", " ▶ dran — läuft ", RUN_STYLE
        else:
            state, text, style = "idle", " brainarbeit · bereit ", IDLE_STYLE
        if state != last:
            _set(text, style)
            last = state
        time.sleep(POLL_S)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
