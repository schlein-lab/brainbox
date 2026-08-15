
import os, json, socket, select, signal, subprocess, time
import re, ssl, threading, mimetypes
import urllib.parse, urllib.request
import pwd

HOME = os.path.expanduser("~")
DATA_DIR = os.path.join(HOME, ".local", "share", "brainbox-portal")
DEFAULT_PRINCIPAL = "owner"
ATTACH_DIR = os.path.join(DATA_DIR, "attachments")

_load_cfg = None
_NETNS_ASKPASS = None
_URL_RE = None
_cockpit_inner = None
_cockpit_policy_enf = None
_ensure_cell_dbus = None
_ensure_netns_askpass = None
_hpc_netns_status = None
_last_claude_session = None
_portal_base_url = None
_prov_log = None
_session_store = None
_traceback_log = None
_voice_agent_token = None
_voice_persona = None
_voice_policy_enf = None
_voice_session_for = None
_sesscell_reg = None
cell = None
links_add = None
tmux_session = None

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

def _recvall(conn, n):
    buf = b""
    while len(buf) < n:
        c = conn.recv(n - len(buf))
        if not c:
            return None
        buf += c
    return buf

WS_MAX_PAYLOAD = 8 << 20
def ws_recv(conn):

    h = _recvall(conn, 2)
    if not h:
        return None
    b1, b2 = h[0], h[1]
    opcode = b1 & 0x0f
    ln = b2 & 0x7f
    if ln == 126:
        ext = _recvall(conn, 2); ln = int.from_bytes(ext, "big") if ext else 0
    elif ln == 127:
        ext = _recvall(conn, 8); ln = int.from_bytes(ext, "big") if ext else 0
    if ln > WS_MAX_PAYLOAD:
        return None
    mask = _recvall(conn, 4) if (b2 & 0x80) else b"\0\0\0\0"
    data = _recvall(conn, ln) if ln else b""
    if data is None:
        return None
    if b2 & 0x80:
        data = bytes(d ^ mask[i % 4] for i, d in enumerate(data))
    if opcode == 0x8:
        return None
    if opcode == 0x9:
        try: ws_send(conn, data, 0xA)
        except Exception: pass
        return b""
    return data

def ws_send(conn, data, opcode=0x1):
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    hdr = bytes([0x80 | opcode])
    n = len(data)
    if n < 126:
        hdr += bytes([n])
    elif n < 65536:
        hdr += bytes([126]) + n.to_bytes(2, "big")
    else:
        hdr += bytes([127]) + n.to_bytes(8, "big")
    conn.sendall(hdr + data)

HOSTSHELL_GONE_REASON = ("Host-Shell entfernt — Arbeit läuft in Session-Zellen, "
                         "Box-Verwaltung per SSH.")
HOSTSHELL_GONE_TERM = (
    "[Host-Shell abgeschaltet.\r\n"
    " Diese Box führt jede Sitzung in ihrer eigenen microVM-Zelle aus — ein Terminal direkt auf dem\r\n"
    " Host ist damit nicht mehr vorgesehen.\r\n"
    " Arbeiten: Reiter „Sessions“ — dort hat jede Sitzung ihr eigenes Terminal, in der Zelle.\r\n"
    " Box verwalten: per SSH auf die Box.]")

AGENT_FAILED_TERM = (
    "[Der Agent konnte in dieser Sitzung nicht starten.\n"
    " Diese Box gibt statt eines Agenten NIE eine Kommandozeile aus - deshalb bleibt dieses Fenster\n"
    " jetzt leer stehen, statt Ihnen eine Shell auf der Box zu oeffnen.\n"
    " Am ehesten hilft: die Sitzung in ihrer eigenen Zelle oeffnen (Reiter „Sessions“).\n"
    " Bleibt es dabei, ist die Box nicht angemeldet oder der Agent defekt - beides sehen Sie im\n"
    " Portal-Log; verwalten laesst sich die Box per SSH.]")
AGENT_FAILED_HOLD_S = 3600
_AGENT_FAILED_FILE = os.path.join(DATA_DIR, "agent-start-failed.txt")

def _agent_failed_tail():

    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(_AGENT_FAILED_FILE, "w", encoding="utf-8") as f:
            f.write(AGENT_FAILED_TERM + "\n")
        if re.match(r"^[A-Za-z0-9_./-]+$", _AGENT_FAILED_FILE):
            return "{ cat %s; exec sleep %d; }" % (_AGENT_FAILED_FILE, AGENT_FAILED_HOLD_S)
    except Exception:
        pass
    return "exec sleep %d" % AGENT_FAILED_HOLD_S

def ws_refuse_hostshell(conn):

    return ws_close_with_reason(conn, WS_CLOSE_UNAVAILABLE, HOSTSHELL_GONE_REASON,
                                term_line=HOSTSHELL_GONE_TERM)

NO_SESSION_REASON = ("Terminal ohne Sitzung — jede Sitzung laeuft in ihrer eigenen Zelle. "
                     "Bitte im Reiter „Sessions“ eine oeffnen.")
