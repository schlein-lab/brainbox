#!/usr/bin/env python3

import json, os, signal, socket, struct, sys, threading, time

DATA = os.environ.get("PHANTOM_PORTAL_DATA",
                      os.path.expanduser("~/.local/share/brainbox-portal"))
REG = os.path.join(DATA, "vmcells.json")

state = {"stop": False, "viewers": 0, "need_full": True}
latest = {"fb": bytearray(0), "seq": 0}
geom = {"w": 0, "h": 0}
fb_lock = threading.Lock()
cell_wlock = threading.Lock()
_forward_sock = [None]

def log(m):
    print("[desk-bridge] %s" % m, flush=True)

def recvn(s, n):
    b = b""
    while len(b) < n:
        d = s.recv(n - len(b))
        if not d:
            raise EOFError("closed")
        b += d
    return b

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
    log("Zellen-RFB up: %dx%d %r" % (sw, sh, nm.decode(errors="replace")))

    pf = struct.pack(">BBBBHHHBBB", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0) + b"\x00\x00\x00"
    conn.sendall(struct.pack(">Bxxx", 0) + pf)
    conn.sendall(struct.pack(">BxH", 2, 2) + struct.pack(">ii", 0, 1))
    return sw, sh

def _blit_raw(x, y, w, h, data):
    fb = latest["fb"]; W = geom["w"]
    for r in range(h):
        d = ((y + r) * W + x) * 4
        s = r * w * 4
        fb[d:d + w * 4] = data[s:s + w * 4]

def _blit_copyrect(x, y, w, h, sx, sy):
    fb = latest["fb"]; W = geom["w"]; rowb = w * 4
    rows = range(h) if (sy < y or (sy == y and sx < x)) else range(h - 1, -1, -1)
    for r in rows:
        d = ((y + r) * W + x) * 4
        s = ((sy + r) * W + sx) * 4
        fb[d:d + rowb] = fb[s:s + rowb]

def grabber(cell):
    W, H = geom["w"], geom["h"]

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
            req(inc)
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
                    log("grabber: unbekanntes Encoding %d" % enc)
                    state["need_full"] = True
                    raise EOFError("enc %d" % enc)
            with fb_lock:
                latest["seq"] += 1
            time.sleep(1.0 / 12)
    except Exception as e:
        log("grabber: Zellen-Lane down (%r)" % (e,))
    state["stop"] = True

def forward_to_cell(data):
    try:
        with cell_wlock:
            _forward_sock[0].sendall(data)
    except Exception as e:
        log("forward fail: %r" % (e,))

def viewer_conn(c):
    W, H = geom["w"], geom["h"]
    c.settimeout(180)
    try:
        c.sendall(b"RFB 003.008\n")
        recvn(c, 12)
        c.sendall(struct.pack(">BB", 1, 1))
        recvn(c, 1)
        c.sendall(struct.pack(">I", 0))
        recvn(c, 1)
        name = b"pn-desk"
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
                forward_to_cell(struct.pack(">B", 4) + recvn(c, 7))
            elif mt == 5:
                forward_to_cell(struct.pack(">B", 5) + recvn(c, 5))
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
        except OSError:
            pass

def register(ref, sock_path, name):
    os.makedirs(os.path.dirname(REG), exist_ok=True)
    try:
        d = json.load(open(REG))
    except (OSError, ValueError):
        d = {}
    d[ref] = {"sock": sock_path, "name": name, "w": geom["w"], "h": geom["h"],
              "pid": os.getpid(), "kind": "desk"}
    tmp = REG + ".tmp.%d" % os.getpid()
    json.dump(d, open(tmp, "w"), indent=2)
    os.replace(tmp, REG)
    log("registriert: %s -> %s" % (ref, sock_path))

def unregister(ref):
    try:
        d = json.load(open(REG))
        if ref not in d:
            return
        d.pop(ref, None)
        tmp = REG + ".tmp.%d" % os.getpid()
        json.dump(d, open(tmp, "w"), indent=2)
        os.replace(tmp, REG)
    except (OSError, ValueError):
        pass

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", required=True, help="GUI-Lane-Socket (pn-vmm verbindet hierher; wir BINDEN)")
    ap.add_argument("--ref", required=True, help="Zellen-Referenz (vmcells.json Key, /ws/vnc?cell=)")
    ap.add_argument("--name", default="Desktop", help="Anzeigename im Cell-Picker")
    a = ap.parse_args()

    view_p = os.path.join(DATA, "vmcells", "%s.sock" % a.ref)
    for p in (a.lane, view_p):
        try:
            os.unlink(p)
        except OSError:
            pass
    os.makedirs(os.path.dirname(view_p), exist_ok=True)

    def bye(*_a):
        state["stop"] = True
        unregister(a.ref)
        os._exit(0)
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)

    lane_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    lane_srv.bind(a.lane); lane_srv.listen(1)
    lane_srv.settimeout(180)
    try:
        cell, _ = lane_srv.accept()
    except socket.timeout:
        log("pn-vmm verband sich nicht mit der GUI-Lane (Binary ohne vsock-9500-Kanal?) — Ende.")
        return 1
    lane_srv.close()
    cell.settimeout(None)
    try:
        w, h = rfb_client_handshake(cell)
    except (AssertionError, EOFError, OSError) as e:
        log("RFB-Handshake mit der Zelle scheiterte: %r — Ende." % (e,))
        return 1
    geom["w"], geom["h"] = w, h
    with fb_lock:
        latest["fb"] = bytearray(w * h * 4)
    cell.settimeout(60)
    _forward_sock[0] = cell
    threading.Thread(target=grabber, args=(cell,), daemon=True).start()

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(view_p); srv.listen(4); srv.settimeout(2.0)
    register(a.ref, view_p, a.name)
    try:
        while not state["stop"]:
            try:
                c, _ = srv.accept()
            except socket.timeout:
                continue
            threading.Thread(target=viewer_conn, args=(c,), daemon=True).start()
    finally:
        unregister(a.ref)
        for s in (srv, cell):
            try:
                s.close()
            except OSError:
                pass
    log("Lane zu — Bridge endet.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
