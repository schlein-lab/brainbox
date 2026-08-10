#!/usr/bin/env python3

import argparse, json, os, shutil, signal, socket, struct, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
try:
    import pn_ram_admission as _ADMIT
except Exception:
    _ADMIT = None
PNH = os.path.expanduser("~/brainarbeit/os/pn-vmm")
BIN = PNH + "/target/release/pn-vmm"
KERNEL = PNH + "/kernel/vmlinux-rng.bin"
INITRD = PNH + "/kernel/initramfs-cell.cpio"
BASE = PNH + "/kernel/base-x11.img"
KS0 = PNH + "/kernel/cell-win-thin-keystore.img"
NET_BROKER = PNH + "/pn_cell_net_broker.py"
DATA = os.path.expanduser("~/.local/share/brainbox-portal")
REG = os.path.join(DATA, "vmcells.json")
W, H = 800, 600

state = {"stop": False, "seatlog": "", "viewers": 0, "need_full": True}
latest = {"fb": bytearray(W * H * 4), "seq": 0}
fb_lock = threading.Lock()
cell_wlock = threading.Lock()

def log(m):
    print("[gui-x11] %s" % m, flush=True)

def recvn(s, n):
    b = b""
    while len(b) < n:
        d = s.recv(n - len(b))
        if not d:
            raise EOFError("closed")
        b += d
    return b

def _seat_command(url, w, h):

    return (
        "export DISPLAY=:7 HOME=/tmp/ffhome XDG_RUNTIME_DIR=/tmp/xdg FONTCONFIG_PATH=/etc/fonts "
        "XKB_CONFIG_ROOT=/usr/share/X11/xkb; mkdir -p -m0700 /tmp/xdg /tmp/.X11-unix /tmp/ffhome; "
        "ip link set lo up 2>/dev/null || busybox ip link set lo up 2>/dev/null || true; "

        "[ -f /tmp/mux.pid ] && kill -0 \"$(cat /tmp/mux.pid)\" 2>/dev/null || { "
        "PN_PROXY_TRANSPORT=vsock:2:9200 PN_PROXY_PORT=8888 /bin/python3 /opt/pn/incell_mux_proxy.py "
        ">/tmp/mux.log 2>&1 & echo $! > /tmp/mux.pid; }; sleep 2; "
        "[ -S /tmp/.X11-unix/X7 ] || { Xvfb :7 -screen 0 %dx%dx24 -ac -nolisten tcp -noreset "
        ">/tmp/xvfb.log 2>&1 & echo $! > /tmp/xvfb.pid; }; "
        "for i in $(seq 1 60); do [ -S /tmp/.X11-unix/X7 ] && break; sleep 0.2; done; "

        "[ -f /tmp/x11vnc.pid ] || { x11vnc -display :7 -rfbport 5901 -localhost -auth /dev/null -nopw "
        "-forever -shared -noxdamage -xkb -add_keysyms -repeat -quiet >/tmp/x11vnc.log 2>&1 & "
        "echo $! > /tmp/x11vnc.pid; }; "
        "bash /opt/pn/gui-up.sh; "

        "export GDK_BACKEND=x11 MOZ_ENABLE_WAYLAND=0 LIBGL_ALWAYS_SOFTWARE=1 "
        "MOZ_ACCELERATED=0 MOZ_WEBRENDER=0 MOZ_X11_EGL=0; mkdir -p /tmp/ffprofile; "

        "{ echo 'user_pref(\"browser.aboutwelcome.enabled\", false);'; "
        "echo 'user_pref(\"datareporting.policy.dataSubmissionPolicyBypassNotification\", true);'; "
        "echo 'user_pref(\"datareporting.policy.dataSubmissionEnabled\", false);'; "
        "echo 'user_pref(\"browser.startup.homepage_override.mstone\", \"ignore\");'; "
        "echo 'user_pref(\"startup.homepage_welcome_url\", \"\");'; "
        "echo 'user_pref(\"startup.homepage_welcome_url.additional\", \"\");'; "
        "echo 'user_pref(\"browser.shell.checkDefaultBrowser\", false);'; "
        "echo 'user_pref(\"trailhead.firstrun.didSeeAboutWelcome\", true);'; "
        "echo 'user_pref(\"browser.cache.disk.enable\", false);'; "

        "echo 'user_pref(\"network.proxy.type\", 1);'; "
        "echo 'user_pref(\"network.proxy.http\", \"127.0.0.1\");'; "
        "echo 'user_pref(\"network.proxy.http_port\", 8888);'; "
        "echo 'user_pref(\"network.proxy.ssl\", \"127.0.0.1\");'; "
        "echo 'user_pref(\"network.proxy.ssl_port\", 8888);'; "
        "echo 'user_pref(\"network.proxy.allow_hijacking_localhost\", false);'; "
        "echo 'user_pref(\"toolkit.telemetry.reportingpolicy.firstRun\", false);'; } > /tmp/ffprofile/user.js; "
        "echo GUI_UP_DONE; "
        "( firefox --no-remote --profile /tmp/ffprofile '%s' "
        "|| /usr/lib/firefox/firefox --no-remote --profile /tmp/ffprofile '%s' "
        "|| /usr/lib/firefox/firefox-bin --no-remote --profile /tmp/ffprofile '%s' "
        "|| echo FIREFOX_FAILED ) 2>&1 & "

        "( sleep 5; while :; do /bin/python3 -c \""
        "import ctypes;x=ctypes.CDLL('libX11.so.6');x.XOpenDisplay.restype=ctypes.c_void_p;"
        "d=ctypes.c_void_p(x.XOpenDisplay(b':7'));x.XSetInputFocus(d,1,2,0);x.XFlush(d)\" "
        "2>/dev/null; sleep 3; done ) & "
        "echo SEAT_CMD_DONE\n" % (w, h, url, url, url)
    )

