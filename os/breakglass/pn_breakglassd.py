#!/usr/bin/env python3

import argparse
import base64
import hashlib
import hmac
import http.server
import json
import os
import pty
import re
import secrets
import select
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.parse

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
COOKIE = "pn_bg"
CHAT_TURNS = 60
CHAT_TAIL_BYTES = 600_000
STATIC_OK = {"xterm.js": "text/javascript", "xterm.css": "text/css",
             "addon-fit.js": "text/javascript"}

CFG = {}
_throttle = {}
_thr_lock = threading.Lock()

def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(os.path.join(CFG["config_dir"], "audit.log"), "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def read_token():

    try:
        with open(CFG["token_file"]) as f:
            t = f.read().strip()
        return t if len(t) >= 16 else ""
    except Exception:
        return ""

_USERS_DB_CANDIDATES = (
    os.path.expanduser("~/.local/share/brainbox-portal/users.db"),
    os.path.expanduser("~/.local/share/phantom-portal/users.db"),
)
_CFG_CANDIDATES = (
    os.path.expanduser("~/.config/brainbox-portal/config.json"),
    os.path.expanduser("~/.config/phantom-portal/config.json"),
)

_PW_PARAMS = {"scrypt-16384-8-1-32": dict(n=16384, r=8, p=1, dklen=32)}
_PW_ALGO_DEFAULT = "scrypt-16384-8-1-32"

def _owner_row():

    import sqlite3
    for p in _USERS_DB_CANDIDATES:
        try:
            c = sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(p), uri=True, timeout=5)
        except Exception:
            continue
        try:
            r = c.execute("SELECT pw_hash,pw_salt,pw_algo FROM users"
                          " WHERE (uid=? OR role=?) AND status='active' AND pw_hash<>''"
                          " ORDER BY (uid=?) DESC LIMIT 1",
                          ("owner", "owner", "owner")).fetchone()
            if r and r[0] and r[1]:
                return r
        except Exception:
            continue
        finally:
            try:
                c.close()
            except Exception:
                pass
    return None

def _seed_pin():

    for p in _CFG_CANDIDATES:
        try:
            with open(p) as f:
                pin = str((json.load(f) or {}).get("pin") or "").strip()
            if len(pin) >= 4:
                return pin
        except Exception:
            continue
    return ""

def owner_login_ok(secret):

    if not secret:
        return False
    row = _owner_row()
    if row:
        pw_hash, pw_salt, pw_algo = row[0], row[1], (row[2] or "")
        params = _PW_PARAMS.get(pw_algo) or _PW_PARAMS[_PW_ALGO_DEFAULT]
        try:
            calc = hashlib.scrypt(secret.encode("utf-8"),
                                  salt=bytes.fromhex(pw_salt), **params).hex()
        except Exception:
            return False
        return hmac.compare_digest(calc, pw_hash)
    seed = _seed_pin()
    return bool(seed) and hmac.compare_digest(secret, seed)

def owner_login_available():

    return bool(_owner_row() or _seed_pin())

def _cookie_secret():

    tok = read_token()
    if tok:
        return tok
    row = _owner_row()
    return ("pwh:" + row[0]) if row else ""

def cookie_value(token):
    return hmac.new(token.encode(), b"pn-breakglass-cookie-v1", hashlib.sha256).hexdigest()

def host_erlaubt(handler):

    if not passkey_vorhanden():
        return True
    host = (handler.headers.get("Host") or "").split(":")[0].strip().lower()
    return host in ERLAUBTE_HOSTS

def authed(handler):
    secret = _cookie_secret()
    if not secret:
        return None
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
    qt = (qs.get("token") or [""])[0]

    token = read_token()
    if qt and token and hmac.compare_digest(qt, token):
        return secret
    ck = handler.headers.get("Cookie") or ""
    for part in ck.split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE and hmac.compare_digest(v, cookie_value(secret)):
            return secret
    return False

def throttle_ok(ip):
    now = time.time()
    with _thr_lock:
        lst = [t for t in _throttle.get(ip, []) if now - t < 300]
        _throttle[ip] = lst
        return len(lst) < 5

def throttle_fail(ip):
    with _thr_lock:
        _throttle.setdefault(ip, []).append(time.time())

def ws_accept_key(key):
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()

def ws_send(sock, lock, payload, opcode=0x2):

    head = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head.append(n)
    elif n < 65536:
        head.append(126)
        head += struct.pack(">H", n)
    else:
        head.append(127)
        head += struct.pack(">Q", n)
    with lock:
        sock.sendall(bytes(head) + payload)

def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("eof")
        buf += chunk
    return buf

def ws_read_frame(sock):

    b1, b2 = _recv_exact(sock, 2)
    opcode = b1 & 0x0F
    masked = b2 & 0x80
    ln = b2 & 0x7F
    if ln == 126:
        ln = struct.unpack(">H", _recv_exact(sock, 2))[0]
    elif ln == 127:
        ln = struct.unpack(">Q", _recv_exact(sock, 8))[0]
    if ln > 1 << 20:
        raise ConnectionError("frame too big")
    mask = _recv_exact(sock, 4) if masked else b""
    data = _recv_exact(sock, ln) if ln else b""
    if masked:
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
    return opcode, data

def set_winsize(fd, rows, cols):
    try:
        import fcntl
        import termios
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except Exception:
        pass

def spawn_tmux():

    pid, master = pty.fork()
    if pid == 0:

        set_winsize(0, 24, 80)
        home = CFG["home"]
        env = {
            "HOME": home, "USER": CFG["user"], "LOGNAME": CFG["user"], "SHELL": "/bin/bash",
            "PATH": "%s/.local/bin:%s/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" % (home, home),
            "TERM": "xterm-256color", "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        os.chdir(CFG["cwd"])
        argv = ["tmux", "new-session", "-A", "-s", CFG["session"], CFG["cmd"]]
        try:
            os.execvpe(argv[0], argv, env)
        except Exception:
            os.write(2, b"tmux exec failed\n")
            os._exit(127)
    return pid, master

def ws_terminal(handler):

    sock = handler.connection
    wlock = threading.Lock()
    pid, master = spawn_tmux()
    log("attach ip=%s pid=%d session=%s" % (handler.client_address[0], pid, CFG["session"]))
    alive = [True]

    def reader():

        try:
            while alive[0]:
                op, data = ws_read_frame(sock)
                if op == 0x8:
                    break
                if op == 0x9:
                    ws_send(sock, wlock, data, 0xA)
                elif op == 0x1:
                    try:
                        m = json.loads(data.decode("utf-8", "replace"))
                        if isinstance(m, dict) and "resize" in m:
                            c, r = int(m["resize"][0]), int(m["resize"][1])
                            if 10 <= c <= 500 and 4 <= r <= 300:
                                set_winsize(master, r, c)
                    except Exception:
                        pass
                elif op == 0x2 and data:
                    os.write(master, data)
        except Exception:
            pass
        alive[0] = False

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        while alive[0]:
            r, _, _ = select.select([master], [], [], 1.0)
            if master in r:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                ws_send(sock, wlock, chunk, 0x2)
    except Exception:
        pass
    alive[0] = False

    try:
        os.close(master)
    except Exception:
        pass
    try:
        os.kill(pid, signal.SIGHUP)
        time.sleep(0.3)
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        os.waitpid(pid, os.WNOHANG)
    except Exception:
        pass
    log("detach ip=%s pid=%d" % (handler.client_address[0], pid))

_URL_ZEICHEN = r"A-Za-z0-9%._~:/?#\[\]@!$&'()*+,;=|-"
_URL_RE = re.compile(r"https?://[%s]+" % _URL_ZEICHEN)
_URL_FORTSETZUNG = re.compile(r"^[%s]+$" % _URL_ZEICHEN)

def _tmux(*args, **kw):

    env = {"HOME": CFG["home"], "USER": CFG["user"],
           "PATH": "/usr/local/bin:/usr/bin:/bin", "TERM": "xterm-256color",
           "LANG": os.environ.get("LANG", "C.UTF-8")}
    try:
        r = subprocess.run(["tmux"] + [str(a) for a in args], capture_output=True,
                           timeout=kw.get("timeout", 10), env=env)
        return r.returncode == 0, r.stdout.decode("utf-8", "replace")
    except Exception:
        return False, ""

def pane_text(zeilen=500):
    ok, out = _tmux("capture-pane", "-p", "-t", CFG["session"], "-S", "-%d" % int(zeilen))
    return out if ok else ""

def urls_aus_terminal(text):

    zeilen = (text or "").split("\n")
    treffer = []
    i = 0
    while i < len(zeilen):
        z = zeilen[i].rstrip()
        m = _URL_RE.search(z)
        if not m:
            i += 1
            continue
        url = m.group(0)
        randvoll = (m.end() == len(z))
        j = i
        while randvoll and j + 1 < len(zeilen) and (j - i) < 20:
            nz = zeilen[j + 1].rstrip()
            if not nz or nz.startswith(" ") or not _URL_FORTSETZUNG.match(nz):
                break
            url += nz
            j += 1
        treffer.append({"url": url, "gefuegt": j > i})
        i = j + 1
    ohne_dubletten, gesehen = [], set()
    for t in reversed(treffer):
        if t["url"] not in gesehen:
            gesehen.add(t["url"])
            ohne_dubletten.append(t)
    return ohne_dubletten[:12]

def _projekt_ordner(cwd):

    return "".join(c if c.isalnum() else "-" for c in os.path.realpath(cwd))

_TUI_RAND = " │┃▌▏▔▁·•>"
_ADOPT_SPERRE = [0.0]

def _adoptions_nadeln(pane, wieviele=4):

    ascii_nadeln, sonstige = [], []
    for z in reversed((pane or "").split("\n")):
        z = z.strip().strip(_TUI_RAND).strip()
        if len(z) < 45 or z.count(" ") < 4 or "http" in z or z.startswith("/"):
            continue
        if any(c in z for c in "▔▁═━│┃"):
            continue
        (ascii_nadeln if z.isascii() else sonstige).append(z[:120])
        if len(ascii_nadeln) >= wieviele:
            break
    return (ascii_nadeln + sonstige)[:wieviele]

def transkript_adoptieren():

    if time.time() < _ADOPT_SPERRE[0]:
        return None
    _ADOPT_SPERRE[0] = time.time() + 90
    ordner = os.path.join(CFG["home"], ".claude", "projects", _projekt_ordner(CFG["cwd"]))
    nadeln = _adoptions_nadeln(pane_text())
    if not nadeln or not os.path.isdir(ordner):
        return None
    try:
        dateien = sorted((os.path.join(ordner, n) for n in os.listdir(ordner) if n.endswith(".jsonl")),
                         key=lambda p: os.path.getmtime(p), reverse=True)[:25]
    except Exception:
        return None
    treffer = []
    for p in dateien:
        try:
            with open(p, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 2_000_000))
                roh = f.read().decode("utf-8", "replace")
        except Exception:
            continue
        if any(n in roh for n in nadeln):
            treffer.append(p)
        if len(treffer) > 1:
            break
    if len(treffer) != 1:
        log("transkript-adoption: %d Treffer -> keine Zuordnung" % len(treffer))
        return None
    sid = os.path.basename(treffer[0])[:-len(".jsonl")]
    try:
        with open(os.path.join(CFG["config_dir"], "session-id"), "w") as f:
            f.write(sid + "\n")
        os.chmod(os.path.join(CFG["config_dir"], "session-id"), 0o600)
    except Exception:
        return None
    log("transkript-adoption: laufende Sitzung erkannt (%s)" % sid)
    return treffer[0]

