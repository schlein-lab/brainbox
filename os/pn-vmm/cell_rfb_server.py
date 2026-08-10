#!/usr/bin/env python3

import socket, struct, sys, time

HOST_CID = 2
RFB_PORT = 5900
W, H = 200, 150
CUR = 8

def log(m):
    sys.stderr.write("[cell-rfb] %s\n" % m); sys.stderr.flush()

def render(px, py):

    fb = bytearray(W * H * 4)
    for y in range(H):
        top = y < H // 2
        for x in range(W):
            left = x < W // 2
            if top and left:      r, g, b = 220, 40, 40
            elif top:             r, g, b = 40, 200, 40
            elif left:            r, g, b = 50, 90, 230
            else:                 r, g, b = 230, 230, 230
            if px <= x < px + CUR and py <= y < py + CUR:
                r = g = b = 0
            o = (y * W + x) * 4
            fb[o] = b; fb[o + 1] = g; fb[o + 2] = r; fb[o + 3] = 0
    return bytes(fb)

def send_fb_update(sock, fb):

    hdr = struct.pack(">BBH", 0, 0, 1) + struct.pack(">HHHHi", 0, 0, W, H, 0)
    sock.sendall(hdr + fb)

def recvn(sock, n):
    b = b""
    while len(b) < n:
        d = sock.recv(n - len(b))
        if not d:
            raise EOFError("client closed")
        b += d
    return b

def main():
    s = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    s.settimeout(30)
    s.connect((HOST_CID, RFB_PORT))
    log("connected to host %d:%d" % (HOST_CID, RFB_PORT))

    s.sendall(b"RFB 003.008\n")
    recvn(s, 12)
    s.sendall(struct.pack(">BB", 1, 1))
    recvn(s, 1)
    s.sendall(struct.pack(">I", 0))
    recvn(s, 1)
    name = b"pn-cell-screen"
    pf = struct.pack(">BBBBHHHBBBBBB", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0, 0, 0, 0)
    s.sendall(struct.pack(">HH", W, H) + pf + struct.pack(">I", len(name)) + name)
    log("ServerInit sent (%dx%d)" % (W, H))

    px, py = W // 2 - CUR // 2, H // 2 - CUR // 2
    events = 0
    s.sendall(b"")
    while True:
        try:
            mt = recvn(s, 1)[0]
        except (EOFError, socket.timeout, OSError) as e:
            log("client gone (%s); events=%d" % (e, events)); break
        if mt == 0:
            recvn(s, 3 + 16)
        elif mt == 2:
            n = struct.unpack(">H", recvn(s, 3)[1:3])[0]
            recvn(s, 4 * n)
        elif mt == 3:
            recvn(s, 9)
            send_fb_update(s, render(px, py))
        elif mt == 4:
            recvn(s, 7); events += 1
        elif mt == 5:
            body = recvn(s, 5)
            _, x, y = struct.unpack(">BHH", body)
            px = max(0, min(W - CUR, x)); py = max(0, min(H - CUR, y))
            events += 1
            log("PointerEvent -> (%d,%d) [cursor box now at %d,%d]" % (x, y, px, py))
        elif mt == 6:
            ln = struct.unpack(">I", recvn(s, 7)[3:7])[0]
            if ln > (1 << 20):
                log("oversize ClientCutText ln=%d; closing" % ln); break
            recvn(s, ln)
        else:
            log("unknown client msg-type %d; closing" % mt); break
    try:
        s.close()
    except Exception:
        pass
    log("done")

if __name__ == "__main__":
    main()
