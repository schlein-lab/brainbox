#!/usr/bin/env python3

import socket, struct, sys, time

HOST_CID, RFB_PORT = 2, 5900
W, H = 384, 216

def log(m):
    sys.stderr.write("[cell-live] %s\n" % m); sys.stderr.flush()

FONT = {
 "A": (0x0E,0x11,0x11,0x1F,0x11,0x11,0x11), "B": (0x1E,0x11,0x11,0x1E,0x11,0x11,0x1E),
 "C": (0x0E,0x11,0x10,0x10,0x10,0x11,0x0E), "E": (0x1F,0x10,0x10,0x1E,0x10,0x10,0x1F),
 "I": (0x0E,0x04,0x04,0x04,0x04,0x04,0x0E), "L": (0x10,0x10,0x10,0x10,0x10,0x10,0x1F),
 "N": (0x11,0x19,0x15,0x13,0x11,0x11,0x11), "O": (0x0E,0x11,0x11,0x11,0x11,0x11,0x0E),
 "R": (0x1E,0x11,0x11,0x1E,0x14,0x12,0x11), "V": (0x11,0x11,0x11,0x11,0x11,0x0A,0x04),
 "X": (0x11,0x11,0x0A,0x04,0x0A,0x11,0x11),
 "0": (0x0E,0x11,0x13,0x15,0x19,0x11,0x0E), "1": (0x04,0x0C,0x04,0x04,0x04,0x04,0x0E),
 "2": (0x0E,0x11,0x01,0x02,0x04,0x08,0x1F), "3": (0x1F,0x02,0x04,0x02,0x01,0x11,0x0E),
 "4": (0x02,0x06,0x0A,0x12,0x1F,0x02,0x02), "5": (0x1F,0x10,0x1E,0x01,0x01,0x11,0x0E),
 "6": (0x06,0x08,0x10,0x1E,0x11,0x11,0x0E), "7": (0x1F,0x01,0x02,0x04,0x08,0x08,0x08),
 "8": (0x0E,0x11,0x11,0x0E,0x11,0x11,0x0E), "9": (0x0E,0x11,0x11,0x0F,0x01,0x02,0x0C),
 ":": (0x00,0x04,0x00,0x00,0x04,0x00,0x00), " ": (0,0,0,0,0,0,0),
}

def build_bg():
    fb = bytearray(W * H * 4)
    for y in range(H):
        v = 16 + int(26 * y / H)
        fb[y*W*4:(y+1)*W*4] = bytes((v+14, v//2+8, v//2+4, 0)) * W
    return bytes(fb)
BG = build_bg()

def fill_rect(fb, x, y, w, h, rgb):
    r, g, b = rgb; row = bytes((b, g, r, 0)) * w
    for yy in range(y, y + h):
        o = (yy * W + x) * 4
        fb[o:o+4*w] = row

def draw_text(fb, x, y, s, text, rgb):
    r, g, b = rgb; px = bytes((b, g, r, 0)) * s
    for ch in text:
        gl = FONT.get(ch)
        if gl:
            for gy, bits in enumerate(gl):
                for gx in range(5):
                    if bits >> (4 - gx) & 1:
                        for dy in range(s):
                            o = ((y + gy*s + dy) * W + x + gx*s) * 4
                            fb[o:o+4*s] = px
        x += 6 * s

class Anim:
    def __init__(self):
        self.t0 = time.monotonic(); self.frame = 0
        self.bx, self.by, self.vx, self.vy = 250.0, 160.0, 23.0, 17.0
    def render(self):
        self.frame += 1
        fb = bytearray(BG)
        draw_text(fb, 18, 14, 3, "BRAINBOX CELL", (80, 220, 255))
        draw_text(fb, 18, 48, 3, "LIVE", (255, 80, 80))
        if self.frame % 2 == 0:
            fill_rect(fb, 104, 52, 12, 12, (255, 60, 60))
        el = int(time.monotonic() - self.t0)
        draw_text(fb, 18, 88, 5, "%02d:%02d:%02d" % (el//3600, el//60 % 60, el % 60), (255, 255, 255))

        self.bx += self.vx; self.by += self.vy
        if self.bx < 210 or self.bx > W - 20: self.vx = -self.vx; self.bx += self.vx
        if self.by < 140 or self.by > H - 26: self.vy = -self.vy; self.by += self.vy
        fill_rect(fb, int(self.bx), int(self.by), 18, 18, (255, 170, 40))

        bx = (self.frame * 9) % (W - 60)
        fill_rect(fb, bx, H - 12, 60, 6, (70, 230, 120))
        return fb

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
    s.settimeout(60)
    s.connect((HOST_CID, RFB_PORT))
    log("connected to host %d:%d" % (HOST_CID, RFB_PORT))
    s.sendall(b"RFB 003.008\n"); recvn(s, 12)
    s.sendall(struct.pack(">BB", 1, 1)); recvn(s, 1)
    s.sendall(struct.pack(">I", 0)); recvn(s, 1)
    name = b"pn-cell-live"
    pf = struct.pack(">BBBBHHHBBBBBB", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0, 0, 0, 0)
    s.sendall(struct.pack(">HH", W, H) + pf + struct.pack(">I", len(name)) + name)
    log("ServerInit sent (%dx%d)" % (W, H))
    anim = Anim()
    hdr = struct.pack(">BBH", 0, 0, 1) + struct.pack(">HHHHi", 0, 0, W, H, 0)
    while True:
        try:
            mt = recvn(s, 1)[0]
        except (EOFError, socket.timeout, OSError) as e:
            log("client gone (%s) after %d frames" % (e, anim.frame)); break
        if mt == 0:   recvn(s, 3 + 16)
        elif mt == 2:
            n = struct.unpack(">H", recvn(s, 3)[1:3])[0]; recvn(s, 4 * n)
        elif mt == 3:
            recvn(s, 9)
            s.sendall(hdr + bytes(anim.render()))
        elif mt == 4: recvn(s, 7)
        elif mt == 5: recvn(s, 5)
        elif mt == 6:
            ln = struct.unpack(">I", recvn(s, 7)[3:7])[0]; recvn(s, ln)
        else:
            log("unknown msg-type %d" % mt); break
    try: s.close()
    except Exception: pass
    log("done")

if __name__ == "__main__":
    main()
