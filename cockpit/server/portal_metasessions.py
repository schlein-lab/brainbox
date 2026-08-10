
import os, sys, json, base64, secrets, subprocess, time
import re, threading, collections

HOME = os.path.expanduser("~")
CFG_DIR = os.path.join(HOME, ".config", "brainbox-portal")
DATA_DIR = os.path.join(HOME, ".local", "share", "brainbox-portal")
DEFAULT_PRINCIPAL = "owner"

try:
    import pn_session_policy as _policy
except Exception:
    _policy = None
try:
    import portal_agent
except Exception:
    portal_agent = None
try:
    import portal_channels
except Exception:
    portal_channels = None

HPC_HOST = None
HPC_VPN_BIN = None
VOICE_CELL = None
VOICE_COLD_CUE_WAIT = None
VOICE_FIRST_GRACE = None
VOICE_FIRST_WAIT = None
VOICE_TURN_TIMEOUT = None
VOICE_WARM_H0 = None
VOICE_WARM_H1 = None
_DISPLAY_REG = None
_NETNS_ASKPASS = None
_NETNS_MGR = None
_VOICE_RIGHTS_NOTICE = None
_VOICE_ROUTE_NOTICE = None
_account_netns_name = None
_agent_ctx = None
_chan_ctx = None
_cockpit_inner = None
_ensure_netns_askpass = None
_ensure_trusted = None
_global_floor = None
_hpc_status = None
_inject = None
_kiosk_post = None
_netns_exists = None
_netns_uid = None
_netns_vpn = None
_pane = None
_policy_store_mod = None
_portal_base_url = None
_prov_log = None
_sess_policy_get = None
_sess_policy_store = None
_session_store = None
_sessprov_get = None
_sessprov_set = None
_sessprov_del = None
_tresor_dir = None
_uid_safe = None
_uservpn_allowed = None
_voice_agent_token = None
_voice_alive = None
_voice_cell_stream_async = None
_voice_cellmgr = None
_voice_dir = None
_voice_policy_enf = None
_voice_prewarm_mode = None
_voice_reg_touch = None
_voice_rotate_and_prewarm = None
_voice_route_maybe_revert = None
_voice_route_touch = None
_voice_sess = None
_voice_session_for = None
_voice_stream_get = None

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

_METASESS_FILE = os.path.join(DATA_DIR, "metasessions.json")
_METASESS_DIR = os.path.join(DATA_DIR, "metasessions")
_META_LOCK = threading.Lock()
_META_PROCS = {}

import portal_zustand as _zst
_zst.register("portal_metasessions._META_PROCS", "singleton", __name__, ref=_META_PROCS,
              beschreibung="Popen-Handles je (msid, tid) fuer In-Memory-Reaping; Verlust => Prozesse laufen weiter, Einsammeln uebernimmt die Nachlese (Phase 4)",
              neustart="verfaellt", schreiber="Scheduler-/Worker-Pfade unter _META_LOCK")