NO_SESSION_TERM = (
    "[Terminal ohne Sitzung.\r\n"
    " Diese Box fuehrt jede Sitzung in ihrer eigenen microVM-Zelle aus — ein Terminal gehoert\r\n"
    " deshalb immer zu genau einer Sitzung. Ohne Sitzungs-ID bliebe nur eine Kommandozeile auf\r\n"
    " der Box selbst, und die gibt diese Box nicht aus.\r\n"
    " Naechster Schritt: Reiter „Sessions“ — dort eine Sitzung oeffnen oder neu anlegen; ihr\r\n"
    " Terminal laeuft dann in ihrer eigenen Zelle.]")

def ws_refuse_no_session(conn):

    return ws_close_with_reason(conn, 4003, NO_SESSION_REASON, term_line=NO_SESSION_TERM)

def ws_terminal(conn, target=None, principal="owner", sid=None, cell=True):

    import pty
    target = target or os.environ.get("PHANTOM_PORTAL_TARGET", "cockpit")

    if target != "cockpit":
        return ws_refuse_hostshell(conn)
    kind = "cockpit"
    sess = tmux_session(principal, kind)

    _sid = sid if (sid and re.match(r'^[a-z0-9]{6,16}$', sid)) else None
    if _sid:
        try:
            _st = _session_store(principal, kind)
            _s = _st.get(_sid) or _st.create(sid=_sid)
            _st.touch(_sid)
            sess = _s['tmux']
        except Exception:
            _sid = None

    if kind == "cockpit" and not _sid:
        return ws_refuse_no_session(conn)
    if kind == "cockpit" and _sid:
        try:
            _live_tmux = subprocess.run(["tmux", "has-session", "-t", sess], capture_output=True).returncode == 0
        except Exception:
            _live_tmux = False

        if not _live_tmux:
            return ws_cell_terminal(conn, principal, _sid)

    _busaddr = _ensure_cell_dbus(principal)
    _lbin = os.path.expanduser("~/.local/bin")
    try:
        for k, v in (("BROWSER", "phantom-open"), ("DBUS_SESSION_BUS_ADDRESS", _busaddr),
                     ("PATH", _lbin + ":" + os.environ.get("PATH", ""))):
            subprocess.run(["tmux", "setenv", "-t", sess, k, v], capture_output=True)
        subprocess.run(["tmux", "set-option", "-g", "history-limit", "100000"], capture_output=True)

        have_new = subprocess.run(["tmux", "has-session", "-t", sess], capture_output=True).returncode == 0
        have_old = subprocess.run(["tmux", "has-session", "-t", kind], capture_output=True).returncode == 0
        if (not have_new) and have_old and sess != kind and not _sid and principal == DEFAULT_PRINCIPAL:
            subprocess.run(["tmux", "rename-session", "-t", kind, sess], capture_output=True)
    except Exception:
        pass

    _rid = _last_claude_session(sess)
    _eq = _cockpit_inner(principal, _sid, sess)
    if _eq:

        _inner = "%s || %s" % (_eq, _agent_failed_tail())
    else:
        _hold = _agent_failed_tail()
        _inner = (("claude --resume %s || claude || %s" % (_rid, _hold)) if _rid
                  else ("claude || %s" % _hold))
    newcmd = 'tmux new -s %s "bash -lc \'%s\'"' % (sess, _inner)
    attach = 'tmux attach -t %s || %s' % (sess, newcmd)
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = _busaddr
        os.environ["BROWSER"] = "phantom-open"
        os.environ["PATH"] = _lbin + ":" + os.environ.get("PATH", "")
        os.execvp("bash", ["bash", "-lc", attach])
        os._exit(1)
    alive = {"v": True}

    _utail = {"b": b""}
    def _scan_urls(chunk):
        try:
            data = _utail["b"] + chunk
            for m in _URL_RE.finditer(data):
                links_add(principal, m.group(0).decode("utf-8", "replace"), "terminal")
            _utail["b"] = data[-256:]
        except Exception:
            pass

    def pty_to_ws():
        while alive["v"]:
            try:
                out = os.read(fd, 65536)
            except OSError:
                break
            if not out:
                break
            _scan_urls(out)
            try:
                ws_send(conn, out, 0x2)
            except Exception:
                break
        alive["v"] = False
    t = threading.Thread(target=pty_to_ws, daemon=True)
    t.start()
    try:
        while alive["v"]:
            msg = ws_recv(conn)
            if msg is None:
                break
            if msg[:1] == b"{":
                try:
                    j = json.loads(msg)
                    if j.get("t") == "r":
                        ws_send_resize(fd, j.get("rows", 24), j.get("cols", 80))
                        continue
                except Exception:
                    pass
            try:
                os.write(fd, msg)
            except OSError:
                break
    finally:
        alive["v"] = False
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass

def ws_send_resize(fd, rows, cols):
    import fcntl, termios, struct
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", int(rows), int(cols), 0, 0))
    except Exception:
        pass

_HKEY_ASKPASS = "/tmp/.pnvpn-hkey-ap.sh"

def _hpc_ssh_target():

    try:
        cfg = dict(_load_cfg() or {}) if callable(_load_cfg) else {}
    except Exception:
        cfg = {}
    return (str(cfg.get("hpc_ssh_target") or "").strip()
            or os.environ.get("HPC_SSH_TARGET", "").strip()
            or os.environ.get("PN_HPC_HOST", "").strip()
            or os.environ.get("PN_HPC_HOST", "").strip()
            or "hpc-front1")

def ws_hpc_terminal(conn, principal):

    import pty
    uid = principal
    nst = _hpc_netns_status(uid)
    ns = nst.get("ns")
    if not (nst.get("connected") and ns):
        try: ws_send(conn, b"\r\n[Kein aktiver VPN-Tunnel. Erst die Session per 'VPN verbinden' + 2FA verbinden.]\r\n", 0x2)
        except Exception: pass
        return
    _ensure_netns_askpass()
    try:
        with open(_HKEY_ASKPASS, "w") as f:
            f.write("#!/bin/bash\n%s/.local/bin/phantom secret get hpc_key_pass\n" % HOME)
        os.chmod(_HKEY_ASKPASS, 0o755)
    except OSError:
        pass

    _owner = pwd.getpwuid(os.getuid()).pw_name
    launch = ("SUDO_ASKPASS=%s sudo -A ip netns exec %s runuser -u %s -- "
              "env HOME=%s TERM=xterm-256color SSH_ASKPASS=%s SSH_ASKPASS_REQUIRE=force DISPLAY= "
              "ssh -tt -F %s/.ssh/config -o StrictHostKeyChecking=accept-new "
              "-o ConnectTimeout=25 -o ServerAliveInterval=20 %s"
              % (_NETNS_ASKPASS, ns, _owner, HOME, _HKEY_ASKPASS, HOME,
                 _hpc_ssh_target()))
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.execvp("bash", ["bash", "-lc", launch])
        os._exit(1)
    try: ws_send(conn, ("\r\n\x1b[36m[Verbinde zum HPC-Cluster über %s …]\x1b[0m\r\n" % ns).encode(), 0x2)
    except Exception: pass
    alive = {"v": True}
    def pty_to_ws():
        while alive["v"]:
            try:
                out = os.read(fd, 65536)
            except OSError:
                break
            if not out:
                break
            try:
                ws_send(conn, out, 0x2)
            except Exception:
                break
        alive["v"] = False
    t = threading.Thread(target=pty_to_ws, daemon=True); t.start()
    try:
        while alive["v"]:
            msg = ws_recv(conn)
            if msg is None:
                break
            if msg[:1] == b"{":
                try:
                    j = json.loads(msg)
                    if j.get("t") == "r":
                        ws_send_resize(fd, j.get("rows", 24), j.get("cols", 80)); continue
                except Exception:
                    pass
            try:
                os.write(fd, msg)
            except OSError:
                break
    finally:
        alive["v"] = False
        try: os.kill(pid, signal.SIGTERM)
        except Exception: pass

_CELL_TERM_ATTACHED = {}
_CELL_TERM_LK = threading.Lock()

def kick_attached(key, code=4002, msg=b"[Session-VM reagiert nicht - starte neu ...]", reason=None):

    import struct as _struct
    with _CELL_TERM_LK:
        c = _CELL_TERM_ATTACHED.get(key)
    if c is None or isinstance(c, _HolderConn):
        return False
    if reason is None:

        reason = (msg or b"").strip().strip(b"[]")
    try: ws_send(c, b"\r\n" + (msg or b"") + b"\r\n", 0x2)
    except Exception: pass
    _r = reason if isinstance(reason, bytes) else str(reason).encode("utf-8", "replace")
    while len(_r) > 123:
        _r = _r[:-1]
        while _r and (_r[-1] & 0xC0) == 0x80:
            _r = _r[:-1]
    try: ws_send(c, _struct.pack(">H", int(code)) + _r, 0x8)
    except Exception: pass
    try: c.shutdown(socket.SHUT_RDWR)
    except Exception: pass
    try: c.close()
    except Exception: pass
    return True

WS_CLOSE_UNAVAILABLE = 4004

def ws_close_with_reason(conn, code, reason, term_line=None):

    import struct as _struct
    txt = term_line if term_line is not None else ("[%s]" % reason)
    try: ws_send(conn, ("\r\n" + txt + "\r\n").encode("utf-8", "replace"), 0x2)
    except Exception: pass
    r = (reason or "").encode("utf-8", "replace")
    while len(r) > 123:
        reason = reason[:-1]
        r = reason.encode("utf-8", "replace")
    try: ws_send(conn, _struct.pack(">H", int(code)) + r, 0x8)
    except Exception: pass
    try: conn.shutdown(socket.SHUT_RDWR)
    except Exception: pass
    return True

