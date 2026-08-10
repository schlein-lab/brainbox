#!/usr/bin/env python3
"""pn_naskbd — die am NAS steckende Funk-Tastatur ZENTRAL für alle Brainbox-Sessions/Seats nutzbar.

Liest ein evdev-Keyboard (roh, reine stdlib — kein python-evdev, kein Compiler nötig), übersetzt
Linux-Keycodes mit DEUTSCHEM Layout und schickt sie an das, was GERADE auf einen Bildschirm gecastet
wird (follow-cast) — so folgt die Tastatur dem Fernseher, an keine einzelne VM gebunden:

  * TERMINAL-Cast  -> Bytes über die read/write-getrennte Input-Lane  /ws/term?input=1&sid=<sid>
  * SEAT/GUI-Cast  -> RFB-KeyEvents (X-Keysyms) über  /ws/vnc[?cell=<cid>]  (rfbd injiziert korrekt)

Maus = separates Gerät (Relacon), hier bewusst nicht behandelt.

  pn_naskbd.py [--device /dev/input/by-id/...-event-kbd] [--portal https://192.0.2.5:8077]
               [--sid <fix-term>] [--pin-file /etc/pn-naskbd/pin]

input_event = 24 B (<qqHHi): sec, usec, type, code, value  (x86-64 little-endian).
"""
import argparse, base64, glob, json, os, socket, ssl, struct, sys, threading, time
import urllib.parse, urllib.request, http.cookiejar

EV_KEY = 0x01
IEV = struct.Struct("<qqHHi")

KEY = dict(ESC=1, K1=2, K2=3, K3=4, K4=5, K5=6, K6=7, K7=8, K8=9, K9=10, K0=11, MINUS=12, EQUAL=13,
           BACKSPACE=14, TAB=15, Q=16, W=17, E=18, R=19, T=20, Y=21, U=22, I=23, O=24, P=25,
           LEFTBRACE=26, RIGHTBRACE=27, ENTER=28, LEFTCTRL=29, A=30, S=31, D=32, F=33, G=34, H=35,
           J=36, K=37, L=38, SEMICOLON=39, APOSTROPHE=40, GRAVE=41, LEFTSHIFT=42, BACKSLASH=43,
           Z=44, X=45, C=46, V=47, B=48, N=49, M=50, COMMA=51, DOT=52, SLASH=53, RIGHTSHIFT=54,
           KPASTERISK=55, LEFTALT=56, SPACE=57, CAPSLOCK=58, F1=59, F2=60, F3=61, F4=62, F5=63,
           F6=64, F7=65, F8=66, F9=67, F10=68, HOME=102, UP=103, PAGEUP=104, LEFT=105, RIGHT=106,
           END=107, DOWN=108, PAGEDOWN=109, INSERT=110, DELETE=111, RIGHTCTRL=97, RIGHTALT=100,
           LEFTMETA=125, KP7=71, KP8=72, KP9=73, KP4=75, KP5=76, KP6=77, KP1=79, KP2=80, KP3=81,
           KP0=82, KPDOT=83, KPENTER=96, KPMINUS=74, KPPLUS=78, KPSLASH=98, F11=87, F12=88)
C2K = {v: k for k, v in KEY.items()}

DE = {
    "GRAVE": ("^", "°", ""), "K1": ("1", "!", ""), "K2": ("2", '"', "²"), "K3": ("3", "§", "³"),
    "K4": ("4", "$", ""), "K5": ("5", "%", ""), "K6": ("6", "&", ""), "K7": ("7", "/", "{"),
    "K8": ("8", "(", "["), "K9": ("9", ")", "]"), "K0": ("0", "=", "}"), "MINUS": ("ß", "?", "\\"),
    "EQUAL": ("´", "`", ""),
    "Q": ("q", "Q", "@"), "W": ("w", "W", ""), "E": ("e", "E", "€"), "R": ("r", "R", ""),
    "T": ("t", "T", ""), "Y": ("z", "Z", ""), "U": ("u", "U", ""), "I": ("i", "I", ""),
    "O": ("o", "O", ""), "P": ("p", "P", ""), "LEFTBRACE": ("ü", "Ü", ""), "RIGHTBRACE": ("+", "*", "~"),
    "A": ("a", "A", ""), "S": ("s", "S", ""), "D": ("d", "D", ""), "F": ("f", "F", ""),
    "G": ("g", "G", ""), "H": ("h", "H", ""), "J": ("j", "J", ""), "K": ("k", "K", ""),
    "L": ("l", "L", ""), "SEMICOLON": ("ö", "Ö", ""), "APOSTROPHE": ("ä", "Ä", ""),
    "BACKSLASH": ("#", "'", ""),
    "Z": ("y", "Y", ""), "X": ("x", "X", ""), "C": ("c", "C", ""), "V": ("v", "V", ""),
    "B": ("b", "B", ""), "N": ("n", "N", ""), "M": ("m", "M", "µ"),
    "COMMA": (",", ";", ""), "DOT": (".", ":", ""), "SLASH": ("-", "_", ""), "SPACE": (" ", " ", " "),
    "KP1": ("1", "1", ""), "KP2": ("2", "2", ""), "KP3": ("3", "3", ""), "KP4": ("4", "4", ""),
    "KP5": ("5", "5", ""), "KP6": ("6", "6", ""), "KP7": ("7", "7", ""), "KP8": ("8", "8", ""),
    "KP9": ("9", "9", ""), "KP0": ("0", "0", ""), "KPDOT": (",", ",", ""), "KPPLUS": ("+", "+", ""),
    "KPMINUS": ("-", "-", ""), "KPASTERISK": ("*", "*", ""), "KPSLASH": ("/", "/", ""),
}

