#!/usr/bin/env python3

import socket, subprocess, os, sys, time, threading

HOST_CID = 2
RFB_PORT = 5900
PHANTOM = "/bin/phantom"
PH_LOG = "/tmp/ph.log"
SIZE = os.environ.get("PN_PH_SIZE", "800x600")

def log(m):
    sys.stderr.write("[cell-ph] %s\n" % m); sys.stderr.flush()

def sh(*a):
    try: return subprocess.call(list(a))
    except Exception as e: log("cmd %r failed: %s" % (a, e)); return -1

def bring_up_lo():

    if sh("ifconfig", "lo", "127.0.0.1", "netmask", "255.0.0.0", "up") == 0:
        return "ifconfig"
    if sh("ip", "link", "set", "lo", "up") == 0:
        return "ip"
    return "FAILED"

def main():
    lo = bring_up_lo(); log("loopback up via %s" % lo)

    try:
        os.makedirs("/dev/shm", exist_ok=True); sh("mount", "-t", "tmpfs", "none", "/dev/shm")
    except Exception: pass
    os.makedirs("/tmp/ph", exist_ok=True)

    env = dict(os.environ)
    env.update({
        "PHANTOM_HEADLESS": "1",
        "PHANTOM_HEADLESS_SIZE": SIZE,
        "PHANTOM_VNC": "127.0.0.1:%d" % RFB_PORT,
        "PHANTOM_NO_INPUT": "1",
        "XDG_RUNTIME_DIR": "/tmp/ph",
    })
    logf = open(PH_LOG, "wb")
    if not os.path.exists(PHANTOM):
        log("FATAL: %s missing in rootfs" % PHANTOM); return 3
    try:
        m = os.stat(PHANTOM).st_mode
        if not (m & 0o111):
            os.chmod(PHANTOM, 0o755); log("set +x on %s" % PHANTOM)
    except Exception as e:
        log("chmod check: %s" % e)
    ph = subprocess.Popen([PHANTOM, "--compositor", "cell-0"], stdout=logf, stderr=logf, env=env)
    log("phantom pid=%d (headless %s, rfbd 127.0.0.1:%d)" % (ph.pid, SIZE, RFB_PORT))

    bound = False; waited = 0.0
    for _ in range(80):
        time.sleep(0.25); waited += 0.25
        try: t = open(PH_LOG, "rb").read().decode("latin1")
        except Exception: t = ""
        if "rfbd: VNC/RFB on" in t:
            bound = True; break
        if ph.poll() is not None:
            log("phantom EXITED rc=%s" % ph.returncode); break
    head = ""
    try: head = open(PH_LOG, "rb").read()[:1400].decode("latin1")
    except Exception: pass
    log("rfbd bound=%s after %.2fs" % (bound, waited))
    log("--- phantom log head ---\n%s\n--- end ---" % head)
    if not bound:
        log("ABORT: rfbd never bound"); return 2

    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    for _ in range(40):
        try: tcp.connect(("127.0.0.1", RFB_PORT)); break
        except OSError: time.sleep(0.15)
    else:
        log("ABORT: could not connect rfbd TCP"); return 4
    log("connected to rfbd TCP 127.0.0.1:%d" % RFB_PORT)
    vs = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    vs.connect((HOST_CID, RFB_PORT))
    log("connected vsock lane %d:%d" % (HOST_CID, RFB_PORT))

    def pump(src, dst, tag):
        n = 0
        try:
            while True:
                d = src.recv(65536)
                if not d: break
                dst.sendall(d); n += len(d)
        except Exception as e:
            log("pump %s ended: %s" % (tag, e))
        finally:
            try: dst.shutdown(socket.SHUT_WR)
            except Exception: pass
            log("pump %s closed after %d bytes" % (tag, n))

    def run_ctl(*a):
        env2 = dict(os.environ); env2["XDG_RUNTIME_DIR"] = "/tmp/ph"
        try:
            r = subprocess.run([PHANTOM] + list(a), env=env2, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=10)
            log("CTL %s -> %s" % (" ".join(a), r.stdout.decode("latin1").strip()))
        except Exception as e:
            log("CTL %s FAILED: %s" % (" ".join(a), e))

    def director():
        time.sleep(2.0); run_ctl("dock", "show")
        time.sleep(2.5); run_ctl("room", "type", "hallo aus der isolierten pn-vmm-Zelle")
        time.sleep(2.5); run_ctl("dock", "hide")

    threading.Thread(target=director, daemon=True).start()
    t1 = threading.Thread(target=pump, args=(tcp, vs, "rfbd->host"), daemon=True)
    t2 = threading.Thread(target=pump, args=(vs, tcp, "host->rfbd"), daemon=True)
    t1.start(); t2.start()
    log("RELAY_PUMPING")
    t1.join(); t2.join()
    log("relay done")

    try:
        tail = open(PH_LOG, "rb").read()[-1800:].decode("latin1")
        log("=== PHANTOM_LOG_TAIL_BEGIN ===\n%s\n=== PHANTOM_LOG_TAIL_END ===" % tail)
    except Exception as e:
        log("log tail read failed: %s" % e)
    try: ph.terminate()
    except Exception: pass
    return 0

if __name__ == "__main__":
    sys.exit(main())
