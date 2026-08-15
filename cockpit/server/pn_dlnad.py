#!/usr/bin/env python3

import os, re, sys, json, time, base64, socket, struct, threading, html, argparse, mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

UDN = "uuid:4d726272-6169-6e62-6f78-000000000002"
NAME = "Brainbox"
PORT = 8200
ROOT = os.environ.get("PN_DLNA_ROOT", "/data/shares/public")
SSDP_MCAST = ("239.255.255.250", 1900)

VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm", ".mpg", ".mpeg", ".ts", ".wmv"}
AUDIO_EXT = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wav", ".wma", ".opus"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}

mimetypes.add_type("video/x-matroska", ".mkv")
mimetypes.add_type("audio/flac", ".flac")

def _lan_ip():

    v = os.environ.get("PN_DLNA_ADVERTISE_IP", "").strip()
    if v:
        return v
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80)); return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()

MYIP = None

def _myip():
    global MYIP
    if MYIP is None:
        MYIP = _lan_ip()
    return MYIP

def _oid(rel):
    if rel in ("", "."):
        return "0"
    return base64.urlsafe_b64encode(rel.encode("utf-8")).decode("ascii").rstrip("=")

def _rel(oid):
    if oid in ("0", "", None):
        return ""
    pad = "=" * (-len(oid) % 4)
    try:
        return base64.urlsafe_b64decode(oid + pad).decode("utf-8", "strict")
    except Exception:
        return None

def _safe_abs(rel):

    if rel is None:
        return None
    if rel.startswith("/") or ".." in rel.split("/"):
        return None
    p = os.path.realpath(os.path.join(ROOT, rel))
    rootrp = os.path.realpath(ROOT)
    if p == rootrp or p.startswith(rootrp + os.sep):
        return p
    return None

def _kind(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    return "file"

def _upnp_class(kind):
    return {"video": "object.item.videoItem.movie",
            "audio": "object.item.audioItem.musicTrack",
            "image": "object.item.imageItem.photo"}.get(kind, "object.item")

def _mime(path, kind):
    m = mimetypes.guess_type(path)[0]
    if m:
        return m
    return {"video": "video/mpeg", "audio": "audio/mpeg", "image": "image/jpeg"}.get(kind, "application/octet-stream")

def _proto(mime):

    return ("http-get:*:%s:DLNA.ORG_OP=01;DLNA.ORG_CI=0;"
            "DLNA.ORG_FLAGS=01700000000000000000000000000000" % mime)

def _listing(rel):

    ab = _safe_abs(rel)
    dirs, files = [], []
    if not ab or not os.path.isdir(ab):
        return dirs, files
    try:
        for nm in sorted(os.listdir(ab), key=lambda x: x.lower()):
            if nm.startswith("."):
                continue
            child = os.path.join(ab, nm)
            crel = (rel + "/" + nm) if rel else nm
            if _safe_abs(crel) is None:
                continue
            if os.path.isdir(child):
                dirs.append(nm)
            elif os.path.isfile(child):
                files.append(nm)
    except OSError:
        pass
    return dirs, files

def _child_count(rel):
    d, f = _listing(rel)
    return len(d) + len(f)

def _system_update_id():
    try:
        return int(os.stat(ROOT).st_mtime) & 0x7fffffff
    except Exception:
        return 1

def _didl_container(rel, title=None):
    oid = _oid(rel)
    parent = "0" if not rel else _oid(rel.rsplit("/", 1)[0] if "/" in rel else "")
    nm = title if title is not None else (os.path.basename(rel) or NAME)
    return ('<container id="%s" parentID="%s" restricted="1" childCount="%d">'
            '<dc:title>%s</dc:title>'
            '<upnp:class>object.container.storageFolder</upnp:class></container>'
            % (oid, parent, _child_count(rel), html.escape(nm)))

def _didl_item(rel):
    ab = _safe_abs(rel)
    if not ab:
        return ""
    oid = _oid(rel)
    parent = "0" if "/" not in rel else _oid(rel.rsplit("/", 1)[0])
    kind = _kind(ab)
    mime = _mime(ab, kind)
    try:
        size = os.path.getsize(ab)
    except OSError:
        size = 0
    url = "http://%s:%d/f/%s" % (_myip(), PORT, oid)
    return ('<item id="%s" parentID="%s" restricted="1"><dc:title>%s</dc:title>'
            '<upnp:class>%s</upnp:class>'
            '<res protocolInfo="%s" size="%d">%s</res></item>'
            % (oid, parent, html.escape(os.path.basename(rel)), _upnp_class(kind),
               _proto(mime), size, url))

def wrap_didl(inner):
    return ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
            'xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/">' + inner + '</DIDL-Lite>')

