
import os, json, signal, subprocess, threading, time
import re, shlex

HOME = os.path.expanduser("~")
DATA_DIR = os.path.join(HOME, ".local", "share", "brainbox-portal")

try:
    import pn_session_policy as _policy
except Exception:
    _policy = None
try:
    import pn_session_cells as _sesscells
except Exception:
    _sesscells = None

_DEVICE_REG = None
_cockpit_policy_enf = None
_inject = None
_last_claude_session = None
_portal_base_url = None
_session_store = None
_traceback_log = None
_voice_agent_token = None

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

_SESSPROV_DIR = os.path.join(DATA_DIR, "session-prov")

_MODELS_SEED = [
    {"id": "opus",   "label": "Opus 4.8", "hint": "staerkste"},
    {"id": "sonnet", "label": "Sonnet 5", "hint": "schnell & stark"},
    {"id": "haiku",  "label": "Haiku 4.5", "hint": "leicht & guenstig"},
    {"id": "fable",  "label": "Fable 5", "hint": "kreativ"},
]
_models_cache = {"mtime": 0.0, "list": None}

import portal_zustand as _zst
_zst.register("portal_session_svc._models_cache", "cache", __name__, ref=_models_cache,
              beschreibung="models.json mtime-gecacht (Session-Modellliste)",
              neustart="verfaellt", schreiber="Modelllisten-Leser bei mtime-Wechsel")

_CODEX_MODELS_CACHE = {"mtime": 0.0, "path": None, "list": None}
_zst.register("portal_session_svc._CODEX_MODELS_CACHE", "cache", __name__, ref=_CODEX_MODELS_CACHE,
              beschreibung="Codex-Modellliste, mtime/pfad-gecacht",
              neustart="verfaellt", schreiber="_codex_models()")

def _codex_home_candidates():

    import glob as _glob
    cands = []
    for cfg in _glob.glob(os.path.expanduser("~/.config/*/llmpool.json")):
        try:
            with open(cfg, encoding="utf-8") as f:
                d = json.load(f)
            accts = d.get("accounts") if isinstance(d, dict) else (d if isinstance(d, list) else [])
            for a in (accts or []):
                if str(a.get("provider") or "").lower() == "codex" and a.get("home"):
                    cands.append(os.path.join(os.path.expanduser(a["home"]), ".codex"))
        except Exception:
            continue
    cands.append(os.path.expanduser("~/.llmpool/codex/.codex"))
    cands.append(os.path.expanduser("~/.codex"))
    seen = set(); out = []
    for c in cands:
        if c not in seen:
            seen.add(c); out.append(c)
    return out

def _codex_models():

    for base in _codex_home_candidates():
        p = os.path.join(base, "models_cache.json")
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        c = _CODEX_MODELS_CACHE
        if c["path"] == p and c["mtime"] == mt and c["list"] is not None:
            return c["list"]
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            out = []
            for m in (d.get("models") or []):
                if not isinstance(m, dict):
                    continue
                slug = m.get("slug") or m.get("id")
                if not slug or str(m.get("visibility") or "").lower() == "hide":
                    continue
                item = {"id": str(slug), "label": str(m.get("display_name") or slug)}
                desc = m.get("description")
                if desc:
                    item["hint"] = str(desc)[:80]
                out.append(item)
            c.update(mtime=mt, path=p, list=out)
            return out
        except Exception:
            continue
    return []

_GEMINI_MODELS_CACHE = {"ts": 0.0, "list": None}
_zst.register("portal_session_svc._GEMINI_MODELS_CACHE", "cache", __name__, ref=_GEMINI_MODELS_CACHE, ttl_s=300.0,
              beschreibung="Gemini-Modellliste, TTL-gecacht",
              neustart="verfaellt", schreiber="_gemini_models()")

def _gemini_models(ttl=300):

    import time as _t
    c = _GEMINI_MODELS_CACHE
    if c["list"] is not None and (_t.time() - c["ts"]) < ttl:
        return c["list"]
    key = ""
    try:
        import portal_insights as _pi
        key = _pi._gemini_key()
    except Exception:
        key = ""
    out = []
    if key:
        try:
            import urllib.request
            import urllib.parse
            url = ("https://generativelanguage.googleapis.com/v1beta/models?key="
                   + urllib.parse.quote(key))
            with urllib.request.urlopen(url, timeout=8) as r:
                d = json.loads(r.read().decode("utf-8", "replace") or "{}")
            for m in (d.get("models") or []):
                if not isinstance(m, dict):
                    continue
                if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                    continue
                slug = str(m.get("name") or "").split("/")[-1]
                if slug.startswith("gemini"):
                    out.append({"id": slug, "label": str(m.get("displayName") or slug)})
        except Exception:
            out = []
    if out:
        c.update(ts=_t.time(), list=out)
    return out

def sess_models(runtime=None):
    if runtime == "codex":
        return _codex_models()
    if runtime == "gemini":
        return _gemini_models()
    if runtime == "ollama":

        try:
            import llm_endpoints as _ep
            ent = _ep.get("ollama") or next((e for e in (_ep.entries() or [])
                                             if (e.get("discovery") == "ollama")), None)
            if ent:
                return [{"id": m.get("id"), "label": m.get("label") or m.get("id"), "hint": "lokal"}
                        for m in (_ep.models(ent) or []) if m.get("id")]
        except Exception:
            pass
        return []
    path = os.path.join(DATA_DIR, "models.json")
    try:
        mt = os.path.getmtime(path)
    except OSError:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(_MODELS_SEED, f, indent=1, ensure_ascii=False)
            mt = os.path.getmtime(path)
        except OSError:
            return list(_MODELS_SEED)
    if _models_cache["list"] is None or mt != _models_cache["mtime"]:
        try:
            with open(path, encoding="utf-8") as f:
                lst = json.load(f)
            if not (isinstance(lst, list) and lst
                    and all(isinstance(m, dict) and m.get("id") for m in lst)):
                raise ValueError("models.json muss eine nicht-leere Liste von {id,label,hint} sein")
            _models_cache.update(mtime=mt, list=lst)
        except Exception:

            return _models_cache["list"] or list(_MODELS_SEED)
    return _models_cache["list"]