def ws_refuse(conn, reason, code=WS_CLOSE_UNAVAILABLE):

    return ws_close_with_reason(conn, code, reason)

def _cells_state():

    v = os.environ.get("CELLS_ENABLED")
    src = "env"
    if v is None:
        for _p in ("/etc/brainbox/caps.env", "/etc/brainbox/site.conf"):
            try:
                for ln in open(_p):
                    ln = ln.strip()
                    if ln.startswith("CELLS_ENABLED="):
                        v = ln.split("=", 1)[1].split("#", 1)[0].strip().strip(chr(34) + chr(39))
                        src = _p; break
            except Exception:
                continue
            if v is not None:
                break
    if v is None:
        return True, ""
    if str(v).strip().lower() not in ("0", "false", "no", "off"):
        return True, ""
    return False, ("Sitzungen deaktiviert (keine Hardware-Virtualisierung) [%s: CELLS_ENABLED=%s]"
                   % (src, v))

def _cells_enabled():
    return _cells_state()[0]

_CELLS_UNAVAIL_MSG = ("\r\n[Session-Zellen (microVM-Isolation) sind auf dieser Box nicht verfuegbar - "
                      "keine Hardware-Virtualisierung (/dev/kvm fehlt). Laeuft die Box in einer VM, im "
                      "Hypervisor die verschachtelte Virtualisierung (nested virt) aktivieren. Portal, "
                      "Sprache, LLM und Dateien funktionieren weiter.]\r\n")

