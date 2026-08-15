#!/usr/bin/env python3

import os, json, time, socket, threading, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pn_cast_display as R
import cast_live as L

PORT = int(os.environ.get("CASTD_PORT", "8096"))
DEVICES_JSON = os.path.expanduser("~/.local/share/brainbox-portal/devices.json")

try:
    _STATIC = json.loads(os.environ.get("CASTD_STATIC_MAP", "{}"))
    if not isinstance(_STATIC, dict):
        _STATIC = {}
except Exception:
    _STATIC = {}

def _resolve(castid):
    try:
        d = json.load(open(DEVICES_JSON))
        if isinstance(d, dict) and isinstance(d.get("devices"), list):
            items = d["devices"]
        elif isinstance(d, dict):
            items = list(d.values())
        else:
            items = d
        for r in items:
            if isinstance(r, dict) and castid in (r.get("id"), r.get("name")):
                tr = r.get("transport") or {}
                a = tr.get("addr") or r.get("ip") or r.get("host")
                if a:
                    return a
    except Exception:
        pass
    return _STATIC.get(castid)

def _self_ip(dev_ip):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((dev_ip, 8009)); ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except Exception:
        return None
    finally:
        s.close()

NATIVE = {"video": ("video/mp4", "BUFFERED"), "audio": ("audio/mpeg", "BUFFERED"),
          "hls": ("application/vnd.apple.mpegurl", "LIVE"),
          "live": ("application/vnd.apple.mpegurl", "LIVE")}

class Target:

    def __init__(self, castid):
        self.castid = castid; self.ip = None
        self.frame = R.render_logo(); self.gen = 0; self.title = "Brainarbeit"; self.state = "idle"
        self.media = None; self.live = False; self._wd = False
        self.last_ok = None; self.last_shown = False; self.last_err = None
        self._lock = threading.Lock()
        self._seq = 0; self._cond = threading.Condition()

    def resolve_ip(self):
        self.ip = _resolve(self.castid) or self.ip
        return self.ip

    def show_ref(self, ref):
        title = ref.get("title") or (ref.get("text") or "")[:40] or "Anzeige"
        kind = (ref.get("kind") or "").lower()
        with self._lock:
            if kind in NATIVE and ref.get("value"):
                ct, st = NATIVE[kind]
                self.media = {"url": ref["value"], "ct": ct, "st": st, "title": title}
            else:
                self.frame = R.render_ref(ref); self.gen += 1; self.media = None
            self.title = title; self.state = "showing"; self.live = (kind in ("live", "hls"))
        threading.Thread(target=self._push_once, daemon=True).start()
        if kind in ("live", "hls"):
            self._start_watchdog()

    def idle(self):
        with self._lock:
            self.live = False; self.state = "idle"; self.media = None
        threading.Thread(target=self._stop_receiver, daemon=True).start()

    def _start_watchdog(self):
        with self._lock:
            if self._wd:
                return
            self._wd = True
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self):
        try:
            while True:
                time.sleep(25)
                with self._lock:
                    if not self.live:
                        break
                if not self._dmr_ok():
                    self._push_once()
        finally:
            with self._lock:
                self._wd = False

    def _frame_url(self):
        sip = _self_ip(self.ip)
        if not sip:
            return None
        with self._lock:
            g = self.gen
        return "http://%s:%d/%s/frame.jpg?gen=%d" % (sip, PORT, self.castid, g)

    def _push_once(self):
        ok = False
        try:
            if not self.ip:
                self.ip = _resolve(self.castid)
            if not self.ip:
                self.last_err = "unresolved castid"
            else:
                with self._lock:
                    media = dict(self.media) if self.media else None
                if media:
                    rc, msg = L.load(self.ip, media["url"], media["ct"], media["st"], media["title"])
                    ok = (rc == 0); self.last_err = None if ok else msg
                else:
                    url = self._frame_url()
                    if not url:
                        self.last_err = "self-ip not ready"
                    else:
                        for st in ("BUFFERED", "NONE"):
                            rc, msg = L.load(self.ip, url, "image/jpeg", st, self.title)
                            if rc == 0:
                                ok = True; self.last_err = None; break
                            self.last_err = msg
            self.last_ok = ok; self.last_shown = ok
        finally:
            with self._cond:
                self._seq += 1; self._cond.notify_all()
        return ok

    def _stop_receiver(self):

        if not self.ip:
            self.ip = _resolve(self.castid)
        if not self.ip:
            return
        try:
            s = L._open(self.ip)
        except Exception:
            return
        try:
            L._send(s, R.NS_CONN, {"type": "CONNECT"}, "receiver-0")
            L._send(s, R.NS_RECV, {"type": "GET_STATUS", "requestId": 1}, "receiver-0")
            sess = None; t = time.time()
            while time.time() - t < 4:
                ns, pl = L._recv(s)
                if ns is None:
                    break
                if L._beat(s, ns, pl):
                    continue
                if ns == R.NS_RECV:
                    d = json.loads(pl or "{}")
                    for a in d.get("status", {}).get("applications", []) or []:
                        if a.get("sessionId"):
                            sess = a["sessionId"]
                    if sess:
                        break
            if sess:
                L._send(s, R.NS_RECV, {"type": "STOP", "sessionId": sess, "requestId": 2}, "receiver-0")
                time.sleep(0.6)
        finally:
            try: s.close()
            except Exception: pass

    def _dmr_ok(self):

        try:
            s = L._open(self.ip)
        except Exception:
            return False
        try:
            L._send(s, R.NS_CONN, {"type": "CONNECT"}, "receiver-0")
            L._send(s, R.NS_RECV, {"type": "GET_STATUS", "requestId": 1}, "receiver-0")
            t = time.time()
            while time.time() - t < 4:
                ns, pl = L._recv(s)
                if ns is None:
                    break
                if L._beat(s, ns, pl):
                    continue
                if ns == R.NS_RECV:
                    d = json.loads(pl or "{}")
                    if d.get("type") == "RECEIVER_STATUS":
                        for a in d.get("status", {}).get("applications", []) or []:
                            if a.get("appId") == R.DEFAULT_MEDIA_RECEIVER:
                                return True
                        return False
            return False
        finally:
            try: s.close()
            except Exception: pass

    def reachable(self, timeout=1.5):
        ip = self.ip or _resolve(self.castid)
        if not ip:
            return False
        try:
            socket.create_connection((ip, 8009), timeout=timeout).close(); return True
        except Exception:
            return False

    def seq(self):
        with self._cond:
            return self._seq

    def wait_push(self, prev, timeout):
        deadline = time.time() + timeout
        with self._cond:
            while self._seq <= prev:
                rem = deadline - time.time()
                if rem <= 0:
                    return False
                self._cond.wait(rem)
            return True

