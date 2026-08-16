#!/usr/bin/env python3

import os, sys, socket, ssl, time, json, threading, struct, ipaddress

MAX_FRAME = 8 << 20
MAX_STREAMS = 256

POLICY_FILE = os.environ.get("PN_POLICY_FILE", "")
LOG = os.environ.get("PN_NET_BROKER_LOG", "/tmp/pn-net-broker.log")
_pol_cache = {"mtime": 0.0, "val": None}

REQUIRE_TUN = os.environ.get("PN_REQUIRE_TUN", "").strip()

PN_PRINCIPAL = os.environ.get("PN_PRINCIPAL", "owner")
PN_SESSION_CELL = os.environ.get("PN_SESSION_CELL", "")
_RT = os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
_LLMD_SOCK = os.environ.get("PN_LLMD_SOCK", os.path.join(_RT, "pn-llmd.sock"))
ADMIT_POLL_S = float(os.environ.get("PN_ADMIT_POLL_S", "0.25") or 0.25)
ADMIT_MAX_WAIT_S = float(os.environ.get("PN_ADMIT_MAX_WAIT_S", "600") or 600)
_tick_cache = {"mtime": -1.0, "val": frozenset()}
_call_seq = [0]

def _unix_rpc(path, req, timeout=2.0):

    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(timeout)
        c.connect(path)
        c.sendall((json.dumps(req, separators=(",", ":")) + "\n").encode())
        buf = b""
        while b"\n" not in buf and len(buf) < (1 << 20):
            d = c.recv(65536)
            if not d:
                break
            buf += d
        c.close()
        return json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace") or "{}")
    except Exception:
        return None

def _ticket_hosts():

    if not POLICY_FILE:
        return frozenset()
    try:
        mt = os.path.getmtime(POLICY_FILE)
    except OSError:
        return frozenset()
    if mt != _tick_cache["mtime"]:
        try:
            d = json.load(open(POLICY_FILE))
            _tick_cache.update(mtime=mt, val=frozenset(
                str(x).lower() for x in (d.get("llm_ticket_hosts") or [])))
        except Exception:
            _tick_cache.update(mtime=mt, val=frozenset())
    return _tick_cache["val"]

def _llm_ticket(host, port):

    key = "%s:%s" % (str(host or "").lower(), port)
    if key not in _ticket_hosts():
        return None
    _call_seq[0] += 1
    cid = "net-%d-%d" % (os.getpid(), _call_seq[0])
    t0 = time.time()
    r = _unix_rpc(_LLMD_SOCK, {"verb": "admit", "id": cid, "cell_principal": PN_PRINCIPAL,
                               "cell": PN_SESSION_CELL or "net", "klass": "interactive", "weight": 1})
    if r is not None and not r.get("granted"):
        log("LLM-TICKET %s HELD pos=%s" % (key, r.get("position")))
        while time.time() - t0 < ADMIT_MAX_WAIT_S:
            time.sleep(ADMIT_POLL_S)
            r = _unix_rpc(_LLMD_SOCK, {"verb": "admit-poll", "id": cid})
            if r is None or r.get("granted"):
                break
    log("LLM-TICKET %s GRANT wait=%dms%s" % (key, (time.time() - t0) * 1000,
                                             " (failopen: llmd still)" if r is None else ""))
    def _rel():
        try:
            _unix_rpc(_LLMD_SOCK, {"verb": "admit-release", "id": cid})
        except Exception:
            pass
    return _rel

def log(m):
    _wer = PN_SESSION_CELL.split("_")[-1][:12] if PN_SESSION_CELL else "?"
    line = "[%.3f] [%s] %s" % (time.time(), _wer, m)
    try:
        open(LOG, "a").write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)

def _policy():

    if not POLICY_FILE:
        g = os.environ.get("PN_NET_GENERAL", "deny")
        h = [x for x in os.environ.get("PN_NET_HOSTS", "").split(",") if x]
        ni = os.environ.get("PN_NET_INTERNAL", "deny")
        dn = [x for x in os.environ.get("PN_NET_DENY", "").split(",") if x]
        return g, frozenset(h), ni, frozenset(dn)
    try:
        mt = os.path.getmtime(POLICY_FILE)
    except OSError:
        return "deny", frozenset(), "deny", frozenset()
    if _pol_cache["val"] is None or mt != _pol_cache["mtime"]:
        try:
            d = json.load(open(POLICY_FILE))
            g = d.get("net_general", "deny")
            h = frozenset(d.get("net_hosts", []) or [])
            ni = d.get("net_internal", "deny")
            dn = frozenset(d.get("net_deny", []) or [])
        except Exception:
            g, h, ni, dn = "deny", frozenset(), "deny", frozenset()
        _pol_cache.update(mtime=mt, val=(g, h, ni, dn))
    return _pol_cache["val"]

