#!/usr/bin/env python3

import collections
import glob
import json
import os
import shutil
import threading
import time

HOME = os.path.expanduser("~")
DATA_DIR = os.path.join(HOME, ".local", "share", "brainbox-portal")
CFG_PATH = os.path.join(DATA_DIR, "hpc_safeguard.json")
DEFAULT_PRINCIPAL = "owner"

SESSION_TITLE = "\U0001F6E1 HPC Safeguard"
NETNS_DIR = "/run/netns"

def _site_get(key, default=None):

    if key in os.environ:
        return os.environ[key]
    try:
        import _sitedev
        from pnlib import site as _pnsite
        return _pnsite.get(key, default)
    except Exception:
        return default

HPC_VPN_ID = (str(os.environ.get("PN_HPC_VPN_ID")
                  or os.environ.get("HPC_VPN_ID")
                  or _site_get("HPC_VPN_ID") or "").strip()
              or "hpc")
NETNS_PREFIX = "pnv-%s-" % HPC_VPN_ID
TOOL_NAME = "front1_wache.py"
TOOL_SUBDIR = "hpc-safeguard"
EVENTS_NAME = "hpc-safeguard-events.jsonl"
SCAN_MAX_BYTES = 1024 * 1024
MAIL_MAX_EVENTS = 50
RECENT_MAX = 20

def _envf(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)

TICK_S = _envf("PN_HPC_TICK_S", 60)

_session_store = None
_chan_ctx = None
_sessprov_set = None
_sessprov_get = None
_sess_policy_store = None
_portal_base_url = None
_voice_agent_token = None
_cockpit_policy_enf = None
_prov_log = None
_traceback_log = None
mailjet_send = None

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

def _dep(name, mod, attr=None):

    v = globals().get(name)
    if v is not None:
        return v
    if not mod:
        return None
    try:
        m = __import__(mod)
        return getattr(m, attr or name, None)
    except Exception:
        return None

def _tb(tag):
    f = globals().get("_traceback_log") or _dep("_traceback_log", "portal_metasessions")
    if callable(f):
        try:
            f(tag)
            return
        except Exception:
            pass
    try:
        import sys
        import traceback
        sys.stderr.write("[pn_hpc_safeguard] %s:\n%s" % (tag, traceback.format_exc()))
    except Exception:
        pass

def _plog(kind, principal, text, meta=None):
    f = globals().get("_prov_log")
    if callable(f):
        try:
            f(kind, principal, text, meta or {})
        except Exception:
            pass

_CFG_LOCK = threading.Lock()

def _cfg_defaults():
    return {"enabled": False, "interval_min": 12, "owner": DEFAULT_PRINCIPAL,
            "email": None, "session_sid": None,
            "offsets": {}, "baselined": False, "last_wake": 0.0}

def _cfg_load():
    cfg = _cfg_defaults()
    try:
        with open(CFG_PATH) as f:
            d = json.load(f)
        if isinstance(d, dict):
            cfg.update(d)
    except Exception:
        pass
    return cfg

def _cfg_update(fn):

    with _CFG_LOCK:
        cfg = _cfg_load()
        try:
            fn(cfg)
        except Exception:
            _tb("hpc cfg fn")
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = "%s.tmp.%d" % (CFG_PATH, os.getpid())
            with open(tmp, "w") as f:
                json.dump(cfg, f, indent=2)
            os.replace(tmp, CFG_PATH)
        except Exception:
            _tb("hpc cfg save")
        return cfg

_LOCK = threading.Lock()
_STARTED = False
_CTX = None
_LAST = {"ts": None, "tunnel": None, "action": None, "last_mail": None}
_RECENT = collections.deque(maxlen=RECENT_MAX)

def _tunnel_active():
    try:
        return any(n.startswith(NETNS_PREFIX) for n in os.listdir(NETNS_DIR))
    except Exception:
        return False

