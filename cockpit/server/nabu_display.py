#!/usr/bin/env python3

import json, os, queue, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ENV_PATH = os.path.expanduser("~/.config/nabu-display.env")
LOG_PATH = os.path.expanduser("~/.local/share/nabu-display.log")
PORT = 8098
MAX_LEN = 1200

_ANNOUNCE_Q = queue.Queue(maxsize=200)

def _announce_worker():

    while True:
        text, source = _ANNOUNCE_Q.get()
        try:
            ok, info = announce(text, source)
            log('spoke -> %s: %s | src=%r | %r' % ('OK' if ok else 'FEHLER', info, source, text[:80]))
        except Exception:
            log('announce-worker Fehler')

def _env():
    d = {}
    try:
        for ln in open(ENV_PATH):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                d[k.strip()] = v.strip()
    except OSError:
        pass
    return d

def log(m):
    line = "%s %s\n" % (time.strftime("%H:%M:%S"), m)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line)
    except OSError:
        pass

def announce(text, source=None):
    cfg = _env()
    url = cfg.get("HA_URL", "").rstrip("/")
    tok = cfg.get("HA_TOKEN", "")
    ent = cfg.get("NABU_ENTITY", "")
    if not (url and tok and ent):
        return False, "nabu-display.env unvollständig (HA_URL/HA_TOKEN/NABU_ENTITY)"
    if source and str(source).strip().lower() not in ("", "daily", "standard", "default", "daily-session"):
        text = "Aus %s: %s" % (str(source).strip(), text)

    def _call(with_chime):
        payload = {"entity_id": ent, "message": text[:MAX_LEN]}
        if with_chime:
            payload["preannounce"] = True
        req = urllib.request.Request(
            url + "/api/services/assist_satellite/announce", data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            r.read()
    try:
        try:
            _call(True)
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            _call(False)
        return True, "angesagt (%d Zeichen)" % min(len(text), MAX_LEN)
    except Exception as e:
        return False, "HA-Fehler: %r" % (e,)

def _text_of(ref):
    if not isinstance(ref, dict):
        return str(ref or "")
    c = ref.get("content") if isinstance(ref.get("content"), dict) else ref
    for k in ("value", "text", "message", "title", "url"):
        v = c.get(k) or ref.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""

def _source_of(ref):
    if not isinstance(ref, dict):
        return ""
    c = ref.get("content") if isinstance(ref.get("content"), dict) else ref
    for k in ("source", "session", "context", "from"):
        v = (c.get(k) if isinstance(c, dict) else None) or ref.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()[:60]
    return ""

class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/health":
            cfg = _env()
            return self._json(200, {"ok": bool(cfg.get("HA_TOKEN")),
                                    "entity": cfg.get("NABU_ENTITY", ""),
                                    "ha": cfg.get("HA_URL", "")})
        return self._json(404, {"ok": False})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            ref = json.loads(self.rfile.read(n).decode("utf-8", "replace") or "{}")
        except ValueError:
            ref = {}
        if self.path == "/show":
            text = _text_of(ref)
            if not text:
                return self._json(400, {"ok": False, "error": "kein Text im Inhalt"})
            source = _source_of(ref)

            try:
                _ANNOUNCE_Q.put_nowait((text, source))
                info = "eingereiht (%d Zeichen; %d in Queue)" % (min(len(text), MAX_LEN), _ANNOUNCE_Q.qsize())
            except queue.Full:
                info = "Queue voll - verworfen"; log("show -> DROP (Queue voll) | %r" % (text[:80],))
            return self._json(200, {"ok": True, "info": info})
        if self.path in ("/idle", "/push"):
            return self._json(200, {"ok": True})
        return self._json(404, {"ok": False})

def main():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    threading.Thread(target=_announce_worker, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    log("nabu_display lauscht auf 127.0.0.1:%d" % PORT)
    srv.serve_forever()

if __name__ == "__main__":
    main()