_SESS_MODELS = None

def init_models():

    global _SESS_MODELS
    if _SESS_MODELS is None:
        _SESS_MODELS = sess_models()
    return _SESS_MODELS
_SESS_EFFORTS = [
    {"id": "medium", "label": "Standard"},
    {"id": "high",   "label": "Hoch"},
    {"id": "xhigh",  "label": "Sehr hoch"},
    {"id": "max",    "label": "Maximal"},
]

_SESS_RUNTIMES = [
    {"id": "claude-tmux", "label": "Claude (Standard)", "hint": "normaler Agent im Terminal"},
    {"id": "biomni",      "label": "Biomni",            "hint": "biomedizinischer Agent (isolierte Zelle + Data-Lake)"},
    {"id": "codex",       "label": "Codex (OpenAI)",    "hint": "OpenAI-Agent im Terminal (eigenes Konto, Admin->LLM verbinden)"},
    {"id": "gemini",      "label": "Gemini (Google)",   "hint": "Google-Agent im Terminal (eigenes Konto, Admin->LLM verbinden)"},

    {"id": "ollama",      "label": "Ollama (lokal)",    "hint": "OpenCode-Agent gegen den Box-Ollama (Admin->LLM-Pool: Endpoint anlegen)"},
]
_SESS_RUNTIME_IDS = {r["id"] for r in _SESS_RUNTIMES}

_RUNTIME_STAMP = {"claude-tmux": "Claude", "claude": "Claude", "codex": "Codex",
                  "gemini": "Gemini", "ollama": "Ollama", "biomni": "Biomni", "voice-repl": "Claude"}
def sess_model_label(prov):
    prov = prov or {}
    rt = (prov.get("runtime") or "claude-tmux")
    m = str(prov.get("model") or "").strip()
    base = _RUNTIME_STAMP.get(rt, rt or "Claude")
    if not m:
        return base
    if base.lower() in m.lower():
        return m
    return "%s · %s" % (base, m)

def _sessprov_path(uid, sid):

    d = os.path.join(_SESSPROV_DIR, re.sub(r"[^A-Za-z0-9_.-]", "_", str(uid)))
    return os.path.join(d, re.sub(r"[^a-z0-9]", "", str(sid))[:32] + ".json")

_sessprov_cache = {}
_sessprov_cache_lock = threading.Lock()
_zst.register("portal_session_svc._sessprov_cache", "cache", __name__, ref=_sessprov_cache,
              beschreibung="Session-Provision-Rohtext je (uid, sid), invalidiert per (mtime_ns, size)-Signatur",
              neustart="verfaellt", schreiber="Provision-Leser bei Signatur-Wechsel")

def _sessprov_get(uid, sid):
    p = _sessprov_path(uid, sid)
    key = (str(uid), str(sid))
    try:
        st = os.stat(p)
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        with _sessprov_cache_lock:
            _sessprov_cache.pop(key, None)
        return {}
    with _sessprov_cache_lock:
        hit = _sessprov_cache.get(key)
    if hit is not None and hit[0] == sig:
        try:
            return json.loads(hit[1])
        except Exception:
            return {}
    try:
        with open(p) as f:
            text = f.read()
        d = json.loads(text)
    except Exception:
        return {}
    with _sessprov_cache_lock:
        _sessprov_cache[key] = (sig, text)
    return d

def _sessprov_set(uid, sid, patch):
    cur = _sessprov_get(uid, sid)
    cur.update(patch or {})
    try:
        p = _sessprov_path(uid, sid); tmp = p + ".tmp"
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(cur, f)
        os.replace(tmp, p)
    except Exception:
        pass
    return cur

def _sessprov_del(uid, sid):

    try:
        p = _sessprov_path(uid, sid)
        for f in (p, (p[:-5] + "-brief.md") if p.endswith(".json") else None):
            if f and os.path.exists(f):
                os.remove(f)
    except Exception:
        pass
    try:
        st = _sess_policy_store()
        if st and hasattr(st, "delete"):
            st.delete(uid, "cockpit", sid)
    except Exception:
        pass

_sess_policy_store_memo = [None]
_zst.register("portal_session_svc._sess_policy_store_memo", "singleton", __name__, ref=_sess_policy_store_memo,
              beschreibung="einmal gebauter PolicyStore (Ctor macht makedirs)",
              neustart="rekonstruiert", schreiber="_sess_policy_store() bei Erstbedarf")

def _sess_policy_store():
    st = _sess_policy_store_memo[0]
    if st is not None:
        return st
    try:
        st = _policy.PolicyStore(os.path.join(DATA_DIR, "session-policies")) if _policy else None
    except Exception:
        return None
    if st is not None:
        _sess_policy_store_memo[0] = st
    return st

_sess_policy_cache = {}
_sess_policy_cache_lock = threading.Lock()
_zst.register("portal_session_svc._sess_policy_cache", "cache", __name__, ref=_sess_policy_cache,
              beschreibung="Session-Policy-JSON je (uid, sid), invalidiert per Datei-Signatur; Rueckgabe je Aufruf frisch (json-Roundtrip, kein geteiltes Objekt)",
              neustart="verfaellt", schreiber="_sess_policy_get() bei Signatur-Wechsel")

