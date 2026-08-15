
from __future__ import annotations
import json, threading, time, inspect, ssl as _ssl

from relaylib import protocol as P

DEFAULT_MAX_SIZE = 1 << 20
DEFAULT_HANDSHAKE_TIMEOUT = 10.0
DEFAULT_IDLE_TIMEOUT = 300.0

DEFAULT_UNMATCHED_TIMEOUT = 5.0
DEFAULT_REGISTER_PARK_TIMEOUT = 600.0
DEFAULT_PARK_SLICE = 1.0
DEFAULT_PING_INTERVAL = 20.0
DEFAULT_MAX_CONNS = 1024

DEFAULT_MAX_CONNS_PER_IP = 16
DEFAULT_CONN_RATE_PER_IP = 8.0
DEFAULT_CONN_BURST_PER_IP = 16
DEFAULT_REG_RATE_PER_IP = 4.0
DEFAULT_REG_BURST_PER_IP = 8
DEFAULT_MAX_TOPICS_PER_IP = 8

def _supported_params(factory) -> set:

    try:
        return set(inspect.signature(factory).parameters)
    except (ValueError, TypeError):
        return set()

def _serve_params() -> set:
    try:
        from websockets.sync.server import serve as _ws_serve
        return _supported_params(_ws_serve)
    except Exception:
        return set()

def _connect_params() -> set:
    try:
        from websockets.sync.client import connect as _ws_connect
        return _supported_params(_ws_connect)
    except Exception:
        return set()

class _Waiter:

    __slots__ = ("conn", "peer", "matched", "role", "src")
    def __init__(self, conn, role, src):
        self.conn = conn
        self.peer = None
        self.matched = threading.Event()
        self.role = role
        self.src = src

class _TokenBucket:

    __slots__ = ("rate", "burst", "tokens", "ts")
    def __init__(self, rate, burst):
        self.rate = float(rate)
        self.burst = float(burst)
        self.tokens = float(burst)
        self.ts = time.monotonic()

    def take(self, now=None) -> bool:
        now = time.monotonic() if now is None else now
        self.tokens = min(self.burst, self.tokens + (now - self.ts) * self.rate)
        self.ts = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

class WSSChannel:
    def __init__(self, conn, rz):
        self._conn = conn
        self.rz = rz

    @classmethod
    def _connect(cls, url: str, timeout: float, ssl_context=None, origin=None):
        if not (url.startswith("wss://") or url.startswith("ws://")):
            raise ValueError(f"WSSChannel requires a ws(s):// URL, got {url!r}")
        from websockets.sync.client import connect as _ws_connect
        kw = {"open_timeout": timeout, "max_size": DEFAULT_MAX_SIZE}

        params = _connect_params()
        if "ping_interval" in params:
            kw["ping_interval"] = DEFAULT_PING_INTERVAL
        if "ping_timeout" in params:
            kw["ping_timeout"] = DEFAULT_PING_INTERVAL

        if "proxy" in params:
            kw["proxy"] = None
        if url.startswith("wss://"):

            kw["ssl"] = ssl_context or _ssl.create_default_context()
        if origin:
            kw["origin"] = origin
        return _ws_connect(url, **kw)

    @classmethod
    def register(cls, url, rz, timeout=10, *, ssl_context=None, origin=None):

        conn = cls._connect(url, timeout, ssl_context, origin)
        conn.send(json.dumps({"t": P.RZ_REGISTER, "rz": rz}))
        return cls(conn, rz)

    @classmethod
    def dial(cls, url, rz, timeout=10, *, ssl_context=None, origin=None):

        conn = cls._connect(url, timeout, ssl_context, origin)
        conn.send(json.dumps({"t": P.RZ_DIAL, "rz": rz}))
        return cls(conn, rz)

    def send_blob(self, blob_hex: str):
        self._conn.send(json.dumps({"t": P.RZ_DATA, "rz": self.rz, "blob": blob_hex}))

    def recv_blob(self, timeout=None):
        try:
            raw = self._conn.recv(timeout=timeout)
        except TimeoutError:
            return None
        except Exception:
            return None
        if raw is None:
            return None
        try:
            f = json.loads(raw if isinstance(raw, str) else raw.decode())
        except Exception:
            return None
        if f.get("t") != P.RZ_DATA:
            return None
        return f.get("blob")

    def close(self):
        try:
            self._conn.send(json.dumps({"t": P.RZ_BYE, "rz": self.rz}))
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass

