#!/usr/bin/env python3

import sys, re, html, json, time, socket, urllib.request, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _sitedev import addr as _dev_addr

TV = _dev_addr("tv", os.environ.get("DEV_TV"))
SVC = "urn:schemas-upnp-org:service:AVTransport:1"

def find_ctrl():
    try:
        h = json.load(urllib.request.urlopen("http://127.0.0.1:8096/health", timeout=3))
        c = h.get("dlna", {}).get("ctrl")
        if c:
            return c
    except Exception:
        pass
    if not TV:
        print("kein TV-Ziel aufgeloest: DeviceRegistry-Eintrag 'tv' (oder DEV_TV env) setzen", file=sys.stderr)
        return None
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
           "MX: 2\r\nST: %s\r\n\r\n" % SVC).encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
    s.sendto(msg, ("239.255.255.250", 1900))
    try:
        while True:
            d, a = s.recvfrom(4096)
            if a[0] != TV: continue
            m = re.search(rb"LOCATION:\s*(\S+)", d, re.I)
            if not m: continue
            x = urllib.request.urlopen(m.group(1).decode(), timeout=4).read().decode("utf-8", "replace")
            mm = re.search(r"AVTransport:1</serviceType>.*?<controlURL>(.*?)</controlURL>", x, re.S)
            if mm:
                ct = mm.group(1)
                base = re.match(r"(https?://[^/]+)", m.group(1).decode()).group(1)
                return ct if ct.startswith("http") else base + ct
    except socket.timeout:
        return None

def soap(ctrl, action, args):
    body = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            '<u:%s xmlns:u="%s">%s</u:%s></s:Body></s:Envelope>' % (action, SVC, args, action))
    req = urllib.request.Request(ctrl, data=body.encode(), headers={
        "Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": '"%s#%s"' % (SVC, action)})
    try:
        return urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "replace")
    except Exception as e:
        return "ERR:%s" % e

def push(url, mime, pn, title="Brainarbeit", live=False, poll=True):
    ctrl = find_ctrl()
    if not ctrl:
        print("NO AVTransport ctrl found"); return 2
    print("ctrl =", ctrl)

    feat = ("DLNA.ORG_PN=%s;DLNA.ORG_OP=00;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=8D500000000000000000000000000000" % pn
            if live else
            "DLNA.ORG_PN=%s;DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000" % pn)
    didl = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
            'xmlns:sec="http://www.sec.co.kr/"><item id="0" parentID="-1" restricted="1">'
            '<dc:title>%s</dc:title><upnp:class>object.item.videoItem.movie</upnp:class>'
            '<res protocolInfo="http-get:*:%s:%s">%s</res></item></DIDL-Lite>'
            % (html.escape(title), mime, feat, html.escape(url)))
    r1 = soap(ctrl, "SetAVTransportURI",
              "<InstanceID>0</InstanceID><CurrentURI>%s</CurrentURI><CurrentURIMetaData>%s</CurrentURIMetaData>"
              % (html.escape(url), html.escape(didl)))
    print("SetAVTransportURI ->", ("OK" if "SetAVTransportURIResponse" in r1 else r1[:200]))
    r2 = soap(ctrl, "Play", "<InstanceID>0</InstanceID><Speed>1</Speed>")
    print("Play              ->", ("OK" if "PlayResponse" in r2 else r2[:200]))
    if not poll:
        return 0
    playing = False
    for i in range(12):
        time.sleep(2)
        ti = soap(ctrl, "GetTransportInfo", "<InstanceID>0</InstanceID>")
        st = re.search("<CurrentTransportState>(.*?)</CurrentTransportState>", ti)
        st = st.group(1) if st else "?"
        pi = soap(ctrl, "GetPositionInfo", "<InstanceID>0</InstanceID>")
        rt = re.search("<RelTime>(.*?)</RelTime>", pi)
        rt = rt.group(1) if rt else "?"
        print("  t+%2ds state=%-16s reltime=%s" % (i * 2, st, rt))
        if st == "PLAYING":
            playing = True
            if i >= 3: break
    print("RESULT:", "PLAYING (video foregrounds)" if playing else "NOT PLAYING")
    return 0 if playing else 1

if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if x != "--live"]
    live = "--live" in sys.argv
    url, mime, pn = a[0], a[1], a[2]
    title = a[3] if len(a) > 3 else "Brainarbeit"
    sys.exit(push(url, mime, pn, title, live))
