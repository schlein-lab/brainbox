
import os, json, socket, signal, subprocess, time
import re, threading, shutil
import urllib.parse, urllib.request

HOME = os.path.expanduser("~")
DATA_DIR = os.path.join(HOME, ".local", "share", "brainbox-portal")
DEFAULT_PRINCIPAL = "owner"

SEAT_RUNTIME = None
SEAT_SIZE = None
SEAT_WL = None
_cell_launch = None
_nice_prefix = None
_seat_bin = None
_uid_safe = None
user_dir = None

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

XDG_RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
CELLS_RUNTIME = os.path.join(XDG_RUNTIME, "cells")
CELL_INDEX_FILE = os.path.join(DATA_DIR, "cell-index.json")
_CELLS = {}
_CELL_LOCK = threading.Lock()

def _cell_index(uid):

    uid = _uid_safe(uid)
    if uid == DEFAULT_PRINCIPAL:
        return 0
    with _CELL_LOCK:
        try:
            with open(CELL_INDEX_FILE) as f: m = json.load(f)
        except Exception:
            m = {}
        if uid not in m:
            used = set(m.values()) | {0}
            i = 1
            while i in used: i += 1
            m[uid] = i
            try:
                with open(CELL_INDEX_FILE, "w") as f: json.dump(m, f)
            except Exception: pass
        return m[uid]

class Cell:

    def __init__(self, uid):
        self.uid = _uid_safe(uid)
        self.idx = _cell_index(self.uid)
        legacy = (self.uid == DEFAULT_PRINCIPAL)
        self.runtime = SEAT_RUNTIME if legacy else os.path.join(CELLS_RUNTIME, self.uid)
        self.ctl = os.path.join(self.runtime, "phantom.ctl")
        self.wayland = os.path.join(self.runtime, SEAT_WL)
        self.xdg = XDG_RUNTIME if legacy else self.runtime
        self.rfb_port = 5900 + self.idx
        self.stream_port = 8092 + self.idx
        self.vnc = "127.0.0.1:%d" % self.rfb_port
        self.stream = "127.0.0.1:%d" % self.stream_port
        self.dbus = "unix:path=%s/bus" % (XDG_RUNTIME if legacy else self.runtime)
        self.profile = None if legacy else os.path.join(user_dir(self.uid), "firefox")

def cell(uid=DEFAULT_PRINCIPAL):
    return Cell(uid)

def _cell_procs(uid):
    return _CELLS.setdefault(_uid_safe(uid), {"comp": None, "apps": [], "ff": None})

def seat_running(uid=DEFAULT_PRINCIPAL):

    p = _cell_procs(uid)["comp"]
    if p is not None and p.poll() is None:
        return True

    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(1.0); c.connect(cell(uid).ctl); c.close()
        return True
    except Exception:
        return False
_RENDER_ENV = None
def _render_env():

    global _RENDER_ENV
    if _RENDER_ENV is not None:
        return _RENDER_ENV
    force = os.environ.get("PHANTOM_SOFTWARE_GL")
    if force == "0":
        _RENDER_ENV = {}
        return _RENDER_ENV
    soft = True
    if force != "1":
        try:
            import glob as _glob
            nodes = sorted(_glob.glob("/dev/dri/renderD*")) + sorted(_glob.glob("/dev/dri/card*"))
            for n in nodes:
                if os.access(n, os.R_OK | os.W_OK):
                    soft = False
                    break
        except Exception:
            soft = True
    _RENDER_ENV = {
        "LIBGL_ALWAYS_SOFTWARE": "1",
        "GALLIUM_DRIVER": "llvmpipe",
        "MESA_LOADER_DRIVER_OVERRIDE": "llvmpipe",
        "MOZ_DISABLE_WAYLAND_DMABUF": "1",
    } if soft else {}
    return _RENDER_ENV