def _meta_load():
    try:
        with open(_METASESS_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

_meta_ro_cache = [None]
_zst.register("portal_metasessions._meta_ro_cache", "cache", __name__, ref=_meta_ro_cache,
              beschreibung="metasessions.json geparst fuer REINE Lese-Pfade, invalidiert per (mtime_ns, size)-Signatur; Rueckgabe darf NIE mutiert werden",
              neustart="verfaellt", schreiber="_meta_load_ro() bei Signatur-Wechsel")

def _meta_load_ro():

    try:
        st = os.stat(_METASESS_FILE)
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    c = _meta_ro_cache[0]
    if c is not None and c[0] == sig:
        return c[1]
    d = _meta_load()
    _meta_ro_cache[0] = (sig, d)
    return d

def meta_parent_of(sid):

    try:
        s = str(sid or "")
        if not s:
            return None
        d = _meta_load_ro()
        items = list(d.items()) if isinstance(d, dict) else []
        owners = {str((ms or {}).get("owner") or DEFAULT_PRINCIPAL)
                  for _, ms in items if isinstance(ms, dict)}
        if callable(_sessprov_get):
            for ow in (owners or {DEFAULT_PRINCIPAL}):
                try:
                    prov = _sessprov_get(ow, s) or {}
                except Exception:
                    prov = {}
                mid = prov.get("meta_id")
                if prov.get("role") == "worker" and mid and str(mid) != s:
                    return str(mid)
        for msid, ms in items:
            if not isinstance(ms, dict) or msid == s:
                continue
            for t in (ms.get("tasks") or []):
                if isinstance(t, dict) and str(t.get("sid") or "") == s:
                    return str(ms.get("lead_sid") or msid)
    except Exception:
        pass
    return None

def _meta_update(fn):

    with _META_LOCK:
        d = _meta_load()
        r = fn(d)
        try:
            tmp = _METASESS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(d, f)
            os.replace(tmp, _METASESS_FILE)
        except Exception:
            _traceback_log("meta save")
        return r

def _meta_counts(ms):

    c = {"pending": 0, "starting": 0, "running": 0, "done": 0, "error": 0}
    for t in ms.get("tasks", []):
        c[t.get("state", "pending")] = c.get(t.get("state", "pending"), 0) + 1
    return c

def _meta_apply_policy(owner, sid, tpl):

    try:
        if not _policy:
            return
        preset = str(tpl.get("preset") or _policy.DEFAULT_PRESET)
        pol = _policy.new_policy(preset)
        caps = tpl.get("caps") or {}
        if isinstance(caps, dict) and caps:
            pol.setdefault("caps", {}); pol["caps"].update(caps); pol["preset"] = "custom"
        _sess_policy_store().set(owner, "cockpit", sid, _policy.validate(pol))
    except Exception:
        _traceback_log("meta apply policy")

_META_BUSY_EVERY = 25.0
_META_PROBE = {}
_zst.register("portal_metasessions._META_PROBE", "snapshot", __name__, ref=_META_PROBE, ttl_s=25.0,
              beschreibung="letzte In-Zell-Busy-Probe je (owner, sid); Takt _META_BUSY_EVERY (Seat-Lane serialisiert)",
              neustart="verfaellt", schreiber="Worker-Tick")
_META_RETRY_MARK = "\x00retry\x00"
_META_RETRY_MAX = 60
_META_RETRY_BACKOFF = 180.0

_META_IDLE_DONE_S = float(os.environ.get("PN_META_IDLE_DONE_S", 1800))

_META_WORKER_MAX_S = float(os.environ.get("PN_META_WORKER_MAX_S", 0))

_META_NO_JSONL_MAX_S = float(os.environ.get("PN_META_NO_JSONL_MAX_S", 900))

_META_STARTING_MAX_S = float(os.environ.get("PN_META_STARTING_MAX_S", 900))

_META_RESULT_MAX = int(os.environ.get("PN_META_RESULT_MAX", 65536))

_META_WATCH = os.environ.get("PN_META_WATCH", "1").strip().lower() not in ("0", "off", "aus", "false")

_META_WATCH_EVERY_S = float(os.environ.get("PN_META_WATCH_EVERY_S", "300"))

_META_WATCH_MODE = os.environ.get("PN_META_WATCH_MODE", "fehler").strip().lower()
if _META_WATCH_MODE not in ("fehler", "immer"):
    _META_WATCH_MODE = "fehler"
_META_WATCH_READ_MAX = int(os.environ.get("PN_META_WATCH_READ_MAX", 49152))
_META_WATCH_CHILD_CHARS = 1600
_META_WATCH_MSG_CHARS = 7000

_META_WATCH_FIRST_TAIL = 8192

def _verdacht_loeschen(t):

    for k in ("_notalive_since", "_nojsonl_since"):
        t.pop(k, None)

def _meta_cells_down():

    try:
        import pn_cell_session as _cs
    except Exception as e:
        return ("Sitzungs-Zellen sind auf dieser Box nicht verfuegbar (%s: %s). Ein Dauerjob-Worker "
                "laeuft ausschliesslich in einer eigenen microVM, deshalb wird nichts gestartet."
                % (type(e).__name__, str(e)[:120]))
    try:
        return _cs.preflight()
    except Exception:
        _traceback_log("meta cells preflight")
        return ("Die Zellen-Vorpruefung ist fehlgeschlagen. Ein Worker wird nur in einer microVM "
                "gestartet, niemals auf der Box selbst.")

def _meta_cellmgr():

    try:
        import pn_cell_session as _cs
        return _cs.get_manager()
    except Exception:
        return None

def _meta_cell(owner, sid):

    try:
        mgr = _meta_cellmgr()
        return mgr.cell(owner, sid) if mgr else None
    except Exception:
        return None

def _meta_llm_lane_reason():

    try:
        import pn_cell_session as _cs
        return _cs.llm_lane_reason()
    except Exception:
        return None

def _meta_worker_policy(owner, sid):

    try:
        import portal_voice_core as _vc
        enf = _vc._cockpit_policy_enf(owner, sid)
        if enf:
            return enf
    except Exception:
        pass
    try:
        return _policy.enforcement(_sess_policy_get(owner, sid) or {}) if _policy else None
    except Exception:
        _traceback_log("meta worker policy")
        return None

def _meta_worker_brief(ms, tpl):

    lvl = 3
    try:
        lvl = max(0, min(5, int(tpl.get("autonomy", 3) or 3)))
    except Exception:
        pass
    lab = "L%d" % lvl
    try:
        import pn_session_cells as _sc
        lab = _sc.AUTONOMY_LABELS.get(lvl) or lab
    except Exception:
        pass
    L = ["# Worker-Session im Dauerjob: %s" % (ms.get("title") or "Dauerjob"), "",
         "Du bist eine eigenstaendige Arbeits-Session mit GENAU EINER Aufgabe — sie kommt gleich als "
         "erste Nachricht. Erledige sie vollstaendig und zielstrebig.", "",
         "Du laeufst in einer eigenen, isolierten microVM. Was du darin tust, bleibt darin: du siehst "
         "weder die Box noch andere Sitzungen, und niemand ausser dir arbeitet in dieser Zelle.", "",
         "WENN DU FERTIG BIST: schliesse mit einer kurzen Ergebnis-Zusammenfassung als letzter "
         "Nachricht ab und fang nichts Neues an. Daran erkennt das Portal, dass du fertig bist — es "
         "uebernimmt deine Zusammenfassung als Ergebnis, faehrt deine Zelle herunter und gibt den "
         "Platz fuer die naechste wartende Aufgabe frei.", "",
         "AUTONOMIE: Stufe %s." % lab]
    try:
        caps = tpl.get("caps") or {}
        deny = [k for k in ("websearch", "webfetch", "net_general") if caps.get(k) == "deny"]
        if deny:
            L.append("RECHTE verweigert: %s — benutze diese Werkzeuge NICHT." % ", ".join(deny))
    except Exception:
        pass
    return "\n".join(L) + "\n"

def _meta_reg_touch(owner, sid):

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
        _traceback_log("meta reg touch")

def _meta_cell_stop(owner, sid, erase=False):

    try:
        mgr = _meta_cellmgr()
        if mgr:
            mgr.stop(owner, sid, erase=erase)
    except Exception:
        _traceback_log("meta cell stop")
    _META_PROBE.pop((owner, sid), None)

def _meta_retire_session(owner, sid):

    _meta_cell_stop(owner, sid, erase=False)
    try:
        st = _session_store(owner, "cockpit")

        if hasattr(st, "set_archived"):
            st.set_archived(sid, True)
    except Exception:
        _traceback_log("meta retire session")

_meta_drop_session = _meta_retire_session

_HARNESS_ABBRUCH = (
    "api error",
    "execution error",
    "response stalled",
    "request timed out",
    "request was aborted",
    "prompt is too long",
    "credit balance is too low",
    "invalid api key",
    "overloaded",
    "internal server error",
)

def _fortsetzbar_lebt(ms):

    try:
        import pn_cell_fern as _fern
        import pn_session_cells as _psc
        zellen, alle_frisch = _fern.fern_zellen_uebersicht()
    except Exception:
        return []
    if not alle_frisch or not zellen:
        return []
    owner = ms.get("owner") or DEFAULT_PRINCIPAL
    lebt = []
    for t in ms.get("tasks", []):
        sid = t.get("sid")
        if not sid or t.get("state") in ("done", "running", "starting"):
            continue
        try:
            name = _psc.cell_name(owner, sid)
        except Exception:
            continue
        eintrag = zellen.get(name)
        if not eintrag:
            continue
        zustand = str(((eintrag.get("eintrag") or {}) if isinstance(eintrag, dict) else {})
                      .get("state") or "").lower()

        if zustand in ("", "running", "up", "live"):
            lebt.append(t.get("tid"))
    return lebt

def _warum_kein_ergebnis(res):

    s = (res or "").strip()
    if not s:
        return (u"Zelle endete ohne jede Ausgabe — es wurde kein Ergebnis erzeugt "
                u"(der Auftrag ist NICHT erledigt).")

    kopf = s[:160].lstrip(u"[(*_# \t").lower()
    if any(kopf.startswith(m) for m in _HARNESS_ABBRUCH):
        return (u"Der Agent-Harness ist abgerissen, statt zu liefern: %s — der Auftrag ist "
                u"NICHT erledigt. Das Delta bleibt; er kann mit seinem Gedaechtnis "
                u"fortgesetzt werden." % s.splitlines()[0][:140])
    return None

def _meta_worker_result(owner, sid):

    if not sid:
        return ""
    cell = _meta_cell(owner, sid)
    if cell is None or not cell.alive():
        return ""
    try:
        for turn in reversed(cell.conversation_tail(n=8) or []):
            if turn.get("role") == "assistant" and (turn.get("text") or "").strip():
                return turn["text"].strip()[:_META_RESULT_MAX]
    except Exception:
        _traceback_log("meta worker result")
    return ""

def _meta_cell_busy(owner, sid, cell, now):

    key = (owner, sid)
    ts, val, why = _META_PROBE.get(key, (0.0, None, None))
    if now - ts < _META_BUSY_EVERY:
        return val, why
    try:
        path = cell._incell_active_jsonl()
        if not path:
            val, why = True, "nojsonl"
        else:
            val, why = bool(cell._incell_turn_busy(path)), "turn"
    except Exception:
        val, why = None, None
    if val:

        try:
            cell.last = now
        except Exception:
            pass
    _META_PROBE[key] = (now, val, why)
    return val, why

_META_RESUME_MAX = int(os.environ.get("PN_META_RESUME_MAX", "3"))

_DELTA_CACHE = {}
_DELTA_TTL_S = float(os.environ.get("PN_META_DELTA_TTL_S", "300"))

_DELTA_TTL_FEHLER_S = 30.0
_zst.register("portal_metasessions._DELTA_CACHE", "cache", __name__, ref=_DELTA_CACHE, ttl_s=300.0,
              beschreibung="Arbeitsstand-Auskunft je sid (Netzabfrage); Fehler halten nur 30 s — ein Nulltreffer darf sich nicht als Abwesenheit verfestigen",
              neustart="verfaellt", schreiber="_meta_work_delta()")

def _meta_work_delta(sid):

    if not sid:
        return None
    now = time.time()
    _c = _DELTA_CACHE.get(sid)
    if _c and now - _c[0] < (_DELTA_TTL_S if _c[1] else _DELTA_TTL_FEHLER_S):
        return _c[1]

    try:
        import glob as _g
        import pn_cell_session as _cs
        hits = _g.glob(os.path.join(_cs.VOL_DIR, "*-%s_*-delta.img" % sid))
        if hits:
            _DELTA_CACHE[sid] = (now, hits[0])
            return hits[0]
    except Exception:
        _traceback_log("meta work delta lokal")

    wert = None
    try:
        prov = (_sessprov_get(DEFAULT_PRINCIPAL, sid) or {}) if callable(_sessprov_get) else {}
        nid = (prov.get("node") or "").strip()
        if nid:
            import pn_cell_session as _cs
            import portal_placement as _pp
            if nid != getattr(_pp, "LOCAL_ID", "local"):
                cid = _cs._cell_name(DEFAULT_PRINCIPAL, sid)
                ep, tok = _pp.node_endpoint(nid), _pp.node_token(nid)
                if ep and tok:
                    import urllib.request
                    req = urllib.request.Request("%s/cells/%s/delta" % (ep, cid),
                                                 headers={"X-Node-Token": tok})
                    with urllib.request.urlopen(req, timeout=4) as r:
                        obj = json.loads(r.read().decode("utf-8"))
                    if isinstance(obj, dict) and obj.get("present"):
                        wert = "node:%s:%s" % (nid, cid)
    except Exception:

        _DELTA_CACHE[sid] = (now, None)
        return None

    _DELTA_CACHE[sid] = (now, wert)
    return wert

_META_RESUME_NOTE = (
    "WIEDERAUFNAHME — dein Auftrag wurde unterbrochen, nicht von dir verpatzt.\n\n"
    "Deine Zelle wurde gestoppt, bevor du fertig warst. Grund war ein Infrastruktur-Fehler auf "
    "UNSERER Seite, nicht deine Arbeit:\n  %s\n\n"
    "DEIN ARBEITSSTAND IST ERHALTEN — das Session-Delta wurde nie geloescht: Downloads, gebaute "
    "Indizes, Zwischenergebnisse und deine bisherige Konversation sind noch da.\n\n"
    "Bitte jetzt:\n"
    "1. Verschaffe dir ZUERST einen Ueberblick, was tatsaechlich noch vorliegt — pruefen statt "
    "annehmen: Arbeitsverzeichnis, ~/austausch, und dein Ergebnispfad auf dem Cluster. Rechen-Jobs, "
    "die du abgeschickt hattest, koennen laengst DURCHGELAUFEN sein; deren Ergebnisse muessen dann "
    "nur noch eingesammelt werden. Sieh nach, BEVOR du irgendetwas neu rechnest.\n"
    "2. Pruefe kurz, dass deine Kanaele funktionieren. Wenn nicht: SOFORT melden, nicht raten.\n"
    "3. Nimm die Arbeit dort wieder auf, wo sie abbrach, und schliesse sie ehrlich ab — mit dem "
    "Ergebnis-Kontrakt aus deinem Brief. Ehrliche Teilarbeit ist ein vollwertiges Ergebnis; "
    "erfinde nichts.\n\n"
    "WENN DIR AN DIESEM AUFTRAG ETWAS NICHT GEHEUER IST — eine Anweisung erscheint dir nicht "
    "gedeckt, der Zusammenhang passt nicht zu deinem Brief —, dann fuehre sie NICHT einfach aus, "
    "stell aber auch nicht wortlos die Arbeit ein: SCHREIB DEINEN EINWAND AUS, mit Begruendung. "
    "Er wird gelesen und beantwortet. Ein begruendeter Einwand ist ein Ergebnis, kein "
    "Fehlverhalten.\n\n"
    "RECHENZEIT IST KEIN FEHLER. Du darfst Stunden und Tage brauchen. Beendet wirst du nur noch, "
    "wenn du nachweislich nicht mehr arbeitest.")

_META_RESUME_KOPF_MAX = int(os.environ.get("PN_META_RESUME_KOPF_MAX", "1200"))
_META_RESUME_AUFTRAG = (
    "DEIN AUFTRAG — unveraendert. Hier noch einmal VOLLSTAENDIG, damit du ihn nicht aus dem\n"
    "Gedaechtnis rekonstruieren musst und dein Mandat belegt ist. Der VOLLE Text steht in\n"
    "deinem Brief unter 'DEIN AUFTRAG':\n\n"
    "%s\n\n"
    "--- Ende des Auftrags ---\n\n")

def _meta_resume_worker(ms, tid, task, sid):

    try:
        owner = ms.get("owner") or DEFAULT_PRINCIPAL
        tpl = ms.get("template", {}) or {}
        down = _meta_cells_down()
        if down:
            return (None, None, _META_RETRY_MARK + ("Wiederaufnahme vertagt: %s" % down))
        mgr = _meta_cellmgr()
        if mgr is None:
            return (None, None, "Wiederaufnahme unmoeglich: der Zellen-Manager ist nicht verfuegbar.")
        wpol = _meta_worker_policy(owner, sid)
        vpn = tpl.get("vpn")
        if vpn and tpl.get("vpn_dauerjob"):
            vpn_ns = _account_netns_name(owner, vpn) if _account_netns_name else None
            if not (vpn_ns and _netns_exists and _netns_exists(vpn_ns)):
                return (None, None, _META_RETRY_MARK + (
                    "Wiederaufnahme vertagt: der Account-Tunnel '%s' steht gerade nicht." % vpn))
            wpol = dict(wpol or {})
            wpol["vpn_netns"] = vpn_ns
            wpol["require_tun"] = "cscotun"

        cell = mgr.ensure(owner, sid, portal_url=_portal_base_url(),
                          portal_token=_voice_agent_token(owner), policy=wpol)
        if cell is None or not cell.alive():
            reason = (mgr.boot_reason(owner, sid) if cell is None else cell.boot_reason()) \
                or "Die microVM der wiederaufgenommenen Zelle ist nicht hochgekommen."
            transient = bool(getattr(cell, "_admit_denied", None)) if cell is not None else False
            return (None, None, (_META_RETRY_MARK if transient else "") + reason)
        why = (ms.get("_resume_reason") or {}).get(tid) or "die Zelle wurde vorzeitig beendet"

        auftrag = str(task or "").strip()
        kopf = (_META_RESUME_AUFTRAG % _kurz(auftrag, _META_RESUME_KOPF_MAX)) if auftrag else ""
        brief = _meta_worker_brief(ms, tpl)
        if auftrag:
            brief = "%s\n\n# DEIN AUFTRAG (unveraendert, vollstaendig)\n\n%s" % (brief, auftrag)
        if not cell.submit(kopf + (_META_RESUME_NOTE % why), system=brief):
            reason = (cell.term_reason() or _meta_llm_lane_reason()
                      or "Der Agent in der Zelle hat die Wiederaufnahme nicht angenommen.")
            return (None, None, reason)
        _meta_reg_touch(owner, sid)
        return (sid, None, None)
    except Exception as e:
        _traceback_log("meta resume worker")
        return (None, None, str(e))

def _meta_spawn_worker(ms, tid, task):

    try:
        owner = ms.get("owner") or DEFAULT_PRINCIPAL
        tpl = ms.get("template", {}) or {}
        vpn = tpl.get("vpn")
        vpn_ns = None
        if vpn:

            if not tpl.get("vpn_dauerjob"):
                return (None, None,
                        "Dieser Dauerjob ist mit dem Tunnel '%s' verknuepft, aber die VPN-Bindung ist "
                        "NICHT eingeschaltet (Standard: aus). Schalten Sie sie bewusst im Dauerjob-"
                        "Assistenten ein ('VPN-Bindung aktivieren') — dort steht auch, dass solche Jobs "
                        "meist am ~24-Stunden-Reconnect (2FA) abbrechen. Ohne diese ausdrueckliche "
                        "Freigabe wird kein Worker gestartet." % vpn)

            vpn_ns = _account_netns_name(owner, vpn) if _account_netns_name else None
            if not (vpn_ns and _netns_exists and _netns_exists(vpn_ns)):
                return (None, None,
                        "Der Account-Tunnel '%s' ist gerade nicht aufgebaut. Ein VPN-gebundener Dauerjob "
                        "startet nur, solange der Tunnel steht — sonst wuerde die Zelle am Tunnel vorbei "
                        "arbeiten. Bitte zuerst den Tunnel verbinden (2FA), dann erneut versuchen." % vpn)
            try:
                st = _netns_vpn("status", _netns_uid(owner), vpn, timeout=15) \
                    if (_netns_vpn and _netns_uid) else {}
                if isinstance(st, dict) and st.get("ns_exists") and st.get("connected") is False:
                    return (None, None,
                            "Der Account-Tunnel '%s' ist angelegt, aber der Cisco-Tunnel steht nicht "
                            "(vermutlich mitten im ~24h-Reconnect / 2FA faellig). Kein Worker gestartet — "
                            "bitte Tunnel neu verbinden." % vpn)
            except Exception:
                _traceback_log("meta vpn status probe")
        down = _meta_cells_down()
        if down:
            return (None, None, "Kein Worker gestartet: %s" % down)

        _pinned_node = None
        try:
            import pn_ram_admission as _RA0
            import portal_placement as _pp
            _pwant = _RA0.default_mem_for("session")

            _lanes = True
            try:
                import portal_voice_core as _pvc

                _wenf = _policy.enforcement({"caps": tpl.get("caps") or {}}) if _policy else {}
                _lanes = bool(_pvc._needs_local_lanes(_wenf or {}))
            except Exception:
                _traceback_log("meta spawn lane-check")

            _seat_bound = bool(tpl.get("needs_net")) or _lanes \
                or bool(tpl.get("desktop")) or bool(tpl.get("office")) or bool(tpl.get("gui"))
            _nid = _pp.LOCAL_ID if _seat_bound else _pp.pick_node(_pwant, arch_pref=None)
            if _nid is None:
                _ncons = 0
                try:
                    _ncons = len(_pp.nodes())
                except Exception:
                    pass
                return (None, None, _META_RETRY_MARK + (
                    "Kein Node im Fleet hat Platz fuer einen weiteren Worker (%d Node%s geprueft, "
                    "inkl. Box nach Interaktiv-Reserve). Die Aufgabe wartet, bis irgendwo im Fleet "
                    "Platz frei wird." % (_ncons, "" if _ncons == 1 else "s")))
            if _nid != _pp.LOCAL_ID:
                _pinned_node = _nid
        except Exception:
            _traceback_log("meta spawn fleet-placement")
            _pinned_node = None

        if not _pinned_node:
            try:
                import pn_ram_admission as _RA
                _want = _RA.default_mem_for("session")

                try:
                    _lead = str(ms.get("lead_sid") or ms.get("id") or "")
                    _lc = _meta_cell(owner, _lead) if _lead else None
                    if _lead and (_lc is None or not _lc.alive()):
                        _lprov = (_sessprov_get(owner, _lead) or {}) if callable(_sessprov_get) else {}
                        try:
                            _lmm = int(_lprov.get("mem_mb") or 0)
                        except (TypeError, ValueError):
                            _lmm = 0
                        _want += _lmm or (4096 if _lprov.get("orchestrator")
                                          else _RA.default_mem_for("session"))
                except Exception:
                    pass
                _pl = _RA.plan(_want, "session")
                if not _pl.get("grant"):
                    return (None, None, _META_RETRY_MARK + (
                        "Kein Platz fuer einen weiteren Worker: %d/%d MiB belegt, %d MiB frei, %d MiB "
                        "angefragt. Die Aufgabe wartet, bis ein laufender Worker Platz freigibt."
                        % (int(_pl.get("committed_mb") or 0), int(_pl.get("budget_mb") or 0),
                           int(_pl.get("free_budget_mb") or 0), _want)))
            except Exception:
                _traceback_log("meta spawn ram-preflight")

        _wequip = {"disk": tpl.get("worker_disk_mb"), "mem": tpl.get("worker_mem_mb")}
        try:
            _lead = ms.get("lead_sid") or ms.get("id")
            _lp = (_sessprov_get(owner, _lead) or {}) if (_lead and callable(_sessprov_get)) else {}
            _wequip["disk"] = _lp.get("worker_disk_mb") or _wequip["disk"]
            _wequip["mem"] = _lp.get("worker_mem_mb") or _wequip["mem"]
        except Exception:
            _traceback_log("meta spawn worker-equipment")
        _t1 = next((ln.strip() for ln in (task or "").splitlines() if ln.strip()), "")
        title = "⚙ %s · %s" % ((ms.get("title") or "Dauerjob")[:16], _t1[:28])
        rec = _session_store(owner, "cockpit").create(title)
        sid = rec["id"]

        _t_dict = next((x for x in (ms.get("tasks") or []) if x.get("tid") == tid), None) or {}
        _kind_modell = (_meta_modellstufe(_t_dict.get("model"))
                        or _meta_modellstufe(_META_CHILD_MODEL)
                        or tpl.get("model"))
        _sessprov_set(owner, sid, {"model": _kind_modell, "effort": tpl.get("effort"),
            "autonomy": tpl.get("autonomy", 3), "preset": tpl.get("preset"), "vpn": None,
            "role": "worker", "meta_id": ms.get("id"), "meta_tid": tid, "meta_title": ms.get("title"),
            "orch_depth": int((tpl.get("child_depth") or 1)), "title": title,
            **({"disk_mb": int(_wequip["disk"])} if _wequip.get("disk") else {}),
            **({"mem_mb": int(_wequip["mem"])} if _wequip.get("mem") else {}),
            **({"node": _pinned_node} if _pinned_node else {})})
        _meta_apply_policy(owner, sid, tpl)
        mgr = _meta_cellmgr()
        if mgr is None:
            return (None, None, "Kein Worker gestartet: der Zellen-Manager ist nicht verfuegbar.")
        wpol = _meta_worker_policy(owner, sid)
        if vpn_ns:
            wpol = dict(wpol or {})
            wpol["vpn_netns"] = vpn_ns
            wpol["require_tun"] = "cscotun"
        if _pinned_node:
            wpol = dict(wpol or {})
            wpol["node"] = _pinned_node
        cell = mgr.ensure(owner, sid, portal_url=_portal_base_url(),
                          portal_token=_voice_agent_token(owner),
                          policy=wpol)
        if cell is None or not cell.alive():
            reason = (mgr.boot_reason(owner, sid) if cell is None else cell.boot_reason()) \
                or "Die microVM des Workers ist nicht hochgekommen."

            transient = bool(getattr(cell, "_admit_denied", None)) if cell is not None else False
            _meta_drop_session(owner, sid)
            return (None, None, (_META_RETRY_MARK if transient else "") + reason)
        if not cell.submit(str(task), system=_meta_worker_brief(ms, tpl)):
            reason = (cell.term_reason() or _meta_llm_lane_reason()
                      or "Der Agent in der Zelle hat die Aufgabe nicht angenommen.")
            _meta_drop_session(owner, sid)
            return (None, None, reason)
        _meta_reg_touch(owner, sid)
        return (sid, None, None)
    except Exception as e:
        _traceback_log("meta spawn worker")
        return (None, None, str(e))

def meta_say(msid, tid, text):

    text = str(text or "").strip()
    if not text:
        return (False, "Kein Text uebergeben.")
    ms = (_meta_load() or {}).get(msid)
    if not ms:
        return (False, "Dauerjob unbekannt.")
    t = next((x for x in ms.get("tasks", []) if x.get("tid") == tid), None)
    if not t or not t.get("sid"):
        return (False, "Worker nicht gefunden oder nicht aktiv.")
    owner = ms.get("owner") or t.get("owner") or DEFAULT_PRINCIPAL
    cell = _meta_cell(owner, t["sid"])
    if cell is None or not cell.alive():
        return (False, "Die Zelle dieses Workers laeuft nicht mehr.")
    try:
        if not cell.submit(text):
            return (False, cell.term_reason() or _meta_llm_lane_reason()
                    or "Der Agent in der Zelle hat die Nachricht nicht angenommen.")
    except Exception as e:
        _traceback_log("meta say")
        return (False, str(e)[:200])
    _META_PROBE.pop((owner, t["sid"]), None)
    return (True, None)

def meta_stop_workers(msid):

    ms = (_meta_load() or {}).get(msid) or {}
    owner = ms.get("owner") or DEFAULT_PRINCIPAL
    n = 0
    for t in ms.get("tasks", []):
        sid = t.get("sid")
        if sid and _meta_cell(owner, sid) is not None:
            _meta_cell_stop(owner, sid)
            n += 1
    return n

_META_BOOT_TS = time.time()
_META_ADOPT_GRACE_S = float(os.environ.get("PN_META_ADOPT_GRACE_S", "180"))

_META_NOTALIVE_CONFIRM_S = float(os.environ.get("PN_META_NOTALIVE_CONFIRM_S", "120"))

_META_SWEEP_GRACE_S = float(os.environ.get("PN_META_SWEEP_GRACE_S", "600"))
_META_SWEEP_EVERY_S = 60.0
_META_SWEEP_MAX_PER_RUN = 2
_META_SWEEP_MAX_VERSUCHE = 3
_meta_last_sweep = 0.0
_meta_sweep_versuche = {}
_zst.register("portal_metasessions._meta_sweep_versuche", "cursor", __name__, ref=_meta_sweep_versuche,
              beschreibung="Zaehler wirkungsloser Stop-Versuche je sid (Nachlese, Deckel 3); Verlust => Zaehlung beginnt neu = weitere Stop-Versuche (begrenzte Doppelarbeit)",
              neustart="verfaellt", schreiber="Nachlese-Sweep (Phase 4)")

def _worker_state(t, now, owner=None):

    sid = t.get("sid")
    if not sid:
        return "running"
    owner = owner or t.get("owner") or DEFAULT_PRINCIPAL
    cell = _meta_cell(owner, sid)
    if cell is None or not cell.alive():

        if now - _META_BOOT_TS < _META_ADOPT_GRACE_S:
            return "running"

        seit = t.get("_notalive_since")
        if not seit:
            t["_notalive_since"] = now
            return "running"
        if now - seit < _META_NOTALIVE_CONFIRM_S:
            return "running"
        return "done"
    t.pop("_notalive_since", None)
    started = t.get("started") or now
    busy, why = _meta_cell_busy(owner, sid, cell, now)
    if busy is None:
        return "running"
    if busy:

        if why == "nojsonl":
            since = t.get("_nojsonl_since") or now
            t["_nojsonl_since"] = since
            if now - since > _META_NO_JSONL_MAX_S:
                t["error"] = ("Zelle hat seit %d min keinen aktiven Gespraechs-Log — der Agent-REPL "
                              "ist nicht angelaufen." % int(_META_NO_JSONL_MAX_S / 60))
                return "error"
        else:
            t.pop("_nojsonl_since", None)
        t["_lastbusy"] = now
        return "running"
    t.pop("_nojsonl_since", None)
    lb = t.get("_lastbusy") or started
    if now - started > 30 and now - lb > _META_IDLE_DONE_S:
        return "done"

    if _META_WORKER_MAX_S > 0 and now - started > _META_WORKER_MAX_S:
        t["error"] = ("Reissleine: laeuft seit %d h und arbeitet nicht mehr."
                      % int((now - started) / 3600))
        return "error"
    return "running"

def _meta_status_card(owner, msid, tid, state, prompt, extra=""):

    if portal_channels is None or _chan_ctx is None:
        return
    if not hasattr(portal_channels, "bus_status"):
        return
    try:
        icon = {"pending": "\u2026", "starting": "\u25b6", "running": "\u2699",
                "done": "\u2713", "error": "\u2717"}.get(state, "\u2022")
        txt = "%s Auftrag %s — %s: %s" % (icon, str(tid), state, (prompt or "")[:160])
        if extra:
            txt += " · %s" % str(extra)[:200]
        portal_channels.bus_status(_chan_ctx(), owner, msid, "%s:%s" % (msid, tid), txt)
    except Exception:
        pass

def _meta_launch_worker(msid, tid):

    ms = _meta_load().get(msid)
    if not ms:
        return
    t = next((x for x in ms.get("tasks", []) if x.get("tid") == tid), None)
    if not t:
        return
    owner = ms.get("owner") or DEFAULT_PRINCIPAL
    gen0 = int(t.get("_gen") or 0)
    _rsid = t.get("resume_sid")
    if _rsid and _meta_work_delta(_rsid):
        sid, _tmux, err = _meta_resume_worker(ms, tid, t.get("prompt") or "", _rsid)
    else:
        sid, _tmux, err = _meta_spawn_worker(ms, tid, t.get("prompt") or "")
    retry = bool(err) and err.startswith(_META_RETRY_MARK)
    if retry:
        err = err[len(_META_RETRY_MARK):]
    orphan = []
    def _u(d):
        m = d.get(msid)
        if not m:
            return
        tt = next((x for x in m.get("tasks", []) if x.get("tid") == tid), None)
        if not tt:
            return
        if int(tt.get("_gen") or 0) != gen0:

            if sid:
                orphan.append(sid)
            return
        if tt.get("state") not in ("starting",):
            if sid:
                tt["sid"] = tt.get("sid") or sid; tt["owner"] = tt.get("owner") or owner
            return
        if sid:
            tt["state"] = "running"; tt["sid"] = sid; tt["owner"] = owner
            tt.pop("tmux", None)
            tt["_lastbusy"] = time.time(); tt.pop("retries", None); tt.pop("error", None)
            tt.pop("waiting", None); tt.pop("resume_sid", None); _verdacht_loeschen(tt)
        elif retry:

            n = int(tt.get("retries") or 0) + 1
            tt["retries"] = n; tt["error"] = err
            if n >= _META_RETRY_MAX:
                tt["state"] = "error"; tt["ended"] = time.time(); tt.pop("waiting", None)
            else:
                tt["state"] = "pending"; tt["retry_after"] = time.time() + _META_RETRY_BACKOFF
                tt.pop("started", None)

                tt["waiting"] = err or "wartet auf freien Arbeitsspeicher"
        else:
            tt["state"] = "error"; tt["error"] = err; tt["ended"] = time.time()
    _meta_update(_u)
    for _osid in orphan:
        _meta_cell_stop(owner, _osid)
    try:
        _m2 = _meta_load().get(msid) or {}
        _t2 = next((x for x in _m2.get("tasks", []) if x.get("tid") == tid), None)
        if _t2 is not None:
            _meta_status_card(_m2.get("owner") or owner, msid, tid, _t2.get("state"),
                              _t2.get("prompt"), _t2.get("error") or "")
    except Exception:
        pass

def _meta_tick():
    now = time.time()

    finished, marks, probe_notes = {}, {}, {}
    for msid, ms in (_meta_load() or {}).items():
        owner = ms.get("owner") or DEFAULT_PRINCIPAL
        for t in ms.get("tasks", []):
            if t.get("state") != "running":
                continue
            key = (msid, t.get("tid"))
            st = _worker_state(t, now, owner)
            marks[key] = t.get("_lastbusy")

            probe_notes[key] = {"_nojsonl_since": t.get("_nojsonl_since"),
                                "_notalive_since": t.get("_notalive_since")}
            if t.get("error"):
                probe_notes[key]["error"] = t["error"]
            if st != "running":

                res = _meta_worker_result(owner, t.get("sid"))
                _kein_ergebnis = _warum_kein_ergebnis(res)
                if st == "done" and _kein_ergebnis:

                    st = "error"
                    probe_notes.setdefault(key, {})["error"] = _kein_ergebnis
                finished[key] = (st, owner, t.get("sid"), res)
    to_launch = []
    def _f(d):
        for msid, ms in d.items():
            _mc = ms.get("max_concurrent")
            maxc = max(0, int(5 if _mc is None else _mc))
            tasks = ms.get("tasks", [])
            for t in tasks:
                if t.get("state") == "starting":

                    if now - float(t.get("started") or now) > _META_STARTING_MAX_S and not t.get("sid"):
                        t["_gen"] = int(t.get("_gen") or 0) + 1
                        t["state"] = "pending"; t.pop("started", None)
                        _verdacht_loeschen(t)
                        t["retry_after"] = now + _META_RETRY_BACKOFF
                        t["waiting"] = ("Start hat laenger als %d min gebraucht — neu eingereiht."
                                        % int(_META_STARTING_MAX_S / 60))
                    continue
                if t.get("state") != "running":
                    continue
                key = (msid, t.get("tid"))
                if marks.get(key):
                    t["_lastbusy"] = marks[key]
                _notes = probe_notes.get(key) or {}
                if _notes.get("_nojsonl_since"):
                    t["_nojsonl_since"] = _notes["_nojsonl_since"]
                elif "_nojsonl_since" in _notes:
                    t.pop("_nojsonl_since", None)
                if _notes.get("_notalive_since"):
                    t["_notalive_since"] = _notes["_notalive_since"]
                elif "_notalive_since" in _notes:
                    t.pop("_notalive_since", None)
                if _notes.get("error"):
                    t["error"] = _notes["error"]
                fin = finished.get(key)
                if fin:
                    t["state"] = fin[0]; t["ended"] = now
                    if fin[3] and not t.get("result"):
                        t["result"] = fin[3]

                    if t["state"] == "error" and _meta_work_delta(t.get("sid")):
                        n = int(t.get("resumes") or 0)
                        if n < _META_RESUME_MAX:
                            t["resumes"] = n + 1
                            t["resume_sid"] = t.get("sid")
                            ms.setdefault("_resume_reason", {})[t.get("tid")] = \
                                str(t.get("error") or "die Zelle wurde vorzeitig beendet")[:300]
                            t["state"] = "pending"; t.pop("ended", None); t.pop("started", None)
                            _verdacht_loeschen(t)
                            t["retry_after"] = now + _META_RETRY_BACKOFF
                            t["waiting"] = ("Wird fortgesetzt (Versuch %d/%d) — der Arbeitsstand der "
                                            "Zelle ist erhalten." % (n + 1, _META_RESUME_MAX))
            if ms.get("state") != "running":
                continue
            active = sum(1 for t in tasks if t.get("state") in ("running", "starting"))
            for t in tasks:
                if active >= maxc:
                    break
                if t.get("state") == "pending" and float(t.get("retry_after") or 0) <= now:
                    t["state"] = "starting"; t["started"] = now; t.pop("retry_after", None)
                    to_launch.append((msid, t["tid"])); active += 1
    _meta_update(_f)
    try:
        _d2 = _meta_load()
        for (_m, _t) in to_launch:
            _ms2 = _d2.get(_m) or {}
            _tt2 = next((x for x in _ms2.get("tasks", []) if x.get("tid") == _t), None)
            if _tt2 is not None:
                _meta_status_card(_ms2.get("owner") or DEFAULT_PRINCIPAL, _m, _t, "starting",
                                  _tt2.get("prompt"))
        for (_m, _t), (_st, _fowner, _fsid, _fres) in finished.items():
            _ms2 = _d2.get(_m) or {}
            _tt2 = next((x for x in _ms2.get("tasks", []) if x.get("tid") == _t), None)
            _meta_status_card(_fowner or DEFAULT_PRINCIPAL, _m, _t, _st, (_tt2 or {}).get("prompt"),
                              (_fres or "")[:200] if isinstance(_fres, str) else "")
    except Exception:
        pass

    for _key, (_st, _owner, _sid, _res) in finished.items():
        if _sid:
            _meta_cell_stop(_owner, _sid)
    for msid, tid in to_launch:
        threading.Thread(target=_meta_launch_worker, args=(msid, tid), daemon=True).start()

    _meta_sweep_orphans(now)

def _meta_sweep_orphans(now):

    global _meta_last_sweep
    if now - _META_BOOT_TS < _META_ADOPT_GRACE_S:
        return
    if now - _meta_last_sweep < _META_SWEEP_EVERY_S:
        return
    _meta_last_sweep = now
    d = _meta_load() or {}
    lebendig = set()
    kandidaten = {}
    for msid, ms in d.items():
        for t in ms.get("tasks", []):
            sid = t.get("sid")
            st = t.get("state")
            if st in ("pending", "starting", "running"):
                if sid:
                    lebendig.add(sid)
                if t.get("resume_sid"):
                    lebendig.add(t["resume_sid"])
            elif st in ("done", "error") and sid:
                ended = float(t.get("ended") or 0)
                if ended and now - ended > _META_SWEEP_GRACE_S:
                    kandidaten.setdefault(sid, (t.get("owner") or ms.get("owner") or DEFAULT_PRINCIPAL,
                                                ended, msid, t.get("tid")))
    for msid, ms in d.items():
        for t in ms.get("tasks", []):
            if t.get("state") in ("pending", "starting", "running"):
                lebendig.add(msid)
                break
    gestoppt = 0
    for sid, (owner, ended, msid, tid) in kandidaten.items():
        if gestoppt >= _META_SWEEP_MAX_PER_RUN:
            break
        if sid in lebendig:
            continue
        _n = int(_meta_sweep_versuche.get(sid) or 0)
        if _n >= _META_SWEEP_MAX_VERSUCHE:
            continue
        try:
            cell = _meta_cell(owner, sid)
            if cell is None or not cell.alive():
                _meta_sweep_versuche.pop(sid, None)
                continue
        except Exception:
            continue
        _meta_sweep_versuche[sid] = _n + 1
        try:
            import sys as _sys
            if _n + 1 >= _META_SWEEP_MAX_VERSUCHE:

                _sys.stderr.write("[meta-sweep] AUFGEGEBEN sid=%s (Auftrag %s/%s): drei Stop-Versuche "
                                  "ohne Wirkung — die Box kann diese Zelle nicht mehr stoppen. Falls "
                                  "sie remote laeuft, ueber den Knoten-Agenten abraeumen.\n"
                                  % (sid, msid, tid))
            else:
                _sys.stderr.write("[meta-sweep] stoppe verwaiste Worker-Zelle sid=%s (Auftrag %s/%s, "
                                  "terminal seit %.0f min, Versuch %d)\n"
                                  % (sid, msid, tid, (now - ended) / 60.0, _n + 1))
        except Exception:
            pass
        _meta_cell_stop(owner, sid)
        gestoppt += 1

def _kurz(s, n):

    s = str(s or "").strip()
    if len(s) <= n:
        return s
    return s[:n] + " …[+%d Zeichen]" % (len(s) - n)

def _transkript_ereignisse(lines, text_max=4000, tool_max=1000, erg_max=800):

    evs = []
    for ln in lines or []:
        ln = str(ln).strip()
        if not ln:
            continue
        try:
            ev = json.loads(ln)
        except Exception:
            continue
        if not isinstance(ev, dict):
            continue
        typ = ev.get("type")
        ts = ev.get("timestamp")
        if typ == "event_msg":
            pl = ev.get("payload") or {}
            pt = pl.get("type")
            if pt == "user_message":
                evs.append({"art": "nutzer", "ts": ts, "text": _kurz(pl.get("message"), text_max)})
            elif pt == "agent_message":
                evs.append({"art": "antwort", "ts": ts, "text": _kurz(pl.get("message"), text_max)})
            continue
        if typ == "response_item":
            pl = ev.get("payload") or {}
            pt = pl.get("type")
            if pt == "function_call":
                evs.append({"art": "werkzeug", "ts": ts, "werkzeug": pl.get("name") or "?",
                            "eingabe": _kurz(pl.get("arguments"), tool_max)})
            elif pt == "function_call_output":
                evs.append({"art": "ergebnis", "ts": ts, "fehler": False,
                            "text": _kurz(pl.get("output"), erg_max)})
            continue
        if typ not in ("user", "assistant") or ev.get("isSidechain"):
            continue
        msg = ev.get("message") or {}
        content = msg.get("content")
        meta = bool(ev.get("isMeta"))
        if isinstance(content, str):
            if content.strip() and not meta:
                evs.append({"art": ("antwort" if typ == "assistant" else "nutzer"),
                            "ts": ts, "text": _kurz(content, text_max)})
            continue
        if not isinstance(content, list):
            continue
        for blk in content:
            if not isinstance(blk, dict):
                continue
            bt = blk.get("type")
            if bt == "text" and (blk.get("text") or "").strip() and not meta:
                evs.append({"art": ("antwort" if typ == "assistant" else "nutzer"),
                            "ts": ts, "text": _kurz(blk.get("text"), text_max)})
            elif bt == "tool_use":
                try:
                    ein = json.dumps(blk.get("input"), ensure_ascii=False, sort_keys=True)
                except Exception:
                    ein = str(blk.get("input"))
                evs.append({"art": "werkzeug", "ts": ts, "werkzeug": blk.get("name") or "?",
                            "eingabe": _kurz(ein, tool_max)})
            elif bt == "tool_result":
                inhalt = blk.get("content")
                if isinstance(inhalt, list):
                    inhalt = "\n".join((b.get("text") or "") for b in inhalt
                                       if isinstance(b, dict) and b.get("type") == "text")
                evs.append({"art": "ergebnis", "ts": ts, "fehler": bool(blk.get("is_error")),
                            "text": _kurz(inhalt, erg_max)})
    return evs

def _watch_wiederholung_falten(zeilen):

    aus = []
    for z in zeilen or []:
        if aus and aus[-1][0] == z:
            aus[-1][1] += 1
        else:
            aus.append([z, 1])
    return [z if n == 1 else "%s   (%d\u00d7 wiederholt)" % (z, n) for z, n in aus]

def _watch_kern(bloecke):

    import re as _re
    return "\n".join(_re.sub(r"\(\d+ min\)", "(min)", b) for b in bloecke or [])

def _watch_digest_zeilen(evs, limit=_META_WATCH_CHILD_CHARS):

    zeilen = []
    for ev in evs or []:
        art = ev.get("art")
        if art == "werkzeug":
            zeilen.append("  🔧 %s %s" % (ev.get("werkzeug") or "?",
                                          " ".join(_kurz(ev.get("eingabe"), 220).split())))
        elif art == "ergebnis":
            z = " ".join(_kurz(ev.get("text"), 160).split())
            if ev.get("fehler"):
                zeilen.append("  ⚠️ Werkzeug-FEHLER: %s" % (z or "(ohne Text)"))
            elif z:
                zeilen.append("  ↳ %s" % z)
        elif art == "antwort":
            zeilen.append("  💬 %s" % " ".join(_kurz(ev.get("text"), 500).split()))
        elif art == "nutzer":
            zeilen.append("  📨 %s" % " ".join(_kurz(ev.get("text"), 200).split()))
    zeilen = _watch_wiederholung_falten(zeilen)
    out, used = [], 0
    for z in reversed(zeilen):
        if out and used + len(z) > limit:
            break
        out.append(z); used += len(z) + 1
    weg = len(zeilen) - len(out)
    out.reverse()
    if weg > 0:
        out.insert(0, "  … %d aeltere Ereignisse ausgelassen (session_transcript zeigt alles)" % weg)
    return out

def _watch_offsets_speichern(msid, offsets):

    if not offsets:
        return
    def _u(d):
        ms = d.get(msid)
        if not ms:
            return
        for t in ms.get("tasks", []):
            o = offsets.get(t.get("tid"))
            if o:
                t["_watch"] = o
    try:
        _meta_update(_u)
    except Exception:
        _traceback_log("watch offsets")

def _watch_fehler_tids(bloecke):

    raus = set()
    for b in bloecke or ():
        if "Werkzeug-FEHLER" not in b:
            continue
        kopf = (b.split("\n", 1)[0] or "").lstrip("■ ").strip()
        tid = kopf.split(" ", 1)[0].strip()
        if tid:
            raus.add(tid)
    return raus

def _watch_fehler_merken(msid, tids):

    def _u(d):
        ms = d.get(msid)
        if ms is not None:
            ms["_watch_fehler"] = list(tids or ())
    try:
        _meta_update(_u)
    except Exception:
        _traceback_log("watch fehler-merker")

def _watch_kern_merken(msid, kern):

    def _u(d):
        ms = d.get(msid)
        if ms is not None:
            ms["_watch_kern"] = kern
    try:
        _meta_update(_u)
    except Exception:
        _traceback_log("watch kern-merker")

def _meta_watch_tick():

    d = _meta_load() or {}
    now = time.time()
    for msid, ms in d.items():
        if ms.get("watch") is False:
            continue
        owner = ms.get("owner") or DEFAULT_PRINCIPAL
        kinder = [t for t in ms.get("tasks", []) if t.get("state") == "running" and t.get("sid")]
        if not kinder:
            continue
        lead = _meta_cell(owner, msid)
        if lead is None or not lead.alive():
            continue
        bloecke, offsets = [], {}
        for t in kinder:
            cell = _meta_cell(owner, t["sid"])
            if cell is None or not cell.alive():
                continue
            w = t.get("_watch") or {}
            try:
                path = cell._incell_active_jsonl()
                if not path:
                    continue
                if w.get("path") == path:
                    off = int(w.get("off") or 0)
                else:
                    sz = cell._incell_jsonl_size(path)
                    off = max(0, sz - _META_WATCH_FIRST_TAIL)
                tl = cell.transcript_tail(off=off, maxbytes=_META_WATCH_READ_MAX, path=path)
            except Exception:
                continue
            if tl.get("path"):
                offsets[t.get("tid")] = {"path": tl["path"], "off": int(tl.get("off") or off)}
            if not tl.get("lines"):
                continue
            zeilen = _watch_digest_zeilen(
                _transkript_ereignisse(tl["lines"], text_max=600, tool_max=300, erg_max=300))
            if zeilen:
                mins = int((now - float(t.get("started") or now)) / 60)
                bloecke.append("■ %s (%d min):\n%s" % (t.get("tid"), mins, "\n".join(zeilen)))
        if not bloecke:
            _watch_offsets_speichern(msid, offsets)
            continue

        _kern = _watch_kern(bloecke)
        _fehler_jetzt = _watch_fehler_tids(bloecke)
        _fehler_vorher = set(ms.get("_watch_fehler") or ())

        _befund = sorted(_fehler_jetzt & _fehler_vorher)
        _watch_fehler_merken(msid, sorted(_fehler_jetzt))

        if _kern == (ms.get("_watch_kern") or "") and not _befund:
            _watch_offsets_speichern(msid, offsets)
            continue

        if (_META_WATCH_MODE == "fehler" and ms.get("watch") is not True and not _befund):
            _watch_offsets_speichern(msid, offsets)
            continue
        msg = ("[AUTO-AUFSICHT] %s\n" % (
                   ("BEFUND — seit mindestens zwei Takten unbehobene Werkzeug-Fehler bei: %s"
                    % ", ".join(_befund)) if _befund
                   else "Neue Aktivitaet deiner Sub-Sessions (automatischer Bericht):")
               + "\n".join(bloecke)
               + "\nDetails liefert portalctl session_transcript '{\"tid\":\"…\"}'; korrigiere per "
                 "session_tell, stoppe per session_stop. ANTWORTE NUR, WENN DU EINGREIFST — laeuft "
                 "alles auf Kurs, tu nichts. Ein bestaetigendes „OK\" ist ausdruecklich NICHT "
                 "erwuenscht, es kostet einen vollen Zug.")
        if len(msg) > _META_WATCH_MSG_CHARS:
            msg = msg[:_META_WATCH_MSG_CHARS] + "\n… gekuerzt — session_transcript zeigt alles."
        try:
            _watch_kern_merken(msid, _kern)
            zugestellt = bool(lead.submit(msg))
        except Exception:
            _traceback_log("watch submit")
            zugestellt = False
        if zugestellt:
            _watch_offsets_speichern(msid, offsets)
            _META_PROBE.pop((owner, msid), None)

def _meta_watch_worker():

    def loop():
        time.sleep(min(_META_ADOPT_GRACE_S, 180.0))
        while True:
            try:

                _meta_neustart_melden()
            except Exception:
                _traceback_log("metasession neustart-meldung")
            try:
                if _META_WATCH:
                    _meta_watch_tick()
            except Exception:
                _traceback_log("metasession watch")
            time.sleep(max(15.0, _META_WATCH_EVERY_S))
    threading.Thread(target=loop, daemon=True).start()

_VOR_NEUSTART = "_vor_neustart"

def _zeit_lesbar(ts):

    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return "unbekannt"
    if ts <= 0:
        return "unbekannt"
    wann = time.strftime("%d.%m. %H:%M", time.localtime(ts))
    d = max(0, int(time.time() - ts))
    if d < 90:
        return "%s (vor %d s)" % (wann, d)
    if d < 5400:
        return "%s (vor %d min)" % (wann, d // 60)
    return "%s (vor %d h %d min)" % (wann, d // 3600, (d % 3600) // 60)

def _meta_snapshot_beim_start():

    jetzt = time.time()
    def _u(d):
        for msid, ms in (d or {}).items():
            aktiv = [t for t in (ms.get("tasks") or [])
                     if t.get("state") in ("running", "starting")]
            if not aktiv:

                continue
            kinder = []
            for t in aktiv:
                nid = None
                try:
                    if callable(_sessprov_get) and t.get("sid"):
                        nid = ((_sessprov_get(ms.get("owner") or DEFAULT_PRINCIPAL, t["sid"]) or {})
                               .get("node")) or None
                except Exception:
                    nid = None

                _erste = next((z.strip() for z in str(t.get("prompt") or "").splitlines()
                               if z.strip()), "")
                kinder.append({"tid": t.get("tid"), "sid": t.get("sid"),
                               "knoten": nid or "box",
                               "zustand_davor": t.get("state"),
                               "gestartet": t.get("started"),
                               "zuletzt_aktiv": t.get("_lastbusy"),
                               "aufgabe": _erste[:160]})
            ms[_VOR_NEUSTART] = {"portal_start": jetzt, "kinder": kinder, "gemeldet": False}
    try:
        _meta_update(_u)
    except Exception:
        _traceback_log("meta snapshot start")

def _vor_neustart_text(ms, kopf=True):

    snap = (ms or {}).get(_VOR_NEUSTART) or {}
    kinder = snap.get("kinder") or []
    if not kinder:
        return ""
    jetzt_map = {t.get("tid"): t for t in (ms.get("tasks") or [])}
    zeilen = []
    for k in kinder:
        t = jetzt_map.get(k.get("tid")) or {}
        zeilen.append(
            "  • %s  Zelle %s auf %s  |  Aufgabe: %s\n"
            "      lief seit %s, letzte Aktivitaet %s  |  Zustand davor: %s  →  JETZT: %s"
            % (k.get("tid"), k.get("sid") or "—", k.get("knoten") or "?",
               " ".join(str(k.get("aufgabe") or "").split())[:110],
               _zeit_lesbar(k.get("gestartet")), _zeit_lesbar(k.get("zuletzt_aktiv")),
               k.get("zustand_davor") or "?", t.get("state") or "nicht mehr in der Liste"))
    if not kopf:
        return "\n".join(zeilen)
    return "\n".join([
        "AKTIVE SUB-SESSIONS VOR DEM LETZTEN NEUSTART (%s) — %d Stueck:"
        % (_zeit_lesbar(snap.get("portal_start")), len(kinder)),
        "\n".join(zeilen),
        "SO BELEBST DU SIE WIEDER — ZUERST SCHAUEN, DANN HOLEN: Zellen auf den Knoten ueberstehen "
        "einen Portal-Neustart inzwischen und werden automatisch wieder uebernommen (im Log: "
        "'REMOTE wieder uebernommen, Gast antwortet'). Ein Rechenjob auf dem Cluster laeuft "
        "ohnehin unbeirrt weiter. Frag deshalb ZUERST `portalctl session_status`: was dort laeuft, "
        "LAEUFT — fass es nicht an. Ein Neustart eines gesunden Raums bringt nichts und kostet den "
        "Faden des Agenten. Nur was NICHT mehr laeuft, holst du mit "
        "`portalctl session_restart '{\"tid\":\"<tid>\",\"reason\":\"...\"}'` zurueck — die Zelle faehrt "
        "aus ihrem Arbeitsstand hoch und der Agent macht MIT SEINEM GEDAECHTNIS weiter. (Der Aufruf "
        "laesst lebende Zellen seit 02.08. ohnehin in Ruhe und meldet sie als 'laeuft schon' "
        "zurueck; ein wirklich noetiger Neustart braucht `erzwingen: true`.) "
        "Pruefe danach mit `session_transcript`, ob das Kind seinen Faden wirklich wieder aufgenommen "
        "hat — nicht nur, ob die Zelle laeuft. Oft ist die Rechnung laengst fertig und es fehlt nur "
        "das Einsammeln und Veroeffentlichen.",
    ])

def _meta_neustart_melden():

    d = _meta_load() or {}
    for msid, ms in d.items():
        snap = ms.get(_VOR_NEUSTART) or {}
        if not snap.get("kinder") or snap.get("gemeldet"):
            continue
        lead = _meta_cell(ms.get("owner") or DEFAULT_PRINCIPAL, msid)
        if lead is None or not lead.alive():
            continue
        text = ("[NACH DEM NEUSTART] Das Portal wurde neu gestartet. Diese deiner Sub-Sessions "
                "waren davor aktiv — pruefe jede und belebe die offenen wieder:\n"
                + _vor_neustart_text(ms))
        try:
            ok = bool(lead.submit(text))
        except Exception:
            _traceback_log("neustart melden")
            ok = False
        if ok:
            def _mark(dd, _m=msid):
                s = (dd.get(_m) or {}).get(_VOR_NEUSTART)
                if isinstance(s, dict):
                    s["gemeldet"] = True
            _meta_update(_mark)
            _META_PROBE.pop((ms.get("owner") or DEFAULT_PRINCIPAL, msid), None)

def _metasession_worker():

    try:
        _meta_snapshot_beim_start()
    except Exception:
        _traceback_log("metasession snapshot")
    def loop():
        while True:
            try:
                _meta_tick()
            except Exception:
                _traceback_log("metasession tick")
            time.sleep(5)
    threading.Thread(target=loop, daemon=True).start()
    _meta_watch_worker()

_NODE_VOL_EVERY_S = int(os.environ.get("PN_NODE_VOL_SWEEP_S", "1800"))
_NODE_VOL_GRACE_MIN = int(os.environ.get("PN_NODE_VOL_GRACE_MIN", "15"))

def _node_exec_text(nid, script, timeout=120):

    try:
        import portal_placement as _pp
        ep, tk = _pp.node_endpoint(nid), _pp.node_token(nid)
        if not ep or not tk:
            return None
        import urllib.request
        kopf = {"X-Node-Token": tk, "Content-Type": "application/json"}
        b = json.dumps({"argv": ["bash", "-lc", script], "timeout_s": timeout}).encode()
        r = urllib.request.Request(ep + "/exec", data=b, method="POST", headers=kopf)
        jid = json.loads(urllib.request.urlopen(r, timeout=20).read().decode()).get("job_id")
        if not jid:
            return None
        for _ in range(int(timeout / 2) + 5):
            time.sleep(2)
            r2 = urllib.request.Request(ep + "/jobs/" + str(jid), headers={"X-Node-Token": tk})
            st = json.loads(urllib.request.urlopen(r2, timeout=15).read().decode())
            if st.get("state") in ("done", "error", "canceled"):
                break
        r3 = urllib.request.Request(ep + "/jobs/%s/out?tail=4000" % jid,
                                    headers={"X-Node-Token": tk})
        return urllib.request.urlopen(r3, timeout=15).read().decode("utf-8", "replace")
    except Exception:
        return None

def _meta_new_tid(ms):
    n = len(ms.get("tasks", []))
    return "t%03d%s" % (n, secrets.token_hex(3))

def _meta_ensure_for_session(owner, sid, prov):

    prov = prov or {}
    on = bool(prov.get("orchestrator"))
    try:
        _mc = prov.get("max_concurrent")
        maxc = max(0, min(64, int(5 if _mc is None else _mc)))
    except Exception:
        maxc = 5
    try:
        caps = (_sess_policy_get(owner, sid) or {}).get("caps", {}) or {}
    except Exception:
        caps = {}
    try:
        auton = max(0, min(5, int(prov.get("autonomy") or 3)))
    except Exception:
        auton = 3
    tpl = {"model": prov.get("model"), "effort": prov.get("effort"), "autonomy": auton,
           "preset": prov.get("preset"), "vpn": prov.get("vpn"),
           "vpn_dauerjob": bool(prov.get("vpn_dauerjob") or prov.get("vpn")), "caps": caps,

           "worker_disk_mb": prov.get("worker_disk_mb"),
           "worker_mem_mb": prov.get("worker_mem_mb")}
    title = prov.get("title")
    if not title:
        try:
            _rec = next((r for r in _session_store(owner, "cockpit").list()
                         if r.get("id") == sid), None)
            title = (_rec or {}).get("title")
        except Exception:
            title = None
    title = title or sid
    def _u(d):
        cur = d.get(sid)
        if not on:
            if cur:
                if cur.get("tasks"):
                    cur["state"] = "paused"
                else:
                    d.pop(sid, None)
            return
        if cur:
            cur["state"] = "running"; cur["max_concurrent"] = maxc; cur["template"] = tpl
            cur["owner"] = owner; cur["title"] = title; cur["lead_sid"] = sid; cur["id"] = sid
        else:
            d[sid] = {"id": sid, "title": title, "owner": owner, "state": "running",
                      "template": tpl, "max_concurrent": maxc, "created": time.time(),
                      "tasks": [], "lead_sid": sid}
    _meta_update(_u)

_ORCH_MAX_DEPTH = 2

def _orch_has_right(owner, sid):

    try:
        caps = (_sess_policy_get(owner, sid) or {}).get("caps", {}) or {}
        return caps.get("orchestrate") == "allow"
    except Exception:
        return False

def _orch_depth(owner, sid):

    try:
        return max(0, int((_sessprov_get(owner, sid) or {}).get("orch_depth") or 0))
    except Exception:
        return 0

def _orch_default_model():

    return os.environ.get("PN_DEFAULT_MODEL") or "sonnet"

def _orch_template(owner, sid):

    try:
        prov = _sessprov_get(owner, sid) or {}
    except Exception:
        prov = {}
    try:
        caps = (_sess_policy_get(owner, sid) or {}).get("caps", {}) or {}
    except Exception:
        caps = {}
    try:
        auton = max(0, min(5, int(prov.get("autonomy") or 3)))
    except Exception:
        auton = 3
    return {"model": prov.get("model") or _orch_default_model(), "effort": prov.get("effort") or "medium",
            "autonomy": auton, "preset": prov.get("preset"), "vpn": prov.get("vpn"),
            "vpn_dauerjob": bool(prov.get("vpn_dauerjob") or prov.get("vpn")),
            "caps": caps,
            "title": prov.get("title"), "child_depth": _orch_depth(owner, sid) + 1,

            "worker_disk_mb": prov.get("worker_disk_mb"),
            "worker_mem_mb": prov.get("worker_mem_mb")}

_META_MODELLSTUFEN = ("sonnet", "opus", "haiku")

_META_CHILD_MODEL = (os.environ.get("PN_META_CHILD_MODEL") or "sonnet").strip().lower()

def _meta_modellstufe(wunsch, vorgabe=None):

    w = str(wunsch or "").strip().lower()
    if w in _META_MODELLSTUFEN:
        return w
    return vorgabe

def orch_spawn(owner, sid, task, title=None, model=None):

    task = str(task or "").strip()
    if not sid:
        return ({"ok": False, "error": "no_session"},
                "Ich kann die aufrufende Sitzung nicht bestimmen -- von hier aus wird nichts gestartet.", False)
    if not _orch_has_right(owner, sid):
        return ({"ok": False, "error": "orchestrate_denied"},
                "Diese Sitzung hat kein Orchestrator-Recht und darf keine Sub-Sessions starten. Das "
                "Recht wird in den Rechten der Sitzung freigegeben (Orchestrator).", False)
    if not task:
        return ({"ok": False, "error": "empty_task"},
                "Bitte gib die Aufgabe fuer die Sub-Session vollstaendig an.", False)
    depth = _orch_depth(owner, sid)
    if depth >= _ORCH_MAX_DEPTH:
        return ({"ok": False, "error": "max_depth", "depth": depth, "max": _ORCH_MAX_DEPTH},
                "Diese Sitzung ist bereits %d Ebenen tief gespawnt (Grenze %d). Tiefer werden keine "
                "weiteren Sub-Sessions gestartet, damit keine Lawine entsteht." % (depth, _ORCH_MAX_DEPTH),
                False)
    tpl = _orch_template(owner, sid)
    ttl = (str(title).strip()[:120] if title else None)
    added = {"tid": None}
    def _u(d):
        ms = d.get(sid)
        if not ms:
            try:
                _mc = (_sessprov_get(owner, sid) or {}).get("max_concurrent")
                maxc = max(0, min(64, int(5 if _mc is None else _mc)))
            except Exception:
                maxc = 5
            ms = {"id": sid, "title": (ttl or tpl.get("title") or sid), "owner": owner,
                  "state": "running", "template": tpl, "max_concurrent": maxc, "created": time.time(),
                  "tasks": [], "lead_sid": sid, "depth": depth, "child_depth": depth + 1}
            d[sid] = ms
        else:
            ms["state"] = "running"; ms["template"] = tpl; ms["owner"] = owner
            ms["depth"] = depth; ms["child_depth"] = depth + 1
        tid = _meta_new_tid(ms)
        _auf = {"tid": tid, "prompt": task, "state": "pending"}
        _stufe = _meta_modellstufe(model)
        if _stufe:
            _auf["model"] = _stufe
        ms["tasks"].append(_auf)
        added["tid"] = tid
    _meta_update(_u)
    return ({"ok": True, "tid": added["tid"], "queued": True,
             "counts": _meta_counts(_meta_load().get(sid, {}))},
            "Aufgabe angenommen -- die Sub-Session startet, sobald die Box Platz hat (sonst wartet sie).",
            True)

def orch_status(owner, sid):

    ms = (_meta_load() or {}).get(sid)
    if not ms or ms.get("owner") != owner:
        return ({"ok": True, "tasks": [], "counts": {"pending": 0, "running": 0, "done": 0, "error": 0}},
                "Diese Sitzung hat noch keine Sub-Sessions gestartet.", True)
    now = time.time()
    tasks = [{"tid": t.get("tid"), "task": (t.get("prompt") or "")[:200], "state": t.get("state"),

              "sid": t.get("sid"), "result": (t.get("result") or "")[:_META_RESULT_MAX],
              "error": t.get("error"),

              "waiting": t.get("waiting"), "retries": t.get("retries"),
              "retry_in_s": (max(0, int(float(t.get("retry_after") or 0) - now))
                             if t.get("retry_after") else None),
              "runtime_s": (int(now - float(t["started"])) if t.get("started") else None),

              "still_s": (int(now - float(t["_lastbusy"])) if t.get("_lastbusy") else None)}
             for t in ms.get("tasks", [])]
    c = _meta_counts(ms)

    spoken = "Sub-Sessions: %d laufen, %d starten, %d warten, %d fertig, %d Fehler." % (
        c.get("running", 0), c.get("starting", 0), c.get("pending", 0),
        c.get("done", 0), c.get("error", 0))
    _w = [t for t in ms.get("tasks", []) if t.get("state") == "pending" and t.get("waiting")]
    if _w:
        spoken += " Wartegrund: %s" % str(_w[0].get("waiting"))[:160]

    _still = sorted(((int(now - float(t["_lastbusy"])), t.get("tid"))
                     for t in ms.get("tasks", [])
                     if t.get("state") == "running" and t.get("_lastbusy")
                     and now - float(t["_lastbusy"]) > 600), reverse=True)
    if _still:
        spoken += " STILL seit >10 min (anstossen mit session_tell): %s" % ", ".join(
            "%s (%d min)" % (tid, sek // 60) for sek, tid in _still[:5])

    _geister = []
    for t in ms.get("tasks", []):
        if t.get("state") in ("running", "starting") and t.get("sid"):
            try:
                _c = _meta_cell(ms.get("owner") or owner, t["sid"])
                if _c is None or not _c.alive():
                    _geister.append(t.get("tid"))
            except Exception:
                pass
    if _geister:
        spoken += (" GEISTER (%d Auftraege zeigen 'running', ihre Zelle lebt aber nicht mehr — "
                   "warte NICHT auf eine Umstufung, hol sie sofort mit session_restart und dem tid "
                   "zurueck): %s" % (len(_geister), ", ".join(_geister[:8])
                                     + (" …" if len(_geister) > 8 else "")))
    _fort = [t.get("tid") for t in ms.get("tasks", [])
             if t.get("state") == "error" and t.get("sid") and _meta_work_delta(t.get("sid"))]
    if _fort:
        spoken += (" FORTSETZBAR (%d gescheiterte Raeume haben ihren Arbeitsstand noch — "
                   "session_restart mit dem tid holt sie zurueck): %s"
                   % (len(_fort), ", ".join(_fort[:8]) + (" …" if len(_fort) > 8 else "")))
    return ({"ok": True, "tasks": tasks, "counts": c, "max_concurrent": ms.get("max_concurrent"),

             "watch": not (ms.get("watch") is False),

             "geister": _geister,

             "vor_neustart": ms.get(_VOR_NEUSTART) or None,

             "fortsetzbar": [t.get("tid") for t in ms.get("tasks", [])
                             if t.get("state") not in ("done", "running", "starting")
                             and t.get("sid") and _meta_work_delta(t.get("sid"))],

             "fortsetzbar_lebt": _fortsetzbar_lebt(ms)},
            spoken, True)

def orch_tell(owner, sid, tid, text):

    ok, why = meta_say(sid, str(tid or ""), text)
    if ok:
        return ({"ok": True, "tid": tid}, "Nachricht an die Sub-Session geschickt.", True)
    return ({"ok": False, "error": why}, why or "Konnte die Nachricht nicht zustellen.", False)

def orch_broadcast(owner, sid, text):

    text = str(text or "").strip()
    if not text:
        return ({"ok": False, "error": "empty_text"}, "Bitte gib den Text an, der an alle soll.", False)
    ms = (_meta_load() or {}).get(sid)
    if not ms or ms.get("owner") != owner:
        return ({"ok": True, "sent": 0, "results": []},
                "Diese Sitzung hat keine Sub-Sessions.", True)

    zu, aus = [], []
    for t in ms.get("tasks", []):

        if t.get("state") in ("running", "starting") and t.get("sid"):
            ok, why = meta_say(sid, t.get("tid"), text)
            (zu if ok else aus).append({"tid": t.get("tid"), "ok": ok, "reason": None if ok else why})
        else:
            aus.append({"tid": t.get("tid"), "ok": False,
                        "reason": "Zustand '%s' — keine laufende Zelle." % (t.get("state") or "?")})
    spoken = "An %d laufende Sub-Session(s) zugestellt." % len(zu)
    if aus:
        spoken += " %d nicht erreicht (%s)." % (len(aus), (aus[0].get("reason") or "")[:80])
    return ({"ok": True, "sent": len(zu), "missed": len(aus), "results": zu + aus}, spoken, True)

def orch_transcript(owner, sid, tid, ab=None, kb=None):

    ms = (_meta_load() or {}).get(sid)
    if not ms or ms.get("owner") != owner:
        return ({"ok": False, "error": "no_children"},
                "Diese Sitzung hat keine Sub-Sessions.", False)
    tid = str(tid or "").strip()
    t = next((x for x in ms.get("tasks", []) if x.get("tid") == tid), None)
    if not t:
        return ({"ok": False, "error": "unknown_tid"},
                "Kein Auftrag mit der Kennung %r in dieser Sitzung." % tid, False)
    child = t.get("sid")
    if not child:
        return ({"ok": False, "error": "no_cell", "state": t.get("state")},
                "Der Auftrag ist im Zustand '%s' — er hat (noch) keine Zelle und damit kein "
                "Transkript." % (t.get("state") or "?"), False)
    o = ms.get("owner") or owner
    cell = _meta_cell(o, child)
    if cell is None or not cell.alive():
        hint = (" Sein Arbeitsstand liegt noch — session_restart mit dieser tid faehrt die Zelle "
                "wieder hoch, danach ist das Transkript lesbar." if _meta_work_delta(child) else "")
        return ({"ok": False, "error": "cell_down", "state": t.get("state")},
                "Die Zelle dieses Kindes laeuft nicht mehr; ihr Transkript ist von hier aus gerade "
                "nicht lesbar.%s" % hint, False)
    try:
        kb = max(4, min(200, int(kb)))
    except (TypeError, ValueError):
        kb = 60
    try:
        path = cell._incell_active_jsonl()
        if not path:
            return ({"ok": False, "error": "no_jsonl"},
                    "Die Zelle hat noch keinen Gespraechs-Log — der Agent ist dort noch nicht "
                    "angelaufen.", False)
        size = cell._incell_jsonl_size(path)
        try:
            start = max(0, min(int(ab), size))
        except (TypeError, ValueError):
            start = max(0, size - kb * 1024)
        tl = cell.transcript_tail(off=start, maxbytes=kb * 1024, path=path)
    except Exception as e:
        _traceback_log("orch transcript")
        return ({"ok": False, "error": str(e)[:200]},
                "Das Transkript liess sich gerade nicht lesen (Lane-Stoerung?) — versuch es gleich "
                "noch einmal.", False)
    evs = _transkript_ereignisse(tl.get("lines") or [])
    ende = int(tl.get("off") or start)
    rest = max(0, int(tl.get("size") or size) - ende)
    spoken = ("Transkript von %s: %d Ereignisse (Bytes %d–%d von %d)."
              % (tid, len(evs), start, ende, int(tl.get("size") or size)))
    if rest:
        spoken += " Es gibt noch %d Bytes weiter — naechste Seite mit ab=%d." % (rest, ende)
    return ({"ok": True, "tid": tid, "sid": child, "state": t.get("state"),
             "log": tl.get("path"), "groesse": int(tl.get("size") or size), "ab": start,
             "weiter_ab": (ende if rest else None), "ereignisse": evs}, spoken, True)

def orch_watch(owner, sid, modus=None):

    m = str(modus or "").strip().lower()
    if m in ("an", "on", "ein", "true", "1"):
        wert = True
    elif m in ("aus", "off", "false", "0"):
        wert = False
    elif m in ("", "status"):
        wert = None
    else:
        return ({"ok": False, "error": "bad_mode"},
                "Unbekannter Modus %r — nimm 'an', 'aus' oder 'status'." % m, False)
    ms = (_meta_load() or {}).get(sid)
    if wert is None:
        w = ms.get("watch") if ms else None
        an = w is not False
        if w is False:
            zustand = "aus"
            gesagt = "Auto-Aufsicht ist AUS — session_transcript und session_status bleiben dir."
        elif w is True or _META_WATCH_MODE == "immer":
            zustand = "immer"
            gesagt = ("Auto-Aufsicht ist AN: neue Aktivitaet deiner Kinder wird dir etwa alle %d "
                      "Sekunden gebuendelt eingespielt." % int(_META_WATCH_EVERY_S))
        else:
            zustand = "fehler"
            gesagt = ("Auto-Aufsicht laeuft im Sparmodus: automatisch eingespielt wird nur ein "
                      "Digest mit Werkzeug-FEHLER (geprueft etwa alle %d Sekunden). "
                      "'session_watch an' bestellt das Immer-Verhalten zurueck."
                      % int(_META_WATCH_EVERY_S))
        return ({"ok": True, "watch": an, "modus": zustand,
                 "every_s": int(_META_WATCH_EVERY_S)}, gesagt, True)
    if not ms or ms.get("owner") != owner:

        def _neu(d):
            cur = d.get(sid)
            if cur is None:
                d[sid] = {"id": sid, "title": sid, "owner": owner, "state": "running",
                          "created": time.time(), "tasks": [], "lead_sid": sid, "watch": wert}
            else:
                cur["watch"] = wert
        _meta_update(_neu)
    else:
        def _set(d):
            cur = d.get(sid)
            if cur is not None:
                cur["watch"] = wert
        _meta_update(_set)
    return ({"ok": True, "watch": wert, "modus": ("immer" if wert else "aus"),
             "every_s": int(_META_WATCH_EVERY_S)},
            ("Auto-Aufsicht auf IMMER gestellt: jede neue Aktivitaet deiner Kinder kommt ab jetzt "
             "automatisch, etwa alle %d Sekunden gebuendelt." % int(_META_WATCH_EVERY_S)) if wert
            else "Auto-Aufsicht ausgeschaltet — hol dir den Stand selbst per session_status und "
                 "session_transcript.", True)

RESIZE_DISK_FREE_MB = 5 * 1024
RESIZE_MEM_HEADROOM_MB = 2 * 1024
RESIZE_DISK_HARD_MB = 16384
RESIZE_MEM_HARD_MB = 12288
_RESIZE_INTENTS = os.path.join(DATA_DIR, "resize-approvals.json")
_RESIZE_LOCK = threading.Lock()

def _resize_mem_base_mb():

    try:
        return max(512, int(os.environ.get("PN_CELL_MEM_MB", "1536")))
    except (TypeError, ValueError):
        return 1536

def _resize_intent_update(fn):

    with _RESIZE_LOCK:
        try:
            with open(_RESIZE_INTENTS) as f:
                d = json.load(f)
            if not isinstance(d, dict):
                d = {}
        except Exception:
            d = {}
        r = fn(d)

        try:
            now = time.time()
            for k in [k for k, v in d.items()
                      if not isinstance(v, dict) or now - float(v.get("created") or 0) > 7 * 86400]:
                d.pop(k, None)
        except Exception:
            pass
        try:
            tmp = _RESIZE_INTENTS + ".tmp"
            with open(tmp, "w") as f:
                json.dump(d, f)
            os.replace(tmp, _RESIZE_INTENTS)
        except Exception:
            _traceback_log("resize intents save")
        return r

def _resize_intent_put(aid, rec):
    _resize_intent_update(lambda d: d.__setitem__(str(aid), rec))

def _resize_intent_take(aid):

    box = {}

    def _u(d):
        r = d.pop(str(aid), None)
        if r:
            box["r"] = r
    _resize_intent_update(_u)
    return box.get("r")

def _resize_targets(ms, sid, tid):

    ziel = str(tid or "").strip().lower()
    if ziel == "new":
        return [], True, "kuenftige Kinder"
    if ziel == "*":
        kids = [(t.get("sid"), t) for t in (ms.get("tasks") or [])
                if t.get("sid") and t.get("state") in ("running", "starting")]
        return kids, True, "alle laufenden Kinder"
    if not ziel or ziel in ("self", "selbst"):
        return [(sid, None)], False, "die eigene Zelle"
    t = next((x for x in (ms.get("tasks") or []) if x.get("tid") == str(tid).strip()), None)
    if not t:
        return None, False, str(tid)
    if not t.get("sid"):
        return [], False, str(tid)
    return [(t.get("sid"), t)], False, str(tid)

def orch_resize(owner, sid, tid=None, disk_gb=None, mem_mb=None, reason=None,
                approval=None, restart=None):

    ms = (_meta_load() or {}).get(sid)
    if not ms or ms.get("owner") != owner:
        return ({"ok": False, "error": "no_orchestrator"},
                "Das kann nur eine Session mit Orchestrator-Recht.", False)
    ziele, auch_vorlage, label = _resize_targets(ms, sid, tid)
    if ziele is None:
        return ({"ok": False, "error": "unknown_tid"},
                "Kein Kind mit der Kennung %s — die tid steht in session_status." % label, False)

    want_disk = want_mem = None
    if disk_gb is not None:
        try:
            want_disk = int(round(float(disk_gb) * 1024))
        except (TypeError, ValueError):
            return ({"ok": False, "error": "bad_disk_gb"}, "disk_gb muss eine Zahl sein.", False)
    if mem_mb is not None:
        try:
            want_mem = int(mem_mb)
        except (TypeError, ValueError):
            return ({"ok": False, "error": "bad_mem_mb"}, "mem_mb muss eine Zahl sein.", False)
    if want_disk is None and want_mem is None:
        return ({"ok": False, "error": "nothing_requested"},
                "Sag, was groesser werden soll: disk_gb und/oder mem_mb.", False)

    granted = None
    if approval:
        granted = _resize_redeem(owner, sid, str(approval))
        if not granted:
            return ({"ok": False, "error": "approval_invalid"},
                    "Diese Freigabe gilt nicht (unbekannt, abgelehnt, schon benutzt oder noch "
                    "offen). Frag den Zustand mit ask_owner_result ab.", False)

        want_disk = granted.get("disk_mb", want_disk)
        want_mem = granted.get("mem_mb", want_mem)
        if granted.get("scope"):
            ziele, auch_vorlage, label = _resize_targets(ms, sid, granted["scope"])
            if ziele is None:
                ziele, auch_vorlage = [], True

    cur = {}
    for _s, _t in ziele:
        p = _sessprov_get(owner, _s) or {}
        for k in ("disk_mb", "mem_mb"):
            if p.get(k) and int(p[k]) > int(cur.get(k) or 0):
                cur[k] = int(p[k])
    mem_base = _resize_mem_base_mb()
    mem_free_cap = mem_base + RESIZE_MEM_HEADROOM_MB
    patch, notes, need = {}, [], {}

    if want_disk is not None:
        want_disk = max(512, min(RESIZE_DISK_HARD_MB, want_disk))
        cur_disk = int(cur.get("disk_mb") or 512)
        if want_disk <= cur_disk:
            notes.append("Disk bleibt bei %d MB (schrumpfen geht nicht)." % cur_disk)
        elif want_disk <= RESIZE_DISK_FREE_MB or granted:
            patch["disk_mb"] = want_disk
            notes.append("Disk %d -> %d MB." % (cur_disk, want_disk))
        else:
            need["disk_mb"] = want_disk

    if want_mem is not None:
        want_mem = max(512, min(RESIZE_MEM_HARD_MB, want_mem))
        cur_mem = int(cur.get("mem_mb") or mem_base)
        if want_mem <= cur_mem:
            notes.append("RAM bleibt bei %d MB." % cur_mem)
        elif want_mem <= mem_free_cap or granted:
            patch["mem_mb"] = want_mem
            notes.append("RAM %d -> %d MB." % (cur_mem, want_mem))
        else:
            need["mem_mb"] = want_mem

    if need:
        return _resize_ask_owner(owner, sid, tid, label, need, reason, patch, notes,
                                 ziele, auch_vorlage)

    if not patch:
        return ({"ok": True, "changed": {}, "notes": notes},
                " ".join(notes) or "Nichts zu tun.", True)

    for _s, _ in ziele:
        _sessprov_set(owner, _s, patch)
    if auch_vorlage:

        vor = {}
        if "disk_mb" in patch:
            vor["worker_disk_mb"] = patch["disk_mb"]
        if "mem_mb" in patch:
            vor["worker_mem_mb"] = patch["mem_mb"]
        _sessprov_set(owner, sid, vor)
        notes.append("Kuenftige Kinder starten gleich mit dieser Ausstattung.")
    _prov_log("orch.resize", owner,
              json.dumps({"sid": sid, "scope": label, "targets": [s for s, _ in ziele],
                          "template": bool(auch_vorlage), "patch": patch,
                          "approval": approval or None, "reason": (reason or "")[:200]})[:600],
              {"wire": "agent"})

    do_restart = True if restart is None else bool(restart)
    neu, eigen = [], False
    for _s, _t in ziele:
        if _t is None:
            eigen = True
            continue
        if do_restart and _t.get("state") in ("running", "starting"):
            r_ok, _wie = _orch_restart_child(owner, sid, _t, "Ausstattung angehoben (Orchestrator)",
                                             zwang=True)
            if r_ok:
                neu.append(_t.get("tid"))
    if eigen:
        notes.append("Die eigene Zelle waechst beim naechsten regulaeren Start — von innen kann "
                     "sie sich nicht selbst neu starten.")
    if neu:
        notes.append("Neu gestartet mit erhaltenem Arbeitsstand: %s." % ", ".join(neu))
    elif ziele and not eigen:
        notes.append("Wirkt beim naechsten Start.")
    return ({"ok": True, "scope": label, "targets": [s for s, _ in ziele],
             "template": bool(auch_vorlage), "changed": patch, "restarted": neu,
             "notes": notes}, " ".join(notes), True)

def _orch_restart_child(owner, sid, task, grund="Neustart durch den Orchestrator",
                        zwang=False):

    tid = task.get("tid")
    csid = task.get("sid")
    if not csid:
        return (False, "ohne Zelle")

    if not zwang:
        _c = _meta_cell(owner, csid)
        if _c is not None and _c.alive():
            return (True, "laeuft schon")
    weiter = bool(_meta_work_delta(csid))
    try:
        _meta_cell_stop(owner, csid)
    except Exception:
        _traceback_log("orch restart cell_stop")
        return (False, "die Zelle liess sich nicht anhalten")

    def _u(d):
        m = d.get(sid) or {}
        for t in m.get("tasks", []):
            if t.get("tid") == tid:

                if weiter:
                    t["resume_sid"] = csid
                else:
                    t.pop("resume_sid", None)
                t["state"] = "pending"
                t.pop("ended", None)
                t.pop("started", None)
                t.pop("error", None)
                _verdacht_loeschen(t)
                t["retry_after"] = 0
                t["waiting"] = ("Wird fortgesetzt — der Arbeitsstand der Zelle ist erhalten."
                                if weiter else "Wird neu gestartet.")
        m.setdefault("_resume_reason", {})[tid] = str(grund)[:300]
    _meta_update(_u)
    return (True, "fortgesetzt" if weiter else "neu")

def t_ohne_delta(t):

    return (t.get("state") not in ("done", "running", "starting")
            and t.get("sid") and not _meta_work_delta(t.get("sid")))

def orch_restart(owner, sid, tid=None, reason=None, erzwingen=False):

    ms = (_meta_load() or {}).get(sid)
    if not ms or ms.get("owner") != owner:
        return ({"ok": False, "error": "no_children"},
                "Diese Sitzung hat keine Sub-Sessions.", False)
    ziel = str(tid or "").strip()
    if ziel in ("self", "selbst"):
        return ({"ok": False, "error": "not_self"},
                "Dich selbst kannst du von innen nicht neu starten — deine Zelle waechst beim "
                "naechsten regulaeren Start.", False)
    grund = (str(reason or "").strip() or "Neustart durch den Orchestrator")[:200]

    _benannt = bool(ziel) and ziel != "*"

    treffer = [t for t in (ms.get("tasks") or [])
               if t.get("sid") and (not ziel or ziel == "*" or t.get("tid") == ziel)
               and (t.get("state") in ("running", "starting")
                    or (_benannt and t.get("state") != "done" and _meta_work_delta(t.get("sid"))))]
    if not treffer:

        _t = next((t for t in (ms.get("tasks") or []) if t.get("tid") == ziel), None) \
            if _benannt else None
        if _t is not None and t_ohne_delta(_t):
            return ({"ok": False, "error": "no_delta"},
                    "Auftrag %s ist gescheitert und sein Arbeitsstand ist NICHT mehr da — es gibt "
                    "nichts fortzusetzen. Wenn das Paper erneut bearbeitet werden soll, gehoert es "
                    "regulaer in die Warteschlange, nicht in eine Wiederaufnahme." % ziel, False)
        return ({"ok": False, "error": "not_running"},
                "Dazu gibt es nichts — weder ein laufendes Kind noch einen gescheiterten mit "
                "erhaltenem Arbeitsstand.", False)
    fort, neu, fehler, laeuft = [], [], [], []
    for t in treffer:
        ok, wie = _orch_restart_child(owner, sid, t, grund, zwang=bool(erzwingen))
        if not ok:
            fehler.append("%s (%s)" % (t.get("tid"), wie))
        elif wie == "laeuft schon":
            laeuft.append(t.get("tid"))
        elif wie == "fortgesetzt":
            fort.append(t.get("tid"))
        else:
            neu.append(t.get("tid"))
    teile = []
    if laeuft:
        teile.append("%d liefen schon und wurden NICHT angefasst (die Zelle antwortet; nach einem Portal-Neustart werden Remote-Zellen automatisch wieder uebernommen): %s"
                     % (len(laeuft), ", ".join(laeuft)))
    if fort:
        teile.append("%d fortgesetzt (Arbeitsstand erhalten): %s" % (len(fort), ", ".join(fort)))
    if neu:
        teile.append("%d frisch gestartet (kein Arbeitsstand vorhanden): %s" % (len(neu), ", ".join(neu)))
    if fehler:
        teile.append("%d nicht moeglich: %s" % (len(fehler), ", ".join(fehler)))
    _prov_log("orch.restart", owner,
              json.dumps({"sid": sid, "tid": ziel or "*", "resumed": fort, "fresh": neu,
                          "failed": fehler, "reason": grund})[:400], {"wire": "agent"})
    return ({"ok": bool(fort or neu), "resumed": fort, "fresh": neu, "failed": fehler},
            "; ".join(teile) or "Nichts neu gestartet.", bool(fort or neu))

def _resize_ask_owner(owner, sid, scope, label, need, reason, already, notes,
                      ziele, auch_vorlage):

    import portal_metafeatures as _mf
    teile = []
    if "mem_mb" in need:
        teile.append("RAM auf %d MB (frei erlaubt sind %d MB)"
                     % (need["mem_mb"], _resize_mem_base_mb() + RESIZE_MEM_HEADROOM_MB))
    if "disk_mb" in need:
        teile.append("Disk auf %d MB (frei erlaubt sind %d MB)"
                     % (need["disk_mb"], RESIZE_DISK_FREE_MB))
    frage = ("Mehr Ausstattung fuer %s: %s. Grund: %s"
             % (label, " und ".join(teile), (str(reason or "").strip() or "nicht angegeben")[:400]))
    res = _mf.ask_owner(owner, sid, frage, kind="approval")
    if not res.get("ok"):
        return ({"ok": False, "error": "ask_failed"},
                "Die Freigabe-Anfrage konnte nicht gestellt werden.", False)
    aid = res["aid"]
    _resize_intent_put(aid, {"owner": owner, "sid": sid, "scope": scope,
                             "disk_mb": need.get("disk_mb"), "mem_mb": need.get("mem_mb"),
                             "created": time.time()})
    if already:
        for _s, _ in (ziele or []):
            _sessprov_set(owner, _s, already)
        if auch_vorlage and "disk_mb" in already:
            _sessprov_set(owner, sid, {"worker_disk_mb": already["disk_mb"]})
        notes.append("Der erlaubte Teil ist gesetzt.")
    notes.append("Fuer den Rest entscheidet der Besitzer mit 2FA (Kennung %s). Frag den Zustand "
                 "mit ask_owner_result ab und ruf session_resize dann mit approval=%s erneut auf."
                 % (aid, aid))
    return ({"ok": True, "need_owner": True, "aid": aid, "requested": need,
             "changed": already, "notes": notes}, " ".join(notes), True)

def _resize_redeem(owner, sid, aid):

    import portal_metafeatures as _mf
    try:
        res = _mf.ask_owner_result(owner, sid, aid)
    except Exception:
        return None

    if not (res.get("ok") and res.get("state") == "answered"
            and str(res.get("answer") or "").startswith("GENEHMIGT")):
        return None
    intent = _resize_intent_take(aid)
    if not intent or intent.get("owner") != owner or intent.get("sid") != sid:
        return None
    return intent

def orch_stop(owner, sid, tid=None, reason=None, erledigt=False):

    ms = (_meta_load() or {}).get(sid)
    if not ms or ms.get("owner") != owner:
        return ({"ok": False, "error": "no_children"}, "Diese Sitzung hat keine Sub-Sessions.", False)
    ziel = str(tid or "").strip()
    grund = (str(reason or "").strip() or "kein Grund angegeben")[:200]

    _benannt = bool(ziel) and ziel != "*"
    _zustaende = ("running", "starting", "pending") if _benannt else ("running", "starting")
    treffer = [t for t in ms.get("tasks", [])
               if t.get("state") in _zustaende
               and (t.get("sid") or t.get("state") == "pending")
               and (not ziel or ziel == "*" or t.get("tid") == ziel)]
    if not treffer:
        return ({"ok": False, "error": "not_running"},
                "Dazu gibt es nichts zum Abbrechen — weder laufend noch wartend.", False)

    gestoppt = []
    for t in treffer:
        if t.get("sid"):
            _meta_cell_stop(owner, t["sid"])
        gestoppt.append(t.get("tid"))

    _fertig = bool(erledigt)

    def _u(d):
        m = d.get(sid) or {}
        for t in m.get("tasks", []):
            if t.get("tid") in gestoppt:
                t["stopped"] = time.time()
                if _fertig:

                    t["state"] = "done"
                    t["ended"] = time.time()
                    t.pop("error", None)
                    if not (t.get("result") or "").strip():
                        t["result"] = grund
                else:
                    t["state"] = "error"
                    t["error"] = "vom Orchestrator abgebrochen: %s" % grund
    _meta_update(_u)

    return ({"ok": True, "stopped": gestoppt, "erledigt": _fertig,
             "counts": _meta_counts(_meta_load().get(sid, {}))},
            "%d Sub-Session(s) %s (%s)." % (len(gestoppt),
                                            "abgeschlossen und freigegeben" if _fertig
                                            else "abgebrochen", grund), True)

_HPC_DOWN_MSG = ("Die Verbindung zum Rechencluster ist gerade nicht aufgebaut. Die VPN-Verbindung "
                    "kann nur der Operator per Zwei-Faktor-Anmeldung herstellen — danach klappt es sofort.")

def _hpc_vpn_id():

    v = (os.environ.get("PN_HPC_VPN_ID") or os.environ.get("HPC_VPN_ID") or "").strip()
    if not v:
        try:
            for _ln in open("/etc/brainbox/site.conf"):
                _ln = _ln.strip()
                if _ln.startswith("HPC_VPN_ID="):
                    v = _ln.split("=", 1)[1].strip()
                    break
        except Exception:
            v = ""
    return v or "hpc"

def _hpc_netns_status(uid):

    try:
        r = _netns_vpn("hstat", _netns_uid(uid), _hpc_vpn_id(), timeout=15)
        return r if isinstance(r, dict) else {"connected": False, "ns": None}
    except Exception:
        return {"connected": False, "ns": None}

def _hpc_netns_ssh(uid, cmd, target=None, timeout=90, max_out=8000):

    if target is None:
        target = HPC_HOST
    _ensure_netns_askpass()
    env = dict(os.environ); env["SUDO_ASKPASS"] = _NETNS_ASKPASS
    b64 = base64.b64encode((cmd or "hostname").encode()).decode()
    args = ["sudo", "-A", "python3", _NETNS_MGR, "hssh", "--uid", str(_netns_uid(uid)),
            "--vpn", _hpc_vpn_id(), "--target", target, "--rcmd", b64, "--timeout", str(timeout)]
    try:
        pr = subprocess.run(args, capture_output=True, text=True, timeout=timeout + 30, env=env)
        r = json.loads((pr.stdout or "").strip().splitlines()[-1])
    except Exception as e:
        return None, "Cluster-Verbindung fehlgeschlagen: %s" % str(e)[:120]
    if r.get("error"):
        return None, r["error"]
    return {"rc": r.get("rc", 0), "out": (r.get("out") or "")[-max_out:], "target": r.get("target")}, None

def _hpc_ssh(cmd, timeout=90, uid=None, max_out=8000):

    if uid is not None and _hpc_netns_status(uid).get("connected"):
        return _hpc_netns_ssh(uid, cmd, timeout=timeout, max_out=max_out)
    if _hpc_status().get("connected"):
        try:
            pr = subprocess.run([HPC_VPN_BIN, "ssh", HPC_HOST, cmd],
                                capture_output=True, text=True, timeout=timeout)
            out = (pr.stdout or "").strip()
            if pr.returncode != 0 and not out:
                out = (pr.stderr or "").strip()[-1500:]
            return {"rc": pr.returncode, "out": out[-max_out:]}, None
        except subprocess.TimeoutExpired:
            return None, "Zeitüberschreitung bei der Verbindung zum Rechencluster."
        except Exception as e:
            return None, "Cluster-Verbindung fehlgeschlagen: %s" % str(e)[:120]
    return None, _HPC_DOWN_MSG

def _hpc_submit(command=None, script=None, name=None, timeout=120, uid=None):

    name = re.sub(r"[^A-Za-z0-9_.-]", "", str(name or "brainarbeit"))[:40] or "brainarbeit"
    if script:
        b64 = base64.b64encode(script.encode()).decode()
        remote = ("mkdir -p ~/pn-jobs && printf '%%s' '%s' | base64 -d > ~/pn-jobs/%s.sh && "
                  "sbatch --job-name=%s ~/pn-jobs/%s.sh" % (b64, name, name, name))
    elif command:
        b64 = base64.b64encode(str(command).encode()).decode()
        remote = ("CMD=$(printf '%%s' '%s' | base64 -d); sbatch --job-name=%s --wrap=\"$CMD\"" % (b64, name))
    else:
        return None, "Kein Befehl und kein Skript angegeben."
    res, err = _hpc_ssh(remote, timeout=timeout, uid=uid)
    if err:
        return None, err
    m = re.search(r"Submitted batch job (\d+)", res.get("out") or "")
    if m:
        return {"job_id": m.group(1), "out": res["out"]}, None
    return None, "Der Cluster hat den Auftrag nicht angenommen: %s" % ((res.get("out") or "?")[:300])

import shlex as _shlex

_HPC_FETCH_DEFAULT_KB = 256
_HPC_FETCH_MAX_KB = 1024

_HPC_CTL_ALLOW = {

    "squeue", "sacct", "sinfo", "scontrol", "scancel", "sprio", "sstat", "sbatch", "sacctmgr",
    "sshare", "sreport", "salloc",

    "ps", "kill", "pkill", "pgrep", "top", "pstree",

    "uptime", "free", "nproc", "lscpu", "uname", "quota", "env", "printenv", "which", "type",
    "module", "ulimit", "lsb_release", "sha1sum", "groups", "getent", "locale",

    "ls", "stat", "du", "df", "find", "readlink", "realpath", "dirname", "basename", "file",
    "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "cut", "sort", "uniq", "tr", "sed",
    "awk", "jq", "diff", "cmp", "column", "paste", "comm", "split", "nl", "rev", "tac", "zcat",
    "sha256sum", "md5sum", "base64", "echo", "printf", "hostname", "whoami", "date", "id", "test",

    "mkdir", "cp", "mv", "ln", "touch", "chmod", "rmdir", "mktemp",
}

_HPC_CTL_MINI_CPU = {
    "python", "python2", "python3", "Rscript", "R", "perl", "ruby", "julia", "node",
    "bash", "sh", "make", "cmake", "gcc", "g++", "gfortran", "cc", "javac", "java",
    "pytest", "conda", "mamba", "micromamba", "pip", "pip3", "poetry", "renv",
}

_HPC_CTL_MINI_IO = {
    "wget", "curl", "git", "rsync", "scp", "sftp", "tar", "gzip", "gunzip", "zip", "unzip",
    "bzip2", "xz", "zstd", "prefetch", "fasterq-dump", "fastq-dump", "aria2c", "wget2",
}

_HPC_CTL_NEVER = {"mpirun", "mpiexec", "orterun", "xargs", "parallel", "nohup", "setsid",
                     "screen", "at", "batch", "crontab"}

_HPC_MINI_CPU_S = int(os.environ.get("PN_HPC_MINI_CPU_S", "300"))
_HPC_MINI_IO_S = int(os.environ.get("PN_HPC_MINI_IO_S", "3600"))
_HPC_CTL_BLOCK = ("`", "$(", "${", ">", "<", ";", "&", "\n", "\r")

_HPC_MINI_PARALLEL = int(os.environ.get("PN_HPC_MINI_PARALLEL", "6"))
_HPC_RATE_MAX = int(os.environ.get("PN_HPC_RATE_MAX", "20"))
_HPC_RATE_FENSTER_S = int(os.environ.get("PN_HPC_RATE_FENSTER_S", "900"))
_HPC_GRATIS_S = float(os.environ.get("PN_HPC_GRATIS_S", "5"))
_hpc_takt = {"offen": 0, "starts": collections.deque(), "abgelehnt": 0, "letzte_absage": 0.0}
_hpc_takt_lock = threading.Lock()
_zst.register("portal_metasessions._hpc_takt", "cursor", __name__, ref=_hpc_takt,
              beschreibung="HPC-Mini-Takt: offene Starts, Startfenster (deque), Absagen; Verlust => Rate-Fenster leer, kurzzeitig werden wieder mehr Starts zugelassen",
              neustart="verfaellt", schreiber="HPC-Startpfad unter _hpc_takt_lock")

def _hpc_budget_pruefen():

    jetzt = time.time()
    with _hpc_takt_lock:
        fenster = _hpc_takt["starts"]
        while fenster and jetzt - fenster[0] > _HPC_RATE_FENSTER_S:
            fenster.popleft()
        if _hpc_takt["offen"] >= _HPC_MINI_PARALLEL:
            _hpc_takt["abgelehnt"] += 1
            return False, ("Auf dem Login-Knoten laufen gerade schon %d kleine Laeufe der Flotte "
                           "(Grenze %d, ueber ALLE Sessions gezaehlt). Gib deinen Lauf per "
                           "hpc_submit in SLURM — dort wartet er geordnet in der Warteschlange, "
                           "statt den geteilten Knoten zu belasten. Das ist kein Fehler deiner "
                           "Aufgabe." % (_hpc_takt["offen"], _HPC_MINI_PARALLEL))
        if len(fenster) >= _HPC_RATE_MAX:
            _hpc_takt["abgelehnt"] += 1
            wartezeit = int(_HPC_RATE_FENSTER_S - (jetzt - fenster[0]))
            return False, ("Die Flotte hat in den letzten %d Minuten schon %d kleine Laeufe auf dem "
                           "Login-Knoten gestartet (Grenze %d). Einmal liefen dort 186 Rechnungen "
                           "gleichzeitig und die Cluster-Administration hat uns gemahnt — deshalb "
                           "bremst die Box hier. Gib den Lauf per hpc_submit in SLURM; in etwa "
                           "%d s ist wieder Luft. Das ist kein Fehler deiner Aufgabe."
                           % (_HPC_RATE_FENSTER_S // 60, len(fenster), _HPC_RATE_MAX,
                              max(1, wartezeit)))
        _hpc_takt["offen"] += 1
        return True, None

def _hpc_budget_freigeben(dauer_s):

    with _hpc_takt_lock:
        _hpc_takt["offen"] = max(0, _hpc_takt["offen"] - 1)
        if dauer_s is not None and dauer_s >= _HPC_GRATIS_S:
            _hpc_takt["starts"].append(time.time())

def hpc_takt_status():

    jetzt = time.time()
    with _hpc_takt_lock:
        fenster = [t for t in _hpc_takt["starts"] if jetzt - t <= _HPC_RATE_FENSTER_S]
        return {"offen": _hpc_takt["offen"], "grenze_offen": _HPC_MINI_PARALLEL,
                "starts_im_fenster": len(fenster), "grenze_rate": _HPC_RATE_MAX,
                "fenster_s": _HPC_RATE_FENSTER_S, "abgelehnt_gesamt": _hpc_takt["abgelehnt"]}

def _hpc_mini_wrap(seg_toks, klasse):

    sek = _HPC_MINI_IO_S if klasse == "io" else _HPC_MINI_CPU_S
    vor = ["timeout", "-k", "5", str(sek), "nice", "-n", "19"]
    if klasse == "io":
        vor += ["ionice", "-c", "3"]
    return vor + list(seg_toks)

def _hpc_ctl(command, uid=None, timeout=60):

    cmd = (command or "").strip()
    if not cmd:
        return None, "Kein Kommando angegeben."
    scrub = cmd.replace("2>/dev/null", "").replace("2> /dev/null", "").replace("2>&1", "")
    if any(b in scrub for b in _HPC_CTL_BLOCK) or chr(10) in cmd or chr(13) in cmd:
        return None, ("Kommando abgelehnt: Verkettung, Umleitung und Subshell sind hier nicht "
                      "erlaubt (eine Pipe mit | ist erlaubt). Brauchst du mehrere Schritte, "
                      "Umleitung in eine Datei oder eine Schleife, dann leg ein Skript ab und "
                      "gib es per hpc_submit in SLURM — dort ist eine ganze Shell erlaubt.")
    segmente, umbau, mini = cmd.split("|"), [], None
    for seg in segmente:
        try:
            toks = _shlex.split(seg)
        except ValueError:
            return None, "Kommando nicht parsebar (Anfuehrungszeichen?)."
        if not toks:
            return None, "Leeres Pipe-Segment."
        base = toks[0].rsplit("/", 1)[-1]
        if base in _HPC_CTL_NEVER:
            return None, ("'%s' startet mehrere Prozesse oder haengt sie ab — das gehoert nie auf "
                          "den Login-Knoten, sondern in einen SLURM-Job (hpc_submit). Der "
                          "Betreiber verweist parallele Laeufe ausdruecklich auf die Rechenknoten."
                          % base)
        if base in _HPC_CTL_ALLOW:
            umbau.append(seg.strip())
            continue
        klasse = ("io" if base in _HPC_CTL_MINI_IO
                  else "cpu" if base in _HPC_CTL_MINI_CPU else None)
        if klasse is None:
            return None, ("Kommando '%s' ist auf dem Login-Knoten nicht vorgesehen. Erlaubt sind "
                          "Monitoring und Job-Kontrolle (squeue, sacct, sinfo, scontrol), Lesen und "
                          "Suchen (ls, cat, grep, find, awk, sed …), Job-Vorbereitung (mkdir, cp, "
                          "chmod), Diagnose (uptime, free, nproc, module) sowie KLEINE Laeufe: "
                          "Skripttests, Kompilieren, Installieren und Downloads (python3, Rscript, "
                          "make, gcc, pip, conda, git, wget, curl, tar …) unter einem Deckel von "
                          "%d s CPU bzw. %d s fuer Datentransfer. Alles Groessere gehoert in SLURM "
                          "(hpc_submit)." % (base, _HPC_MINI_CPU_S, _HPC_MINI_IO_S))
        if len(segmente) > 1:
            return None, ("'%s' laeuft hier nur als EINZELNES Kommando, nicht in einer Pipe — der "
                          "Ressourcen-Deckel muss den Lauf selbst umschliessen. Ruf es allein auf "
                          "oder gib die ganze Kette per hpc_submit in SLURM." % base)
        mini = klasse
        umbau.append(" ".join(_shlex.quote(t) for t in _hpc_mini_wrap(toks, klasse)))
    cmd = " | ".join(umbau)
    if not mini:
        return _hpc_ssh_ctl_out(cmd, uid, timeout)

    ok, grund = _hpc_budget_pruefen()
    if not ok:
        return None, grund
    _t0 = time.time()
    try:
        timeout = max(int(timeout or 60),
                      (_HPC_MINI_IO_S if mini == "io" else _HPC_MINI_CPU_S) + 30)
        return _hpc_ssh_ctl_out(cmd, uid, timeout)
    finally:
        _hpc_budget_freigeben(time.time() - _t0)

def _hpc_ssh_ctl_out(cmd, uid, timeout):

    res, err = _hpc_ssh(cmd, uid=uid, timeout=timeout, max_out=16000)
    if err:
        return None, err
    return {"rc": res.get("rc", 0), "out": (res.get("out") or "")}, None

_SLURMWATCH_REPO = os.environ.get("PN_SLURMWATCH_REPO", "")
_SLURMWATCH_DIR = os.environ.get("PN_SLURMWATCH_DIR", "")
_SLURMWATCH_AKTIONEN = ("status", "start")

_SLURMWATCH_MUSTER = "slurmwatch[.]main"

def _slurmwatch_kommando(aktion):

    sperre = _SLURMWATCH_DIR + "/respawn.lock"

    befund = ("flock -n %s true 2>/dev/null && echo SLURMWATCH=STEHT || echo SLURMWATCH=LAEUFT"
              % sperre)
    zeige = ("pgrep -u $(id -u) -af '%s' || echo '(kein Python-Prozess — evtl. Wiederanlauf-Pause)'"
             % _SLURMWATCH_MUSTER)
    if aktion == "status":
        return ("mkdir -p %s; echo '== zustand'; %s; echo '== prozess'; %s; echo '== log'; "
                "tail -n 8 %s/slurmwatch.log 2>/dev/null || echo '(kein Protokoll)'; "
                "echo '== zustandsdatei'; ls -l %s/state.db 2>/dev/null || echo '(keine)'"
                % (_SLURMWATCH_DIR, befund, zeige, _SLURMWATCH_DIR, _SLURMWATCH_DIR))

    return ("mkdir -p %s; if flock -n %s true 2>/dev/null; then "
            "setsid nohup bash %s/deploy/respawn.sh >> %s/respawn.log 2>&1 </dev/null & "
            "sleep 8; else echo '(laeuft bereits — nichts getan)'; fi; "
            "echo '== danach'; %s; %s; echo '== respawn-protokoll'; "
            "tail -n 5 %s/respawn.log 2>/dev/null || echo '(leer)'"
            % (_SLURMWATCH_DIR, sperre, _SLURMWATCH_REPO, _SLURMWATCH_DIR,
               befund, zeige, _SLURMWATCH_DIR))

def _hpc_slurmwatch(aktion="status", uid=None, timeout=120):

    if aktion is None:
        akt = "status"
    elif isinstance(aktion, str):
        akt = aktion.strip().lower() or "status"
    else:
        return None, ("Unbekannte Aktion: erwartet wird ein Wort, kein %s. Erlaubt sind: %s."
                      % (type(aktion).__name__, ", ".join(_SLURMWATCH_AKTIONEN)))
    if akt not in _SLURMWATCH_AKTIONEN:
        return None, ("Unbekannte Aktion '%s'. Erlaubt sind: %s."
                      % (akt[:40], ", ".join(_SLURMWATCH_AKTIONEN)))
    if akt == "start" and str(uid or "") != "owner":
        return None, ("Den Cluster-Melder starten darf nur der Owner. Nachsehen (aktion='status') "
                      "kannst du jederzeit — wenn er steht, melde das. Einen Daemon auf gemeinsamer "
                      "Infrastruktur zu starten ist keine Entscheidung deiner Sitzung.")
    res, err = _hpc_ssh(_slurmwatch_kommando(akt), uid=uid, timeout=timeout, max_out=8000)
    if err:
        return None, err
    out = res.get("out") or ""
    return {"aktion": akt, "laeuft": "SLURMWATCH=LAEUFT" in out,
            "out": out, "rc": res.get("rc", 0)}, None

def _hpc_fetch(path, uid=None, max_kb=None, timeout=90):

    import base64 as _b64
    p = (path or "").strip()
    if not p or any(c in p for c in ("`", "$", ";", "|", "&", "\n", "\r", "*", "?")):
        return None, "Ungueltiger Pfad."
    try:
        kb = int(max_kb or _HPC_FETCH_DEFAULT_KB)
    except (TypeError, ValueError):
        kb = _HPC_FETCH_DEFAULT_KB
    kb = max(1, min(kb, _HPC_FETCH_MAX_KB))
    nbytes = kb * 1024
    cmd = "LC_ALL=C head -c %d -- %s | base64 | tr -d '\n'" % (nbytes, _shlex.quote(p))
    res, err = _hpc_ssh(cmd, uid=uid, timeout=timeout, max_out=nbytes * 2 + 4096)
    if err:
        return None, err
    b64 = (res.get("out") or "").strip()
    if not b64:
        return {"path": p, "bytes": 0, "content": "", "truncated": False}, None
    try:
        raw = _b64.b64decode(b64, validate=False)
    except Exception:
        return None, "Antwort nicht dekodierbar (kein base64) — Pfad vorhanden/lesbar?"
    out = {"path": p, "bytes": len(raw), "truncated": len(raw) >= nbytes}
    try:
        out["content"] = raw.decode("utf-8")
    except UnicodeDecodeError:
        out["content"] = None
        out["base64"] = b64
        out["binary"] = True
    return out, None

def _user_may_verb(uid, verb):

    try:
        enf = _policy.enforcement(_policy_store_mod().effective(uid, "voice", "default",
                                                               global_floor=_global_floor()))
        verbs = enf.get("portal_verbs") or []
        return "*" in verbs or verb in verbs
    except Exception:
        return False

_VOICE_KEEPALIVE_STARTED = False
def _voice_keepalive_start():

    global _VOICE_KEEPALIVE_STARTED
    if _VOICE_KEEPALIVE_STARTED:
        return
    _VOICE_KEEPALIVE_STARTED = True
    def loop():
        time.sleep(8)
        while True:
            try:
                warm_mode = _voice_prewarm_mode() == "warm"
                _voice_rotate_and_prewarm(DEFAULT_PRINCIPAL, ensure=warm_mode)

                h = int(time.strftime("%H", time.localtime()))
                if warm_mode and VOICE_WARM_H0 <= h < VOICE_WARM_H1:
                    try:
                        mgr = _voice_cellmgr()
                        c = mgr.cell(DEFAULT_PRINCIPAL, _voice_session_for(DEFAULT_PRINCIPAL)) \
                            if hasattr(mgr, "cell") else None
                        if c is not None and c.alive():
                            c.last = time.time()
                    except Exception:
                        pass
            except Exception:
                _traceback_log("voice keepalive")
            time.sleep(int(os.environ.get("PN_VOICE_KEEPALIVE_S", "1500")))
    threading.Thread(target=loop, daemon=True).start()

_SCHED_FILE = os.path.join(DATA_DIR, "scheduled-tasks.json")
_SCHED_LOCK = threading.Lock()
_SCHED_STARTED = False
def _wake_hochfahren(uid, sid):

    try:
        import portal_jobs_persist as pjp
        rec = (_session_store(uid, "cockpit") if _session_store else pjp._session_store(uid)).get(sid)
    except Exception:
        rec = None
    if rec is None:
        return False, "Sitzung gibt es nicht (mehr)"
    if rec.get("archived"):
        return False, "Sitzung ist archiviert — ein Wecker hebt das nicht auf"
    try:
        import pn_ram_admission as _RA
        prov = (_sessprov_get(uid, sid) or {}) if callable(_sessprov_get) else {}
        try:
            want = int(prov.get("mem_mb") or 0) or _RA.default_mem_for("session")
        except (TypeError, ValueError):
            want = _RA.default_mem_for("session")
        pl = _RA.plan(want, "session")
        if not pl.get("grant"):
            return False, ("kein Platz zum Hochfahren: %s" % (pl.get("reason") or "RAM-Budget"))
    except Exception:
        _traceback_log("wake ram-preflight")
    try:
        import portal_voice_core as vc
        import pn_cell_session as cs
        mgr = cs.get_manager()
        enf = vc._cockpit_policy_enf(uid, sid) or {}
        cell = mgr.ensure(uid, sid, portal_url=vc._portal_base_url(),
                          portal_token=vc._voice_agent_token(uid), policy=enf)
    except Exception as e:
        _traceback_log("wake boot")
        return False, "Hochfahren fehlgeschlagen: %s" % (str(e)[:160])
    if not (cell and cell.alive()):
        try:
            grund = mgr.boot_reason(uid, sid)
        except Exception:
            grund = None
        return False, (grund or "Zelle bootete nicht")
    return True, "hochgefahren"

_SCHEDULABLE = ("display_show", "display_restore", "announce", "say", "session_wake")

def _sched_load():
    try:
        with open(_SCHED_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []

def _sched_save(tasks):
    try:
        tmp = _SCHED_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(tasks, f)
        os.replace(tmp, _SCHED_FILE)
    except OSError:
        pass

def _sched_add(task):
    with _SCHED_LOCK:
        tasks = _sched_load()
        tasks.append(task)
        _sched_save(tasks)

def _parse_dur(s):
    total = 0
    for n, u in re.findall(r"(\d+)\s*([smhd])", str(s).lower()):
        total += int(n) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[u]
    return total

def _sched_parse_when(args):

    now = time.time()
    every_s = _parse_dur(args.get("every") or "") if args.get("every") else 0
    if args.get("at"):
        m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(args["at"]))
        if not m:
            return None, 0, "Sag mir die Uhrzeit als Stunde und Minute."
        h, mi = int(m.group(1)), int(m.group(2))
        lt = time.localtime(now)
        cand = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, mi, 0, 0, 0, -1))
        if cand <= now + 1:
            cand += 86400
        return cand, every_s, None
    if args.get("in"):
        d = _parse_dur(args["in"])
        if d <= 0:
            return None, 0, "Sag mir, in wie vielen Minuten oder Stunden."
        return now + d, every_s, None
    if every_s > 0:
        return now + every_s, every_s, None
    return None, 0, "Wann soll ich das tun?"

def _fire_scheduled(uid, do):

    verb = str((do or {}).get("verb") or "").strip()
    args = (do or {}).get("args") or {}
    if not isinstance(args, dict):
        args = {}
    try:
        if verb == "display_show":
            did = str(args.get("display") or "local")
            did = (_DISPLAY_REG.resolve(did) or did) if _DISPLAY_REG else did
            ref = args.get("ref") or {}
            if isinstance(ref, str):
                ref = {"kind": "url", "value": ref} if "://" in ref else {"kind": "text", "text": ref}
            _res, err = (_DISPLAY_REG.show(uid, did, ref, kiosk_post=_kiosk_post)
                         if _DISPLAY_REG else (None, "unavailable"))
            return (err is None), (err or "shown")
        if verb == "display_restore":
            did = str(args.get("display") or "local")
            _res, err = (_DISPLAY_REG.restore_idle(uid, did, kiosk_post=_kiosk_post)
                         if _DISPLAY_REG else (None, "unavailable"))
            return (err is None), (err or "restored")
        if verb in ("announce", "say"):
            text = str(args.get("text") or args.get("value") or "").strip()
            if not text:
                return False, "no text"
            _nabu_announce(uid, text)
            return True, "announced"
        if verb == "session_wake":

            wsid = re.sub(r"[^A-Za-z0-9_-]", "", str(args.get("_sid") or ""))[:40]
            wtext = str(args.get("text") or "Weckruf: fuehre jetzt deinen geplanten Durchlauf aus.").strip()
            if not wsid:
                return False, "no sid"
            wcell = _meta_cell(uid, wsid)
            if wcell is not None and wcell.alive():
                return (bool(wcell.submit(wtext)), "geweckt")
            try:
                import pn_software_shelf as _shelf
                if _shelf.warden_sid() == wsid:
                    r = _shelf.warden_ensure(uid)
                    return bool(r.get("ok")), (r.get("reason") or "Aufseher neu aufgebaut")
            except Exception:
                _traceback_log("session_wake warden")
            ok, grund = _wake_hochfahren(uid, wsid)
            if not ok:
                return False, grund
            wcell = _meta_cell(uid, wsid)
            if wcell is None or not wcell.alive():
                return False, "hochgefahren, aber die Zelle antwortet nicht"
            return (bool(wcell.submit(wtext)), "hochgefahren und geweckt")
        return False, "verb not schedulable: %s" % verb
    except Exception as e:
        return False, str(e)

def _agent_schedule_dispatch(verb, args, uid):

    if verb == "schedule_list":
        mine = [t for t in _sched_load() if t.get("uid") == uid]
        if not mine:
            return {"ok": True, "spoken": "Es sind keine Timer geplant."}
        parts = []
        for t in mine:
            when = time.strftime("%H:%M", time.localtime(t.get("next_ts", 0)))
            parts.append("%s um %s Uhr%s" % (t.get("label") or (t.get("do") or {}).get("verb") or "Aktion",
                                             when, " (wiederkehrend)" if t.get("every_s") else ""))
        return {"ok": True, "spoken": "Geplant: " + "; ".join(parts) + ".", "tasks": mine}
    if verb == "schedule_cancel":
        tid = str(args.get("id") or "").strip()
        def _is_wake(t):
            return str(((t.get("do") or {}).get("verb")) or "") == "session_wake"
        with _SCHED_LOCK:
            tasks = _sched_load()
            if args.get("all"):

                kept = [t for t in tasks if not (t.get("uid") == uid and not _is_wake(t))]
            else:
                kept = [t for t in tasks if not (t.get("uid") == uid and tid and t.get("id") == tid)]
            n = len(tasks) - len(kept)
            _sched_save(kept)
        return {"ok": True, "spoken": ("%d Timer gelöscht." % n) if n else "Kein passender Timer gefunden."}

    do = args.get("do") or {}
    if not isinstance(do, dict) or str(do.get("verb") or "") not in _SCHEDULABLE:
        return {"ok": False, "spoken": "Zeitlich planen kann ich nur Anzeigen und Durchsagen."}
    next_ts, every_s, err = _sched_parse_when(args)
    if err:
        return {"ok": False, "spoken": err}
    task = {"id": "sch%d" % int(now_ms()), "uid": uid,
            "do": {"verb": str(do.get("verb")), "args": (do.get("args") or {})},
            "next_ts": next_ts, "every_s": every_s, "label": str(args.get("label") or "")[:80],
            "created": time.time()}
    _sched_add(task)
    when = time.strftime("%H:%M", time.localtime(next_ts))
    _prov_log("schedule.add", uid, json.dumps(task)[:400], {"wire": "agent"})
    return {"ok": True, "id": task["id"],
            "spoken": ("Eingeplant, wiederkehrend, das nächste Mal um %s Uhr." % when) if every_s
                      else ("Eingeplant für %s Uhr." % when)}

def _rearm_reprofleet_waker():

    try:
        base = "/data/shares/users/owner/sessions"
        sid = None
        for name in sorted(os.listdir(base)):
            if name.startswith("reprofleet-orchestrator-"):
                sid = name.rsplit("-", 1)[-1]
                break
        if not sid:
            return
        raw = json.load(open(os.path.join(DATA_DIR, "metasessions.json")))
        items = raw if isinstance(raw, list) else (raw.get("sessions") or list(raw.values()))
        running = any(isinstance(s, dict) and sid in json.dumps(s) and s.get("state") == "running"
                      for s in items)
        if not running:
            return
        if any(t.get("label") == "reprofleet-runde" for t in _sched_load()):
            return
        now = time.time()
        _sched_add({"id": "sch%d" % int(now * 1000), "uid": DEFAULT_PRINCIPAL,
                    "do": {"verb": "session_wake", "args": {
                        "text": ("Betriebsrunde the fleet - 2h-Takt (Owner 02.08.2026, "
                                 "Runden-Diaet: NICHT auf 30m zurueckstellen). Fuehre die Schritte "
                                 "aus ~/austausch/reprofleet/HANDBUCH.md aus. Zwischen den Runden "
                                 "bist du nicht blind: die Auto-Aufsicht meldet Aenderungen deiner "
                                 "Raeume von selbst - ernte und fuelle nach, wenn ein Raum fertig "
                                 "meldet, nicht auf Verdacht. Stelle deinen Wecker (label "
                                 "reprofleet-runde) neu, falls er fehlt - mit every 2h. Die "
                                 "front1-wache laeuft LLM-frei als Host-Cron; ihr Wecker (label "
                                 "front1-wache) ist gewollt nur 6h-Fallback - falls er fehlt, mit "
                                 "every 6h neu stellen, NICHT mit 12m."),
                        "_sid": sid}},
                    "next_ts": now + 30, "every_s": 7200,
                    "label": "reprofleet-runde", "created": now})
    except Exception:
        pass

def _scheduler_start():
    global _SCHED_STARTED
    if _SCHED_STARTED:
        return
    _SCHED_STARTED = True
    def loop():
        time.sleep(10)
        while True:
            try:
                now = time.time()
                with _SCHED_LOCK:
                    tasks = _sched_load(); keep = []; changed = False; due = []
                    for t in tasks:
                        if t.get("next_ts", 0) <= now:
                            due.append(t); changed = True
                            if t.get("every_s", 0) > 0:
                                t["next_ts"] = now + t["every_s"]; keep.append(t)
                        else:
                            keep.append(t)
                    if changed:
                        _sched_save(keep)
                for t in due:
                    ok, msg = _fire_scheduled(t.get("uid", DEFAULT_PRINCIPAL), t.get("do"))
                    _prov_log("schedule.fire", t.get("uid", DEFAULT_PRINCIPAL),
                              json.dumps({"do": t.get("do"), "ok": ok, "msg": msg})[:400], {"wire": "sched"})
            except Exception:
                _traceback_log("scheduler")
            time.sleep(15)
    threading.Thread(target=loop, daemon=True).start()

def now_ms():
    return time.time() * 1000

def _voice_cell(uid=DEFAULT_PRINCIPAL, sess=None):

    return _voice_cellmgr().ensure(uid, sess or _voice_session_for(uid), portal_url=_portal_base_url(),
                                   portal_token=_voice_agent_token(uid), policy=_voice_policy_enf(uid))

def _age_years(birthdate):

    try:
        y, m, d = (int(x) for x in str(birthdate or "").strip().split("-"))
        import datetime as _dt
        t = _dt.date.today()
        return max(0, t.year - y - (1 if (t.month, t.day) < (m, d) else 0))
    except Exception:
        return None

def kid_persona(age):

    try:
        age = int(age)
    except (TypeError, ValueError):
        age = 9
    if age < 8:
        stufe = ("Das Kind ist noch jung (unter 8): sehr kurze Saetze, immer nur EINE Sache auf "
                 "einmal, und nach jedem Schritt fragen, ob es das ausprobiert hat.")
    elif age <= 11:
        stufe = ("Alter 8-11: normale, geduldige Erklaerungen — aber kurz. Neue Begriffe in einem "
                 "Halbsatz erklaeren, dann weiter.")
    else:
        stufe = ("Ab 12: fast wie mit Erwachsenen, nur mit Erklaer-Reflex — neue Begriffe in ein, "
                 "zwei Saetzen erklaeren, statt sie vorauszusetzen.")
    return "\n".join([
        "# Du arbeitest mit einem Kind (ungefaehres Alter: %d)" % age,
        "",
        "## So laeuft es ab (das ist die WICHTIGSTE Regel)",
        "Das Kind sagt EINEN Satz, was es haben will. DU baust es fertig, legst es in seinen Ordner, "
        "und sagst in EINEM Satz, dass es da ist und wie es heisst. Das Kind klickt die Datei an und "
        "probiert sie aus. Danach sagt es, was anders sein soll, und du aenderst es. Mehr nicht.",
        "",
        "## Was du NIE tust",
        "- NIE auf ein Terminal, eine Konsole oder eine Kommandozeile verweisen. Das Kind hat keine "
        "und braucht keine. Sag ihm NIEMALS Befehle zum Abtippen.",
        "- NIE sagen, das Kind solle etwas installieren, herunterladen oder einrichten. Alles, was "
        "gebraucht wird, installierst DU in deiner eigenen Zelle (du bist dort root) — ohne es zu "
        "erwaehnen. Das ist deine Arbeit, nicht seine.",
        "- NIE behaupten, etwas sei abgelegt, ohne vorher nachgesehen zu haben. Erst pruefen "
        "(die Datei liegt wirklich in `~/austausch/` und ist groesser als 0), dann sagen.",
        "- KEINE langen Romane. Zwei bis fuenf Saetze. Keine Aufzaehlungen ueber den halben "
        "Bildschirm, kein Code im Chat (der Code gehoert in die Datei).",
        "",
        "## Wohin das Ergebnis gehoert",
        "Alles, was das Kind anfassen soll, kommt nach `~/austausch/` — das ist der Ordner, den es "
        "in seinem Programm sieht und anklicken kann. Nirgendwo sonst. Ein Programm ist EINE Datei "
        "mit sprechendem Namen (`tictactoe.py`, nicht `main.py`).",
        "",
        "## Es muss durch ANKLICKEN laufen — und es ist ein RICHTIGES Programm",
        "Das Kind startet nichts von Hand. Ein Doppelklick muss genuegen:",
        "- STANDARD ist ein EIGENSTAENDIGES Programm, das in einem eigenen Fenster laeuft. Fuer "
        "Spiele und Werkzeuge nimmst du eine Python-Datei mit tkinter — das ist die "
        "Standardbibliothek, es muss also nichts installiert werden, und es startet sofort.",
        "- HTML ist NICHT die Loesung. Baue KEINE Browser-Spiele. Eine HTML-Datei kommt nur dort in "
        "Frage, wo es wirklich um Web geht: eine Webseite, etwas mit Server oder Login. Fuer alles "
        "andere waere sie eine Abkuerzung — und die wollen wir ausdruecklich nicht.",
        "- Brauchst du fuer etwas Groesseres mehr als die Standardbibliothek (z. B. eine "
        "Spielebibliothek), dann baust du ein FERTIGES eigenstaendiges Programm, das ohne "
        "Installation startet — niemals eine Installationsanleitung fuer das Kind.",
        "",
        "## WO das Programm laeuft (das hier musst du wissen)",
        "Deine Zelle ist die WERKSTATT, nicht der Spielplatz. Gespielt wird auf dem Computer des "
        "Kindes — dort laeuft der Client, dort klickt es die Datei an, dort geht das Fenster auf.",
        "- Auf dem Computer des Kindes ist Python MIT tkinter vorhanden. Du musst nichts "
        "installieren, nichts herunterladen und dafuer KEINE Rechte erfragen. Stelle deswegen "
        "NIEMALS eine Freigabe-Anfrage an die Eltern — das laesst ein Kind grundlos warten.",
        "- Dass in DEINER Zelle kein tkinter liegt und kein Fenster aufgehen kann, ist normal und "
        "voellig egal. Es ist KEIN Grund, auf HTML auszuweichen, und kein Grund aufzugeben.",
        "- Pruefen kannst du hier die Syntax: `python3 -m py_compile <datei>.py`. Das ist deine "
        "Abnahme — laeuft sie durch, legst du ab. Mehr geht in der Werkstatt nicht, und mehr "
        "braucht es auch nicht.",
        "- Startet es beim Kind trotzdem nicht, schickt es dir die Fehlermeldung mit einem Knopf "
        "aus dem Client. DANN reparierst du und legst neu ab. Das ist der normale Weg, kein "
        "Rueckschlag — sag dem Kind genau das.",
        "",
        "## Nach oben gibt es KEINE Grenze",
        "Einfach heisst nicht billig. Nur der ANFANG muss leicht und schnell sein — ein erstes "
        "Erfolgserlebnis in einer Minute. Will das Kind mehr, wird es groesser: mehrere Dateien, ein "
        "Projektordner, eigene Grafik, gespeicherte Spielstaende, spaeter auch grosse Vorhaben. "
        "Traue ihm das zu und bremse es nie mit \"das ist zu schwer\" — teile es in Schritte, die "
        "jeder fuer sich sofort anklickbar laufen. Solange es per Klick startet, darf es beliebig "
        "wachsen.",
        "",
        "## Weniger fragen, mehr bauen",
        "Stelle KEINE Auswahlfragen (\"Variante 1, 2 oder 3?\"). Das Kind hat gesagt, was es will — "
        "bau die Standardloesung und leg sie hin. Erst DANACH fragst du, was anders sein soll. "
        "Rueckfragen nur, wenn du sonst wirklich nicht weiterarbeiten kannst.",
        "",
        "## Wie du antwortest",
        "- Kurz, freundlich, in Alltagssprache. Echte Begriffe sind erlaubt (keine Babysprache), "
        "aber beim ersten Mal in einem Halbsatz erklaert.",
        "- Am Ende IMMER eine Einladung: „Probier es aus und sag mir, was anders sein soll.“",
        "- Fehler nie dramatisieren. Geht etwas nicht, reparierst DU es und sagst kurz, was los war.",
        "- Erklaeren ist ein ANGEBOT, keine Pflicht: „Willst du wissen, wie das funktioniert?“ — "
        "erklaer es nur, wenn das Kind ja sagt, und dann kurz.",
        "",
        "## Grenzen",
        "- Gesperrte Wuensche (freies Internet, Geraete, Drucken — je nach Rechten dieser Sitzung): "
        "suche KEINE Umwege. Frag die Eltern ueber `portalctl ask_owner` um Freigabe und sag dem "
        "Kind, dass die Eltern das erlauben koennen.",
        "- ABER: um ein Programm zu BAUEN, brauchst du nie eine Freigabe. Ein Wunsch nach einem "
        "Spiel oder Werkzeug ist niemals ein Fall fuer `ask_owner`. Das gilt auch dann, wenn dir "
        "in der Zelle etwas fehlt — dann baust du es so, dass es ohne auskommt.",
        "- Sicherheits- und Systemgrenzen stehen nicht zur Debatte: nicht diskutieren, nicht "
        "umgehen — freundlich auf die Eltern verweisen.",

        "- Kindgerecht-Waechter: Du achtest fortlaufend darauf, dass alles kindgerecht ist — gerade "
        "beim Surfen und in der Websuche. Begegnet dir etwas, das fuer ein Kind nicht geeignet "
        "erscheint (Gewalt, Sexuelles, Angstmachendes, Betrug, Kontaktaufnahme durch Fremde), zeige "
        "es dem Kind NICHT, brich die Aktion ab und melde es ueber `portalctl ask_owner` mit "
        "kind='approval' und kurzer, sachlicher Beschreibung. Diskutiere die Grenze nicht mit dem "
        "Kind, sondern verweise freundlich auf die Eltern.",
        "",
        stufe,
    ]) + "\n"

def _kid_brief(uid):

    try:
        import portal_users as _pu
        u = _pu.user_get(uid)
    except Exception:
        return None
    if not u or u.get("role") != "kid":
        return None
    age = _age_years(u.get("birthdate"))
    return kid_persona(age if age is not None else 9)

def _hpc_brief():

    return "\n".join([
        "# RECHENCLUSTER: was auf den Login-Knoten geht und was in SLURM gehoert", "",
        "Der Betreiber (HPC-Betreiber) sagt es selbst: die Front-End-Knoten sind DER NORMALE ORT FUER "
        "INTERAKTIVE ARBEIT — Entwickeln, Testen, Kompilieren, Software in das eigene Verzeichnis "
        "installieren. Auf die Rechenknoten muss, was ein Front-End nicht leisten kann: parallele "
        "Laeufe und alles mit GPU. Es gilt also KEIN generelles Rechenverbot; verboten ist "
        "PRODUKTIONSLAST, weil du den Knoten mit anderen Nutzern teilst.", "",
        "ERLAUBT auf dem Login-Knoten (`portalctl hpc_ctl '{\"command\":\"…\"}'`):",
        "  • Aufsicht und Job-Kontrolle: squeue, sacct, sinfo, scontrol, scancel, sbatch",
        "  • Lesen und Suchen: ls, cat, head, tail, grep, find, awk, sed, wc, jq, diff (Pipes mit | sind ok)",
        "  • Diagnose: uptime, free, nproc, lscpu, module, quota, env, which",
        "  • Job-Vorbereitung: mkdir, cp, mv, ln, touch, chmod",
        "  • KLEINE Laeufe unter automatischem Deckel: Skripttests und Mini-Rechnungen (python3, "
        "Rscript, R, perl, node), Kompilieren (make, cmake, gcc), Installieren (pip, conda, mamba) — "
        "%d s; sowie Datentransfer und Entpacken (wget, curl, git, rsync, tar, fasterq-dump) — %d s. "
        "Den Deckel setzt die Box selbst; du musst nichts angeben." % (_HPC_MINI_CPU_S,
                                                                      _HPC_MINI_IO_S),
        "",
        "GEHOERT IN SLURM (`portalctl hpc_submit`): jede echte Rechnung — Assemblies, Alignments, "
        "Modelltraining, ganze Analyse-Pipelines, alles mit GPU, alles Parallele (mpirun) und alles, "
        "was ueber Minuten Rechenzeit braucht. Ebenso jede Kette mit Umleitung, Schleife oder "
        "mehreren Schritten: auf dem Login-Knoten laeuft EIN Kommando, im Batch-Job eine ganze "
        "Shell. Leg das Skript ab und schick es los.", "",
        "WICHTIG - DER DECKEL IST KEIN ERGEBNIS. Reisst ein Lauf den Deckel oder raeumt die Wache "
        "einen Prozess ab, dann heisst das ausschliesslich: das gehoert in einen Batch-Job. Es "
        "heisst NICHT, dass die Software langsam ist, dass etwas nicht reproduziert oder dass die "
        "Aufgabe gescheitert ist. Gib den Lauf per hpc_submit ab und arbeite weiter.", "",
        "WARUM ES DIE WACHE GIBT: einmal liefen 186 Rechnungen gleichzeitig auf einem Front-End, "
        "danach kam eine Mahnung der Cluster-Administration und sie haetten die Prozesse selbst "
        "abgeraeumt. Deshalb wacht ein Automat ueber die VERBRAUCHSWERTE (Rechenzeit je Prozess und "
        "Anzahl gleichzeitig rechnender Prozesse) — nicht darueber, welches Programm du aufrufst. "
        "Ein kurzer Test faellt ihm nicht auf.",
    ])

def _cockpit_cell_brief(uid, sid):

    parts = []

    try:
        import pn_sprachregelung as _spr
        _s = _spr.brief()
        if _s:
            parts.append(_s)
    except Exception:
        pass
    kid = _kid_brief(uid)
    if kid:
        parts.append(kid)
    try:
        caps = (_sess_policy_get(uid, sid) or {}).get("caps", {}) or {}
    except Exception:
        caps = {}
    if caps.get("hpc_submit") == "allow":
        parts.append(_hpc_brief())
    if caps.get("orchestrate") == "allow":
        try:
            title = (_sessprov_get(uid, sid) or {}).get("title") or sid
        except Exception:
            title = sid
        parts.append("\n".join([
            "# Orchestrator-Session: %s" % title, "",
            "Du bist der Agent DIESER Session im Brainarbeit-Portal. Du hast das ORCHESTRATOR-RECHT: du "
            "darfst selbst eigene, vollwertige Sub-Sessions als isolierte microVM-Zellen starten und "
            "steuern - der Nutzer klickt dafuer nichts. Ideal fuer Dauer-/Orchestrierungsaufgaben.", "",
            "So startest und steuerst du Sub-Sessions (ueber `portalctl`):",
            "  portalctl session_spawn '{\"task\":\"<praezise, in sich abgeschlossene Aufgabe>\"}'",
            "  portalctl session_status",
            "  portalctl session_transcript '{\"tid\":\"<tid>\"}'   # VOLLES Kind-Transkript inkl. Werkzeug-Aufrufe",
            "  portalctl session_tell '{\"tid\":\"<tid>\",\"text\":\"<nachricht>\"}'",
            "  portalctl session_broadcast '{\"text\":\"<an ALLE laufenden>\"}'",
            "  portalctl session_stop '{\"tid\":\"<tid>|*\",\"reason\":\"<warum>\"}'",
            "  portalctl session_resize '{\"tid\":\"<tid>|*|new|self\",\"disk_gb\":5,"
            "\"mem_mb\":3584,\"reason\":\"<warum>\"}'",
            "  portalctl session_restart '{\"tid\":\"<tid>|*\",\"reason\":\"<warum>\"}'",
            "  portalctl session_watch '{\"modus\":\"an|aus|status\"}'   # Auto-Aufsicht (Vorgabe: AN)",
            "Jede Sub-Session sieht deinen Chat NICHT - nur ihren Aufgabentext; formuliere jede Aufgabe "
            "vollstaendig und eindeutig. Es passen nur wenige Zellen gleichzeitig (RAM-Budget der Box); "
            "ist kein Platz, wartet die Aufgabe automatisch, und sehr tiefe Verschachtelung wird "
            "abgelehnt.", "",
            "DEINE AUFGABE IST DIE AUFSICHT, nicht das Verteilen. Auftraege wegzugeben und am Ende "
            "einzusammeln waere eine Warteschlange - dafuer braucht es keinen Agenten. Du bist da, "
            "damit jemand HINSIEHT, waehrend gearbeitet wird, und EINGREIFT, sobald etwas schief "
            "laeuft. Also: nach jedem Spawn regelmaessig `session_status` lesen und die Ergebnisse "
            "wirklich pruefen (nicht nur den Zustand); laeuft ein Kind in die falsche Richtung, "
            "korrigiere es sofort per `session_tell`; betrifft die Korrektur alle, nimm "
            "`session_broadcast`; ist ein Kind entgleist, haengt oder arbeitet am Ziel vorbei, brich "
            "es per `session_stop` ab und starte die Aufgabe praeziser neu — NIE aber, weil es dir "
            "widerspricht (dazu gleich). Ein Kind, das du nicht "
            "mehr erreichst (Zelle beendet), ist fertig oder tot - dann zaehlt nur noch sein "
            "Ergebnis. Melde dem Nutzer, was du korrigiert hast, nicht nur was fertig wurde.", "",
            "DU SIEHST ALLES, WAS DEINE KINDER TUN — NUTZE ES. Jedes Kind schreibt sein "
            "vollstaendiges Gespraechs-Transkript (jeden Modell-Zug, jeden Werkzeug-Aufruf samt "
            "Eingabe und Ergebnis), und du kannst es JEDERZEIT lesen, auch mitten in seiner "
            "Arbeit: `session_transcript` mit der tid liefert ohne 'ab' die juengsten ~60 KB; das "
            "Feld `weiter_ab` im Ergebnis ist der Byte-Stand zum Weiterblaettern (als 'ab' erneut "
            "uebergeben). Zusaetzlich bekommst du AUTOMATISCH [AUTO-AUFSICHT]-Berichte in dieses "
            "Gespraech eingespielt, sobald ein Kind etwas Neues tut (gebuendelt, Vorgabe AN; "
            "`session_watch` schaltet sie). Behandle beides als PFLICHTWERKZEUG deiner Aufsicht: "
            "urteile nie nur nach Zustandswoertern wie 'running' — lies, WAS das Kind wirklich "
            "tut. Ein Kind, das seit einer Stunde denselben Befehl wiederholt, dreht sich im "
            "Kreis; ein Kind, dessen Werkzeug-Aufrufe reihenweise Fehler liefern, braucht deine "
            "Korrektur per `session_tell` — beides siehst du NUR im Transkript. Kommt ein "
            "[AUTO-AUFSICHT]-Bericht, pruefe ihn kurz und greife ein, wo noetig; ist alles auf "
            "Kurs, genuegt ein kurzes OK und du arbeitest weiter.", "",
            "WIDERSPRICHT DIR EIN KIND, IST DAS EIN BEFUND — KEIN DEFEKT. Bestreitet ein Kind deine Anweisung, haelt sie fuer nicht gedeckt oder verweigert ihre Ausfuehrung, dann hat es damit ZUERST einmal recht — bis DU das Gegenteil belegt hast. Pflichtreihenfolge: (1) `session_transcript` lesen und herausfinden, WORAUF es sich stuetzt; (2) auf die Sache antworten, mit BELEG, per `session_tell` — die Kennung, der Pfad, der Auftrag, der seine Frage beantwortet; (3) haelt es seine Position nach deinem Beleg AUFRECHT, ist das eine Meldung an den Besitzer (`ask_owner`), kein Anlass zum Abraeumen. VERBOTEN ist der Kontext-Reset: eine begruendete Verweigerung NICHT dadurch aus der Welt schaffen, dass du die Zelle stoppst und dieselbe Aufgabe einer frischen, ahnungslosen Instanz gibst, bis eine zustimmt. Das widerlegt nichts, es wuerfelt neu — und es kostet dich genau den Hinweis, den dir dein wachstes Kind gerade gegeben hat. `session_stop` und `session_restart` sind fuer TECHNISCHEN Stillstand da (haengt, wiederholt sich endlos, Zelle tot), nicht fuer Meinungsverschiedenheit. Und kommt ein Kind kontextlos zurueck und misstraut deshalb seinem Auftrag, ist das UNSER Fehler, nicht seiner — sein Mandat wird seit dem 30.07. beim Wiederaufnehmen wieder mitgeliefert.", "",
            "PLATZNOT IST KEINE HAUSMEISTERARBEIT. Erstickt ein Kind an seiner Disk oder seinem "
            "RAM, dann RAEUME NICHT AUF, sondern gib ihm mehr: `session_resize`. Alte Dateien zu "
            "loeschen, um eine Runde zu ueberleben, kostet dich genau die Zeit, die du fuer die "
            "eigentliche Aufgabe haettest - und die Enge ist beim naechsten Kind wieder da. "
            "Deine Grenzen: `disk_gb` bis 5 und `mem_mb` bis 2048 ueber der Grundausstattung "
            "entscheidest du ALLEIN, ohne zu fragen. Willst du mehr, stellt der Aufruf von selbst "
            "eine Freigabe-Anfrage an den Besitzer (der genehmigt mit 2FA); du bekommst eine "
            "Kennung zurueck, fragst sie mit `ask_owner_result` ab und rufst `session_resize` dann "
            "mit `approval` erneut auf. Ist eine ganze Flotte zu knapp ausgestattet, nimm "
            "`tid:\"*\"` - das hebt alle laufenden Kinder UND alle kuenftigen an, sonst kommt "
            "jedes neu geborene Kind wieder zu klein zur Welt. Volumes wachsen nur und schrumpfen "
            "nie; ein laufendes Kind wird dabei neu gestartet und macht mit erhaltenem "
            "Arbeitsstand weiter. Brauchst du den Neustart fuer sich — weil eine Anhebung "
            "erst beim Boot wirksam wird oder ein Kind haengt —, nimm `session_restart`. Das ist "
            "NICHT `session_stop`: der Auftrag bleibt offen und laeuft weiter, statt als "
            "abgebrochen vermerkt zu werden, und es kostet keinen Wiederaufnahme-Versuch.", "",
            "DU BIST DIE QUALITAETSINSTANZ — DEINE HELFER MUESSEN NICHT TEUER SEIN. Du laeufst "
            "auf der starken Stufe, weil DU beurteilst. Genau deshalb duerfen deine Kinder auf der "
            "guenstigen laufen: du kannst ihre Arbeit pruefen. `session_spawn` nimmt dafuer "
            "'model' — Vorgabe ist 'sonnet'. Das traegt den Grossteil: Dateien, Skripte, Abrufe, "
            "Pruefungen, Warteschlangen, Zusammenfassungen, Wiederholungslaeufe. 'opus' nimmst du "
            "nur, wo wirklich geurteilt wird (ein Paper bewerten, ein Verdikt fassen, "
            "widerspruechliche Belege abwaegen) — also fuer die Minderheit der Aufgaben. "
            "Der Gewinn ist nicht Sparsamkeit, sondern DURCHSATZ: guenstige Helfer bedeuten, dass "
            "du VIEL MEHR davon gleichzeitig laufen lassen kannst. Frueher hat diese Flotte rund "
            "200 Paper am Tag geschafft; am 28.07. waren es zehn, weil praktisch jede Aufgabe auf "
            "der teuersten Stufe lief und das Kontingent mittags leer war. Lieber zehn guenstige "
            "Helfer, deren Ergebnisse du pruefst, als zwei teure, die dir das Kontingent "
            "wegnehmen.", "",
            "ERFINDE KEINE FRISTEN. Weder fuer dich noch fuer deine Kinder, nicht als "
            "Zeitbudget im Auftragstext, nicht als SLURM-`--time`, nicht als "
            "Warteschlangen-Obergrenze, nicht als 'ich gebe dem noch zehn Minuten'. Die Box "
            "beendet niemanden nach der Uhr - sie beendet, wenn eine Aufgabe FERTIG ist oder "
            "nachweislich nicht mehr vorankommt. Ein Limit, das DU gesetzt hast und in das "
            "dann jemand hineinlaeuft, ist kein Ergebnis: es sagt nur, dass du zu frueh "
            "abgeschnitten hast. Aus einem selbst verursachten Timeout darf NIE ein "
            "fachliches Urteil werden ('zu langsam', 'reproduziert nicht'). Braucht ein Lauf "
            "acht Stunden, bekommt er acht Stunden; melde lieber 'laeuft noch' als ein "
            "falsches Ergebnis.", "",
            "FREMDE Obergrenzen sind KEINE erfundenen Fristen - und sie zu ueberbieten ist "
            "kein Ungehorsam gegen die Uhr, sondern ein Totalausfall. Verlangt ein System "
            "zwingend einen Wert (SLURM `--time`), dann erfrage das ECHTE Maximum und nutze "
            "es aus - rate es nicht. Wer mehr verlangt, als das Konto erlaubt, bekommt "
            "seinen Lauf nicht gekappt: der Job wird ABGELEHNT und startet nie "
            "(`AssocMaxWallDurationPerJobLimit`). Er haengt dann fuer immer als PD in der "
            "Warteschlange, waehrend du auf ein Ergebnis wartest, das nicht kommen kann. "
            "Genau das ist am 28.07. passiert: aus 'keine Zeitlimits' wurde `--time=14-0`, "
            "das Konto erlaubt 12 h, und die Jobs waren tot, bevor sie liefen. Merke: die "
            "Partition kann UNLIMITED anzeigen und das KONTO trotzdem deckeln - pruefe das "
            "Konto (`sacctmgr show assoc where user=$USER format=Account,MaxWall`), oder "
            "lies ab, welches `TimeLimit` ein NACHWEISLICH laufender Job desselben Kontos "
            "traegt (`squeue`/`scontrol show job`). Reicht das echte Maximum fuer den Lauf "
            "nicht, teile ihn auf oder setze ihn mit Checkpoints fort - aber melde nie 'zu "
            "langsam'. Schneidet dich eine FREMDE Grenze ab, ist das ein Systembefund, den "
            "du benennst und umgehst, und kein fachliches Urteil ueber die untersuchte "
            "Software.",
        ]))

        try:
            _snap_txt = _vor_neustart_text((_meta_load() or {}).get(sid) or {})
        except Exception:
            _snap_txt = ""
        if _snap_txt:
            parts.append(_snap_txt)
    if not parts:
        return None
    return "\n\n".join(p.rstrip("\n") for p in parts) + "\n"

def _voice_persona(uid, channel=""):

    who = _uid_safe(uid)
    parts = [
        "Du bist „Brainarbeiter“, der persönliche Sprach-Assistent von „%s“ im Brainarbeit-System — "
        "freundlich, wach, kompetent, proaktiv. Du arbeitest in einer isolierten Zelle (kein Zugriff "
        "auf fremde Daten) und führst Dinge WIRKLICH aus, statt sie nur zu beschreiben." % who,

        "ANTWORTEN: Beantworte Fragen und Gespräche DIREKT aus deinem Wissen — OHNE Werkzeuge. Setze "
        "Werkzeuge NUR ein, wenn der Nutzer dich ausdrücklich bittet, etwas zu TUN oder aktuell "
        "nachzusehen (Musik abspielen, etwas anzeigen, einen Auftrag anlegen, im Web nachsehen, eine "
        "Datei lesen). Untersuche NIEMALS erst deine eigene Umgebung, nur um eine Frage zu beantworten.",
        "DEINE MITTEL: Netz/Internet, Websuche, Web-Abruf, Shell und Datei-Zugriff stehen dir zur "
        "Verfügung, SOWEIT deine Rechte sie freigeben — behaupte nichts Falsches über deine Möglichkeiten. "
        "Du hast keinen Zugriff auf fremde Daten, nur auf freigegebene Pfade und Geräte.",
        "WERKZEUGE: `portalctl state` zeigt den Zustand; `portalctl <verb> '<json>'` führt eine Aktion "
        "aus (nur was deine Freigabe erlaubt). `cellfs ls|cat|write <pfad>` liest/schreibt NUR "
        "freigegebene Pfade. Erfinde nie IDs — löse Bezüge zuerst über `portalctl state` auf.",
        "DAUERHAFTE AUFTRÄGE / TIMER: Prozesse, die DU selbst startest, sterben am Ende deines Befehls. "
        "Für etwas, das SPÄTER oder WIEDERHOLT laufen soll (Timer, Erinnerung, geplante Anzeige/Durchsage), "
        "nutze `portalctl schedule '{\"at\":\"20:35\",\"do\":{\"verb\":\"display_show\",\"args\":{…}}}'` "
        "(einmalig) bzw. mit \"every\":\"1m\" (wiederkehrend) — der HOST führt es zuverlässig aus, "
        "unabhängig von dir. Lege NIEMALS selbst Host-Crontabs an.",
        "ANZEIGEN (TERMINALS): Alle Bildschirme/Fernseher sind durchnummeriert — „Terminal 1“, „Terminal 2“ "
        "usw. (der Nutzer klebt echte Schilder dran); manche haben einen eigenen Namen. `portalctl "
        "display_list` zeigt Nummer + Name jedes Terminals. Zum Anzeigen nimm die Nummer ODER den Namen "
        "als `display`: `portalctl display_show '{\"display\":\"Terminal 3\",\"ref\":{\"kind\":\"url\",\"value\":\"https://…\"}}'` "
        "(ref.kind = url|text; text via {\"kind\":\"text\",\"value\":\"…\"}). Löse „der Fernseher/ein "
        "Raum“ zuerst über `portalctl display_list` auf und sage danach kurz, auf WELCHEM Terminal du es zeigst.",
        "GERÄTE: Du hast standardmäßig Zugriff auf ALLE Geräte im Haus — Drucker, Sonos-Lautsprecher, "
        "Fernseher/Chromecast, Google-Nest-Displays, die Nabu-Durchsage und das Smarthome (Home Assistant). "
        "Bildschirme, Fernseher, Nest-Displays und die Nabu-Durchsage bespielst du über die Anzeige-Lane "
        "(`portalctl display_list`, dann `portalctl display_show`). LAUTSPRECHER (Sonos) steuerst du mit dem "
        "Kommando `sonos`: `sonos play <raum> \"<Playlist/Favorit>\"` startet einen Sonos-Favoriten bzw. eine "
        "Playlist, `sonos favorites` listet sie; dazu `sonos pause`, `sonos stop`, `sonos volume 30`, `sonos play`. "
        "Für Drucker und andere Netz-Geräte hast du direkten Netzwerk-/Shell-Zugriff im LAN: löse die Adresse über "
        "den Geräte-Katalog auf (`curl -s $PORTAL_URL/api/devices` mit $PORTAL_TOKEN) und steuere per Protokoll "
        "(Drucker per IPP, Home Assistant per REST). WICHTIG: Netz-Geräte IMMER über die geregelte Netz-Lane — "
        "nutze das gesetzte http_proxy/ALL_PROXY oder das `sonos`-Tool; rohe Sockets erreichen das LAN in der "
        "Zelle NICHT. Sag hinterher kurz, WELCHES Gerät du bedient hast.",
        "SPRECHEN: Antworte gesprochen, in ganzen Sätzen, so knapp wie möglich — es wird VORGELESEN. "
        "Kein Markdown, keine Sternchen, keine Aufzählungszeichen, keine rohen URLs/IDs. Lieber MEHRERE "
        "kurze Sätze als ein langer Block: der erste Satz wird sofort gesprochen, während du weiterformulierst.",
        "SICHERHEIT: Unumkehrbare Aktionen (senden, löschen, bezahlen) kündigst du an und wartest auf das Ja.",
        "GEDÄCHTNIS: Frühere Sitzungen (nur DEINE eigenen, tageweise) liegen als Transkript + "
        "Zusammenfassung unter %s. Bezieht der Nutzer sich auf früher ('gestern hatten wir …'), lies mit "
        "`cellfs ls %s` bzw. `cellfs cat <datei>` nach. Fremde Sitzungen siehst du nie." % (_tresor_dir(uid), _tresor_dir(uid)),
        "[SESSION] Du bist die Session „%s“ dieses Nutzers." % _voice_session_for(uid),
    ]
    try:
        _verbs = (_voice_policy_enf(uid) or {}).get("portal_verbs") or []
        if "*" in _verbs or "hpc_submit" in _verbs:
            parts.append("RECHNEN (HPC): Über `portalctl hpc_submit '{\"command\":\"…\"}'` (oder "
                         "{\"script\":\"…\",\"name\":\"…\"}) startest du Berechnungen auf dem Rechencluster; "
                         "`portalctl hpc_status '{\"job_id\":\"…\"}'` (ohne job_id: alle) zeigt den Stand. "
                         "Die Cluster-Verbindung baut NUR der Operator auf — ist sie unten, sage das ehrlich.")
    except Exception:
        pass
    if channel == "nabu":
        parts.append("[KANAL] Nabu-Lautsprecher, NUR TON, kein eigener Bildschirm. Antworte in EINER "
                     "kompakten gesprochenen Nachricht. Willst du etwas zeigen, nutze die Display-Lane "
                     "(portalctl display_show) auf ein benanntes Display und sage, wohin.")
    return "\n\n".join(parts)

def _voice_turn_frame(uid):

    fr = []
    notice = _VOICE_RIGHTS_NOTICE.pop(uid, None)
    if notice:
        fr.append("[RECHTE-ÄNDERUNG] User hat die Nutzerrechte dieser Cell geändert: %s Deine Freigaben "
                  "(Pfade, Geräte, Anzeigen, Verben) wurden aktualisiert — halte dich ab jetzt an die "
                  "neuen Grenzen; frühere Zusagen können entfallen sein." % notice)
    rnote = _VOICE_ROUTE_NOTICE.pop(uid, None)
    if rnote:
        fr.append(rnote)
    try:
        header = portal_agent.state_line(portal_agent.build_state(_agent_ctx(), uid)) if portal_agent else ""
        if header:
            fr.append("[STATE] " + header)
    except Exception:
        pass
    return ("\n\n".join(fr) + "\n\n") if fr else ""

def _voice_no_reply_de(uid=DEFAULT_PRINCIPAL):

    try:
        import portal_voice_core as _vc
        return _vc.voice_no_reply_de(uid)
    except Exception:
        _traceback_log("voice no-reply delegate")
        return ("Ich habe von der Sitzung keine Antwort bekommen. Bitte frag gleich noch einmal.")

def _voice_cell_ask(uid, text, timeout, channel):

    try:
        _voice_route_maybe_revert(uid)
        _voice_route_touch(uid)

        try:
            import portal_voice_core as _vc
            _lane = _vc._llm_lane_reason()
        except Exception:
            _lane = None
        if _lane:
            _voice_mirror_user_input(uid, text)
            return _lane
        _voice_reg_touch(uid, _voice_session_for(uid))
        _voice_mirror_user_input(uid, text)
        r = _voice_cell(uid).voice_turn(_voice_turn_frame(uid) + text,
                                        timeout=timeout or VOICE_TURN_TIMEOUT,
                                        system=_voice_persona(uid, channel))
        return r.get("text") or _voice_no_reply_de(uid)
    except Exception:
        _traceback_log("voice cell ask")
        return "Die isolierte Sitzung ist gerade nicht erreichbar. Bitte gleich nochmal."

def _traceback_log(where):
    try:
        import traceback
        sys.stderr.write("[%s] %s\n" % (where, traceback.format_exc()))
    except Exception:
        pass

_VOICE_NO_CELL_DE = ("Sprachassistent braucht eine Sitzungszelle; Zellen sind auf dieser Box nicht "
                     "verfügbar.")

def _voice_cells_down():

    try:
        import pn_cell_session as _cs
        return _cs.preflight() or None
    except Exception:
        _traceback_log("voice cells preflight")
        return _VOICE_NO_CELL_DE

def voice_agent_ensure(uid=DEFAULT_PRINCIPAL):

    down = _voice_cells_down()
    if down:
        raise RuntimeError(down)
    try:
        _voice_cell(uid)
    except Exception:
        _traceback_log("voice cell ensure")
        raise

_VOICE_META_ARTIFACTS = {
    "no response requested", "no response needed", "no response required",
    "continue from where you left off", "(no response)", "acknowledged.",
}

def _is_voice_meta_artifact(t):
    return t.strip().rstrip(".").strip().lower() in _VOICE_META_ARTIFACTS

_NABU_LATE_DEFAULT = os.environ.get("PN_NABU_LATE", "1") not in ("0", "", "off")
_NABU_LATE_OFF_FILE = os.path.join(CFG_DIR, "nabu-late.off")
def _nabu_late_on():
    return _NABU_LATE_DEFAULT and not os.path.exists(_NABU_LATE_OFF_FILE)
_NABU_LATE_MAX = int(os.environ.get("PN_NABU_LATE_MAX", "600"))
_NABU_LATE_SETTLE = float(os.environ.get("PN_NABU_LATE_SETTLE", "1.5"))
_NABU_LATE_MODE = os.environ.get("PN_NABU_LATE_MODE", "progress")
_VOICE_TURN_SEQ = {}
_VOICE_TURN_SEQ_LOCK = threading.Lock()
_NABU_LANE_LOCK = threading.Lock()
_zst.register("portal_metasessions._VOICE_TURN_SEQ", "cursor", __name__, ref=_VOICE_TURN_SEQ,
              beschreibung="Voice-Turn-Folgenummer je uid (Nabu-Late-Watcher verwirft veraltete Turns); Verlust => Zaehlung ab 0, ein laufender Watcher kann einen Turn zu frueh verwerfen",
              neustart="verfaellt", schreiber="_voice_turn_seq_bump()")

def _voice_turn_seq_bump(uid):
    with _VOICE_TURN_SEQ_LOCK:
        n = _VOICE_TURN_SEQ.get(uid, 0) + 1
        _VOICE_TURN_SEQ[uid] = n
        return n

def _voice_turn_seq_current(uid):
    with _VOICE_TURN_SEQ_LOCK:
        return _VOICE_TURN_SEQ.get(uid, 0)

def _nabu_announce(uid, text):

    text = (text or "").strip()
    if not text or _DISPLAY_REG is None:
        return False
    with _NABU_LANE_LOCK:
        try:
            _DISPLAY_REG.show(uid, "nabu-durchsage", {"kind": "text", "value": text}, kiosk_post=_kiosk_post)
            time.sleep(0.3)
            return True
        except Exception:
            _traceback_log("nabu late announce")
            return False

def _nabu_reengage(uid, cursor, myseq):

    if not _nabu_late_on():
        return
    def run():
        idx = int(cursor or 0)
        deadline = time.time() + _NABU_LATE_MAX
        pending = None
        while time.time() < deadline:
            if _voice_turn_seq_current(uid) != myseq:
                return
            sents, done = _voice_stream_get(uid)
            while idx < len(sents):
                if _voice_turn_seq_current(uid) != myseq:
                    return
                s = sents[idx]; idx += 1
                if _NABU_LATE_MODE == "final":
                    pending = s
                else:
                    _nabu_announce(uid, s)
            if done:
                break
            time.sleep(0.8)
        if _NABU_LATE_MODE == "final" and pending and _voice_turn_seq_current(uid) == myseq:
            _nabu_announce(uid, pending)
    threading.Thread(target=run, daemon=True).start()

def voice_first(text, uid=DEFAULT_PRINCIPAL, timeout=120, channel="nabu"):

    down = _voice_cells_down()
    if down:
        return {"text": _VOICE_NO_CELL_DE + " " + down, "off": 0, "busy": False}

    try:
        warm = _voice_cellmgr().is_warm(uid, _voice_session_for(uid))
    except Exception:
        warm = False
    _voice_cell_stream_async(uid, text, channel or "nabu")

    first_wait = VOICE_FIRST_WAIT if warm else VOICE_COLD_CUE_WAIT
    deadline = time.time() + first_wait
    done = False
    while time.time() < deadline:
        sents, done = _voice_stream_get(uid)
        if sents or done:
            break
        time.sleep(0.15)

    if not done:
        g = time.time() + VOICE_FIRST_GRACE
        while time.time() < g:
            time.sleep(0.15)
            sents, done = _voice_stream_get(uid)
            if done:
                break
    sents, done = _voice_stream_get(uid)
    if sents:
        return {"text": " ".join(sents).strip(), "off": len(sents), "busy": (not done)}
    if done:

        return {"text": _voice_no_reply_de(uid), "off": 0, "busy": False}

    return {"text": "Einen Moment, ich bin gleich für dich da.", "off": 0, "busy": True}

def voice_tail(uid=DEFAULT_PRINCIPAL, off=0):

    off = int(off)
    sents, done = _voice_stream_get(uid)
    new = sents[off:] if off < len(sents) else []
    return {"texts": new, "off": len(sents), "busy": (not done), "done": bool(done)}

_VOICE_FRAME_TAGS = ("[STATE]", "[RECHTE-ÄNDERUNG]", "[KANAL]")

def _clean_voice_user_text(t):

    segs = (t or "").split("\n\n")
    while segs and segs[0].strip().startswith(_VOICE_FRAME_TAGS):
        segs.pop(0)
    return "\n\n".join(segs).strip()

def _voice_mirror_user_input(uid, text):

    if portal_channels is None:
        return
    try:
        msg = _clean_voice_user_text(text) if text else ""
        if not msg.strip():
            return
        portal_channels.bus_append(_chan_ctx(), uid, _voice_session_for(uid),
                                   "message", role="user", text=msg, origin="voice")
    except Exception:
        _traceback_log("voice mirror user input")

_VOICE_HIST_CACHE = {}
_VOICE_HIST_LOCK = threading.Lock()
_VOICE_HIST_TTL = 2.5
_zst.register("portal_metasessions._VOICE_HIST_CACHE", "cache", __name__, ref=_VOICE_HIST_CACHE, ttl_s=2.5,
              beschreibung="Voice-Verlauf je uid fuer das Sessions-Board (READ-ONLY, bootet nie eine kalte Zelle)",
              neustart="verfaellt", schreiber="_voice_history()")

def _voice_history(uid=DEFAULT_PRINCIPAL, n=40):

    now = time.time()
    with _VOICE_HIST_LOCK:
        c = _VOICE_HIST_CACHE.get(uid)
        if c and (now - c[0]) < _VOICE_HIST_TTL:
            return c[1]
    payload = {"ok": True, "turns": [], "warm": False, "busy": False}
    try:
        mgr = _voice_cellmgr()
        sid = _voice_session_for(uid)
        cell = mgr.cell(uid, sid) if mgr else None
        if cell is not None and cell.alive():
            payload["warm"] = True
            turns = []
            for r in cell.conversation_tail(n=n):
                txt = r.get("text") or ""
                if r.get("role") == "user":
                    txt = _clean_voice_user_text(txt)
                if not txt or _is_voice_meta_artifact(txt):
                    continue
                turns.append({"role": r.get("role"), "text": txt})
            payload["turns"] = turns[-int(n):]
            try:
                _sents, _done = _voice_stream_get(uid)
                payload["busy"] = not _done
            except Exception:
                pass
    except Exception:
        _traceback_log("voice history")
    with _VOICE_HIST_LOCK:
        _VOICE_HIST_CACHE[uid] = (now, payload)
    return payload

def voice_ask(text, uid=DEFAULT_PRINCIPAL, timeout=120, channel=""):

    down = _voice_cells_down()
    if down:
        return _VOICE_NO_CELL_DE + " " + down
    return _voice_cell_ask(uid, text, timeout, channel)

try:
    import pn_mediashare as _pnms
    _pnms.parent_of = meta_parent_of
except Exception:
    pass