def _sess_policy_get(uid, sid):
    st = _sess_policy_store()
    if st is None:
        return {}
    try:
        p = st._p(uid, "cockpit", sid, mkdir=False)
        try:
            s = os.stat(p)
            sig = (s.st_mtime_ns, s.st_size)
        except OSError:
            try:
                sd = os.stat(st._default_p())
                sig = ("default", sd.st_mtime_ns, sd.st_size)
            except OSError:
                sig = ("default", None, None)
    except Exception:
        sig = None
    key = (str(uid), str(sid))
    if sig is not None:
        with _sess_policy_cache_lock:
            hit = _sess_policy_cache.get(key)
        if hit is not None and hit[0] == sig:
            try:
                return json.loads(hit[1])
            except Exception:
                pass
    try:
        pol = st.get(uid, "cockpit", sid)
    except Exception:
        return {}
    if sig is not None:
        try:
            with _sess_policy_cache_lock:
                _sess_policy_cache[key] = (sig, json.dumps(pol))
        except Exception:
            pass
    return pol

HOST_AUTONOMY_NOTE_MIN_LEVEL = 2

def _autonomy_requested_mode(level):

    try:
        lvl = int(level)
    except Exception:
        lvl = 1
    if lvl >= 5:
        return "bypassPermissions"
    if lvl >= 2:
        return "acceptEdits"
    return None

def _autonomy_permission_mode(level):

    return None

def _host_autonomy_note(level):

    return ""

def _cockpit_disallowed(uid, sid):
    tools = []
    try:
        caps = (_sess_policy_get(uid, sid) or {}).get("caps", {})
        if caps.get("websearch") == "deny":
            tools.append("WebSearch")
        if caps.get("webfetch") == "deny":
            tools.append("WebFetch")
    except Exception:
        pass
    return tools

def _cockpit_brief_file(uid, sid, prov):
    try:
        d = os.path.join(_SESSPROV_DIR, re.sub(r"[^A-Za-z0-9_.-]", "_", str(uid)))
        os.makedirs(d, exist_ok=True)
        bp = os.path.join(d, re.sub(r"[^a-z0-9]", "", str(sid))[:32] + "-brief.md")
        try:
            lvl = _sesscells.normalize_level(prov.get("autonomy")) if _sesscells else 2
            short = _sesscells.AUTONOMY_SHORT.get(lvl, str(lvl)) if _sesscells else str(lvl)
            exp = _sesscells.AUTONOMY_EXPERIENCE.get(lvl, "") if _sesscells else ""
        except Exception:
            lvl, short, exp = 2, "Standard", ""

        auton_line = ("AUTONOMIE (Bestätigungsstufe „%s“): %s Diese Stufe regelt NUR, wie viel der "
                      "Nutzer per Handy-Code (2FA) bestätigt, bevor etwas nach außen geht oder "
                      "Unumkehrbares passiert — sie ändert nicht, wie du sonst arbeitest." % (short, exp))
        role = prov.get("role")
        L = []

        try:
            import pn_sprachregelung as _spr
            _s = _spr.brief()
            if _s:
                L += [_s, ""]
        except Exception:
            pass

        caps = (_sess_policy_get(uid, sid) or {}).get("caps", {})
        allow = [k for k in ("websearch", "webfetch", "net_general") if caps.get(k) == "allow"]
        deny = [k for k in ("websearch", "webfetch", "net_general") if caps.get(k) == "deny"]
        if role == "worker":
            L += ["# Arbeits-Session mit GENAU EINER Aufgabe: %s" % (prov.get("meta_title") or prov.get("title") or sid), "",
                  "Du bist eine eigenstaendige Arbeits-Session mit GENAU EINER Aufgabe - sie kommt gleich "
                  "als erste Nachricht. Erledige sie vollstaendig und zielstrebig und schliesse mit einer "
                  "kurzen Ergebnis-Zusammenfassung als letzter Nachricht ab.", "",
                  "AUTONOMIE: Stufe L%d - %s." % (lvl, lab)]
        else:
            L += ["# Arbeits-Session: %s" % (prov.get("title") or sid), "",
                  "Du bist der Agent DIESER Session im Brainarbeit-Portal dieses Nutzers. Arbeite "
                  "zielstrebig und eigenstaendig an den Auftraegen, die dir hier gegeben werden, und "
                  "fasse Ergebnisse knapp zusammen.", "",
                  "AUTONOMIE: Stufe L%d - %s." % (lvl, lab)]
        if allow:
            L.append("RECHTE erlaubt: %s." % ", ".join(allow))
        if deny:
            L.append("RECHTE verweigert: %s - benutze diese Werkzeuge NICHT." % ", ".join(deny))
        if caps.get("orchestrate") == "allow":
            L += ["", "ORCHESTRATOR-RECHT: DU (dieser Agent) darfst selbst eigene, vollwertige Sub-"
                  "Sessions als isolierte microVM-Zellen starten und steuern - der Nutzer klickt dafuer "
                  "nichts. Ueber `portalctl`:",
                  "  portalctl session_spawn '{\"task\":\"<praezise, in sich abgeschlossene Aufgabe>\"}'",
                  "  portalctl session_status",
                  "  portalctl session_transcript '{\"tid\":\"<tid>\"}'   # volles Kind-Transkript inkl. Werkzeug-Aufrufe",
                  "  portalctl session_tell '{\"tid\":\"<tid>\",\"text\":\"<nachricht>\"}'",
                  "Sub-Sessions sehen deinen Chat NICHT - nur ihren Aufgabentext; formuliere vollstaendig. "
                  "Es passen nur wenige Zellen gleichzeitig (RAM-Budget); ist kein Platz, wartet die "
                  "Aufgabe automatisch, sehr tiefe Verschachtelung wird abgelehnt. Du SIEHST alles, was "
                  "deine Kinder tun: session_transcript liefert dir jederzeit ihr volles Transkript, und "
                  "neue Aktivitaet wird dir automatisch als [AUTO-AUFSICHT]-Bericht eingespielt "
                  "(session_watch schaltet das; Vorgabe AN). Pruefe die Arbeit damit laufend, greife per "
                  "session_tell ein und fasse am Ende zusammen."]
        if prov.get("vpn"):
            L.append("VPN: Der Netzverkehr laeuft durch den Account-Tunnel %s." % prov.get("vpn"))

        L.append("")
        L.append("PAUSE/FORTSETZEN: Kommt eine ⏸ SYSTEM-PAUSE-Nachricht, beende den aktuellen Schritt "
                 "sauber, starte nichts Neues und idle, bis ▶ SYSTEM-FORTSETZEN kommt.")
        L.append("Unumkehrbare Aktionen (senden, loeschen, bezahlen, veroeffentlichen) laufen ueber "
                 "die Bestaetigungs-Zeremonie: du kuendigst sie an und wartest auf die Freigabe. Je "
                 "nach Bestaetigungsstufe verlangt die Box dafuer zusaetzlich den Handy-Code (2FA) des "
                 "Nutzers — ohne verifizierten zweiten Faktor wird eine solche Aktion NICHT ausgefuehrt.")
        open(bp, "w").write("\n".join(L) + "\n")
        return bp
    except Exception:
        return None

