#!/usr/bin/env python3

import os, sys, time, socket, struct, threading, subprocess, shutil, signal, tempfile

RFB_HOST = os.environ.get("SEATCAST_RFB_HOST", "127.0.0.1")
RFB_PORT = int(os.environ.get("SEATCAST_RFB_PORT", "5900"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from _sitedev import resolve as _res
DEVICE   = os.environ.get("SEATCAST_DEVICE") or _res("cast-fernseher", os.environ.get("PN_CAST_DEVICE", ""))
NAME     = os.environ.get("SEATCAST_NAME", "Brainbox-Bildschirm")
HTTP_PORT = int(os.environ.get("SEATCAST_HTTP", "8121"))
FPS      = int(os.environ.get("SEATCAST_FPS", "12"))
DO_CAST  = "SEATCAST_NOCAST" not in os.environ
TERM_SID = os.environ.get("SEATCAST_TERM_SID")

def _tmpdir():
    for v in ("TMPDIR", "TEMP", "TMP"):
        d = os.environ.get(v)
        if d:
            return d
    return "/tmp"

HLS_DIR  = os.path.join(_tmpdir(), "seatcast_%d" % HTTP_PORT)

state = {"stop": False, "w": 0, "h": 0, "fb": None, "lock": threading.Lock(),
         "frames": 0, "ts_bytes": 0}

def log(m): print("[seatcast] %s %s" % (time.strftime("%H:%M:%S"), m), flush=True)

STATUS_FILE = os.path.join(_tmpdir(), "seatcast-cast-status-%d.json" % HTTP_PORT)

def _cast_status(st, err=None):
    try:
        import json
        tmp = STATUS_FILE + ".tmp"
        open(tmp, "w").write(json.dumps({"cast": st, "cast_err": err}))
        os.replace(tmp, STATUS_FILE)
    except Exception:
        pass

def recvn(s, n):
    b = bytearray()
    while len(b) < n:
        d = s.recv(n - len(b))
        if not d:
            raise ConnectionError("rfb closed")
        b += d
    return bytes(b)

def rfb_connect():
    s = socket.create_connection((RFB_HOST, RFB_PORT), timeout=10)
    ver = recvn(s, 12)
    if not ver.startswith(b"RFB 003."):
        raise RuntimeError("bad RFB banner %r" % ver)
    s.sendall(b"RFB 003.008\n")
    n = recvn(s, 1)[0]
    types = list(recvn(s, n))
    if 1 not in types:
        raise RuntimeError("rfbd offers no None-auth (sectypes=%r)" % types)
    s.sendall(bytes([1]))
    if struct.unpack(">I", recvn(s, 4))[0] != 0:
        raise RuntimeError("rfb auth failed")
    s.sendall(bytes([1]))
    hdr = recvn(s, 24)
    w, h = struct.unpack(">HH", hdr[:4])
    nl = struct.unpack(">I", hdr[20:24])[0]
    recvn(s, nl)

    pf = struct.pack(">BBBBHHHBBB", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0) + b"\x00\x00\x00"
    s.sendall(struct.pack(">Bxxx", 0) + pf)
    s.sendall(struct.pack(">BxH", 2, 1) + struct.pack(">i", 0))
    with state["lock"]:
        state["w"], state["h"] = w, h
        state["fb"] = bytearray(w * h * 4)
    log("RFB connected: %dx%d (our phantom VNC)" % (w, h))
    s.settimeout(30)
    return s

def fb_request(s, incremental):
    s.sendall(struct.pack(">BBHHHH", 3, 1 if incremental else 0, 0, 0, state["w"], state["h"]))

def _blit_raw(x, y, w, h, data):
    fb = state["fb"]; W = state["w"]
    stride = w * 4
    for row in range(h):
        di = row * stride
        fi = ((y + row) * W + x) * 4
        fb[fi:fi + stride] = data[di:di + stride]

def _blit_copyrect(x, y, w, h, sx, sy):
    fb = state["fb"]; W = state["w"]
    rows = range(h) if sy >= y else reversed(range(h))
    for row in rows:
        si = ((sy + row) * W + sx) * 4
        di = ((y + row) * W + x) * 4
        fb[di:di + w * 4] = fb[si:si + w * 4]

def rfb_loop():
    while not state["stop"]:
        try:
            s = rfb_connect()
            fb_request(s, 0)
            while not state["stop"]:
                try:
                    mt = recvn(s, 1)[0]
                except socket.timeout:
                    fb_request(s, 1)
                    continue
                if mt == 0:
                    recvn(s, 1)
                    nrect = struct.unpack(">H", recvn(s, 2))[0]
                    for _ in range(nrect):
                        x, y, w, h, enc = struct.unpack(">HHHHi", recvn(s, 12))
                        if enc == 0:
                            data = recvn(s, w * h * 4)
                            with state["lock"]: _blit_raw(x, y, w, h, data)
                        elif enc == 1:
                            sx, sy = struct.unpack(">HH", recvn(s, 4))
                            with state["lock"]: _blit_copyrect(x, y, w, h, sx, sy)
                        else:
                            raise RuntimeError("unsupported RFB encoding %d" % enc)
                    state["frames"] += 1
                    fb_request(s, 1)
                elif mt == 2:
                    pass
                elif mt == 3:
                    recvn(s, 3); tl = struct.unpack(">I", recvn(s, 4))[0]; recvn(s, tl)
                else:
                    raise RuntimeError("unexpected server msg %d" % mt)
        except Exception as e:
            if state["stop"]: return
            log("rfb loop: %r — reconnect in 2s" % e)
            time.sleep(2)

def start_ffmpeg(w, h):
    return subprocess.Popen(
        ["ffmpeg", "-loglevel", "warning", "-nostats",
         "-f", "rawvideo", "-pix_fmt", "bgra", "-s", "%dx%d" % (w, h), "-r", str(FPS), "-i", "pipe:0",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-map", "0:v:0", "-map", "1:a:0",
         "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
                "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p", "-r", str(FPS),
         "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-level", "4.1",
         "-b:v", "3000k", "-maxrate", "3500k", "-bufsize", "5000k",
         "-force_key_frames", "expr:gte(t,n_forced*2)",
         "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2",
         "-f", "hls", "-hls_time", "2", "-hls_list_size", "6",
         "-hls_flags", "delete_segments+omit_endlist+temp_file", "-hls_segment_type", "mpegts",
         "-hls_segment_filename", HLS_DIR + "/seg%05d.ts", HLS_DIR + "/live.m3u8"],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

def emit_loop(ff):
    period = 1.0 / FPS
    while not state["stop"]:
        t0 = time.time()
        with state["lock"]:
            buf = bytes(state["fb"]) if state["fb"] is not None else None
        if buf:
            try:
                ff.stdin.write(buf); ff.stdin.flush()
            except Exception as e:
                log("ffmpeg stdin closed: %r" % e); return
        dt = period - (time.time() - t0)
        if dt > 0: time.sleep(dt)

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
MIME = {".m3u8": "application/vnd.apple.mpegurl", ".ts": "video/mp2t"}

class HlsHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type, Accept-Encoding")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range")
    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.send_header("Content-Length", "0"); self.end_headers()
    def do_GET(self):
        name = os.path.basename(self.path.split("?")[0])
        ext = os.path.splitext(name)[1]
        path = os.path.join(HLS_DIR, name)
        if ext not in MIME or not os.path.isfile(path):
            self.send_response(404); self._cors(); self.send_header("Content-Length", "0"); self.end_headers(); return
        try:
            data = open(path, "rb").read()
        except OSError:
            self.send_response(404); self._cors(); self.send_header("Content-Length", "0"); self.end_headers(); return
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            try:
                a, b = rng[6:].split("-")
                a = int(a); b = int(b) if b else len(data) - 1
                b = min(b, len(data) - 1)
                chunk = data[a:b + 1]
                self.send_response(206); self._cors()
                self.send_header("Content-Type", MIME[ext])
                self.send_header("Content-Range", "bytes %d-%d/%d" % (a, b, len(data)))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                try: self.wfile.write(chunk)
                except OSError: pass
                return
            except Exception:
                pass
        self.send_response(200); self._cors()
        self.send_header("Content-Type", MIME[ext])
        if ext == ".m3u8":
            self.send_header("Cache-Control", "no-cache")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try: self.wfile.write(data)
        except OSError: pass

def cast_worker(url):
    import pychromecast
    if not DEVICE:
        raise SystemExit("kein Cast-Ziel aufgeloest: DeviceRegistry 'cast-fernseher' oder SEATCAST_DEVICE/PN_CAST_DEVICE setzen")
    while not state["stop"]:
        try:
            cast = pychromecast.get_chromecast_from_host((DEVICE, 8009, None, None, None))
            cast.wait(timeout=15)
            mc = cast.media_controller
            def load():
                mc.play_media(url, "application/x-mpegurl", stream_type="LIVE", title="Brainbox " + NAME)
                mc.block_until_active(timeout=15)
            load()

            _settled = None
            for _ in range(24):
                if state["stop"]:
                    return
                _ps = (mc.status.player_state if mc.status else None)
                if _ps in ("PLAYING", "BUFFERING"):
                    _settled = _ps
                    break
                time.sleep(0.5)
            if _settled:
                log("Wiedergabe laeuft: %s (%s)" % (_settled, url)); _cast_status("playing")
            else:
                log("Receiver bleibt IDLE - spielt nicht"); _cast_status("stalled", "Receiver bleibt IDLE")
            idle_since = None
            while not state["stop"]:
                time.sleep(5)
                st = mc.status.player_state if mc.status else "UNKNOWN"
                if st in ("PLAYING", "BUFFERING", "PAUSED"):
                    idle_since = None
                else:
                    idle_since = idle_since or time.time()
                    if time.time() - idle_since > 12:
                        log("idle (state=%s) -> re-LOAD" % st); load(); idle_since = None
        except Exception as e:
            if state["stop"]: return
            log("cast worker: %r — retry 15s" % e)
            _cast_status("error", repr(e))
            for _ in range(15):
                if state["stop"]: return
                time.sleep(1)

def main():
    signal.signal(signal.SIGTERM, lambda *a: state.update(stop=True) or sys.exit(0))
    shutil.rmtree(HLS_DIR, ignore_errors=True); os.makedirs(HLS_DIR, exist_ok=True)
    if TERM_SID:
        from pn_termsource import term_loop
        threading.Thread(target=term_loop, args=(state, TERM_SID, log), daemon=True).start()
    else:
        threading.Thread(target=rfb_loop, daemon=True).start()
    t0 = time.time()
    while state["w"] == 0 and time.time() - t0 < 30:
        time.sleep(0.2)
    if state["w"] == 0:
        log("source produced no frame within 30s"); return 1
    ff = start_ffmpeg(state["w"], state["h"])
    threading.Thread(target=emit_loop, args=(ff,), daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), HlsHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("HLS on :%d (dir %s), source %s" % (HTTP_PORT, HLS_DIR,
        ("TERM sid=%s" % TERM_SID) if TERM_SID else ("RFB %s:%d" % (RFB_HOST, RFB_PORT))))
    t0 = time.time()
    while time.time() - t0 < 120:
        try:
            if open(HLS_DIR + "/live.m3u8").read().count(".ts") >= 2:
                break
        except OSError:
            pass
        if state["stop"]: return 0
        time.sleep(0.5)
    if not DEVICE:
        raise SystemExit("kein Cast-Ziel aufgeloest: DeviceRegistry 'cast-fernseher' oder SEATCAST_DEVICE/PN_CAST_DEVICE setzen")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect((DEVICE, 8009))
    self_ip = s.getsockname()[0]; s.close()
    url = "http://%s:%d/live.m3u8" % (self_ip, HTTP_PORT)
    log("stream URL: %s" % url)
    if DO_CAST:
        threading.Thread(target=cast_worker, args=(url,), daemon=True).start()
    beat = 0
    while not state["stop"]:
        time.sleep(10)
        beat += 10
        if beat % 120 == 0:
            log("heartbeat: frames=%d %dx%d" % (state["frames"], state["w"], state["h"]))
    return 0

if __name__ == "__main__":
    sys.exit(main() or 0)
