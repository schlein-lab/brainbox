#!/usr/bin/env python3

import os, sys, time, socket, threading, subprocess, json
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cell_tv_stream as engine
from tv_push import push
engine.DLNA_PN = "AVC_TS_MP_HD_AAC_MULT5_ISO"

INGEST_PORT = 9400
HTTP_PORT   = 8095
TV          = engine.TV

def log(m):
    print("[wintv] %s %s" % (time.strftime("%H:%M:%S"), m), flush=True)

def spawn_ffmpeg():

    return subprocess.Popen(
        ["ffmpeg", "-loglevel", "warning", "-nostats",
         "-i", "tcp://0.0.0.0:%d?listen=1" % INGEST_PORT,
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p", "-r", "15",
         "-c:v", "libx264", "-preset", "ultrafast", "-threads", "3",
         "-profile:v", "high", "-level", "4.0",
         "-b:v", "6000k", "-maxrate", "7000k", "-bufsize", "8000k",
         "-g", "30", "-x264-params", "scenecut=0",
         "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
         "-shortest",
         "-f", "mpegts", "-muxrate", "8500k", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

def reader_loop():

    while not engine.state["stop"]:
        ff = spawn_ffmpeg()
        log("ffmpeg ingest up (pid %d), waiting for the Windows sender on :%d" % (ff.pid, INGEST_PORT))
        carry = b""
        while not engine.state["stop"]:
            d = ff.stdout.read(65536)
            if not d:
                break
            carry += d
            n = len(carry) - (len(carry) % 188)
            if n:
                chunk, carry = carry[:n], carry[n:]
                with engine.RING_COND:
                    engine.RING.append((engine.RING_SEQ[0], chunk)); engine.RING_SEQ[0] += 1
                    engine.state["ts_bytes"] += len(chunk)
                    total = sum(len(c) for _, c in engine.RING)
                    while total > engine.RING_MAX_BYTES:
                        total -= len(engine.RING[0][1]); engine.RING.pop(0)
                    engine.RING_COND.notify_all()
        try: ff.kill()
        except Exception: pass
        if not engine.state["stop"]:
            log("sender gone — respawning ingest")
            time.sleep(1)

def main():
    import signal
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(143))

    if not os.environ.get("PN_JOB_ID") and "--no-govern" not in sys.argv:
        _pn = os.path.expanduser("~/.local/bin/pn")
        _sock = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid()),
                             "pnd.sock")
        if os.path.exists(_pn) and os.path.exists(_sock):
            log("re-exec unter pnd-Governance (pn run --tag media.ingest; --no-govern erzwingt direkt)")
            os.execvp(_pn, [_pn, "run", "--mem", "700", "--cpu-quota", "300",
                            "--latency", "realtime", "--timeout", "43200",
                            "--tag", "media.ingest", "--",
                            sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
        log("WARNUNG: Governor (pnd) nicht erreichbar — läuft ausnahmsweise DIREKT (ungoverned)")
    do_push = "--no-push" not in sys.argv
    engine.RING_COND = threading.Condition(engine.RING_LOCK)
    threading.Thread(target=reader_loop, daemon=True).start()

    srv = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), engine.StreamHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("TS broadcast on :%d/live.ts" % HTTP_PORT)

    if "--selftest" in sys.argv:

        def sender():
            time.sleep(2)
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-re",
                 "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=15",
                 "-t", "30", "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                 "-pix_fmt", "yuv420p", "-b:v", "4000k", "-g", "20",
                 "-f", "mpegts", "tcp://127.0.0.1:%d" % INGEST_PORT])
        threading.Thread(target=sender, daemon=True).start()

    t0 = time.time()
    while engine.state["ts_bytes"] < 262144:
        time.sleep(0.5)
        if engine.state["stop"]:
            return 1
        if time.time() - t0 > 3600:
            log("no sender within 1h — exiting"); return 1
    log("ingest live: %d TS bytes" % engine.state["ts_bytes"])

    if do_push:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((TV, 7676)); self_ip = s.getsockname()[0]; s.close()
        url = "http://%s:%d/live.ts" % (self_ip, HTTP_PORT)
        log("pushing %s to Samsung" % url)
        rc = push(url, "video/mpeg", "AVC_TS_MP_HD_AAC_MULT5_ISO", "Windows Bildschirm", live=False)
        log("push rc=%d" % rc)
        def watchdog():

            while not engine.state["stop"]:
                engine.REPUSH_EVENT.wait(6); engine.REPUSH_EVENT.clear()
                if engine.state["stop"]:
                    continue
                time.sleep(0.25)
                if engine.state["clients"] > 0:
                    continue
                if engine.state["ts_bytes"] < 262144:
                    continue
                log("watchdog: no client — re-pushing")
                try:
                    push(url, "video/mpeg", "AVC_TS_MP_HD_AAC_MULT5_ISO", "Windows Bildschirm",
                         live=False, poll=False)
                except Exception as e:
                    log("watchdog push error: %r" % e)
        threading.Thread(target=watchdog, daemon=True).start()

    while not engine.state["stop"]:
        time.sleep(30)
        log("heartbeat: ts=%.1fMB clients=%d" %
            (engine.state["ts_bytes"] / 2**20, engine.state["clients"]))
    return 0

if __name__ == "__main__":
    sys.exit(main() or 0)
