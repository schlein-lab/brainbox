#!/usr/bin/env python3

import os, sys, time, json, shutil, socket, threading, subprocess, tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def arg(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default

INGEST_PORT = int(arg("--port", "9401"))
HTTP_PORT   = int(arg("--http", "8120"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _sitedev import resolve as _res
DEVICE      = arg("--device", _res("cast-fernseher", os.environ.get("PN_CAST_DEVICE", "")))
NAME        = arg("--name", "VM-Bildschirm")
DO_CAST     = "--no-cast" not in sys.argv

def _tmpdir():
    for v in ("TMPDIR", "TEMP", "TMP"):
        d = os.environ.get(v)
        if d:
            return d
    return "/tmp"

HLS_DIR     = os.path.join(_tmpdir(), "screenhls_%d" % HTTP_PORT)

state = {"stop": False, "ts_bytes": 0, "connected": False}

def log(m):
    print("[screencast] %s %s" % (time.strftime("%H:%M:%S"), m), flush=True)

MIME = {".m3u8": "application/vnd.apple.mpegurl", ".ts": "video/mp2t"}

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
            b = json.dumps({"ok": True, "ts_bytes": state["ts_bytes"],
                            "connected": state["connected"],
                            "playlist": os.path.exists(HLS_DIR + "/live.m3u8")}).encode()
            self.send_response(200); self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers()
            self.wfile.write(b); return
        self._serve()
    def _serve(self, head=False):
        name = os.path.basename(self.path.split("?")[0])
        path = os.path.join(HLS_DIR, name)
        ext = os.path.splitext(name)[1]
        if ext not in MIME or not os.path.isfile(path):
            self.send_response(404); self._cors()
            self.send_header("Content-Length", "0"); self.end_headers(); return
        data = open(path, "rb").read()
        rng = self.headers.get("Range")
        a, b = 0, len(data) - 1; code = 200
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

def start_ffmpeg():
    return subprocess.Popen(
        ["ffmpeg", "-loglevel", "warning", "-nostats",
         "-fflags", "+genpts+igndts", "-probesize", "2M", "-analyzeduration", "2M",
         "-f", "h264", "-i", "pipe:0",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-map", "0:v:0", "-map", "1:a:0",
         "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
                "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p", "-r", "15",
         "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-level", "4.1",
         "-b:v", "3500k", "-maxrate", "4000k", "-bufsize", "5000k",
         "-force_key_frames", "expr:gte(t,n_forced*2)",
         "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2",
         "-f", "hls", "-hls_time", "2", "-hls_list_size", "6",
         "-hls_flags", "delete_segments+omit_endlist+temp_file", "-hls_segment_type", "mpegts",
         "-hls_segment_filename", HLS_DIR + "/seg%05d.ts", HLS_DIR + "/live.m3u8"],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

def find_sps(buf):

    i = 0
    n = len(buf)
    while i < n - 4:
        if buf[i] == 0 and buf[i + 1] == 0:
            if buf[i + 2] == 1 and (buf[i + 3] & 0x1F) == 7:
                return i
            if buf[i + 2] == 0 and buf[i + 3] == 1 and i + 4 < n and (buf[i + 4] & 0x1F) == 7:
                return i
        i += 1
    return -1

def ffmpeg_loop():

    ff = {"p": None}
    presync = bytearray()
    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lsock.bind(("0.0.0.0", INGEST_PORT)); lsock.listen(1); lsock.settimeout(1.0)
    log("listening for the desktop sender on tcp:%d (ffmpeg starts at first SPS)" % INGEST_PORT)
    while not state["stop"]:
        try:
            conn, addr = lsock.accept()
        except socket.timeout:
            continue
        log("sender connected from %s" % (addr[0],)); state["connected"] = True
        try:
            while not state["stop"]:
                d = conn.recv(65536)
                if not d: break
                state["ts_bytes"] += len(d)
                if ff["p"] is None:
                    presync += d
                    idx = find_sps(presync)
                    if idx < 0:
                        if len(presync) > 4 * 2**20:
                            idx = 0
                        else:
                            continue
                    ff["p"] = start_ffmpeg()
                    log("ffmpeg up (pid %d), synced at SPS offset %d" % (ff["p"].pid, idx))
                    ff["p"].stdin.write(bytes(presync[idx:])); ff["p"].stdin.flush()
                    presync = bytearray()
                    continue
                ff["p"].stdin.write(d); ff["p"].stdin.flush()
        except OSError as e:
            log("sender pump ended: %r" % e)
        finally:
            try: conn.close()
            except OSError: pass
        state["connected"] = False
        log("sender disconnected — ffmpeg stays warm, awaiting reconnect")

def cast_worker(url):
    import pychromecast
    if not DEVICE:
        raise SystemExit("kein Cast-Ziel aufgeloest: DeviceRegistry 'cast-fernseher' oder --device/PN_CAST_DEVICE setzen")
    while not state["stop"]:
        try:
            cast = pychromecast.get_chromecast_from_host((DEVICE, 8009, None, None, None))
            cast.wait(timeout=15)
            mc = cast.media_controller
            def load():
                mc.play_media(url, "application/x-mpegurl", stream_type="LIVE", title="Brainbox " + NAME)
                mc.block_until_active(timeout=15)
            load()
            log("[%s] LOAD %s; state=%s" % (NAME, url, mc.status.player_state))
            idle_since, last_beat = None, 0
            while not state["stop"]:
                time.sleep(5)
                st = mc.status.player_state if mc.status else "UNKNOWN"
                if st in ("PLAYING", "BUFFERING", "PAUSED"): idle_since = None
                else:
                    idle_since = idle_since or time.time()
                    if time.time() - idle_since > 10:
                        log("[%s] state=%s -> re-LOAD" % (NAME, st)); load(); idle_since = None
                if time.time() - last_beat > 120:
                    log("[%s] heartbeat: %s ts=%.1fMB" % (NAME, st, state["ts_bytes"] / 2**20))
                    last_beat = time.time()
        except Exception as e:
            log("[%s] worker error: %r — retry 30s" % (NAME, e))
            for _ in range(30):
                if state["stop"]: return
                time.sleep(1)

def main():
    import signal
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(143))
    shutil.rmtree(HLS_DIR, ignore_errors=True); os.makedirs(HLS_DIR, exist_ok=True)
    threading.Thread(target=ffmpeg_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), HlsHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("HLS server on :%d (dir %s)" % (HTTP_PORT, HLS_DIR))

    t0 = time.time()
    while time.time() - t0 < 300:
        try:
            if open(HLS_DIR + "/live.m3u8").read().count(".ts") >= 2: break
        except OSError: pass
        if state["stop"]: return 1
        time.sleep(0.5)
    else:
        log("no playlist within 5min (sender never connected?)");
    try:
        log("playlist ready:\n%s" % open(HLS_DIR + "/live.m3u8").read().strip())
    except OSError:
        pass

    if not DEVICE:
        raise SystemExit("kein Cast-Ziel aufgeloest: DeviceRegistry 'cast-fernseher' oder --device/PN_CAST_DEVICE setzen")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((DEVICE, 8009)); self_ip = s.getsockname()[0]; s.close()
    url = "http://%s:%d/live.m3u8" % (self_ip, HTTP_PORT)
    log("stream url: %s" % url)
    if DO_CAST:
        threading.Thread(target=cast_worker, args=(url,), daemon=True).start()
    while not state["stop"]:
        time.sleep(30)
    return 0

if __name__ == "__main__":
    sys.exit(main() or 0)