def _sessions_base(owner):

    b = os.environ.get("PN_HPC_EVENTS_BASE")
    if b:
        return b
    try:
        import pn_mediashare as _ms
        return os.path.join(_ms.SHARES_ROOT, "users", str(owner), "sessions")
    except Exception:
        return os.path.join("/data/shares/users", str(owner), "sessions")

def _tool_source(owner):

    src = os.environ.get("PN_HPC_WACHE_SRC")
    if src:
        return src if os.path.isfile(src) else None
    cands = glob.glob(os.path.join(_sessions_base(owner), "*", "reprofleet", "bin", TOOL_NAME))
    cands = [c for c in cands if os.path.isfile(c)]
    if not cands:
        return None
    return max(cands, key=lambda p: os.path.getmtime(p))

def _ensure_session(cfg):

    store_f = _dep("_session_store", "portal_jobs_persist")
    if not callable(store_f):
        raise RuntimeError("session_store nicht verfuegbar")
    owner = str(cfg.get("owner") or DEFAULT_PRINCIPAL)
    store = store_f(owner, "cockpit")
    sid = cfg.get("session_sid")
    if sid:
        try:
            if store.get(sid) is not None:
                return owner, sid, False
        except Exception:
            pass

        _plog("hpc.safeguard.session.gone", owner,
              "gemerkte Safeguard-Session existiert nicht mehr — lege neu an", {"old_sid": sid})
    rec = store.create(SESSION_TITLE)
    sid = rec["id"]
    sp = _dep("_sessprov_set", "portal_session_svc")
    if callable(sp):
        sp(owner, sid, {"model": "sonnet", "effort": "medium", "autonomy": 4,
                        "preset": "voll", "role": "hpc-safeguard", "title": SESSION_TITLE})
    try:
        import pn_session_policy as _pol
        stf = _dep("_sess_policy_store", "portal_session_svc")
        st = stf() if callable(stf) else None
        if st is not None:
            st.set(owner, "cockpit", sid, _pol.validate(_pol.new_policy("voll")))
    except Exception:
        _tb("hpc policy")

    def _u(c):
        c["session_sid"] = sid
    _cfg_update(_u)
    cfg["session_sid"] = sid
    _plog("hpc.safeguard.session", owner, "Safeguard-Session angelegt", {"sid": sid})
    return owner, sid, True

def _stage_tool(owner, sid):

    rec = None
    try:
        import portal_routes_session as _prs
        rec = _prs._share_pub_granted(sid, SESSION_TITLE, owner)
    except Exception:
        rec = None
    if not (isinstance(rec, dict) and rec.get("path")):
        try:
            import pn_mediashare as _ms
            rec = _ms.ShareManager().ensure_share(sid, title=SESSION_TITLE, principal=owner)
        except Exception:
            _tb("hpc share")
            return None
    path = (rec or {}).get("path")
    if not path:
        return None
    try:
        dstdir = os.path.join(path, TOOL_SUBDIR)
        os.makedirs(dstdir, exist_ok=True)
        dst = os.path.join(dstdir, TOOL_NAME)
        src = _tool_source(owner)
        if src and (not os.path.isfile(dst) or os.path.getmtime(src) > os.path.getmtime(dst)):
            shutil.copy2(src, dst)
            try:
                os.chmod(dst, 0o775)
            except OSError:
                pass
    except Exception:
        _tb("hpc stage tool")
    return path

def _reg_touch(owner, sid):

    try:
        import portal_voice_core as _vc
        reg = _vc._sesscell_reg()
        if not reg:
            return
        if reg.get(owner, sid) is None:
            reg.provision(owner, sid)
        else:
            reg.attach(owner, sid)
    except Exception:
        pass