SEQ = {"ENTER": b"\r", "KPENTER": b"\r", "TAB": b"\t", "BACKSPACE": b"\x7f", "ESC": b"\x1b",
       "UP": b"\x1b[A", "DOWN": b"\x1b[B", "RIGHT": b"\x1b[C", "LEFT": b"\x1b[D",
       "HOME": b"\x1b[H", "END": b"\x1b[F", "PAGEUP": b"\x1b[5~", "PAGEDOWN": b"\x1b[6~",
       "INSERT": b"\x1b[2~", "DELETE": b"\x1b[3~"}

XK = {"ENTER": 0xff0d, "KPENTER": 0xff8d, "TAB": 0xff09, "BACKSPACE": 0xff08, "ESC": 0xff1b,
      "UP": 0xff52, "DOWN": 0xff54, "RIGHT": 0xff53, "LEFT": 0xff51, "HOME": 0xff50, "END": 0xff57,
      "PAGEUP": 0xff55, "PAGEDOWN": 0xff56, "INSERT": 0xff63, "DELETE": 0xffff,
      "F1": 0xffbe, "F2": 0xffbf, "F3": 0xffc0, "F4": 0xffc1, "F5": 0xffc2, "F6": 0xffc3,
      "F7": 0xffc4, "F8": 0xffc5, "F9": 0xffc6, "F10": 0xffc7, "F11": 0xffc8, "F12": 0xffc9}
XK_CONTROL_L = 0xffe3

def _char_keysym(ch):
    cp = ord(ch)
    return cp if cp < 0x100 else (0x01000000 + cp)

def term_bytes(name, shift, altgr, ctrl):
    if name in SEQ:
        return SEQ[name]
    ent = DE.get(name)
    if not ent:
        return b""
    ch = ent[2] if altgr else (ent[1] if shift else ent[0])
    if not ch:
        return b""
    if ctrl and len(ch) == 1:
        o = ord(ch.lower())
        if 0x61 <= o <= 0x7a:
            return bytes([o - 0x60])
        if ch in "[\\]^_":
            return bytes([ord(ch) - 0x40])
        if ch == " ":
            return b"\x00"
        return b""
    return ch.encode("utf-8")

def gui_keysym(name, shift, altgr, ctrl):

    if name in XK:
        return XK[name], ctrl
    ent = DE.get(name)
    if not ent:
        return None, False
    if ctrl:
        base = ent[0]
        return (_char_keysym(base) if base else None), True
    ch = ent[2] if altgr else (ent[1] if shift else ent[0])
    if not ch:
        return None, False
    return _char_keysym(ch), False

def _sslctx():
    c = ssl.create_default_context(); c.check_hostname = False; c.verify_mode = ssl.CERT_NONE
    return c

def _login(portal, pin, ctx):
    cj = http.cookiejar.CookieJar()
    o = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                                    urllib.request.HTTPCookieProcessor(cj))
    o.open(portal.rstrip("/") + "/api/login", urllib.parse.urlencode({"pin": pin}).encode(), timeout=10).read()
    return o, "; ".join("%s=%s" % (c.name, c.value) for c in cj)