_TARGETS = {}; _TLOCK = threading.Lock()
def target(castid):
    with _TLOCK:
        t = _TARGETS.get(castid)
        if not t:
            t = Target(castid); _TARGETS[castid] = t
        return t

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def _send(self, code, ctype, body):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass

    def _json_body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except Exception:
            return {}

    def _parse(self):
        p = [x for x in urlparse(self.path).path.strip("/").split("/") if x != ""]
        if len(p) >= 2:
            return p[0], "/" + "/".join(p[1:])
        if len(p) == 1:
            return None, "/" + p[0]
        return None, "/"

    def do_GET(self):
        cid, route = self._parse()
        if cid is None and route == "/health":
            return self._send(200, "application/json", json.dumps({"ok": True, "targets": list(_TARGETS.keys())}))
        if cid and route == "/frame.jpg":
            t = target(cid)
            with t._lock:
                data = t.frame
            return self._send(200, "image/jpeg", data)
        if cid and route == "/health":

            t = target(cid); t.resolve_ip()
            return self._send(200, "application/json", json.dumps(
                {"ok": True, "castid": cid, "ip": t.ip, "state": t.state, "tv_reachable": t.reachable(),
                 "last_ok": t.last_ok, "last_shown": t.last_shown, "last_err": t.last_err}))
        return self._send(404, "application/json", json.dumps({"error": "no route"}))

    def do_POST(self):
        cid, route = self._parse()
        if not cid:
            return self._send(404, "application/json", json.dumps({"error": "no castid in path"}))
        t = target(cid)
        if not t.resolve_ip():
            return self._send(200, "application/json", json.dumps(
                {"ok": False, "shown_on_tv": False, "error": "castid %s unresolved" % cid,
                 "note": "Gerät nicht in der Geräteliste gefunden"}))
        if route == "/show":
            ref = self._json_body()
            try:
                prev = t.seq(); t.show_ref(ref)
                fresh = t.wait_push(prev, 4.5)
                if fresh:
                    shown = bool(t.last_shown)
                    note = None if shown else ("Cast: %s" % t.last_err if t.last_err
                                               else "Gerät nicht erreichbar (vermutlich aus)")
                else:
                    shown = t.reachable(); note = None if shown else "Gerät nicht erreichbar (vermutlich aus)"
                return self._send(200, "application/json", json.dumps(
                    {"ok": True, "state": "showing", "shown_on_tv": shown, "tv": t.ip, "note": note}))
            except Exception as e:
                return self._send(200, "application/json", json.dumps({"ok": False, "error": str(e)}))
        if route == "/idle":
            t.idle()
            return self._send(200, "application/json", json.dumps({"ok": True, "state": "idle"}))
        return self._send(404, "application/json", json.dumps({"error": "no route"}))

def main():

    BIND = os.environ.get("PN_CASTD_BIND", "127.0.0.1")
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    print("pn-castd multi-target on http://%s:%d (non-intrusive: casts only on /show)" % (BIND, PORT))
    srv.serve_forever()

if __name__ == "__main__":
    main()