def ws_cell_terminal(conn, principal, sid):

    import select, struct as _struct
    _is_voice = (sid == "__voice__")
    if _is_voice:
        sid = _voice_session_for(principal)
    _cok, _creason = _cells_state()
    if not _cok:

        ws_close_with_reason(conn, WS_CLOSE_UNAVAILABLE,
                             _creason or "Sitzungen deaktiviert (keine Hardware-Virtualisierung)",
                             term_line=_CELLS_UNAVAIL_MSG.strip())
        return
    key = (principal, sid)
    with _CELL_TERM_LK:
        _old = _CELL_TERM_ATTACHED.get(key)
        _CELL_TERM_ATTACHED[key] = conn
    if _old is not None and _old is not conn:

        try: _peer = "%s:%s" % conn.getpeername()[:2]
        except Exception: _peer = "?"
        try: open("/tmp/ws_cell.log", "a").write("%.1f TAKEOVER %s/%s by %s\n" % (time.time(), principal, sid, _peer))
        except Exception: pass
        try: ws_send(_old, b"\r\n[Session wurde auf einem anderen Screen uebernommen]\r\n", 0x2)
        except Exception: pass
        try: ws_send(_old, _struct.pack(">H", 4001) + b"taken-over", 0x8)
        except Exception: pass
        try: _old.shutdown(socket.SHUT_RDWR)
        except Exception: pass
        try: _old.close()
        except Exception: pass
    up = None
    try:
        try:
            import pn_cell_session as _cs
        except Exception as _e:
            _why = ("%s: %s" % (type(_e).__name__, _e)).strip()
            ws_refuse(conn, "Sitzungs-Zellen nicht verfuegbar: " + _why)
            return

        try: ws_send(conn, b"\r\n[Session startet - die microVM wird gebootet ...]\r\n", 0x2)
        except Exception: pass
        try:
            cell = _cs.get_manager().ensure(principal, sid, portal_url=_portal_base_url(),
                                            portal_token=_voice_agent_token(principal),
                                            policy=(_voice_policy_enf(principal) if _is_voice
                                                    else _cockpit_policy_enf(principal, sid)))
        except Exception as _e:

            _traceback_log("cell terminal ensure")
            _why = ("%s: %s" % (type(_e).__name__, _e)).strip() or "unbekannter Fehler"
            ws_close_with_reason(conn, 4003, "microVM konnte nicht starten: " + _why,
                                 term_line="[microVM konnte nicht starten: %s]" % _why)
            return
        if not (cell and cell.alive()):
            _reason = ""
            try:
                _ad = getattr(cell, "_admit_denied", None) if cell is not None else None
                if _ad: _reason = str((_ad or {}).get("reason") or "")
            except Exception:
                _reason = ""
            if not _reason:

                try:
                    _bd = getattr(cell, "_boot_denied", None) if cell is not None else None
                    if _bd: _reason = str(_bd)
                except Exception:
                    pass
            if not _reason:

                try:
                    if cell is None:
                        _reason = "Zelle konnte nicht angelegt werden"
                    else:
                        _p = getattr(cell, "proc", None)
                        if _p is None:
                            _reason = "VM-Prozess wurde nicht gestartet (pn-vmm fehlt oder KVM nicht verfuegbar)"
                        elif _p.poll() is not None:
                            _reason = "VM-Prozess sofort beendet (exit %s)" % _p.poll()
                        elif getattr(cell, "conn", None) is None:
                            _reason = "VM-Steuerkanal (vsock) nicht verbunden"
                        else:
                            _reason = "microVM ist nicht am Leben"
                except Exception:
                    _reason = "microVM ist nicht am Leben"
            ws_close_with_reason(conn, 4003, "Start verweigert: " + _reason,
                                 term_line="[Start verweigert: %s]" % _reason)
            return

        def _bump_active():
            try:
                _r = _sesscell_reg() if _sesscell_reg else None
                if _r is not None: _r.attach(principal, sid)
            except Exception: pass
        _bump_active(); _next_bump = [time.time() + 300.0]
        try:
            _pm0 = __import__("portal_metasessions")
            _sys = _voice_persona(principal) if _is_voice else _pm0._cockpit_cell_brief(principal, sid)
        except Exception:
            _sys = _voice_persona(principal) if _is_voice else None
        _stok = cell.start_terminal(system=_sys)
        up = cell.term_conn
        def _dbg(m):
            try: open("/tmp/ws_cell.log","a").write("%.1f %s\n" % (time.time(), m))
            except Exception: pass
        _dbg("ensure alive=%s cid=%s start_terminal=%s term_conn=%s" % (cell.alive(), cell.cid, _stok, up is not None))
        if up is None:
            ws_close_with_reason(conn, 4003,
                                 "Terminal-Lane nicht verbunden (start_terminal=%s, cid=%s)" % (_stok, cell.cid),
                                 term_line="[Terminal-Lane nicht verbunden]")
            return
        up.setblocking(False)
        RS = b"\xff\xfaWSZ"
        _rx=[0]; _tx=[0]
        while True:
            if time.time() >= _next_bump[0]:
                _next_bump[0] = time.time() + 300.0; _bump_active()
            try:
                pend = conn.pending() > 0
            except Exception:
                pend = False
            if pend:
                rl = [conn]
            else:
                try:
                    rl, _, _ = select.select([up, conn], [], [], 30.0)
                except Exception:
                    break
                if not rl:
                    try: ws_send(conn, b"", 0x9)
                    except Exception: break
                    continue

            if up in rl:
                broke = False
                while True:
                    try:
                        data = up.recv(65536)
                    except (BlockingIOError, ssl.SSLWantReadError):
                        break
                    except Exception:
                        broke = True; break
                    if data == b"":

                        try: ws_send(conn, b"\r\n[Session-VM startet neu - verbinde gleich neu ...]\r\n", 0x2)
                        except Exception: pass
                        try: ws_send(conn, _struct.pack(">H", 4002) + b"cell-restart", 0x8)
                        except Exception: pass
                        broke = True; break
                    _rx[0]+=len(data)
                    _watch_fan(key, data)
                    try:
                        ws_send(conn, data, 0x2)
                    except Exception:
                        broke = True; break
                if broke:
                    break

            if (conn in rl) or pend:
                try:
                    msg = ws_recv(conn)
                except Exception:
                    break
                if msg is None:
                    break
                if not msg:
                    continue
                if msg[:1] == b"{":
                    try:
                        j = json.loads(msg)
                        if j.get("t") == "r":
                            _wr, _wc = int(j.get("rows", 40)) & 0xffff, int(j.get("cols", 120)) & 0xffff
                            _CELL_TERM_SIZE[key] = (_wr, _wc)
                            _watch_fan(key, (_wr, _wc))
                            up.sendall(RS + _struct.pack(">HH", _wr, _wc))
                            continue
                    except Exception:
                        pass
                try:
                    up.sendall(msg); _tx[0]+=len(msg)
                except Exception:
                    break
    finally:
        try: _dbg("loop exit rx=%d tx=%d" % (_rx[0], _tx[0]))
        except Exception: pass

        with _CELL_TERM_LK:
            _mine = _CELL_TERM_ATTACHED.get(key) is conn
            if _mine:
                del _CELL_TERM_ATTACHED[key]
        if _mine:
            try:
                _c = locals().get('cell')
                if _c is not None: _c.sync()
            except Exception:
                pass
        if _mine:
            _ensure_cell_holder(key[0], key[1])

_CELL_TERM_WATCH = {}
_CELL_TERM_SIZE = {}
_CELL_TERM_RING = {}
_CELL_TERM_REPAINT = {}
RING_MAX = 128 * 1024

def _watch_fan(key, item):

    if isinstance(item, (bytes, bytearray)) and item:
        with _CELL_TERM_LK:
            r = _CELL_TERM_RING.get(key)
            if r is None:
                r = bytearray(); _CELL_TERM_RING[key] = r
            r += item
            if len(r) > RING_MAX:
                del r[:len(r) - RING_MAX]
    ws = _CELL_TERM_WATCH.get(key)
    if not ws:
        return
    for qid, q in list(ws.items()):
        try:
            q.put_nowait(item)
        except Exception:

            try:
                while True: q.get_nowait()
            except Exception:
                pass
            try: q.put_nowait(None)
            except Exception: pass