def _ws_open(portal, cookie, path, ctx):
    u = urllib.parse.urlparse(portal)
    host, port = u.hostname, u.port or 443
    raw = socket.create_connection((host, port), timeout=15)
    s = ctx.wrap_socket(raw, server_hostname=host) if u.scheme == "https" else raw
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\nCookie: %s\r\n\r\n"
               % (path, host, key, cookie)).encode())
    s.settimeout(15); h = b""
    while b"\r\n\r\n" not in h:
        c = s.recv(4096)
        if not c:
            raise ConnectionError("upgrade EOF")
        h += c
    if b" 101 " not in h.split(b"\r\n", 1)[0]:
        raise ConnectionError("no 101: %r" % h[:120])
    return s, bytearray(h.split(b"\r\n\r\n", 1)[1])

def _ws_send(s, data, op=0x2):
    m = os.urandom(4); hdr = bytes([0x80 | op]); n = len(data)
    hdr += bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + n.to_bytes(2, "big")
    s.sendall(hdr + m + bytes(b ^ m[i % 4] for i, b in enumerate(data)))

class TermSink:

    def __init__(self, portal, cookie, sid, ctx):
        self.s, _ = _ws_open(portal, cookie, "/ws/term?target=cockpit&input=1&sid=" + urllib.parse.quote(sid), ctx)
    def key(self, name, shift, altgr, ctrl):
        d = term_bytes(name, shift, altgr, ctrl)
        if d:
            _ws_send(self.s, d)
    def close(self):
        try: self.s.close()
        except Exception: pass

class RfbSink:

    def __init__(self, portal, cookie, cell_id, ctx):
        path = "/ws/vnc" + ("?cell=" + urllib.parse.quote(cell_id) if cell_id else "")
        self.s, self.buf = _ws_open(portal, cookie, path, ctx)
        self._handshake()
        threading.Thread(target=self._drain, daemon=True).start()

    def _wsrecv(self, n):
        while True:
            b = self.buf
            while len(b) >= 2:
                ln = b[1] & 0x7f; off = 2
                if ln == 126 and len(b) >= 4: ln = int.from_bytes(b[2:4], "big"); off = 4
                elif ln == 127 and len(b) >= 10: ln = int.from_bytes(b[2:10], "big"); off = 10
                elif ln in (126, 127): break
                if len(b) < off + ln: break
                op = b[0] & 0x0f; pl = bytes(b[off:off + ln]); del b[:off + ln]
                if op == 0x2 or op == 0x1:
                    self._rfb += pl
                elif op == 0x8:
                    raise ConnectionError("ws closed")
            if len(self._rfb) >= n:
                out = self._rfb[:n]; self._rfb = self._rfb[n:]; return out
            c = self.s.recv(65536)
            if not c:
                raise ConnectionError("EOF")
            self.buf.extend(c)

    def _handshake(self):
        self._rfb = b""
        ver = self._wsrecv(12)
        if not ver.startswith(b"RFB "):
            raise ConnectionError("bad RFB banner %r" % ver)
        _ws_send(self.s, b"RFB 003.008\n")
        n = self._wsrecv(1)[0]
        types = list(self._wsrecv(n)) if n else []
        if 1 not in types:
            raise ConnectionError("rfbd bietet kein None-auth: %r" % types)
        _ws_send(self.s, bytes([1]))
        if int.from_bytes(self._wsrecv(4), "big") != 0:
            raise ConnectionError("RFB SecurityResult != 0")
        _ws_send(self.s, bytes([1]))
        hdr = self._wsrecv(24)
        nl = int.from_bytes(hdr[20:24], "big")
        if nl:
            self._wsrecv(nl)

    def _drain(self):
        try:
            while True:
                self._wsrecv(4096)
        except Exception:
            pass

    def _keyevent(self, keysym, down):
        _ws_send(self.s, struct.pack(">BBHI", 4, 1 if down else 0, 0, keysym))

    def key(self, name, shift, altgr, ctrl):
        ks, wrap_ctrl = gui_keysym(name, shift, altgr, ctrl)
        if ks is None:
            return
        if wrap_ctrl:
            self._keyevent(XK_CONTROL_L, True)
        self._keyevent(ks, True); self._keyevent(ks, False)
        if wrap_ctrl:
            self._keyevent(XK_CONTROL_L, False)

    def close(self):
        try: self.s.close()
        except Exception: pass

def _target_from_cast(c):

    it = c.get("input_target")
    if isinstance(it, dict) and it.get("kind"):
        k = it["kind"]
        if k == "term":
            return ("term", it.get("id") or c.get("term_sid"))
        if k == "cell" and it.get("id"):
            return ("cell", it["id"])
        return ("seat", None)
    if c.get("term_sid"):
        return ("term", c["term_sid"])
    src = c.get("source") or ""
    if src.startswith("Zelle "):
        return ("cell", src[len("Zelle "):].strip())
    return ("seat", None)