def boot_cell(seat_p, rfbs_p, ks_p, url, w, h, mem_mb, net_p=None, pol_p=None):
    for p in (seat_p, rfbs_p, net_p):
        if not p:
            continue
        try:
            os.unlink(p)
        except OSError:
            pass
    shutil.copyfile(KS0, ks_p)

    net_broker = None
    if net_p:
        nenv = dict(os.environ)
        nenv["PN_POLICY_FILE"] = pol_p or ""
        net_broker = subprocess.Popen(["/usr/bin/python3", NET_BROKER, "--unix-mux", net_p],
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=open("/tmp/guix11-net.log", "ab", buffering=0), env=nenv)
        t0 = time.time()
        while not os.path.exists(net_p) and time.time() - t0 < 10:
            time.sleep(0.1)
    seat_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    seat_srv.bind(seat_p); seat_srv.listen(1); seat_srv.settimeout(90)
    rfb_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    rfb_srv.bind(rfbs_p); rfb_srv.listen(1); rfb_srv.settimeout(90)

    def seat_thread():
        try:
            c, _ = seat_srv.accept()
        except Exception as e:
            log("seat accept fail: %r" % e); return
        c.settimeout(90); buf = b""
        while b"PN_SEAT_READY" not in buf:
            try:
                d = c.recv(4096)
            except Exception:
                return
            if not d:
                return
            buf += d
        time.sleep(0.4)
        c.sendall(_seat_command(url, w, h).encode())
        log("seat ready; gui-up + firefox command sent (%dx%d)" % (w, h))
        while not state["stop"]:
            try:
                d = c.recv(4096)
            except Exception:
                break
            if not d:
                break
            state["seatlog"] += d.decode(errors="replace")
            if len(state["seatlog"]) > 20000:
                state["seatlog"] = state["seatlog"][-8000:]

    threading.Thread(target=seat_thread, daemon=True).start()
    env = dict(os.environ)
    env["PN_VMM_BLK"] = "%s,%s" % (BASE, ks_p)
    env["PN_VMM_VSOCK"] = "23"
    env["PN_VMM_VSOCK_SEAT"] = seat_p
    env["PN_VMM_VSOCK_RFB"] = rfbs_p
    if net_p:
        env["PN_VMM_VSOCK_NET"] = net_p

    env["PN_VMM_MEM_MB"] = str(mem_mb)
    env["CELL_MEM_MAX"] = "%dM" % (mem_mb + 1536)
    vm = subprocess.Popen([BIN, KERNEL, INITRD],
                          stdout=subprocess.DEVNULL,
                          stderr=open("/tmp/guix11-vm.log", "ab", buffering=0), env=env)
    log("pn-vmm booted (pid %d), warte auf x11vnc-RFB-Lane ..." % vm.pid)
    conn, _ = rfb_srv.accept()
    conn.settimeout(60)
    return vm, conn, net_broker

