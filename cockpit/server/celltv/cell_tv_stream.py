#!/usr/bin/env python3

import os, sys, socket, struct, threading, subprocess, time, shutil, base64, zlib, json, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PNH   = os.path.expanduser("~/brainarbeit/os/pn-vmm")
BIN   = PNH + "/target/release/pn-vmm"
KERNEL= PNH + "/kernel/vmlinux-rng.bin"
INITRD= PNH + "/kernel/initramfs-cell.cpio"
BASE  = PNH + "/kernel/base-python.img"
KS0   = PNH + "/kernel/cell-win-thin-keystore.img"
HERE  = os.path.dirname(os.path.abspath(__file__))
APP   = os.path.join(HERE, "cell_live_app.py")
SEAT  = "/tmp/celltv-seat.sock"
RFBS  = "/tmp/celltv-rfb.sock"
KS    = "/tmp/celltv-keystore.img"
W, H, FPS = 384, 216, 5
CELL_ID = "1"
HTTP_PORT = 8099
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _sitedev import addr as _dev_addr
TV = _dev_addr("tv", os.environ.get("DEV_TV"))

state = {"frames": 0, "ts_bytes": 0, "clients": 0, "seatlog": "", "stop": False}

def log(m):
    print("[celltv] %s %s" % (time.strftime("%H:%M:%S"), m), flush=True)

def recvn(s, n):
    b = b""
    while len(b) < n:
        d = s.recv(n - len(b))
        if not d:
            raise EOFError("closed")
        b += d
    return b

def boot_cell():
    for p in (SEAT, RFBS):
        try: os.unlink(p)
        except OSError: pass
    shutil.copyfile(KS0, KS)
    seat_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    seat_srv.bind(SEAT); seat_srv.listen(1); seat_srv.settimeout(60)
    rfb_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    rfb_srv.bind(RFBS); rfb_srv.listen(1); rfb_srv.settimeout(60)

    def seat_thread():
        try:
            c, _ = seat_srv.accept()
        except Exception as e:
            log("seat accept fail: %r" % e); return
        c.settimeout(60); buf = b""
        while b"PN_SEAT_READY" not in buf:
            try: d = c.recv(4096)
            except Exception: return
            if not d: return
            buf += d
        src = open(APP, "rb").read().replace(b'CELL_ID = "0"', b'CELL_ID = "%s"' % str(CELL_ID).encode())
        b64 = base64.b64encode(src).decode()
        cmd = "/bin/python3 -c \"import base64;exec(base64.b64decode('%s'))\" 2>&1\n" % b64
        time.sleep(0.4); c.sendall(cmd.encode())
        log("seat ready; live app injected (%d b64 bytes)" % len(b64))
        while not state["stop"]:
            try: d = c.recv(4096)
            except Exception: break
            if not d: break
            state["seatlog"] += d.decode(errors="replace")

    threading.Thread(target=seat_thread, daemon=True).start()
    env = dict(os.environ)
    env["PN_VMM_BLK"] = "%s,%s" % (BASE, KS)
    env["PN_VMM_VSOCK"] = "21"
    env["PN_VMM_VSOCK_SEAT"] = SEAT
    env["PN_VMM_VSOCK_RFB"] = RFBS
    vm = subprocess.Popen([BIN, KERNEL, INITRD],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    log("pn-vmm booted (pid %d)" % vm.pid)
    conn, _ = rfb_srv.accept()
    conn.settimeout(60)
    return vm, conn

def rfb_handshake(conn):
    ver = recvn(conn, 12)
    assert ver.startswith(b"RFB 003."), "bad banner %r" % ver
    conn.sendall(b"RFB 003.008\n")
    nsec = recvn(conn, 1)[0]; sects = recvn(conn, nsec)
    assert 1 in sects
    conn.sendall(bytes([1]))
    assert struct.unpack(">I", recvn(conn, 4))[0] == 0
    conn.sendall(bytes([1]))
    sw, sh = struct.unpack(">HH", recvn(conn, 4))
    recvn(conn, 16)
    nl = struct.unpack(">I", recvn(conn, 4))[0]; nm = recvn(conn, nl)
    log("RFB up: %dx%d %r" % (sw, sh, nm.decode(errors="replace")))
    assert (sw, sh) == (W, H), "unexpected geometry"
    return conn

def grab_frame(conn):
    conn.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, W, H))
    mt, _p, nrect = struct.unpack(">BBH", recvn(conn, 4))
    assert mt == 0 and nrect == 1
    x, y, w, h, enc = struct.unpack(">HHHHi", recvn(conn, 12))
    assert enc == 0 and (w, h) == (W, H)
    return recvn(conn, w * h * 4)

def write_png(path, bgrx):
    raw = bytearray()
    for y in range(H):
        raw.append(0)
        for x in range(W):
            o = (y * W + x) * 4
            raw += bytes((bgrx[o+2], bgrx[o+1], bgrx[o]))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")
    open(path, "wb").write(png)