def _biomni_launch_cmd(principal, sid):

    try:
        bridge = os.path.expanduser("~/.local/bin/pn_biomni_bridge.py")
        if not os.path.exists(bridge):
            return None
        return "PN_BIOMNI_UID=%s PN_BIOMNI_SID=%s exec /usr/bin/python3 %s" % (
            shlex.quote(str(principal)), shlex.quote(str(sid)), shlex.quote(bridge))
    except Exception:
        return None

def _cockpit_inner(principal, sid, sess):

    try:
        if not sid:
            return None
        prov = _sessprov_get(principal, sid)
        if not prov:
            return None
        if prov.get("runtime") == "biomni":
            return _biomni_launch_cmd(principal, sid)
        flags = _equip_flags(principal, sid, prov, headless=False)
        rid = _last_claude_session(sess)
        if rid:
            flags = ["--resume", str(rid)] + flags
        netns = ""
        vpn = prov.get("vpn")
        if vpn and _uservpn_allowed(principal, vpn):
            ns = _account_netns_name(principal, vpn)
            if _netns_exists(ns):
                netns = ns
        env = None
        role = prov.get("role")
        try:

            env = {"PORTAL_URL": _portal_base_url(), "PORTAL_TOKEN": _voice_agent_token(principal),
                   "SESSION_SID": sid, "PORTAL_SESSION_SID": sid}
            if role in ("lead", "worker") or prov.get("orchestrator"):
                env["META_ID"] = prov.get("meta_id") or (sid if prov.get("orchestrator") else "")
            if role == "worker":
                env["WORKER_TID"] = prov.get("meta_tid") or ""
        except Exception:
            env = None
        d = os.path.join(DATA_DIR, "session-prov", re.sub(r"[^A-Za-z0-9_.-]", "_", str(principal)))
        os.makedirs(d, exist_ok=True)
        script = os.path.join(d, re.sub(r"[^a-z0-9]", "", str(sid))[:32] + "-launch.sh")

        note = _host_autonomy_note(prov.get("autonomy", 1))
        p = _write_launch_script(script, HOME, flags, netns=netns, task=None, fail_closed=False, env=env,
                                 note=note)
        return ("bash %s" % p) if p else None
    except Exception:
        return None

def _session_pause_notify(uid, sid, paused):

    try:
        tn = _session_store(uid, "cockpit").tmux_name(sid)
        if subprocess.run(["tmux", "has-session", "-t", tn], capture_output=True).returncode != 0:
            return False
        msg = ("⏸ SYSTEM-PAUSE: Beende den aktuellen Schritt sauber und starte NICHTS Neues "
               "(keine neuen Sub-Sessions/Auftraege). Bleibe ruhig, bis eine ▶ SYSTEM-FORTSETZEN-"
               "Nachricht kommt." if paused else
               "▶ SYSTEM-FORTSETZEN: Mach genau da weiter, wo du vor der Pause aufgehoert hast.")
        _inject(tn, msg)
        return True
    except Exception:
        return False

_PORTAL_CG_BASE = None
def _portal_cg_base():

    global _PORTAL_CG_BASE
    if _PORTAL_CG_BASE is not None:
        return _PORTAL_CG_BASE or None
    _PORTAL_CG_BASE = ""
    try:
        with open("/proc/self/cgroup") as f:
            for ln in f:
                parts = ln.strip().split(":", 2)
                if len(parts) == 3 and parts[0] == "0":
                    d = "/sys/fs/cgroup" + parts[2]
                    if os.path.isdir(d) and os.path.exists(os.path.join(d, "cgroup.procs")):
                        _PORTAL_CG_BASE = d
                    break
    except Exception:
        pass
    return _PORTAL_CG_BASE or None

def _session_cg_dir(sid):
    base = _portal_cg_base()
    if not base:
        return None
    return os.path.join(base, "sess-" + re.sub(r"[^a-z0-9]", "", str(sid))[:32])

def _tmux_subtree_pids(tmux_name):

    try:
        r = subprocess.run(["tmux", "list-panes", "-t", tmux_name, "-F", "#{pane_pid}"],
                           capture_output=True, text=True)
        roots = [x for x in (r.stdout or "").split() if x.isdigit()]
    except Exception:
        return []
    seen = set(); stack = list(roots)
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        try:
            ch = subprocess.run(["pgrep", "-P", p], capture_output=True, text=True).stdout.split()
            stack += [c for c in ch if c.isdigit()]
        except Exception:
            pass
    return [p for p in seen if p]

def _cg_migrate(cgdir, pids):

    procs = os.path.join(cgdir, "cgroup.procs")
    moved = 0
    for p in pids:
        try:
            with open(procs, "w") as f:
                f.write(str(p) + "\n")
            moved += 1
        except Exception:
            pass
    return moved