def rfb_client_handshake(conn):
    ver = recvn(conn, 12)
    assert ver.startswith(b"RFB 003."), "bad banner %r" % ver
    conn.sendall(b"RFB 003.008\n")
    nsec = recvn(conn, 1)[0]; sects = recvn(conn, nsec)
    assert 1 in sects, "cell RFB kein None-auth: %r" % list(sects)
    conn.sendall(bytes([1]))
    assert struct.unpack(">I", recvn(conn, 4))[0] == 0, "RFB SecurityResult != 0"
    conn.sendall(bytes([1]))
    sw, sh = struct.unpack(">HH", recvn(conn, 4))
    recvn(conn, 16)
    nl = struct.unpack(">I", recvn(conn, 4))[0]; nm = recvn(conn, nl)
    log("Zellen-RFB (x11vnc) up: %dx%d %r" % (sw, sh, nm.decode(errors="replace")))
    if (sw, sh) != (W, H):
        log("WARN: Geometrie %dx%d != erwartete %dx%d" % (sw, sh, W, H))

    pf = struct.pack(">BBBBHHHBBB", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0) + b"\x00\x00\x00"
    conn.sendall(struct.pack(">Bxxx", 0) + pf)
    conn.sendall(struct.pack(">BxH", 2, 2) + struct.pack(">ii", 0, 1))
    return sw, sh

def _blit_raw(x, y, w, h, data):
    fb = latest["fb"]; rowb = W * 4
    for r in range(h):
        d = ((y + r) * W + x) * 4
        s = r * w * 4
        fb[d:d + w * 4] = data[s:s + w * 4]

def _blit_copyrect(x, y, w, h, sx, sy):
    fb = latest["fb"]; rowb = w * 4
    if sy < y or (sy == y and sx < x):
        rows = range(h)
    else:
        rows = range(h - 1, -1, -1)
    for r in rows:
        d = ((y + r) * W + x) * 4
        s = ((sy + r) * W + sx) * 4
        fb[d:d + rowb] = fb[s:s + rowb]

def grabber(cell):
    def req(inc):
        with cell_wlock:
            cell.sendall(struct.pack(">BBHHHH", 3, inc, 0, 0, W, H))
    try:
        while not state["stop"]:
            if state["viewers"] <= 0:
                state["need_full"] = True
                time.sleep(0.2); continue
            inc = 0 if state["need_full"] else 1
            state["need_full"] = False
            try:
                req(inc)
            except Exception as e:
                log("grabber req: %r" % e); state["stop"] = True; return
            mt, _pad, nrect = struct.unpack(">BBH", recvn(cell, 4))
            if mt != 0:
                if mt == 1:
                    recvn(cell, 3); ncol = struct.unpack(">H", recvn(cell, 4)[2:4])[0]; recvn(cell, ncol * 6)
                continue
            for _ in range(nrect):
                x, y, w, h, enc = struct.unpack(">HHHHi", recvn(cell, 12))
                if enc == 0:
                    data = recvn(cell, w * h * 4)
                    with fb_lock:
                        _blit_raw(x, y, w, h, data)
                elif enc == 1:
                    sx, sy = struct.unpack(">HH", recvn(cell, 4))
                    with fb_lock:
                        _blit_copyrect(x, y, w, h, sx, sy)
                else:
                    log("grabber: unbekanntes Encoding %d -> resync full" % enc)
                    state["need_full"] = True
                    raise EOFError("enc %d" % enc)
            with fb_lock:
                latest["seq"] += 1
            time.sleep(1.0 / 12)
    except Exception as e:
        log("grabber: Zellen-Lane down (%r)" % (e,)); state["stop"] = True