class RendezvousServer:
    def __init__(self, host="127.0.0.1", port=0, *, ssl_context=None, allow_insecure=False,
                 allowed_origins=None, max_size=DEFAULT_MAX_SIZE,
                 handshake_timeout=DEFAULT_HANDSHAKE_TIMEOUT, idle_timeout=DEFAULT_IDLE_TIMEOUT,
                 unmatched_timeout=DEFAULT_UNMATCHED_TIMEOUT,
                 register_park_timeout=DEFAULT_REGISTER_PARK_TIMEOUT,
                 park_slice=DEFAULT_PARK_SLICE,
                 ping_interval=DEFAULT_PING_INTERVAL,
                 max_conns=DEFAULT_MAX_CONNS, max_waiters_per_topic=1,
                 max_conns_per_ip=DEFAULT_MAX_CONNS_PER_IP,
                 conn_rate_per_ip=DEFAULT_CONN_RATE_PER_IP,
                 conn_burst_per_ip=DEFAULT_CONN_BURST_PER_IP,
                 reg_rate_per_ip=DEFAULT_REG_RATE_PER_IP,
                 reg_burst_per_ip=DEFAULT_REG_BURST_PER_IP,
                 max_topics_per_ip=DEFAULT_MAX_TOPICS_PER_IP):

        if ssl_context is None and not allow_insecure:
            raise ValueError("RendezvousServer requires a TLS ssl_context in production "
                             "(pass allow_insecure=True ONLY for a private local test)")
        self._ssl = ssl_context
        self._allowed_origins = set(allowed_origins) if allowed_origins else None
        self._max_size = max_size
        self._handshake_timeout = handshake_timeout
        self._idle_timeout = idle_timeout
        self._unmatched_timeout = unmatched_timeout

        self._register_park_timeout = register_park_timeout
        self._park_slice = max(0.05, park_slice)
        self._ping_interval = ping_interval
        self._max_conns = max_conns
        self._max_waiters = max_waiters_per_topic

        self._max_conns_per_ip = max_conns_per_ip
        self._conn_rate_per_ip = conn_rate_per_ip
        self._conn_burst_per_ip = conn_burst_per_ip

        self._reg_rate_per_ip = reg_rate_per_ip
        self._reg_burst_per_ip = reg_burst_per_ip
        self._max_topics_per_ip = max_topics_per_ip
        self._host, self._port = host, port
        self._lock = threading.Lock()
        self._waiting = {}
        self._conns = 0
        self._ip_conns = {}
        self._ip_buckets = {}
        self._ip_reg_buckets = {}
        self._ip_topics = {}

        self.seen_frames = []
        self._server = None
        self._thread = None

    @property
    def url(self):
        scheme = "wss" if self._ssl else "ws"
        return f"{scheme}://{self._host}:{self._port}"

    def start(self):
        from websockets.sync.server import serve as _ws_serve

        params = _serve_params()
        kw = dict(ssl=self._ssl, max_size=self._max_size)
        if "open_timeout" in params:
            kw["open_timeout"] = self._handshake_timeout
        if "ping_interval" in params:
            kw["ping_interval"] = self._ping_interval
        if "ping_timeout" in params:
            kw["ping_timeout"] = self._ping_interval
        self._server = _ws_serve(self._handle, self._host, self._port, **kw)

        try:
            self._port = self._server.socket.getsockname()[1]
        except Exception:
            pass
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        try:
            self._server.shutdown()
        except Exception:
            pass

    def _src_of(self, conn) -> str:

        try:
            ra = conn.remote_address
            if isinstance(ra, (tuple, list)) and ra:
                return str(ra[0])
        except Exception:
            pass
        return "?"

    def _admit_conc(self, src) -> bool:

        if self._ip_conns.get(src, 0) >= self._max_conns_per_ip:
            return False
        self._ip_conns[src] = self._ip_conns.get(src, 0) + 1
        return True

    def _admit_rate(self, src, is_register) -> bool:

        if is_register:
            bucket = self._ip_reg_buckets.get(src)
            if bucket is None:
                bucket = self._ip_reg_buckets[src] = _TokenBucket(self._reg_rate_per_ip,
                                                                  self._reg_burst_per_ip)
        else:
            bucket = self._ip_buckets.get(src)
            if bucket is None:
                bucket = self._ip_buckets[src] = _TokenBucket(self._conn_rate_per_ip,
                                                              self._conn_burst_per_ip)
        return bucket.take()

    def _release_ip(self, src):
        with self._lock:
            n = self._ip_conns.get(src, 0) - 1
            if n <= 0:
                self._ip_conns.pop(src, None)

                for d in (self._ip_buckets, self._ip_reg_buckets):
                    b = d.get(src)
                    if b is not None and b.tokens >= b.burst:
                        d.pop(src, None)
            else:
                self._ip_conns[src] = n

    def _origin_ok(self, conn) -> bool:
        if self._allowed_origins is None:
            return True
        try:
            origin = conn.request.headers.get("Origin")
        except Exception:
            origin = None

        return origin is None or origin in self._allowed_origins

    def _handle(self, conn):
        src = self._src_of(conn)
        with self._lock:
            if self._conns >= self._max_conns:
                conn.close()
                return

            if not self._admit_conc(src):
                conn.close(); return
            self._conns += 1
        parked = False
        topic = None
        me = None
        try:
            if not self._origin_ok(conn):
                conn.close(); return

            try:
                raw = conn.recv(timeout=self._handshake_timeout)
            except Exception:
                conn.close(); return
            f = _loads(raw)
            if not f:
                conn.close(); return
            self._see(f)
            t, topic = f.get("t"), f.get("rz")
            if t not in (P.RZ_REGISTER, P.RZ_DIAL) or not isinstance(topic, str) or not topic:
                conn.close(); return
            role = "box" if t == P.RZ_REGISTER else "device"
            is_register = (t == P.RZ_REGISTER)

            with self._lock:
                if not self._admit_rate(src, is_register):
                    self._see({"t": "rate", "rz": topic})
                    conn.close(); return

            me = _Waiter(conn, role, src)
            peer = None
            evicted_stale = None
            parked = False
            with self._lock:
                other = self._waiting.get(topic)
                if other is None:

                    if len(self._ip_topics.get(src, ())) >= self._max_topics_per_ip:
                        conn.close(); return
                    self._waiting[topic] = me
                    self._ip_topics.setdefault(src, set()).add(topic)
                    parked = True
                elif other.role == role:

                    if is_register and _conn_is_dead(other.conn):

                        evicted_stale = other
                        self._untrack_topic(other.src, topic)
                        self._waiting[topic] = me
                        self._ip_topics.setdefault(src, set()).add(topic)
                        parked = True
                    else:

                        self._see({"t": "reject", "rz": topic})
                        conn.close(); return
                else:

                    self._waiting.pop(topic, None)
                    self._untrack_topic(other.src, topic)
                    peer = other
            if evicted_stale is not None:

                evicted_stale.matched.set()
            if peer is not None:

                me.peer = peer.conn
                peer.peer = conn
                peer.matched.set()
                self._one_way(conn, peer.conn)
            else:

                deadline = time.monotonic() + (self._register_park_timeout if role == "box"
                                               else self._unmatched_timeout)
                while not me.matched.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    if me.matched.wait(timeout=min(self._park_slice, remaining)):
                        break

                    if getattr(conn, "close_code", None) is not None:
                        break
                if me.matched.is_set() and me.peer is not None:
                    self._one_way(conn, me.peer)
                else:
                    with self._lock:
                        if self._waiting.get(topic) is me:
                            self._waiting.pop(topic, None)
                            self._untrack_topic(src, topic)
        finally:
            if parked:

                with self._lock:
                    if self._waiting.get(topic) is me:
                        self._waiting.pop(topic, None)
                        self._untrack_topic(src, topic)
            with self._lock:
                self._conns -= 1
            self._release_ip(src)
            try:
                conn.close()
            except Exception:
                pass

    def _untrack_topic(self, src, topic):

        s = self._ip_topics.get(src)
        if s is not None:
            s.discard(topic)
            if not s:
                self._ip_topics.pop(src, None)

    def _one_way(self, src, dst):

        try:
            while True:
                try:
                    raw = src.recv(timeout=self._idle_timeout)
                except TimeoutError:
                    break
                if raw is None:
                    break
                f = _loads(raw)
                if not f:
                    break
                self._see(f)
                if f.get("t") == P.RZ_DATA:
                    try:
                        dst.send(json.dumps({"t": P.RZ_DATA, "rz": f.get("rz"),
                                             "blob": f.get("blob")}))
                    except Exception:
                        break
                elif f.get("t") == P.RZ_BYE:
                    break
        except Exception:
            pass
        finally:
            try:
                dst.close()
            except Exception:
                pass

    def _see(self, f):

        if len(self.seen_frames) < 100000:
            self.seen_frames.append({k: f.get(k) for k in ("t", "rz", "blob")})

def _conn_is_dead(conn) -> bool:

    try:
        if getattr(conn, "close_code", None) is not None:
            return True
    except Exception:
        return True

    try:
        from websockets.protocol import State as _State
        st = getattr(getattr(conn, "protocol", None), "state", None)
        if st is not None and st != _State.OPEN:
            return True
    except Exception:
        pass
    return False

def _loads(raw):
    if raw is None:
        return None
    try:
        return json.loads(raw if isinstance(raw, str) else raw.decode())
    except Exception:
        return None
