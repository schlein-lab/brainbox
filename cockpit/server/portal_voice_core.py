
import os, sys, json, base64, socket, secrets, subprocess, time
import re, ssl, threading
import urllib.parse, urllib.request

HOME = os.path.expanduser("~")
CFG_DIR = os.path.join(HOME, ".config", "brainbox-portal")
DATA_DIR = os.path.join(HOME, ".local", "share", "brainbox-portal")
DEFAULT_PRINCIPAL = "owner"
ATTACH_DIR = os.path.join(DATA_DIR, "attachments")

try:
    import pn_session_policy as _policy
except Exception:
    _policy = None
try:
    import pn_session_cells as _sesscells
except Exception:
    _sesscells = None
try:
    import portal_voice_ext as _vext
except Exception:
    _vext = None
try:
    import pn_governed as _PN
except Exception:
    _PN = None

Handler = None
PIPER_BIN = None
PIPER_DIR = None
PIPER_MODEL = None
SESSIONS = None
_session_new = None
_session_uid = None
TTS_DIR = None
VENV = None
VENV_PY = None
VOICED_PY_PATH = None
VOICED_SOCK = None
WHISPER_SIZE = None
_NABU_LATE_MAX = None
_attach_owner_map = None
_prov_log = None
_save_sessions = None
_sess_policy_get = None
_session_store = None
_sessprov_get = None
_traceback_log = None
_uid_safe = None
_voice_cell = None
_voice_mirror_user_input = None
_voice_persona = None
_voice_turn_frame = None
_vpn_registry = None
links_load = None
load_cfg = None
save_cfg = None
seat_enumerate = None
seat_focused = None
tmux_session = None
user_dir = None

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

VOICED_PY = r'''import os, sys, json, socket, subprocess, tempfile, threading, time, base64, traceback
ARG = sys.argv[1]                       # digits -> loopback TCP port; else legacy unix path
WHISPER = os.environ.get("PP_WHISPER", "small")
PIPER_BIN = os.environ.get("PP_PIPER_BIN", "")
PIPER_MODEL = os.environ.get("PP_PIPER_MODEL", "")
IDLE_S = int(os.environ.get("VOICED_IDLE_S", "0") or 0)
from faster_whisper import WhisperModel
wm = WhisperModel(WHISPER, device="cpu", compute_type="int8")
sys.stderr.write("voiced: whisper bereit\n"); sys.stderr.flush()
LAST = [time.time()]

def do_stt(req):
    _lang = req.get("lang")
    _lang = None if _lang in (None, "", "de", "auto") else _lang  # auto-detect DE/EN; expliziter non-de-Hint gilt
    segs, info = wm.transcribe(req["path"], language=_lang, vad_filter=True)
    return {"text": "".join(x.text for x in segs).strip(), "lang": getattr(info, "language", None)}

def do_tts(req):
    # Synthesize into OUR OWN tmp (the only dir a governed job may write) and answer the WAV
    # bytes INLINE — the un-sandboxed portal writes the caller's `out` path itself.
    text = req["text"]
    out = os.path.join(tempfile.mkdtemp(prefix="tts-"), "out.wav")
    eng = None
    if PIPER_BIN and PIPER_MODEL and os.path.exists(PIPER_MODEL):
        p = subprocess.run([PIPER_BIN, "-m", PIPER_MODEL, "-f", out], input=text.encode(), capture_output=True)
        if p.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 44:
            eng = "piper"
    if eng is None:
        subprocess.run(["espeak-ng", "-v", "de", "-w", out, text], capture_output=True)
        eng = "espeak"
    try:
        data = open(out, "rb").read()
    except OSError:
        data = b""
    try:
        os.remove(out); os.rmdir(os.path.dirname(out))
    except OSError:
        pass
    if len(data) <= 44:
        return {"error": "Sprachausgabe erzeugte keine Audiodaten (engine=%s)" % eng}
    return {"ok": True, "engine": eng, "wav_b64": base64.b64encode(data).decode()}

if ARG.isdigit():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", int(ARG)))
else:
    if os.path.exists(ARG):
        os.remove(ARG)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(ARG)
srv.listen(8)
sys.stderr.write("voiced: socket bereit\n"); sys.stderr.flush()

if IDLE_S > 0:
    def _idle_watch():
        while True:
            time.sleep(30)
            if time.time() - LAST[0] > IDLE_S:
                sys.stderr.write("voiced: %ds ohne Anfrage -> Ende (on-demand)\n" % IDLE_S)
                sys.stderr.flush()
                os._exit(0)
    threading.Thread(target=_idle_watch, daemon=True).start()

while True:
    conn, _ = srv.accept()
    LAST[0] = time.time()
    try:
        buf = b""
        while b"\n" not in buf:
            c = conn.recv(65536)
            if not c: break
            buf += c
        req = json.loads((buf.split(b"\n", 1)[0] or b"{}"))
        op = req.get("op")
        res = do_stt(req) if op == "stt" else do_tts(req) if op == "tts" else {"error": "bad op"}
        LAST[0] = time.time()
    except Exception as e:
        res = {"error": str(e), "tb": traceback.format_exc()[-300:]}
    try:
        conn.sendall((json.dumps(res) + "\n").encode())
    except Exception:
        pass
    conn.close()
'''

VOICED_PORT = int(os.environ.get("PP_VOICED_PORT", "8126"))
VOICED_IDLE_S = int(os.environ.get("PP_VOICED_IDLE_S", "7200"))