RING, RING_LOCK, RING_COND = [], threading.Lock(), None
RING_SEQ = [0]
RING_MAX_BYTES = 12 * 2**20
BACKLOG_CHUNKS = 48

REPUSH_EVENT = threading.Event()

def ts_reader(ff):
    global RING
    carry = b""
    while not state["stop"]:
        d = ff.stdout.read(65536)
        if not d:
            log("ffmpeg stdout EOF"); state["stop"] = True
            with RING_COND: RING_COND.notify_all()
            return
        carry += d
        n = len(carry) - (len(carry) % 188)
        if n:
            chunk, carry = carry[:n], carry[n:]
            with RING_COND:
                RING.append((RING_SEQ[0], chunk)); RING_SEQ[0] += 1
                state["ts_bytes"] += len(chunk)
                total = sum(len(c) for _, c in RING)
                while total > RING_MAX_BYTES:
                    total -= len(RING[0][1]); RING.pop(0)
                RING_COND.notify_all()

DLNA_MIME = "video/mpeg"
DLNA_PN   = "MPEG_TS_SD_EU_ISO"

FAKE_LEN = 2_000_000_000

class StreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _dlna_headers(self):
        self.send_header("Content-Type", DLNA_MIME)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("transferMode.dlna.org", "Streaming")
        self.send_header("contentFeatures.dlna.org",
            "DLNA.ORG_PN=" + DLNA_PN + ";DLNA.ORG_OP=01;DLNA.ORG_CI=0;"
            "DLNA.ORG_FLAGS=01700000000000000000000000000000")
        self.send_header("Connection", "close")
    def _parse_range(self):
        rng = self.headers.get("Range")
        if not rng:
            return None
        m = re.match(r"bytes=(\d+)-(\d*)", rng)
        if not m:
            return None
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else FAKE_LEN - 1
        return a, min(b, FAKE_LEN - 1)
    def _start(self, head_only=False):
        r = self._parse_range()
        if r:
            a, b = r
            self.send_response(206)
            self._dlna_headers()
            self.send_header("Content-Range", "bytes %d-%d/%d" % (a, b, FAKE_LEN))
            self.send_header("Content-Length", str(b - a + 1))
            self.end_headers()
            return r
        self.send_response(200)
        self._dlna_headers()
        self.send_header("Content-Length", str(FAKE_LEN))
        self.end_headers()
        return (0, FAKE_LEN - 1)
    def do_HEAD(self):
        if self.path.startswith("/health"):
            return self._health()
        self._start(head_only=True)
    def do_GET(self):
        if self.path.startswith("/health"):
            return self._health()
        peer = self.client_address[0]
        log("HTTP client %s GET %s (Range=%s UA=%s)" % (
            peer, self.path, self.headers.get("Range"), self.headers.get("User-Agent")))
        a, b = self._start()
        want = b - a + 1
        if want <= 2 * 188:
            with RING_COND:
                data = RING[-1][1][:want] if RING else b"\x47" * want
            try: self.wfile.write(data)
            except OSError: pass
            return
        state["clients"] += 1
        sent = 0
        try:
            with RING_COND:
                nxt = max(0, RING_SEQ[0] - BACKLOG_CHUNKS) if RING else 0
            while not state["stop"]:
                with RING_COND:
                    while RING and RING[-1][0] < nxt and not state["stop"]:
                        RING_COND.wait(5)
                    if state["stop"]: break
                    chunks = [c for sq, c in RING if sq >= nxt]
                    if chunks: nxt = RING[-1][0] + 1
                if not chunks:
                    with RING_COND: RING_COND.wait(5)
                    continue
                for c in chunks:
                    self.wfile.write(c); sent += len(c)
        except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
            pass
        state["clients"] -= 1
        if state["clients"] <= 0:
            REPUSH_EVENT.set()
        log("HTTP client %s done (%d bytes)" % (peer, sent))
    def _health(self):
        b = json.dumps({"ok": True, "frames": state["frames"], "ts_bytes": state["ts_bytes"],
                        "clients": state["clients"], "ring": len(RING)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

def main():
    global RING_COND
    import signal
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(143))
    RING_COND = threading.Condition(RING_LOCK)
    frames_test = 0
    if "--frames-test" in sys.argv:
        frames_test = int(sys.argv[sys.argv.index("--frames-test") + 1])
    do_push = "--no-push" not in sys.argv and not frames_test

    vm, conn = boot_cell()
    try:
        rfb_handshake(conn)

        if frames_test:
            prev = None
            for i in range(frames_test):
                fb = grab_frame(conn)
                diff = sum(1 for a, b in zip(fb, prev) if a != b) if prev else -1
                log("frame %d: %d bytes, diff-bytes-vs-prev=%s" % (i, len(fb), diff))
                write_png("/tmp/celltv-frame%d.png" % i, fb)
                if prev is not None and diff == 0:
                    log("FAIL: frames identical — not live"); return 1
                prev = fb
                time.sleep(1.2)
            log("FRAMES-TEST PASS: cell screen is ALIVE (frames differ; PNGs in /tmp)")
            return 0

        ff = subprocess.Popen(
            ["ffmpeg", "-loglevel", "warning", "-nostats",
             "-f", "rawvideo", "-pix_fmt", "bgra", "-s", "%dx%d" % (W, H), "-r", str(FPS), "-i", "pipe:0",
             "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
             "-vf", "scale=720:576:flags=neighbor,setdar=16/9",
             "-r", "25", "-c:v", "mpeg2video", "-b:v", "1200k", "-maxrate", "1500k",
             "-bufsize", "1000k", "-g", "12",
             "-c:a", "mp2", "-b:a", "192k", "-ar", "48000", "-ac", "2",
             "-f", "mpegts", "-muxrate", "2000k", "pipe:1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=ts_reader, args=(ff,), daemon=True).start()

        latest = {"fb": None}
        def grabber():
            period = 1.0 / (FPS * 2)
            while not state["stop"]:
                t = time.monotonic()
                try:
                    latest["fb"] = grab_frame(conn)
                    state["frames"] += 1
                except Exception as e:
                    log("grabber ended: %r" % e); state["stop"] = True; return
                dt = period - (time.monotonic() - t)
                if dt > 0: time.sleep(dt)
        threading.Thread(target=grabber, daemon=True).start()
        def feeder():
            period = 1.0 / FPS
            nxt = time.monotonic()
            while not state["stop"]:
                nxt += period
                fb = latest["fb"]
                if fb:
                    try: ff.stdin.write(fb)
                    except Exception as e:
                        log("feeder ended: %r" % e); state["stop"] = True; return
                dt = nxt - time.monotonic()
                if dt > 0: time.sleep(dt)
        threading.Thread(target=feeder, daemon=True).start()

        srv = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), StreamHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        log("TS broadcast on :%d/live.ts" % HTTP_PORT)

        t0 = time.time()
        while state["ts_bytes"] < 262144 and time.time() - t0 < 30:
            time.sleep(0.5)
        log("ring primed: %d TS bytes, %d frames grabbed" % (state["ts_bytes"], state["frames"]))
        if state["ts_bytes"] == 0:
            log("FAIL: no TS output"); return 1

        if do_push:
            if not TV:
                raise SystemExit("kein TV-Ziel aufgeloest: DeviceRegistry-Eintrag 'tv' (oder DEV_TV env) setzen")
            sys.path.insert(0, HERE)
            from tv_push import push
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((TV, 7676)); self_ip = s.getsockname()[0]; s.close()
            url = "http://%s:%d/live.ts" % (self_ip, HTTP_PORT)
            log("pushing %s to TV" % url)
            rc = push(url, "video/mpeg", "MPEG_TS_SD_EU_ISO", "Brainbox Cell Live", live=False)
            log("push rc=%d" % rc)
            if rc != 0:
                log("not PLAYING yet — retrying push once")
                rc = push(url, "video/mpeg", "MPEG_TS_SD_EU_ISO", "Brainbox Cell Live", live=False)
                log("retry rc=%d — stream stays up either way (debug via curl :%d)" % (rc, HTTP_PORT))

            def repush_watchdog():

                while not state["stop"]:
                    REPUSH_EVENT.wait(6); REPUSH_EVENT.clear()
                    if state["stop"]:
                        continue
                    time.sleep(0.25)
                    if state["clients"] > 0:
                        continue
                    log("watchdog: no client — re-pushing to TV")
                    try:
                        r = push(url, "video/mpeg", "MPEG_TS_SD_EU_ISO", "Brainbox Cell Live", live=False, poll=False)
                        log("watchdog re-push fired (rc=%d)" % r)
                    except Exception as e:
                        log("watchdog push error: %r" % e)
            threading.Thread(target=repush_watchdog, daemon=True).start()

        while not state["stop"]:
            time.sleep(30)
            log("heartbeat: frames=%d ts=%.1fMB clients=%d" %
                (state["frames"], state["ts_bytes"] / 2**20, state["clients"]))
        return 0
    finally:
        state["stop"] = True
        try:
            if RING_COND:
                with RING_COND: RING_COND.notify_all()
        except Exception: pass
        try: vm.kill()
        except Exception: pass
        try: os.unlink(KS)
        except OSError: pass
        tail = state["seatlog"][-400:]
        if tail: log("seatlog tail: %s" % tail.replace("\n", " | "))

if __name__ == "__main__":
    sys.exit(main() or 0)
