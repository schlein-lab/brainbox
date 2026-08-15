#!/bin/python3

import os, pty, socket, select, fcntl, termios, struct, signal, time

CID = int(os.environ.get("PN_TERM_CID", "2"))
PORT = int(os.environ.get("PN_TERM_PORT", "9300"))
CMD = os.environ.get("PN_TERM_CMD", "/bin/claude")
COLS = int(os.environ.get("PN_TERM_COLS", "120"))
ROWS = int(os.environ.get("PN_TERM_ROWS", "40"))

pid, mfd = pty.fork()
if pid == 0:

    try:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    except Exception:
        pass
    env = dict(os.environ)
    env.setdefault("HOME", "/root")
    env["TERM"] = "xterm-256color"
    env["IS_SANDBOX"] = "1"
    env["COLUMNS"] = str(COLS); env["LINES"] = str(ROWS)
    try:
        os.execvpe("/bin/sh", ["/bin/sh", "-lc", CMD], env)
    except Exception:
        os._exit(127)

try:
    fcntl.ioctl(mfd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
except Exception:
    pass

vs = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
vs.connect((CID, PORT))
vfd = vs.fileno()
os.set_blocking(mfd, True)
vs.setblocking(True)

RS = b"\xff\xfaWSZ"
hb = b""

_cur = [ROWS, COLS]

def _apply_winsize(rows, cols):

    try:
        if rows == _cur[0] and cols == _cur[1] and rows > 1:
            fcntl.ioctl(mfd, termios.TIOCSWINSZ, struct.pack("HHHH", rows - 1, cols, 0, 0))
            time.sleep(0.08)
        fcntl.ioctl(mfd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        _cur[0], _cur[1] = rows, cols
    except Exception:
        pass

try:
    while True:
        r, _, _ = select.select([mfd, vfd], [], [])
        if mfd in r:
            try:
                d = os.read(mfd, 65536)
            except OSError:
                break
            if not d:
                break
            try:
                vs.sendall(d)
            except OSError:
                break
        if vfd in r:
            try:
                d = vs.recv(65536)
            except OSError:
                break
            if not d:
                break
            hb += d
            out = b""
            while hb:
                i = hb.find(RS)
                if i < 0:

                    keep = 0
                    for k in range(min(len(RS) - 1, len(hb)), 0, -1):
                        if hb[-k:] == RS[:k]:
                            keep = k; break
                    if keep:
                        out += hb[:-keep]; hb = hb[-keep:]
                    else:
                        out += hb; hb = b""
                    break
                out += hb[:i]
                rest = hb[i:]
                if len(rest) < len(RS) + 4:
                    hb = rest; break
                rows = (rest[len(RS)] << 8) | rest[len(RS) + 1]
                cols = (rest[len(RS) + 2] << 8) | rest[len(RS) + 3]
                _apply_winsize(rows, cols)
                hb = rest[len(RS) + 4:]
            if out:
                try:
                    os.write(mfd, out)
                except OSError:
                    break
finally:
    try: os.kill(pid, signal.SIGKILL)
    except Exception: pass
    try: vs.close()
    except Exception: pass
