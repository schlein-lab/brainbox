#!/usr/bin/env python3

import socket, struct, threading, os, itertools, collections

LISTEN = ("127.0.0.1", int(os.environ.get("PN_PROXY_PORT", "8088")))
TRANSPORT = os.environ.get("PN_PROXY_TRANSPORT", "vsock:2:9100")
HDR = struct.Struct("!IBI")
DATA, CLOSE = 0, 1
MAX_BACKLOG = 64 << 20

def connect_transport():
    kind = TRANSPORT.split(":")[0]
    if kind == "vsock":
        _, cid, port = TRANSPORT.split(":")
        s = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        s.connect((int(cid), int(port)))
        return s
    _, path = TRANSPORT.split(":", 1)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(path)
    return s

class _Writer:

    def __init__(self, sock):
        self.sock, self.q, self.n = sock, collections.deque(), 0
        self.cv = threading.Condition()
        self.dead = self.eof = False
        threading.Thread(target=self._drain, daemon=True).start()

    def feed(self, payload):
        with self.cv:
            if self.dead:
                return
            if self.n + len(payload) > MAX_BACKLOG:
                self.dead = True
                self.cv.notify()
                return
            self.q.append(payload)
            self.n += len(payload)
            self.cv.notify()

    def finish(self):
        with self.cv:
            self.eof = True
            self.cv.notify()

    def _drain(self):
        while True:
            with self.cv:
                while not self.q and not self.dead and not self.eof:
                    self.cv.wait()
                if self.dead or (self.eof and not self.q):
                    break
                d = self.q.popleft()
                self.n -= len(d)
            try:
                self.sock.sendall(d)
            except OSError:
                with self.cv:
                    self.dead = True
                break
        try:
            self.sock.close()
        except OSError:
            pass

class Mux:
    def __init__(self, transport):
        self.t = transport
        self.wlock = threading.Lock()
        self.conns = {}
        self.outs = {}
        self.ids = itertools.count(1)
        threading.Thread(target=self.reader, daemon=True).start()

    def send_frame(self, sid, typ, payload=b""):
        with self.wlock:
            self.t.sendall(HDR.pack(sid, typ, len(payload)) + payload)

    def _recvn(self, buf, n):
        while len(buf[0]) < n:
            d = self.t.recv(65536)
            if not d:
                return False
            buf[0] += d
        return True

    def reader(self):
        buf = [b""]
        while True:
            if not self._recvn(buf, HDR.size):
                return
            sid, typ, ln = HDR.unpack(buf[0][:HDR.size]); buf[0] = buf[0][HDR.size:]
            if not self._recvn(buf, ln):
                return
            payload = buf[0][:ln]; buf[0] = buf[0][ln:]
            w = self.outs.get(sid)
            if typ == DATA and w:
                w.feed(payload)
            elif typ == CLOSE and w:
                w.finish()
                self.conns.pop(sid, None)
                self.outs.pop(sid, None)

    def new_client(self, c):
        sid = next(self.ids)
        self.conns[sid] = c
        self.outs[sid] = _Writer(c)
        threading.Thread(target=self.pump_client, args=(sid, c), daemon=True).start()

    def pump_client(self, sid, c):
        try:
            while True:
                d = c.recv(65536)
                if not d:
                    break
                self.send_frame(sid, DATA, d)
        except OSError:
            pass
        try:
            self.send_frame(sid, CLOSE)
        except OSError:
            pass

def main():
    mux = Mux(connect_transport())
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(LISTEN); srv.listen(16)
    print("PN_INCELL_PROXY_READY %s:%d -> %s" % (LISTEN[0], LISTEN[1], TRANSPORT), flush=True)
    while True:
        c, _ = srv.accept()
        mux.new_client(c)

if __name__ == "__main__":
    main()