def _kill_tmux_tree(tmux_name):

    for p in _tmux_subtree_pids(tmux_name):
        try:
            os.kill(int(p), signal.SIGKILL)
        except Exception:
            pass
    subprocess.run(["tmux", "kill-session", "-t", tmux_name], capture_output=True)

def _cg_capture_freeze(cgdir, tn, passes=8):

    for _ in range(passes):
        _cg_migrate(cgdir, _tmux_subtree_pids(tn))
        try:
            with open(os.path.join(cgdir, "cgroup.freeze"), "w") as f:
                f.write("1")
        except Exception:
            return False
        try:
            inside = set(open(os.path.join(cgdir, "cgroup.procs")).read().split())
        except Exception:
            inside = set()
        if all(p in inside for p in _tmux_subtree_pids(tn)):
            return True
    return True

def _session_hard_freeze(uid, sid, on):

    try:
        tn = _session_store(uid, "cockpit").tmux_name(sid)
        if not tn or subprocess.run(["tmux", "has-session", "-t", tn], capture_output=True).returncode != 0:
            return False
        cgdir = _session_cg_dir(sid)
        if not cgdir:
            return False
        os.makedirs(cgdir, exist_ok=True)
        if on:
            _cg_capture_freeze(cgdir, tn)
        else:
            with open(os.path.join(cgdir, "cgroup.freeze"), "w") as f:
                f.write("0")
        return True
    except Exception:
        _traceback_log("session hard freeze")
        return False

def _session_hard_kill(uid, sid):

    try:
        tn = _session_store(uid, "cockpit").tmux_name(sid)
    except Exception:
        return False
    if not tn:
        return False
    existed = subprocess.run(["tmux", "has-session", "-t", tn], capture_output=True).returncode == 0
    snapshot = _tmux_subtree_pids(tn) if existed else []
    cgdir = _session_cg_dir(sid)
    if cgdir and existed:
        try:
            os.makedirs(cgdir, exist_ok=True)
            _cg_capture_freeze(cgdir, tn)
            with open(os.path.join(cgdir, "cgroup.kill"), "w") as f:
                f.write("1")
        except Exception:
            pass
    for p in snapshot:
        try:
            os.kill(int(p), signal.SIGKILL)
        except Exception:
            pass
    subprocess.run(["tmux", "kill-session", "-t", tn], capture_output=True)
    if cgdir:
        try:
            for _ in range(20):
                try:
                    with open(os.path.join(cgdir, "cgroup.events")) as f:
                        if "populated 0" in f.read():
                            break
                except Exception:
                    break
                time.sleep(0.05)
            os.rmdir(cgdir)
        except Exception:
            pass
    return existed

def _cell_present(uid, sid):

    try:
        import pn_cell_session as _cs
        if _cs.get_manager().get(uid, sid) is not None:
            return True
        namer = getattr(_cs, "_cell_name", None)
        run_dir = getattr(_cs, "RUN_DIR", None)
        vol_dir = getattr(_cs, "VOL_DIR", None)
        if not (namer and run_dir and vol_dir):
            return None
        name = namer(uid, sid)
        if os.path.isdir(os.path.join(run_dir, name)):
            return True
        if os.path.exists(os.path.join(vol_dir, name + "-delta.img")):
            return True
        return False
    except Exception:
        return None

def _cell_kill_erase(uid, sid):

    try:
        import pn_cell_session as _cs
        if _cell_present(uid, sid) is False:
            return False

        return bool(_cs.get_manager().stop(uid, sid, erase=True, owner_2fa=True))
    except Exception:
        _traceback_log("cell kill erase")
        return False

def _cell_freeze(uid, sid, on):

    try:
        import pn_cell_session as _cs
        return bool(_cs.get_manager().freeze(uid, sid, on))
    except Exception:
        _traceback_log("cell freeze")
        return False

def _cell_power(uid, sid, on):

    try:
        import pn_cell_session as _cs
        mgr = _cs.get_manager()
        if on:
            cell = mgr.ensure(uid, sid, portal_url=_portal_base_url(),
                              portal_token=_voice_agent_token(uid),
                              policy=_cockpit_policy_enf(uid, sid))
            if cell and cell.alive():
                return {"ok": True, "reason": None}

            reason = None
            try:
                reason = mgr.boot_reason(uid, sid)
            except Exception:
                pass
            if not reason:
                den = getattr(cell, "_admit_denied", None) if cell else None
                reason = (den or {}).get("reason") if den else None
            return {"ok": False, "reason": reason or "Die Session-Zelle konnte nicht starten (kein Grund ermittelbar)."}
        c = mgr.get(uid, sid)
        if c is not None:
            try: c.sync()
            except Exception: pass
        stopped = bool(mgr.stop(uid, sid, erase=False))

        return {"ok": stopped, "reason": None if stopped else
                "Die Session-Zelle lief nicht — es war nichts herunterzufahren."}
    except Exception:
        _traceback_log("cell power")
        return {"ok": False, "reason": None}

_DESK_JOBS = {}
_DESK_LOCK = threading.Lock()
_zst.register("portal_session_svc._DESK_JOBS", "snapshot", __name__, ref=_DESK_JOBS,
              beschreibung="Phase/Detail laufender Desktop-Umschaltungen je (uid, sid) fuer die Ladeanzeige; Wahrheit ist der Zellzustand",
              neustart="verfaellt", schreiber="Desktop-Jobs unter _DESK_LOCK")
_DESK_BUSY = ("start", "sichern", "neustart", "arbeitsflaeche")

def _desk_set(uid, sid, phase, detail="", error=None, on=None):
    with _DESK_LOCK:
        j = _DESK_JOBS.get((uid, sid)) or {}
        j.update({"phase": phase, "detail": detail, "ts": time.time(), "error": error})
        if on is not None:
            j["on"] = on
        _DESK_JOBS[(uid, sid)] = j