def _cell_ensure(owner, sid):

    try:
        import pn_cell_session as _cs
        mgr = _cs.get_manager()
    except Exception as e:
        return None, "Zellen-Manager nicht verfuegbar: %s" % e
    if mgr is None:
        return None, "Zellen-Manager nicht verfuegbar"
    try:
        c = mgr.get(owner, sid)
        if c is not None and c.alive():
            return c, None
    except Exception:
        pass
    try:
        mgr.freeze(owner, sid, False)
    except Exception:
        pass
    pb = _dep("_portal_base_url", "portal_voice_core")
    tok = _dep("_voice_agent_token", "portal_voice_core")
    enf = _dep("_cockpit_policy_enf", "portal_voice_core")
    try:
        cell = mgr.ensure(owner, sid,
                          portal_url=(pb() if callable(pb) else None),
                          portal_token=(tok(owner) if callable(tok) else None),
                          policy=(enf(owner, sid) if callable(enf) else None))
    except Exception as e:
        return None, "Zellen-Boot fehlgeschlagen: %s" % e
    if cell is None or not cell.alive():
        reason = None
        try:
            reason = mgr.boot_reason(owner, sid)
        except Exception:
            pass
        return None, reason or "Die Safeguard-Zelle ist nicht hochgekommen."
    _reg_touch(owner, sid)
    return cell, None

def _cell_pause(owner, sid):

    try:
        import pn_cell_session as _cs
        mgr = _cs.get_manager()
        if mgr is None:
            return False
        c = mgr.get(owner, sid)
        if c is None or not c.alive():
            return False
        mgr.stop(owner, sid, erase=False)
        return True
    except Exception:
        _tb("hpc pause")
        return False

WAKE_TEXT = (
    "HPC-Wache (automatischer Weckruf): Der HPC-Tunnel steht. Fuehre jetzt dein "
    "Wach-Werkzeug aus: python3 ~/austausch/" + TOOL_SUBDIR + "/" + TOOL_NAME + " --apply — es "
    "prueft die Frontend-Knoten und beendet regelwidrige Prozesse. Protokolliere danach JEDES "
    "Kill-Event als EINE JSON-Zeile (Felder z.B. ts, host, pid, user, cmd, reason) ANGEHAENGT an "
    "~/austausch/" + EVENTS_NAME + " (niemals ueberschreiben; Datei bei Bedarf anlegen). Wenn "
    "nichts zu beenden war, ist kein Eintrag noetig. Antworte kurz (1-3 Saetze Ergebnis) und "
    "warte dann auf den naechsten Weckruf.")

def _wake(owner, sid):

    cc = _dep("_chan_ctx", "portal_jobs_persist")
    if not callable(cc):
        return False, "chan ctx nicht verfuegbar"
    try:
        import portal_channels as _pc
        payload, code = _pc.session_say(cc(), owner,
                                        {"sid": sid, "text": WAKE_TEXT, "origin": "hpc-safeguard"})
        if code == 200:
            return True, None
        return False, str((payload or {}).get("error") or ("http %s" % code))
    except Exception as e:
        _tb("hpc wake")
        return False, str(e)

def _scan_events(cfg, owner):

    base = _sessions_base(owner)
    paths = sorted(set(glob.glob(os.path.join(base, "*", EVENTS_NAME)) +
                       glob.glob(os.path.join(base, "*", "reprofleet", EVENTS_NAME))))
    offsets = dict(cfg.get("offsets") or {})
    first = not bool(cfg.get("baselined"))
    new = []
    changed = False
    for p in paths:
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        if first and p not in offsets:
            offsets[p] = size
            changed = True
            continue
        off = int(offsets.get(p, 0) or 0)
        if size < off:
            off = 0
        if size <= off:
            if offsets.get(p) != off:
                offsets[p] = off
                changed = True
            continue
        try:
            with open(p, "rb") as f:
                f.seek(off)
                data = f.read(SCAN_MAX_BYTES)
        except OSError:
            continue
        nl = data.rfind(b"\n")
        if nl < 0:
            continue
        chunk = data[:nl + 1]
        offsets[p] = off + len(chunk)
        changed = True
        for ln in chunk.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                ev = json.loads(ln.decode("utf-8", "replace"))
                if not isinstance(ev, dict):
                    ev = {"raw": str(ev)[:500]}
            except Exception:
                ev = {"raw": ln.decode("utf-8", "replace")[:500]}
            new.append({"source": p, "event": ev, "seen": time.time()})
    for p in list(offsets):
        if p not in paths and not os.path.exists(p):
            offsets.pop(p)
            changed = True
    if changed or first:
        def _u(c):
            c["offsets"] = offsets
            c["baselined"] = True
        _cfg_update(_u)
        cfg["offsets"] = offsets
        cfg["baselined"] = True
    return new

