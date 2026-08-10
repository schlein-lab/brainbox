#!/usr/bin/env python3

import argparse, base64, json, os, shutil, signal, socket, struct, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
PNH = os.path.expanduser("~/brainarbeit/os/pn-vmm")
BIN = PNH + "/target/release/pn-vmm"
KERNEL = PNH + "/kernel/vmlinux-rng.bin"
INITRD = PNH + "/kernel/initramfs-cell.cpio"
BASE = PNH + "/kernel/base-python.img"
KS0 = PNH + "/kernel/cell-win-thin-keystore.img"
APP = os.path.join(HERE, "cell_gui_app.py")
DATA = os.path.expanduser("~/.local/share/brainbox-portal")
REG = os.path.join(DATA, "vmcells.json")
W, H = 384, 216

state = {"stop": False, "seatlog": "", "viewers": 0}
latest = {"fb": bytes(W * H * 4), "seq": 0}
fb_lock = threading.Lock()
cell_wlock = threading.Lock()

def log(m):
    print("[gui-run] %s" % m, flush=True)

def recvn(s, n):
    b = b""
    while len(b) < n:
        d = s.recv(n - len(b))
        if not d:
            raise EOFError("closed")
        b += d
    return b

def boot_cell(seat_p, rfbs_p, ks_p, cell_id):
    for p in (seat_p, rfbs_p):
        try:
            os.unlink(p)
        except OSError:
            pass
    shutil.copyfile(KS0, ks_p)
    seat_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    seat_srv.bind(seat_p); seat_srv.listen(1); seat_srv.settimeout(60)
    rfb_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    rfb_srv.bind(rfbs_p); rfb_srv.listen(1); rfb_srv.settimeout(60)

    def seat_thread():
        try:
            c, _ = seat_srv.accept()
        except Exception as e:
            log("seat accept fail: %r" % e); return
        c.settimeout(60); buf = b""
        while b"PN_SEAT_READY" not in buf:
            try:
                d = c.recv(4096)
            except Exception:
                return
            if not d:
                return
            buf += d
        src = open(APP, "rb").read().replace(b'CELL_ID = "0"', b'CELL_ID = "%s"' % cell_id.encode())
        b64 = base64.b64encode(src).decode()
        cmd = "/bin/python3 -c \"import base64;exec(base64.b64decode('%s'))\" 2>&1\n" % b64
        time.sleep(0.4); c.sendall(cmd.encode())
        log("seat ready; gui app injected (%d b64 bytes)" % len(b64))
        while not state["stop"]:
            try:
                d = c.recv(4096)
            except Exception:
                break
            if not d:
                break
            state["seatlog"] += d.decode(errors="replace")

    threading.Thread(target=seat_thread, daemon=True).start()
    env = dict(os.environ)
    env["PN_VMM_BLK"] = "%s,%s" % (BASE, ks_p)
    env["PN_VMM_VSOCK"] = "23"
    env["PN_VMM_VSOCK_SEAT"] = seat_p
    env["PN_VMM_VSOCK_RFB"] = rfbs_p
    vm = subprocess.Popen([BIN, KERNEL, INITRD],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    log("pn-vmm booted (pid %d)" % vm.pid)
    conn, _ = rfb_srv.accept()
    conn.settimeout(60)
    return vm, conn

def rfb_client_handshake(conn):
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
    log("Zellen-RFB up: %dx%d %r" % (sw, sh, nm.decode(errors="replace")))
    assert (sw, sh) == (W, H), "unexpected geometry"

def grabber(cell):

    while not state["stop"]:
        if state["viewers"] <= 0:
            time.sleep(0.25); continue
        try:
            with cell_wlock:
                cell.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, W, H))
            mt, _p, nrect = struct.unpack(">BBH", recvn(cell, 4))
            assert mt == 0 and nrect == 1
            x, y, w, h, enc = struct.unpack(">HHHHi", recvn(cell, 12))
            assert enc == 0 and (w, h) == (W, H)
            fb = recvn(cell, w * h * 4)
            with fb_lock:
                latest["fb"] = fb; latest["seq"] += 1
            time.sleep(1.0 / 12)
        except Exception as e:
            log("grabber: Zellen-Lane down (%r)" % (e,)); state["stop"] = True; return

def forward_pointer(cell, mask, x, y):
    try:
        with cell_wlock:
            cell.sendall(struct.pack(">BBHH", 5, mask, x, y))
    except Exception as e:
        log("pointer forward fail: %r" % (e,))

def viewer_conn(c, cell):
    c.settimeout(120)
    try:
        c.sendall(b"RFB 003.008\n")
        recvn(c, 12)
        c.sendall(struct.pack(">BB", 1, 1))
        recvn(c, 1)
        c.sendall(struct.pack(">I", 0))
        recvn(c, 1)
        name = b"pn-vmcell-gui"
        pf = struct.pack(">BBBBHHHBBBBBB", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0, 0, 0, 0)
        c.sendall(struct.pack(">HH", W, H) + pf + struct.pack(">I", len(name)) + name)
        state["viewers"] += 1
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

                for _ in range(120):
                    with fb_lock:
                        seq, fb = latest["seq"], latest["fb"]
                    if seq != last_sent or seq == 0:
                        break
                    time.sleep(1.0 / 24)
                last_sent = seq
                c.sendall(hdr + fb)
            elif mt == 4:
                recvn(c, 7)
            elif mt == 5:
                mask, x, y = struct.unpack(">BHH", recvn(c, 5))
                forward_pointer(cell, mask, x, y)
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
    return pid if ("cell_gui_run" in cmd and cell_ref in cmd) else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", default="gui-demo", help="Zellen-Referenz für /ws/vnc?cell=<id>")
    ap.add_argument("--cell-id", default="7", help="Nummer im Zellen-Badge")
    a = ap.parse_args()
    prior = _live_broker_pid(a.id)
    if prior:
        log("bereits lebender Broker für %s (pid %d) — kein zweiter Boot (Anti-Doppelboot)" % (a.id, prior))
        return
    seat_p = "/tmp/cellgui-%s-seat.sock" % a.id
    rfbs_p = "/tmp/cellgui-%s-rfb.sock" % a.id
    ks_p = "/tmp/cellgui-%s-keystore.img" % a.id
    view_p = os.path.join(DATA, "vmcells", "%s.sock" % a.id)
    os.makedirs(os.path.dirname(view_p), exist_ok=True)
    try:
        os.unlink(view_p)
    except OSError:
        pass

    vm, cell = boot_cell(seat_p, rfbs_p, ks_p, a.cell_id)
    rfb_client_handshake(cell)
    threading.Thread(target=grabber, args=(cell,), daemon=True).start()

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(view_p); srv.listen(4)
    os.chmod(view_p, 0o600)
    register(a.id, view_p, "GUI-Zelle %s (Demo)" % a.cell_id)

    def bye(*_):
        state["stop"] = True
        unregister(a.id)
        try:
            vm.terminate()
        except Exception:
            pass
        sys.exit(0)
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)

    log("RFB-Broker bereit: %s (Zelle pid %d)" % (view_p, vm.pid))
    srv.settimeout(2)
    while not state["stop"]:
        try:
            c, _ = srv.accept()
        except socket.timeout:
            if vm.poll() is not None:
                log("Zelle beendet (%s)" % vm.returncode); break
            continue
        threading.Thread(target=viewer_conn, args=(c, cell), daemon=True).start()
    bye()

if __name__ == "__main__":
    main()