class _HolderConn:

    def __init__(self):
        self.dead = threading.Event()
    def sendall(self, *a, **k):
        raise OSError("holder has no socket")
    def shutdown(self, *a, **k):
        self.dead.set()
    def close(self):
        self.dead.set()

def _ensure_cell_holder(principal, sid):
    with _CELL_TERM_LK:
        if _CELL_TERM_ATTACHED.get((principal, sid)) is not None:
            return
    threading.Thread(target=_cell_holder_pump, args=(principal, sid), daemon=True,
                     name="term-holder-%s" % sid).start()

def _cell_holder_pump(principal, sid):
    import select as _sel, struct as _struct
    key = (principal, sid)
    hc = _HolderConn()
    with _CELL_TERM_LK:
        if _CELL_TERM_ATTACHED.get(key) is not None:
            return
        _CELL_TERM_ATTACHED[key] = hc
    try:
        try:
            import pn_cell_session as _cs
        except Exception:
            return
        try:
            cell = _cs.get_manager().ensure(principal, sid, portal_url=_portal_base_url(),
                                            portal_token=_voice_agent_token(principal),
                                            policy=_cockpit_policy_enf(principal, sid))
        except Exception:
            return
        if not (cell and cell.alive()):
            return
        cell.start_terminal()
        up = cell.term_conn
        if up is None:
            return
        up.setblocking(False)

        def _wiggle():
            rows, cols = _CELL_TERM_SIZE.get(key) or (36, 120)

            if rows < 24 or cols < 80:

                _tv = os.environ.get("PN_TV_TERM_SIZE", "40x132")
                try:
                    _tr, _tc = (int(x) for x in _tv.lower().split("x", 1))
                except Exception:
                    _tr, _tc = 40, 132
                rows, cols = max(_tr, 24), max(_tc, 80)
                _CELL_TERM_SIZE[key] = (rows, cols)
            RS = b"\xff\xfaWSZ"
            try:
                up.sendall(RS + _struct.pack(">HH", rows, max(20, cols - 1)))
                up.sendall(RS + _struct.pack(">HH", rows, cols))
                _watch_fan(key, (rows, cols))
            except Exception:
                pass
        _wiggle()
        while not hc.dead.is_set():
            _ev = _CELL_TERM_REPAINT.get(key)
            if _ev is not None and _ev.is_set():
                _ev.clear(); _wiggle()
            with _CELL_TERM_LK:
                if _CELL_TERM_ATTACHED.get(key) is not hc:
                    break

            try:
                rl, _, _ = _sel.select([up], [], [], 1.0)
            except Exception:
                break
            if not rl:
                continue
            broke = False
            while True:
                try:
                    data = up.recv(65536)
                except (BlockingIOError, ssl.SSLWantReadError):
                    break
                except Exception:
                    broke = True; break
                if data == b"":
                    broke = True; break
                _watch_fan(key, data)
            if broke:
                break
    finally:
        with _CELL_TERM_LK:
            if _CELL_TERM_ATTACHED.get(key) is hc:
                del _CELL_TERM_ATTACHED[key]

def ws_cell_terminal_watch(conn, principal, sid):

    import queue as _qu
    if sid == "__voice__":
        sid = _voice_session_for(principal)
    _cok, _creason = _cells_state()
    if not _cok:

        ws_close_with_reason(conn, WS_CLOSE_UNAVAILABLE,
                             _creason or "Sitzungen deaktiviert (keine Hardware-Virtualisierung)",
                             term_line=_CELLS_UNAVAIL_MSG.strip())
        return
    key = (principal, sid)
    q = _qu.Queue(maxsize=512)
    qid = id(q)
    with _CELL_TERM_LK:
        _CELL_TERM_WATCH.setdefault(key, {})[qid] = q
    try:
        _ensure_cell_holder(principal, sid)
        rc = _CELL_TERM_SIZE.get(key)
        if rc:
            try: ws_send(conn, json.dumps({"t": "wsz", "rows": rc[0], "cols": rc[1]}).encode(), 0x1)
            except Exception: return

        _ev = _CELL_TERM_REPAINT.get(key)
        if _ev is None:
            _ev = threading.Event(); _CELL_TERM_REPAINT[key] = _ev
        _ev.set()
        with _CELL_TERM_LK:
            _ring0 = bytes(_CELL_TERM_RING.get(key) or b"")
        if _ring0:
            try: ws_send(conn, _ring0, 0x2)
            except Exception: return
        while True:
            try:
                item = q.get(timeout=15.0)
            except _qu.Empty:
                try: ws_send(conn, b"", 0x9)
                except Exception: break
                continue
            if item is None:
                break
            try:
                if isinstance(item, tuple):
                    ws_send(conn, json.dumps({"t": "wsz", "rows": item[0], "cols": item[1]}).encode(), 0x1)
                else:
                    ws_send(conn, item, 0x2)
            except Exception:
                break
    finally:
        with _CELL_TERM_LK:
            ws = _CELL_TERM_WATCH.get(key)
            if ws is not None:
                ws.pop(qid, None)
                if not ws:
                    del _CELL_TERM_WATCH[key]