def _mail_events(cfg, events):

    to = str(cfg.get("email") or "").strip()
    if not to or not events:
        return
    ms = _dep("mailjet_send", "portal_email_portioneer")
    if not callable(ms):
        _LAST["last_mail"] = {"ok": False, "detail": "mailjet_send nicht verfuegbar",
                              "ts": time.time(), "events": len(events)}
        return
    n = len(events)
    lines = []
    for e in events[:MAIL_MAX_EVENTS]:
        try:
            txt = json.dumps(e.get("event"), ensure_ascii=False)
        except Exception:
            txt = str(e.get("event"))
        lines.append("- Quelle: %s\n  %s" % (e.get("source"), txt[:600]))
    if n > MAIL_MAX_EVENTS:
        lines.append("... und %d weitere Ereignisse." % (n - MAIL_MAX_EVENTS))
    noun = "1 neues Kill-Event" if n == 1 else ("%d neue Kill-Events" % n)
    subject = "Brainbox HPC-Wache: " + noun
    text = ("Die HPC-Wache hat %s in den Session-Austauschordnern gefunden:\n\n%s\n\n"
            "Quelle: %s (Automat pn_hpc_safeguard auf der Brainbox)."
            % (noun, "\n".join(lines), EVENTS_NAME))
    try:
        ok, detail = ms(to, subject, text)
    except Exception as e:
        ok, detail = False, str(e)
    _LAST["last_mail"] = {"ok": bool(ok), "detail": str(detail)[:300],
                          "ts": time.time(), "events": n}
    _plog("hpc.safeguard.mail", cfg.get("owner") or DEFAULT_PRINCIPAL,
          "%s gemailt (%s)" % (noun, "ok" if ok else "FEHLER"),
          {"detail": str(detail)[:200], "to_set": True})

def tick():
    cfg = _cfg_load()
    now = time.time()
    tun = _tunnel_active()
    _LAST["ts"] = now
    _LAST["tunnel"] = tun
    if not cfg.get("enabled"):
        _LAST["action"] = "aus (Owner-Opt-in fehlt)"
        return
    owner = str(cfg.get("owner") or DEFAULT_PRINCIPAL)
    try:
        evs = _scan_events(cfg, owner)
        if evs:
            for e in evs:
                _RECENT.append(e)
            _mail_events(cfg, evs)
    except Exception:
        _tb("hpc scan")
    if tun:
        try:
            owner, sid, created = _ensure_session(cfg)
        except Exception as e:
            _LAST["action"] = "Session-Anlage fehlgeschlagen: %s" % e
            _tb("hpc ensure session")
            return
        _stage_tool(owner, sid)
        cell, err = _cell_ensure(owner, sid)
        if cell is None:
            _LAST["action"] = "Zelle nicht verfuegbar: %s" % (err or "?")
            return
        try:
            iv = max(1, min(1440, int(cfg.get("interval_min") or 12)))
        except (TypeError, ValueError):
            iv = 12
        due = created or (now - float(cfg.get("last_wake") or 0)) >= iv * 60
        if due:
            ok, werr = _wake(owner, sid)
            if ok:
                def _u(c):
                    c["last_wake"] = now
                _cfg_update(_u)
                _LAST["action"] = "Weckruf zugestellt"
                _plog("hpc.safeguard.wake", owner, "Weckruf an Safeguard-Session", {"sid": sid})
            else:
                _LAST["action"] = "Weckruf fehlgeschlagen: %s" % (werr or "?")
        else:
            _LAST["action"] = ("Zelle laeuft; naechster Weckruf in %ds"
                               % max(0, int(iv * 60 - (now - float(cfg.get("last_wake") or 0)))))
    else:
        sid = cfg.get("session_sid")
        if sid and _cell_pause(owner, sid):
            _LAST["action"] = "Tunnel weg — Zelle gestoppt (RAM frei, Delta bleibt)"
            _plog("hpc.safeguard.pause", owner, "Tunnel weg — Safeguard-Zelle gestoppt", {"sid": sid})
        else:
            _LAST["action"] = "kein Tunnel — nichts zu tun"

