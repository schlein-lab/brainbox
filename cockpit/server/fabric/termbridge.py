
import os
import pty
import fcntl
import json
import select
import signal
import struct
import base64
import termios
import hashlib

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

def ws_accept(key):
    return base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()

def _frame(payload, opcode=0x2):

    n = len(payload)
    h = bytes([0x80 | opcode])
    if n < 126:
        h += bytes([n])
    elif n < 65536:
        h += bytes([126]) + struct.pack("!H", n)
    else:
        h += bytes([127]) + struct.pack("!Q", n)
    return h + payload

def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        try:
            c = sock.recv(n - len(buf))
        except OSError:
            return None
        if not c:
            return None
        buf += c
    return buf

def read_frame(sock):

    hdr = _recv_exact(sock, 2)
    if not hdr:
        return None, None
    b0, b1 = hdr[0], hdr[1]
    opcode = b0 & 0x0F
    masked = b1 & 0x80
    ln = b1 & 0x7F
    if ln == 126:
        ext = _recv_exact(sock, 2)
        ln = struct.unpack("!H", ext)[0] if ext else 0
    elif ln == 127:
        ext = _recv_exact(sock, 8)
        ln = struct.unpack("!Q", ext)[0] if ext else 0
    mask = _recv_exact(sock, 4) if masked else b"\x00\x00\x00\x00"
    data = _recv_exact(sock, ln) if ln else b""
    if data is None:
        return None, None
    if masked and data:
        m = mask
        data = bytes(data[i] ^ m[i % 4] for i in range(len(data)))
    return opcode, data

def _set_winsize(fd, rows, cols):
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass

def run(sock, shell_argv, cwd, env=None):

    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.chdir(cwd)
        except OSError:
            pass
        e = dict(os.environ)
        e["TERM"] = "xterm-256color"
        e["PWD"] = cwd
        if env:
            e.update(env)
        try:
            os.execvpe(shell_argv[0], shell_argv, e)
        except OSError:
            os._exit(127)
    _set_winsize(fd, 24, 80)
    try:
        while True:
            r, _, _ = select.select([sock, fd], [], [], 300)
            if not r:
                break
            if sock in r:
                op, data = read_frame(sock)
                if op is None or op == 0x8:
                    break
                if op == 0x9:
                    try:
                        sock.sendall(_frame(data, 0xA))
                    except OSError:
                        break
                    continue
                if op in (0x1, 0x2) and data:
                    if data[:1] == b"{":
                        try:
                            msg = json.loads(data.decode("utf-8", "replace"))
                            if isinstance(msg, dict) and "resize" in msg:
                                c, rr = msg["resize"]
                                _set_winsize(fd, int(rr), int(c))
                                continue
                        except (ValueError, KeyError, TypeError):
                            pass
                    try:
                        os.write(fd, data)
                    except OSError:
                        break
            if fd in r:
                try:
                    out = os.read(fd, 65536)
                except OSError:
                    break
                if not out:
                    break
                try:
                    sock.sendall(_frame(out, 0x2))
                except OSError:
                    break
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except OSError:
            pass