def _ensure_browser_swgl_prefs():

    if not _render_env():
        return []
    import glob as _glob
    home = os.path.expanduser("~")
    roots = [os.path.join(home, ".mozilla", "firefox"),
             os.path.join(home, ".config", "mozilla", "firefox")]
    lines = [
        "// phantom seat: no usable GPU -> Firefox native software rendering (SWGL).",
        'user_pref("gfx.webrender.software", true);',
        'user_pref("gfx.webrender.all", true);',
        'user_pref("gfx.canvas.accelerated", false);',
        'user_pref("layers.gpu-process.enabled", false);',
        'user_pref("media.hardware-video-decoding.enabled", false);',
        'user_pref("dom.webgpu.enabled", false);',
        'user_pref("media.ffmpeg.vaapi.enabled", false);',
        "",
    ]
    block = chr(10).join(lines)
    wrote = []
    for root in roots:
        for prof in _glob.glob(os.path.join(root, "*.default*")):
            if not os.path.isdir(prof):
                continue
            uj = os.path.join(prof, "user.js")
            try:
                cur = open(uj).read() if os.path.exists(uj) else ""
                if "media.ffmpeg.vaapi.enabled" in cur:
                    continue
                with open(uj, "a") as f:
                    if cur and not cur.endswith(chr(10)):
                        f.write(chr(10))
                    f.write(block)
                wrote.append(uj)
            except Exception:
                pass
    return wrote

def _ensure_firefox_ui():

    import glob, json as _json
    home = os.path.expanduser("~")
    roots = [os.path.join(home, ".mozilla", "firefox"),
             os.path.join(home, ".config", "mozilla", "firefox")]
    for root in roots:
        for prof in glob.glob(os.path.join(root, "*.default*")):
            if not os.path.isdir(prof):
                continue
            xp = os.path.join(prof, "xulstore.json")
            try:
                try:
                    x = _json.load(open(xp))
                except Exception:
                    x = {}
                mb = x.setdefault("chrome://browser/content/browser.xhtml", {}).setdefault("toolbar-menubar", {})
                if mb.get("autohide") != "true":
                    mb["autohide"] = "true"
                    _json.dump(x, open(xp, "w"))
            except Exception:
                pass