def transkript_pfad():

    try:
        with open(os.path.join(CFG["config_dir"], "session-id")) as f:
            sid = f.read().strip()
    except Exception:
        sid = ""
    if not re.match(r"^[0-9a-fA-F-]{8,64}$", sid or ""):
        return transkript_adoptieren()
    p = os.path.join(CFG["home"], ".claude", "projects", _projekt_ordner(CFG["cwd"]), sid + ".jsonl")
    return p if os.path.isfile(p) else None

def transkript_turns(n=CHAT_TURNS):

    p = transkript_pfad()
    if not p:
        return None
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            groesse = f.tell()
            f.seek(max(0, groesse - CHAT_TAIL_BYTES))
            roh = f.read().decode("utf-8", "replace")
    except Exception:
        return None
    zeilen = roh.split("\n")
    if groesse > CHAT_TAIL_BYTES and zeilen:
        zeilen = zeilen[1:]
    turns = []
    for ln in zeilen:
        ln = ln.strip()
        if not ln:
            continue
        try:
            ev = json.loads(ln)
        except Exception:
            continue
        typ = ev.get("type")
        if typ not in ("user", "assistant") or ev.get("isMeta"):
            continue
        msg = ev.get("message") or {}
        inhalt = msg.get("content")
        texte, werkzeuge = [], []
        if isinstance(inhalt, str):
            texte.append(inhalt)
        elif isinstance(inhalt, list):
            for b in inhalt:
                if not isinstance(b, dict):
                    continue
                art = b.get("type")
                if art == "text":
                    texte.append(b.get("text") or "")
                elif art == "tool_use":
                    werkzeuge.append(str(b.get("name") or "Werkzeug"))
                elif art == "thinking":
                    pass
        text = "\n".join(t for t in texte if t.strip()).strip()
        if not text and not werkzeuge:
            continue
        turns.append({"rolle": "du" if typ == "user" else "konsole",
                      "text": text, "werkzeuge": werkzeuge,
                      "ts": ev.get("timestamp") or "",
                      "compact": bool(ev.get("isCompactSummary"))})
    return turns[-int(n):]

def chat_zustand():

    pane = pane_text()
    turns = transkript_turns()
    links = urls_aus_terminal(pane)
    bekannt = {l["url"] for l in links}
    for t in (turns or [])[-8:]:
        for u in _URL_RE.findall(t.get("text") or ""):
            if u not in bekannt:
                bekannt.add(u)
                links.append({"url": u, "gefuegt": False})
    schwanz = [z.rstrip() for z in pane.split("\n")][-40:]
    while schwanz and not schwanz[-1]:
        schwanz.pop()
    return {"turns": turns, "kein_transkript": turns is None,
            "links": links[:12], "terminal": "\n".join(schwanz),
            "session": CFG["session"], "host": socket.gethostname()}

def chat_senden(text, taste):

    sess = CFG["session"]
    if text:
        ok, _ = _tmux("send-keys", "-t", sess, "-l", "--", text)
        if not ok:
            return False, "tmux nahm die Eingabe nicht an (laeuft die Sitzung?)"
        time.sleep(0.15)
    if taste in ("enter", "esc", "ctrl-c", "ctrl-d"):
        schluessel = {"enter": "Enter", "esc": "Escape", "ctrl-c": "C-c", "ctrl-d": "C-d"}[taste]
        ok, _ = _tmux("send-keys", "-t", sess, schluessel)
        if not ok:
            return False, "tmux nahm die Taste nicht an"
    return True, "ok"

def anmeldung_starten(warte=45.0):

    vorher = {l["url"] for l in urls_aus_terminal(pane_text())}
    breit_ok, _ = chat_breite(500)
    try:

        _tmux("send-keys", "-t", CFG["session"], "Escape")
        time.sleep(0.4)
        _tmux("send-keys", "-t", CFG["session"], "-l", "--", "/login")
        time.sleep(0.2)
        _tmux("send-keys", "-t", CFG["session"], "Enter")
        ende = time.time() + max(5.0, float(warte))
        gewaehlt = False
        while time.time() < ende:
            time.sleep(1.0)
            schirm = pane_text()
            if not gewaehlt and "Select login method" in schirm:

                _tmux("send-keys", "-t", CFG["session"], "-l", "--", "1")
                time.sleep(1.0)
                if "Select login method" in pane_text():
                    _tmux("send-keys", "-t", CFG["session"], "Enter")
                gewaehlt = True
                continue
            for l in urls_aus_terminal(schirm):

                if "oauth" in l["url"].lower() and l["url"] not in vorher:
                    return True, l["url"]
    finally:
        if breit_ok:
            chat_breite(0)
    return False, ("Kein Anmeldelink erschienen. Laeuft in der Sitzung gerade `claude`? "
                   "Sonst im Terminal-Reiter nachsehen.")

def chat_breite(spalten):

    if not spalten:
        _tmux("set-window-option", "-t", CFG["session"], "window-size", "latest")
        return True, "Breite folgt wieder dem Fenster"
    spalten = max(80, min(800, int(spalten)))
    _tmux("set-window-option", "-t", CFG["session"], "window-size", "manual")
    ok, _ = _tmux("resize-window", "-t", CFG["session"], "-x", spalten, "-y", 50)
    return ok, ("fest auf %d Spalten" % spalten) if ok else "tmux konnte nicht umstellen"