def _host_listed(host, hosts, port=None):

    host = (host or "").lower().strip(".")
    for h in hosts:
        h = h.lower().strip(".")
        want_port = None
        if h.count(":") == 1:
            h, _, p = h.rpartition(":")
            want_port = int(p) if p.isdigit() else -1
        if host == h or host.endswith("." + h):
            if want_port is None or (port is not None and int(port) == want_port):
                return True
    return False

def _host_denied(host, deny, port=None):

    host = (host or "").lower().strip(".")
    if not host:
        return None
    for d in deny:
        d = (d or "").lower().strip(".")
        if not d:
            continue
        want_port = None
        if d.count(":") == 1:
            d, _, p = d.rpartition(":")
            want_port = int(p) if p.isdigit() else -1
        if want_port is not None and (port is None or int(port) != want_port):
            continue

        if _ist_ip_literal(d):
            if host == d:
                return d
        elif host == d or host.endswith("." + d):
            return d
    return None

def _ist_ip_literal(s):
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False

def _ip_is_internal(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return (a.is_loopback or a.is_link_local or a.is_private or a.is_multicast
            or a.is_reserved or a.is_unspecified)

def _iface_has_ipv4(nic):

    try:
        import fcntl, struct as _st
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            fcntl.ioctl(s.fileno(), 0x8915, _st.pack("256s", nic[:15].encode()))
            return True
        except OSError:
            return False
        finally:
            s.close()
    except Exception:
        return False

def _tun_up(prefix):

    if not prefix:
        return True
    try:
        for nic in os.listdir("/sys/class/net"):
            if nic.startswith(prefix) and _iface_has_ipv4(nic):
                return True
    except OSError:
        pass
    return False

def _hausnetz_ip(ip):

    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return bool(a.is_private) and not (a.is_loopback or a.is_link_local
                                       or a.is_multicast or a.is_reserved or a.is_unspecified)

def _nur_hausnetz(host, port, hosts, internal, deny):

    if _host_denied(host, deny, port):
        return False
    if internal != "allow" and not _host_listed(host, hosts, port):
        return False
    try:
        ips = [ai[4][0] for ai in socket.getaddrinfo(host, None)]
    except OSError:
        return False
    return bool(ips) and all(_hausnetz_ip(ip) for ip in ips)

def _allowed(host, port=None):

    g, hosts, internal, deny = _policy()
    if REQUIRE_TUN and not _tun_up(REQUIRE_TUN):

        if not _nur_hausnetz(host, port, hosts, internal, deny):
            return False, ("VPN-Tunnel (%s*) nicht aktiv - Egress gesperrt (fail-closed, kein Leck am "
                           "Tunnel vorbei)" % REQUIRE_TUN), None
    getroffen = _host_denied(host, deny, port)
    if getroffen:
        log("DENYLIST %s:%s (Regel %s)" % (host, port, getroffen))
        return False, "auf der Sperrliste dieser Zelle (Regel: %s)" % getroffen, None
    listed = _host_listed(host, hosts, port)
    if listed:
        return True, "explicit net_hosts grant", None
    if g != "allow":
        return False, "net_general=deny and host not in net_hosts", None

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        return False, "resolve failed: %s" % e, None
    ips = [ai[4][0] for ai in infos]
    if internal != "allow" and any(_ip_is_internal(ip) for ip in ips):
        return False, "resolves to an internal address (loopback/LAN) — needs net_hosts or net_internal=allow", None
    return True, ("net_general=allow (UNRESTRICTED incl. internal)" if internal == "allow"
                  else "net_general=allow (public)"), ips[0]

def _pipe(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d:
                break
            b.sendall(d)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

def _recvn(sock, n):
    buf = b""
    while len(buf) < n:
        try:
            d = sock.recv(n - len(buf))
        except OSError:
            return None
        if not d:
            return None
        buf += d
    return buf

def _socks5(client):

    try:
        nm = _recvn(client, 1)
        if not nm:
            client.close(); return
        if nm[0] and _recvn(client, nm[0]) is None:
            client.close(); return
        client.sendall(b"\x05\x00")
        hdr = _recvn(client, 4)
        if not hdr or hdr[0] != 0x05:
            client.close(); return
        cmd, atyp = hdr[1], hdr[3]
        if atyp == 0x01:
            raw = _recvn(client, 4); host = socket.inet_ntoa(raw) if raw else None
        elif atyp == 0x03:
            ln = _recvn(client, 1)
            host = _recvn(client, ln[0]).decode("latin1") if ln else None
        elif atyp == 0x04:
            raw = _recvn(client, 16); host = socket.inet_ntop(socket.AF_INET6, raw) if raw else None
        else:
            client.sendall(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00"); client.close(); return
        pr = _recvn(client, 2)
        if host is None or pr is None:
            client.close(); return
        port = struct.unpack("!H", pr)[0]
        if cmd != 0x01:
            client.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00"); client.close(); return
        ok, why, pin = _allowed(host, port)
        log("SOCKS5 CONNECT %s:%d -> %s (%s)" % (host, port, "ALLOW" if ok else "DENY", why))
        if not ok:
            client.sendall(b"\x05\x02\x00\x01\x00\x00\x00\x00\x00\x00"); client.close(); return
        try:
            up = socket.create_connection((pin or host, port), timeout=30)
        except OSError as e:
            client.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
            log("SOCKS5 upstream fail %s:%d %s" % (host, port, e)); client.close(); return
        client.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        client.settimeout(None); up.settimeout(None)
        t = threading.Thread(target=_pipe, args=(up, client), daemon=True); t.start()
        _pipe(client, up)
        t.join(timeout=1); up.close(); client.close()
    except Exception as e:
        log("SOCKS5_ERR %r" % e)
        try: client.close()
        except OSError: pass

def _ablehnung(why, mit_seite):
    grund = (why or "abgelehnt").replace("\r", " ").replace("\n", " ")[:300]
    kopf = ("HTTP/1.1 403 Forbidden\r\n"
            "X-PN-Deny-Reason: %s\r\n"
            "Connection: close\r\n" % grund)
    if not mit_seite:
        return (kopf + "Content-Length: 0\r\n\r\n").encode("latin1", "replace")
    seite = (
        "<!doctype html><meta charset=utf-8>"
        "<title>Vom Netz-Waechter der Zelle abgelehnt</title>"
        "<h1>Diese Verbindung wurde abgelehnt</h1>"
        "<p><b>Grund:</b> %s</p>"
        "<p>Das ist keine Fehlkonfiguration deines Browsers: die Anfrage hat den "
        "Netz-Waechter der Zelle erreicht, und er hat sie mit dem oben genannten Grund "
        "abgewiesen. Derselbe Grund steht als Kopfzeile <code>X-PN-Deny-Reason</code>.</p>"
        % grund
    ).encode("utf-8")
    return (kopf + "Content-Type: text/html; charset=utf-8\r\n"
            "Content-Length: %d\r\n\r\n" % len(seite)).encode("latin1", "replace") + seite


def handle(client):

    try:
        client.settimeout(60)
        first = client.recv(1)
        if not first:
            client.close(); return
        if first == b"\x05":
            _socks5(client); return
        head = first
        while b"\r\n\r\n" not in head:
            d = client.recv(4096)
            if not d:
                client.close(); return
            head += d
            if len(head) > (1 << 20):
                break
        line0 = head.split(b"\r\n", 1)[0]
        parts = line0.split(b" ")
        if len(parts) < 2:
            client.close(); return
        method = parts[0].decode("latin1").upper()
        tgt = parts[1].decode("latin1")

        if method == "CONNECT":
            host, _, port = tgt.partition(":")
            port = int(port or "443")
            ok, why, pin = _allowed(host, port)
            log("CONNECT %s:%d -> %s (%s)" % (host, port, "ALLOW" if ok else "DENY", why))
            if not ok:
                client.sendall(_ablehnung(why, mit_seite=False))
                client.close(); return
            rel = _llm_ticket(host, port)
            try:
                try:
                    up = socket.create_connection((pin or host, port), timeout=30)
                except OSError as e:
                    client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                    log("CONNECT upstream fail %s:%d %s" % (host, port, e)); client.close(); return
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                client.settimeout(None); up.settimeout(None)
                t = threading.Thread(target=_pipe, args=(up, client), daemon=True); t.start()
                _pipe(client, up)
                t.join(timeout=1)
                up.close(); client.close(); return
            finally:
                if rel:
                    rel()

        if tgt.startswith("http://"):
            rest = tgt[len("http://"):]
            hostport, _, path = rest.partition("/")
            host, _, port = hostport.partition(":")
            port = int(port or "80")
            ok, why, pin = _allowed(host, port)
            log("HTTP %s %s -> %s (%s)" % (method, tgt, "ALLOW" if ok else "DENY", why))
            if not ok:
                client.sendall(_ablehnung(why, mit_seite=(method != "HEAD")))
                client.close(); return
            body = head.replace(line0, ("%s /%s %s" % (method, path, "HTTP/1.1")).encode("latin1"), 1)
            rel = _llm_ticket(host, port)
            try:
                try:
                    up = socket.create_connection((pin or host, port), timeout=30)
                except OSError:
                    client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n"); client.close(); return
                up.sendall(body)
                t = threading.Thread(target=_pipe, args=(up, client), daemon=True); t.start()
                _pipe(client, up)
                t.join(timeout=1); up.close(); client.close(); return
            finally:
                if rel:
                    rel()

        client.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
        client.close()
    except Exception as e:
        log("HANDLE_ERR %r" % e)
        try:
            client.close()
        except OSError:
            pass

def mux_serve(conn):

    HDRM = struct.Struct("!IBI"); DATA, CLOSE = 0, 1
    wlock = threading.Lock()
    streams = {}

    def send_frame(sid, typ, payload=b""):
        with wlock:
            conn.sendall(HDRM.pack(sid, typ, len(payload)) + payload)

    def out_forwarder(sid, a):
        while True:
            try:
                d = a.recv(65536)
            except OSError:
                break
            if not d:
                break
            send_frame(sid, DATA, d)
        send_frame(sid, CLOSE)
        try:
            a.close()
        except OSError:
            pass
        streams.pop(sid, None)

    buf = b""
    while True:
        while len(buf) < HDRM.size:
            d = conn.recv(65536)
            if not d:
                return
            buf += d
        sid, typ, ln = HDRM.unpack(buf[:HDRM.size]); buf = buf[HDRM.size:]
        if ln > MAX_FRAME:
            log("OVERSIZE frame ln=%d (sid=%d) — closing mux to protect the host" % (ln, sid))
            return
        while len(buf) < ln:
            d = conn.recv(65536)
            if not d:
                return
            buf += d
        payload = buf[:ln]; buf = buf[ln:]
        if typ == DATA:
            a = streams.get(sid)
            if a is None:
                if len(streams) >= MAX_STREAMS:
                    log("STREAM_CAP %d reached — dropping sid=%d" % (MAX_STREAMS, sid))
                    continue
                a, b = socket.socketpair()
                streams[sid] = a
                threading.Thread(target=handle, args=(b,), daemon=True).start()
                threading.Thread(target=out_forwarder, args=(sid, a), daemon=True).start()
            try:
                a.sendall(payload)
            except OSError:
                pass
        elif typ == CLOSE:
            a = streams.get(sid)
            if a:
                try:
                    a.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--tcp"
    g, hosts, _ni, _dn = _policy()
    if mode == "--tcp":
        hostport = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1:8888"
        host, port = hostport.split(":")
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, int(port))); srv.listen(16)
        log("NET_BROKER_TCP %s (net_general=%s hosts=%d require_tun=%s)"
            % (hostport, g, len(hosts), REQUIRE_TUN or "-"))
        while True:
            c, _ = srv.accept()
            threading.Thread(target=handle, args=(c,), daemon=True).start()
    elif mode == "--unix-mux":
        sock = sys.argv[2]
        if os.path.exists(sock):
            os.unlink(sock)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock); srv.listen(8)
        log("NET_BROKER_UNIX_MUX %s (governed forward-proxy; net_general=%s hosts=%d require_tun=%s)"
            % (sock, g, len(hosts), REQUIRE_TUN or "-"))

        def _serve(conn):
            try:
                mux_serve(conn)
            except Exception as e:
                log("MUX_SERVE_ERR %r" % e)
            finally:
                try: conn.close()
                except OSError: pass
                log("NET_BROKER_UNIX_MUX_CONN_DONE (still listening)")
        while True:
            try:
                conn, _ = srv.accept()
            except OSError as e:
                log("ACCEPT_ERR %r" % e); time.sleep(0.2); continue
            threading.Thread(target=_serve, args=(conn,), daemon=True).start()

if __name__ == "__main__":
    main()
