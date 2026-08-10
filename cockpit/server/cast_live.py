#!/usr/bin/env python3

import sys, os, ssl, socket, struct, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pn_cast_display as C

def _open(ip):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    s = ctx.wrap_socket(socket.create_connection((ip, 8009), 8), server_hostname=None); s.settimeout(10)
    return s

def _recv(s):
    h = b""
    while len(h) < 4:
        c = s.recv(4 - len(h))
        if not c: return None, None
        h += c
    ln = struct.unpack(">I", h)[0]; b = b""
    while len(b) < ln:
        c = s.recv(ln - len(b))
        if not c: return None, None
        b += c
    return C._decode_castmessage(b)

def _send(s, ns, obj, dst):
    s.sendall(C._encode_castmessage("sender-brainbox-0", dst, ns, json.dumps(obj)))

def _beat(s, ns, pl):
    if ns == C.NS_BEAT:
        try:
            if json.loads(pl or "{}").get("type") == "PING":
                _send(s, C.NS_BEAT, {"type": "PONG"}, "receiver-0")
        except Exception:
            pass
        return True
    return False

def load(ip, url, content_type="video/mp4", stream_type="LIVE", title="Brainbox Live"):
    s = _open(ip)
    try:
        _send(s, C.NS_CONN, {"type": "CONNECT"}, "receiver-0")
        _send(s, C.NS_RECV, {"type": "GET_STATUS", "requestId": 1}, "receiver-0")
        transport = None; launched = False; t = time.time()
        while time.time() - t < 15:
            ns, pl = _recv(s)
            if ns is None: break
            if _beat(s, ns, pl): continue
            if ns == C.NS_RECV:
                d = json.loads(pl or "{}")
                if d.get("type") == "RECEIVER_STATUS":
                    for a in d.get("status", {}).get("applications", []) or []:
                        if a.get("appId") == C.DEFAULT_MEDIA_RECEIVER:
                            transport = a.get("transportId")
                    if transport: break
                    if not launched:
                        _send(s, C.NS_RECV, {"type": "LAUNCH", "appId": C.DEFAULT_MEDIA_RECEIVER,
                                             "requestId": 2}, "receiver-0")
                        launched = True
        if not transport:
            return 2, "no receiver transport"
        _send(s, C.NS_CONN, {"type": "CONNECT"}, transport)
        media = {"contentId": url, "contentType": content_type, "streamType": stream_type,
                 "metadata": {"metadataType": 0, "title": title}}
        _send(s, C.NS_MEDIA, {"type": "LOAD", "media": media, "autoplay": True,
                              "currentTime": 0, "requestId": 3}, transport)
        t = time.time()
        while time.time() - t < 12:
            ns, pl = _recv(s)
            if ns is None: break
            if _beat(s, ns, pl): continue
            if ns == C.NS_MEDIA:
                d = json.loads(pl or "{}"); ty = d.get("type")
                if ty == "MEDIA_STATUS" and (d.get("status") or []):
                    st = (d["status"][0] or {}).get("playerState")
                    return 0, "ok (playerState=%s)" % st
                if ty in ("LOAD_FAILED", "LOAD_CANCELLED", "INVALID_REQUEST", "INVALID_PLAYER_STATE"):
                    return 3, ty
        return 4, "no media status (timeout)"
    finally:
        try: s.close()
        except Exception: pass

if __name__ == "__main__":
    ip = sys.argv[1]; url = sys.argv[2]
    ct = sys.argv[3] if len(sys.argv) > 3 else "video/mp4"
    st = sys.argv[4] if len(sys.argv) > 4 else "LIVE"
    ti = sys.argv[5] if len(sys.argv) > 5 else "Brainbox Live"
    code, msg = load(ip, url, ct, st, ti)
    print("cast_live: rc=%d %s" % (code, msg))
    sys.exit(code)
