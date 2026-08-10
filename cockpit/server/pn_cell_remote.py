

import hmac
import json
import os
import socket
import ssl
import sys
import threading
import time

DEFAULT_LANE_PORT = int(os.environ.get("PN_CELL_LANE_PORT", "8197"))

_ALLOWED_LANES = ("seat", "llm", "term", "portal", "net", "act")

_SPLICE_BUF = 65536

_REJECT_LOG_S = 60.0
_reject_last = {}
_reject_lk = threading.Lock()

def _reject_note(cell_id, lane, grund):

    now = time.time()
    key = (cell_id, lane, grund)
    with _reject_lk:
        ent = _reject_last.get(key)
        if ent is None or (now - ent[0]) >= _REJECT_LOG_S:
            unterdrueckt = ent[1] if ent else 0
            _reject_last[key] = [now, 0]
            return True, unterdrueckt
        ent[1] += 1
        return False, 0

def _splice(src, dst, on_close):

    try:
        while True:
            data = src.recv(_SPLICE_BUF)
            if not data:
                break
            dst.sendall(data)
    except (OSError, ssl.SSLError):
        pass
    finally:
        on_close()

class LaneTerminator:

    def __init__(self, port=None, cert=None, key=None):
        self.port = int(port or DEFAULT_LANE_PORT)
        self._cert = cert
        self._key = key
        self._srv = None
        self._ctx = None
        self._thread = None
        self._running = False
        self._lock = threading.Lock()

        self._cells = {}

    def register(self, cell_id, lane, sockpath, node_id, node_token):

        if lane not in _ALLOWED_LANES:
            raise ValueError("unknown lane %r" % lane)
        with self._lock:
            c = self._cells.setdefault(cell_id, {"node_id": node_id, "token": node_token, "lanes": {}})
            c["node_id"] = node_id
            c["token"] = node_token
            c["lanes"][lane] = sockpath

    def unregister_cell(self, cell_id):
        with self._lock:
            self._cells.pop(cell_id, None)

    def _lookup(self, cell_id, lane, token):

        with self._lock:
            c = self._cells.get(cell_id)
            if not c:
                return None
            want = c.get("token") or ""
            if not want or not token:
                return None
            if not hmac.compare_digest(str(token).encode(), str(want).encode()):
                return None
            return c["lanes"].get(lane)

    def _make_ctx(self):
        cert, key = self._cert, self._key
        if not cert or not key:

            try:
                import pn_certs
                cert, key = pn_certs.ensure_portal_cert({})
            except Exception as e:
                raise RuntimeError("pn_cell_remote: kein TLS-Leaf verfuegbar (%s)" % e)
        if not cert or not key:
            raise RuntimeError("pn_cell_remote: pn_certs lieferte kein Zertifikat/Key")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)

        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def start(self):
        with self._lock:
            if self._running:
                return self.port
            self._ctx = self._make_ctx()
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", self.port))
            srv.listen(64)
            self._srv = srv
            self._running = True
            self._thread = threading.Thread(target=self._serve, name="cell-lane-terminator", daemon=True)
            self._thread.start()
        sys.stderr.write("[pn-cell-remote] lane terminator listening on :%d (TLS)\n" % self.port)
        return self.port

    def stop(self):
        with self._lock:
            self._running = False
            s = self._srv
            self._srv = None
        if s is not None:
            try:
                s.close()
            except OSError:
                pass

    def _serve(self):
        srv = self._srv
        while self._running and srv is not None:
            try:
                raw, addr = srv.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(raw, addr), daemon=True).start()

    def _handle(self, raw, addr):
        tls = None
        local = None
        try:
            raw.settimeout(20)
            tls = self._ctx.wrap_socket(raw, server_side=True)

            hs = self._read_line(tls)
            if hs is None:
                return
            try:
                obj = json.loads(hs)
            except ValueError:
                return
            cell_id = str(obj.get("cell_id") or "")
            lane = str(obj.get("lane") or "")
            token = str(obj.get("token") or "")
            sockpath = self._lookup(cell_id, lane, token)
            if not sockpath:

                zeigen, verschluckt = _reject_note(cell_id[:40], lane, "unregistriert")
                if zeigen:
                    extra = (" (+%d unterdrueckt/%ds)" % (verschluckt, int(_REJECT_LOG_S))) if verschluckt else ""
                    sys.stderr.write("[pn-cell-remote] reject lane cell=%s lane=%s from %s%s\n"
                                     % (cell_id[:40], lane, addr[0], extra))
                return

            local = self._connect_local(sockpath)
            if local is None:
                sys.stderr.write("[pn-cell-remote] local sock unreachable cell=%s lane=%s (%s)\n"
                                 % (cell_id[:40], lane, sockpath))
                return
            tls.settimeout(None)
            self._pump(tls, local)
        except (OSError, ssl.SSLError) as e:
            try:
                sys.stderr.write("[pn-cell-remote] lane error from %s: %s\n" % (addr[0], e))
            except Exception:
                pass
        finally:
            for s in (tls, local, raw):
                try:
                    if s is not None:
                        s.close()
                except OSError:
                    pass

    @staticmethod
    def _read_line(sock, limit=8192):
        buf = b""
        while b"\n" not in buf:
            try:
                d = sock.recv(1)
            except (OSError, ssl.SSLError):
                return None
            if not d:
                return None
            buf += d
            if len(buf) > limit:
                return None
        return buf.split(b"\n", 1)[0].decode("utf-8", "replace")

    @staticmethod
    def _connect_local(sockpath, tries=50, delay=0.1):

        for _ in range(tries):
            try:
                c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                c.connect(sockpath)
                return c
            except OSError:
                time.sleep(delay)
        return None

    @staticmethod
    def _pump(a, b):

        done = threading.Event()

        def close_both():
            if not done.is_set():
                done.set()
                for s in (a, b):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        t1 = threading.Thread(target=_splice, args=(a, b, close_both), daemon=True)
        t2 = threading.Thread(target=_splice, args=(b, a, close_both), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

_TERM = None
_TERM_LOCK = threading.Lock()

def get_terminator(port=None, autostart=True):

    global _TERM
    with _TERM_LOCK:
        if _TERM is None:
            _TERM = LaneTerminator(port=port)
            if autostart:
                _TERM.start()
        return _TERM

if __name__ == "__main__":

    t = get_terminator()
    print("lane terminator on :%d — Ctrl-C to stop" % t.port)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        t.stop()