def _cell_desktop_status(uid, sid):

    with _DESK_LOCK:
        j = dict(_DESK_JOBS.get((uid, sid)) or {})
    active = False; screen = None
    mem_mb = vcpus = work_gb = None
    try:
        import pn_cell_session as _cs
        c = _cs.get_manager().get(uid, sid)
        if c is not None and c.alive():
            pol = c.policy or {}
            desktop = bool(pol.get("desktop"))

            mem_mb = int(pol.get("mem_mb") or getattr(_cs, "MEM_MB", 0)) or None
            if desktop:
                mem_mb = max(mem_mb or 0, _cs.OFFICE_MEM_MB)
                vcpus = int(pol.get("vcpus") or _cs.OFFICE_VCPUS)
            else:
                vcpus = 1
            try:
                wp = getattr(c, "work", None)
                if wp and os.path.exists(wp):
                    work_gb = round(os.path.getsize(wp) / float(1 << 30), 1)
            except OSError:
                pass
        if c is not None and c.alive() and (c.policy or {}).get("desktop"):
            br = getattr(c, "desk_bridge", None)
            bridge_up = br is not None and br.poll() is None
            if not bridge_up:
                try:
                    import pn_cell_desk_bridge as _brg
                    bridge_up = bool(_brg.lebende_bruecke(c.cell))
                except Exception:
                    pass

            registered = False
            if bridge_up:
                try:
                    reg = os.path.join(os.environ.get("PHANTOM_PORTAL_DATA",
                                       os.path.expanduser("~/.local/share/brainbox-portal")), "vmcells.json")
                    registered = c.cell in (json.load(open(reg)) or {})
                except (OSError, ValueError):
                    registered = False
            active = bool(bridge_up and registered)
            screen = c.cell if active else None
    except Exception:
        pass
    j["active"] = active
    j["cell"] = screen
    if mem_mb is not None:
        j["mem_mb"] = mem_mb
    if vcpus is not None:
        j["vcpus"] = vcpus
    if work_gb is not None:
        j["work_gb_provisioned"] = work_gb
    return j

def _cell_desktop(uid, sid, on):

    try:
        import pn_cell_session as _cs
    except Exception:
        return {"ok": False, "reason": "Zellen-Subsystem nicht verfuegbar."}
    mgr = _cs.get_manager()
    with _DESK_LOCK:
        j = _DESK_JOBS.get((uid, sid))
        if j and j.get("phase") in _DESK_BUSY:
            return {"ok": False, "reason": "Ein Desktop-Wechsel laeuft fuer diese Session bereits."}
        _vorher = dict(j) if j else None
        _DESK_JOBS[(uid, sid)] = {"phase": "start", "detail": "Desktop wird geprueft …",
                                  "ts": time.time(), "error": None,
                                  "on": (j or {}).get("on")}

    def _frei():
        with _DESK_LOCK:
            if _vorher is None:
                _DESK_JOBS.pop((uid, sid), None)
            else:
                _DESK_JOBS[(uid, sid)] = _vorher

    st = _cell_desktop_status(uid, sid)
    if on and st.get("active"):
        _frei()
        return {"ok": True, "already": True, "cell": st.get("cell")}
    if not on and not st.get("active"):
        _frei()
        return {"ok": True, "already": True}
    if on:
        if not os.path.exists(_cs.OFFICE_BASE):
            _frei()
            return {"ok": False, "reason": "Das Office-Image fehlt auf dieser Box (kernel/%s)."
                    % os.path.basename(_cs.OFFICE_BASE)}
        pol0 = _cockpit_policy_enf(uid, sid) or {}
        if pol0.get("runtime") == "biomni":
            _frei()
            return {"ok": False, "reason": "Desktop und Biomni-Laufzeit schliessen sich aus "
                                           "(beide belegen das vdc-Volume)."}
        try:
            import pn_ram_admission as _adm
            cell0 = mgr.get(uid, sid)
            admit_id = getattr(cell0, "_admit_id", None) or ("sess:" + _cs._cell_name(uid, sid))
            want = max(int(pol0.get("mem_mb") or 0), _cs.OFFICE_MEM_MB)
            pl = _adm.plan(want, "office", exclude_id=admit_id)
            if not pl.get("grant"):
                _frei()
                return {"ok": False, "reason": pl.get("reason") or "RAM-Budget erschoepft."}
        except Exception:
            pass
    _desk_set(uid, sid, "start", "Desktop wird %s …" % ("aktiviert" if on else "beendet"), on=bool(on))
    threading.Thread(target=_desk_transition, args=(uid, sid, bool(on)), daemon=True).start()
    return {"ok": True}

def _desk_transition(uid, sid, on):
    import pn_cell_session as _cs
    mgr = _cs.get_manager()
    try:
        _desk_set(uid, sid, "sichern", "Gespraech wird gesichert …")
        c = mgr.get(uid, sid)
        if c is not None:
            try:
                c.sync()
            except Exception:
                pass
        _desk_set(uid, sid, "neustart",
                  "Die Session startet neu — %s …" % ("als Office-VM mit Bildschirm und mehr RAM"
                                                      if on else "als schlanke Terminal-VM"))
        mgr.stop(uid, sid, erase=False)

        _sessprov_set(uid, sid, {"desktop": bool(on)})
        pol = _cockpit_policy_enf(uid, sid) or {}
        if on:
            pol["desktop"] = True
            pol["mem_mb"] = max(int(pol.get("mem_mb") or 0), _cs.OFFICE_MEM_MB)
        else:
            pol.pop("desktop", None)
        cell = mgr.ensure(uid, sid, portal_url=_portal_base_url(),
                          portal_token=_voice_agent_token(uid), policy=pol)
        if not (cell and cell.alive()):
            reason = None
            try:
                reason = mgr.boot_reason(uid, sid)
            except Exception:
                pass
            if on:

                pol.pop("desktop", None)
                try:
                    mgr.ensure(uid, sid, portal_url=_portal_base_url(),
                               portal_token=_voice_agent_token(uid), policy=pol)
                except Exception:
                    pass
            _desk_set(uid, sid, "fehler", "", error=(reason or "Die Zelle startete nicht."))
            return
        if on:
            _desk_set(uid, sid, "arbeitsflaeche",
                      "Arbeitsflaeche wird gestartet (Fenster, Bildschirm-Lane) …")
            reason = cell.desktop_stage()
            if reason:
                _desk_set(uid, sid, "fehler", "", error=reason)
                return
        _desk_set(uid, sid, "bereit" if on else "aus", "")
    except Exception as e:
        _traceback_log("desk transition")
        _desk_set(uid, sid, "fehler", "", error="Unerwarteter Fehler: %r" % (e,))

