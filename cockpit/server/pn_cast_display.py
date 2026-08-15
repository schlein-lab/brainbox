#!/usr/bin/env python3

import os, io, sys, json, time, ssl, struct, socket, threading, argparse, subprocess, tempfile, shutil, re, shlex
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from PIL import Image, ImageDraw, ImageFont

try:
    import pn_governed as _PN
except Exception:
    try:
        sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
        import pn_governed as _PN
    except Exception:
        _PN = None

W, H = 1920, 1080
LOGO = os.environ.get("TV_IDLE_LOGO", os.path.expanduser("~/brainarbeit-site/brainbox-lockup.png"))
BG = (255, 255, 255)

def _font(sz):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()

def _canvas():
    return Image.new("RGB", (W, H), BG)

def _fit(img):
    c = _canvas(); im = img.convert("RGB")
    im.thumbnail((W, H), Image.LANCZOS)
    c.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
    return c

def _to_jpeg(img) -> bytes:
    b = io.BytesIO()
    img.save(b, format="JPEG", quality=88, progressive=False, optimize=False)
    return b.getvalue()

def render_logo() -> bytes:
    if os.path.exists(LOGO):
        try: return _to_jpeg(_fit(Image.open(LOGO)))
        except Exception: pass
    c = _canvas(); d = ImageDraw.Draw(c)
    d.text((W // 2, H // 2), "BRAINARBEIT", fill=(20, 24, 30), font=_font(96), anchor="mm")
    return _to_jpeg(c)

def render_text(text) -> bytes:
    c = _canvas(); d = ImageDraw.Draw(c)
    d.text((W // 2, H // 2), (text or "")[:400], fill=(20, 24, 30), font=_font(72), anchor="mm")
    return _to_jpeg(c)

def render_image_file(path) -> bytes:
    return _to_jpeg(_fit(Image.open(path)))

_FIREFOX_LOCK = threading.Lock()

def render_url(url, wait=3.0) -> bytes:

    if not _FIREFOX_LOCK.acquire(timeout=0.1):
        return render_text("(Anzeige gerade beschäftigt — bitte gleich erneut)")
    try:
        if _PN is not None and _PN.pn_available():
            sh = ('export HOME="$TMPDIR" XDG_CACHE_HOME="$TMPDIR/.cache"; '
                  'mkdir -p "$TMPDIR/ffprof"; '
                  'firefox --headless --no-remote --profile "$TMPDIR/ffprof" '
                  '--window-size %d,%d --screenshot "$TMPDIR/shot.png" %s >&2; '
                  'cat "$TMPDIR/shot.png"' % (W, H, shlex.quote(url)))
            rc, png, err = _PN.run_capture(["/bin/sh", "-c", sh], mem=700, timeout_s=50,
                                           tag="display.render", latency="realtime", wait_s=90)
            if err:
                return render_text("(Anzeige nicht möglich: %s)" % err[:160])
            if png:
                try:
                    return _to_jpeg(_fit(Image.open(io.BytesIO(png))))
                except Exception:
                    pass
            return render_text("(konnte %s nicht rendern)" % url)
        print("[castd] Governor (pnd) nicht erreichbar — URL-Render läuft ausnahmsweise direkt.",
              file=sys.stderr, flush=True)
        png_path = tempfile.mktemp(suffix=".png")
        prof = tempfile.mkdtemp(prefix="ffcast-")
        try:
            subprocess.run(["nice", "-n", "10", "firefox", "--headless", "--no-remote",
                            "--profile", prof, "--window-size", "%d,%d" % (W, H),
                            "--screenshot", png_path, url], timeout=45, capture_output=True)
            if os.path.exists(png_path) and os.path.getsize(png_path) > 0:
                return _to_jpeg(_fit(Image.open(png_path)))
        except Exception:
            pass
        finally:
            try: os.unlink(png_path)
            except OSError: pass
            try: shutil.rmtree(prof, ignore_errors=True)
            except Exception: pass
        return render_text("(konnte %s nicht rendern)" % url)
    finally:
        _FIREFOX_LOCK.release()

def _local_image_bytes(url_or_path):

    if re.match(r"^https?://", url_or_path or "", re.I):
        import urllib.request
        with urllib.request.urlopen(url_or_path, timeout=10) as r:
            return r.read(64 * 1024 * 1024)
    with open(url_or_path, "rb") as f:
        return f.read()

def render_ref(ref: dict) -> bytes:

    if not isinstance(ref, dict):
        return render_text(str(ref))
    kind = (ref.get("kind") or "").lower()
    val = ref.get("value")
    if kind in ("image", "file", "path") and val:
        try:
            return _to_jpeg(_fit(Image.open(io.BytesIO(_local_image_bytes(val)))))
        except Exception as e:
            return render_text("Bildfehler: %s" % e)
    if kind == "url" and val:

        if re.search(r"\.(png|jpe?g|gif|webp|bmp)(\?|$)", val, re.I):
            try:
                return _to_jpeg(_fit(Image.open(io.BytesIO(_local_image_bytes(val)))))
            except Exception:
                pass
        return render_url(val)
    if ref.get("text"):
        return render_text(ref["text"])
    if kind == "logo" or not ref:
        return render_logo()
    return render_text(val or json.dumps(ref)[:200])

class Frame:
    def __init__(self):
        self.jpeg = render_logo(); self.state = "idle"; self.title = "Brainarbeit"; self.gen = 0
        self.cond = threading.Condition()
    def set(self, jpeg, state, title=None):
        with self.cond:
            self.jpeg = jpeg; self.state = state
            if title: self.title = title
            self.gen += 1; self.cond.notify_all()

    def wait(self, last_gen, timeout):
        with self.cond:
            if self.gen == last_gen:
                self.cond.wait(timeout)
            return self.jpeg, self.gen

FRAME = Frame()
FETCHES = 0

NS_CONN = "urn:x-cast:com.google.cast.tp.connection"
NS_BEAT = "urn:x-cast:com.google.cast.tp.heartbeat"
NS_RECV = "urn:x-cast:com.google.cast.receiver"
NS_MEDIA = "urn:x-cast:com.google.cast.media"
SENDER = "sender-brainbox-0"
DEFAULT_MEDIA_RECEIVER = "CC1AD845"

def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F; n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)

def _dec_varint(buf, i):
    shift = 0; val = 0
    while i < len(buf):
        b = buf[i]; i += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, i
        shift += 7
    return val, i

def _str_field(fn, s):
    b = s.encode("utf-8")
    return _varint((fn << 3) | 2) + _varint(len(b)) + b

def _vint_field(fn, v):
    return _varint((fn << 3) | 0) + _varint(v)

def _encode_castmessage(source, dest, namespace, payload):
    m = (_vint_field(1, 0) + _str_field(2, source) + _str_field(3, dest)
         + _str_field(4, namespace) + _vint_field(5, 0) + _str_field(6, payload))
    return struct.pack(">I", len(m)) + m

def _decode_castmessage(buf):
    fields = {}; i = 0
    while i < len(buf):
        tag, i = _dec_varint(buf, i)
        fn, wire = tag >> 3, tag & 7
        if wire == 0:
            v, i = _dec_varint(buf, i); fields[fn] = v
        elif wire == 2:
            ln, i = _dec_varint(buf, i); fields[fn] = buf[i:i + ln]; i += ln
        elif wire == 5:
            fields[fn] = buf[i:i + 4]; i += 4
        elif wire == 1:
            fields[fn] = buf[i:i + 8]; i += 8
        else:
            break
    def s(fn):
        v = fields.get(fn)
        return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else ""
    return s(4), s(6)

class _Cast:

    def __init__(self):
        self.cast = None; self.port = None; self.self_ip = None
        self.enabled = False; self.keepalive = 90
        self.last_ok = None; self.last_err = None; self.last_shown = False
        self._nonce = 0; self._rid = 0
        self._lock = threading.Lock()
        self._dirty = threading.Event(); self._want_force = False
        self._push_seq = 0; self._push_done = threading.Condition()

    def configure(self, cast_ip, http_port, self_ip=None, keepalive=90):
        self.cast = cast_ip; self.port = http_port
        self.self_ip = self_ip or self._detect_ip(cast_ip)
        self.keepalive = keepalive; self.enabled = bool(cast_ip)
        if self.enabled:
            threading.Thread(target=self._pusher_loop, daemon=True).start()

    def request_push(self, force=False):
        with self._lock:
            self._want_force = self._want_force or force
        self._dirty.set()

    def _detect_ip(self, cast_ip):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((cast_ip, 8009)); ip = s.getsockname()[0]
            return None if ip.startswith("127.") else ip
        except Exception:
            return None
        finally:
            s.close()

    def _pusher_loop(self):
        self.push_current(force=True)
        while True:
            wait = self.keepalive if self.last_ok else 5
            self._dirty.wait(timeout=wait)
            self._dirty.clear()
            with self._lock:
                force = self._want_force; self._want_force = False
            try:
                self.push_current(force=force)
            except Exception as e:
                self.last_ok = False; self.last_err = str(e)

    def _open(self, timeout=8):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection((self.cast, 8009), timeout=timeout)
        s = ctx.wrap_socket(raw, server_hostname=None)
        s.settimeout(timeout)
        return s

    def _send(self, s, namespace, payload_obj, dest):
        s.sendall(_encode_castmessage(SENDER, dest, namespace, json.dumps(payload_obj)))

    def _recv(self, s):
        hdr = self._read_exact(s, 4)
        if not hdr:
            return None, None
        (ln,) = struct.unpack(">I", hdr)
        if ln == 0 or ln > 8 * 1024 * 1024:
            return None, None
        body = self._read_exact(s, ln)
        if not body:
            return None, None
        return _decode_castmessage(body)

    def _read_exact(self, s, n):
        buf = b""
        while len(buf) < n:
            try:
                chunk = s.recv(n - len(buf))
            except socket.timeout:
                return None
            if not chunk:
                return None
            buf += chunk
        return buf

    def _rid_next(self):
        self._rid += 1
        return self._rid

    def _pump(self, s, until, deadline):

        while time.time() < deadline:
            try:
                ns, payload = self._recv(s)
            except (socket.timeout, ssl.SSLError):
                continue
            except Exception:
                return None
            if ns is None:
                return None
            if ns == NS_BEAT:
                try:
                    if json.loads(payload or "{}").get("type") == "PING":
                        self._send(s, NS_BEAT, {"type": "PONG"}, "receiver-0")
                except Exception:
                    pass
                continue
            try:
                data = json.loads(payload or "{}")
            except Exception:
                data = {}
            res = until(ns, data)
            if res is not None:
                return res
        return None

    def _app_transport(self, data):
        for a in (data.get("status", {}) or {}).get("applications", []) or []:
            if a.get("appId") == DEFAULT_MEDIA_RECEIVER:
                return a.get("transportId")
        return None

    def _dmr_running(self):

        try:
            s = self._open(timeout=5)
        except Exception:
            return False
        try:
            self._send(s, NS_CONN, {"type": "CONNECT"}, "receiver-0")
            self._send(s, NS_RECV, {"type": "GET_STATUS", "requestId": self._rid_next()}, "receiver-0")
            tp = self._pump(s, lambda ns, d: (self._app_transport(d) or "") if ns == NS_RECV
                            and d.get("type") == "RECEIVER_STATUS" else None, time.time() + 5)
            return bool(tp)
        finally:
            try: s.close()
            except Exception: pass

    def push_seq(self):
        with self._push_done:
            return self._push_seq

    def wait_for_push(self, prev_seq, timeout):

        deadline = time.time() + timeout
        with self._push_done:
            while self._push_seq <= prev_seq:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False, self.last_shown
                self._push_done.wait(remaining)
            return True, self.last_shown

    def push_current(self, force=False):

        try:
            ok = self._push_current_inner(force)
        except Exception as e:
            self.last_ok = False; self.last_err = str(e); self.last_shown = False; ok = False
        with self._push_done:
            self._push_seq += 1
            self._push_done.notify_all()
        return ok

    def _push_current_inner(self, force=False):
        if not self.enabled:
            return False
        with self._lock:
            if not self.self_ip:
                self.self_ip = self._detect_ip(self.cast)
            if not self.self_ip:
                self.last_ok = False; self.last_err = "self-ip not ready (network down?)"; return False
        if not force and self._dmr_running():
            self.last_ok = True; self.last_err = None; self.last_shown = True
            return True
        self._nonce += 1
        url = "http://%s:%d/frame.jpg?gen=%d&r=%d" % (self.self_ip, self.port, FRAME.gen, self._nonce)
        ok, err = self._launch_and_load(url, FRAME.title)
        self.last_ok = ok; self.last_err = err; self.last_shown = ok
        return ok

    def _launch_and_load(self, url, title):
        try:
            s = self._open(timeout=8)
        except Exception as e:
            return False, "connect: %s" % e
        try:
            self._send(s, NS_CONN, {"type": "CONNECT"}, "receiver-0")
            self._send(s, NS_RECV, {"type": "GET_STATUS", "requestId": self._rid_next()}, "receiver-0")

            def _status(ns, d):
                if ns == NS_RECV and d.get("type") == "RECEIVER_STATUS":
                    return self._app_transport(d) or "__none__"
                return None
            transport = self._pump(s, _status, time.time() + 6)
            if not transport or transport == "__none__":
                self._send(s, NS_RECV, {"type": "LAUNCH", "appId": DEFAULT_MEDIA_RECEIVER,
                                        "requestId": self._rid_next()}, "receiver-0")
                transport = self._pump(s, lambda ns, d: (self._app_transport(d) or None) if ns == NS_RECV
                                       and d.get("type") == "RECEIVER_STATUS" else None, time.time() + 12)
            if not transport or transport == "__none__":
                return False, "receiver did not start Default Media Receiver"
            self._send(s, NS_CONN, {"type": "CONNECT"}, transport)

            for stream_type in ("BUFFERED", "NONE"):
                rid = self._rid_next()
                media = {"contentId": url, "contentType": "image/jpeg", "streamType": stream_type,
                         "metadata": {"metadataType": 0, "title": title or "Brainarbeit"}}
                self._send(s, NS_MEDIA, {"type": "LOAD", "media": media, "autoplay": True,
                                         "currentTime": 0, "requestId": rid}, transport)
                verdict = self._pump(s, self._load_verdict(rid), time.time() + 8)
                if verdict == "ok":
                    return True, None
                if verdict in ("LOAD_FAILED", "LOAD_CANCELLED"):
                    continue

            return False, "LOAD not confirmed"
        finally:
            try: s.close()
            except Exception: pass

    def _load_verdict(self, rid):
        def _f(ns, d):
            if ns != NS_MEDIA:
                return None
            t = d.get("type")
            if t == "MEDIA_STATUS" and (d.get("status") or []):
                return "ok"
            if t in ("LOAD_FAILED", "LOAD_CANCELLED", "INVALID_REQUEST", "INVALID_PLAYER_STATE"):
                return t
            return None
        return _f

    def reachable(self, timeout=1.5):

        if not self.enabled or not self.cast:
            return False
        try:
            socket.create_connection((self.cast, 8009), timeout=timeout).close()
            return True
        except Exception:
            return False

CAST = _Cast()

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def _send(self, code, ctype, body, extra=None):
        if isinstance(body, str): body = body.encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass

    def _json(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        try: return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except Exception: return {}

    def _frame_get(self, head_only=False):
        global FETCHES
        FETCHES += 1
        data = FRAME.jpeg; total = len(data); rng = self.headers.get("Range")
        hdrs = {"Accept-Ranges": "bytes", "Cache-Control": "no-store", "Pragma": "no-cache"}
        if rng:
            m = re.match(r"bytes=(\d+)-(\d*)", rng.strip())
            if m:
                a = int(m.group(1)); b = int(m.group(2)) if m.group(2) else total - 1
                b = min(b, total - 1); a = min(a, b); chunk = data[a:b + 1]
                self.send_response(206); self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(chunk)))
                hdrs["Content-Range"] = "bytes %d-%d/%d" % (a, b, total)
                for k, v in hdrs.items(): self.send_header(k, v)
                self.end_headers()
                if not head_only:
                    try: self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError): pass
                return
        self.send_response(200); self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(total))
        for k, v in hdrs.items(): self.send_header(k, v)
        self.end_headers()
        if not head_only:
            try: self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError): pass

    def do_HEAD(self):
        if urlparse(self.path).path == "/frame.jpg":
            return self._frame_get(head_only=True)
        self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/health":
            return self._send(200, "application/json", json.dumps(
                {"ok": True, "state": FRAME.state, "gen": FRAME.gen, "wh": [W, H], "fetches": FETCHES,
                 "cast": {"enabled": CAST.enabled, "cast": CAST.cast, "self_ip": CAST.self_ip,
                          "tv_reachable": CAST.reachable(), "last_ok": CAST.last_ok,
                          "last_shown": CAST.last_shown, "last_err": CAST.last_err}}))
        if p == "/frame.jpg":
            return self._frame_get()
        return self._send(404, "application/json", json.dumps({"error": "no route"}))

    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/show":
            ref = self._json()
            try:
                title = ref.get("title") or (ref.get("text") or "")[:40] or "Anzeige"
                seq0 = CAST.push_seq()
                FRAME.set(render_ref(ref), "showing", title=title)
                CAST.request_push(force=True)

                fresh, shown_real = CAST.wait_for_push(seq0, timeout=4.5)
                if fresh:
                    shown = bool(shown_real)
                    note = None if shown else ("Cast: %s" % CAST.last_err if CAST.last_err
                                               else "Fernseher/Streamer nicht erreichbar (vermutlich aus)")
                else:
                    shown = CAST.reachable()
                    note = None if shown else "Streamer nicht erreichbar (vermutlich aus)"
                return self._send(200, "application/json", json.dumps(
                    {"ok": True, "state": "showing", "shown_on_tv": shown, "tv": CAST.cast, "note": note}))
            except Exception as e:
                return self._send(200, "application/json", json.dumps({"ok": False, "error": str(e)}))
        if p == "/idle":
            FRAME.set(render_logo(), "idle", title="Brainarbeit")
            CAST.request_push(force=True)
            return self._send(200, "application/json", json.dumps({"ok": True, "state": "idle"}))
        if p == "/push":
            CAST.request_push(force=True)
            return self._send(200, "application/json",
                              json.dumps({"ok": CAST.enabled, "queued": True, "gen": FRAME.gen}))
        return self._send(404, "application/json", json.dumps({"error": "no route"}))

def main():
    ap = argparse.ArgumentParser(prog="pn-cast-display")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8096)
    ap.add_argument("--cast", default=os.environ.get("CAST_IP", ""), help="Google Cast device IP")
    ap.add_argument("--self-ip", default=os.environ.get("CAST_SELF_IP", ""),
                    help="address the Cast device reaches us on")
    ap.add_argument("--keepalive", type=int, default=90, help="seconds between re-assert (persistence)")
    ap.add_argument("--no-cast", action="store_true", help="serve frames only; do not drive a device")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    if a.cast and not a.no_cast:
        CAST.configure(a.cast, a.port, self_ip=(a.self_ip or None), keepalive=a.keepalive)
        print("pn-cast-display on http://%s:%d  -> Cast %s (self=%s)" %
              (a.host, a.port, a.cast, CAST.self_ip))
    else:
        print("pn-cast-display on http://%s:%d  (frames only, no Cast target)" % (a.host, a.port))
    srv.serve_forever()

if __name__ == "__main__":
    main()
