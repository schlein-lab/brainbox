#!/usr/bin/env python3

import os, sys, time, json, shutil, socket, threading, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cell_tv_stream as engine

from _sitedev import resolve as _res
def _load_cast_devices():
    import json
    try:
        spec = json.loads(os.environ.get("PN_CAST_DEVICES", "") or "[]")
    except Exception:
        spec = []
    return [(_res(d.get("role", ""), d.get("ip", "")), d.get("label", d.get("role", ""))) for d in spec]
CAST_DEVICES = _load_cast_devices()
BASE_PORT = 8110
FPS  = engine.FPS
W, H = engine.W, engine.H

state = {"stop": False, "frames": 0}

def log(m):
    print("[casttv] %s %s" % (time.strftime("%H:%M:%S"), m), flush=True)

MIME = {".m3u8": "application/vnd.apple.mpegurl", ".ts": "video/mp2t"}

def make_handler(hls_dir):
    class HlsHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *a): pass
        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept-Encoding, Range")
            self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range")
        def do_OPTIONS(self):
            self.send_response(200); self._cors()
            self.send_header("Content-Length", "0"); self.end_headers()
        def do_HEAD(self): self._serve(head=True)
        def do_GET(self):
            if self.path.startswith("/health"):
                b = json.dumps({"ok": True, "frames": state["frames"],
                                "playlist": os.path.exists(hls_dir + "/live.m3u8")}).encode()
                self.send_response(200); self._cors()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b))); self.end_headers()
                self.wfile.write(b); return
            self._serve()
        def _serve(self, head=False):
            name = os.path.basename(self.path.split("?")[0])
            path = os.path.join(hls_dir, name)
            ext = os.path.splitext(name)[1]
            if ext not in MIME or not os.path.isfile(path):
                self.send_response(404); self._cors()
                self.send_header("Content-Length", "0"); self.end_headers(); return
            data = open(path, "rb").read()
            rng = self.headers.get("Range")
            a, b = 0, len(data) - 1
            code = 200
            if rng:
                import re as _re
                m = _re.match(r"bytes=(\d+)-(\d*)", rng)
                if m:
                    a = int(m.group(1)); b = int(m.group(2)) if m.group(2) else len(data) - 1
                    b = min(b, len(data) - 1); code = 206
            self.send_response(code); self._cors()
            self.send_header("Content-Type", MIME[ext])
            self.send_header("Accept-Ranges", "bytes")
            if ext == ".m3u8":
                self.send_header("Cache-Control", "no-cache")
            if code == 206:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (a, b, len(data)))
            self.send_header("Content-Length", str(b - a + 1))
            self.end_headers()
            if not head:
                try: self.wfile.write(data[a:b + 1])
                except OSError: pass
    return HlsHandler

def cast_worker(ip, name, url):

    import pychromecast
    while not state["stop"]:
        try:
            cast = pychromecast.get_chromecast_from_host((ip, 8009, None, None, None))
            cast.wait(timeout=15)
            mc = cast.media_controller
            def load():
                mc.play_media(url, "application/x-mpegurl", stream_type="LIVE",
                              title="Brainbox " + name)
                mc.block_until_active(timeout=15)
            load()
            log("[%s] LOAD %s; state=%s" % (name, url, mc.status.player_state))
            idle_since, last_beat = None, 0
            while not state["stop"]:
                time.sleep(5)
                st = mc.status.player_state if mc.status else "UNKNOWN"
                if st in ("PLAYING", "BUFFERING", "PAUSED"):
                    idle_since = None
                else:
                    idle_since = idle_since or time.time()
                    if time.time() - idle_since > 10:
                        log("[%s] state=%s -> re-LOAD" % (name, st))
                        load(); idle_since = None
                if time.time() - last_beat > 300:
                    log("[%s] heartbeat: %s" % (name, st)); last_beat = time.time()
        except Exception as e:
            log("[%s] worker error: %r — retry in 60s" % (name, e))
            for _ in range(60):
                if state["stop"]: return
                time.sleep(1)