def ws_cell_terminal_input(conn, principal, sid):

    if sid == "__voice__":
        sid = _voice_session_for(principal)
    _cok, _creason = _cells_state()
    if not _cok:

        ws_close_with_reason(conn, WS_CLOSE_UNAVAILABLE,
                             _creason or "Sitzungen deaktiviert (keine Hardware-Virtualisierung)",
                             term_line=_CELLS_UNAVAIL_MSG.strip())
        return
    try:
        import pn_cell_session as _cs
    except Exception:
        return
    try:
        cell = _cs.get_manager().ensure(principal, sid, portal_url=_portal_base_url(),
                                        portal_token=_voice_agent_token(principal),
                                        policy=_cockpit_policy_enf(principal, sid))
    except Exception:
        _traceback_log("term input ensure")
        return
    if not (cell and cell.alive()):
        return
    cell.start_terminal()
    up = cell.term_conn
    if up is None:
        return
    try: _prov_log("term.input.connect", principal, sid, {"wire": "ws"})
    except Exception: pass
    while True:
        try:
            msg = ws_recv(conn)
        except Exception:
            break
        if msg is None:
            break
        if not msg:
            continue
        if msg[:1] == b"{":
            try:
                if json.loads(msg).get("t") == "r":
                    continue
            except Exception:
                pass
        try:
            up.sendall(msg)
        except Exception:
            break

_ATTACH_OWNERS = os.path.join(ATTACH_DIR, ".owners.json")
_ATTACH_LOCK = threading.Lock()

def _attach_owner_map():
    try:
        with open(_ATTACH_OWNERS) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _attach_set_owner(name, principal):

    with _ATTACH_LOCK:
        d = _attach_owner_map()
        d[os.path.basename(name)] = str(principal)
        os.makedirs(ATTACH_DIR, exist_ok=True)
        tmp = _ATTACH_OWNERS + ".tmp.%d" % os.getpid()
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, _ATTACH_OWNERS)

def _attach_owner(name):
    return _attach_owner_map().get(os.path.basename(name))

def list_attachments():
    out = []
    try:
        for f in sorted(os.listdir(ATTACH_DIR), reverse=True):
            if f.startswith("."):
                continue
            full = os.path.join(ATTACH_DIR, f)
            if os.path.isfile(full):
                out.append({"name": f, "url": "attach/" + urllib.parse.quote(f),
                            "path": full, "size": os.path.getsize(full),
                            "type": mimetypes.guess_type(f)[0] or "application/octet-stream"})
    except FileNotFoundError:
        pass
    return out[:200]

def attachments_for(principal, is_admin=False):

    if is_admin:
        return list_attachments()
    return [a for a in list_attachments() if _attach_owner(a["name"]) == principal]

def _filter_owned_attachments(attachments, principal, is_admin=False):

    out = []
    for a in (attachments or []):
        nm = a.get("name") if isinstance(a, dict) else a
        if isinstance(nm, str) and (is_admin or _attach_owner(nm) == principal):
            out.append(a)
    return out

def cockpit_paste(text, target):

    if not text or not target:
        return False
    try:
        if subprocess.run(["tmux", "has-session", "-t", target],
                          capture_output=True).returncode != 0:
            return False
        return subprocess.run(["tmux", "send-keys", "-t", target, "-l", text],
                              capture_output=True).returncode == 0
    except Exception:
        return False

def ws_feed(conn, room):

    feed = os.path.join(HOME, ".local", "state", "phantom-rooms", room, "feed.log")
    off = 0
    try:
        with open(feed) as f:
            data = f.read(); off = f.tell()
        ws_send(conn, data[-6000:])
    except Exception:
        pass
    idle = 0
    while True:
        time.sleep(1.0)
        chunk = ""
        try:
            with open(feed) as f:
                f.seek(off); chunk = f.read(); off = f.tell()
        except Exception:
            pass
        try:
            if chunk:
                ws_send(conn, chunk); idle = 0
            else:
                idle += 1
                if idle >= 5:
                    ws_send(conn, b"", 0x9); idle = 0
        except Exception:
            break

_RFB_BRIDGES = {}
_RFB_BRIDGE_LK = threading.Lock()
def _rfb_tcp_bridge(unix_path):

    with _RFB_BRIDGE_LK:
        if unix_path in _RFB_BRIDGES:
            return _RFB_BRIDGES[unix_path]
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", 0)); srv.listen(8)
            port = srv.getsockname()[1]
        except OSError:
            return None
        def _splice(a, b):
            try:
                while True:
                    d = a.recv(65536)
                    if not d: break
                    b.sendall(d)
            except OSError: pass
            for x in (a, b):
                try: x.shutdown(socket.SHUT_RDWR)
                except OSError: pass
        def _loop():
            while True:
                try: c, _ = srv.accept()
                except OSError: break
                try:
                    u = socket.socket(socket.AF_UNIX); u.connect(unix_path)
                except OSError:
                    try: c.close()
                    except OSError: pass
                    continue
                threading.Thread(target=_splice, args=(c, u), daemon=True).start()
                threading.Thread(target=_splice, args=(u, c), daemon=True).start()
        threading.Thread(target=_loop, daemon=True).start()
        _RFB_BRIDGES[unix_path] = port
        return port

