#!/usr/bin/env python3

import sys, os, io, time, json, threading, http.client
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from http.server import ThreadingHTTPServer
import tv_samygo_display as TV
from PIL import Image

PORT = 8097
srv = ThreadingHTTPServer(("127.0.0.1", PORT), TV.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)

fails = 0
def check(n, ok, d=""):
    global fails
    print(("  [PASS] " if ok else "  [FAIL] ") + n + (("  -- " + d) if d else ""))
    if not ok: fails += 1

def req(method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=60)
    c.request(method, path, body=json.dumps(body) if body is not None else None,
              headers={"Content-Type": "application/json"} if body is not None else {})
    r = c.getresponse(); data = r.read(); ct = r.getheader("Content-Type"); c.close()
    return r.status, ct, data

def is_baseline_jpeg(b):

    return b[:2] == b"\xff\xd8" and (b"\xff\xc0" in b) and (b"\xff\xc2" not in b)

def dims(b):
    im = Image.open(io.BytesIO(b)); return im.size

s, ct, d = req("GET", "/health"); j = json.loads(d)
check("health: idle + 1920x1080", s == 200 and j.get("state") == "idle" and j.get("wh") == [1920, 1080], "j=%s" % j)

s, ct, d = req("GET", "/frame.jpg")
check("idle /frame.jpg = baseline JPEG @1920x1080", s == 200 and ct == "image/jpeg" and is_baseline_jpeg(d) and dims(d) == (1920, 1080),
      "ct=%s baseline=%s dims=%s" % (ct, is_baseline_jpeg(d), dims(d) if d[:2]==b'\xff\xd8' else '?'))
logo_bytes = d

s, ct, d = req("POST", "/show", {"kind": "text", "text": "HALLO TV"}); j = json.loads(d)
s2, _, f2 = req("GET", "/frame.jpg")
check("/show text -> new baseline 1920x1080 frame", j.get("ok") and is_baseline_jpeg(f2) and dims(f2) == (1920, 1080) and f2 != logo_bytes)

s, ct, d = req("POST", "/show", {"kind": "image", "value": TV.LOGO}); j = json.loads(d)
check("/show image(file) -> ok", j.get("ok") is True, "j=%s" % j)

s, ct, d = req("POST", "/idle", {}); j = json.loads(d)
s2, _, f3 = req("GET", "/frame.jpg")
check("/idle -> state idle + logo frame restored", j.get("state") == "idle" and f3 == logo_bytes)

def read_one_mjpeg():
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10); c.request("GET", "/stream")
    r = c.getresponse()
    ct = r.getheader("Content-Type")
    buf = b""
    while b"\r\n\r\n" not in buf: buf += r.read(64)
    hdr, rest = buf.split(b"\r\n\r\n", 1)

    rest += r.read(512); c.close()
    return ct, (b"--frame" in hdr or b"image/jpeg" in hdr), rest[:2] == b"\xff\xd8"
ct, ok_hdr, ok_jpeg = read_one_mjpeg()
check("/stream is multipart/x-mixed-replace with a JPEG frame", "multipart/x-mixed-replace" in (ct or "") and ok_jpeg)

s, ct, d = req("GET", "/")
check("receiver HTML served", s == 200 and b"<img" in d and b"/frame.jpg" in d)
s, ct, d = req("GET", "/widgetlist.xml")
check("widgetlist.xml (develop App Sync) served", s == 200 and b"<widget" in d and b"/widget/Brainarbeit.zip" in d)

print("\nRESULT:", "NACHTRAG-8-BOXSIDE PASS" if fails == 0 else "FAIL (%d)" % fails)
srv.shutdown()
sys.exit(1 if fails else 0)