def browse_children(oid, start, count):

    rel = _rel(oid)
    if rel is None:
        return "", 0, 0
    dirs, files = _listing(rel)
    entries = [("d", d) for d in dirs] + [("f", f) for f in files]
    total = len(entries)
    if count in (0, None):
        count = total
    window = entries[start:start + count]
    parts = []
    for typ, nm in window:
        crel = (rel + "/" + nm) if rel else nm
        parts.append(_didl_container(crel) if typ == "d" else _didl_item(crel))
    return "".join(parts), len(window), total

def browse_metadata(oid):
    rel = _rel(oid)
    if rel is None:
        return "", 0, 0
    if oid == "0" or rel == "":
        return _didl_container("", title=NAME), 1, 1
    ab = _safe_abs(rel)
    if ab and os.path.isdir(ab):
        return _didl_container(rel), 1, 1
    if ab and os.path.isfile(ab):
        return _didl_item(rel), 1, 1
    return "", 0, 0

def desc_xml():
    return ('<?xml version="1.0"?>\n'
      '<root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:dlna="urn:schemas-dlna-org:device-1-0" xmlns:sec="http://www.sec.co.kr/dlna">\n'
      '<specVersion><major>1</major><minor>0</minor></specVersion>\n'
      '<device>\n'
      '<dlna:X_DLNADOC>DMS-1.50</dlna:X_DLNADOC>\n'
      '<deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>\n'
      '<friendlyName>%s</friendlyName>\n'
      '<manufacturer>Brainarbeit</manufacturer>\n'
      '<modelDescription>Brainbox Media Server</modelDescription>\n'
      '<modelName>Brainbox</modelName>\n'
      '<modelNumber>1</modelNumber>\n'
      '<serialNumber>0002</serialNumber>\n'
      '<UDN>%s</UDN>\n'
      '<sec:ProductCap>smi,DCM10,getMediaInfo.sec,getCaptionInfo.sec</sec:ProductCap>\n'
      '<serviceList>\n'
      '<service><serviceType>urn:schemas-upnp-org:service:ContentDirectory:1</serviceType>'
      '<serviceId>urn:upnp-org:serviceId:ContentDirectory</serviceId>'
      '<SCPDURL>/cd/scpd.xml</SCPDURL><controlURL>/cd/control</controlURL><eventSubURL>/cd/event</eventSubURL></service>\n'
      '<service><serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType>'
      '<serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>'
      '<SCPDURL>/cm/scpd.xml</SCPDURL><controlURL>/cm/control</controlURL><eventSubURL>/cm/event</eventSubURL></service>\n'
      '</serviceList>\n'
      '</device>\n</root>\n' % (html.escape(NAME), UDN)).encode()

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

SOURCE_PI = ("http-get:*:video/mp4:*,http-get:*:video/x-matroska:*,http-get:*:image/jpeg:*,"
             "http-get:*:image/png:*,http-get:*:audio/mpeg:*,http-get:*:audio/flac:*,http-get:*:*:*")
CD_SVC = "urn:schemas-upnp-org:service:ContentDirectory:1"
CM_SVC = "urn:schemas-upnp-org:service:ConnectionManager:1"

def soap_resp(action, svc, body_inner):
    return ('<?xml version="1.0"?>\n<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            '<u:%sResponse xmlns:u="%s">%s</u:%sResponse></s:Body></s:Envelope>'
            % (action, svc, body_inner, action)).encode()