def start_pipeline(idx, ip, name, do_cast, cleanup):
    cell_id = str(2 + idx)
    engine.SEAT = "/tmp/castcell%s-seat.sock" % cell_id
    engine.RFBS = "/tmp/castcell%s-rfb.sock" % cell_id
    engine.KS   = "/tmp/castcell%s-keystore.img" % cell_id
    engine.CELL_ID = cell_id
    ks = engine.KS
    hls_dir = "/tmp/casthls%s" % cell_id
    port = BASE_PORT + idx
    vm, conn = engine.boot_cell()
    cleanup.append((vm, ks))
    engine.rfb_handshake(conn)
    shutil.rmtree(hls_dir, ignore_errors=True)
    os.makedirs(hls_dir, exist_ok=True)

    ff = subprocess.Popen(
        ["ffmpeg", "-loglevel", "warning", "-nostats",
         "-f", "rawvideo", "-pix_fmt", "bgra", "-s", "%dx%d" % (W, H), "-r", str(FPS), "-i", "pipe:0",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-vf", "scale=1280:720:flags=neighbor", "-r", "15",
         "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2", "-profile:v", "high", "-level", "4.1",
         "-pix_fmt", "yuv420p", "-b:v", "2500k", "-maxrate", "3000k", "-bufsize", "4000k",
         "-force_key_frames", "expr:gte(t,n_forced*3)", "-x264-params", "scenecut=0",
         "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2",
         "-f", "hls", "-hls_time", "3", "-hls_list_size", "6",
         "-hls_flags", "delete_segments+omit_endlist+temp_file",
         "-hls_segment_type", "mpegts",
         "-hls_segment_filename", hls_dir + "/seg%05d.ts", hls_dir + "/live.m3u8"],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    latest = {"fb": None}
    def grabber():
        period = 1.0 / (FPS * 2)
        while not state["stop"]:
            t = time.monotonic()
            try:
                latest["fb"] = engine.grab_frame(conn); state["frames"] += 1
            except Exception as e:
                log("[cell %s] grabber ended: %r" % (cell_id, e)); return
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
                    log("[cell %s] feeder ended: %r" % (cell_id, e)); return
            dt = nxt - time.monotonic()
            if dt > 0: time.sleep(dt)
    threading.Thread(target=feeder, daemon=True).start()

    srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(hls_dir))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    t0 = time.time()
    while time.time() - t0 < 60:
        try:
            if open(hls_dir + "/live.m3u8").read().count(".ts") >= 2:
                break
        except OSError:
            pass
        time.sleep(0.5)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((ip, 8009)); self_ip = s.getsockname()[0]; s.close()
    url = "http://%s:%d/live.m3u8" % (self_ip, port)
    log("[%s] CELL %s ready -> %s" % (name, cell_id, url))
    if do_cast:
        threading.Thread(target=cast_worker, args=(ip, name, url), daemon=True).start()

def main():
    import signal
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(143))
    do_cast = "--no-cast" not in sys.argv

    devices = [(ip, name) for ip, name in CAST_DEVICES if ip]
    for _ip, name in CAST_DEVICES:
        if not _ip:
            log("Ziel '%s' unaufgeloest (DeviceRegistry/env) — uebersprungen" % name)
    if "--device" in sys.argv:
        ip = sys.argv[sys.argv.index("--device") + 1]
        devices = [(ip, ip)]

    cleanup = []
    try:
        for idx, (ip, name) in enumerate(devices):
            start_pipeline(idx, ip, name, do_cast, cleanup)
        while not state["stop"]:
            time.sleep(60)
            log("heartbeat: %d cells, frames=%d" % (len(cleanup), state["frames"]))
        return 0
    finally:
        state["stop"] = True
        engine.state["stop"] = True
        for vm, ks in cleanup:
            try: vm.kill()
            except Exception: pass
            try: os.unlink(ks)
            except OSError: pass
        log("cleaned up %d cells" % len(cleanup))

if __name__ == "__main__":
    sys.exit(main() or 0)