def forward_to_cell(data):
    try:
        with cell_wlock:
            _forward_sock[0].sendall(data)
    except Exception as e:
        log("forward fail: %r" % (e,))

_forward_sock = [None]

def viewer_conn(c, cell):
    c.settimeout(180)
    try:
        c.sendall(b"RFB 003.008\n")
        recvn(c, 12)
        c.sendall(struct.pack(">BB", 1, 1))
        recvn(c, 1)
        c.sendall(struct.pack(">I", 0))
        recvn(c, 1)
        name = b"pn-vmcell-x11"
        pf = struct.pack(">BBBBHHHBBBBBB", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0, 0, 0, 0)
        c.sendall(struct.pack(">HH", W, H) + pf + struct.pack(">I", len(name)) + name)
        state["viewers"] += 1; state["need_full"] = True
        log("viewer verbunden (%d aktiv)" % state["viewers"])
        hdr = struct.pack(">BBH", 0, 0, 1) + struct.pack(">HHHHi", 0, 0, W, H, 0)
        last_sent = -1
        while not state["stop"]:
            mt = recvn(c, 1)[0]
            if mt == 0:
                recvn(c, 3 + 16)
            elif mt == 2:
                n = struct.unpack(">H", recvn(c, 3)[1:3])[0]; recvn(c, 4 * n)
            elif mt == 3:
                recvn(c, 9)
                for _ in range(240):
                    with fb_lock:
                        seq = latest["seq"]
                    if seq != last_sent or seq == 0:
                        break
                    time.sleep(1.0 / 24)
                with fb_lock:
                    last_sent = latest["seq"]
                    frame = bytes(latest["fb"])
                c.sendall(hdr + frame)
            elif mt == 4:
                body = recvn(c, 7)
                forward_to_cell(struct.pack(">B", 4) + body)
            elif mt == 5:
                body = recvn(c, 5)
                forward_to_cell(struct.pack(">B", 5) + body)
            elif mt == 6:
                ln = struct.unpack(">I", recvn(c, 7)[3:7])[0]; recvn(c, ln)
            else:
                break
    except (EOFError, OSError, socket.timeout):
        pass
    finally:
        state["viewers"] -= 1
        log("viewer weg (%d aktiv)" % state["viewers"])
        try:
            c.close()
        except Exception:
            pass

def register(cell_ref, sock_path, name):
    os.makedirs(os.path.dirname(REG), exist_ok=True)
    try:
        d = json.load(open(REG))
    except (OSError, ValueError):
        d = {}
    d[cell_ref] = {"sock": sock_path, "name": name, "w": W, "h": H, "pid": os.getpid()}
    tmp = REG + ".tmp.%d" % os.getpid()
    json.dump(d, open(tmp, "w"), indent=2)
    os.replace(tmp, REG)
    log("registriert: %s -> %s" % (cell_ref, sock_path))

def unregister(cell_ref):
    try:
        d = json.load(open(REG))
        d.pop(cell_ref, None)
        tmp = REG + ".tmp.%d" % os.getpid()
        json.dump(d, open(tmp, "w"), indent=2)
        os.replace(tmp, REG)
    except (OSError, ValueError):
        pass

