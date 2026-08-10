#!/usr/bin/env python3

import sys, os, time, json, subprocess, urllib.request, re, html

TV = os.environ.get("TV_DMR", "")
if not TV:
    sys.exit("TV_DMR env var required (IP/host of the DLNA renderer TV)")
PORT = 8096
HERE = os.path.dirname(os.path.abspath(__file__))

fails = 0
def check(n, ok, d=""):
    global fails
    print(("  [PASS] " if ok else "  [FAIL] ") + n + (("  -- " + d) if d else ""))
    if not ok: fails += 1

def local(method, path, body=None):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path),
        data=(json.dumps(body).encode() if body is not None else None), method=method,
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

def tv_current_uri(ctrl):
    if not ctrl: return ""
    body = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            '<u:GetMediaInfo xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
            '<InstanceID>0</InstanceID></u:GetMediaInfo></s:Body></s:Envelope>')
    req = urllib.request.Request(ctrl, data=body.encode(),
        headers={"Content-Type": 'text/xml; charset="utf-8"',
                 "SOAPACTION": '"urn:schemas-upnp-org:service:AVTransport:1#GetMediaInfo"'})
    r = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "replace")
    m = re.search(r"<CurrentURI>(.*?)</CurrentURI>", r)
    return html.unescape(m.group(1)) if m else ""

def wait_uri_gen(ctrl, gen, timeout):

    t = time.time(); last = ""
    while time.time() - t < timeout:
        try:
            last = tv_current_uri(ctrl)
            if ("gen=%d" % gen) in last: return last
        except Exception: pass
        time.sleep(1.0)
    return last

proc = subprocess.Popen([sys.executable, os.path.join(HERE, "tv_samygo_display.py"),
                         "--tv", TV, "--port", str(PORT), "--keepalive", "9999"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:

    ctrl = None
    for _ in range(20):
        time.sleep(0.6)
        try:
            h = local("GET", "/health")
            if h.get("dlna", {}).get("ctrl"):
                ctrl = h["dlna"]["ctrl"]; break
        except Exception:
            pass
    h = local("GET", "/health")
    check("driver up + DLNA discovered", bool(ctrl) and h["dlna"]["enabled"],
          "ctrl=%s self_ip=%s" % (ctrl, h.get("dlna", {}).get("self_ip")))
    check("self_ip is a LAN address the TV can reach", (h["dlna"]["self_ip"] or "").startswith("192.168."),
          h["dlna"]["self_ip"])

    uri0 = wait_uri_gen(ctrl, 0, 25)
    h0 = local("GET", "/health")
    check("startup: idle logo pushed; TV CurrentURI = this driver's frame",
          ("/frame.jpg" in uri0) and (h0["dlna"]["self_ip"] in uri0),
          "CurrentURI=%s fetches=%s" % (uri0, h0["fetches"]))

    f_before = local("GET", "/health")["fetches"]
    local("POST", "/show", {"kind": "text", "text": "INTEGRATION OK"})
    hs = local("GET", "/health")
    uris = wait_uri_gen(ctrl, hs["gen"], 25)
    hs = local("GET", "/health")
    check("/show -> TV CurrentURI advanced to new gen", ("gen=%d" % hs["gen"]) in uris,
          "gen=%d uri=%s" % (hs["gen"], uris))
    check("/show -> TV actually pulled the new frame (fetch counter climbed)", hs["fetches"] > f_before,
          "fetches %d -> %d" % (f_before, hs["fetches"]))

    f_before = hs["fetches"]
    local("POST", "/idle", {})
    hi = local("GET", "/health")
    urii = wait_uri_gen(ctrl, hi["gen"], 25)
    hi = local("GET", "/health")
    check("/idle -> TV CurrentURI advanced again (back to logo)", ("gen=%d" % hi["gen"]) in urii and hi["state"] == "idle",
          "gen=%d state=%s uri=%s" % (hi["gen"], hi["state"], urii))
    check("/idle -> TV pulled the logo frame", hi["fetches"] > f_before, "fetches %d -> %d" % (f_before, hi["fetches"]))

    r = local("POST", "/push", {})
    check("/push re-assert returns ok", r.get("ok") is True, "r=%s" % r)

finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except Exception: proc.kill()

print("\nRESULT:", "NACHTRAG-8-DLNA-LIVE PASS" if fails == 0 else "FAIL (%d)" % fails)
sys.exit(1 if fails else 0)
