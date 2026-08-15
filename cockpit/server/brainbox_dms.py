#!/usr/bin/env python3

import os, io, re, sys, json, time, uuid, socket, struct, threading, subprocess, tempfile, html, argparse, shlex
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

UDN = "uuid:4d726272-6169-6e62-6f78-000000000001"
NAME = "Brainbox"
W, H = 1920, 1080
LOGO = os.environ.get("TV_IDLE_LOGO", os.path.expanduser("~/brainarbeit-site/brainbox-lockup.png"))
PORT = 8200
SSDP_MCAST = ("239.255.255.250", 1900)

def _font(sz):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p): return ImageFont.truetype(p, sz)
    return ImageFont.load_default()

def _fit_logo():
    c = Image.new("RGB", (W, H), (255, 255, 255))
    if os.path.exists(LOGO):
        try:
            im = Image.open(LOGO).convert("RGB"); im.thumbnail((W, H), Image.LANCZOS)
            c.paste(im, ((W - im.width) // 2, (H - im.height) // 2)); return c
        except Exception: pass
    d = ImageDraw.Draw(c); d.text((W // 2, H // 2), "BRAINARBEIT", fill=(20, 24, 30), font=_font(110), anchor="mm")
    return c

def _text(txt):
    c = Image.new("RGB", (W, H), (255, 255, 255)); d = ImageDraw.Draw(c)
    d.text((W // 2, H // 2), (txt or "")[:400], fill=(20, 24, 30), font=_font(96), anchor="mm"); return c

def render_ref(ref):
    if not isinstance(ref, dict): return _text(str(ref))
    k = (ref.get("kind") or "").lower()
    if k in ("image", "file", "path") and ref.get("value"):
        try:
            im = Image.open(ref["value"]).convert("RGB"); im.thumbnail((W, H), Image.LANCZOS)
            c = Image.new("RGB", (W, H), (255, 255, 255)); c.paste(im, ((W - im.width) // 2, (H - im.height) // 2)); return c
        except Exception as e: return _text("Bildfehler: %s" % e)
    if ref.get("text"): return _text(ref["text"])
    return _fit_logo()

def to_jpeg(img):
    b = io.BytesIO(); img.save(b, "JPEG", quality=88, progressive=False); return b.getvalue()

class Media:
    def __init__(self):
        self.lock = threading.Lock(); self.title = "Brainbox Logo"; self.rev = 0
        self.jpeg = b""; self.mp4 = b""
        self.set(_fit_logo(), "Brainbox Logo")
    def set(self, img, title):
        jpeg = to_jpeg(img)
        mp4 = self._mp4_from_jpeg(jpeg)
        with self.lock:
            self.jpeg, self.mp4, self.title, self.rev = jpeg, mp4, title, self.rev + 1
    def _mp4_from_jpeg(self, jpeg):

        src = tempfile.mktemp(suffix=".jpg")
        open(src, "wb").write(jpeg)
        try:
            if _PN is not None and _PN.pn_available():
                sh = ('ffmpeg -y -loglevel error -loop 1 -i %s -f lavfi -i anullsrc=r=48000:cl=stereo '
                      '-t 10 -r 5 -c:v libx264 -profile:v main -level 3.1 -pix_fmt yuv420p '
                      '-c:a aac -b:a 96k -shortest -movflags +faststart "$TMPDIR/out.mp4" '
                      '&& cat "$TMPDIR/out.mp4"' % shlex.quote(src))
                rc, mp4, err = _PN.run_capture(["/bin/sh", "-c", sh], mem=300, timeout_s=70,
                                               tag="media.encode", latency="realtime", wait_s=120)
                if err:
                    print("[dms] governed encode fehlgeschlagen: %s" % err,
                          file=sys.stderr, flush=True)
                    return b""
                return mp4 or b""
            print("[dms] Governor (pnd) nicht erreichbar — Encode läuft ausnahmsweise direkt.",
                  file=sys.stderr, flush=True)
            out = tempfile.mktemp(suffix=".mp4")
            try:
                subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", src,
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "10", "-r", "5",
                    "-c:v", "libx264", "-profile:v", "main", "-level", "3.1", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", out],
                    capture_output=True, timeout=60)
                return open(out, "rb").read() if os.path.exists(out) and os.path.getsize(out) else b""
            except Exception:
                return b""
            finally:
                try: os.unlink(out)
                except OSError: pass
        finally:
            try: os.unlink(src)
            except OSError: pass

MEDIA = None

def self_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try: s.connect(("239.255.255.250", 1900)); return s.getsockname()[0]
    except Exception: return "127.0.0.1"
    finally: s.close()
MYIP = None

def desc_xml():
    base = "http://%s:%d" % (MYIP, PORT)
    return ('<?xml version="1.0"?>\n'
      '<root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:dlna="urn:schemas-dlna-org:device-1-0" xmlns:sec="http://www.sec.co.kr/dlna">\n'
      '<specVersion><major>1</major><minor>0</minor></specVersion>\n'
      '<device>\n'
      '<dlna:X_DLNADOC>DMS-1.50</dlna:X_DLNADOC>\n'
      '<deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>\n'
      '<friendlyName>%s</friendlyName>\n'
      '<manufacturer>Brainarbeit</manufacturer>\n'
      '<manufacturerURL>http://brainarbeit.com</manufacturerURL>\n'
      '<modelDescription>Brainbox Media Server</modelDescription>\n'
      '<modelName>Brainbox</modelName>\n'
      '<modelNumber>1</modelNumber>\n'
      '<modelURL>http://brainarbeit.com</modelURL>\n'
      '<serialNumber>0001</serialNumber>\n'
      '<UDN>%s</UDN>\n'
      '<sec:ProductCap>smi,DCM10,getMediaInfo.sec,getCaptionInfo.sec</sec:ProductCap>\n'
      '<iconList><icon><mimetype>image/png</mimetype><width>48</width><height>48</height><depth>24</depth><url>/icon.png</url></icon></iconList>\n'
      '<serviceList>\n'
      '<service><serviceType>urn:schemas-upnp-org:service:ContentDirectory:1</serviceType>'
      '<serviceId>urn:upnp-org:serviceId:ContentDirectory</serviceId>'
      '<SCPDURL>/cd/scpd.xml</SCPDURL><controlURL>/cd/control</controlURL><eventSubURL>/cd/event</eventSubURL></service>\n'
      '<service><serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType>'
      '<serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>'
      '<SCPDURL>/cm/scpd.xml</SCPDURL><controlURL>/cm/control</controlURL><eventSubURL>/cm/event</eventSubURL></service>\n'
      '</serviceList>\n'
      '</device>\n</root>\n' % (NAME, UDN)).encode()

CD_SCPD = ('<?xml version="1.0"?>\n<scpd xmlns="urn:schemas-upnp-org:service-1-0">'
  '<specVersion><major>1</major><minor>0</minor></specVersion><actionList>'
  '<action><name>Browse</name><argumentList>'
  '<argument><name>ObjectID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ObjectID</relatedStateVariable></argument>'
  '<argument><name>BrowseFlag</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_BrowseFlag</relatedStateVariable></argument>'
  '<argument><name>Filter</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Filter</relatedStateVariable></argument>'
  '<argument><name>StartingIndex</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Index</relatedStateVariable></argument>'
  '<argument><name>RequestedCount</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>'
  '<argument><name>SortCriteria</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_SortCriteria</relatedStateVariable></argument>'
  '<argument><name>Result</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Result</relatedStateVariable></argument>'
  '<argument><name>NumberReturned</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>'
  '<argument><name>TotalMatches</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>'
  '<argument><name>UpdateID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_UpdateID</relatedStateVariable></argument>'
  '</argumentList></action>'
  '<action><name>GetSystemUpdateID</name><argumentList><argument><name>Id</name><direction>out</direction><relatedStateVariable>SystemUpdateID</relatedStateVariable></argument></argumentList></action>'
  '<action><name>GetSortCapabilities</name><argumentList><argument><name>SortCaps</name><direction>out</direction><relatedStateVariable>SortCapabilities</relatedStateVariable></argument></argumentList></action>'
  '<action><name>GetSearchCapabilities</name><argumentList><argument><name>SearchCaps</name><direction>out</direction><relatedStateVariable>SearchCapabilities</relatedStateVariable></argument></argumentList></action>'
  '</actionList><serviceStateTable>'
  '<stateVariable sendEvents="no"><name>A_ARG_TYPE_ObjectID</name><dataType>string</dataType></stateVariable>'
  '<stateVariable sendEvents="no"><name>A_ARG_TYPE_Result</name><dataType>string</dataType></stateVariable>'
  '<stateVariable sendEvents="no"><name>A_ARG_TYPE_BrowseFlag</name><dataType>string</dataType><allowedValueList><allowedValue>BrowseMetadata</allowedValue><allowedValue>BrowseDirectChildren</allowedValue></allowedValueList></stateVariable>'
  '<stateVariable sendEvents="no"><name>A_ARG_TYPE_Filter</name><dataType>string</dataType></stateVariable>'
  '<stateVariable sendEvents="no"><name>A_ARG_TYPE_SortCriteria</name><dataType>string</dataType></stateVariable>'
  '<stateVariable sendEvents="no"><name>A_ARG_TYPE_Index</name><dataType>ui4</dataType></stateVariable>'
  '<stateVariable sendEvents="no"><name>A_ARG_TYPE_Count</name><dataType>ui4</dataType></stateVariable>'
  '<stateVariable sendEvents="no"><name>A_ARG_TYPE_UpdateID</name><dataType>ui4</dataType></stateVariable>'
  '<stateVariable sendEvents="yes"><name>SystemUpdateID</name><dataType>ui4</dataType></stateVariable>'
  '<stateVariable sendEvents="no"><name>SortCapabilities</name><dataType>string</dataType></stateVariable>'
  '<stateVariable sendEvents="no"><name>SearchCapabilities</name><dataType>string</dataType></stateVariable>'
  '</serviceStateTable></scpd>').encode()

CM_SCPD = ('<?xml version="1.0"?>\n<scpd xmlns="urn:schemas-upnp-org:service-1-0">'
  '<specVersion><major>1</major><minor>0</minor></specVersion><actionList>'
  '<action><name>GetProtocolInfo</name><argumentList>'
  '<argument><name>Source</name><direction>out</direction><relatedStateVariable>SourceProtocolInfo</relatedStateVariable></argument>'
  '<argument><name>Sink</name><direction>out</direction><relatedStateVariable>SinkProtocolInfo</relatedStateVariable></argument></argumentList></action>'
  '<action><name>GetCurrentConnectionIDs</name><argumentList><argument><name>ConnectionIDs</name><direction>out</direction><relatedStateVariable>CurrentConnectionIDs</relatedStateVariable></argument></argumentList></action>'
  '</actionList><serviceStateTable>'
  '<stateVariable sendEvents="yes"><name>SourceProtocolInfo</name><dataType>string</dataType></stateVariable>'
  '<stateVariable sendEvents="yes"><name>SinkProtocolInfo</name><dataType>string</dataType></stateVariable>'
  '<stateVariable sendEvents="yes"><name>CurrentConnectionIDs</name><dataType>string</dataType></stateVariable>'
  '</serviceStateTable></scpd>').encode()

JPEG_PI = "http-get:*:image/jpeg:DLNA.ORG_PN=JPEG_LRG;DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=00D00000000000000000000000000000"
MP4_PI  = "http-get:*:video/mp4:DLNA.ORG_PN=AVC_MP4_MP_SD_AAC_MULT5;DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"
SOURCE_PI = JPEG_PI + "," + MP4_PI

def didl_items():
    base = "http://%s:%d" % (MYIP, PORT)
    with MEDIA.lock:
        jlen, mlen, title = len(MEDIA.jpeg), len(MEDIA.mp4), MEDIA.title
    items = ('<item id="2" parentID="0" restricted="1"><dc:title>Brainbox Anzeige</dc:title>'
             '<upnp:class>object.item.videoItem.movie</upnp:class>'
             '<res protocolInfo="%s" resolution="1920x1080" duration="0:00:10.000" size="%d">%s/media/current.mp4</res></item>'
             '<item id="1" parentID="0" restricted="1"><dc:title>Brainbox Logo</dc:title>'
             '<upnp:class>object.item.imageItem.photo</upnp:class>'
             '<res protocolInfo="%s" resolution="1920x1080" size="%d">%s/media/current.jpg</res></item>'
             % (MP4_PI, mlen, base, JPEG_PI, jlen, base))
    return items, 2

def didl_root_meta():
    return ('<container id="0" parentID="-1" restricted="1" childCount="2"><dc:title>Brainbox</dc:title>'
            '<upnp:class>object.container.storageFolder</upnp:class></container>')

def wrap_didl(inner):
    return ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
            'xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/">' + inner + '</DIDL-Lite>')

def soap_resp(action, svc, body_inner):
    return ('<?xml version="1.0"?>\n<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            '<u:%sResponse xmlns:u="%s">%s</u:%sResponse></s:Body></s:Envelope>' % (action, svc, body_inner, action)).encode()

CD_SVC = "urn:schemas-upnp-org:service:ContentDirectory:1"
CM_SVC = "urn:schemas-upnp-org:service:ConnectionManager:1"

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _send(self, code, ctype, body, extra=None):
        if isinstance(body, str): body = body.encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(body)
    def _media(self, kind, head=False):
        with MEDIA.lock:
            data = MEDIA.jpeg if kind == "jpg" else MEDIA.mp4
        ctype = "image/jpeg" if kind == "jpg" else "video/mp4"
        pn = JPEG_PI.split(":", 3)[3] if kind == "jpg" else MP4_PI.split(":", 3)[3]
        tmode = "Interactive" if kind == "jpg" else "Streaming"
        total = len(data); rng = self.headers.get("Range")
        base_h = {"Accept-Ranges": "bytes", "transferMode.dlna.org": tmode,
                  "contentFeatures.dlna.org": pn, "Cache-Control": "no-store"}
        if rng and not head:
            m = re.match(r"bytes=(\d+)-(\d*)", rng); a = int(m.group(1)); b = int(m.group(2)) if m.group(2) else total - 1
            b = min(b, total - 1); base_h["Content-Range"] = "bytes %d-%d/%d" % (a, b, total)
            self.send_response(206); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(b - a + 1))
            for k, v in base_h.items(): self.send_header(k, v)
            self.end_headers(); self.wfile.write(data[a:b + 1]); return
        self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(total))
        for k, v in base_h.items(): self.send_header(k, v)
        self.end_headers()
        if not head: self.wfile.write(data)
    def do_HEAD(self):
        p = urlparse(self.path).path
        if p == "/media/current.jpg": return self._media("jpg", head=True)
        if p == "/media/current.mp4": return self._media("mp4", head=True)
        self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/desc.xml", "/"): return self._send(200, "text/xml; charset=utf-8", desc_xml())
        if p == "/cd/scpd.xml": return self._send(200, "text/xml; charset=utf-8", CD_SCPD)
        if p == "/cm/scpd.xml": return self._send(200, "text/xml; charset=utf-8", CM_SCPD)
        if p == "/media/current.jpg": return self._media("jpg")
        if p == "/media/current.mp4": return self._media("mp4")
        if p == "/icon.png":
            b = io.BytesIO(); _fit_logo().resize((48, 48)).save(b, "PNG"); return self._send(200, "image/png", b.getvalue())
        if p == "/health":
            return self._send(200, "application/json", json.dumps({"ok": True, "name": NAME, "udn": UDN, "ip": MYIP, "rev": MEDIA.rev, "title": MEDIA.title}))
        return self._send(404, "text/plain", "no route")
    def do_POST(self):
        p = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0) or 0); raw = self.rfile.read(n) if n else b""
        if p == "/cd/control": return self._cd_control(raw)
        if p == "/cm/control": return self._cm_control(raw)
        if p == "/show":
            try: ref = json.loads(raw or b"{}")
            except Exception: ref = {}
            title = ref.get("title") or (ref.get("text") or "")[:40] or "Brainbox Anzeige"
            MEDIA.set(render_ref(ref), title)
            return self._send(200, "application/json", json.dumps({"ok": True, "rev": MEDIA.rev}))
        if p == "/idle":
            MEDIA.set(_fit_logo(), "Brainbox Logo")
            return self._send(200, "application/json", json.dumps({"ok": True, "rev": MEDIA.rev}))
        return self._send(404, "text/plain", "no route")
    def _soapaction(self):
        return (self.headers.get("SOAPACTION") or "").strip('"')
    def _cd_control(self, raw):
        act = self._soapaction()
        if act.endswith("#Browse"):
            txt = raw.decode("utf-8", "replace")
            obj = (re.search(r"<ObjectID>(.*?)</ObjectID>", txt) or [None, "0"])[1] if re.search(r"<ObjectID>(.*?)</ObjectID>", txt) else "0"
            flag = (re.search(r"<BrowseFlag>(.*?)</BrowseFlag>", txt) or [None, "BrowseDirectChildren"])[1] if re.search(r"<BrowseFlag>(.*?)</BrowseFlag>", txt) else "BrowseDirectChildren"
            if flag == "BrowseMetadata":
                didl = wrap_didl(didl_root_meta()); num, total = 1, 1
            else:
                items, cnt = didl_items(); didl = wrap_didl(items); num, total = cnt, cnt
            inner = ("<Result>%s</Result><NumberReturned>%d</NumberReturned><TotalMatches>%d</TotalMatches><UpdateID>%d</UpdateID>"
                     % (html.escape(didl), num, total, MEDIA.rev))
            return self._send(200, 'text/xml; charset="utf-8"', soap_resp("Browse", CD_SVC, inner))
        if act.endswith("#GetSortCapabilities"):
            return self._send(200, 'text/xml; charset="utf-8"', soap_resp("GetSortCapabilities", CD_SVC, "<SortCaps></SortCaps>"))
        if act.endswith("#GetSearchCapabilities"):
            return self._send(200, 'text/xml; charset="utf-8"', soap_resp("GetSearchCapabilities", CD_SVC, "<SearchCaps></SearchCaps>"))
        if act.endswith("#GetSystemUpdateID"):
            return self._send(200, 'text/xml; charset="utf-8"', soap_resp("GetSystemUpdateID", CD_SVC, "<Id>%d</Id>" % MEDIA.rev))
        return self._send(200, 'text/xml; charset="utf-8"', soap_resp("Browse", CD_SVC, "<Result></Result><NumberReturned>0</NumberReturned><TotalMatches>0</TotalMatches><UpdateID>0</UpdateID>"))
    def _cm_control(self, raw):
        act = self._soapaction()
        if act.endswith("#GetProtocolInfo"):
            return self._send(200, 'text/xml; charset="utf-8"', soap_resp("GetProtocolInfo", CM_SVC, "<Source>%s</Source><Sink></Sink>" % html.escape(SOURCE_PI)))
        if act.endswith("#GetCurrentConnectionIDs"):
            return self._send(200, 'text/xml; charset="utf-8"', soap_resp("GetCurrentConnectionIDs", CM_SVC, "<ConnectionIDs>0</ConnectionIDs>"))
        return self._send(200, 'text/xml; charset="utf-8"', soap_resp("GetProtocolInfo", CM_SVC, "<Source>%s</Source><Sink></Sink>" % html.escape(SOURCE_PI)))

NTS = ["upnp:rootdevice", UDN, "urn:schemas-upnp-org:device:MediaServer:1",
       "urn:schemas-upnp-org:service:ContentDirectory:1", "urn:schemas-upnp-org:service:ConnectionManager:1"]
def usn(nt): return UDN if nt == UDN else (UDN + "::" + nt)
SERVER = "Linux/6 UPnP/1.0 Brainbox/1.0"

def ssdp_thread():
    loc = "http://%s:%d/desc.xml" % (MYIP, PORT)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try: sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except Exception: pass
    sock.bind(("", 1900))
    mreq = struct.pack("4sl", socket.inet_aton("239.255.255.250"), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    def notify(alive=True):
        for nt in NTS:
            if alive:
                msg = ("NOTIFY * HTTP/1.1\r\nHOST:239.255.255.250:1900\r\nCACHE-CONTROL:max-age=1800\r\n"
                       "LOCATION:%s\r\nSERVER:%s\r\nNT:%s\r\nNTS:ssdp:alive\r\nUSN:%s\r\n\r\n" % (loc, SERVER, nt, usn(nt)))
            else:
                msg = ("NOTIFY * HTTP/1.1\r\nHOST:239.255.255.250:1900\r\nNT:%s\r\nNTS:ssdp:byebye\r\nUSN:%s\r\n\r\n" % (nt, usn(nt)))
            try: sock.sendto(msg.encode(), SSDP_MCAST)
            except Exception: pass
    def responder():
        while True:
            try: data, addr = sock.recvfrom(2048)
            except Exception: break
            t = data.decode("utf-8", "replace")
            if not t.startswith("M-SEARCH"): continue
            st = (re.search(r"ST:\s*(\S+)", t, re.I) or [None, ""])[1] if re.search(r"ST:\s*(\S+)", t, re.I) else ""
            targets = NTS if st in ("ssdp:all", "") else [n for n in NTS if n == st]
            if st == "upnp:rootdevice": targets = ["upnp:rootdevice"]
            for nt in targets:
                resp = ("HTTP/1.1 200 OK\r\nCACHE-CONTROL:max-age=1800\r\nEXT:\r\nLOCATION:%s\r\n"
                        "SERVER:%s\r\nST:%s\r\nUSN:%s\r\n\r\n" % (loc, SERVER, nt, usn(nt)))
                try: sock.sendto(resp.encode(), addr)
                except Exception: pass
    threading.Thread(target=responder, daemon=True).start()

    for _ in range(3): notify(True); time.sleep(0.3)
    while True:
        time.sleep(30); notify(True)

def main():
    global PORT, NAME, MYIP, MEDIA
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8200)
    ap.add_argument("--name", default="Brainbox")
    a = ap.parse_args()
    PORT = a.port; NAME = a.name
    MEDIA = Media()
    MYIP = self_ip()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=ssdp_thread, daemon=True).start()
    print("brainbox-dms '%s' on http://%s:%d/desc.xml  (UDN %s)" % (NAME, MYIP, PORT, UDN))
    srv.serve_forever()

if __name__ == "__main__":
    main()