def _live_broker_pid(cell_ref):
    try:
        ent = json.load(open(REG)).get(cell_ref)
    except (OSError, ValueError):
        return None
    pid = (ent or {}).get("pid")
    if not pid or pid == os.getpid():
        return None
    try:
        cmd = open("/proc/%d/cmdline" % pid, "rb").read().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return None
    return pid if ("cell_gui_x11_run" in cmd and cell_ref in cmd) else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", default="guix11", help="Zellen-Referenz für /ws/vnc?cell + cast")
    ap.add_argument("--cell-id", default="x11", help="Badge")
    ap.add_argument("--url", default="https://duckduckgo.com", help="Start-URL in der Zellen-Firefox")
    ap.add_argument("--geom", default="800x600", help="Xvfb-Geometrie (klein = leicht auf schwacher Box)")
    ap.add_argument("--mem", type=int, default=2048, help="Gast-RAM MB (Screen-Zelle: 2 GB Geschenk-Saum)")
    ap.add_argument("--net", default="allow", choices=["allow", "deny"],
                    help="governed egress default (cells get network by default); live-toggle via the policy file")
    a = ap.parse_args()
    gw, gh = (int(x) for x in a.geom.lower().split("x"))
    prior = _live_broker_pid(a.id)
    if prior:
        log("bereits lebender Broker für %s (pid %d) — kein zweiter Boot" % (a.id, prior))
        return
    seat_p = "/tmp/cellguix11-%s-seat.sock" % a.id
    rfbs_p = "/tmp/cellguix11-%s-rfb.sock" % a.id
    ks_p = "/tmp/cellguix11-%s-keystore.img" % a.id
    net_p = "/tmp/cellguix11-%s-net.sock" % a.id
    pol_p = "/tmp/cellguix11-%s-policy.json" % a.id

    try:
        json.dump({"net_general": a.net}, open(pol_p, "w"))
    except OSError:
        pass
    view_p = os.path.join(DATA, "vmcells", "%s.sock" % a.id)
    os.makedirs(os.path.dirname(view_p), exist_ok=True)
    try:
        os.unlink(view_p)
    except OSError:
        pass

    admit_id = "screen:" + a.id

    if _ADMIT is not None:
        pl = _ADMIT.plan(a.mem, "screen", exclude_id=admit_id)
        if not pl.get("grant"):
            log("RAM-Admission verweigert: %s" % pl.get("reason", ""))
            print("RAM_ADMISSION_DENIED: %s" % pl.get("reason", ""), file=sys.stderr)
            sys.exit(11)
        log("RAM-Admission OK: %d/%d MiB Budget, danach belegt %d" %
            (pl["committed_mb"], pl["budget_mb"], pl["would_use_mb"]))

    vm, cell, net_broker = boot_cell(seat_p, rfbs_p, ks_p, a.url, gw, gh, a.mem, net_p, pol_p)
    _forward_sock[0] = cell
    if _ADMIT is not None:
        try:
            _ADMIT.reserve(admit_id, "screen", a.mem, vm.pid, ctl_pid=os.getpid(),
                           label="GUI-Zelle %s (X11/Firefox)" % a.cell_id)
        except Exception:
            pass
    global W, H
    sw, sh = rfb_client_handshake(cell)
    W, H = sw, sh
    latest["fb"] = bytearray(W * H * 4)
    log("Broker-Geometrie gesetzt auf %dx%d" % (W, H))
    threading.Thread(target=grabber, args=(cell,), daemon=True).start()

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(view_p); srv.listen(4)
    os.chmod(view_p, 0o600)
    register(a.id, view_p, "GUI-Zelle %s (X11/Firefox)" % a.cell_id)

    def bye(*_):
        state["stop"] = True
        unregister(a.id)
        if _ADMIT is not None:
            try: _ADMIT.release(admit_id)
            except Exception: pass
        try:
            net_broker and net_broker.terminate()
        except Exception:
            pass
        try:
            vm.terminate()
        except Exception:
            pass
        sys.exit(0)
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)

    log("RFB-Broker bereit: %s (Zelle pid %d)" % (view_p, vm.pid))
    srv.settimeout(2)
    _seatpos = [0]; _tick = [0]
    while not state["stop"]:
        try:
            c, _ = srv.accept()
        except socket.timeout:
            _tick[0] += 1
            if _tick[0] % 8 == 0:
                sl = state["seatlog"]
                if len(sl) > _seatpos[0]:
                    log("SEAT+ %s" % sl[_seatpos[0]:][-800:].replace("\n", " | "))
                    _seatpos[0] = len(sl)
            if vm.poll() is not None:
                log("Zelle beendet (%s); seatlog tail: %s" % (vm.returncode, state["seatlog"][-600:])); break
            continue
        threading.Thread(target=viewer_conn, args=(c, cell), daemon=True).start()
    bye()

if __name__ == "__main__":
    main()