def cmd_install_voice(args):
    os.makedirs(DATA_DIR, exist_ok=True); os.makedirs(PIPER_DIR, exist_ok=True); os.makedirs(TTS_DIR, exist_ok=True)
    if not os.path.exists(VENV_PY):
        print("venv anlegen…")
        subprocess.run([sys.executable, "-m", "venv", VENV], check=False)
    print("pip install faster-whisper piper-tts (CPU, kein torch) …")
    subprocess.run([VENV_PY, "-m", "pip", "install", "-q", "--upgrade", "pip"], check=False)
    r = subprocess.run([VENV_PY, "-m", "pip", "install", "-q", "faster-whisper", "piper-tts"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("pip-Fehler:\n" + (r.stderr or "")[-700:]); return
    import urllib.request
    base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/medium/"
    for suf in ("de_DE-thorsten-medium.onnx", "de_DE-thorsten-medium.onnx.json"):
        dest = os.path.join(PIPER_DIR, suf)
        if os.path.exists(dest) and os.path.getsize(dest) > 1000:
            continue
        print("lade Piper-Stimme:", suf)
        try:
            urllib.request.urlretrieve(base + suf + "?download=true", dest)
        except Exception as e:
            print("  Download-Fehler:", str(e)[:90])
    print(f"lade Whisper-Modell ({WHISPER_SIZE}) …")
    subprocess.run([VENV_PY, "-c",
        f"from faster_whisper import WhisperModel; WhisperModel('{WHISPER_SIZE}',device='cpu',compute_type='int8')"],
        check=False)
    open(VOICED_PY_PATH, "w").write(VOICED_PY)
    cfg = load_cfg(); caps = cfg.setdefault("caps", {})
    caps["voice"] = True; caps["voice_stt_server"] = True
    save_cfg(cfg)
    print("Voice installiert (Whisper STT + Piper TTS). Server neu starten: brainbox-portal serve")

def voice_stack_missing():

    if not os.path.exists(VENV_PY):
        return ("Sprach-Erkennung nicht installiert — einmalig auf der Box-Konsole "
                "`brainbox-portal install-voice` ausführen (installiert Whisper + Piper).")
    return None

def _voiced_tcp_alive(timeout=1.0):
    try:
        s = socket.create_connection(("127.0.0.1", VOICED_PORT), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False

def start_voiced():

    reason = voice_stack_missing()
    if reason:
        return False, reason

    try:
        cur = open(VOICED_PY_PATH).read() if os.path.exists(VOICED_PY_PATH) else ""
        if cur != VOICED_PY:
            open(VOICED_PY_PATH, "w").write(VOICED_PY)
    except OSError as e:
        return False, "voiced.py nicht schreibbar (%s)" % e
    if _voiced_tcp_alive():
        return True, None

    if _PN is not None:
        try:
            prev = int(open(os.path.join(DATA_DIR, "voiced.job")).read().strip())
        except Exception:
            prev = None
        if prev is not None:
            pj = (_PN.job(prev) or {}).get("state")
            if pj == "running":
                return True, None
            if pj in ("queued", "staged"):
                return False, ("Kein Platz im Echtzeit-Band — der Sprachdienst wartet auf freien "
                               "RAM (Job %d in der Warteschlange). Eine Session schließen oder "
                               "später erneut versuchen." % prev)

    subprocess.run(["pkill", "-f", f"voiced.py {VOICED_SOCK}"], capture_output=True)
    envv = ["/usr/bin/env",
            "PP_WHISPER=%s" % WHISPER_SIZE,
            "PP_PIPER_BIN=%s" % (PIPER_BIN if os.path.exists(PIPER_BIN) else ""),
            "PP_PIPER_MODEL=%s" % (PIPER_MODEL if os.path.exists(PIPER_MODEL) else ""),
            "VOICED_IDLE_S=%d" % VOICED_IDLE_S]
    argv = envv + [VENV_PY, VOICED_PY_PATH, str(VOICED_PORT)]
    if _PN is not None and _PN.pn_available():

        r = _PN.submit(argv, mem=700, timeout_s=86400, tag="voice.stt", latency="realtime")
        if not r.get("ok"):
            return False, ("Der Governor (pnd) lässt den Sprachdienst gerade nicht zu: %s"
                           % (r.get("error") or "unbekannt"))
        try:
            open(os.path.join(DATA_DIR, "voiced.job"), "w").write(str(r.get("id")))
        except OSError:
            pass
        print("[portal] voiced: governed gestartet als pn-Job %s (tag voice.stt, realtime, "
              "idle-exit %ds)" % (r.get("id"), VOICED_IDLE_S), file=sys.stderr, flush=True)
        return True, None

    print("[portal] voiced: Governor (pnd) nicht erreichbar — Sprachdienst läuft ausnahmsweise "
          "DIREKT (ungoverned). Sobald pnd wieder läuft, startet er governed neu.",
          file=sys.stderr, flush=True)
    env = os.environ.copy()
    env["PP_WHISPER"] = WHISPER_SIZE
    env["PP_PIPER_BIN"] = PIPER_BIN if os.path.exists(PIPER_BIN) else ""
    env["PP_PIPER_MODEL"] = PIPER_MODEL if os.path.exists(PIPER_MODEL) else ""
    env["VOICED_IDLE_S"] = str(VOICED_IDLE_S)
    log = open(os.path.join(DATA_DIR, "voiced.log"), "a")
    subprocess.Popen([VENV_PY, VOICED_PY_PATH, str(VOICED_PORT)], env=env,
                     stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    return True, None

def _voice_connect(timeout):

    try:
        s = socket.create_connection(("127.0.0.1", VOICED_PORT), timeout=timeout)
        return s
    except Exception:
        pass
    if os.path.exists(VOICED_SOCK):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(VOICED_SOCK)
        return s
    raise ConnectionRefusedError("voiced nicht erreichbar")

def voice_request(req, timeout=120):

    missing = voice_stack_missing()
    if missing:
        raise RuntimeError(missing)
    try:
        s = _voice_connect(timeout)
    except Exception:
        ok, reason = start_voiced()
        if not ok:
            raise RuntimeError(reason or "Sprachdienst nicht startbar")
        s = None
        deadline = time.time() + min(150, max(30, timeout))
        while time.time() < deadline:
            try:
                s = _voice_connect(2.0)
                break
            except Exception:
                time.sleep(1.0)
        if s is None:

            st = None
            if _PN is not None:
                try:
                    jid = int(open(os.path.join(DATA_DIR, "voiced.job")).read().strip())
                    st = (_PN.job(jid) or {}).get("state")
                except Exception:
                    st = None
            if st == "running":
                raise RuntimeError("Der Sprachdienst lädt noch (Whisper-Modell-Start) — "
                                   "bitte in etwa einer Minute erneut versuchen.")
            if st in ("queued", "staged"):
                raise RuntimeError("Kein Platz im Echtzeit-Band — der Sprachdienst wartet auf "
                                   "freien RAM. Eine Session schließen oder später erneut "
                                   "versuchen.")
            raise RuntimeError("Sprachdienst antwortet nicht (Start oder Modell-Laden "
                               "schlug fehl — Details im voiced-Job-Log).")
    s.settimeout(timeout)
    s.sendall((json.dumps(req) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        c = s.recv(1 << 20)
        if not c:
            break
        buf += c
    s.close()
    res = json.loads((buf.split(b"\n", 1)[0] or b"{}"))

    wav_b64 = res.pop("wav_b64", None)
    if req.get("op") == "tts" and wav_b64 and req.get("out"):
        try:
            with open(req["out"], "wb") as f:
                f.write(base64.b64decode(wav_b64))
        except (OSError, ValueError) as e:
            res = {"error": "Sprachausgabe-Datei nicht schreibbar: %s" % e}
    return res

VOICE_AGENT_SESS = "ppvoice"
VOICE_AGENT_DIR = os.path.join(DATA_DIR, "voice-agent")

def _voice_sess(uid=DEFAULT_PRINCIPAL):
    return "%s-%s" % (VOICE_AGENT_SESS, _uid_safe(uid))

def _voice_dir(uid=DEFAULT_PRINCIPAL):
    return os.path.join(VOICE_AGENT_DIR, _uid_safe(uid))

def _last_claude_session(tmux_name):

    if not tmux_name:
        return None
    path = os.path.join(HOME, ".claude", "session-map.jsonl")
    best = None
    try:
        with open(path) as f:
            for ln in f:
                try:
                    rec = json.loads(ln)
                except ValueError:
                    continue
                sid = rec.get("session_id"); tp = rec.get("transcript_path")
                if rec.get("tmux") == tmux_name and sid and tp:
                    if best is None or rec.get("ts", 0) >= best[0]:
                        best = (rec.get("ts", 0), sid, tp)
    except OSError:
        return None
    if best and re.match(r"^[A-Za-z0-9][A-Za-z0-9-]{7,63}$", best[1]) and os.path.exists(best[2]):
        return best[1]
    return None

_PENDING_ACTIONS = {}
_PENDING_LOCK = threading.Lock()

_SESSION_SUMMARY_CACHE = {}
_SESSION_SUMMARY_LOCK = threading.Lock()

import portal_zustand as _zst
_zst.register("portal_voice_core._PENDING_ACTIONS", "cursor", __name__, ref=_PENDING_ACTIONS,
              beschreibung="je uid gepufferte Client-Aktionen (Agent -> UI-Reflex), vom naechsten Voice-Turn gedraint; Verlust => Aktionen gehen VERLOREN (Ausfall, keine Doppelzustellung)",
              neustart="verfaellt", schreiber="pending_actions_push() (Agent-Exec); Drain im Voice-Turn")
_zst.register("portal_voice_core._SESSION_SUMMARY_CACHE", "cache", __name__, ref=_SESSION_SUMMARY_CACHE,
              beschreibung="Session-Wechsel-Zusammenfassungen je (principal, kind, session), invalidiert ueber den Transcript-Cursor (kein LLM-Spend bei unveraendertem Stand)",
              neustart="verfaellt", schreiber="Voice-Turn unter _SESSION_SUMMARY_LOCK")

def pending_actions_push(uid, action):
    with _PENDING_LOCK:
        _PENDING_ACTIONS.setdefault(_uid_safe(uid), []).append(action)

    if _ACTION_BUS is not None and isinstance(action, dict):
        try:
            verb = action.get("verb") or action.get("action") or "action"
            _ACTION_BUS.push(_uid_safe(uid), verb, action.get("args") if isinstance(action.get("args"), dict) else action)
        except Exception:
            pass

def pending_actions_drain(uid):
    with _PENDING_LOCK:
        return _PENDING_ACTIONS.pop(_uid_safe(uid), [])

_CLIENT_VERBS = frozenset((
    "copy", "cut", "paste", "select", "select-all", "undo", "redo", "delete",
    "right-click", "context-menu", "press", "type"))
_CLIENT_VERB_ALIAS = {
    "kopieren": "copy", "ausschneiden": "cut", "einfuegen": "paste", "einfügen": "paste",
    "markieren": "select", "alles-markieren": "select-all", "alles markieren": "select-all",
    "rueckgaengig": "undo", "rückgängig": "undo", "wiederholen": "redo",
    "loeschen": "delete", "löschen": "delete", "rechtsklick": "right-click",
    "kontextmenue": "context-menu", "kontextmenü": "context-menu",
    "taste": "press", "tippen": "type", "schreiben": "type"}

_ACTION_BUS = _vext.ActionBus() if _vext else None

def _push_client_action(uid, verb, args):

    pending_actions_push(uid, {"action": verb, "verb": verb, "args": args})

_DISPLAY_REG = _vext.DisplayRegistry(os.path.join(DATA_DIR, "displays.json"),
                                     push_fn=_push_client_action) if _vext else None

_WORKER_REG = (_vext.WorkerRegistry(os.path.join(DATA_DIR, "workers.json"))
               if _vext and hasattr(_vext, "WorkerRegistry") else None)

_DEVICE_REG = (_vext.DeviceRegistry(os.path.join(DATA_DIR, "devices.json"),
                                    seed=(lambda: _policy.device_roster() if _policy else []))
               if _vext and hasattr(_vext, "DeviceRegistry") else None)

if _policy:
    try:
        if _DEVICE_REG and hasattr(_policy, "set_device_provider"):
            _policy.set_device_provider(lambda: _DEVICE_REG.list() or [])
        if _DISPLAY_REG and hasattr(_policy, "set_display_provider"):
            _policy.set_display_provider(lambda: _DISPLAY_REG.list() or [])
    except Exception:
        pass
_SCAN_TOKENS = {}
_SCAN_LOCK = threading.Lock()
_zst.register("portal_voice_core._SCAN_TOKENS", "snapshot", __name__, ref=_SCAN_TOKENS,
              beschreibung="laufende Geraete-Scan-Tokens",
              neustart="verfaellt", schreiber="Scan-Pfade unter _SCAN_LOCK")

def _netprofile_allows(cap, what="", angefordert=False):

    try:
        import sys as _s
        for _p in ("/home/brainbox/portioneer", os.path.expanduser("~/portioneer")):
            if _p not in _s.path and os.path.isdir(_p):
                _s.path.append(_p)
        from pnlib import netprofile as _np
    except Exception:
        return bool(angefordert)
    try:
        return _np.require(cap, what, angefordert=angefordert)
    except TypeError:
        return _np.require(cap, what) and bool(angefordert)
    except Exception:
        return bool(angefordert)

def _device_host_scan(angefordert=False):

    if _DEVICE_REG is None:
        return 0
    found = {}
    try:
        pr = subprocess.run(["ip", "-4", "neigh", "show"], capture_output=True, text=True, timeout=6)
        for ln in (pr.stdout or "").splitlines():
            parts = ln.split()
            if not parts or "." not in parts[0]:
                continue
            ip = parts[0]
            if ip.endswith(".255") or ip.startswith(("224.", "239.", "255.", "127.")):
                continue
            low = ln.lower()
            if "failed" in low or "incomplete" in low:
                continue
            if "lladdr" not in parts:
                continue
            found[ip] = {"ip": ip, "mac": parts[parts.index("lladdr") + 1], "state": "online", "cast": False}
    except Exception:
        pass

    if not _netprofile_allows("lan_scan", "cast sweep of the local /24", angefordert=angefordert):
        if found:
            try:
                _DEVICE_REG.merge_discovered(list(found.values()))
            except Exception:
                pass
        return len(found)
    try:
        base = None
        try:
            pr = subprocess.run(["ip", "-4", "route", "get", "8.8.8.8"], capture_output=True, text=True, timeout=4)
            m = re.search(r"src (\d+\.\d+\.\d+)\.\d+", pr.stdout or "")
            base = m.group(1) if m else None
        except Exception:
            base = None
        if not base:
            for ip in found:
                if ip.startswith(("192.168.", "10.", "172.")):
                    base = ip.rsplit(".", 1)[0]; break
        if base:
            hits = {}; lk = threading.Lock()
            def _probe(n):
                ip = "%s.%d" % (base, n)
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.settimeout(0.35)
                    if s.connect_ex((ip, 8009)) == 0:
                        with lk:
                            hits[ip] = True
                    s.close()
                except Exception:
                    pass
            ths = []
            for n in range(1, 255):
                t = threading.Thread(target=_probe, args=(n,), daemon=True); t.start(); ths.append(t)
                if len(ths) >= 48:
                    for t in ths:
                        t.join(1.0)
                    ths = []
            for t in ths:
                t.join(1.0)
            for ip in hits:
                rec = found.setdefault(ip, {"ip": ip, "mac": "", "state": "online", "cast": False})
                rec["cast"] = True; rec["state"] = "online"
    except Exception:
        pass

    cast_meta = {}
    _cands = [ip for ip, r in found.items() if r.get("cast")]
    if _cands:
        _clk = threading.Lock()
        def _eureka(ip):
            ok, nm = _cast_eureka_name(ip)
            with _clk:
                cast_meta[ip] = (ok, nm)
        _cts = [threading.Thread(target=_eureka, args=(ip,), daemon=True) for ip in _cands]
        for t in _cts:
            t.start()
        for t in _cts:
            t.join(4.0)
    items = []
    _old_to = socket.getdefaulttimeout()
    socket.setdefaulttimeout(0.6)
    for ip, r in found.items():
        cast = bool(r.get("cast"))
        ename = ""
        if cast:
            _ok, ename = cast_meta.get(ip, (False, ""))
            if not _ok:
                cast = False
        if cast:
            name, name_src = ename, "eureka"
        else:
            try:
                name = socket.gethostbyaddr(ip)[0].split(".")[0]
            except Exception:
                name = ""
            name = name or ip
            name_src = "hostname"
        did = ("cast-" if cast else "host-") + ip.replace(".", "-")
        rec = {"id": did, "name": name, "name_src": name_src,
               "kind": ("cast" if cast else "host"),
               "state": r.get("state") or "online",
               "transport": {"addr": ip, "mac": r.get("mac") or "",
                             "proto": ("googlecast" if cast else "ip"),
                             "port": (8009 if cast else 0)}}
        if cast:
            rec["driver"] = "cast"
        items.append(rec)
    socket.setdefaulttimeout(_old_to)
    try:
        return _DEVICE_REG.merge_discovered(items)
    except Exception:
        return 0

_EUREKA_TLS = ssl.create_default_context()
_EUREKA_TLS.check_hostname = False
_EUREKA_TLS.verify_mode = ssl.CERT_NONE

def _cast_eureka_name(ip, timeout=1.5):

    for url, ctx in (("http://%s:8008/setup/eureka_info?params=name" % ip, None),
                     ("https://%s:8443/setup/eureka_info?params=name" % ip, _EUREKA_TLS)):
        try:
            with urllib.request.urlopen(url, timeout=timeout, context=ctx) as r:
                j = json.loads(r.read(4096).decode("utf-8", "replace") or "{}")
            if isinstance(j, dict):
                return True, str(j.get("name") or "").strip()[:80]
        except Exception:
            continue
    return False, ""

def _device_mdns_scan(angefordert=False):

    if _DEVICE_REG is None:
        return 0

    if not _netprofile_allows("mdns_browse", "Geraetesuche (mDNS/SSDP)", angefordert=angefordert):
        return 0
    script = None
    for c in (os.path.join(HOME, ".local", "bin", "device_discover.py"),
              os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                  os.path.realpath(__file__)))), "engine", "tools", "device_discover.py")):
        if os.path.exists(c):
            script = c
            break
    if not script:
        return 0
    if _PN is not None and _PN.pn_available():

        rc, out_b, err_de = _PN.run_capture(["/usr/bin/python3", script, "--dur", "6",
                                             "--angefordert"],
                                            mem=128, timeout_s=40, tag="device.discover",
                                            latency="realtime", wait_s=90)
        if err_de:
            print("[portal] device-merge: governter mDNS/SSDP-Scan lief nicht: %s" % err_de,
                  file=sys.stderr, flush=True)
            return 0
        out = (out_b or b"").decode("utf-8", "replace")
    else:
        print("[portal] device-merge: Governor (pnd) nicht erreichbar — mDNS/SSDP-Scan läuft "
              "ausnahmsweise direkt auf der Box.", file=sys.stderr, flush=True)
        pr = subprocess.run([sys.executable, script, "--dur", "6", "--angefordert"],
                            capture_output=True, text=True, timeout=60)
        out = pr.stdout or ""
    for ln in out.splitlines():
        if ln.startswith("DEVICES_JSON="):
            payload = json.loads(ln[len("DEVICES_JSON="):])
            return _DEVICE_REG.merge_discovered(payload)
    return 0

_DEVICE_SCAN_CFG_PATH = os.path.join(DATA_DIR, "device_scan.json")
_DEVICE_SCAN_CFG_CACHE = {"mtime": None, "cfg": None}
_DEVICE_SCAN_CFG_LK = threading.Lock()
_zst.register("portal_voice_core._DEVICE_SCAN_CFG_CACHE", "cache", __name__, ref=_DEVICE_SCAN_CFG_CACHE,
              beschreibung="device_scan.json mtime-gecacht",
              neustart="verfaellt", schreiber="_device_scan_cfg()")

def _device_scan_cfg():

    defaults = {"enabled": True, "aktiv_suchen": False, "interval_min": 1, "paused_until": None}
    with _DEVICE_SCAN_CFG_LK:
        try:
            mt = os.stat(_DEVICE_SCAN_CFG_PATH).st_mtime
        except OSError:
            _DEVICE_SCAN_CFG_CACHE["mtime"] = None
            _DEVICE_SCAN_CFG_CACHE["cfg"] = dict(defaults)
            return dict(defaults)
        if _DEVICE_SCAN_CFG_CACHE["mtime"] != mt or _DEVICE_SCAN_CFG_CACHE["cfg"] is None:
            cfg = dict(defaults)
            try:
                with open(_DEVICE_SCAN_CFG_PATH) as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    cfg["enabled"] = bool(raw.get("enabled", True))
                    cfg["aktiv_suchen"] = bool(raw.get("aktiv_suchen", False))
                    try:
                        cfg["interval_min"] = max(1, int(raw.get("interval_min", 1)))
                    except Exception:
                        pass
                    pu = raw.get("paused_until")
                    try:
                        cfg["paused_until"] = float(pu) if pu else None
                    except Exception:
                        cfg["paused_until"] = None
            except Exception:
                pass
            _DEVICE_SCAN_CFG_CACHE["mtime"] = mt
            _DEVICE_SCAN_CFG_CACHE["cfg"] = cfg
        return dict(_DEVICE_SCAN_CFG_CACHE["cfg"])

def _device_scan_worker():

    def loop():
        n = 0
        while True:
            cfg = _device_scan_cfg()
            paused = (not cfg["enabled"]) or bool(cfg["paused_until"] and time.time() < cfg["paused_until"])

            suchen = bool(cfg.get("aktiv_suchen"))
            if not paused:
                try:
                    _device_host_scan(angefordert=suchen)
                except Exception:
                    _traceback_log("device host scan")
                if suchen and n % 5 == 0:
                    try:
                        _device_mdns_scan(angefordert=True)
                    except Exception:
                        _traceback_log("device mdns scan")
                n += 1
            total = 30.0 if paused else cfg["interval_min"] * 60.0
            end = time.time() + total
            while True:
                rem = end - time.time()
                if rem <= 0:
                    break
                time.sleep(min(30.0, rem))
                if _device_scan_cfg() != cfg:
                    break
    threading.Thread(target=loop, daemon=True).start()

def _vext_tts(text):

    try:
        os.makedirs(TTS_DIR, exist_ok=True)
        out = os.path.join(TTS_DIR, "read-" + secrets.token_hex(6) + ".wav")
        voice_request({"op": "tts", "text": (text or "")[:3000], "out": out})
        if os.path.exists(out):
            with open(out, "rb") as f:
                data = f.read()
            try:
                os.remove(out)
            except Exception:
                pass
            return data
    except Exception:
        return None
    return None

def _vext_object_resolve(principal, oid):

    safe = os.path.basename(str(oid or ""))
    if not safe:
        return None
    p = os.path.join(ATTACH_DIR, safe)
    if os.path.exists(p):
        owner = _attach_owner_map().get(safe)
        if owner is None or owner == principal:
            return p
    return None

def _vext_tmux_by_id(uid, kind, sid):

    try:
        s = _session_store(uid, kind).get(sid)
        return s and s.get("tmux")
    except Exception:
        return None

def _vext_live_tmux(uid, kind):

    legacy = tmux_session(uid, kind)
    pref = None
    try:
        import portal_sessions as _ps
        pref = _ps._uid_tag(uid) + "-" + re.sub(r"[^a-z]", "", str(kind))[:12] + "-"
    except Exception:
        pass
    best = None
    try:
        r = subprocess.run(["tmux", "ls", "-F",
                            "#{session_name}\t#{session_attached}\t#{session_activity}"],
                           capture_output=True, text=True, timeout=5)
        for ln in (r.stdout or "").splitlines():
            try:
                name, att, act = ln.split("\t")
                key = (1 if int(att) > 0 else 0, int(act))
            except ValueError:
                continue
            if name != legacy and not (pref and name.startswith(pref)):
                continue
            if best is None or key > best[0]:
                best = (key, name)
    except Exception:
        pass
    return best[1] if best else legacy

def _vext_ctx():

    return {
        "home": HOME, "data_dir": DATA_DIR,
        "user_dir": user_dir, "attach_dir": ATTACH_DIR,
        "session_tmux": _vext_live_tmux,
        "session_tmux_by_id": _vext_tmux_by_id,
        "tts": _vext_tts,
        "object_resolve": _vext_object_resolve,
    }

_SESSCELL_REG = None

def _sesscell_reg():

    global _SESSCELL_REG
    if _sesscells is None:
        return None
    if _SESSCELL_REG is None:
        _SESSCELL_REG = _sesscells.SessionCellRegistry(os.path.join(DATA_DIR, "session-cells"))
    return _SESSCELL_REG

def _kiosk_post(endpoint, route, payload):

    if not endpoint:
        return False, "no endpoint"
    try:
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(endpoint.rstrip("/") + route, data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=6) as r:
            body = r.read(65536).decode("utf-8", "replace")
        try:
            return True, json.loads(body)
        except Exception:
            return True, {"raw": body[:200]}
    except Exception as e:
        return False, str(e)

def _node_health_get(endpoint, token):

    if not endpoint:
        return False, "no endpoint"
    try:
        req = urllib.request.Request(endpoint.rstrip("/") + "/health",
                                     headers={"X-Node-Token": token or ""}, method="GET")
        with urllib.request.urlopen(req, timeout=6) as r:
            body = r.read(65536).decode("utf-8", "replace")
        try:
            return True, json.loads(body)
        except Exception:
            return True, {"raw": body[:200]}
    except Exception as e:
        return False, str(e)

def _portal_base_url():

    cfg = getattr(Handler, "cfg", {}) or {}
    scheme = "https" if cfg.get("cert") else "http"
    return "%s://127.0.0.1:%s" % (scheme, cfg.get("port", 8076))

def _voice_agent_token(uid):

    uid = _uid_safe(uid)

    for tok, _rec in list(SESSIONS.items()):
        if tok.startswith("agent-") and _session_uid(tok) == uid:
            return tok
    return _session_new(uid, kind="agent", prefix="agent-")

def _agent_ctx():

    return {"seat_enumerate": seat_enumerate, "seat_focused": seat_focused,
            "tmux_session": tmux_session, "tmux_capture": _pane,
            "links_load": links_load, "active_lens": None}

def _agent_norm_url(u):

    u = (u or "").strip()
    if not u:
        return ""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u):
        return u
    if (" " in u) or ("." not in u):
        return "https://www.google.com/search?q=" + urllib.parse.quote(u)
    return "https://" + u

def _ensure_trusted(cwd):
    p = os.path.join(HOME, ".claude.json")
    try:
        d = json.load(open(p))
    except Exception:
        return
    proj = d.setdefault("projects", {}).setdefault(cwd, {})
    if proj.get("hasTrustDialogAccepted") is True:
        return
    proj["hasTrustDialogAccepted"] = True
    proj["hasClaudeMdExternalIncludesApproved"] = True
    proj["hasClaudeMdExternalIncludesWarningShown"] = True
    tmp = p + ".pp.tmp"; json.dump(d, open(tmp, "w"), indent=2); os.replace(tmp, p)

def _inject(sess, text):
    subprocess.run(["tmux", "set-buffer", "-b", "ppinj", "--", text], capture_output=True)
    subprocess.run(["tmux", "paste-buffer", "-d", "-p", "-b", "ppinj", "-t", sess], capture_output=True)
    time.sleep(0.2)
    subprocess.run(["tmux", "send-keys", "-t", sess, "Enter"], capture_output=True)

def _pane(sess):
    return subprocess.run(["tmux", "capture-pane", "-p", "-t", sess], capture_output=True, text=True).stdout or ""

def _voice_alive(sess):

    r = subprocess.run(["tmux", "list-panes", "-t", sess, "-F", "#{pane_current_command}"],
                       capture_output=True, text=True)
    return any(c in ("claude", "node", "claude-code") for c in (r.stdout or "").split())

VOICE_CELL = True
_VOICE_CELL_MGR = None
VOICE_TURN_TIMEOUT = int(os.environ.get("PN_VOICE_TURN_TIMEOUT", "90"))
VOICE_FIRST_WAIT = float(os.environ.get("PN_VOICE_FIRST_WAIT", "12"))
VOICE_COLD_CUE_WAIT = float(os.environ.get("PN_VOICE_COLD_CUE_WAIT", "6"))
VOICE_FIRST_GRACE = float(os.environ.get("PN_VOICE_FIRST_GRACE", "0.9"))

def _voice_cellmgr():
    global _VOICE_CELL_MGR
    if _VOICE_CELL_MGR is None:
        import pn_cell_session as _cs
        _VOICE_CELL_MGR = _cs.get_manager()
    return _VOICE_CELL_MGR

_VOICE_STREAMS = {}
_VOICE_STREAMS_LOCK = threading.Lock()
_zst.register("portal_voice_core._VOICE_STREAMS", "cursor", __name__, ref=_VOICE_STREAMS,
              beschreibung="laufende Voice-Streams je uid (Saetze, done, gen): voice_first fuellt, voice_tail draint; Verlust => Stream reisst ab (Client faengt sich am naechsten Turn)",
              neustart="verfaellt", schreiber="voice_first()/voice_tail() unter _VOICE_STREAMS_LOCK")

def _voice_stream_reset(uid):
    with _VOICE_STREAMS_LOCK:
        gen = _VOICE_STREAMS.get(uid, {}).get("gen", 0) + 1
        _VOICE_STREAMS[uid] = {"sentences": [], "done": False, "err": None, "gen": gen}
        return gen

def _voice_stream_append(uid, gen, s):
    with _VOICE_STREAMS_LOCK:
        st = _VOICE_STREAMS.get(uid)
        if st and st["gen"] == gen and s:
            st["sentences"].append(s)

def _voice_stream_finish(uid, gen, err=None):
    with _VOICE_STREAMS_LOCK:
        st = _VOICE_STREAMS.get(uid)
        if st and st["gen"] == gen:
            st["done"] = True
            st["err"] = err

def _voice_stream_get(uid):
    with _VOICE_STREAMS_LOCK:
        st = _VOICE_STREAMS.get(uid)
        if not st:
            return [], True
        return list(st["sentences"]), bool(st["done"])

def _voice_stream_is_gen(uid, gen):

    with _VOICE_STREAMS_LOCK:
        st = _VOICE_STREAMS.get(uid)
        return bool(st and st.get("gen") == gen)

def _llm_lane_reason():

    try:
        import pn_cell_session as _cs
        return _cs.llm_lane_reason()
    except Exception:
        _traceback_log("voice llm lane reason")
        return None

def voice_no_reply_de(uid=DEFAULT_PRINCIPAL):

    try:
        mgr = _voice_cellmgr()
        cell = mgr.cell(uid, _voice_session_for(uid)) if mgr else None
        r = cell.term_reason() if cell is not None else None
        if r:
            return r
    except Exception:
        _traceback_log("voice no-reply cell reason")
    try:
        import pn_cell_session as _cs
        r = _cs.llm_lane_reason()
        if r:
            return r
    except Exception:
        _traceback_log("voice no-reply llm reason")
    return ("Ich habe von der Sitzung keine Antwort bekommen. Die Zelle läuft und ein Konto ist "
            "verbunden — bitte frag gleich noch einmal.")

def _voice_cell_stream_async(uid, text, channel):

    _voice_route_maybe_revert(uid)
    _voice_route_touch(uid)
    _VOICE_ZURUF[_uid_safe(uid)] = time.time()
    gen = _voice_stream_reset(uid)
    persona = _voice_persona(uid, channel)
    frame = _voice_turn_frame(uid)
    def run():
        try:

            _lane = _llm_lane_reason()
            if _lane:
                _voice_mirror_user_input(uid, text)
                _voice_stream_append(uid, gen, _lane)
                _voice_stream_finish(uid, gen)
                return
            cell = _voice_cell(uid)
            _voice_reg_touch(uid, _voice_session_for(uid))
            _voice_mirror_user_input(uid, text)
            r = cell.voice_turn(frame + text, on_sentence=lambda s: _voice_stream_append(uid, gen, s),
                                timeout=VOICE_TURN_TIMEOUT, system=persona)

            try:
                _ans = ((r.get("text") if isinstance(r, dict) else "") or "").strip()
                if (not _ans or _ans == "(keine Antwort erhalten)") and not cell.term_runner_alive():
                    _voice_stream_append(uid, gen, "Die Session war eingefroren - ich starte sie neu und versuche es noch einmal.")
                    try:
                        import pn_session_watchdog as _wd
                        _wd.restart_now(uid, _voice_session_for(uid))
                    except Exception:
                        _traceback_log("voice watchdog restart")
                    r = cell.voice_turn(frame + text, on_sentence=lambda s: _voice_stream_append(uid, gen, s),
                                        timeout=VOICE_TURN_TIMEOUT, system=persona)
            except Exception:
                _traceback_log("voice frozen recovery")

            _sents, _ = _voice_stream_get(uid)
            if not _sents and not (isinstance(r, dict) and r.get("busy")):
                _final = ((r.get("text") if isinstance(r, dict) else "") or "").strip()
                if not _final or _final == "(keine Antwort erhalten)":
                    _final = voice_no_reply_de(uid)
                sys.stderr.write("[voice] leere Antwort uid=%s -> %s\n" % (uid, _final[:160]))
                _voice_stream_append(uid, gen, _final)
                _voice_stream_finish(uid, gen)
                return

            try:
                if isinstance(r, dict) and r.get("path"):
                    cell.voice_watch(r.get("path"), r.get("off0", 0), r.get("emitted", 0),
                                     on_sentence=lambda s: _voice_stream_append(uid, gen, s),
                                     should_continue=lambda: _voice_stream_is_gen(uid, gen),
                                     budget=_NABU_LATE_MAX)
            except Exception:
                _traceback_log("voice cell watch")

            try:
                _sents, _ = _voice_stream_get(uid)
                if not _sents:
                    _final = ((r.get("text") if isinstance(r, dict) else "") or "").strip()
                    if not _final or _final == "(keine Antwort erhalten)":
                        _final = voice_no_reply_de(uid)

                    sys.stderr.write("[voice] leere Antwort uid=%s gen=%s cur_gen=%s -> %s\n"
                                     % (uid, gen, _VOICE_STREAMS.get(uid, {}).get("gen"), _final[:160]))
                    _voice_stream_append(uid, gen, _final)
            except Exception:
                _traceback_log("voice cell empty-turn reason")
            _voice_stream_finish(uid, gen)
        except Exception:
            _traceback_log("voice cell stream")
            _voice_stream_append(uid, gen, "Die isolierte Sitzung ist gerade nicht erreichbar. Bitte gleich nochmal.")
            _voice_stream_finish(uid, gen, err="cell")
    threading.Thread(target=run, daemon=True).start()
    return gen

def _policy_store_mod():
    return _policy.PolicyStore(os.path.join(DATA_DIR, "session-policies"))

def _global_floor():
    try:
        return _policy.validate(json.load(open(os.path.join(DATA_DIR, "session-policies", "policy-floor.json"))))
    except Exception:
        return None

_LAN_PREFIX = None

def _origin_for_ip(ip):

    global _LAN_PREFIX
    ip = (ip or "").strip().replace("::ffff:", "")
    if ip.startswith("127.") or ip == "::1":
        return "lan"
    if _LAN_PREFIX is None:
        try:
            r = subprocess.run(["ip", "-4", "route", "get", "8.8.8.8"], capture_output=True, text=True, timeout=3)
            toks = (r.stdout or "").split()
            _LAN_PREFIX = toks[toks.index("src") + 1].rsplit(".", 1)[0] + "." if "src" in toks else ""
        except Exception:
            _LAN_PREFIX = ""
    if _LAN_PREFIX and ip.startswith(_LAN_PREFIX):
        return "lan"
    return "offlan"

def _known_principals():

    try:
        import portal_users
        us = [u.get("uid") for u in (portal_users.user_list() or [])
              if isinstance(u, dict) and u.get("uid") and u.get("status") not in ("deleted",)]
        if us:
            return sorted(set(us))
    except Exception:
        pass
    users = set()
    try:
        for u in json.load(open(os.path.join(DATA_DIR, "sessions.json"))).values():
            if isinstance(u, str) and u:
                users.add(u)
    except Exception:
        pass
    return sorted(users)

def _voice_kits(gewuenscht=None):

    try:
        import pn_software_shelf as _regal
    except Exception:
        return []
    try:
        if gewuenscht:
            ids = [str(k).strip() for k in gewuenscht if str(k).strip()]
        else:
            ids = [k for k in sorted(os.listdir(_regal.KITS_ROOT)) if not k.startswith("_")]
        return [k for k in ids if _regal.kit_img(k)][:6]
    except Exception:
        return []

def _voice_policy_enf(uid):

    try:
        eff = _policy_store_mod().effective(uid, "voice", "default", global_floor=_global_floor())
        enf = _policy.enforcement(eff)
        caps = eff.get("caps") or {}

        enf["model"] = caps.get("model") or os.environ.get("PN_VOICE_MODEL", "opus")
        if caps.get("effort"):
            enf["effort"] = caps["effort"]
        enf["orchestrator"] = True
        tz = _tresor_dir(uid)
        enf.setdefault("fs_read", [])
        if not any(isinstance(r, dict) and r.get("path") == tz for r in enf["fs_read"]):
            enf["fs_read"].append({"path": tz, "mode": "ro"})

        enf["kits"] = _voice_kits(caps.get("kits"))
        return enf
    except Exception:
        return {}

_GRANT_LAEUFT = threading.local()

def _eigenen_ordner_sichern(uid, sid):

    if getattr(_GRANT_LAEUFT, "drin", False):
        return
    _GRANT_LAEUFT.drin = True
    try:
        import portal_routes_session as _prs
        titel = None
        try:
            titel = (_sessprov_get(uid, sid) or {}).get("title")
        except Exception:
            titel = None

        _prs._share_pub_granted(sid, titel, uid, neu_pruefen=True)
    except Exception:
        pass
    finally:
        _GRANT_LAEUFT.drin = False

def _cockpit_policy_enf(uid, sid):

    _eigenen_ordner_sichern(uid, sid)
    try:
        enf = _policy.enforcement(_policy_store_mod().effective(uid, "cockpit", sid,
                                                               global_floor=_global_floor()))
    except Exception:
        try:
            enf = _policy.enforcement(_sess_policy_get(uid, sid) or {})
        except Exception:
            enf = {}

    try:
        prov = _sessprov_get(uid, sid) or {}
        if prov.get("model"):
            enf["model"] = str(prov["model"])
        if prov.get("effort"):
            enf["effort"] = str(prov["effort"])

        if prov.get("kits"):
            enf["kits"] = [str(k) for k in (prov.get("kits") or []) if k]

        try:
            _mm = int(prov.get("mem_mb") or 0)
        except (TypeError, ValueError):
            _mm = 0
        try:
            _dm = int(prov.get("disk_mb") or 0)
        except (TypeError, ValueError):
            _dm = 0
        if prov.get("orchestrator"):
            _mm = _mm or 4096
            _dm = _dm or 2048
        if _mm:
            enf["mem_mb"] = max(1024, min(_mm, 12288))
        if _dm:
            enf["delta_mb"] = max(512, min(_dm, 16384))
        if prov.get("runtime"):
            enf["runtime"] = str(prov["runtime"])

            if enf["runtime"] == "ollama":
                try:
                    import llm_endpoints as _ep
                    _ent = _ep.get("ollama") or next((e for e in (_ep.entries() or [])
                                                      if (e.get("discovery") == "ollama")), None)
                    if _ent and _ent.get("base_url"):
                        enf["ollama_base"] = str(_ent["base_url"])
                        if not enf.get("model") and _ent.get("model"):
                            enf["model"] = str(_ent["model"])

                        import urllib.parse as _up
                        _sp = _up.urlsplit(_ent["base_url"])
                        _h = _sp.hostname

                        _hp = ("%s:%d" % (_h, _sp.port or (443 if _sp.scheme == "https" else 80))) if _h else None
                        _nh = list(enf.get("net_hosts") or [])
                        for _g in ((_hp,) if _hp else ()) + ("models.dev",):
                            if _g and _g not in _nh:
                                _nh.append(_g)
                        enf["net_hosts"] = _nh

                        if _hp:
                            enf["llm_ticket_hosts"] = [_hp]
                except Exception:
                    pass

        if prov.get("desktop"):
            enf["desktop"] = True
            try:
                import pn_cell_session as _cs
                enf["mem_mb"] = max(int(enf.get("mem_mb") or 0), _cs.OFFICE_MEM_MB)
            except Exception:
                pass
    except Exception:
        pass
    _cockpit_place_node(uid, sid, enf)
    return enf

_os_path_join = os.path.join

def _needs_local_lanes(enf):

    try:
        if str(os.environ.get("PN_REMOTE_LANES") or "").strip() in ("1", "true", "yes"):
            return False
        if os.path.exists(os.path.join(DATA_DIR, "remote_lanes.on")):
            return False
    except Exception:
        pass
    try:
        if str(enf.get("net_general") or "deny") != "deny":
            return True
        if str(enf.get("net_internal") or "deny") != "deny":
            return True
        if enf.get("net_hosts"):
            return True
        if enf.get("portal_enabled") and enf.get("portal_verbs"):
            return True
        for k in ("hpc_submit", "orchestrate", "portal_state"):
            if str(enf.get(k) or "deny") != "deny":
                return True
    except Exception:
        return True
    return False

def _cockpit_place_node(uid, sid, enf):

    try:
        if str(sid) == "__voice__" or enf.get("node"):
            return

        try:
            if str(sid) == _voice_sess_name():
                return
        except Exception:
            pass
        if enf.get("vpn_netns") or enf.get("secrets"):

            return
        import portal_session_svc as _svc
        prov = _svc._sessprov_get(uid, sid) or {}
        pin = prov.get("node")
        if pin:
            import portal_placement as _pp
            if pin != _pp.LOCAL_ID:

                if _needs_local_lanes(enf):
                    try:
                        import sys as _sys
                        _sys.stderr.write("[placement] %s/%s: Pin auf %s ignoriert — Session braucht "
                                          "NETZ/PORTAL, remote gibt es beides nicht -> lokal\n"
                                          % (uid, sid, pin))
                    except Exception:
                        pass
                    return
                enf["node"] = str(pin)
            return
        if enf.get("desktop") or prov.get("desktop") or enf.get("vpn_netns") or prov.get("vpn"):
            return
        if _needs_local_lanes(enf):
            return
        try:

            import glob as _glob
            import pn_cell_session as _cs
            if _glob.glob(_os_path_join(_cs.VOL_DIR, "*-%s_*-delta.img" % sid)):
                return
        except Exception:
            return
        import portal_placement as _pp
        want = int(enf.get("mem_mb") or 0) or 1536
        nid = _pp.pick_node(want, arch_pref=None)
        if not nid:
            return
        try:
            _svc._sessprov_set(uid, sid, {"node": nid})
        except Exception:
            return
        if nid != _pp.LOCAL_ID:
            enf["node"] = nid
    except Exception:
        pass

_VOICE_RIGHTS_NOTICE = {}

def _voice_rights_changed(uid, msg):

    try:
        enf = _voice_policy_enf(uid)
        mgr = _voice_cellmgr()
        cell = mgr.cell(uid, _voice_session_for(uid)) if hasattr(mgr, "cell") else None
        if cell is not None:
            cell.update_policy(enf)
    except Exception:
        _traceback_log("rights update cell")
    _VOICE_RIGHTS_NOTICE[uid] = msg or "Deine Nutzerrechte wurden geändert."

VOICE_DAY_ROLL_H = int(os.environ.get("PN_VOICE_DAY_ROLL_H", "3"))
VOICE_WARM_H0 = int(os.environ.get("PN_VOICE_WARM_H0", "6"))
VOICE_WARM_H1 = int(os.environ.get("PN_VOICE_WARM_H1", "24"))

def _voice_day():

    return time.strftime("%Y%m%d", time.localtime(time.time() - VOICE_DAY_ROLL_H * 3600))

VOICE_PERSIST = os.environ.get("PN_VOICE_PERSIST", "1") not in ("0", "false", "no")
_VOICE_PERSIST_P = os.path.join(DATA_DIR, "voice-persist.json")
_VOICE_PERSIST_LOCK = threading.Lock()

def _voice_persist_name():

    with _VOICE_PERSIST_LOCK:
        try:
            d = json.load(open(_VOICE_PERSIST_P))
            name = str((d or {}).get("session") or "")
            if name.startswith("voice-"):
                return name
        except Exception:
            pass
        name = "voice-" + _voice_day()
        try:
            tmp = _VOICE_PERSIST_P + ".tmp"
            json.dump({"session": name, "seit": int(time.time())}, open(tmp, "w"))
            os.replace(tmp, _VOICE_PERSIST_P)

            if callable(_prov_log):
                _prov_log("voice.persist.pin", DEFAULT_PRINCIPAL, name, {"wire": "auto"})
        except Exception:
            if callable(_traceback_log):
                _traceback_log("voice persist pin")
        return name

def _voice_sess_name(day=None):
    if day:
        return "voice-" + day
    if VOICE_PERSIST:
        return _voice_persist_name()
    return "voice-" + _voice_day()

def _pid_alive(pid):
    try:
        os.kill(int(pid), 0); return True
    except Exception:
        return False

def _cast_slug(name, addr):
    return ("".join(c if c.isalnum() else "-" for c in ("%s-%s" % (name, addr))).strip("-").lower()[:48]) or "cast"

def _tresor_dir(uid):

    d = os.path.join(os.path.expanduser("~/.local/share/brainarbeit-tresor"), _uid_safe(uid))
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d

def _voice_archive(uid, sess_name):

    mgr = _voice_cellmgr()
    try:
        cell = mgr.cell(uid, sess_name)
        if cell is not None and cell.alive():
            day = sess_name.replace("voice-", "")
            dst = os.path.join(_tresor_dir(uid), day)
            os.makedirs(dst, exist_ok=True)
            ok, out = cell._run("cat /root/.claude/projects/*/*.jsonl 2>/dev/null; echo __TE__", "__TE__", 25)
            body = out.split("__TE__")[0] if "__TE__" in out else ""
            if body.strip():
                open(os.path.join(dst, "transcript.jsonl"), "w").write(body)
            try:
                r = cell.voice_turn(
                    "Fasse unser Gespräch von heute in 4–6 knappen Stichpunkten zusammen "
                    "(nur Fakten/Entscheidungen), als Gedächtnisnotiz für morgen. Reine Merkliste, kein Vorwort.",
                    timeout=60)
                open(os.path.join(dst, "summary.txt"), "w").write((r.get("text") or "").strip() + "\n")
            except Exception:
                pass
    except Exception:
        _traceback_log("voice archive")
    finally:
        try:
            mgr.stop(uid, sess_name)
        except Exception:
            pass

_VOICE_STAND_P = os.path.join(DATA_DIR, "voice-stand.json")
_VOICE_STAND_LOCK = threading.Lock()
_VOICE_ZURUF = {}
_VOICE_ABLAGE_GRUND = {}

VOICE_COMPACT_TOKENS = int(os.environ.get("PN_VOICE_COMPACT_TOKENS", "120000"))
VOICE_COMPACT_RUHE_S = int(os.environ.get("PN_VOICE_COMPACT_RUHE_S", "900"))
VOICE_COMPACT_ABSTAND_S = int(os.environ.get("PN_VOICE_COMPACT_ABSTAND_S", "3600"))

def _voice_stand_load():
    try:
        d = json.load(open(_VOICE_STAND_P))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _voice_stand_set(uid, **werte):
    with _VOICE_STAND_LOCK:
        d = _voice_stand_load()
        e = d.get(_uid_safe(uid)) if isinstance(d.get(_uid_safe(uid)), dict) else {}
        e.update(werte)
        d[_uid_safe(uid)] = e
        try:
            tmp = _VOICE_STAND_P + ".tmp"
            json.dump(d, open(tmp, "w"), indent=1)
            os.replace(tmp, _VOICE_STAND_P)
        except Exception:
            _traceback_log("voice stand save")

_ZELL_HAPPEN = 48 * 1024

def _zelle_datei_holen(cell, pfad, start, ende):

    stuecke = []
    versatz = int(start)
    while versatz < int(ende):
        wieviel = min(_ZELL_HAPPEN, int(ende) - versatz)
        ok, out = cell._run(
            "/bin/python3 -c \"import base64,sys;f=open('%s','rb');f.seek(%d);"
            "sys.stdout.write(base64.b64encode(f.read(%d)).decode())\" 2>/dev/null; echo __TB__"
            % (pfad, versatz, wieviel), "__TB__", 40)
        if not ok:
            break
        roh = "".join(out.split())
        if not roh:
            break
        try:
            stueck = base64.b64decode(roh)
        except Exception:
            break
        if not stueck:
            break
        stuecke.append(stueck)
        versatz += len(stueck)
        if len(stueck) < wieviel:
            break
    return b"".join(stuecke)

def _voice_tresor_schnappschuss(uid, sess, mit_zusammenfassung=False):

    mgr = _voice_cellmgr()
    try:
        cell = mgr.cell(uid, sess)
        if cell is None or not cell.alive():
            _VOICE_ABLAGE_GRUND[_uid_safe(uid)] = ("Zelle nicht greifbar (%s)"
                                                   % ("unbekannt" if cell is None else "gestoppt"))
            return False

        grenze = 12 * 1024 * 1024
        pfad = cell._incell_active_jsonl()
        if not pfad:
            _VOICE_ABLAGE_GRUND[_uid_safe(uid)] = "kein Gespraechs-Log in der Zelle"
            return False
        ok, out = cell._run("busybox wc -c < '%s' 2>/dev/null; echo __SZ__" % pfad, "__SZ__", 20)
        try:
            groesse = int((out.split("__SZ__")[0].strip().split() or ["0"])[0])
        except (ValueError, IndexError):
            groesse = 0
        start = max(0, groesse - grenze)
        body = _zelle_datei_holen(cell, pfad, start, groesse).decode("utf-8", "replace")
        if not body.strip():
            _VOICE_ABLAGE_GRUND[_uid_safe(uid)] = ("Gespraechs-Log lieferte nichts (gemessen %d Bytes, %s)"
                                                   % (groesse, pfad))

            return False
        dst = os.path.join(_tresor_dir(uid), _voice_day())
        os.makedirs(dst, exist_ok=True)
        open(os.path.join(dst, "transcript.jsonl"), "w").write(body)
        if start > 0:
            open(os.path.join(dst, "HINWEIS.txt"), "w").write(
                "Nur das ENDE des Gespraechs gesichert: ab Byte %d von %d (Grenze %d).\n"
                "Der vollstaendige Verlauf liegt weiterhin im Delta der Zelle.\n"
                % (start, groesse, grenze))
        if mit_zusammenfassung:
            try:
                r = cell.voice_turn(
                    "Fasse unser Gespräch bis hierher in 5–8 knappen Stichpunkten zusammen "
                    "(nur Fakten/Entscheidungen/offene Punkte), als Gedächtnisnotiz. "
                    "Reine Merkliste, kein Vorwort.", timeout=90)
                open(os.path.join(dst, "summary.txt"), "a").write(
                    "\n## %s\n%s\n" % (time.strftime("%H:%M"), (r.get("text") or "").strip()))
            except Exception:
                _traceback_log("voice tresor summary")
        return True
    except Exception as e:
        _VOICE_ABLAGE_GRUND[_uid_safe(uid)] = "Fehler: %s" % (str(e)[:160] or type(e).__name__)
        _traceback_log("voice tresor schnappschuss")
        return False

def _voice_tagesablage(uid, sess):

    tag = _voice_day()
    if (_voice_stand_load().get(_uid_safe(uid)) or {}).get("tag") == tag:
        return
    if _voice_tresor_schnappschuss(uid, sess):
        _voice_stand_set(uid, tag=tag)
        return
    try:
        import sys as _sys
        _sys.stderr.write("[sprache] Tagesablage %s/%s nicht moeglich: %s\n"
                          % (uid, sess, _VOICE_ABLAGE_GRUND.get(_uid_safe(uid)) or "kein Grund"))
        _sys.stderr.flush()
    except Exception:
        pass

def _voice_autoverdichtung(uid):

    if not VOICE_PERSIST:
        return None
    try:
        sess = _voice_session_for(uid)
        mgr = _voice_cellmgr()
        cell = mgr.cell(uid, sess) if hasattr(mgr, "cell") else None
        if cell is None or not cell.alive() or not getattr(cell, "term_on", False):
            return None
        stand = _voice_stand_load().get(_uid_safe(uid)) or {}
        if (time.time() - float(stand.get("verdichtet") or 0)) < VOICE_COMPACT_ABSTAND_S:
            return None
        ruhe = time.time() - float(_VOICE_ZURUF.get(_uid_safe(uid)) or 0)
        if ruhe < VOICE_COMPACT_RUHE_S:
            return None
        tokens = cell.kontext_tokens() if hasattr(cell, "kontext_tokens") else None
        if tokens is None or tokens < VOICE_COMPACT_TOKENS:
            return None
        _voice_stand_set(uid, verdichtet=int(time.time()), tokens=int(tokens))
        _voice_tresor_schnappschuss(uid, sess, mit_zusammenfassung=True)
        ok, grund = cell.verdichten(timeout=300)
        try:
            nachher = cell.kontext_tokens()
        except Exception:
            nachher = None
        _prov_log("voice.verdichtung", uid,
                  json.dumps({"session": sess, "vorher": tokens, "nachher": nachher,
                              "ok": bool(ok), "grund": grund}), {"wire": "auto"})
        return bool(ok)
    except Exception:
        _traceback_log("voice autoverdichtung")
        return None

def _voice_reg_touch(uid, session):

    try:
        reg = _sesscell_reg()
        if not reg:
            return
        if reg.get(uid, session) is None:
            reg.provision(uid, session, autonomy=(_sesscells.DEFAULT_AUTONOMY if _sesscells else 1))
        else:
            reg.attach(uid, session)
    except Exception:
        _traceback_log("voice reg touch")

def _voice_stale_daily(uid, target):

    mgr = _voice_cellmgr()
    today = _voice_sess_name()
    stale = [s for s in mgr.sessions_for(uid)
             if s.startswith("voice-") and s != target and s != today]
    try:
        reg = _sesscell_reg()
        for r in (reg.list_live(uid) if reg else []):
            s = r.get("session") or ""
            if (s.startswith("voice-") and s != target and s != today and s not in stale
                    and r.get("state") != (_sesscells.EVICTED if _sesscells else "evicted")):
                stale.append(s)
    except Exception:
        _traceback_log("voice stale scan")
    return stale

def _voice_rotate_and_prewarm(uid, ensure=True):

    try:
        _voice_route_maybe_revert(uid)
        mgr = _voice_cellmgr()
        target = _voice_session_for(uid)
        for s in _voice_stale_daily(uid, target):
            if not mgr.is_warm(uid, s):
                try:
                    _voice_cell(uid, sess=s)
                except Exception:
                    _traceback_log("voice stale boot")
            _voice_archive(uid, s)
            try:
                _rz = _sesscell_reg()
                if _rz and _rz.get(uid, s) is not None:
                    _rz.evict(uid, s, reason="daily-rotation")
            except Exception:
                _traceback_log("voice reg evict")
        was_warm = mgr.is_warm(uid, target)
        if not (ensure or was_warm):
            return
        _voice_cell(uid)
        cell = mgr.cell(uid, target)
        if cell is not None:
            cell.update_policy(_voice_policy_enf(uid))
            try:
                cell.stage_sonos()
                if hasattr(cell, "netz_tor_sicherstellen"):

                    cell.netz_tor_sicherstellen()
            except Exception:
                pass
            if not was_warm:
                cell.start_terminal(system=_voice_persona(uid, "nabu"))

            if VOICE_PERSIST:

                _voice_tagesablage(uid, target)
                _voice_autoverdichtung(uid)
        _voice_reg_touch(uid, target)
    except Exception:
        _traceback_log("voice prewarm")

_VOICE_PREWARM_P = os.path.join(DATA_DIR, "voice-prewarm.json")
VOICE_PREWARM_MODES = ("warm", "wakeword")

def _voice_prewarm_mode():

    try:
        m = json.load(open(_VOICE_PREWARM_P)).get("mode")
    except Exception:
        m = None
    m = m or os.environ.get("PN_VOICE_PREWARM", "wakeword")
    return m if m in VOICE_PREWARM_MODES else "wakeword"

def _voice_prewarm_set(mode):
    mode = mode if mode in VOICE_PREWARM_MODES else "warm"
    tmp = _VOICE_PREWARM_P + ".tmp"
    json.dump({"mode": mode}, open(tmp, "w"))
    os.replace(tmp, _VOICE_PREWARM_P)
    return mode

_VOICE_ROUTE_P = os.path.join(DATA_DIR, "voice-route.json")
_VOICE_ROUTE_LOCK = threading.Lock()

def _voice_route_load():
    try:
        d = json.load(open(_VOICE_ROUTE_P))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _voice_route_set(uid, session):
    with _VOICE_ROUTE_LOCK:
        d = _voice_route_load()
        u = _uid_safe(uid); meta = _voice_route_meta_load()
        if session:
            d[u] = str(session)[:64]
            _n = int(time.time()); meta[u] = {"set_at": _n, "last": _n, "day": _voice_sess_name()}
        else:
            d.pop(u, None); meta.pop(u, None)
        tmp = _VOICE_ROUTE_P + ".tmp"
        json.dump(d, open(tmp, "w"), indent=1)
        os.replace(tmp, _VOICE_ROUTE_P)
        _voice_route_meta_save(meta)

def _voice_session_for(uid):

    return _voice_route_load().get(_uid_safe(uid)) or _voice_sess_name()

def _voice_route_options(uid):

    opts = {_voice_sess_name(): ("Sprach-Session (dauerhaft)" if VOICE_PERSIST
                                 else "Tages-Session (Standard)")}
    try:
        for c in _voice_cellmgr().list_live():
            if c.get("principal") == uid:
                opts.setdefault(c.get("session"), c.get("session"))
    except Exception:
        pass
    try:
        reg = _sesscell_reg()
        for c in (reg.list_live(uid) if reg else []):
            s = c.get("session") or c.get("sid")
            if s:
                opts.setdefault(s, s)
    except Exception:
        pass
    return [{"session": k, "label": v} for k, v in opts.items()]

_VOICE_ROUTE_META_P = os.path.join(DATA_DIR, "voice-route-meta.json")
_VOICE_ROUTE_NOTICE = {}
VOICE_ROUTE_IDLE_S = int(os.environ.get("PN_VOICE_ROUTE_IDLE_S", "3600"))

def _voice_route_meta_load():
    try:
        d = json.load(open(_VOICE_ROUTE_META_P)); return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _voice_route_meta_save(d):
    tmp = _VOICE_ROUTE_META_P + ".tmp"
    json.dump(d, open(tmp, "w"), indent=1); os.replace(tmp, _VOICE_ROUTE_META_P)

def _voice_route_touch(uid):

    with _VOICE_ROUTE_LOCK:
        u = _uid_safe(uid); d = _voice_route_meta_load()
        if isinstance(d.get(u), dict):
            d[u]["last"] = int(time.time()); _voice_route_meta_save(d)

def _voice_route_maybe_revert(uid):

    with _VOICE_ROUTE_LOCK:
        u = _uid_safe(uid); route = _voice_route_load(); sess = route.get(u)
        if not sess:
            return False
        meta = _voice_route_meta_load(); m = meta.get(u) if isinstance(meta.get(u), dict) else {}
        now = time.time()
        last = float(m.get("last") or m.get("set_at") or now)
        day = m.get("day")
        rolled = bool(day) and day != _voice_sess_name()
        idle = (now - last) > VOICE_ROUTE_IDLE_S
        if not (rolled or idle):
            return False
        route.pop(u, None)
        _tmp = _VOICE_ROUTE_P + ".tmp"; json.dump(route, open(_tmp, "w"), indent=1); os.replace(_tmp, _VOICE_ROUTE_P)
        meta.pop(u, None); _voice_route_meta_save(meta)
    reason = "es ist ein neuer Tag" if rolled else "über eine Stunde ohne Sprach-Eingabe"
    _VOICE_ROUTE_NOTICE[uid] = (
        "[AUTO-WECHSEL] Du bist automatisch von der zuvor gewählten Arbeits-Session „%s“ zurück auf die "
        "reguläre Tages-Voice-Session geschwenkt (Grund: %s). Teile das dem Nutzer in EINEM kurzen Satz "
        "zu Beginn deiner Antwort mit und weise ihn hin: wenn er an einer bestimmten Session "
        "weiterarbeiten will, soll er sie im Portal oder Client als Voice-Session auswählen." % (sess, reason))
    try:
        _prov_log("voice.route.autorevert", uid, json.dumps({"was": sess, "reason": reason}), {"wire": "auto"})
    except Exception:
        pass
    return True

def _hpc_site_get(key, default=""):

    v = os.environ.get(key)
    if v:
        return v
    for p in ("/etc/brainbox/site.conf", "/run/brainbox/site.env"):
        try:
            for line in open(p):
                line = line.strip()
                if line.startswith(key + "="):
                    v = line.split("=", 1)[1].strip()
                    if v:
                        return v
        except Exception:
            pass
    return default

HPC_VPN_ID = _hpc_site_get("HPC_VPN_ID", "hpc")
HPC_VPN_BIN = os.path.expanduser(
    _hpc_site_get("HPC_VPN_BIN")
    or os.environ.get("PN_HPC_VPN", "").strip()
    or "~/.local/bin/vpn-tunnel")

HPC_SSH_TARGET = (_hpc_site_get("HPC_SSH_TARGET")
                  or os.environ.get("PN_HPC_HOST", "").strip()
                  or os.environ.get("PN_HPC_HOST", "").strip()
                  or "hpc-front1")
HPC_VPN_BIN = HPC_VPN_BIN
HPC_HOST = HPC_SSH_TARGET

def _hpc_auth_url():

    try:
        import os as _os, time as _t
        p = "/tmp/sso_current.txt"
        if _t.time() - _os.stat(p).st_mtime > 600:
            return None
        u = open(p, encoding="utf-8", errors="replace").read().strip()
        return u if u.startswith("http") else None
    except Exception:
        return None

def _hpc_status():

    try:
        pr = subprocess.run([HPC_VPN_BIN, "status-json"], capture_output=True, text=True, timeout=20)
        line = (pr.stdout or "").strip().splitlines()[-1]
        return json.loads(line)
    except Exception:
        return {"id": HPC_VPN_ID, "connected": False, "netns": False, "tunnel": False}

_NETNS_MGR = os.path.expanduser("~/.local/bin/pn_vpn_netns.py")
_NETNS_ASKPASS = "/tmp/.pnvpn-portal-askpass.sh"

def _netns_backends():
    try:
        return {e.get("id") for e in _vpn_registry() if e.get("backend") == "netns"}
    except Exception:
        return set()

def _netns_uid(principal):
    import zlib
    return 1000 + (zlib.crc32((principal or "owner").encode()) % 200)

def _netns_gateway(vid):

    for e in _vpn_registry():
        if e.get("id") == vid:
            return e.get("gateway")
    return None

def _netns_vpn(cmd, uid, vid, timeout=90, session=None, force=False):

    if not os.path.exists(_NETNS_ASKPASS):
        with open(_NETNS_ASKPASS, "w") as f:
            f.write("#!/bin/bash\n%s/.local/bin/phantom secret get sudo_pass\n" % HOME)
        os.chmod(_NETNS_ASKPASS, 0o755)
    gw = _netns_gateway(vid)
    if not gw:
        return {"error": "no gateway configured for VPN '%s'" % vid}
    env = dict(os.environ); env["SUDO_ASKPASS"] = _NETNS_ASKPASS

    args = ["sudo", "-A", "PN_PORTAL_DATA=%s" % DATA_DIR, "python3", _NETNS_MGR,
            cmd, "--uid", str(uid), "--vpn", vid,
            "--gateway", gw, "--timeout", str(timeout)]
    if session:
        args += ["--session", str(session)]
    if force:
        args += ["--force"]
    try:
        pr = subprocess.run(args, capture_output=True, text=True, timeout=timeout + 45, env=env)
        return json.loads((pr.stdout or "").strip().splitlines()[-1])
    except subprocess.TimeoutExpired:

        return {"error": "VPN-Helfer: Zeitueberschreitung (%ss)" % (timeout + 45)}
    except Exception as e:
        return {"error": "VPN-Helfer fehlgeschlagen (%s)" % e.__class__.__name__}

_NETNS_ST_TTL = 8.0
_NETNS_ST_LK = threading.Lock()
_NETNS_ST_CACHE = {}
_NETNS_ST_INFLIGHT = {}
_zst.register("portal_voice_core._NETNS_ST_CACHE", "cache", __name__, ref=_NETNS_ST_CACHE, ttl_s=8.0,
              beschreibung="VPN-netns-Statusproben je (uid, vid, session); Fehler werden MIT gecacht (ehrliche Antwort ohne 20-s-Timeout im Sekundentakt)",
              neustart="verfaellt", schreiber="_netns_status_probe_store() (Hintergrund-Prober)")
_zst.register("portal_voice_core._NETNS_ST_INFLIGHT", "singleton", __name__, ref=_NETNS_ST_INFLIGHT,
              beschreibung="genau EIN laufender netns-Prober je Key (Single-Flight-Events)",
              neustart="verfaellt", schreiber="Status-Leser")

def _netns_status_probe_store(key, uid, vid, session, ev):
    try:
        r = _netns_vpn("status", uid, vid, timeout=20, session=session)
    except Exception as e:
        r = {"error": "VPN-Statusprobe fehlgeschlagen (%s)" % e.__class__.__name__}
    with _NETNS_ST_LK:
        _NETNS_ST_CACHE[key] = (time.time(), r)
        _NETNS_ST_INFLIGHT.pop(key, None)
    ev.set()
    return r

def _netns_vpn_status_cached(uid, vid, session=None, ttl=_NETNS_ST_TTL):

    key = (int(uid), str(vid), str(session or ""))
    now = time.time()
    with _NETNS_ST_LK:
        ent = _NETNS_ST_CACHE.get(key)
        if ent and now - ent[0] < ttl:
            return dict(ent[1])
        inflight = _NETNS_ST_INFLIGHT.get(key)
        if inflight is None:
            ev = threading.Event()
            _NETNS_ST_INFLIGHT[key] = ev
        else:
            ev = inflight
        stale = dict(ent[1]) if ent else None
    if stale is not None:

        if inflight is None:
            threading.Thread(target=_netns_status_probe_store,
                             args=(key, uid, vid, session, ev), daemon=True).start()
        return stale

    if inflight is None:
        return dict(_netns_status_probe_store(key, uid, vid, session, ev))
    ev.wait(timeout=70)
    with _NETNS_ST_LK:
        ent = _NETNS_ST_CACHE.get(key)
    if ent:
        return dict(ent[1])
    return {"error": "VPN-Statusprobe laeuft noch (keine Antwort im Zeitfenster)"}

