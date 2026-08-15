
import json
import os
import re
import shutil
import threading
import importlib.util

HOME = os.path.expanduser("~")
DATA_DIR = os.environ.get("BRAINBOX_DATA_DIR") or os.path.join(HOME, ".local", "share", "brainbox-portal")
ADDON_DIR = os.path.join(DATA_DIR, "widget-addons")
REPO_ADDON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "widget-addons")

_LOCK = threading.RLock()
_MODULES = {}
_CTX = {}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

def configure(**kw):

    _CTX.update({k: v for k, v in kw.items() if v is not None})

def _manifest(d):
    try:
        with open(os.path.join(d, "manifest.json"), encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) and _ID_RE.match(str(m.get("id") or "")) else None
    except (OSError, ValueError):
        return None

def _state_path(aid):
    return os.path.join(ADDON_DIR, aid, ".state.json")

def _state_load(aid):
    try:
        with open(_state_path(aid), encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}

def _state_save(aid, st):
    p = _state_path(aid)
    tmp = "%s.tmp.%d" % (p, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)

def _installed():
    out = []
    try:
        ids = sorted(os.listdir(ADDON_DIR))
    except OSError:
        ids = []
    for i in ids:
        if not _ID_RE.match(i):
            continue
        m = _manifest(os.path.join(ADDON_DIR, i))
        if not m:
            continue
        st = _state_load(i)
        out.append({"id": m["id"], "manifest": m, "enabled": bool(st.get("enabled", True))})
    return out

def _available():
    out = []
    try:
        ids = sorted(os.listdir(REPO_ADDON_DIR))
    except OSError:
        ids = []
    for i in ids:
        if not _ID_RE.match(i):
            continue
        m = _manifest(os.path.join(REPO_ADDON_DIR, i))
        if m:
            out.append({"id": m["id"], "manifest": m, "source": "repo"})
    return out

def catalog():
    with _LOCK:
        inst = _installed()
        have = {a["id"] for a in inst}
        return {"ok": True, "installed": inst,
                "available": [a for a in _available() if a["id"] not in have]}

def install(aid):
    aid = str(aid or "")
    if not _ID_RE.match(aid):
        return {"ok": False, "error": "ungültige Add-on-Kennung"}
    with _LOCK:
        src = os.path.join(REPO_ADDON_DIR, aid)
        if not _manifest(src):
            return {"ok": False, "error": "Add-on nicht im Katalog"}
        dst = os.path.join(ADDON_DIR, aid)
        os.makedirs(ADDON_DIR, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        _state_save(aid, {"enabled": True})
        err = _load_backend(aid)
        _log("widget-addon installiert: %s%s" % (aid, (" (Backend-Fehler: %s)" % err) if err else ""))
        return {"ok": True, "id": aid, "backend_error": err}

def uninstall(aid):
    aid = str(aid or "")
    if not _ID_RE.match(aid):
        return {"ok": False, "error": "ungültige Add-on-Kennung"}
    with _LOCK:
        _MODULES.pop(aid, None)
        dst = os.path.join(ADDON_DIR, aid)
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        _log("widget-addon deinstalliert: %s" % aid)
        return {"ok": True, "id": aid}

def set_enabled(aid, on):
    aid = str(aid or "")
    if not _ID_RE.match(aid):
        return {"ok": False, "error": "ungültige Add-on-Kennung"}
    with _LOCK:
        if not _manifest(os.path.join(ADDON_DIR, aid)):
            return {"ok": False, "error": "nicht installiert"}
        st = _state_load(aid)
        st["enabled"] = bool(on)
        _state_save(aid, st)
        if on and aid not in _MODULES:
            _load_backend(aid)
        if not on:
            _MODULES.pop(aid, None)
        return {"ok": True, "id": aid, "enabled": bool(on)}

def config_get(aid):
    aid = str(aid or "")
    if not _ID_RE.match(aid):
        return {"ok": False, "error": "ungültige Add-on-Kennung"}
    try:
        with open(os.path.join(ADDON_DIR, aid, "config.json"), encoding="utf-8") as f:
            return {"ok": True, "id": aid, "config": json.load(f) or {}}
    except (OSError, ValueError):
        return {"ok": True, "id": aid, "config": {}}

def config_set(aid, cfg):
    aid = str(aid or "")
    if not _ID_RE.match(aid):
        return {"ok": False, "error": "ungültige Add-on-Kennung"}
    if not isinstance(cfg, dict):
        return {"ok": False, "error": "config muss ein Objekt sein"}
    with _LOCK:
        d = os.path.join(ADDON_DIR, aid)
        if not _manifest(d):
            return {"ok": False, "error": "nicht installiert"}
        p = os.path.join(d, "config.json")
        tmp = "%s.tmp.%d" % (p, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        return {"ok": True, "id": aid}

def widget_js_path(aid):

    aid = str(aid or "")
    if not _ID_RE.match(aid):
        return None
    d = os.path.join(ADDON_DIR, aid)
    m = _manifest(d)
    if not m or not _state_load(aid).get("enabled", True):
        return None
    p = os.path.join(d, str(m.get("widget") or "widget.js"))
    return p if os.path.isfile(p) else None

def _load_backend(aid):

    d = os.path.join(ADDON_DIR, aid)
    m = _manifest(d)
    if not m or not m.get("backend"):
        return None
    p = os.path.join(d, str(m["backend"]))
    if not os.path.isfile(p):
        return "Backend-Datei fehlt"
    try:
        spec = importlib.util.spec_from_file_location("widget_addon_%s" % aid.replace("-", "_"), p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "configure"):
            try:
                mod.configure(**_CTX)
            except Exception:
                pass
        _MODULES[aid] = mod
        return None
    except Exception as e:
        return str(e)[:300]

def addons_boot():

    try:
        for a in _installed():
            if a.get("enabled"):
                err = _load_backend(a["id"])
                if err:
                    _log("widget-addon %s: Backend nicht geladen: %s" % (a["id"], err))
    except Exception:
        pass

def dispatch(aid, verb, method, body, query, principal, is_admin):

    aid = str(aid or "")
    if not _ID_RE.match(aid):
        return {"ok": False, "error": "ungültige Add-on-Kennung"}
    mod = _MODULES.get(aid)
    if mod is None:
        return {"ok": False, "error": "Add-on nicht installiert/aktiv (oder ohne Backend)"}
    if not hasattr(mod, "handle"):
        return {"ok": False, "error": "Add-on-Backend ohne handle()"}
    ctx = dict(_CTX)
    ctx.update({"principal": principal, "is_admin": bool(is_admin),
                "config": (config_get(aid).get("config") or {})})
    try:
        r = mod.handle(str(verb or ""), str(method or "GET"), body or {}, query or {}, ctx)
        return r if isinstance(r, dict) else {"ok": False, "error": "Add-on-Antwort kein Objekt"}
    except Exception as e:
        return {"ok": False, "error": ("%s: %s" % (type(e).__name__, e))[:300]}

def _log(msg):
    f = _CTX.get("prov_log")
    if callable(f):
        try:
            f("widgets", msg)
            return
        except Exception:
            pass
    try:
        print("[widget-addons] %s" % msg, flush=True)
    except Exception:
        pass
