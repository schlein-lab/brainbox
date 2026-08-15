#!/usr/bin/env python3

import base64, json, os, socket, ssl, threading, time
import urllib.parse, urllib.request, http.cookiejar

import pyte
from PIL import Image, ImageDraw, ImageFont

PORTAL = os.environ.get("SEATCAST_PORTAL", "https://127.0.0.1:8077")
CFG = os.path.expanduser("~/.config/brainbox-portal/config.json")
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
try:
    CANVAS_W, CANVAS_H = (int(x) for x in os.environ.get("PN_CAST_RES", "1280x720").lower().split("x"))
except Exception:
    CANVAS_W, CANVAS_H = 1280, 720

try:
    PAD = int(os.environ.get("PN_TV_SAFE_PAD", str(max(24, CANVAS_H // 25))))
except Exception:
    PAD = max(24, CANVAS_H // 25)

BG = (0x0b, 0x0e, 0x15)
FG = (0xe8, 0xec, 0xf5)
ANSI = {
    "black": (0x21, 0x26, 0x2e), "red": (0xff, 0x6b, 0x6b), "green": (0x98, 0xc3, 0x79),
    "brown": (0xe5, 0xc0, 0x7b), "yellow": (0xe5, 0xc0, 0x7b), "blue": (0x61, 0xaf, 0xef),
    "magenta": (0xc6, 0x78, 0xdd), "cyan": (0x56, 0xb6, 0xc2), "white": (0xe8, 0xec, 0xf5),
    "brightblack": (0x5c, 0x63, 0x70), "brightred": (0xff, 0x8f, 0x8f), "brightgreen": (0xb5, 0xe8, 0x90),
    "brightbrown": (0xf0, 0xd5, 0x91), "brightyellow": (0xf0, 0xd5, 0x91), "brightblue": (0x8c, 0xc7, 0xff),
    "brightmagenta": (0xdd, 0xa0, 0xe8), "brightcyan": (0x7d, 0xd6, 0xe0), "brightwhite": (0xff, 0xff, 0xff),
    "default": None,
}

def _color(c, dflt):
    if not c or c == "default":
        return dflt
    v = ANSI.get(c)
    if v:
        return v
    try:
        n = int(str(c), 16)
        return ((n >> 16) & 255, (n >> 8) & 255, n & 255)
    except Exception:
        return dflt

class TermRenderer:

    def __init__(self, rows=36, cols=120):
        self.lock = threading.Lock()
        self.rows, self.cols = rows, cols
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.changed = True
        self._fit()

    def _fit(self):

        for fs in range(64, 7, -1):
            f = ImageFont.truetype(FONT_PATH, fs)
            adv = f.getlength("M")
            asc, desc = f.getmetrics()
            lh = asc + desc
            if self.cols * adv <= CANVAS_W - 2 * PAD and self.rows * lh <= CANVAS_H - 2 * PAD:
                break

        MIN_TV_FS = 14
        self.crop_r0 = 0
        self.crop_c0 = 0
        self.vis_rows = self.rows
        self.vis_cols = self.cols
        if fs < MIN_TV_FS:
            fs = MIN_TV_FS
            f = ImageFont.truetype(FONT_PATH, fs)
            adv = f.getlength("M")
            asc, desc = f.getmetrics()
            lh = asc + desc
            self.vis_cols = max(20, min(self.cols, int((CANVAS_W - 2 * PAD) // adv)))
            self.vis_rows = max(6, min(self.rows, int((CANVAS_H - 2 * PAD) // lh)))
            self.crop_r0 = max(0, self.rows - self.vis_rows)
            self.crop_c0 = 0
        self.font = f
        try:
            self.font_b = ImageFont.truetype(FONT_BOLD, fs)
        except Exception:
            self.font_b = f
        self.cw, self.ch = adv, lh
        self.ox = int((CANVAS_W - self.vis_cols * adv) / 2)
        self.oy = int((CANVAS_H - self.vis_rows * lh) / 2)

    def feed(self, data):
        with self.lock:
            try:
                self.stream.feed(data)
            except Exception:
                pass
            self.changed = True

    def resize(self, rows, cols):
        rows = max(4, min(120, int(rows))); cols = max(20, min(400, int(cols)))
        with self.lock:
            if (rows, cols) == (self.rows, self.cols):
                return
            self.rows, self.cols = rows, cols
            try:
                self.screen.resize(rows, cols)
            except Exception:
                self.screen = pyte.Screen(cols, rows)
                self.stream = pyte.ByteStream(self.screen)
            self._fit()
            self.changed = True

    def render_bgra(self):

        with self.lock:
            if not self.changed:
                return None
            self.changed = False
            buf = self.screen.buffer
            rows, cols = self.rows, self.cols
            cur = self.screen.cursor
            cur_xy = None if cur.hidden else (cur.x, cur.y)

            grid = []
            for y in range(rows):
                line = buf[y]
                grid.append([line[x] for x in range(cols)])
        img = Image.new("RGB", (CANVAS_W, CANVAS_H), BG)
        d = ImageDraw.Draw(img)
        cw, ch, ox, oy = self.cw, self.ch, self.ox, self.oy

        r0 = getattr(self, "crop_r0", 0)
        c0 = getattr(self, "crop_c0", 0)
        vr = getattr(self, "vis_rows", rows)
        vc = getattr(self, "vis_cols", cols)
        if r0 or c0 or vr != rows or vc != cols:
            grid = [row[c0:c0 + vc] for row in grid[r0:r0 + vr]]
            cols = vc
            if cur_xy is not None:
                cur_xy = (cur_xy[0] - c0, cur_xy[1] - r0)
                if not (0 <= cur_xy[0] < vc and 0 <= cur_xy[1] < vr):
                    cur_xy = None
        for y, line in enumerate(grid):
            x = 0
            while x < cols:
                c = line[x]
                fg = _color(c.fg, FG); bg = _color(c.bg, None)
                if c.reverse:
                    fg, bg = (bg or BG), fg
                bold = bool(c.bold)

                x2 = x + 1
                while x2 < cols:
                    n = line[x2]
                    nfg = _color(n.fg, FG); nbg = _color(n.bg, None)
                    if n.reverse:
                        nfg, nbg = (nbg or BG), nfg
                    if (nfg, nbg, bool(n.bold)) != (fg, bg, bold):
                        break
                    x2 += 1
                px, py = ox + x * cw, oy + y * ch
                if bg is not None and bg != BG:
                    d.rectangle([px, py, ox + x2 * cw - 1, py + ch - 1], fill=bg)
                text = "".join((ch2.data or " ") for ch2 in line[x:x2])
                if text.strip():
                    d.text((px, py), text, fill=fg, font=(self.font_b if bold else self.font))
                x = x2
            if cur_xy and cur_xy[1] == y and cur_xy[0] < cols:
                cx, cy0 = ox + cur_xy[0] * cw, oy + y * ch
                d.rectangle([cx, cy0, cx + cw - 1, cy0 + ch - 1], fill=FG)
                cc = grid[y][cur_xy[0]].data or " "
                if cc.strip():
                    d.text((cx, cy0), cc, fill=BG, font=self.font)
        return img.convert("RGBA").tobytes("raw", "BGRA"), CANVAS_W, CANVAS_H

def _login(opener):

    tf = os.environ.get("SEATCAST_TOKEN_FILE", "")
    if tf:
        try:
            tok = open(tf).read().strip()
            if tok:
                return "pp_session=" + tok
        except Exception:
            pass
    pin = (json.load(open(CFG)) or {}).get("pin", "")
    opener.open(PORTAL + "/api/login", urllib.parse.urlencode({"pin": pin}).encode(), timeout=10).read()
    return None

def _ws_watch(sid, cookie, sslctx, host, port):
    raw = socket.create_connection((host, port), timeout=15)
    s = sslctx.wrap_socket(raw, server_hostname=host)
    key = base64.b64encode(os.urandom(16)).decode()
    path = "/ws/term?target=cockpit&watch=1&sid=" + urllib.parse.quote(sid)
    s.sendall(("GET %s HTTP/1.1\r\nHost: %s:%s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\nCookie: %s\r\n\r\n"
               % (path, host, port, key, cookie)).encode())
    hdr = b""
    s.settimeout(15)
    while b"\r\n\r\n" not in hdr:
        c = s.recv(4096)
        if not c:
            raise ConnectionError("upgrade EOF")
        hdr += c
    if b" 101 " not in hdr.split(b"\r\n", 1)[0]:
        raise ConnectionError("no 101: %r" % hdr[:120])
    return s, bytearray(hdr.split(b"\r\n\r\n", 1)[1])

def _frames(s, buf):

    s.settimeout(45)
    while True:
        while True:
            if len(buf) >= 2:
                ln = buf[1] & 0x7f; off = 2
                if ln == 126 and len(buf) >= 4:
                    ln = int.from_bytes(buf[2:4], "big"); off = 4
                elif ln == 127 and len(buf) >= 10:
                    ln = int.from_bytes(buf[2:10], "big"); off = 10
                elif ln in (126, 127):
                    break
                if len(buf) >= off + ln:
                    op, pl = buf[0] & 0x0f, bytes(buf[off:off + ln])
                    del buf[:off + ln]
                    yield op, pl
                    continue
            break
        c = s.recv(65536)
        if not c:
            raise ConnectionError("EOF")
        buf.extend(c)

def term_loop(state, sid, log):

    ren = TermRenderer()
    u = urllib.parse.urlparse(PORTAL)
    host, port = u.hostname or "127.0.0.1", u.port or 8077
    sslctx = ssl.create_default_context()
    sslctx.check_hostname = False; sslctx.verify_mode = ssl.CERT_NONE

    def pump():
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=sslctx),
                                             urllib.request.HTTPCookieProcessor(cj))
        while not state["stop"]:
            try:
                tokck = _login(opener)
                cookie = tokck or "; ".join("%s=%s" % (c.name, c.value) for c in cj)
                s, buf = _ws_watch(sid, cookie, sslctx, host, port)
                log("watch lane connected (sid=%s)" % sid)
                for op, pl in _frames(s, buf):
                    if state["stop"]:
                        return
                    if op == 0x2 and pl:
                        ren.feed(pl)
                    elif op == 0x1:
                        try:
                            j = json.loads(pl)
                            if j.get("t") == "wsz":
                                ren.resize(j.get("rows", 36), j.get("cols", 120))
                        except Exception:
                            pass
                    elif op == 0x8:
                        raise ConnectionError("server closed")
            except Exception as e:
                if state["stop"]:
                    return
                msg = repr(e)
                delay = 60 if any(x in msg for x in ("401", "403", "429")) else 3

                log("watch lane: %s — reconnect in %ds" % (msg, delay))
                for _ in range(delay):
                    if state["stop"]:
                        return
                    time.sleep(1)

    threading.Thread(target=pump, daemon=True, name="term-watch").start()
    while not state["stop"]:
        out = ren.render_bgra()
        if out:
            fb, w, h = out
            with state["lock"]:
                state["fb"] = bytearray(fb); state["w"] = w; state["h"] = h
            state["frames"] += 1
        time.sleep(0.33)