def _tag(txt, name, default=""):
    m = re.search(r"<%s>(.*?)</%s>" % (name, name), txt, re.S)
    return m.group(1) if m else default

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body, extra=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve_file(self, oid, head=False):
        rel = _rel(oid)
        ab = _safe_abs(rel)
        if not ab or not os.path.isfile(ab):
            return self._send(404, "text/plain", "not found")
        kind = _kind(ab)
        ctype = _mime(ab, kind)
        try:
            total = os.path.getsize(ab)
        except OSError:
            return self._send(404, "text/plain", "not found")
        base_h = {"Accept-Ranges": "bytes",
                  "transferMode.dlna.org": ("Streaming" if kind in ("video", "audio") else "Interactive"),
                  "contentFeatures.dlna.org": _proto(ctype).split(":", 3)[3],
                  "Cache-Control": "no-store"}
        rng = self.headers.get("Range")
        a, b = 0, total - 1
        code = 200
        if rng and not head:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                a = int(m.group(1))
                b = int(m.group(2)) if m.group(2) else total - 1
                b = min(b, total - 1)
                if a > b:
                    return self._send(416, "text/plain", "range")
                code = 206
                base_h["Content-Range"] = "bytes %d-%d/%d" % (a, b, total)
        length = b - a + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        for k, v in base_h.items():
            self.send_header(k, v)
        self.end_headers()
        if head:
            return
        try:
            with open(ab, "rb") as f:
                f.seek(a)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(262144, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError:
            pass

    def do_HEAD(self):
        p = urlparse(self.path).path
        if p.startswith("/f/"):
            return self._serve_file(unquote(p[3:]), head=True)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/desc.xml", "/"):
            return self._send(200, "text/xml; charset=utf-8", desc_xml())
        if p == "/cd/scpd.xml":
            return self._send(200, "text/xml; charset=utf-8", CD_SCPD)
        if p == "/cm/scpd.xml":
            return self._send(200, "text/xml; charset=utf-8", CM_SCPD)
        if p.startswith("/f/"):
            return self._serve_file(unquote(p[3:]))
        if p == "/health":
            d, f = _listing("")
            return self._send(200, "application/json", json.dumps(
                {"ok": True, "name": NAME, "udn": UDN, "ip": _myip(), "root": ROOT,
                 "root_dirs": len(d), "root_files": len(f)}))
        return self._send(404, "text/plain", "no route")

    def do_POST(self):
        p = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        if p == "/cd/control":
            return self._cd_control(raw)
        if p == "/cm/control":
            return self._cm_control(raw)
        return self._send(404, "text/plain", "no route")

    def _soapaction(self):
        return (self.headers.get("SOAPACTION") or "").strip('"')

    def _cd_control(self, raw):
        act = self._soapaction()
        txt = raw.decode("utf-8", "replace")
        if act.endswith("#Browse"):
            obj = _tag(txt, "ObjectID", "0") or "0"
            flag = _tag(txt, "BrowseFlag", "BrowseDirectChildren") or "BrowseDirectChildren"
            try:
                start = int(_tag(txt, "StartingIndex", "0") or "0")
            except ValueError:
                start = 0
            try:
                count = int(_tag(txt, "RequestedCount", "0") or "0")
            except ValueError:
                count = 0
            if flag == "BrowseMetadata":
                inner, num, total = browse_metadata(obj)
            else:
                inner, num, total = browse_children(obj, start, count)
            didl = wrap_didl(inner)
            body_inner = ("<Result>%s</Result><NumberReturned>%d</NumberReturned>"
                          "<TotalMatches>%d</TotalMatches><UpdateID>%d</UpdateID>"
                          % (html.escape(didl), num, total, _system_update_id()))
            return self._send(200, 'text/xml; charset="utf-8"', soap_resp("Browse", CD_SVC, body_inner))
        if act.endswith("#GetSystemUpdateID"):
            return self._send(200, 'text/xml; charset="utf-8"',
                              soap_resp("GetSystemUpdateID", CD_SVC, "<Id>%d</Id>" % _system_update_id()))
        if act.endswith("#GetSortCapabilities"):
            return self._send(200, 'text/xml; charset="utf-8"',
                              soap_resp("GetSortCapabilities", CD_SVC, "<SortCaps>dc:title</SortCaps>"))
        if act.endswith("#GetSearchCapabilities"):
            return self._send(200, 'text/xml; charset="utf-8"',
                              soap_resp("GetSearchCapabilities", CD_SVC, "<SearchCaps></SearchCaps>"))
        return self._send(200, 'text/xml; charset="utf-8"',
                          soap_resp("Browse", CD_SVC,
                                    "<Result></Result><NumberReturned>0</NumberReturned>"
                                    "<TotalMatches>0</TotalMatches><UpdateID>0</UpdateID>"))

    def _cm_control(self, raw):
        act = self._soapaction()
        if act.endswith("#GetCurrentConnectionIDs"):
            return self._send(200, 'text/xml; charset="utf-8"',
                              soap_resp("GetCurrentConnectionIDs", CM_SVC, "<ConnectionIDs>0</ConnectionIDs>"))
        return self._send(200, 'text/xml; charset="utf-8"',
                          soap_resp("GetProtocolInfo", CM_SVC,
                                    "<Source>%s</Source><Sink></Sink>" % html.escape(SOURCE_PI)))

NTS = ["upnp:rootdevice", UDN, "urn:schemas-upnp-org:device:MediaServer:1",
       "urn:schemas-upnp-org:service:ContentDirectory:1", "urn:schemas-upnp-org:service:ConnectionManager:1"]

def usn(nt):
    return UDN if nt == UDN else (UDN + "::" + nt)

SERVER = "Linux/6 UPnP/1.0 Brainbox/1.0"

def ssdp_thread():
    loc = "http://%s:%d/desc.xml" % (_myip(), PORT)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except Exception:
        pass
    sock.bind(("", 1900))
    mreq = struct.pack("4s4s", socket.inet_aton("239.255.255.250"), socket.inet_aton(_myip()))
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except Exception:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                        struct.pack("4sl", socket.inet_aton("239.255.255.250"), socket.INADDR_ANY))

    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(_myip()))
    except Exception:
        pass

    def notify(alive=True):
        for nt in NTS:
            if alive:
                msg = ("NOTIFY * HTTP/1.1\r\nHOST:239.255.255.250:1900\r\nCACHE-CONTROL:max-age=1800\r\n"
                       "LOCATION:%s\r\nSERVER:%s\r\nNT:%s\r\nNTS:ssdp:alive\r\nUSN:%s\r\n\r\n"
                       % (loc, SERVER, nt, usn(nt)))
            else:
                msg = ("NOTIFY * HTTP/1.1\r\nHOST:239.255.255.250:1900\r\nNT:%s\r\nNTS:ssdp:byebye\r\nUSN:%s\r\n\r\n"
                       % (nt, usn(nt)))
            try:
                sock.sendto(msg.encode(), SSDP_MCAST)
            except Exception:
                pass

    def responder():
        while True:
            try:
                data, addr = sock.recvfrom(2048)
            except Exception:
                break
            t = data.decode("utf-8", "replace")
            if not t.startswith("M-SEARCH"):
                continue
            m = re.search(r"ST:\s*(\S+)", t, re.I)
            st = m.group(1) if m else ""
            if st in ("ssdp:all", ""):
                targets = NTS
            elif st == "upnp:rootdevice":
                targets = ["upnp:rootdevice"]
            else:
                targets = [n for n in NTS if n == st]
            for nt in targets:
                resp = ("HTTP/1.1 200 OK\r\nCACHE-CONTROL:max-age=1800\r\nEXT:\r\nLOCATION:%s\r\n"
                        "SERVER:%s\r\nST:%s\r\nUSN:%s\r\n\r\n" % (loc, SERVER, nt, usn(nt)))
                try:
                    sock.sendto(resp.encode(), addr)
                except Exception:
                    pass

    threading.Thread(target=responder, daemon=True).start()
    for _ in range(3):
        notify(True); time.sleep(0.3)
    while True:
        time.sleep(30); notify(True)

def main():
    global PORT, NAME, MYIP, ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8200)
    ap.add_argument("--name", default="Brainbox")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--advertise-ip", default="")
    a = ap.parse_args()
    PORT = a.port
    NAME = a.name
    ROOT = os.path.realpath(a.root)
    if a.advertise_ip:
        os.environ["PN_DLNA_ADVERTISE_IP"] = a.advertise_ip
        MYIP = a.advertise_ip
    if not os.path.isdir(ROOT):
        try:
            os.makedirs(ROOT, exist_ok=True)
        except OSError:
            pass
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=ssdp_thread, daemon=True).start()
    print("pn-dlnad '%s' on http://%s:%d/desc.xml  (UDN %s, root %s)" % (NAME, _myip(), PORT, UDN, ROOT), flush=True)
    srv.serve_forever()

if __name__ == "__main__":
    main()