_APP_ID_RE = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9._-]){2,127}\Z")
_APP_JOBS = {}
_APP_LOCK = threading.Lock()
_zst.register("portal_session_svc._APP_JOBS", "snapshot", __name__, ref=_APP_JOBS,
              beschreibung="Phase laufender App-Installationen je (uid, sid); Wahrheit ist die Zelle",
              neustart="verfaellt", schreiber="_app_set() unter _APP_LOCK")

def _app_set(uid, sid, phase, op=None, app_id=None, detail="", error=None):
    with _APP_LOCK:
        j = _APP_JOBS.get((uid, sid)) or {}
        j.update({"phase": phase, "detail": detail, "ts": time.time(), "error": error})
        if op is not None:
            j["op"] = op
        if app_id is not None:
            j["app_id"] = app_id
        _APP_JOBS[(uid, sid)] = j

def _app_cell(uid, sid):

    try:
        import pn_cell_session as _cs
    except Exception:
        return None, "Zellen-Subsystem nicht verfuegbar."
    c = _cs.get_manager().get(uid, sid)
    if c is None or not c.alive():
        return None, "Die Session laeuft nicht — bitte zuerst starten."
    if not (c.policy or {}).get("desktop"):
        return None, "Der Desktop ist nicht aktiv — Apps installieren geht nur im Office-Modus (🖥 Desktop)."
    return c, None

def _cell_app_status(uid, sid):

    with _APP_LOCK:
        j = dict(_APP_JOBS.get((uid, sid)) or {})
    c, _why = _app_cell(uid, sid)
    apps = []
    if c is not None and j.get("phase") not in ("laeuft",):
        ok, out = c._run("flatpak list --user --app --columns=application,name 2>/dev/null; echo __APPL__",
                         "__APPL__", 20)
        if ok:
            for ln in (out or "").strip().splitlines():
                parts = ln.split("\t")
                if parts and _APP_ID_RE.match(parts[0].strip()):
                    apps.append({"id": parts[0].strip(),
                                 "name": (parts[1].strip() if len(parts) > 1 else parts[0].strip())})
    j["installed"] = apps
    return j

def _cell_app(uid, sid, op, app_id):

    if op not in ("install", "remove"):
        return {"ok": False, "reason": "Unbekannte Aktion."}
    if not _APP_ID_RE.match(str(app_id or "")):
        return {"ok": False, "reason": "Ungueltige App-Kennung."}
    c, why = _app_cell(uid, sid)
    if c is None:
        return {"ok": False, "reason": why}

    if op == "install" and getattr(c, "tap", None) is None:
        return {"ok": False, "reason": "Zum Installieren braucht diese Session Netz-Zugang. Unter "
                                       "Rechte & Ausstattung das Internet für die Session freigeben, "
                                       "dann den Desktop einmal aus- und wieder einschalten."}
    with _APP_LOCK:
        j = _APP_JOBS.get((uid, sid))
        if j and j.get("phase") == "laeuft":
            return {"ok": False, "reason": "Fuer diese Session laeuft bereits eine App-Aktion."}

    try:
        import pn_cell_session as _cs
        import pn_session_cells as _sc2

        reg = _sc2.SessionCellRegistry(os.path.dirname(_cs.VOL_DIR))
        verdict = reg.decide_for(uid, sid, action_class="write")
        if verdict == "twofa":
            return {"ok": False, "reason": "Der Autonomie-Regler dieser Session verlangt eine 2FA-"
                                           "Bestaetigung fuer Installationen (Stufe Streng). Regler auf "
                                           "Standard stellen oder die Aktion bestaetigen."}
    except Exception:
        pass
    _app_set(uid, sid, "laeuft", op=op, app_id=app_id,
             detail=("%s wird installiert …" if op == "install" else "%s wird entfernt …") % app_id)
    threading.Thread(target=_app_run, args=(uid, sid, op, app_id), daemon=True).start()
    return {"ok": True}

def _app_run(uid, sid, op, app_id):
    try:
        c, why = _app_cell(uid, sid)
        if c is None:
            _app_set(uid, sid, "fehler", error=why)
            return

        pre = ("busybox mkdir -p /var/tmp /work/flatpak; "
               "export FLATPAK_SYSTEM_DIR=/work/flatpak; ")
        if op == "install":
            script = (pre +
                      "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY; "
                      "flatpak remote-add --system --if-not-exists flathub "
                      "https://dl.flathub.org/repo/flathub.flatpakrepo >/tmp/app.log 2>&1; "
                      "flatpak install --system -y --noninteractive flathub %s >>/tmp/app.log 2>&1; "
                      "RC=$?; busybox tail -c 500 /tmp/app.log; echo __APPRC${RC}__" % app_id)
            ok, out = c._run(script, "__APPRC", 1500)
            rc0 = ok and "__APPRC0__" in (out or "")
        else:
            script = (pre +
                      "flatpak uninstall --system -y %s >/tmp/app.log 2>&1; "
                      "RC=$?; busybox tail -c 500 /tmp/app.log; echo __APPRC${RC}__" % app_id)
            ok, out = c._run(script, "__APPRC", 300)
            rc0 = ok and "__APPRC0__" in (out or "")
        if rc0:
            _app_set(uid, sid, "fertig", detail="")
        else:
            tail = (out or "").strip()[-400:]
            _app_set(uid, sid, "fehler",
                     error=("Die App-Aktion schlug fehl: %s" % (tail or "kein Log — Zeitlimit?")))
    except Exception as e:
        _traceback_log("app run")
        _app_set(uid, sid, "fehler", error="Unerwarteter Fehler: %r" % (e,))