LOGIN_HTML = r"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>body{font:15px system-ui;background:#0b0f14;color:#dbe2ea;display:flex;align-items:center;justify-content:center;min-height:95vh}
form{background:#121821;border:1px solid #263241;border-radius:12px;padding:28px;max-width:340px}
h1{font-size:17px;margin:0 0 6px}p{font-size:12.5px;opacity:.75;margin:0 0 14px}
input{width:100%%;padding:9px;border-radius:8px;border:1px solid #2c3a4c;background:#0b0f14;color:inherit;box-sizing:border-box}
button{margin-top:12px;width:100%%;padding:9px;border-radius:8px;border:0;background:#2563eb;color:#fff;font:inherit;cursor:pointer}
.err{color:#f87171;font-size:12.5px;margin-top:8px}</style>
<form method="post" action="/auth" id="pinform"><h1>%(title)s</h1>
<div id="pk" hidden>
  <p>Mit <b>Passkey</b> anmelden — Fingerabdruck oder Gesicht.</p>
  <button type="button" id="pkbtn">Mit Passkey anmelden</button>
  <div class="err" id="pkerr"></div>
</div>
<div id="pin">
<p>Mit deiner <b>Owner-PIN</b> anmelden — dieselbe wie am Portal.<br>
<span style="opacity:.6">(Alternativ das Konsolen-Token aus %(token_file)s.)</span></p>
<input name="token" type="password" autofocus autocomplete="current-password" placeholder="Owner-PIN">
<button>Verbinden</button>%(err)s
</div>
</form>
<script>
function b64u2ab(s){s=s.replace(/-/g,"+").replace(/_/g,"/");var p=atob(s+"===".slice((s.length+3)%%4));
  var a=new Uint8Array(p.length);for(var i=0;i<p.length;i++)a[i]=p.charCodeAt(i);return a.buffer;}
function ab2b64u(b){var x="",a=new Uint8Array(b);for(var i=0;i<a.length;i++)x+=String.fromCharCode(a[i]);
  return btoa(x).split("+").join("-").split("/").join("_").replace(/=+$/,"");}
(function(){
  if(!window.PublicKeyCredential||!location.protocol.startsWith("https")) return;
  fetch("/passkey/anmelden/start",{method:"POST",credentials:"same-origin",
    headers:{"Content-Type":"application/json"},body:"{}"})
   .then(function(r){return r.json();}).then(function(d){
     if(!d.ok) return;                       // kein Passkey eingetragen: PIN-Kasten bleibt
     document.getElementById("pk").hidden=false;
     document.getElementById("pin").hidden=true;
     document.getElementById("pkbtn").onclick=function(){
       var err=document.getElementById("pkerr");err.textContent="";
       navigator.credentials.get({publicKey:{
         challenge:b64u2ab(d.challenge), rpId:d.rpId, userVerification:"required",
         allowCredentials:(d.allowCredentials||[]).map(function(c){
           return {type:"public-key", id:b64u2ab(c.id)};})}})
       .then(function(c){
         return fetch("/passkey/anmelden/fertig",{method:"POST",credentials:"same-origin",
           headers:{"Content-Type":"application/json"},
           body:JSON.stringify({challenge:d.challenge, id:c.id,
             clientDataJSON:ab2b64u(c.response.clientDataJSON),
             authenticatorData:ab2b64u(c.response.authenticatorData),
             signature:ab2b64u(c.response.signature)})});
       }).then(function(r){return r.json();}).then(function(e){
         if(e.ok) location.href="/"; else err.textContent="Nicht angemeldet: "+(e.grund||"unbekannt");
       }).catch(function(x){err.textContent="Abgebrochen: "+x;});
     };
   }).catch(function(){});
})();
</script>"""

PASSKEY_HTML = r"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s — Passkeys</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}
body{margin:0;font:16px/1.5 system-ui,sans-serif;background:#0b0f14;color:#dbe2ea}
header{position:sticky;top:0;background:#0b0f14ee;border-bottom:1px solid #1d2735;padding:10px 12px;display:flex;gap:10px;align-items:center}
header a{color:#7dd3fc;font-size:13px;text-decoration:none}
main{padding:12px;max-width:760px;margin:0 auto}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#7f8ea3;margin:18px 0 8px}
button{font:inherit;font-size:15px;padding:11px 16px;border-radius:10px;border:1px solid #2a3a4d;background:#16202c;color:#dbe2ea;cursor:pointer;min-height:46px}
button.gefahr{border-color:#7f1d1d;background:#2a1416;color:#fca5a5}
.reihe{border:1px solid #1d2735;border-radius:10px;padding:10px;background:#0e141c;margin-bottom:8px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.reihe b{font-size:15px}.reihe span{font-size:12px;color:#7f8ea3}
.hinweis{font-size:13px;color:#9aa7b5;line-height:1.55;border:1px solid #1d2735;border-radius:10px;padding:12px;background:#0e141c;margin-top:12px}
.warn{border-color:#78350f;background:#1a1204;color:#fcd34d}
input{font:inherit;padding:10px;border-radius:8px;border:1px solid #2a3a4d;background:#0b1119;color:#dbe2ea}
#st{font-size:13px;margin-top:10px}
</style>
<header><a href="/">&#9666; Lage</a> <b>Passkeys</b></header>
<main>
<div id="liste"></div>
<h2>Kann dieses Ger&auml;t einen Passkey anlegen?</h2>
<div id="probe" class="hinweis">wird gepr&uuml;ft &hellip;</div>

<h2>Neuen Passkey anlegen</h2>
<div class="reihe">
  <input id="label" placeholder="Name des Ger&auml;ts, z.&nbsp;B. iPhone" maxlength="60">
  <button id="neu">Auf diesem Ger&auml;t anlegen</button>
</div>
<div id="st"></div>
<div class="hinweis warn" id="folge"></div>
<div class="hinweis">
<b>Wenn das Ger&auml;t verloren geht:</b> die Konsole l&auml;sst sich weiterhin mit dem
Konsolen-Token &ouml;ffnen (<code>~/.config/pn-breakglass/token</code>). Das Token ist nicht
erratbar und nur mit Dateizugriff zu holen &mdash; also &uuml;ber SSH, den Hauptweg. Ein starkes
Schloss ohne Ersatzschl&uuml;ssel w&auml;re keine Sicherheit, sondern eine Falle.
</div>
</main>
<script>
function b64u2ab(s){s=s.replace(/-/g,"+").replace(/_/g,"/");var p=atob(s+"===".slice((s.length+3)%%4));
  var a=new Uint8Array(p.length);for(var i=0;i<p.length;i++)a[i]=p.charCodeAt(i);return a.buffer;}
function ab2b64u(b){var x="",a=new Uint8Array(b);for(var i=0;i<a.length;i++)x+=String.fromCharCode(a[i]);
  return btoa(x).split("+").join("-").split("/").join("_").replace(/=+$/,"");}
function zeit(t){if(!t)return "nie";var d=new Date(t*1000);return d.toLocaleString();}
var st=document.getElementById("st"), folge=document.getElementById("folge");
function laden(){
  fetch("/api/passkeys",{credentials:"same-origin"}).then(function(r){return r.json();}).then(function(d){
    var L=document.getElementById("liste");L.textContent="";
    var h=document.createElement("h2");h.textContent="Eingetragen ("+d.schluessel.length+")";L.appendChild(h);
    if(!d.schluessel.length){
      var e=document.createElement("div");e.className="reihe";
      e.textContent="Noch keiner. Solange gilt die Owner-PIN wie bisher.";L.appendChild(e);
      folge.innerHTML="<b>Sobald der erste Passkey eingetragen ist</b>, &auml;ndert sich zweierlei: "+
        "die Owner-PIN &ouml;ffnet diese Konsole nicht mehr, und sie ist nur noch unter "+
        "<code>https://"+d.rp+":8090</code> erreichbar &mdash; ein Passkey gilt nur f&uuml;r diesen "+
        "Namen, &uuml;ber eine nackte IP kann kein Ger&auml;t einen vorzeigen.";
    } else {
      folge.innerHTML="<b>Aktiv:</b> die Owner-PIN &ouml;ffnet diese Konsole nicht mehr, und sie ist "+
        "nur unter <code>https://"+d.rp+":8090</code> erreichbar.";
      d.schluessel.forEach(function(k){
        var r=document.createElement("div");r.className="reihe";
        var b=document.createElement("b");b.textContent=k.label||"(ohne Namen)";r.appendChild(b);
        var s=document.createElement("span");
        s.textContent="angelegt "+zeit(k.angelegt)+" \u00b7 zuletzt "+zeit(k.zuletzt);r.appendChild(s);
        var x=document.createElement("button");x.className="gefahr";x.textContent="Entfernen";
        x.style.marginLeft="auto";
        x.onclick=function(){
          if(!confirm("Passkey \u201e"+(k.label||"")+"\u201c entfernen?"))return;
          fetch("/passkey/entfernen",{method:"POST",credentials:"same-origin",
            headers:{"Content-Type":"application/json"},body:JSON.stringify({cred_id:k.cred_id})})
           .then(function(r){return r.json();}).then(function(){laden();});
        };
        r.appendChild(x);L.appendChild(r);
      });
    }
  });
}
/* \u2500\u2500 EIGNUNGSPROBE \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
   Vier Bedingungen muessen stimmen, bevor ein Passkey ueberhaupt entstehen kann. Sie
   VORHER einzeln zu pruefen und beim Namen zu nennen ist der ganze Unterschied zwischen
   \u201eAbgebrochen: SecurityError" und einem Satz, der sagt, was zu tun ist. Der Knopf wird
   gesperrt, solange etwas Hartes fehlt \u2014 ein Knopf, der sicher scheitert, ist schlimmer
   als keiner. */
var neu=document.getElementById("neu");
function esc(s){var e=document.createElement("div");e.textContent=s;return e.innerHTML;}
function probeMalen(zeilen, blocker){
  var p=document.getElementById("probe");
  p.innerHTML=zeilen.join("<br>");
  neu.disabled=!!blocker;
  neu.style.opacity=blocker?".45":"1";
  neu.title=blocker||"";
  if(blocker){p.className="hinweis warn";}
}
function probe(){
  var zeilen=[], blocker="";
  fetch("/api/passkeys",{credentials:"same-origin"}).then(function(r){return r.json();})
  .then(function(d){
    var hier=(d.hier||location.host).split(":")[0];
    var rp=d.rp;

    if(d.name_stabil===false){
      zeilen.push("&#10007; <b>Diese Box tr&auml;gt ihren Namen nicht.</b> "+esc(d.name_grund||""));
      blocker=blocker||"Name der Box wandert";
    } else if(d.name_wirklich){
      zeilen.push("&#10003; Die Box hei&szlig;t im Netz <code>"+esc(d.name_wirklich)+"</code>.");
    }

    if(!window.PublicKeyCredential){
      zeilen.push("&#10007; Dieser Browser kann keine Passkeys.");
      blocker=blocker||"Browser kann kein WebAuthn";
    } else { zeilen.push("&#10003; Browser kann Passkeys."); }

    if(!window.isSecureContext){
      zeilen.push("&#10007; Die Verbindung gilt nicht als sicher &mdash; meist, weil dieses "+
        "Ger&auml;t der Box nicht traut und die Warnseite weggeklickt wurde. "+
        "<a href='/ca.crt' download='brainbox-ca.crt'>Wurzelzertifikat laden</a> und "+
        "installieren, dann diese Seite neu &ouml;ffnen.");
      blocker=blocker||"kein sicherer Kontext";
    } else { zeilen.push("&#10003; Verbindung ist sicher."); }

    if(hier===rp){
      zeilen.push("&#10003; Ge&ouml;ffnet unter dem Namen <code>"+rp+"</code>.");
    } else {
      zeilen.push("&#10007; Ge&ouml;ffnet als <code>"+hier+"</code>, gebraucht wird "+
        "<code>"+rp+"</code>. Ein Passkey wird an einen NAMEN gebunden, nie an eine Adresse "+
        "&mdash; unter einer IP kann kein Ger&auml;t je einen vorzeigen. "+
        "<a href='https://"+rp+":8090/passkey'>Hier unter dem Namen &ouml;ffnen</a>");
      blocker=blocker||"falscher Name";
      /* Ehrliche Zusatzprobe: findet dieses Geraet den Namen ueberhaupt? Android loest
         `.local` in Chrome oft nicht auf \u2014 dann fuehrt der Link oben ins Leere, und das
         soll hier stehen und nicht erst nach dem Klick auffallen. */
      var t=setTimeout(function(){nachtrag("&#8987; Der Name antwortet nicht schnell &mdash; "+
        "siehe Hinweis unten.");},4000);
      fetch("https://"+rp+":8090/healthz",{mode:"no-cors",cache:"no-store"})
        .then(function(){clearTimeout(t);nachtrag("&#10003; Dieses Ger&auml;t erreicht "+
          "<code>"+rp+"</code> &mdash; dem Link oben folgen, dann geht es weiter.");})
        .catch(function(){clearTimeout(t);nachtrag("&#10007; Dieses Ger&auml;t erreicht "+
          "<code>"+rp+"</code> nicht (Name unbekannt oder Zertifikat nicht vertraut). "+
          "Android-Chrome findet <code>.local</code>-Namen h&auml;ufig nicht. Dann bleibt "+
          "SSH der Weg, oder der Name wird im Router eingetragen.");});
    }

    probeMalen(zeilen, blocker);

    if(window.PublicKeyCredential&&PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable){
      PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable().then(function(ja){
        nachtrag(ja?"&#10003; Fingerabdruck/Gesicht steht bereit."
                  :"&#8505; Kein eingebauter Sensor gefunden &mdash; ein Sicherheitsschl&uuml;ssel "+
                    "am USB/NFC geht trotzdem.");
      }).catch(function(){});
    }
  }).catch(function(e){
    probeMalen(["Probe nicht m&ouml;glich: "+e], "Probe fehlgeschlagen");
  });
}
function nachtrag(html){
  var p=document.getElementById("probe");
  p.innerHTML=p.innerHTML+"<br>"+html;
}
probe();

document.getElementById("neu").onclick=function(){
  st.textContent="\u2026";
  if(!window.PublicKeyCredential){st.textContent="Dieser Browser kann keine Passkeys.";return;}
  fetch("/passkey/anlegen/start",{method:"POST",credentials:"same-origin",
    headers:{"Content-Type":"application/json"},body:"{}"})
   .then(function(r){return r.json();}).then(function(d){
     if(!d.ok){st.textContent="Nicht m\u00f6glich: "+(d.grund||"");return;}
     return navigator.credentials.create({publicKey:{
       challenge:b64u2ab(d.challenge), rp:d.rp,
       user:{id:b64u2ab(d.user.id), name:d.user.name, displayName:d.user.displayName},
       pubKeyCredParams:d.pubKeyCredParams,
       authenticatorSelection:d.authenticatorSelection,
       attestation:d.attestation,
       excludeCredentials:(d.excludeCredentials||[]).map(function(c){
         return {type:"public-key", id:b64u2ab(c.id)};})}})
      .then(function(c){
        return fetch("/passkey/anlegen/fertig",{method:"POST",credentials:"same-origin",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({challenge:d.challenge,
            label:document.getElementById("label").value||"Ger\u00e4t",
            clientDataJSON:ab2b64u(c.response.clientDataJSON),
            attestationObject:ab2b64u(c.response.attestationObject)})});
      }).then(function(r){return r.json();}).then(function(e){
        st.textContent=e.ok?("\u2713 angelegt. "+(e.hinweis||"")):("Fehlgeschlagen: "+(e.grund||""));
        laden();
      });
   }).catch(function(x){st.textContent="Abgebrochen: "+x;});
};
laden();
</script>"""

HANDGRIFFE = {
    "dienste":   ("Dienste", [["pnctl", "list"]]),
    "platte":    ("Platte", [["df", "-h", "-x", "tmpfs", "-x", "devtmpfs"]]),
    "speicher":  ("Speicher", [["free", "-m"], ["cat", "/proc/pressure/memory"]]),
    "netz":      ("Netz", [["ip", "-br", "addr"], ["ss", "-ltn"]]),
}
PROTOKOLLE = {
    "portal":  os.path.expanduser("~/.local/share/brainbox-portal/portal.console.log"),
    "konsole": None,
}

_WIRT_BLOCK = None
_WIRT_MIB = 16
_WIRT_LETZTE = {}

def _stat_werte():

    try:
        with open("/proc/stat") as f:
            for zeile in f:
                if zeile.startswith("cpu "):
                    t = [int(x) for x in zeile.split()[1:]]
                    return sum(t), (t[7] if len(t) > 7 else 0)
    except (OSError, ValueError):
        pass
    return 0, 0

def wirt_verdacht():

    global _WIRT_BLOCK
    zeilen = []

    gesamt, gestohlen = _stat_werte()
    alt_g, alt_s = _WIRT_LETZTE.get("cpu", (0, 0))
    _WIRT_LETZTE["cpu"] = (gesamt, gestohlen)

    if alt_g and (gesamt - alt_g) < 200:
        zeilen.append("Entzogene Rechenzeit: Spanne zu kurz fuer eine belastbare Zahl — "
                      "einen Moment warten und nochmal druecken.")
    elif alt_g and gesamt > alt_g:
        anteil = 100.0 * (gestohlen - alt_s) / float(gesamt - alt_g)
        zeilen.append("Entzogene Rechenzeit seit der letzten Messung: %.1f %%" % anteil)
        if anteil > 10:
            zeilen.append("  -> Der Wirt gibt unsere CPU gerade anderen. Das ist nicht unser "
                          "Problem und nicht hier zu beheben.")
    else:
        zeilen.append("Entzogene Rechenzeit: (erste Messung — nochmal druecken fuer die "
                      "Differenz)")
    zeilen.append("Seit dem Start insgesamt gestohlen: %.2f %% aller Takte"
                  % (100.0 * gestohlen / gesamt if gesamt else 0.0))

    frisch = _WIRT_BLOCK is None
    if frisch:
        _WIRT_BLOCK = bytearray(_WIRT_MIB * 1024 * 1024)
    t0 = time.perf_counter()
    schritt = 4096
    summe = 0
    for i in range(0, len(_WIRT_BLOCK), schritt):
        summe += _WIRT_BLOCK[i]
        _WIRT_BLOCK[i] = (summe + i) & 0xFF
    ms = (time.perf_counter() - t0) * 1000.0
    zeilen.append("")
    zeilen.append("Griff auf %d MiB eigenen Speicher: %.0f ms" % (_WIRT_MIB, ms))
    if frisch:
        zeilen.append("  (gerade erst belegt — der naechste Griff ist der aussagekraeftige)")
    elif ms > 250:
        zeilen.append("  -> Das ist PLATTENLATENZ, nicht Speicher. Dieser Block lag nicht im "
                      "RAM: der Wirt hat ihn ausgelagert. Von innen ist das sonst unsichtbar — "
                      "kein Zaehler im Gast zeigt es an. Am Hypervisor nachsehen "
                      "(freier Speicher, Auslagerung, ueberbuchte VMs).")
    else:
        zeilen.append("  -> Normal. Unser Speicher liegt im RAM.")

    try:
        with open("/proc/meminfo") as f:
            mi = dict((z.split(":")[0], z.split()[1]) for z in f if ":" in z)
        zeilen.append("")
        zeilen.append("Im Gast: %s MiB verfuegbar von %s MiB"
                      % (int(mi.get("MemAvailable", 0)) // 1024,
                         int(mi.get("MemTotal", 0)) // 1024))
        zeilen.append("⚠ Diese Zahl sagt NICHTS darueber, ob der Wirt uns Seiten entzieht — "
                      "sie sah am 07.08.2026 gesund aus, waehrend 2,9 GB auf der Platte lagen.")
    except (OSError, ValueError, IndexError):
        pass

    return {"titel": "Wirt-Verdacht", "text": "\n".join(zeilen)}

def handgriff_lesen(was):

    if was == "wirt":
        return wirt_verdacht()
    eintrag = HANDGRIFFE.get(was)
    if not eintrag:
        return None
    titel, befehle = eintrag
    stuecke = []
    for argv in befehle:
        if not shutil.which(argv[0]):
            stuecke.append("$ %s\n  (nicht vorhanden)" % " ".join(argv))
            continue
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=20)
            aus = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
        except subprocess.TimeoutExpired:
            aus = "(antwortet nicht — das ist selbst der Befund)"
        except OSError as e:
            aus = "(nicht ausfuehrbar: %s)" % e
        stuecke.append("$ %s\n%s" % (" ".join(argv), aus.rstrip()))
    return {"titel": titel, "text": "\n\n".join(stuecke)}

def protokoll_lesen(welches, zeilen=200):
    pfad = PROTOKOLLE.get(welches)
    if welches == "konsole":
        pfad = os.path.join(CFG["config_dir"], "audit.log")
    if not pfad or not os.path.isfile(pfad):
        return {"titel": "Protokoll", "text": "(keins vorhanden: %s)" % (pfad or welches)}
    try:
        with open(pfad, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 200_000))
            roh = f.read().decode("utf-8", "replace")
    except OSError as e:
        return {"titel": "Protokoll", "text": "(nicht lesbar: %s)" % e}
    return {"titel": "Protokoll · " + welches,
            "text": "\n".join(roh.splitlines()[-zeilen:])}

_SHELL_LK = threading.Lock()
_SHELL_ZER = {}
_SHELL_FREI = set()
_SHELL_OFFEN = set()
COOKIE_SHELL = "pn_bg_shell"

def shell_sitzung_oeffnen():
    zeichen = secrets.token_hex(16)
    with _SHELL_LK:
        _SHELL_OFFEN.add(zeichen)
    return zeichen

def shell_sitzung_gilt(zeichen):
    if not zeichen:
        return False
    with _SHELL_LK:
        return zeichen in _SHELL_OFFEN

def shell_sitzung_schliessen(zeichen):
    with _SHELL_LK:
        _SHELL_OFFEN.discard(zeichen)

def shell_zeichen_aus(handler):
    ck = handler.headers.get("Cookie") or ""
    for teil in ck.split(";"):
        k, _, v = teil.strip().partition("=")
        if k == COOKIE_SHELL:
            return v
    return ""

def zeremonie_beginnen(wer):
    re_id = secrets.token_hex(6)
    zahl = "%06d" % secrets.randbelow(1000000)
    with _SHELL_LK:
        _SHELL_ZER[re_id] = {"zahl": zahl, "wer": wer}
    log("shell.zeremonie.start re=%s von=%s" % (re_id, wer))
    return {"re": re_id, "zahl": zahl,
            "rueckmeldung": "Es wird eine WURZEL-SHELL auf dieser Box geoeffnet. "
                            "Sie hat sudo — also die ganze Maschine. "
                            "Der Vorgang steht im Protokoll dieser Konsole."}

def zeremonie_bestaetigen(re_id, getippt, wer):
    with _SHELL_LK:
        eintrag = _SHELL_ZER.pop(re_id, None)
    if not eintrag:
        return None
    if not hmac.compare_digest(str(getippt or "").strip(), eintrag["zahl"]):
        log("shell.zeremonie.falsch re=%s von=%s" % (re_id, wer))
        return None
    marke = secrets.token_hex(16)
    with _SHELL_LK:
        _SHELL_FREI.add(marke)
    log("shell.zeremonie.bestaetigt re=%s von=%s" % (re_id, wer))
    return marke

def zeremonie_einloesen(marke):

    if not marke:
        return False
    with _SHELL_LK:
        if marke in _SHELL_FREI:
            _SHELL_FREI.discard(marke)
            return True
    return False

def _mdns_name():

    try:
        with open("/etc/avahi/avahi-daemon.conf") as f:
            for zeile in f:
                s = zeile.strip()
                if s.startswith("#") or "=" not in s:
                    continue
                schluessel, _, wert = s.partition("=")
                if schluessel.strip() == "host-name":
                    name = "".join(c for c in wert.strip().lower()
                                   if c.isalnum() or c in "-")
                    if name:
                        return name + ".local"
    except OSError:
        pass
    return ""

def _mdns_wirklich():

    try:
        for eintrag in os.listdir("/proc"):
            if not eintrag.isdigit():
                continue
            try:
                with open("/proc/%s/cmdline" % eintrag, "rb") as f:
                    titel = f.read().decode("utf-8", "replace")
            except OSError:
                continue
            if "avahi-daemon:" not in titel:
                continue
            a = titel.find("[")
            b = titel.find("]", a + 1)
            if a > 0 and b > a:
                name = titel[a + 1:b].strip().lower()
                if name.endswith(".local"):
                    return name
    except OSError:
        pass
    return ""

def _rp_id_ermitteln():

    aus_umgebung = (os.environ.get("PN_BG_RP_ID") or "").strip().lower()
    if aus_umgebung:
        return aus_umgebung
    mdns = _mdns_name()
    if mdns:
        return mdns

    import socket
    kurz = socket.gethostname().split(".")[0].lower()
    return (kurz + ".local") if kurz else "localhost"

RP_ID = _rp_id_ermitteln()
RP_NAME = "Brainbox Notfallkonsole"

NAME_WIRKLICH = _mdns_wirklich()

def name_ist_stabil():

    if not NAME_WIRKLICH:
        return True, ""
    if NAME_WIRKLICH == RP_ID:
        return True, ""
    return False, ("Diese Box heisst im Netz gerade %s, nicht %s. Avahi haengt bei einem "
                   "Namenskonflikt eine Ziffer an und zaehlt sie bei jedem weiteren Konflikt "
                   "hoch — ein Passkey wuerde an einen Namen gebunden, den es morgen nicht "
                   "mehr gibt. Erst den Namenskonflikt aufloesen." % (NAME_WIRKLICH, RP_ID))

_ZUSATZ_HOSTS = tuple(n.strip().lower()
                      for n in (os.environ.get("PN_BG_HOSTS") or "").split(",") if n.strip())

ERLAUBTE_HOSTS = tuple(dict.fromkeys(
    n for n in (RP_ID, NAME_WIRKLICH, "localhost", "127.0.0.1") + _ZUSATZ_HOSTS if n))

def ca_pfad():

    p = os.path.join(CFG.get("home") or os.path.expanduser("~"),
                     ".config", "brainbox-portal", "ca", "brainbox-ca.pem")
    return p if os.path.isfile(p) else ""

def passkey_datei():
    return os.path.join(CFG["config_dir"], "passkeys.json")

def b64u_de(s):
    s = str(s or "")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

def b64u_en(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")

def _cbor(b, i=0):
    if i >= len(b):
        raise ValueError("CBOR zu kurz")
    kopf = b[i]
    haupt, zusatz = kopf >> 5, kopf & 0x1F
    i += 1
    if zusatz < 24:
        wert = zusatz
    elif zusatz == 24:
        wert = b[i]; i += 1
    elif zusatz == 25:
        wert = int.from_bytes(b[i:i + 2], "big"); i += 2
    elif zusatz == 26:
        wert = int.from_bytes(b[i:i + 4], "big"); i += 4
    elif zusatz == 27:
        wert = int.from_bytes(b[i:i + 8], "big"); i += 8
    else:
        raise ValueError("CBOR: unbestimmte Laenge wird nicht angenommen")
    if haupt == 0:
        return wert, i
    if haupt == 1:
        return -1 - wert, i
    if haupt == 2:
        return b[i:i + wert], i + wert
    if haupt == 3:
        return b[i:i + wert].decode("utf-8", "replace"), i + wert
    if haupt == 4:
        aus = []
        for _ in range(wert):
            e, i = _cbor(b, i)
            aus.append(e)
        return aus, i
    if haupt == 5:
        aus = {}
        for _ in range(wert):
            k, i = _cbor(b, i)
            v, i = _cbor(b, i)
            aus[k] = v
        return aus, i
    if haupt == 7:
        return {20: False, 21: True, 22: None, 23: None}.get(zusatz), i
    raise ValueError("CBOR: Typ %d wird nicht angenommen" % haupt)

def cbor_lesen(b):
    wert, _ = _cbor(b, 0)
    return wert

def passkeys_laden():
    try:
        with open(passkey_datei(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []

def passkeys_speichern(liste):
    p = passkey_datei()
    tmp = p + ".neu"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(liste, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)

def passkey_vorhanden():

    return bool(passkeys_laden())

def _cose_pruefer(cose):

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
    from cryptography.exceptions import InvalidSignature

    kty = cose.get(1)
    alg = cose.get(3)
    if kty == 2 and alg == -7:
        x = int.from_bytes(cose[-2], "big")
        y = int.from_bytes(cose[-3], "big")
        pub = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()

        def pruefe(sig, daten):
            try:
                pub.verify(sig, daten, ec.ECDSA(hashes.SHA256()))
                return True
            except InvalidSignature:
                return False
        return alg, pruefe
    if kty == 3 and alg == -257:
        n = int.from_bytes(cose[-1], "big")
        e = int.from_bytes(cose[-2], "big")
        pub = rsa.RSAPublicNumbers(e, n).public_key()

        def pruefe(sig, daten):
            try:
                pub.verify(sig, daten, padding.PKCS1v15(), hashes.SHA256())
                return True
            except InvalidSignature:
                return False
        return alg, pruefe
    if kty == 1 and alg == -8:
        pub = ed25519.Ed25519PublicKey.from_public_bytes(cose[-2])

        def pruefe(sig, daten):
            try:
                pub.verify(sig, daten)
                return True
            except InvalidSignature:
                return False
        return alg, pruefe
    raise ValueError("Schluesseltyp/Verfahren wird nicht gefuehrt (kty=%r alg=%r)" % (kty, alg))

def _authdata(b):
    if len(b) < 37:
        raise ValueError("authData zu kurz")
    d = {"rpIdHash": b[:32], "flags": b[32], "signCount": int.from_bytes(b[33:37], "big")}
    d["up"] = bool(d["flags"] & 0x01)
    d["uv"] = bool(d["flags"] & 0x04)
    if d["flags"] & 0x40:
        i = 37
        d["aaguid"] = b[i:i + 16]; i += 16
        clen = int.from_bytes(b[i:i + 2], "big"); i += 2
        d["credId"] = b[i:i + clen]; i += clen
        anfang = i
        cose, i = _cbor(b, i)
        d["cose"] = cose

        d["cose_bytes"] = b[anfang:i]
    return d

def _client_data_pruefen(roh, erwarteter_typ, herausforderung, host):

    d = json.loads(roh.decode("utf-8"))
    if d.get("type") != erwarteter_typ:
        return None, "falscher Typ (%s)" % d.get("type")
    if not hmac.compare_digest(str(d.get("challenge") or ""), herausforderung):
        return None, "Herausforderung passt nicht"
    herkunft = str(d.get("origin") or "")
    erlaubt = ["https://" + RP_ID, "https://" + RP_ID + ":8090"]
    if host:
        erlaubt.append("https://" + host)
    if herkunft not in erlaubt:
        return None, "fremde Herkunft (%s)" % herkunft
    return d, ""

_PK_LK = threading.Lock()
_PK_OFFEN = {}

def _herausforderung_neu(art):
    h = b64u_en(secrets.token_bytes(32))
    with _PK_LK:

        if len(_PK_OFFEN) > 32:
            _PK_OFFEN.clear()
        _PK_OFFEN[h] = {"art": art}
    return h

def _herausforderung_einloesen(h, art):
    with _PK_LK:
        e = _PK_OFFEN.pop(h, None)
    return bool(e and e.get("art") == art)

def passkey_anlegen_start():
    stabil, grund = name_ist_stabil()
    if not stabil:
        return {"ok": False, "grund": grund}
    h = _herausforderung_neu("anlegen")

    return {"ok": True, "challenge": h,
            "rp": {"id": RP_ID, "name": RP_NAME},
            "user": {"id": b64u_en(b"owner"), "name": "owner", "displayName": "Owner"},
            "pubKeyCredParams": [{"type": "public-key", "alg": -7},
                                 {"type": "public-key", "alg": -257},
                                 {"type": "public-key", "alg": -8}],
            "authenticatorSelection": {"residentKey": "required",
                                       "userVerification": "required"},
            "attestation": "none",
            "excludeCredentials": [{"type": "public-key", "id": k["cred_id"]}
                                   for k in passkeys_laden()]}

def passkey_anlegen_fertig(koerper, host, wer):
    h = str(koerper.get("challenge") or "")
    if not _herausforderung_einloesen(h, "anlegen"):
        return {"ok": False, "grund": "Herausforderung unbekannt oder schon benutzt"}
    try:
        cd = b64u_de(koerper.get("clientDataJSON"))
        ao = cbor_lesen(b64u_de(koerper.get("attestationObject")))
        _d, fehler = _client_data_pruefen(cd, "webauthn.create", h, host)
        if fehler:
            return {"ok": False, "grund": fehler}
        ad = _authdata(ao["authData"])
        if not hmac.compare_digest(ad["rpIdHash"], hashlib.sha256(RP_ID.encode()).digest()):
            return {"ok": False, "grund": "Schluessel gehoert zu einem anderen Namen"}
        if not ad.get("up"):
            return {"ok": False, "grund": "Geraet wurde nicht beruehrt"}
        if "cose" not in ad:
            return {"ok": False, "grund": "kein Schluessel in der Antwort"}
        alg, _pruefe = _cose_pruefer(ad["cose"])
    except Exception as e:
        return {"ok": False, "grund": "%s: %s" % (type(e).__name__, e)}

    liste = passkeys_laden()
    cid = b64u_en(ad["credId"])
    if any(k["cred_id"] == cid for k in liste):
        return {"ok": False, "grund": "dieser Passkey ist schon eingetragen"}
    liste.append({"cred_id": cid,
                  "cose": b64u_en(ad["cose_bytes"]),
                  "alg": alg, "sign_count": ad["signCount"],
                  "label": str(koerper.get("label") or "Geraet")[:60],
                  "uv": bool(ad.get("uv")),
                  "angelegt": int(time.time())})
    passkeys_speichern(liste)
    log("passkey.angelegt cred=%s alg=%s von=%s" % (cid[:12], alg, wer))
    return {"ok": True, "cred_id": cid, "anzahl": len(liste),
            "hinweis": "Ab jetzt ist der Passkey der Ausweis dieser Konsole. Der PIN-Weg und "
                       "der Zugriff ueber die nackte IP sind damit geschlossen."}

def passkey_anmelden_start():
    if not passkey_vorhanden():
        return {"ok": False, "grund": "kein Passkey eingetragen"}
    h = _herausforderung_neu("anmelden")
    return {"ok": True, "challenge": h, "rpId": RP_ID,
            "userVerification": "required",
            "allowCredentials": [{"type": "public-key", "id": k["cred_id"]}
                                 for k in passkeys_laden()]}

def passkey_anmelden_fertig(koerper, host, wer):
    h = str(koerper.get("challenge") or "")
    if not _herausforderung_einloesen(h, "anmelden"):
        return {"ok": False, "grund": "Herausforderung unbekannt oder schon benutzt"}
    cid = str(koerper.get("id") or "")
    liste = passkeys_laden()
    eintrag = next((k for k in liste if k["cred_id"] == cid), None)
    if eintrag is None:
        log("passkey.unbekannt cred=%s von=%s" % (cid[:12], wer))
        return {"ok": False, "grund": "unbekannter Passkey"}
    try:
        cd = b64u_de(koerper.get("clientDataJSON"))
        adr = b64u_de(koerper.get("authenticatorData"))
        sig = b64u_de(koerper.get("signature"))
        _d, fehler = _client_data_pruefen(cd, "webauthn.get", h, host)
        if fehler:
            return {"ok": False, "grund": fehler}
        ad = _authdata(adr)
        if not hmac.compare_digest(ad["rpIdHash"], hashlib.sha256(RP_ID.encode()).digest()):
            return {"ok": False, "grund": "Schluessel gehoert zu einem anderen Namen"}
        if not ad.get("up"):
            return {"ok": False, "grund": "Geraet wurde nicht beruehrt"}
        if not ad.get("uv"):

            return {"ok": False, "grund": "ohne Fingerabdruck/Gesicht/Geraete-PIN"}
        _alg, pruefe = _cose_pruefer(cbor_lesen(b64u_de(eintrag["cose"])))
        if not pruefe(sig, adr + hashlib.sha256(cd).digest()):
            log("passkey.signatur_falsch cred=%s von=%s" % (cid[:12], wer))
            return {"ok": False, "grund": "Signatur stimmt nicht"}

        neu = ad["signCount"]
        alt = int(eintrag.get("sign_count") or 0)
        if neu and alt and neu <= alt:
            log("passkey.zaehler_rueckwaerts cred=%s alt=%d neu=%d von=%s" % (cid[:12], alt, neu, wer))
            return {"ok": False, "grund": "Zaehler laeuft rueckwaerts — moeglicher Klon"}
    except Exception as e:
        return {"ok": False, "grund": "%s: %s" % (type(e).__name__, e)}

    eintrag["sign_count"] = ad["signCount"]
    eintrag["zuletzt"] = int(time.time())
    passkeys_speichern(liste)
    log("passkey.anmeldung OK cred=%s label=%r von=%s" % (cid[:12], eintrag.get("label"), wer))
    return {"ok": True, "label": eintrag.get("label")}

LAGE_HTML = r"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}
body{margin:0;font:16px/1.5 system-ui,sans-serif;background:#0b0f14;color:#dbe2ea}
header{position:sticky;top:0;background:#0b0f14ee;border-bottom:1px solid #1d2735;padding:10px 12px;
 display:flex;gap:10px;align-items:center;flex-wrap:wrap}
header b{font-size:15px}header a{color:#7dd3fc;font-size:13px;text-decoration:none}
main{padding:12px;max-width:820px;margin:0 auto}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#7f8ea3;margin:18px 0 8px}
.knoepfe{display:flex;gap:8px;flex-wrap:wrap}
button{font:inherit;font-size:15px;padding:11px 16px;border-radius:10px;border:1px solid #2a3a4d;
 background:#16202c;color:#dbe2ea;cursor:pointer;min-height:46px}
button:active{background:#1d2b3a}
button.gefahr{border-color:#7f1d1d;background:#2a1416;color:#fca5a5}
pre{white-space:pre-wrap;word-break:break-word;background:#070b0f;border:1px solid #1d2735;
 border-radius:10px;padding:10px;font:13px/1.45 ui-monospace,monospace;overflow-x:auto;margin:10px 0 0}
.hinweis{font-size:13px;color:#9aa7b5;line-height:1.5;border:1px solid #1d2735;border-radius:10px;
 padding:10px;background:#0e141c;margin-top:10px}
.zer{border:1px solid #7f1d1d;background:#160e11;border-radius:10px;padding:12px;margin-top:12px}
.zahl{font:22px ui-monospace,monospace;letter-spacing:.16em;color:#fca5a5}
input{font:inherit;font-size:18px;padding:10px;border-radius:8px;border:1px solid #2a3a4d;
 background:#0b1119;color:#dbe2ea;width:170px;letter-spacing:.14em}
</style>
<header><b>%(title)s</b> <a href="/chat">Chat</a> <a href="/passkey">Passkeys</a>
 <span id="st" style="margin-left:auto;font-size:12px;opacity:.6"></span></header>
<main>
<h2>Zugang</h2>
<div id="zugang" class="hinweis">wird geladen &hellip;</div>

<h2>Lagebild &middot; nur lesen</h2>
<div class="knoepfe">
  <button onclick="hol('dienste')">Dienste</button>
  <button onclick="hol('platte')">Platte</button>
  <button onclick="hol('speicher')">Speicher</button>
  <button onclick="hol('netz')">Netz</button>
  <button onclick="hol('wirt')">Wirt-Verdacht</button>
  <button onclick="hol('log:portal')">Portal-Protokoll</button>
  <button onclick="hol('log:konsole')">Konsolen-Protokoll</button>
</div>
<pre id="aus">Ein Handgriff oben waehlen.</pre>

<h2>Wurzel-Shell</h2>
<div class="knoepfe"><button class="gefahr" onclick="zerStart()">Wurzel-Shell eroeffnen</button></div>
<div id="zer"></div>
<div class="hinweis">
<b>Was hier passiert:</b> es &ouml;ffnet sich ein Terminal auf dieser Box, angemeldet als der
Box-Benutzer, mit <code>sudo</code> &mdash; also die ganze Maschine. Zur Best&auml;tigung wird
eine sechsstellige Zahl angezeigt, die abgetippt werden muss; ein blosses &bdquo;Ja&ldquo;
&ouml;ffnet nichts. Der Vorgang steht im Konsolen-Protokoll (Knopf oben).
Eine <b>Uhr l&auml;uft nicht</b>: wer drin ist, arbeitet so lange er will. Einmalig ist nur das
&Ouml;ffnen &mdash; beim n&auml;chsten Mal braucht es wieder die Zahl.
</div>

<h2>Wirkende Handgriffe</h2>
<div id="wirkend" class="hinweis">wird geladen &hellip;</div>
</main>
<script>
var aus=document.getElementById("aus"),zer=document.getElementById("zer"),st=document.getElementById("st");
var RP="%(name)s";

/* Zugang + wirkende Handgriffe: BEIDE Kaesten sagen den ZUSTAND und den NAECHSTEN SCHRITT.
   Ein Text, der nur aufzaehlt, was es nicht gibt, hilft im Notfall niemandem. */
function esc(s){var d=document.createElement("div");d.textContent=s;return d.innerHTML;}
function zugangMalen(d){
  var z=document.getElementById("zugang"), w=document.getElementById("wirkend");
  var hier=(d.hier||"").split(":")[0];
  var stimmt=(hier===d.rp)||(hier==="localhost")||(hier==="127.0.0.1");
  var t="";
  if(d.name_stabil===false){
    t+="<div style='color:#fcd34d;margin-bottom:8px'><b>&#9888; Namenskonflikt.</b> "+
       esc(d.name_grund||"")+" Solange das so ist, findet kein Ger&auml;t die Box unter "+
       "ihrem Produktnamen, und es kann kein Passkey angelegt werden.</div>";
  }
  t+="<div>Name dieser Konsole: <code>"+esc(d.rp)+"</code>"+
     (d.name_wirklich&&d.name_wirklich!==d.rp?(" &mdash; im Netz aber <code>"+
      esc(d.name_wirklich)+"</code>"):"")+"<br>";
  t+="Ge&ouml;ffnet als: <code>"+esc(hier||"?")+"</code>"+(stimmt?" &check;":" &mdash; nicht der Name")+"<br>";
  t+="Passkeys eingetragen: <b>"+d.passkeys+"</b>"+(d.passkeys?"":" &mdash; es gilt noch die Owner-PIN")+"</div>";
  if(d.passkeys>0){
    t+="<div style='margin-top:8px'><b>Passkey aktiv.</b> Die PIN &ouml;ffnet diese Konsole nicht "+
       "mehr, und sie ist nur noch unter <code>"+esc(d.rp)+"</code> erreichbar. "+
       "<a href='/passkey'>Schl&uuml;ssel verwalten</a></div>";
  } else if(d.name_stabil===false){
    t+="<div style='margin-top:8px'><b>N&auml;chster Schritt:</b> den Namenskonflikt aufl&ouml;sen. "+
       "Bis dahin bleibt die Owner-PIN der Weg, und die Konsole sperrt sich nicht zu.</div>";
  } else if(stimmt){
    t+="<div style='margin-top:8px'><b>N&auml;chster Schritt:</b> "+
       "<a href='/passkey'>Passkey auf diesem Ger&auml;t anlegen</a>. "+
       "Danach &ouml;ffnet Fingerabdruck oder Gesicht diese Konsole &mdash; und die wirkenden "+
       "Handgriffe unten schalten sich frei.</div>";
  } else {
    t+="<div style='margin-top:8px'><b>N&auml;chster Schritt:</b> diese Seite unter ihrem "+
       "<b>Namen</b> &ouml;ffnen &mdash; <a href='https://"+esc(d.rp)+":8090/'>https://"+
       esc(d.rp)+":8090</a>. Ein Passkey l&auml;sst sich nur dort anlegen: er wird an einen "+
       "Namen gebunden, nie an eine Adresse. Auf der "+
       "<a href='/passkey'>Passkey-Seite</a> steht eine Probe, die sagt, ob dieses Ger&auml;t "+
       "den Namen findet.</div>";
  }
  if(d.ca){
    t+="<div style='margin-top:8px'>Traut dieses Ger&auml;t der Box noch nicht (Warnseite beim "+
       "&Ouml;ffnen), dann fehlt ihm das Wurzelzertifikat: "+
       "<a href='/ca.crt' download='brainbox-ca.crt'>brainbox-ca.crt laden</a>.</div>";
  }
  z.innerHTML=t;

  if(d.passkeys>0){
    w.innerHTML="<b>Freigeschaltet, sobald sie gebaut sind.</b> Der Ausweis dieser Sitzung "+
      "tr&auml;gt sie: mit Passkey d&uuml;rfen Dienst-Neustart und Box-Neustart mit einem Tipp "+
      "gehen. Bis dahin f&uuml;hrt der Weg &uuml;ber die Wurzel-Shell.";
  } else {
    w.innerHTML="Dienst neu starten, Box neu starten &mdash; mit <b>einem</b> Tipp gibt es das "+
      "hier bewusst nicht, solange nur die sechsstellige PIN dahintersteht. "+
      "<b>Die St&auml;rke des Ausweises entscheidet, was die T&uuml;r darf.</b> "+
      "Mit einem Passkey kommen sie; bis dahin ist die Wurzel-Shell oben der Weg &mdash; "+
      "sie verlangt die Zahl und schreibt mit.";
  }
}
fetch("/api/zugang",{credentials:"same-origin"}).then(function(r){return r.json();})
 .then(zugangMalen)
 .catch(function(e){document.getElementById("zugang").textContent="Zugangslage nicht lesbar: "+e;});
function hol(was){
  aus.textContent="…";
  var u=was.indexOf("log:")===0?("/api/lage?log="+encodeURIComponent(was.slice(4))):("/api/lage?was="+encodeURIComponent(was));
  fetch(u,{credentials:"same-origin"}).then(function(r){return r.json();}).then(function(d){
    aus.textContent=(d.titel?("— "+d.titel+" —\n\n"):"")+(d.text||"(leer)");
  }).catch(function(e){aus.textContent="Fehler: "+e;});
}
function zerStart(){
  fetch("/shell/anfordern",{method:"POST",credentials:"same-origin"}).then(function(r){return r.json();}).then(function(d){
    if(!d.ok){zer.textContent="Nicht moeglich: "+(d.grund||"unbekannt");return;}
    zer.innerHTML="";
    var k=document.createElement("div");k.className="zer";
    var p=document.createElement("div");p.textContent=d.rueckmeldung;k.appendChild(p);
    var z=document.createElement("div");z.className="zahl";z.textContent=d.zahl;
    var l=document.createElement("div");l.style.marginTop="8px";l.textContent="Zur Bestaetigung diese Zahl eintippen:";
    k.appendChild(l);k.appendChild(z);
    var i=document.createElement("input");i.inputMode="numeric";i.placeholder="Zahl";
    k.appendChild(document.createElement("br"));k.appendChild(i);
    var b=document.createElement("button");b.className="gefahr";b.style.marginLeft="8px";b.textContent="Oeffnen";
    b.onclick=function(){
      fetch("/shell/bestaetigen",{method:"POST",credentials:"same-origin",
        headers:{"Content-Type":"application/json"},body:JSON.stringify({re:d.re,zahl:i.value})})
      .then(function(r){return r.json();}).then(function(e){
        if(e.ok&&e.marke){location.href="/term?marke="+encodeURIComponent(e.marke);}
        else{p.textContent="Nicht bestaetigt. Bitte neu anstossen.";}
      });
    };
    k.appendChild(b);
    var a=document.createElement("button");a.style.marginLeft="8px";a.textContent="Abbrechen";
    a.onclick=function(){zer.innerHTML="";};k.appendChild(a);
    zer.appendChild(k);i.focus();
  });
}
</script>"""

TERM_HTML = r"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<link rel="stylesheet" href="/static/xterm.css">
<style>html,body{margin:0;height:100%%;background:#000}#t{height:100%%}
#bar{position:fixed;top:0;right:0;z-index:9;font:11px system-ui;color:#9aa7b5;background:#0b0f14cc;padding:3px 8px;border-radius:0 0 0 8px}</style>
<div id="bar"><a href="/" style="color:#7dd3fc;text-decoration:none">◂ Lage</a> · <a href="/chat" style="color:#7dd3fc;text-decoration:none">💬 Chat</a> · <a href="/shell/schliessen" style="color:#fca5a5;text-decoration:none">Shell schließen</a> · %(title)s · <span id="st">verbinde …</span></div><div id="t"></div>
<script src="/static/xterm.js"></script><script src="/static/addon-fit.js"></script>
<script>
var term=new Terminal({fontSize:14,scrollback:8000,theme:{background:"#000000"}});
var fit=new FitAddon.FitAddon();term.loadAddon(fit);term.open(document.getElementById("t"));
var st=document.getElementById("st"),ws=null,tries=0;
function connect(){
  var proto=location.protocol==="https:"?"wss://":"ws://";
  ws=new WebSocket(proto+location.host+"/ws");ws.binaryType="arraybuffer";
  ws.onopen=function(){tries=0;st.textContent="verbunden";fit.fit();
    ws.send(JSON.stringify({resize:[term.cols,term.rows]}));term.focus();};
  ws.onmessage=function(e){term.write(new Uint8Array(e.data));};
  ws.onclose=function(e){st.textContent="getrennt ("+(e.code||"?")+") — reconnect …";
    setTimeout(connect,Math.min(10000,1000*(++tries)));};
  ws.onerror=function(){try{ws.close();}catch(x){}};
}
term.onData(function(d){if(ws&&ws.readyState===1)ws.send(new TextEncoder().encode(d));});
term.onResize(function(s){if(ws&&ws.readyState===1)ws.send(JSON.stringify({resize:[s.cols,s.rows]}));});
window.addEventListener("resize",function(){fit.fit();});
setInterval(function(){if(ws&&ws.readyState===1)ws.send(JSON.stringify({ping:1}));},30000);
connect();
</script>"""

CHAT_HTML = r"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s — Chat</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;font:16px/1.5 system-ui,sans-serif;background:#0b0f14;color:#dbe2ea;
 display:flex;flex-direction:column;min-height:100dvh}
header{position:sticky;top:0;z-index:5;background:#0b0f14ee;border-bottom:1px solid #1d2735;
 padding:8px 12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
header b{font-size:14px}header a{color:#7dd3fc;font-size:13px;text-decoration:none}
#st{font-size:12px;opacity:.6;margin-left:auto}
main{flex:1;padding:10px 12px 4px;max-width:900px;width:100%%;margin:0 auto}
.msg{margin:0 0 14px}
.who{font-size:11px;letter-spacing:.04em;text-transform:uppercase;opacity:.55;margin-bottom:3px}
.du .who{color:#93c5fd}.konsole .who{color:#86efac}
.txt{white-space:pre-wrap;word-wrap:break-word;overflow-wrap:anywhere}
.tools{font-size:12px;opacity:.6;margin-top:3px}
.leer{opacity:.6;font-size:14px;border:1px dashed #2c3a4c;border-radius:10px;padding:12px}
#links{background:#101a24;border:1px solid #1e3a52;border-radius:12px;padding:10px 12px;margin:0 0 14px}
#links h2{font-size:13px;margin:0 0 8px;color:#7dd3fc}
.lnk{margin:0 0 10px;padding-bottom:10px;border-bottom:1px solid #17232f}
.lnk:last-child{border:0;padding-bottom:0;margin-bottom:0}
.lnk a{color:#bfe6ff;font-size:13px;word-break:break-all;display:block;margin-bottom:6px}
.warn{font-size:12px;color:#fbbf24;margin:0 0 8px}
.row{display:flex;gap:8px;flex-wrap:wrap}
button{font:inherit;font-size:14px;padding:8px 12px;border-radius:9px;border:1px solid #2c3a4c;
 background:#17222f;color:#dbe2ea;cursor:pointer}
button.p{background:#2563eb;border-color:#2563eb;color:#fff}
button:active{opacity:.7}
pre#term{background:#000;border:1px solid #1d2735;border-radius:10px;padding:10px;font-size:11.5px;
 line-height:1.35;overflow-x:auto;white-space:pre;margin:0 0 12px;max-height:38vh}
footer{position:sticky;bottom:0;background:#0b0f14ee;border-top:1px solid #1d2735;padding:8px 12px 12px}
footer .row{max-width:900px;margin:0 auto}
#in{flex:1;min-width:140px;padding:10px;border-radius:9px;border:1px solid #2c3a4c;background:#0b0f14;
 color:inherit;font:inherit}
details summary{cursor:pointer;font-size:13px;opacity:.75;margin:0 0 8px}
</style>
<header><b>%(title)s</b> <a href="/">Terminal ›</a><span id=st>lädt …</span></header>
<main>
 <div id=links hidden><h2>🔗 Links aus dieser Sitzung</h2><div id=linkbox></div></div>
 <div class=row style="margin:0 0 14px">
  <button class=p onclick="anmelden()">🔐 Anmeldung starten</button>
  <button onclick="taste('esc')">Esc</button>
  <button onclick="taste('enter')">⏎</button>
  <button onclick="breit()">Breit</button>
 </div>
 <details><summary>Terminal-Ende (letzte Zeilen)</summary><pre id=term></pre></details>
 <div id=chat></div>
</main>
<footer><div class=row>
 <input id=in placeholder="Antwort / Code einfügen …" autocomplete=off>
 <button class=p onclick="senden()">Senden</button>
</div></footer>
<script>
var st=document.getElementById("st"),chat=document.getElementById("chat"),
    term=document.getElementById("term"),linkbox=document.getElementById("linkbox"),
    links=document.getElementById("links"),inp=document.getElementById("in");
function esc(s){var d=document.createElement("div");d.textContent=s==null?"":s;return d.innerHTML;}
function kopieren(t,b){
  var fertig=function(){b.textContent="✓ kopiert";setTimeout(function(){b.textContent="Kopieren";},1500);};
  if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(t).then(fertig,alt);}
  else alt();
  function alt(){var e=document.createElement("textarea");e.value=t;e.style.position="fixed";
    e.style.opacity=0;document.body.appendChild(e);e.select();
    try{document.execCommand("copy");fertig();}catch(x){b.textContent="⚠ manuell markieren";}
    document.body.removeChild(e);}
}
function zeigeLinks(us){
  if(!us||!us.length){links.hidden=true;return;}
  links.hidden=false;linkbox.innerHTML="";
  us.forEach(function(l){
    var u=l.url,d=document.createElement("div");d.className="lnk";
    d.innerHTML='<a href="'+esc(u)+'" target="_blank" rel="noreferrer noopener">'+esc(u)+'</a>'+
      (l.gefuegt?'<div class=warn>⚠ aus mehreren Bildschirmzeilen zusammengesetzt — das Ende kann '+
       'fehlen. Sicherer: „Anmeldung starten" oben, dann kommt der Link ungebrochen.</div>':'');
    var r=document.createElement("div");r.className="row";
    var b=document.createElement("button");b.textContent="Kopieren";
    b.onclick=function(){kopieren(u,b);};r.appendChild(b);
    var o=document.createElement("button");o.textContent="Öffnen";
    o.onclick=function(){window.open(u,"_blank","noreferrer");};r.appendChild(o);
    d.appendChild(r);linkbox.appendChild(d);
  });
}
function zeigeChat(z){
  if(z.kein_transkript){
    chat.innerHTML='<div class=leer>Für diese Sitzung liegt kein Gesprächs-Transkript vor — '+
      'sie wurde ohne feste Kennung gestartet. Nach dem nächsten Neustart der Konsole ist es da. '+
      'Links und Eingabe funktionieren trotzdem.</div>';return;}
  if(!z.turns.length){chat.innerHTML='<div class=leer>Noch nichts gesprochen.</div>';return;}
  chat.innerHTML=z.turns.map(function(t){
    var w=t.werkzeuge&&t.werkzeuge.length?'<div class=tools>🔧 '+esc(t.werkzeuge.join(", "))+'</div>':'';
    var k=t.compact?'<div class=tools>— Verlauf zusammengefasst —</div>':'';
    return '<div class="msg '+t.rolle+'"><div class=who>'+(t.rolle=="du"?"Du":"Konsole")+'</div>'+
           k+'<div class=txt>'+esc(t.text)+'</div>'+w+'</div>';}).join("");
}
var beiUns=true;
window.addEventListener("scroll",function(){
  beiUns=(window.innerHeight+window.scrollY)>=(document.body.scrollHeight-80);});
function laden(){
  fetch("/api/chat",{credentials:"same-origin"}).then(function(r){
    if(r.status===401){location.href="/";return null;}return r.json();}).then(function(z){
    if(!z)return; st.textContent=z.session;
    zeigeLinks(z.links);zeigeChat(z);term.textContent=z.terminal||"(leer)";
    if(beiUns)window.scrollTo(0,document.body.scrollHeight);
  }).catch(function(){st.textContent="offline";});
}
function post(pfad,daten){
  return fetch(pfad,{method:"POST",credentials:"same-origin",
    headers:{"Content-Type":"application/x-www-form-urlencoded"},
    body:new URLSearchParams(daten).toString()}).then(function(r){return r.json();});
}
function senden(){
  var t=inp.value;if(!t)return;inp.value="";st.textContent="sende …";
  post("/chat/send",{text:t,taste:"enter"}).then(function(a){
    st.textContent=a.ok?"gesendet":(a.grund||"Fehler");setTimeout(laden,700);});
}
function taste(k){post("/chat/send",{taste:k}).then(function(){setTimeout(laden,600);});}
function breit(){post("/chat/breite",{spalten:200}).then(function(a){st.textContent=a.grund||"";});}
function anmelden(){
  st.textContent="Anmeldung läuft — bitte warten …";
  post("/chat/login",{}).then(function(a){
    st.textContent=a.ok?"Link da":"kein Link";
    if(!a.ok)alert(a.grund||"Kein Anmeldelink erschienen.");
    laden();window.scrollTo(0,0);
  }).catch(function(){st.textContent="Fehler";});
}
inp.addEventListener("keydown",function(e){if(e.key==="Enter")senden();});
laden();setInterval(laden,4000);
</script>"""

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pn-breakglassd"

    def log_message(self, *a):
        pass

    def _page(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/static/"):
            name = path[len("/static/"):]
            if name not in STATIC_OK:
                return self._page(404, "not found", "text/plain")
            for d in CFG["asset_dirs"]:
                fp = os.path.join(d, name)
                if os.path.isfile(fp):
                    with open(fp, "rb") as f:
                        return self._page(200, f.read(), STATIC_OK[name],
                                          [("Cache-Control", "max-age=86400")])
            return self._page(404, "asset missing", "text/plain")
        if path == "/healthz":
            return self._page(200, "ok", "text/plain")
        if path == "/ca.crt":

            p = ca_pfad()
            if not p:
                return self._page(404, "kein Wurzelzertifikat hinterlegt", "text/plain")
            try:
                with open(p, "rb") as f:
                    roh = f.read()
            except OSError as e:
                return self._page(500, "nicht lesbar: %s" % e, "text/plain")
            return self._page(200, roh, "application/x-x509-ca-cert",
                              [("Content-Disposition",
                                'attachment; filename="brainbox-ca.crt"')])
        if not host_erlaubt(self):

            return self._page(421, "Diese Konsole ist nur unter https://%s:8090 erreichbar, "
                                   "seit ein Passkey eingetragen ist — ein Passkey gilt nur "
                                   "fuer diesen Namen. Wenn der Name nicht aufloest: ueber SSH "
                                   "auf die Box, das ist der Hauptweg." % RP_ID, "text/plain")
        auth = authed(self)
        if auth is None:
            return self._page(503, "weder Owner-Konto noch Token — Konsole gesperrt (fail-closed)",
                              "text/plain")
        if path == "/ws":
            if not auth:
                return self._page(401, "unauthorized", "text/plain")

            if not shell_sitzung_gilt(shell_zeichen_aus(self)):
                return self._page(403, "keine eroeffnete Wurzel-Shell — erst ueber die "
                                       "Zeremonie auf der Startseite", "text/plain")
            return self._ws_upgrade()
        if not auth:
            return self._page(200, LOGIN_HTML % {"title": CFG["title"], "token_file": CFG["token_file"], "err": ""})
        if path == "/chat":
            return self._page(200, CHAT_HTML % {"title": CFG["title"]})
        if path == "/api/chat":
            return self._json(200, chat_zustand())
        if path == "/api/lage":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            welches = (qs.get("log") or [""])[0]
            if welches:
                return self._json(200, protokoll_lesen(welches))
            d = handgriff_lesen((qs.get("was") or [""])[0])
            return self._json(200 if d else 404,
                              d or {"titel": "unbekannt", "text": "(diesen Handgriff gibt es nicht)"})
        if path == "/term":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            marke = (qs.get("marke") or [""])[0]
            if zeremonie_einloesen(marke):
                zeichen = shell_sitzung_oeffnen()
                log("shell.geoeffnet von=%s" % self.client_address[0])
                return self._page(200, TERM_HTML % {"title": CFG["title"]}, "text/html; charset=utf-8",
                                  [("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Strict%s"
                                    % (COOKIE_SHELL, zeichen, "; Secure" if CFG.get("tls") else ""))])
            if shell_sitzung_gilt(shell_zeichen_aus(self)):
                return self._page(200, TERM_HTML % {"title": CFG["title"]})
            return self._page(403, "keine eroeffnete Wurzel-Shell — zurueck zur Startseite",
                              "text/plain")
        if path == "/passkey":
            return self._page(200, PASSKEY_HTML % {"title": CFG["title"]})
        if path == "/api/zugang":

            stabil, grund = name_ist_stabil()
            return self._json(200, {"ok": True, "rp": RP_ID,
                                    "hier": (self.headers.get("Host") or ""),
                                    "tls": bool(CFG.get("tls")),
                                    "ca": bool(ca_pfad()),
                                    "name_wirklich": NAME_WIRKLICH,
                                    "name_stabil": stabil, "name_grund": grund,
                                    "passkeys": len(passkeys_laden())})
        if path == "/api/passkeys":
            _stabil, _grund = name_ist_stabil()
            return self._json(200, {"ok": True, "rp": RP_ID, "hier": (self.headers.get("Host") or ""),
                                    "name_wirklich": NAME_WIRKLICH,
                                    "name_stabil": _stabil, "name_grund": _grund,
                                    "schluessel": [{"cred_id": k.get("cred_id"),
                                                    "label": k.get("label"),
                                                    "angelegt": k.get("angelegt"),
                                                    "zuletzt": k.get("zuletzt")}
                                                   for k in passkeys_laden()]})
        if path == "/shell/schliessen":
            shell_sitzung_schliessen(shell_zeichen_aus(self))
            log("shell.geschlossen von=%s" % self.client_address[0])
            return self._page(302, "", "text/plain",
                              [("Location", "/"),
                               ("Set-Cookie", "%s=; Path=/; Max-Age=0" % COOKIE_SHELL)])
        return self._page(200, LAGE_HTML % {"title": CFG["title"], "name": RP_ID})

    def _json(self, code, obj):
        self._page(code, json.dumps(obj), "application/json; charset=utf-8",
                   [("Cache-Control", "no-store")])

    def _chat_post(self, path, felder):

        origin = self.headers.get("Origin") or ""
        if origin and urllib.parse.urlparse(origin).netloc != (self.headers.get("Host") or ""):
            return self._json(403, {"ok": False, "grund": "fremde Herkunft"})
        if path == "/chat/send":
            text = (felder.get("text", [""])[0] or "")[:4000]
            taste = (felder.get("taste", [""])[0] or "").lower()
            if not text and not taste:
                return self._json(400, {"ok": False, "grund": "nichts zu senden"})
            ok, grund = chat_senden(text, taste)
            log("chat send ip=%s zeichen=%d taste=%s ok=%s"
                % (self.client_address[0], len(text), taste or "-", ok))
            return self._json(200 if ok else 500, {"ok": ok, "grund": grund})
        if path == "/chat/breite":
            try:
                sp = int(felder.get("spalten", ["0"])[0])
            except ValueError:
                sp = 0
            ok, grund = chat_breite(sp)
            return self._json(200, {"ok": ok, "grund": grund})
        if path == "/chat/login":
            log("chat login-flow ip=%s" % self.client_address[0])
            ok, was = anmeldung_starten()
            return self._json(200, {"ok": ok, "url": was if ok else "", "grund": "" if ok else was})
        return self._json(404, {"ok": False, "grund": "unbekannt"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/chat/"):
            auth = authed(self)
            if auth is None:
                return self._json(503, {"ok": False, "grund": "Konsole gesperrt (fail-closed)"})
            if not auth:
                return self._json(401, {"ok": False, "grund": "nicht angemeldet"})
            ln = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(ln, 65536)).decode("utf-8", "replace")
            return self._chat_post(path, urllib.parse.parse_qs(body, keep_blank_values=True))
        if path in ("/shell/anfordern", "/shell/bestaetigen"):
            if not authed(self):
                return self._json(401, {"ok": False, "grund": "nicht angemeldet"})

            origin = self.headers.get("Origin") or ""
            if origin and urllib.parse.urlparse(origin).netloc != (self.headers.get("Host") or ""):
                return self._json(403, {"ok": False, "grund": "fremde Herkunft"})
            wer = self.client_address[0]
            if path == "/shell/anfordern":
                d = zeremonie_beginnen(wer)
                d["ok"] = True
                return self._json(200, d)
            try:
                laenge = int(self.headers.get("Content-Length") or 0)
                koerper = json.loads(self.rfile.read(laenge).decode("utf-8") or "{}")
            except Exception:
                koerper = {}
            marke = zeremonie_bestaetigen(koerper.get("re"), koerper.get("zahl"), wer)
            if not marke:
                return self._json(403, {"ok": False, "grund": "nicht bestaetigt"})
            return self._json(200, {"ok": True, "marke": marke})
        if path.startswith("/passkey/"):
            return self._passkey_post(path)
        if path != "/auth":
            return self._page(404, "not found", "text/plain")
        ip = self.client_address[0]
        if not throttle_ok(ip):
            log("login THROTTLED ip=%s" % ip)
            return self._page(429, "zu viele Versuche — 5 Minuten warten", "text/plain")
        ln = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(min(ln, 4096)).decode("utf-8", "replace")
        tok = urllib.parse.parse_qs(body).get("token", [""])[0].strip()
        real = read_token()
        if not (real or owner_login_available()):
            return self._page(503, "weder Owner-Konto noch Token hinterlegt", "text/plain")

        how = ""
        if tok and real and hmac.compare_digest(tok, real):
            how = "token"
        elif tok and owner_login_ok(tok):
            if passkey_vorhanden():

                log("login ABGEWIESEN (PIN, aber Passkey ist eingetragen) ip=%s" % ip)
                return self._page(403, LOGIN_HTML % {
                    "title": CFG["title"], "token_file": CFG["token_file"],
                    "err": "<div class=err>Diese Konsole wird mit dem Passkey geoeffnet. "
                           "Die PIN gilt hier nicht mehr.</div>"})
            how = "pin"
        if how:
            log("login OK ip=%s via=%s" % (ip, how))
            ck = "%s=%s; HttpOnly; SameSite=Strict; Path=/%s" % (
                COOKIE, cookie_value(_cookie_secret()), "; Secure" if CFG["tls"] else "")
            return self._page(303, "", extra=[("Set-Cookie", ck), ("Location", "/")])
        throttle_fail(ip)
        log("login FAIL ip=%s" % ip)
        return self._page(401, LOGIN_HTML % {"title": CFG["title"], "token_file": CFG["token_file"],
                                             "err": "<div class=err>Zugang falsch.</div>"})

    def _passkey_post(self, path):

        wer = self.client_address[0]
        origin = self.headers.get("Origin") or ""
        host_h = self.headers.get("Host") or ""
        if origin and urllib.parse.urlparse(origin).netloc != host_h:
            return self._json(403, {"ok": False, "grund": "fremde Herkunft"})
        try:
            laenge = int(self.headers.get("Content-Length") or 0)
            koerper = json.loads(self.rfile.read(min(laenge, 65536)).decode("utf-8") or "{}")
        except Exception:
            koerper = {}

        if path in ("/passkey/anlegen/start", "/passkey/anlegen/fertig", "/passkey/entfernen"):
            if not authed(self):
                return self._json(401, {"ok": False, "grund": "nicht angemeldet"})
            if path == "/passkey/anlegen/start":
                return self._json(200, passkey_anlegen_start())
            if path == "/passkey/anlegen/fertig":
                d = passkey_anlegen_fertig(koerper, host_h, wer)
                return self._json(200 if d.get("ok") else 400, d)
            cid = str(koerper.get("cred_id") or "")
            liste = passkeys_laden()
            rest = [k for k in liste if k.get("cred_id") != cid]
            if len(rest) == len(liste):
                return self._json(404, {"ok": False, "grund": "unbekannter Passkey"})
            passkeys_speichern(rest)
            log("passkey.entfernt cred=%s von=%s" % (cid[:12], wer))
            return self._json(200, {"ok": True, "verbleibend": len(rest)})

        if path == "/passkey/anmelden/start":
            if not throttle_ok(wer):
                return self._json(429, {"ok": False, "grund": "zu viele Versuche"})
            d = passkey_anmelden_start()
            return self._json(200 if d.get("ok") else 400, d)

        if path == "/passkey/anmelden/fertig":
            if not throttle_ok(wer):
                return self._json(429, {"ok": False, "grund": "zu viele Versuche"})
            d = passkey_anmelden_fertig(koerper, host_h, wer)
            if not d.get("ok"):
                throttle_fail(wer)
                return self._json(403, d)
            ck = "%s=%s; HttpOnly; SameSite=Strict; Path=/%s" % (
                COOKIE, cookie_value(_cookie_secret()), "; Secure" if CFG["tls"] else "")
            return self._page(200, json.dumps(d), "application/json; charset=utf-8",
                              [("Set-Cookie", ck), ("Cache-Control", "no-store")])

        return self._json(404, {"ok": False, "grund": "unbekannter Weg"})

    def _ws_upgrade(self):

        origin = self.headers.get("Origin") or ""
        host = self.headers.get("Host") or ""
        if origin:
            oh = urllib.parse.urlparse(origin).netloc
            if oh != host:
                return self._page(403, "origin mismatch", "text/plain")
        key = self.headers.get("Sec-WebSocket-Key")
        if not key or (self.headers.get("Upgrade") or "").lower() != "websocket":
            return self._page(400, "not a websocket request", "text/plain")
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", ws_accept_key(key))
        self.end_headers()
        try:
            ws_terminal(self)
        finally:
            self.close_connection = True

class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

def ensure_token():
    tf = CFG["token_file"]
    if not os.path.isfile(tf):
        os.makedirs(os.path.dirname(tf), mode=0o700, exist_ok=True)
        fd = os.open(tf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32) + "\n")
        log("token gemintet: %s" % tf)

def find_tls(args):

    if args.cert and args.key and os.path.isfile(args.cert) and os.path.isfile(args.key):
        return args.cert, args.key
    leaf = os.path.join(CFG["home"], ".config", "brainbox-portal")
    c, k = os.path.join(leaf, "cert.pem"), os.path.join(leaf, "key.pem")
    if os.path.isfile(c) and os.path.isfile(k) and os.access(k, os.R_OK):
        return c, k
    c = os.path.join(CFG["config_dir"], "selfsigned-cert.pem")
    k = os.path.join(CFG["config_dir"], "selfsigned-key.pem")
    if not (os.path.isfile(c) and os.path.isfile(k)):
        try:
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                            "-keyout", k, "-out", c, "-days", "825",
                            "-subj", "/CN=%s" % socket.gethostname()],
                           check=True, capture_output=True, timeout=60)
            os.chmod(k, 0o600)
            log("self-signed Zertifikat gemintet")
        except Exception as e:
            log("WARN: kein TLS moeglich (%s)" % e)
            return None, None
    return c, k

def main():
    ap = argparse.ArgumentParser(description="Break-Glass/Dev Browser-Terminal (portal-unabhaengig)")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--session", default="breakglass", help="tmux-Session-Name")
    ap.add_argument("--cmd", default="bash -lc 'claude; exec bash -l'",
                    help="Kommando in der tmux-Session")
    ap.add_argument("--cwd", default=os.path.expanduser("~"))
    ap.add_argument("--title", default=None)
    ap.add_argument("--cert")
    ap.add_argument("--key")
    ap.add_argument("--config-dir", default=os.path.expanduser("~/.config/pn-breakglass"))
    ap.add_argument("--assets", action="append", default=[],
                    help="Verzeichnis mit xterm.js/xterm.css/addon-fit.js (mehrfach erlaubt)")
    args = ap.parse_args()

    home = os.path.expanduser("~")
    CFG.update({
        "home": home, "user": os.environ.get("USER") or "user",
        "session": args.session, "cmd": args.cmd, "cwd": args.cwd,
        "title": args.title or ("🛟 " + args.session + " @ " + socket.gethostname()),
        "config_dir": args.config_dir,
        "token_file": os.path.join(args.config_dir, "token"),
        "asset_dirs": args.assets + [
            "/usr/local/share/brainbox-breakglass/static",
            os.path.join(home, "brainarbeit", "cockpit", "server", "webapp", "static"),
        ],
        "tls": False,
    })
    os.makedirs(CFG["config_dir"], mode=0o700, exist_ok=True)
    ensure_token()

    cert, key = find_tls(args)
    host = args.host
    if not cert:
        host = "127.0.0.1"
        log("TLS fehlt -> binde NUR loopback")
    srv = Server((host, args.port), Handler)
    if cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        CFG["tls"] = True
    log("pn-breakglassd bereit auf %s:%d (tls=%s, session=%s)"
        % (host, args.port, CFG["tls"], CFG["session"]))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