def _vmcell_sock(ref):

    if not ref or "/" in ref or ref.startswith("llmoauth_"):
        return None
    try:
        with open(os.path.join(DATA_DIR, "vmcells.json")) as f:
            e = (json.load(f) or {}).get(ref) or {}
        s = e.get("sock", "")
        return s if s and os.path.exists(s) else None
    except (OSError, ValueError):
        return None

_DEVINPUT_AGENTS = {}
_DEVINPUT_LK = threading.Lock()
def ws_devinput(conn, principal, agent):

    name = re.sub(r"[^A-Za-z0-9_.-]", "", str(agent or ""))[:48] or "device"
    with _DEVINPUT_LK:
        old = _DEVINPUT_AGENTS.get(name); _DEVINPUT_AGENTS[name] = conn
    if old is not None and old is not conn:
        try: old.shutdown(socket.SHUT_RDWR); old.close()
        except Exception: pass
    try: _prov_log("devinput.connect", principal, name, {"wire": "ws"})
    except Exception: pass
    try:
        while True:
            if ws_recv(conn) is None:
                break
    except Exception:
        pass
    finally:
        with _DEVINPUT_LK:
            if _DEVINPUT_AGENTS.get(name) is conn:
                del _DEVINPUT_AGENTS[name]
def _devinput_send(name, events):
    with _DEVINPUT_LK:
        conn = _DEVINPUT_AGENTS.get(name)
    if conn is None:
        return False, "Agent '%s' nicht verbunden" % name
    try:
        ws_send(conn, json.dumps({"events": events}).encode(), 0x1); return True, None
    except Exception as e:
        return False, str(e)

def ws_vnc(conn, uid=DEFAULT_PRINCIPAL, unix_sock=None):

    import select
    try:
        if unix_sock:
            up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            up.settimeout(6); up.connect(unix_sock)
        else:
            host, _, port = cell(uid).vnc.partition(":")
            up = socket.create_connection((host, int(port)), timeout=6)
    except Exception:
        return

    up.setblocking(False)
    tx = [0]
    c2p = [0]
    reason = ["?"]

    def up_send(data):
        mv = memoryview(data)
        while mv:
            try:
                mv = mv[up.send(mv):]
            except BlockingIOError:
                select.select([], [up], [], 1.0)

    try:
        while True:
            try:
                pend = conn.pending() > 0
            except Exception:
                pend = False
            if pend:
                rl = [conn]
            else:
                try:
                    rl, _, _ = select.select([up, conn], [], [], 1.0)
                except Exception as e:
                    reason[0] = "select:%r" % e; break

            if up in rl:
                broke = False
                while True:
                    try:
                        data = up.recv(65536)
                    except (BlockingIOError, ssl.SSLWantReadError):
                        break
                    except Exception as e:
                        reason[0] = "up.recv:%r" % e; broke = True; break
                    if data == b"":
                        reason[0] = "phantom EOF (rfbd closed)"; broke = True; break
                    try:
                        ws_send(conn, data, 0x2); tx[0] += len(data)
                    except Exception as e:
                        reason[0] = "ws_send:%r" % e; broke = True; break
                if broke:
                    break

            if (conn in rl) or pend:
                try:
                    msg = ws_recv(conn)
                except Exception as e:
                    reason[0] = "ws_recv:%r" % e; break
                if msg is None:
                    reason[0] = "ws_recv None (browser closed)"; break
                if msg:
                    if c2p[0] < 25:
                        c2p[0] += 1
                        try:
                            open("/tmp/vncdiag.log", "a").write("C2P#%d len=%d head=%s\n" % (c2p[0], len(msg), msg[:8].hex()))
                        except Exception:
                            pass
                    try:
                        up_send(msg)
                    except Exception as e:
                        reason[0] = "up_send:%r" % e; break
    finally:
        try:
            open("/tmp/vncdiag.log", "a").write("WS_VNC(1thread) tx=%d exit=%s\n" % (tx[0], reason[0]))
        except Exception:
            pass
        try:
            up.close()
        except Exception:
            pass

def list_rooms():
    base = os.path.join(HOME, ".local", "state", "phantom-rooms")
    out = []
    try:
        for n in sorted(os.listdir(base)):
            if os.path.isdir(os.path.join(base, n)):
                broker = subprocess.run(["pgrep", "-f", f"phantom-room broker {n}"], capture_output=True).returncode == 0
                out.append({"name": n, "broker": broker})
    except FileNotFoundError:
        pass
    return out