_FLATHUB_CACHE = {}
_zst.register("portal_session_svc._FLATHUB_CACHE", "cache", __name__, ref=_FLATHUB_CACHE, ttl_s=600.0,
              beschreibung="Flathub-Katalogsuche (Box-seitig; die Zelle bleibt governed), 10 min TTL",
              neustart="verfaellt", schreiber="_flathub_search()")

def _flathub_search(q):

    q = (q or "").strip()[:80]
    if len(q) < 2:
        return [], "Suchbegriff zu kurz."
    now = time.time()
    hit = _FLATHUB_CACHE.get(q.lower())
    if hit and now - hit[0] < 600:
        return hit[1], None
    import urllib.request
    body = json.dumps({"query": q}).encode()
    req = urllib.request.Request(
        "https://flathub.org/api/v2/search",
        data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.load(r)
    except Exception as e:
        return [], "Flathub nicht erreichbar (%s) — laeuft die Box offline?" % e.__class__.__name__
    out = []
    for h in (d.get("hits") or [])[:20]:
        aid = h.get("app_id") or h.get("id") or ""
        if _APP_ID_RE.match(aid):
            out.append({"id": aid, "name": h.get("name") or aid,
                        "summary": h.get("summary") or ""})
    _FLATHUB_CACHE[q.lower()] = (now, out)
    return out, None

def _machine_mem_mb():
    try:
        for ln in open("/proc/meminfo"):
            if ln.startswith("MemTotal:"):
                return int(ln.split()[1]) // 1024
    except Exception:
        pass
    return 0

def _machine_disk_mb(path):
    try:
        st = os.statvfs(path)
        return (st.f_blocks * st.f_frsize) // (1024 * 1024)
    except Exception:
        return 0

_CELL_CPU_SAMPLE = {}
_zst.register("portal_session_svc._CELL_CPU_SAMPLE", "snapshot", __name__, ref=_CELL_CPU_SAMPLE,
              beschreibung="CPU-Tick-Basis je KVM-PID fuer CPU%-Deltas zwischen Board-Polls; Verlust => erster Poll ohne CPU%-Delta",
              neustart="rekonstruiert", schreiber="_cell_resources()")

def _cell_resources(uid, sid):

    try:
        import pn_cell_session as _cs
        cell = _cs.get_manager().get(uid, sid)
    except Exception:
        cell = None
    if not (cell and getattr(cell, "proc", None) is not None and cell.proc.poll() is None):
        return None
    _rv = getattr(_cs, "RemoteVmm", None)
    if _rv is not None and isinstance(cell.proc, _rv):

        r = {"running": True, "remote": True}
        try:
            r["node"] = cell._remote_node()
        except Exception:
            pass
        try:
            alloc = int((cell.policy or {}).get("mem_mb") or 0) or int(getattr(_cs, "MEM_MB", 0) or 0)
            if alloc:
                r["ram_alloc_mb"] = alloc
        except Exception:
            pass
        return r
    pid = cell.proc.pid
    r = {"running": True}
    ncpu = os.cpu_count() or 1
    mem_total = _machine_mem_mb()

    try:
        rss = 0
        for ln in open("/proc/%d/status" % pid):
            if ln.startswith("VmRSS:"):
                rss = int(ln.split()[1]) // 1024; break
        try:

            alloc = int((cell.policy or {}).get("mem_mb") or 0) or int(getattr(_cs, "MEM_MB", 0) or 0)
        except Exception:
            alloc = 0
        r["ram_mb"] = rss
        r["ram_alloc_mb"] = alloc
        r["ram_pct_alloc"] = round(100.0 * rss / alloc, 1) if alloc else None
        r["ram_pct_machine"] = round(100.0 * rss / mem_total, 1) if mem_total else None
    except Exception:
        pass

    try:
        with open("/proc/%d/stat" % pid) as f:
            parts = f.read().rsplit(") ", 1)[1].split()
        busy = int(parts[11]) + int(parts[12])
        clk = os.sysconf("SC_CLK_TCK") or 100
        now = time.time(); prev = _CELL_CPU_SAMPLE.get(pid)
        _CELL_CPU_SAMPLE[pid] = (busy, now)
        if prev and now > prev[1]:
            core_pct = 100.0 * (busy - prev[0]) / clk / (now - prev[1])
            r["cpu_pct_core"] = round(max(0.0, core_pct), 1)
            r["cpu_pct_machine"] = round(max(0.0, core_pct / ncpu), 1)
    except Exception:
        pass
    r["cpu_ncpu"] = ncpu

    try:
        stt = os.stat(cell.delta)
        r["disk_mb"] = (stt.st_blocks * 512) // (1024 * 1024)
        try:
            cap = int((cell.policy or {}).get("delta_mb") or 0)
        except Exception:
            cap = 0
        r["disk_cap_mb"] = cap or 512
        r["disk_pct_alloc"] = round(100.0 * r["disk_mb"] / r["disk_cap_mb"], 1) if r["disk_cap_mb"] else None
        dtot = _machine_disk_mb(os.path.dirname(cell.delta))
        r["disk_pct_machine"] = round(100.0 * r["disk_mb"] / dtot, 1) if dtot else None
    except Exception:
        pass
    return r

import portal_account_vpn
from portal_account_vpn import (
    _uservpn_grants, _uservpn_allowed, _uservpn_set, _account_netns_name, _netns_exists,
    _ensure_netns_askpass, _write_launch_script, _equip_flags,
)