def _ensure_cell_dbus(uid=DEFAULT_PRINCIPAL):

    addr = cell(uid).dbus
    sock = addr[len("unix:path="):] if addr.startswith("unix:path=") else ""
    try:
        if sock and not os.path.exists(sock):
            os.makedirs(os.path.dirname(sock), exist_ok=True)
            import shutil
            if shutil.which("dbus-daemon"):
                subprocess.run(["dbus-daemon", "--session", "--address=" + addr,
                                "--nopidfile", "--fork"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                for _ in range(20):
                    if os.path.exists(sock):
                        break
                    time.sleep(0.1)
    except Exception:
        pass
    return addr

def _ensure_user_dbus():
    return _ensure_cell_dbus(DEFAULT_PRINCIPAL)

SEAT_READY_TIMEOUT = float(os.environ.get("PHANTOM_SEAT_READY_TIMEOUT", "25"))

def _seat_bin_missing_msg():

    try:
        from portal_email_portioneer import _seat_bin_diag
        return _seat_bin_diag()
    except Exception:
        return ("Die Bildschirm-Komponente (phantom) fehlt in dieser Installation — der Bildschirm "
                "kann deshalb nicht starten. Bitte das Systemabbild aktualisieren bzw. neu einspielen.")

def _port_open(hostport, timeout=0.4):

    try:
        host, _, port = hostport.rpartition(":")
        s = socket.create_connection((host or "127.0.0.1", int(port)), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False

def _unix_open(path, timeout=0.4):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout); s.connect(path); s.close()
        return True
    except Exception:
        return False

def _seat_wait_ready(uid, proc=None, timeout=None):

    c = cell(uid)
    deadline = time.time() + (SEAT_READY_TIMEOUT if timeout is None else timeout)
    missing = "noch nichts"
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False, ("Der Compositor-Prozess hat sich sofort wieder beendet (Code %s)"
                           % proc.poll())
        have_ctl = _unix_open(c.ctl)
        have_vnc = _port_open(c.vnc)
        have_str = _port_open(c.stream)
        if have_ctl and have_vnc and have_str:
            return True, ""
        missing = ", ".join(n for n, ok in (("Steuersocket", have_ctl), ("Bildkanal (VNC)", have_vnc),
                                            ("Videokanal (MJPEG)", have_str)) if not ok)
        time.sleep(0.25)
    return False, "Zeitüberschreitung nach %gs — nicht bereit: %s" % (
        (SEAT_READY_TIMEOUT if timeout is None else timeout), missing)

def _seat_log_tail(uid, n=400):

    try:
        with open(os.path.join(cell(uid).runtime, "phantom.log"), "rb") as f:
            try: f.seek(-n, os.SEEK_END)
            except Exception: pass
            return f.read().decode("utf-8", "replace").strip()
    except Exception:
        return ""

def seat_start(uid=DEFAULT_PRINCIPAL):
    c = cell(uid)
    if seat_running(uid):
        return {"ok": True, "already": True, "stream": c.stream, "size": SEAT_SIZE}
    binp = _seat_bin()
    if not binp:

        return {"ok": False, "error": _seat_bin_missing_msg()}
    try:
        os.makedirs(c.runtime, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": "Das Laufzeitverzeichnis %s konnte nicht angelegt werden: %s"
                                      % (c.runtime, e)}
    _dbus = _ensure_cell_dbus(uid)
    env = dict(os.environ)
    env.update({"XDG_RUNTIME_DIR": c.runtime, "PHANTOM_HEADLESS": "1", "PHANTOM_NO_INPUT": "1",
                "PHANTOM_HEADLESS_SIZE": SEAT_SIZE, "PHANTOM_STREAM": c.stream,
                "PHANTOM_VNC": c.vnc, "DBUS_SESSION_BUS_ADDRESS": _dbus})
    env.update(_render_env())
    try:
        proc = _cell_launch(uid, [binp, "--compositor", SEAT_WL], env, "comp",
                            fallback_prefix=_nice_prefix(),
                            log_path=os.path.join(c.runtime, "phantom.log"))
        _cell_procs(uid)["comp"] = proc
    except Exception as e:
        return {"ok": False, "error": "Der Bildschirm-Prozess konnte nicht gestartet werden: %s" % e}
    ready, why = _seat_wait_ready(uid, proc)
    if not ready:

        tail = _seat_log_tail(uid)
        seat_stop(uid)
        msg = "Der Bildschirm konnte nicht gestartet werden: %s." % why
        return {"ok": False, "error": msg, "detail": tail, "stream": c.stream, "size": SEAT_SIZE}
    return {"ok": True, "stream": c.stream, "size": SEAT_SIZE, "vnc": c.vnc}

def _cell_dbus_pids(uid):

    addr = cell(uid).dbus
    out = []
    try:
        for e in os.listdir("/proc"):
            if not e.isdigit():
                continue
            try:
                with open("/proc/%s/cmdline" % e, "rb") as f:
                    cl = f.read().decode("utf-8", "replace")
            except Exception:
                continue
            if "dbus-daemon" in cl and addr in cl:
                out.append(int(e))
    except Exception:
        pass
    return out

def seat_stop(uid=DEFAULT_PRINCIPAL):

    c = cell(uid)
    pc = _cell_procs(uid)

    for p in ([pc["comp"], pc.get("ff")] + pc["apps"]):
        try:
            if p and p.poll() is None:
                p.terminate()
        except Exception:
            pass

    try:
        for pid in _cell_firefox_pids(uid):
            try: os.kill(pid, signal.SIGTERM)
            except Exception: pass
    except Exception:
        pass

    for pid in _seat_comp_pids(uid):
        try: os.kill(pid, signal.SIGTERM)
        except Exception: pass

    for pid in _cell_dbus_pids(uid):
        try: os.kill(pid, signal.SIGTERM)
        except Exception: pass
    pc["comp"] = None; pc["apps"] = []; pc["ff"] = None

    left = []
    for _ in range(30):
        left = [n for n, busy in (("ctl", _unix_open(c.ctl)), ("vnc", _port_open(c.vnc)),
                                  ("stream", _port_open(c.stream))) if busy]
        if not left:
            break
        time.sleep(0.2)
    if left:
        for pid in _seat_comp_pids(uid) + _cell_dbus_pids(uid):
            try: os.kill(pid, signal.SIGKILL)
            except Exception: pass
        time.sleep(0.5)
        left = [n for n, busy in (("ctl", _unix_open(c.ctl)), ("vnc", _port_open(c.vnc)),
                                  ("stream", _port_open(c.stream))) if busy]
    try:
        if not _unix_open(c.ctl) and os.path.exists(c.ctl):
            os.unlink(c.ctl)
    except Exception:
        pass

    try:
        if not _unix_open(c.wayland):
            for stale in (c.wayland, c.wayland + ".lock"):
                if os.path.exists(stale):
                    os.unlink(stale)
    except Exception:
        pass
    if left:
        return {"ok": False, "error": "Der Bildschirm ließ sich nicht vollständig beenden; noch belegt: %s"
                                      % ", ".join(left)}
    return {"ok": True}

def _seat_comp_pids(uid):

    c = cell(uid)
    out = []
    try:
        for e in os.listdir("/proc"):
            if not e.isdigit():
                continue
            try:
                with open("/proc/%s/cmdline" % e, "rb") as f:
                    cl = f.read().decode("utf-8", "replace")
                if "phantom" not in cl or "--compositor" not in cl:
                    continue
                with open("/proc/%s/environ" % e, "rb") as f:
                    en = f.read().decode("utf-8", "replace")
            except Exception:
                continue
            if ("XDG_RUNTIME_DIR=" + c.runtime) in en:
                out.append(int(e))
    except Exception:
        pass
    return out
def seat_ctl(cmd, timeout=6, uid=DEFAULT_PRINCIPAL):

    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(timeout); c.connect(cell(uid).ctl)
        c.sendall((cmd + "\n").encode())
        try: c.shutdown(socket.SHUT_WR)
        except Exception: pass
        buf = b""
        while True:
            ch = c.recv(65536)
            if not ch: break
            buf += ch
        c.close(); return buf.decode("utf-8", "replace").strip()
    except Exception as e:
        return f"error: {e}"

_SEAT_LIST_RE = re.compile(
    r'cid=(\d+)\s+pid=(\d+)\s+ready=(\w+)\s+title="([^"]*)"\s+app_id="([^"]*)"(\s*\*)?')

def seat_enumerate(uid=DEFAULT_PRINCIPAL):

    r = seat_ctl("list", uid=uid)
    if r.startswith("error") or r.startswith("usage") or r.startswith("unknown"):
        return []
    apps = []
    for line in r.splitlines():
        m = _SEAT_LIST_RE.match(line.strip())
        if m:
            apps.append({"cid": int(m.group(1)), "pid": int(m.group(2)),
                         "ready": m.group(3) == "true", "title": m.group(4),
                         "app_id": m.group(5), "focused": bool(m.group(6))})
    return apps

def seat_focused(apps=None, uid=DEFAULT_PRINCIPAL):
    apps = apps if apps is not None else seat_enumerate(uid)
    for a in apps:
        if a.get("focused"):
            return a
    return apps[0] if apps else None

def seat_sense(cid, mode="text", uid=DEFAULT_PRINCIPAL):

    if mode not in ("text", "intent", "shot"):
        mode = "text"
    r = seat_ctl("sense %s %s" % (cid, mode), uid=uid)
    empty = (not r.strip()) or ("a11y read empty" in r) or r.startswith("error") or r.startswith("usage")
    return {"cid": cid, "mode": mode, "text": ("" if empty else r.strip()), "a11y_empty": bool(empty)}

def seat_forge(action, x=None, y=None, n=None, text=None, code=None, btn=None, uid=DEFAULT_PRINCIPAL):

    a = str(action)
    if a in ("move", "click", "drag"):
        cmd = "screen %s %d %d" % (a, int(x or 0), int(y or 0)) + (
            (" " + btn) if btn in ("left", "right", "middle") else "")
    elif a == "scroll":
        cmd = "screen scroll %d %d %d" % (int(x or 0), int(y or 0), int(n or 1))
    elif a == "text":
        cmd = "screen text " + str(text or "")
    elif a == "key":
        cmd = "screen key %d" % int(code or 0)
    elif a == "enter":
        cmd = "screen enter"
    else:
        return False, "bad action %r" % a, {"action": a}
    reply = seat_ctl(cmd, uid=uid)
    ok = not reply.startswith("error")

    forged = {"action": a, "cmd": (cmd if a != "text" else "screen text <…>")}
    if x is not None:
        forged["x"], forged["y"] = int(x or 0), int(y or 0)
    return ok, reply, forged

def seat_low_stakes(verb, params, uid=DEFAULT_PRINCIPAL):

    apps = seat_enumerate(uid)
    foc = seat_focused(apps, uid)
    if verb == "app.sense":
        if not foc:
            return {"ok": True, "earcon": "done", "speech": "Kein Fenster ist gerade offen.",
                    "extra": {"apps": []}}
        s = seat_sense(foc["cid"], params.get("mode", "text"), uid)
        if s["a11y_empty"]:
            speech = "Aktives Fenster: %s. Kein Bedienbaum (a11y) verfügbar — ich sehe nur Titel und Pixel." % foc["title"]
        else:
            speech = "%s: %s" % (foc["title"], s["text"][:240])
        return {"ok": True, "earcon": "done", "speech": speech,
                "extra": {"focused": foc, "sense": s, "apps": apps}}
    if not foc:
        return {"ok": False, "earcon": "error", "speech": "Kein Fenster ist aktiv, in das ich etwas eingeben könnte."}
    secret = bool(params.get("secret"))
    if verb == "app.type":
        txt = params.get("text", "")
        ok, reply, forged = seat_forge("text", text=txt, uid=uid)
        shown = "<versteckt>" if secret else ('„%s"' % txt)
        speech = ("Getippt %s in %s." % (shown, foc["title"])) if ok else ("Konnte nicht tippen: %s" % reply)
    elif verb in ("app.enter",):
        ok, reply, forged = seat_forge("enter", uid=uid)
        speech = ("Enter in %s." % foc["title"]) if ok else ("Enter fehlgeschlagen: %s" % reply)
    elif verb == "app.scroll":
        ok, reply, forged = seat_forge("scroll", x=params.get("x", 0), y=params.get("y", 0), n=params.get("n", 1), uid=uid)
        speech = ("Gescrollt in %s." % foc["title"]) if ok else ("Scroll fehlgeschlagen: %s" % reply)
    elif verb in ("app.click",):
        if params.get("x") is None or params.get("y") is None:

            return {"ok": False, "earcon": "error",
                    "speech": "Ich kann nicht blind klicken — im %s fehlt der Bedienbaum. Klick im Screen-Reiter, "
                              "dann trifft es genau." % foc["title"]}
        ok, reply, forged = seat_forge("click", x=params["x"], y=params["y"], btn=params.get("btn"), uid=uid)
        speech = ("Geklickt bei %d,%d in %s." % (int(params["x"]), int(params["y"]), foc["title"])) if ok \
            else ("Klick fehlgeschlagen: %s" % reply)
    else:
        return {"ok": False, "earcon": "error", "speech": "Unbekannter App-Verb: %s" % verb}
    return {"ok": ok, "earcon": ("done" if ok else "error"), "speech": speech,
            "forged": forged, "extra": {"focused": foc}}

class _Marionette:
    def __init__(self, host="127.0.0.1", port=2828):
        self.host, self.port = host, port
        self._sock = None
        self._mid = 0
        self._lock = threading.Lock()

    def _recv(self):
        buf = b""
        while b":" not in buf:
            ch = self._sock.recv(1)
            if not ch:
                raise IOError("marionette closed")
            buf += ch
        ln, rest = buf.split(b":", 1)
        ln = int(ln)
        while len(rest) < ln:
            ch = self._sock.recv(ln - len(rest))
            if not ch:
                raise IOError("marionette short read")
            rest += ch
        return json.loads(rest.decode("utf-8"))

    def _raw(self, cmd, params):
        self._mid += 1
        payload = json.dumps([0, self._mid, cmd, params or {}]).encode("utf-8")
        self._sock.sendall(("%d:" % len(payload)).encode() + payload)
        return self._recv()

    def _connect(self):

        last = None
        for _ in range(4):
            try:
                s = socket.create_connection((self.host, self.port), timeout=3)
                s.settimeout(3)
                self._sock = s
                self._recv()

                self._raw("WebDriver:NewSession",
                          {"capabilities": {"alwaysMatch": {"pageLoadStrategy": "eager"}}})
                self._sock.settimeout(20)
                try:
                    self._raw("WebDriver:SetTimeouts", {"pageLoad": 15000, "script": 15000})
                except Exception:
                    pass
                return
            except Exception as e:
                last = e
                try:
                    if self._sock:
                        self._sock.close()
                except Exception:
                    pass
                self._sock = None
                time.sleep(0.4)
        raise last if last else IOError("marionette: connect failed")

    def command(self, cmd, params=None):
        with self._lock:
            for attempt in range(2):
                try:
                    if self._sock is None:
                        self._connect()
                    return self._raw(cmd, params)
                except Exception:
                    try:
                        if self._sock:
                            self._sock.close()
                    except Exception:
                        pass
                    self._sock = None
                    if attempt == 1:
                        raise

    def available(self):
        if self._sock is not None:
            return True
        try:
            socket.create_connection((self.host, self.port), timeout=1).close()
            return True
        except Exception:
            return False

    @staticmethod
    def _val(reply):
        if isinstance(reply, list) and len(reply) > 3 and isinstance(reply[3], dict):
            return reply[3].get("value")
        return None

    def navigate(self, url):
        u = (url or "").strip()
        if not u:
            return {"ok": False, "error": "Keine Adresse angegeben."}
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u):

            if (" " in u) or ("." not in u):
                u = "https://www.google.com/search?q=" + urllib.parse.quote(u)
            else:
                u = "https://" + u
        r = self.command("WebDriver:Navigate", {"url": u})
        err = r[2] if isinstance(r, list) and len(r) > 2 and r[2] else None
        cur = self._val(self.command("WebDriver:GetCurrentURL", {})) if not err else None
        return {"ok": not err, "navigated": u, "url": cur, "error": err}

_marionette = _Marionette()

def _seed_cell_profile(profdir):

    try:
        os.makedirs(profdir, exist_ok=True)
    except Exception:
        return
    if _render_env():
        uj = os.path.join(profdir, "user.js")
        try:
            cur = open(uj).read() if os.path.exists(uj) else ""
            if "browser.aboutwelcome.enabled" not in cur:
                lines = ['user_pref("gfx.webrender.software", true);',
                         'user_pref("gfx.webrender.all", true);',
                         'user_pref("gfx.canvas.accelerated", false);',
                         'user_pref("layers.gpu-process.enabled", false);',
                         'user_pref("media.hardware-video-decoding.enabled", false);',
                         'user_pref("dom.webgpu.enabled", false);',

                         'user_pref("media.ffmpeg.vaapi.enabled", false);',

                         'user_pref("browser.aboutwelcome.enabled", false);',
                         'user_pref("browser.startup.homepage_override.mstone", "ignore");',
                         'user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);',
                         'user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);', ""]
                with open(uj, "a") as f:
                    if cur and not cur.endswith(chr(10)): f.write(chr(10))
                    f.write(chr(10).join(lines))
        except Exception:
            pass
    xp = os.path.join(profdir, "xulstore.json")
    try:
        try: x = json.load(open(xp))
        except Exception: x = {}
        mb = x.setdefault("chrome://browser/content/browser.xhtml", {}).setdefault("toolbar-menubar", {})
        if mb.get("autohide") != "true":
            mb["autohide"] = "true"; json.dump(x, open(xp, "w"))
    except Exception:
        pass

FF_SETTLE_NICE = 5

def _cell_firefox_procs(uid=DEFAULT_PRINCIPAL):

    import glob as _glob
    c = cell(uid)
    prof_b = ("--profile " + c.profile).encode() if c.profile else None
    want_wl = c.wayland.encode()
    pids = []
    for pd in _glob.glob("/proc/[0-9]*"):
        try:
            cl = open(pd + "/cmdline", "rb").read()
        except Exception:
            continue
        if b"firefox" not in cl or b"-contentproc" in cl or b"glxtest" in cl or b"vaapitest" in cl:
            continue
        if prof_b is not None:
            if prof_b not in cl.replace(b"\0", b" "):
                continue
        else:
            if b"--profile" in cl:
                continue
            try:
                if want_wl not in open(pd + "/environ", "rb").read():
                    continue
            except Exception:
                continue
        try:
            pids.append(int(pd.split("/")[-1]))
        except Exception:
            pass
    return pids

def _cell_firefox_pids(uid=DEFAULT_PRINCIPAL):

    return _cell_firefox_procs(uid)

def _cell_firefox_cid(uid=DEFAULT_PRINCIPAL):

    fpids = set(_cell_firefox_procs(uid))
    if not fpids:
        return None, False
    best, best_ready = None, False
    for ln in seat_ctl("list", uid=uid).splitlines():
        m = re.match(r"cid=(\d+)\s+pid=(\d+)", ln)
        if not m:
            continue
        cid, pid = int(m.group(1)), int(m.group(2))
        if pid not in fpids:
            continue
        if "ready=true" in ln and not best_ready:
            best, best_ready = cid, True
        elif best is None:
            best = cid
    return best, best_ready

def _renice_ff(uid=DEFAULT_PRINCIPAL, ni=FF_SETTLE_NICE):

    for pid in _cell_firefox_procs(uid):
        try:
            os.setpriority(os.PRIO_PGRP, os.getpgid(pid), ni)
        except Exception:
            try: os.setpriority(os.PRIO_PROCESS, pid, ni)
            except Exception: pass

def _present_firefox(uid=DEFAULT_PRINCIPAL, timeout=22.0):

    deadline = time.time() + timeout
    cid = None
    while time.time() < deadline:
        cid, ready = _cell_firefox_cid(uid)
        if cid is not None and ready:
            break
        time.sleep(0.3)
    if cid is not None:
        seat_ctl("focus %d" % cid, uid=uid)
    _renice_ff(uid)
    return cid

NO_BROWSER_DE = ("Auf dieser Box ist kein Firefox installiert — der Bildschirm läuft, "
                 "aber es gibt keinen Browser zum Anzeigen. Bitte Firefox nachinstallieren.")
FIREFOX_BINS = ("/usr/lib/firefox/firefox-bin", "/usr/bin/firefox", "/snap/bin/firefox")

def _spawn_marionette_firefox(url=None, uid=DEFAULT_PRINCIPAL):

    import subprocess
    c = cell(uid)
    if _cell_firefox_procs(uid):
        return {"ok": True, "already": True}
    pc = _cell_procs(uid)
    if pc.get("ff") and pc["ff"].poll() is None:
        return {"ok": True, "already": True}
    env = dict(os.environ, HOME=os.path.expanduser("~"), XDG_RUNTIME_DIR=c.xdg,
               WAYLAND_DISPLAY=c.wayland,
               MOZ_ENABLE_WAYLAND="1", GDK_BACKEND="wayland", MOZ_DISABLE_WAYLAND_DMABUF="1",
               MOZ_APP_REMOTINGNAME="phantom-" + c.uid,
               DBUS_SESSION_BUS_ADDRESS=_ensure_cell_dbus(uid))
    env.update(_render_env())
    if c.profile:
        _seed_cell_profile(c.profile)
    else:
        _ensure_browser_swgl_prefs()
        _ensure_firefox_ui()

    args = (["--profile", c.profile] if c.profile else []) + ["--new-instance"] + ([url] if url else [])
    for binpath in FIREFOX_BINS:
        if os.path.exists(binpath):
            try:

                pc["ff"] = _cell_launch(uid, [binpath] + args, env, "ff")
            except Exception as e:
                return {"ok": False, "error": "Der Browser konnte nicht gestartet werden (%s): %s"
                                              % (binpath, e)}
            return {"ok": True, "launched": binpath, "url": url}
    return {"ok": False, "error": NO_BROWSER_DE}

def _firefox_restart(url=None, uid=DEFAULT_PRINCIPAL):

    for pid in _cell_firefox_pids(uid):
        try: os.kill(pid, signal.SIGTERM)
        except Exception: pass
    dead = False
    for _ in range(30):
        if not _cell_firefox_pids(uid):
            dead = True
            break
        time.sleep(0.2)
    if not dead:
        for pid in _cell_firefox_pids(uid):
            try: os.kill(pid, signal.SIGKILL)
            except Exception: pass
        time.sleep(1.0)
    _cell_procs(uid)["ff"] = None
    return _spawn_marionette_firefox(url, uid=uid)

def _screen_open(url, uid=DEFAULT_PRINCIPAL):

    import subprocess
    c = cell(uid)
    url = (url or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"ok": False, "error": "Ungültige Adresse — es werden nur http:// und https:// geöffnet "
                                      "(erhalten: %r)." % url[:120]}
    if not seat_running(uid):
        r = seat_start(uid)
        if not r.get("ok"):

            return {"ok": False, "error": r.get("error") or "Der Bildschirm konnte nicht gestartet werden.",
                    "detail": r.get("detail", "")}
    running = bool(_cell_firefox_procs(uid))
    if not running:
        r = _spawn_marionette_firefox(url, uid=uid)
        if not r.get("ok"):

            return r
    else:

        env = dict(os.environ, HOME=os.path.expanduser("~"), XDG_RUNTIME_DIR=c.xdg,
                   WAYLAND_DISPLAY=c.wayland,
                   MOZ_ENABLE_WAYLAND="1", GDK_BACKEND="wayland", MOZ_DISABLE_WAYLAND_DMABUF="1",
                   MOZ_APP_REMOTINGNAME="phantom-" + c.uid,
                   DBUS_SESSION_BUS_ADDRESS=_ensure_cell_dbus(uid))
        env.update(_render_env())
        args = (["--profile", c.profile] if c.profile else []) + ["--new-tab", url]
        remoted = False
        for binpath in FIREFOX_BINS:
            if os.path.exists(binpath):
                try:
                    subprocess.Popen([binpath] + args, env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
                except Exception as e:
                    return {"ok": False, "error": "Die Seite konnte nicht im laufenden Browser geöffnet "
                                                  "werden: %s" % e}
                remoted = True
                break
        if not remoted:

            return {"ok": False, "error": NO_BROWSER_DE}

    cid = _present_firefox(uid)
    return {"ok": True, "url": url, "focused": cid}