def _cast_target(opener, portal, fixed_sid):

    if fixed_sid:
        return ("term", fixed_sid)
    try:
        t = json.loads(opener.open(portal.rstrip("/") + "/api/cast/targets", timeout=8).read())
    except Exception:
        return None
    best, bts = None, -1
    for c in t.get("casts") or []:
        if not c.get("alive"):
            continue
        ts = c.get("started") or 0
        if ts < bts:
            continue
        tgt = _target_from_cast(c)
        if tgt is not None:
            best, bts = tgt, ts
    return best

def _find_kbd(explicit):
    if explicit:
        return explicit
    for p in sorted(glob.glob("/dev/input/by-id/*-event-kbd")):
        return p
    raise SystemExit("kein *-event-kbd unter /dev/input/by-id/ (--device angeben)")

def _read_pin(a):
    if a.pin:
        return a.pin
    if os.environ.get("BRAINBOX_PIN"):
        return os.environ["BRAINBOX_PIN"]
    if a.pin_file and os.path.exists(a.pin_file):
        return open(a.pin_file).read().strip()
    raise SystemExit("keine PIN (--pin / $BRAINBOX_PIN / --pin-file)")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default=None)

    ap.add_argument("--portal", default=os.environ.get("BRAINBOX_PORTAL", "https://brainbox.local:8077"))
    ap.add_argument("--pin", default=None)
    ap.add_argument("--pin-file", default="/etc/pn-naskbd/pin")
    ap.add_argument("--sid", default=None, help="feste Terminal-Session statt follow-cast")
    ap.add_argument("--poll", type=float, default=3.0)
    a = ap.parse_args()

    dev = _find_kbd(a.device)
    pin = _read_pin(a)
    ctx = _sslctx()
    st = {"key": None, "sink": None, "cookie": None, "opener": None}

    def relogin():
        st["opener"], st["cookie"] = _login(a.portal, pin, ctx)

    def ensure_sink():
        tgt = _cast_target(st["opener"], a.portal, a.sid)
        if tgt is None:
            if st["sink"]:
                st["sink"].close(); st["sink"] = None; st["key"] = None
            return
        if tgt == st["key"] and st["sink"] is not None:
            return
        if st["sink"]:
            st["sink"].close(); st["sink"] = None
        kind, ident = tgt
        if kind == "term":
            st["sink"] = TermSink(a.portal, st["cookie"], ident, ctx)
        else:
            st["sink"] = RfbSink(a.portal, st["cookie"], ident if kind == "cell" else None, ctx)
        st["key"] = tgt
        sys.stderr.write("[pn_naskbd] Tastatur -> %s %s\n" % (kind, ident or "")); sys.stderr.flush()

    relogin()

    def poller():
        while True:
            try:
                ensure_sink()
            except Exception as e:
                sys.stderr.write("[pn_naskbd] sink: %r\n" % e)
                try: relogin()
                except Exception: pass
                if st["sink"]:
                    try: st["sink"].close()
                    except Exception: pass
                st["sink"] = None; st["key"] = None
            time.sleep(a.poll)
    threading.Thread(target=poller, daemon=True).start()

    mods = {"shift": 0, "ctrl": 0, "altgr": 0}
    MOD = {KEY["LEFTSHIFT"]: "shift", KEY["RIGHTSHIFT"]: "shift",
           KEY["LEFTCTRL"]: "ctrl", KEY["RIGHTCTRL"]: "ctrl", KEY["RIGHTALT"]: "altgr"}
    sys.stderr.write("[pn_naskbd] lese %s (DE) -> %s\n" % (dev, a.portal)); sys.stderr.flush()
    f = open(dev, "rb", buffering=0)
    while True:
        buf = f.read(IEV.size)
        if not buf or len(buf) < IEV.size:
            continue
        _, _, etype, code, value = IEV.unpack(buf)
        if etype != EV_KEY:
            continue
        if code in MOD:
            mods[MOD[code]] = 1 if value else 0
            continue
        if value == 0:
            continue
        name = C2K.get(code)
        if not name:
            continue
        sink = st["sink"]
        if sink is None:
            continue
        try:
            sink.key(name, mods["shift"], mods["altgr"], mods["ctrl"])
        except Exception:
            st["sink"] = None; st["key"] = None

if __name__ == "__main__":
    main()
