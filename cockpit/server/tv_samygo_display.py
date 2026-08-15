#!/usr/bin/env python3

import os, io, sys, json, time, socket, threading, argparse, subprocess, tempfile, html, re, shlex
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
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

def _to_baseline_jpeg(img) -> bytes:
    b = io.BytesIO()
    img.save(b, format="JPEG", quality=85, progressive=False, optimize=False)
    return b.getvalue()

def render_logo() -> bytes:
    if os.path.exists(LOGO):
        try: return _to_baseline_jpeg(_fit(Image.open(LOGO)))
        except Exception: pass
    c = _canvas(); d = ImageDraw.Draw(c)
    d.text((W // 2, H // 2), "BRAINARBEIT", fill=(20, 24, 30), font=_font(96), anchor="mm")
    return _to_baseline_jpeg(c)

def render_text(text) -> bytes:
    c = _canvas(); d = ImageDraw.Draw(c)
    d.text((W // 2, H // 2), (text or "")[:400], fill=(20, 24, 30), font=_font(72), anchor="mm")
    return _to_baseline_jpeg(c)

def render_image_file(path) -> bytes:
    return _to_baseline_jpeg(_fit(Image.open(path)))

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
            rc, png_b, err = _PN.run_capture(["/bin/sh", "-c", sh], mem=700, timeout_s=50,
                                             tag="display.render", latency="realtime", wait_s=90)
            if err:
                return render_text("(Anzeige nicht möglich: %s)" % err[:160])
            if png_b:
                try:
                    return _to_baseline_jpeg(_fit(Image.open(io.BytesIO(png_b))))
                except Exception:
                    pass
            return render_text("(konnte %s nicht rendern)" % url)
        print("[samygo] Governor (pnd) nicht erreichbar — URL-Render läuft ausnahmsweise direkt.",
              file=sys.stderr, flush=True)
        png = tempfile.mktemp(suffix=".png")
        try:
            subprocess.run(["nice", "-n", "10", "firefox", "--headless", "--width", str(W),
                            "--height", str(H), "--screenshot", png, url], timeout=45, capture_output=True)
            if os.path.exists(png) and os.path.getsize(png) > 0:
                return _to_baseline_jpeg(_fit(Image.open(png)))
        except Exception:
            pass
        finally:
            try: os.unlink(png)
            except OSError: pass
        return render_text("(konnte %s nicht rendern)" % url)
    finally:
        _FIREFOX_LOCK.release()

def render_ref(ref: dict) -> bytes:

    if not isinstance(ref, dict):
        return render_text(str(ref))
    kind = (ref.get("kind") or "").lower()
    if kind in ("image", "file", "path") and ref.get("value"):
        try: return render_image_file(ref["value"])
        except Exception as e: return render_text("Bildfehler: %s" % e)
    if kind == "url" and ref.get("value"):
        return render_url(ref["value"])
    if ref.get("text"):
        return render_text(ref["text"])
    if kind == "logo" or not ref:
        return render_logo()
    return render_text(ref.get("value") or json.dumps(ref)[:200])

class Frame:

    def __init__(self):
        self.jpeg = render_logo(); self.state = "idle"; self.title = "Brainarbeit"; self.gen = 0
        self.cond = threading.Condition()
    def set(self, jpeg, state, title=None):
        with self.cond:
            self.jpeg = jpeg; self.state = state
            if title: self.title = title
            self.gen += 1; self.cond.notify_all()
        DLNA.request_push()
    def wait(self, last_gen, timeout):
        with self.cond:
            if self.gen == last_gen:
                self.cond.wait(timeout)
            return self.jpeg, self.gen

FRAME = Frame()
FETCHES = 0

DLNA_CF = ("DLNA.ORG_PN=JPEG_LRG;DLNA.ORG_OP=01;DLNA.ORG_CI=0;"
           "DLNA.ORG_FLAGS=00D00000000000000000000000000000")
AVT_SVC = "urn:schemas-upnp-org:service:AVTransport:1"

class _Dlna:

    def __init__(self):
        self.tv = None; self.port = None; self.self_ip = None
        self.ctrl = None; self.enabled = False; self._nonce = 0
        self.keepalive = 90; self.last_ok = None; self.last_err = None
        self._lock = threading.Lock(); self._dirty = threading.Event()
    def configure(self, tv_ip, http_port, self_ip=None, keepalive=90):
        self.tv = tv_ip; self.port = http_port
        self.self_ip = self_ip or self._detect_ip(tv_ip)
        self.keepalive = keepalive; self.enabled = bool(tv_ip)
        if self.enabled:
            self.discover()
            threading.Thread(target=self._pusher_loop, daemon=True).start()
    def request_push(self):

        self._dirty.set()
    def _pusher_loop(self):
        self.push_current()
        while True:

            wait = self.keepalive if self.last_ok else 5
            self._dirty.wait(timeout=wait)
            self._dirty.clear()
            try: self.push_current()
            except Exception: pass
    def _detect_ip(self, tv_ip):

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((tv_ip, 7676)); ip = s.getsockname()[0]
            return None if ip.startswith("127.") else ip
        except Exception:
            return None
        finally:
            s.close()
    def discover(self):

        cands = self._ssdp_locations()
        cands.append("http://%s:7676/smp_15_" % self.tv)
        seen = set()
        for loc in cands:
            if not loc or loc in seen: continue
            seen.add(loc)
            ctrl = self._avt_control_from(loc)
            if ctrl:
                self.ctrl = ctrl
                return ctrl
        return None
    def _avt_control_from(self, loc):
        try:
            xml = urllib.request.urlopen(loc, timeout=5).read().decode("utf-8", "replace")
        except Exception:
            return None
        base = "%s://%s" % (urlparse(loc).scheme, urlparse(loc).netloc)
        for m in re.finditer(r"<service>(.*?)</service>", xml, re.S):
            blk = m.group(1)
            if "AVTransport" in blk:
                cu = re.search(r"<controlURL>(.*?)</controlURL>", blk)
                if cu:
                    u = cu.group(1).strip()
                    return u if u.startswith("http") else base + (u if u.startswith("/") else "/" + u)
        return None
    def _ssdp_locations(self):

        out, s = [], None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2); s.settimeout(3)
            msg = ("M-SEARCH * HTTP/1.1\r\nHOST:239.255.255.250:1900\r\n"
                   'MAN:"ssdp:discover"\r\nMX:2\r\nST:ssdp:all\r\n\r\n')
            s.sendto(msg.encode(), ("239.255.255.250", 1900))
            t = time.time()
            while time.time() - t < 3.5:
                try: data, addr = s.recvfrom(4096)
                except socket.timeout: break
                if addr[0] == self.tv:
                    m = re.search(r"LOCATION:\s*(\S+)", data.decode("utf-8", "replace"), re.I)
                    if m and m.group(1) not in out: out.append(m.group(1))
        except Exception:
            pass
        finally:
            try:
                if s: s.close()
            except Exception: pass
        return out
    def _soap(self, action, args, timeout=8):
        body = ('<?xml version="1.0" encoding="utf-8"?>'
                '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
                '<s:Body><u:%s xmlns:u="%s">%s</u:%s></s:Body></s:Envelope>' % (action, AVT_SVC, args, action))
        req = urllib.request.Request(self.ctrl, data=body.encode(),
            headers={"Content-Type": 'text/xml; charset="utf-8"',
                     "SOAPACTION": '"%s#%s"' % (AVT_SVC, action)})
        return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    def _didl(self, url, title):
        return ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" '
                'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
                'xmlns:sec="http://www.sec.co.kr/">'
                '<item id="0" parentID="-1" restricted="1"><dc:title>%s</dc:title>'
                '<upnp:class>object.item.imageItem.photo</upnp:class>'
                '<res protocolInfo="http-get:*:image/jpeg:%s">%s</res></item></DIDL-Lite>'
                % (html.escape(title), DLNA_CF, html.escape(url)))
    def push_current(self):
        if not self.enabled: return False
        with self._lock:

            if not self.self_ip:
                self.self_ip = self._detect_ip(self.tv)
            if not self.self_ip:
                self.last_ok = False; self.last_err = "self-ip not ready (network down?)"; return False
            if not self.ctrl and not self.discover():
                self.last_ok = False; self.last_err = "no AVTransport found"; return False
            self._nonce += 1
            url = "http://%s:%d/frame.jpg?gen=%d&r=%d" % (self.self_ip, self.port, FRAME.gen, self._nonce)
            meta = self._didl(url, FRAME.title)
            try:

                self._soap("SetAVTransportURI",
                    "<InstanceID>0</InstanceID><CurrentURI>%s</CurrentURI>"
                    "<CurrentURIMetaData>%s</CurrentURIMetaData>" % (html.escape(url), html.escape(meta)),
                    timeout=20)
                self.last_ok = True; self.last_err = None
                return True
            except Exception as e:
                msg = str(e).lower()
                if isinstance(e, TimeoutError) or "timed out" in msg or "timeout" in msg:

                    self.last_ok = True; self.last_err = "slow(timeout)"
                    return True

                sys.stderr.write("dlna push failed (%s); will re-discover\n" % e)
                self.last_ok = False; self.last_err = str(e); self.ctrl = None
                return False

    def reachable(self, timeout=1.5):

        if not self.enabled or not self.tv:
            return False
        try:
            s = socket.create_connection((self.tv, 7676), timeout=timeout)
            s.close()
            return True
        except Exception:
            return False

DLNA = _Dlna()

RECEIVER_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Brainarbeit</title>
<style>html,body{margin:0;height:100%;background:#fff;overflow:hidden;cursor:none}
img{position:fixed;inset:0;width:100%;height:100%;object-fit:contain;background:#fff}</style></head>
<body><img id="s" src="/frame.jpg">
<script>
/* Legacy Smart-Hub fallback receiver (the primary path is DLNA push). */
var img=document.getElementById('s'), useStream=false;
function refresh(){ if(!useStream){ img.src='/frame.jpg?t='+Date.now(); } }
var probe=new Image(); probe.onload=function(){ useStream=true; img.src='/stream'; };
probe.onerror=function(){ setInterval(refresh, 700); }; probe.src='/stream';
setTimeout(function(){ if(!useStream){ setInterval(refresh, 700); } }, 2500);
document.onkeydown=function(e){ e.preventDefault(); return false; };
</script></body></html>"""

def widgetlist_xml(host_base):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rsp stat="ok">\n'
            '  <list>\n    <widget id="Brainarbeit.TVReceiver">\n'
            '      <title>Brainarbeit</title>\n      <compression size="0" type="zip"/>\n'
            '      <description>Brainarbeit TV Receiver</description>\n'
            '      <download>%s/widget/Brainarbeit.zip</download>\n'
            '    </widget>\n  </list>\n</rsp>\n' % host_base)

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def _send(self, code, ctype, body, extra=None):
        if isinstance(body, str): body = body.encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)

    def _json(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        try: return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except Exception: return {}

    def _dlna_img_headers(self, extra=None):
        h = {"Accept-Ranges": "bytes", "transferMode.dlna.org": "Interactive",
             "contentFeatures.dlna.org": DLNA_CF, "Cache-Control": "no-store", "Pragma": "no-cache"}
        if extra: h.update(extra)
        return h

    def _frame_get(self, head_only=False):

        global FETCHES
        FETCHES += 1
        data = FRAME.jpeg; total = len(data); rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d+)-(\d*)", rng.strip())
            if m:
                a = int(m.group(1)); b = int(m.group(2)) if m.group(2) else total - 1
                b = min(b, total - 1); a = min(a, b)
                chunk = data[a:b + 1]
                self.send_response(206)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(chunk)))
                for k, v in self._dlna_img_headers({"Content-Range": "bytes %d-%d/%d" % (a, b, total)}).items():
                    self.send_header(k, v)
                self.end_headers()
                if not head_only: self.wfile.write(chunk)
                return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(total))
        for k, v in self._dlna_img_headers().items(): self.send_header(k, v)
        self.end_headers()
        if not head_only: self.wfile.write(data)

    def do_HEAD(self):
        if urlparse(self.path).path == "/frame.jpg":
            return self._frame_get(head_only=True)
        self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()

    def do_GET(self):
        u = urlparse(self.path); p = u.path
        if p == "/health":
            return self._send(200, "application/json", json.dumps(
                {"ok": True, "state": FRAME.state, "gen": FRAME.gen, "wh": [W, H], "fetches": FETCHES,
                 "dlna": {"enabled": DLNA.enabled, "tv": DLNA.tv, "ctrl": DLNA.ctrl, "self_ip": DLNA.self_ip,
                          "tv_reachable": DLNA.reachable(), "last_ok": DLNA.last_ok, "last_err": DLNA.last_err}}))
        if p == "/" or p == "/index.html":
            return self._send(200, "text/html; charset=utf-8", RECEIVER_HTML)
        if p == "/frame.jpg":
            return self._frame_get()
        if p == "/widgetlist.xml":
            host = "http://" + (self.headers.get("Host") or "localhost")
            return self._send(200, "application/xml", widgetlist_xml(host))
        if p == "/stream":
            return self._stream()
        return self._send(404, "application/json", json.dumps({"error": "no route"}))

    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/show":
            ref = self._json()
            try:
                title = ref.get("title") or (ref.get("text") or "")[:40] or "Anzeige"
                FRAME.set(render_ref(ref), "showing", title=title)
                shown = DLNA.reachable()
                return self._send(200, "application/json", json.dumps(
                    {"ok": True, "state": "showing", "shown_on_tv": shown, "tv": DLNA.tv,
                     "note": None if shown else "Fernseher nicht erreichbar (vermutlich aus)"}))
            except Exception as e:
                return self._send(200, "application/json", json.dumps({"ok": False, "error": str(e)}))
        if p == "/idle":
            FRAME.set(render_logo(), "idle", title="Brainarbeit")
            return self._send(200, "application/json", json.dumps({"ok": True, "state": "idle"}))
        if p == "/push":
            DLNA.request_push()
            return self._send(200, "application/json",
                              json.dumps({"ok": DLNA.enabled, "queued": True, "gen": FRAME.gen}))
        return self._send(404, "application/json", json.dumps({"error": "no route"}))

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store"); self.end_headers()
        last = -1
        try:
            while True:
                jpeg, last = FRAME.wait(last, 2.0)
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

def main():
    ap = argparse.ArgumentParser(prog="tv-samygo-display")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8096)
    ap.add_argument("--tv", default=os.environ.get("TV_DMR", ""), help="Samsung MediaRenderer IP (DLNA target)")
    ap.add_argument("--self-ip", default=os.environ.get("TV_SELF_IP", ""), help="address the TV reaches us on")
    ap.add_argument("--keepalive", type=int, default=90, help="seconds between re-push (survive standby)")
    ap.add_argument("--no-dlna", action="store_true", help="serve frames only; do not push to a TV")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    if a.tv and not a.no_dlna:
        DLNA.configure(a.tv, a.port, self_ip=(a.self_ip or None), keepalive=a.keepalive)

        print("tv-samygo-display on http://%s:%d  -> DLNA push to %s (ctrl=%s, self=%s)" %
              (a.host, a.port, a.tv, DLNA.ctrl, DLNA.self_ip))
    else:
        print("tv-samygo-display on http://%s:%d  (frames only, no DLNA target)" % (a.host, a.port))
    srv.serve_forever()

if __name__ == "__main__":
    main()