def status():
    cfg = _cfg_load()
    try:
        iv = int(cfg.get("interval_min") or 12)
    except (TypeError, ValueError):
        iv = 12
    return {"ok": True,
            "enabled": bool(cfg.get("enabled")),
            "interval_min": iv,
            "owner": cfg.get("owner") or DEFAULT_PRINCIPAL,
            "email": cfg.get("email"),
            "session_sid": cfg.get("session_sid"),
            "tunnel_now": _tunnel_active(),
            "last_check": _LAST.get("ts"),
            "last_action": _LAST.get("action"),
            "last_wake": (cfg.get("last_wake") or None),
            "last_mail": _LAST.get("last_mail"),
            "ticker_running": bool(_STARTED),
            "recent_events": list(_RECENT)}

class HpcSafeguardRoutes:
    def _hpc_json(self, obj, code=200):
        return self.send_html(json.dumps(obj, ensure_ascii=False), code,
                              [("Content-Type", "application/json")])

    def _api_hpc_safeguard_get(self):

        if not self._is_admin():
            return self._hpc_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        return self._hpc_json(status())

    def _api_hpc_safeguard_post(self, raw):

        if not self._is_admin():
            return self._hpc_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = None
        if not isinstance(body, dict):
            return self._hpc_json({"ok": False,
                                   "error": "Anfrage unlesbar — JSON-Objekt erwartet."}, 400)
        patch = {}
        if "enabled" in body:
            patch["enabled"] = bool(body.get("enabled"))
        if "interval_min" in body:
            try:
                iv = int(body.get("interval_min"))
            except (TypeError, ValueError):
                return self._hpc_json({"ok": False,
                                       "error": "interval_min muss eine Zahl sein."}, 400)
            if not 1 <= iv <= 1440:
                return self._hpc_json({"ok": False,
                                       "error": "interval_min muss zwischen 1 und 1440 liegen."}, 400)
            patch["interval_min"] = iv
        if "email" in body:
            em = body.get("email")
            if em in (None, ""):
                patch["email"] = None
            elif isinstance(em, str) and "@" in em and " " not in em.strip() and len(em) <= 254:
                patch["email"] = em.strip()
            else:
                return self._hpc_json({"ok": False,
                                       "error": "email ist keine gueltige Adresse."}, 400)
        if not patch:
            return self._hpc_json({"ok": False,
                                   "error": "nichts zu aendern (enabled/interval_min/email)."}, 400)

        def _u(c):
            c.update(patch)
        _cfg_update(_u)
        _plog("hpc.safeguard.config", self._principal(),
              ", ".join("%s=%s" % (k, patch[k]) for k in sorted(patch)), dict(patch))
        return self._hpc_json(status())

def start(ctx=None):

    global _STARTED, _CTX
    v = str(os.environ.get("PN_HPC_SAFEGUARD", "1")).strip().lower()
    if v in ("0", "false", "no", "off", ""):
        return None
    with _LOCK:
        if _STARTED:
            return None
        _STARTED = True
        _CTX = ctx or {}

    def _loop():
        time.sleep(12)
        while True:
            try:
                tick()
            except Exception:
                _tb("hpc tick")
            time.sleep(TICK_S)

    t = threading.Thread(target=_loop, name="pn-hpc-safeguard", daemon=True)
    t.start()
    return t

if __name__ == "__main__":

    print(json.dumps(status(), indent=2, ensure_ascii=False))
