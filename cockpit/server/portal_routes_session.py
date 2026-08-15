
import os, json, base64, subprocess, time
import re
import sys
import threading
import urllib.parse, urllib.request

DATA_DIR = None
VOICE_CELL = None
_SESSION_SUMMARY_CACHE = None
_SESSION_SUMMARY_LOCK = None
_SESS_EFFORTS = None
_SESS_MODELS = None

def _models_live(runtime=None):

    try:
        import portal_session_svc as _svc
        return _svc.sess_models(runtime)
    except Exception:
        return _SESS_MODELS or []

def _default_models():

    ids = [str(m.get("id")) for m in _models_live() if m.get("id")]
    def pick(env, hist):
        v = os.environ.get(env)
        if v:
            return v
        if hist in ids:
            return hist
        return ids[0] if ids else hist
    return pick("PN_SESSION_MODEL", "opus"), pick("PN_DEFAULT_MODEL", "sonnet")

_MEDIASHARE = None
_MEDIASHARE_TRIED = False

def _share_mgr():

    global _MEDIASHARE, _MEDIASHARE_TRIED
    if _MEDIASHARE is None and not _MEDIASHARE_TRIED:
        _MEDIASHARE_TRIED = True
        try:
            import pn_mediashare
            _MEDIASHARE = pn_mediashare.ShareManager()
            try:
                import pn_media_janitor
                pn_media_janitor.janitor_start(_MEDIASHARE)
            except Exception:
                _traceback_log('media janitor start')
        except Exception:
            _MEDIASHARE = None
    return _MEDIASHARE

def _share_pub(sid, title=None, principal=None):

    mgr = _share_mgr()
    if mgr is None:
        return None
    try:
        rec = mgr.get(sid)
        if rec is None:

            rec = mgr.ensure_share(sid, title=title, principal=principal)
        return rec
    except Exception:
        return None

def _eigene_session_ablage(principal, kind, sid):

    try:
        if str(kind or "cockpit") != "cockpit":
            return []
        mgr = _share_mgr()
        if mgr is None:
            return []
        rec = mgr.get(sid)
        if not isinstance(rec, dict):
            return []
        pfad = rec.get("path")
        if not pfad:
            return []
        besitzer = rec.get("owner_uid")
        if besitzer:
            import pn_mediashare as _ms
            if str(besitzer) != str(_ms._sso_uid(principal) or ""):
                return []
        raus = [str(pfad)]
        try:
            raus.append(os.path.join(mgr.archive_dir_for(rec),
                                     os.path.basename(str(pfad).rstrip("/"))))
        except Exception:
            pass
        return raus
    except Exception:
        return []

try:
    import pn_session_policy as _pol_blatt
    if hasattr(_pol_blatt, "set_own_share_provider"):
        _pol_blatt.set_own_share_provider(_eigene_session_ablage)
except Exception:
    pass

_SHARE_GRANTED = set()
_SHARE_STAGED = set()

import portal_zustand as _zst
_zst.register("portal_routes_session._SHARE_GRANTED", "snapshot", __name__, ref=_SHARE_GRANTED,
              beschreibung="(uid, sid) mit bereits sichergestelltem Austausch-Grant; Verlust => idempotente Wiederholung des Grants (Doppelarbeit, kein Schaden)",
              neustart="verfaellt", schreiber="_ensure_share_grant()")
_zst.register("portal_routes_session._SHARE_STAGED", "snapshot", __name__, ref=_SHARE_STAGED,
              beschreibung="(uid, sid) mit bereits live gestagtem Austausch-Sync (Retry bis Zelle warm); Verlust => erneutes Stagen (idempotent)",
              neustart="verfaellt", schreiber="_ensure_share_grant()")

def _ensure_share_grant(uid, sid, rec):

    try:
        path = (rec or {}).get("path")
        if not path or not uid or not sid:
            return
        key = (str(uid), str(sid))

        if key not in _SHARE_GRANTED:
            pol = (_sess_policy_get(uid, sid) if callable(_sess_policy_get) else {}) or {}
            caps = pol.setdefault("caps", {})
            changed = False
            for k in ("fs_read", "fs_write"):
                rows = caps.setdefault(k, [])
                have = False
                for row in rows:
                    rp0 = row.get("path") if isinstance(row, dict) else row
                    if rp0 and os.path.realpath(str(rp0)) == os.path.realpath(path):
                        have = True
                        break
                if not have:
                    rows.append({"path": path, "mode": "rw"})
                    changed = True
            if changed:
                st = _sess_policy_store() if callable(_sess_policy_store) else None
                if st is not None:
                    st.set(uid, "cockpit", sid, pol)
            _SHARE_GRANTED.add(key)

        if key not in _SHARE_STAGED:
            try:
                import pn_cell_session as _cs
                mgr = _cs.get_manager()
                cell = mgr.get(uid, sid) if mgr is not None else None
                alive = False
                try:
                    alive = bool(cell is not None and cell.alive())
                except Exception:
                    alive = cell is not None
                if alive:

                    enf = None
                    try:
                        if (callable(_voice_sess_name) and callable(_voice_policy_enf)
                                and sid == _voice_sess_name()):
                            enf = _voice_policy_enf(uid)
                    except Exception:
                        enf = None
                    if enf is None and _cockpit_policy_enf is not None:
                        enf = _cockpit_policy_enf(uid, sid)
                    if enf is not None:
                        cell.update_policy(enf)

                    if cell._stage_exchange():
                        _SHARE_STAGED.add(key)
            except Exception:
                pass
    except Exception:
        pass

def _share_grant_vergessen(uid, sid):

    key = (str(uid), str(sid))
    _SHARE_GRANTED.discard(key)
    _SHARE_STAGED.discard(key)

def _share_pub_granted(sid, title, uid, neu_pruefen=False):
    if neu_pruefen:
        _share_grant_vergessen(uid, sid)
    rec = _share_pub(sid, title, principal=uid)
    _ensure_share_grant(uid, sid, rec)
    return rec

import collections as _nf_collections
import threading as _nf_threading
import time as _nf_time

_UNREAD_STATE = {}
_UNREAD_CACHE = {}
_UNREAD_LOCK = _nf_threading.Lock()
_UNREAD_TAIL = 262144
_zst.register("portal_routes_session._UNREAD_STATE", "cursor", __name__, ref=_UNREAD_STATE,
              beschreibung="Unread-Plane: je uid ein Byte-Cursor in den session-bus + je sid ein Ring (seq, ts, Vorschau). Der GELESEN-Stand (last-seen-seq) liegt persistent in DATA_DIR/unread-seen.json; nur Byte-Cursor + Ringe sind RAM. Verlust => Tail-Rescan (256k), rote Zahlen zaehlen ab Tail neu — Doppelarbeit, keine Doppelzustellung.",
              neustart="rekonstruiert", schreiber="Unread-Leser unter _UNREAD_LOCK")
_zst.register("portal_routes_session._UNREAD_CACHE", "cache", __name__, ref=_UNREAD_CACHE, ttl_s=2.0,
              beschreibung="Unread-Zaehler je uid, 2 s TTL gegen Board+Shell-Doppel-Poll",
              neustart="verfaellt", schreiber="Unread-Leser")

def _unread_seen_path():
    return os.path.join(DATA_DIR, "unread-seen.json")

def _unread_seen_load():
    try:
        with open(_unread_seen_path()) as f:
            return json.load(f) or {}
    except Exception:
        return {}

def _unread_seen_set(uid, sid, seq):
    with _UNREAD_LOCK:
        d = _unread_seen_load()
        d.setdefault(str(uid), {})[str(sid)] = int(seq)
        tmp = _unread_seen_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, _unread_seen_path())

def _unread_ingest(uid):

    if portal_channels is None or _chan_ctx is None or not DATA_DIR:
        return {}
    with _UNREAD_LOCK:
        st = _UNREAD_STATE.get(uid)
        try:
            _gen = portal_channels.bus_gen(_chan_ctx())
        except Exception:
            _gen = 0
        if st is not None and st.get("gen", 0) != _gen:

            st = None
        if st is None:
            try:
                size = os.path.getsize(os.path.join(DATA_DIR, "session-bus.jsonl"))
            except OSError:
                size = 0
            st = _UNREAD_STATE[uid] = {"byte": max(0, size - _UNREAD_TAIL), "sids": {}, "gen": _gen}
        try:
            evs, nb = portal_channels.bus_read(_chan_ctx(), st["byte"], principal=uid, limit=5000)
            st["byte"] = nb
        except Exception:
            return st["sids"]
        for ev in evs:
            try:
                if ev.get("kind") != "message":
                    continue

                _nf = ev.get("notify") or ("normal" if ev.get("role") == "assistant" else "ambient")
                if _nf == "ambient":
                    continue
                sid = str(ev.get("sid") or "")
                if not sid:
                    continue
                dq = st["sids"].setdefault(sid, _nf_collections.deque(maxlen=50))
                dq.append((int(ev.get("seq") or 0), float(ev.get("ts") or 0),
                           str(ev.get("text") or "")[:160], _nf))
            except Exception:
                continue
        return st["sids"]

def _unread_counts(uid):
    now = _nf_time.time()
    c = _UNREAD_CACHE.get(uid)
    if c and now - c[0] < 2.0:
        return c[1]
    sids = _unread_ingest(uid)
    seen = _unread_seen_load().get(str(uid), {})
    out = {}
    for sid, dq in list(sids.items()):
        sv = int(seen.get(sid) or 0)
        items = [e for e in dq if e[0] > sv]
        if not items:
            continue
        last = items[-1]
        out[sid] = {"unread": len(items), "last_seq": last[0], "ts": last[1], "preview": last[2],
                    "alert": sum(1 for e in items if len(e) > 3 and e[3] == "alert")}
    _UNREAD_CACHE[uid] = (now, out)
    return out

def _unread_count(uid, sid):
    try:
        return (_unread_counts(uid).get(str(sid)) or {}).get("unread", 0)
    except Exception:
        return 0

_MSG_TALLY = {}
_zst.register("portal_routes_session._MSG_TALLY", "cache", __name__, ref=_MSG_TALLY, ttl_s=10.0,
              beschreibung="grobe Nachrichten-Zahl je Session aus dem letzten 256-KB-Busfenster (Board-Statistik)",
              neustart="verfaellt", schreiber="_board_msg_counts()")

def _board_msg_counts(uid):

    now = _nf_time.time()
    c = _MSG_TALLY.get(uid)
    if c and now - c[0] < 10.0:
        return c[1]
    counts = {}
    if portal_channels is not None and _chan_ctx is not None and DATA_DIR:
        try:
            size = os.path.getsize(os.path.join(DATA_DIR, "session-bus.jsonl"))
            evs, _ = portal_channels.bus_read(_chan_ctx(), max(0, size - 262144),
                                              principal=uid, limit=8000)
            for ev in evs:
                if ev.get("kind") == "message" and ev.get("role") in ("user", "assistant"):
                    sid = str(ev.get("sid") or "")
                    if sid:
                        counts[sid] = counts.get(sid, 0) + 1
        except Exception:
            counts = {}
    _MSG_TALLY[uid] = (now, counts)
    return counts

_SESS_RUNTIMES = None
_SESS_RUNTIME_IDS = None
_apikeys = None
_cell_app = None
_cell_app_status = None
_cell_desktop = None
_cell_desktop_status = None
_flathub_search = None
_cell_freeze = None
_cell_kill_erase = None
KIT_DECKEL = int(os.environ.get("PN_KIT_DECKEL", "14"))
_cell_power = None

_RELAY_WARMING = set()
_RELAY_WARM_LOCK = threading.Lock()
def _relay_warm_kick(principal, sid):
    key = (principal, sid)
    with _RELAY_WARM_LOCK:
        if key in _RELAY_WARMING:
            return False
        _RELAY_WARMING.add(key)
    def _run():
        try:
            if callable(_cell_power):
                _cell_power(principal, sid, True)
        except Exception as e:
            sys.stderr.write("[pn-session] %s: Hochfahren im Hintergrund "
                             "fehlgeschlagen: %s\n" % (sid, e))
        finally:
            with _RELAY_WARM_LOCK:
                _RELAY_WARMING.discard(key)
    threading.Thread(target=_run, name="zelle-hochfahren", daemon=True).start()
    return True

_cell_resources = None
_cells_enabled = None
_chan_ctx = None
_cockpit_policy_enf = None
_fabric = None
_kill_tmux_tree = None
_meta_ensure_for_session = None
_meta_load = None
_meta_update = None
_policy = None
_portal_base_url = None
_prov_log = None
_sess_policy_get = None
_sess_policy_store = None
_sesscell_reg = None
_sesscells = None
_session_hard_freeze = None
_session_hard_kill = None
_session_pause_notify = None
_session_store = None
_sessprov_del = None
_sessprov_get = None
_sessprov_set = None
_traceback_log = None
_tresor_dir = None
_vext = None
_vext_ctx = None
_voice_agent_token = None
_voice_cellmgr = None
_voice_policy_enf = None
_voice_sess_name = None
_voice_session_for = None
_vpn_registry = None
_watchdog_health = None
portal_channels = None
pn_req = None

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

try:
    from portal_terminal import HOSTSHELL_GONE_REASON as _HOSTSHELL_GONE
except Exception:
    _HOSTSHELL_GONE = ("Host-Shell entfernt — Arbeit läuft in Session-Zellen, "
                       "Box-Verwaltung per SSH.")

def _vpn_is_cluster(entry):

    if not isinstance(entry, dict):
        return False
    if entry.get("cluster") is True or entry.get("hpc") is True:
        return True
    for k in ("kind", "role", "class", "category"):
        if str(entry.get(k, "")).strip().lower() in ("hpc", "cluster", "compute"):
            return True
    hay = " ".join(str(entry.get(k, "")) for k in ("purpose", "name", "label", "role")).lower()
    return ("hpc" in hay) or ("cluster" in hay) or ("slurm" in hay)

_ERKLAER = {}
_ERKLAER_LK = threading.Lock()
_ERKLAER_STALL_S = 420.0
_ERKLAER_KEEP_S = 600.0
_zst.register("portal_routes_session._ERKLAER", "snapshot", __name__, ref=_ERKLAER,
              beschreibung="Stand der On-Demand-Beobachter-Laeufe je (uid, sid); Ground truth ist /root/.obs/state IN der Zelle — Verlust => Poll sieht den Lauf nicht mehr, die Zelle arbeitet weiter",
              neustart="verfaellt", schreiber="Request-Threads unter _ERKLAER_LK")

class SessionRoutes:
    def _fs_get(self, query):

        if _fabric is None:
            return self.send_html(json.dumps({"error": "fabric unavailable"}), 500,
                                  [("Content-Type", "application/json")])
        qs = urllib.parse.parse_qs(query)
        app = (qs.get("app", ["notes"])[0]) or "notes"
        fname = (qs.get("file", [""])[0])
        store = _fabric.open_store(self._principal(), app)
        if not fname:
            return self.send_html(json.dumps({"app": app, "files": store.list_files()}), 200,
                                  [("Content-Type", "application/json")])
        try:
            with store.open(fname, "rb") as fh:
                data = fh.read()
        except (FileNotFoundError, ValueError):
            return self.send_html(json.dumps({"error": "not found"}), 404,
                                  [("Content-Type", "application/json")])
        return self._browse_send("application/octet-stream", data)

    def _fs_write(self, query, body):

        if _fabric is None:
            return self.send_html(json.dumps({"error": "fabric unavailable"}), 500,
                                  [("Content-Type", "application/json")])
        qs = urllib.parse.parse_qs(query)
        app = (qs.get("app", ["notes"])[0]) or "notes"
        fname = (qs.get("file", [""])[0])
        op = (qs.get("op", [""])[0])
        if not fname:
            return self.send_html(json.dumps({"error": "no file"}), 400,
                                  [("Content-Type", "application/json")])
        store = _fabric.open_store(self._principal(), app)
        try:
            if op == "delete":
                ok = store.delete(fname)
            else:
                with store.open(fname, "wb") as fh:
                    fh.write(body if isinstance(body, (bytes, bytearray)) else body.encode("utf-8"))
                ok = True
        except (ValueError, OSError) as e:
            return self.send_html(json.dumps({"error": str(e)}), 400,
                                  [("Content-Type", "application/json")])
        return self.send_html(json.dumps({"ok": ok}), 200, [("Content-Type", "application/json")])

    def _term_page(self):

        css = "/api/browse?raw=1&url=" + urllib.parse.quote(
            "https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.css", safe="")
        js = "/api/browse?raw=1&url=" + urllib.parse.quote(
            "https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.js", safe="")
        page = (
            "<!doctype html><html><head><meta charset=utf-8><title>terminal</title>"
            "<link rel=stylesheet href='%s'>"
            "<style>html,body{margin:0;height:100%%;background:#0b0b14}#t{height:100%%;padding:6px;box-sizing:border-box}</style>"
            "<script src='%s'></script></head><body><div id=t></div><script>"
            "var term=new Terminal({cursorBlink:true,fontFamily:'ui-monospace,Menlo,monospace',fontSize:14,"
            "theme:{background:'#0b0b14',foreground:'#d6d6e7'}});"
            "term.open(document.getElementById('t'));"
            "function fit(){var c=Math.max(20,Math.floor(window.innerWidth/9)),r=Math.max(6,Math.floor(window.innerHeight/19));"
            "try{term.resize(c,r);if(ws&&ws.readyState===1)ws.send(JSON.stringify({resize:[c,r]}));}catch(e){}}"
            "var proto=location.protocol==='https:'?'wss':'ws';"
            "var ws=new WebSocket(proto+'://'+location.host+'/api/term');ws.binaryType='arraybuffer';"
            "ws.onmessage=function(e){term.write(new Uint8Array(e.data));};"
            "term.onData(function(d){if(ws.readyState===1)ws.send(d);});"
            "ws.onopen=function(){fit();term.focus();};"
            "ws.onclose=function(){term.write('\\r\\n\\x1b[31m[verbindung getrennt]\\x1b[0m\\r\\n');};"
            "window.addEventListener('resize',fit);"
            "</script></body></html>") % (css, js)
        return self.send_html(page)

    def _term_ws(self):

        if _fabric is None or _fabric.termbridge is None:
            return self.send_html("terminal unavailable", 500)
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            return self.send_html("expected websocket upgrade", 400)
        store = _fabric.open_store(self._principal(), "terminal")
        acc = _fabric.termbridge.ws_accept(key)
        try:
            self.wfile.write((
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                "Sec-WebSocket-Accept: %s\r\n\r\n" % acc).encode())
            self.wfile.flush()
        except OSError:
            return
        self.close_connection = True
        try:
            _fabric.termbridge.run(self.connection, ["/bin/bash", "-l"], store.files_dir)
        except Exception:
            pass

    def _sessions_list(self, query=""):

        try:
            alles = _session_store(self._principal()).list()
            mit = str(urllib.parse.parse_qs(query or "").get("include_archived", ["0"])[0]).lower() \
                in ("1", "true", "yes", "on")
            sessions = alles if mit else [s for s in alles if not s.get("archived")]
            return self._sess_json({"ok": True, "sessions": sessions,
                                    "archiviert": sum(1 for s in alles if s.get("archived"))})
        except Exception as e:
            return self._sess_json({"ok": False, "error": str(e)}, 500)

    def _sessions_last(self):
        try:
            return self._sess_json({"ok": True, "session": _session_store(self._principal()).last_active()})
        except Exception as e:
            return self._sess_json({"ok": False, "error": str(e)}, 500)

    _NO_COCKPIT_MSG = ("Terminal nicht verfügbar (tmux fehlt) — eine Sitzung würde sich nie "
                       "verbinden können und wird deshalb nicht angelegt.")

    _SID_RE = re.compile(r"^[a-z0-9]{6,16}$")
    _UNKNOWN_SID_MSG = "Unbekannte Session — es gibt hier nichts mit diesem Kennzeichen."

    def _sid_known(self, uid, sid, kind="cockpit"):

        try:
            return _session_store(uid, kind).get(sid) is not None
        except Exception:
            _traceback_log("sid known")
            return None

    def _sess_files_base(self, sid):
        principal = self._principal()
        bad = self._bad_sid(sid, principal)
        if bad:
            return None, bad
        rec = _share_pub(sid, principal=principal)
        path = (rec or {}).get("path")
        if not path or not os.path.isdir(path):
            return None, self._sess_json(
                {"ok": False, "error": "Kein Austausch-Ordner für diese Session (Share-Dienst aus?)"}, 404)
        return os.path.realpath(path), None

    def _sess_files_resolve(self, base, sub):
        p = os.path.realpath(os.path.join(base, (sub or "").lstrip("/\\")))
        if p != base and not p.startswith(base + os.sep):
            return None
        return p

    def _api_session_files(self, query):
        q = urllib.parse.parse_qs(query or "")
        sid = (q.get("sid", [""])[0] or "").strip()
        base, err = self._sess_files_base(sid)
        if err is not None:
            return err
        p = self._sess_files_resolve(base, q.get("sub", [""])[0])
        if p is None:
            return self._sess_json({"ok": False, "error": "Pfad außerhalb des Ordners"}, 403)
        if not os.path.isdir(p):
            return self._sess_json({"ok": False, "error": "Ordner nicht gefunden"}, 404)
        out = []
        try:
            with os.scandir(p) as it:
                for e in it:
                    if e.name.startswith(".pn-") or e.name in (".sync-state", ".DS_Store"):
                        continue
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    out.append({"name": e.name, "dir": e.is_dir(follow_symlinks=False),
                                "size": int(st.st_size), "mtime": int(st.st_mtime)})
        except OSError as e:
            return self._sess_json({"ok": False, "error": "Lesen fehlgeschlagen: %s" % e}, 500)
        out.sort(key=lambda r: (not r["dir"], r["name"].lower()))
        rel = "" if p == base else p[len(base) + 1:]
        return self._sess_json({"ok": True, "sub": rel, "entries": out[:500],
                                "truncated": len(out) > 500})

    def _api_session_explain_file(self, raw):

        try:
            body = json.loads(raw.decode("utf-8", "replace") or "{}")
        except Exception:
            return self._sess_json({"ok": False, "error": "bad json"}, 400)
        sid = str(body.get("sid") or "").strip()
        rel = str(body.get("path") or "").strip()
        if not sid or not rel:
            return self._sess_json({"ok": False, "error": "sid und path noetig"}, 400)
        base, err = self._sess_files_base(sid)
        if err is not None:
            return err
        p = self._sess_files_resolve(base, rel)
        if p is None or not os.path.isfile(p):
            return self._sess_json({"ok": False, "error": "Datei nicht gefunden"}, 404)
        try:
            size = os.path.getsize(p)
            with open(p, "rb") as f:
                head = f.read(4000)
        except OSError as e:
            return self._sess_json({"ok": False, "error": str(e)}, 500)
        printable = sum(1 for b in head if 9 <= b <= 13 or 32 <= b < 127 or b >= 160)
        binary = bool(head) and printable / max(1, len(head)) < 0.75
        excerpt = "(Binaerdatei)" if binary else head.decode("utf-8", "replace")[:2000]
        try:
            siblings = sorted(os.listdir(os.path.dirname(p)))[:40]
        except OSError:
            siblings = []
        title = sid
        try:
            import portal_session_svc as _psvc
            title = (_psvc._sessprov_get(self._principal(), sid) or {}).get("title") or sid
        except Exception:
            pass
        prompt = (
            "Du erklaerst einem Menschen in EINER Datei-Ansicht, was eine bestimmte Datei ist.\n"
            "Antworte auf Deutsch, HOECHSTENS zwei kurze Saetze, ohne Anrede, ohne Codeblock,\n"
            "konkret auf dieses Vorhaben bezogen (z. B. 'Hilfsskript fuer das Spiel: erzeugt die\n"
            "Gegner-Wellen').\n\n"
            "Vorhaben/Session: %s\nDatei: %s (%d Bytes)\nWeitere Dateien im Ordner: %s\n\n"
            "Anfang der Datei:\n%s\n" % (title, rel, size, ", ".join(siblings) or "-", excerpt)
        )

        quick = _file_gist(rel, size, head, binary, excerpt)
        if not body.get("deep"):
            return self._sess_json({"ok": True, "path": rel, "text": quick, "heuristic": True})
        try:
            import portal_email_portioneer as _llm
            res = _llm.llm_run_core(
                prompt,
                system="Du bist ein knapper, praeziser Datei-Erklaerer. Zwei Saetze, kein Vorwort.",
                timeout=int(body.get("timeout") or 60)) or {}
        except Exception:
            res = {}
        text = " ".join(str(res.get("text") or "").split())[:400] if res.get("ok") else ""
        if not text:
            return self._sess_json({"ok": True, "path": rel, "text": quick, "heuristic": True})
        return self._sess_json({"ok": True, "path": rel, "text": text})

    def _api_session_file(self, query):
        import mimetypes
        q = urllib.parse.parse_qs(query or "")
        sid = (q.get("sid", [""])[0] or "").strip()
        base, err = self._sess_files_base(sid)
        if err is not None:
            return err
        p = self._sess_files_resolve(base, q.get("sub", [""])[0])
        if p is None or not os.path.isfile(p):
            return self._sess_json({"ok": False, "error": "Datei nicht gefunden"}, 404)
        try:
            st = os.stat(p)
            if st.st_size > 512 * 2**20:
                return self._sess_json({"ok": False, "error": "Datei zu groß für den Browser-Download "
                                        "— bitte über das Netzlaufwerk holen"}, 413)
            with open(p, "rb") as f:
                data = f.read()
        except OSError as e:
            return self._sess_json({"ok": False, "error": str(e)}, 500)
        name = os.path.basename(p)
        ascii_name = name.encode("ascii", "replace").decode()
        ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition",
                         "attachment; filename=\"%s\"; filename*=UTF-8''%s"
                         % (ascii_name, urllib.parse.quote(name)))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _api_session_file_upload(self, query, body):
        q = urllib.parse.parse_qs(query or "")
        sid = (q.get("sid", [""])[0] or "").strip()
        name = os.path.basename((q.get("name", [""])[0] or "").strip())
        if not name or name.startswith("."):
            return self._sess_json({"ok": False, "error": "Dateiname fehlt/ungültig"}, 400)
        if body is None or len(body) > 256 * 2**20:
            return self._sess_json({"ok": False, "error": "Datei fehlt oder ist größer als 256 MiB"}, 413)
        base, err = self._sess_files_base(sid)
        if err is not None:
            return err
        d = self._sess_files_resolve(base, q.get("sub", [""])[0])
        if d is None or not os.path.isdir(d):
            return self._sess_json({"ok": False, "error": "Zielordner nicht gefunden"}, 404)
        dest = os.path.join(d, name)
        try:
            tmp = dest + ".pn-up"
            with open(tmp, "wb") as f:
                f.write(body)
            os.replace(tmp, dest)
        except OSError as e:
            return self._sess_json({"ok": False, "error": "Schreiben fehlgeschlagen: %s" % e}, 500)
        return self._sess_json({"ok": True, "name": name, "size": len(body)})

    def _bad_sid(self, sid, uid=None, kind="cockpit"):

        if not self._SID_RE.match(str(sid or "")):
            self._sess_json({"ok": False, "error": "bad sid"}, 400)
            return 400
        if uid is not None and self._sid_known(uid, sid, kind) is False:
            self._sess_json({"ok": False, "error": self._UNKNOWN_SID_MSG, "sid": sid}, 404)
            return 404
        return None

    def _q_session(self, q):

        raw = q.get("sid", [None])[0]
        if raw is None:
            raw = q.get("session", [None])[0]
        if raw is None:
            return None, False
        return (str(raw).strip() or None), True

    def _api_relay_onbehalf(self, raw):

        import os as _os, json as _json, hmac as _hmac, sqlite3 as _sq
        ip = self.client_address[0] if self.client_address else ""
        if ip not in ("127.0.0.1", "::1"):
            return self._sess_json({"ok": False, "error": "forbidden"}, 403)
        try:
            want = open(_os.path.expanduser("~/.local/share/portioneer/relay/bridge.secret")).read().strip()
        except Exception:
            want = ""
        got = self.headers.get("X-Relay-Bridge", "")
        if not want or not _hmac.compare_digest(str(got), str(want)):
            return self._sess_json({"ok": False, "error": "forbidden"}, 403)
        try:
            body = _json.loads(raw or b"{}")
        except Exception:
            return self._sess_json({"ok": False, "error": "bad json"}, 400)
        did = str(body.get("did") or "").strip()
        op = str(body.get("op") or "").strip()
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        if not did or not op:
            return self._sess_json({"ok": False, "error": "did+op required"}, 400)

        principal = None
        try:
            from pnlib import DB_PATH
            cx = _sq.connect("file:%s?mode=ro" % DB_PATH, uri=True)
            try:
                r = cx.execute("SELECT principal, verified FROM identities "
                               "WHERE method='device-channel' AND selector=?", (did,)).fetchone()
            finally:
                cx.close()
            if r and r[1]:
                principal = r[0]
        except Exception:
            principal = None
        if not principal:
            return self._sess_json({"ok": False, "error": "device not bound"}, 403)

        if op == "sessions.list":
            inc = bool(args.get("include_archived"))
            try:
                alles = _session_store(principal).list()
            except Exception as e:
                return self._sess_json({"ok": False, "error": "list: %s" % e}, 500)
            out = []
            for s in alles:
                if s.get("archived") and not inc:
                    continue
                out.append({"sid": s.get("sid") or s.get("id"),
                            "title": s.get("title") or s.get("name") or "Sitzung",
                            "updated": s.get("updated") or s.get("last_active") or s.get("ts"),
                            "unread": s.get("unread"), "state": s.get("state"),
                            "archived": bool(s.get("archived"))})
            return self._sess_json({"ok": True, "sessions": out})

        if op == "session.transcript":
            sid = str(args.get("sid") or "").strip()
            if not sid:
                return self._sess_json({"ok": False, "error": "sid required"}, 400)
            try:
                if _session_store(principal).get(sid) is None:
                    return self._sess_json({"ok": False, "error": "no such session"}, 404)
            except Exception:
                return self._sess_json({"ok": False, "error": "no such session"}, 404)
            try:
                since = int(args.get("since") or 0)
            except Exception:
                since = 0
            turns = self._bus_turns(principal, sid, since) or []
            try:
                lim = int(args.get("limit") or 200)
            except Exception:
                lim = 200
            if lim > 0 and len(turns) > lim:
                turns = turns[-lim:]
            out = [{"i": t.get("i"), "role": t.get("role"), "text": t.get("text"),
                    "ts": t.get("ts"), "model": self._model_label(t.get("model"))} for t in turns]
            resp = {"ok": True, "sid": sid, "turns": out}
            if args.get("act"):
                resp["activity"] = self._bounded_call(
                    lambda: self._relay_activity(principal, sid), 2.0, None)
            return self._sess_json(resp)

        if op == "session.say":
            sid = str(args.get("sid") or "").strip()
            text = args.get("text")
            if not sid or not isinstance(text, str) or not text.strip():
                return self._sess_json({"ok": False, "error": "sid+text required"}, 400)
            if portal_channels is None or _chan_ctx is None:
                return self._sess_json({"ok": False, "error": "channels unavailable"}, 503)
            b2 = _json.dumps({"sid": sid, "text": text}).encode()
            payload, code = portal_channels.session_say(_chan_ctx(), principal, b2)
            return self._sess_json(payload, code)

        if op == "stt":
            if not self.cfg.get("caps", {}).get("voice"):
                return self._sess_json({"ok": False, "error": "voice nicht installiert"}, 200)
            import base64 as _b64, secrets as _sec, tempfile as _tf
            try:
                data = _b64.b64decode(str(args.get("audio") or ""), validate=True)
            except Exception:
                return self._sess_json({"ok": False, "error": "bad audio"}, 400)
            if not data or len(data) > 8 * 1024 * 1024:
                return self._sess_json({"ok": False, "error": "empty or too-large audio"}, 400)
            try:
                import portal_voice_core as _pvc
                _dir = _pvc.TTS_DIR or _tf.gettempdir()
                _os.makedirs(_dir, exist_ok=True)
                tmp = _os.path.join(_dir, "in-" + _sec.token_hex(6) + ".webm")
                with open(tmp, "wb") as _f:
                    _f.write(data)
                try:
                    res = _pvc.voice_request({"op": "stt", "path": tmp, "lang": "de"})
                finally:
                    try: _os.remove(tmp)
                    except Exception: pass
            except Exception as e:
                return self._sess_json({"ok": False, "error": "stt: %s" % e}, 503)
            if res.get("error"):
                return self._sess_json({"ok": False, "error": res["error"]}, 200)
            return self._sess_json({"ok": True, "text": (res.get("text") or "").strip(), "lang": res.get("lang")})

        if op == "voice.turn":
            if not self.cfg.get("caps", {}).get("voice"):
                return self._sess_json({"ok": False, "error": "voice nicht installiert"}, 200)
            text = str(args.get("text") or "").strip()
            if not text:
                return self._sess_json({"ok": False, "error": "leerer Text"}, 400)
            try:
                import portal_metasessions as _pms
                r = _pms.voice_first(text[:4000], principal, channel="offlan")
            except Exception as e:
                return self._sess_json({"ok": False, "error": "voice: %s" % e}, 503)
            return self._sess_json({"ok": True, "speak": (r.get("text") or "").strip(),
                                    "cursor": int(r.get("off") or 0), "busy": bool(r.get("busy"))})
        if op == "voice.tail":
            try:
                off = int(args.get("cursor") or 0)
            except Exception:
                off = 0
            try:
                import portal_metasessions as _pms
                r = _pms.voice_tail(principal, off)
            except Exception as e:
                return self._sess_json({"ok": False, "error": "voice: %s" % e}, 503)
            return self._sess_json({"ok": True, "texts": r.get("texts") or [],
                                    "cursor": int(r.get("off") or off), "busy": bool(r.get("busy")),
                                    "done": bool(r.get("done"))})
        if op == "tts":
            if not self.cfg.get("caps", {}).get("voice"):
                return self._sess_json({"ok": False, "error": "voice nicht installiert"}, 200)
            text = str(args.get("text") or "").strip()[:3000]
            if not text:
                return self._sess_json({"ok": False, "error": "leerer Text"}, 400)
            import base64 as _b64t, secrets as _sect, tempfile as _tft
            wav = b""
            try:
                import portal_voice_core as _pvc
                _dir = _pvc.TTS_DIR or _tft.gettempdir()
                _os.makedirs(_dir, exist_ok=True)
                _out = _os.path.join(_dir, "say-" + _sect.token_hex(6) + ".wav")
                res = _pvc.voice_request({"op": "tts", "text": text, "out": _out})
                if not res.get("error"):
                    try:
                        with open(_out, "rb") as _f:
                            wav = _f.read()
                    except OSError:
                        wav = b""
                try: _os.remove(_out)
                except OSError: pass
            except Exception as e:
                return self._sess_json({"ok": False, "error": "tts: %s" % e}, 503)
            if res.get("error"):
                return self._sess_json({"ok": False, "error": res["error"]}, 200)
            if not wav or len(wav) <= 44:
                return self._sess_json({"ok": False, "error": "keine Audiodaten"}, 200)
            return self._sess_json({"ok": True, "wav_b64": _b64t.b64encode(wav).decode(),
                                    "engine": res.get("engine")})

        if op == "voice.warm":
            try:
                import portal_voice_core as _pvc
                ready = bool(_pvc._voiced_tcp_alive(1.0))
                if not ready:
                    try: _pvc.start_voiced()
                    except Exception: pass
            except Exception as e:
                return self._sess_json({"ok": False, "error": "warm: %s" % e}, 200)
            return self._sess_json({"ok": True, "ready": ready})

        if op == "session.new":
            title = str(args.get("title") or "").strip()[:120]
            try:
                s = _session_store(principal).create(title or None)
            except Exception as e:
                return self._sess_json({"ok": False, "error": "neu: %s" % e}, 500)
            try: _share_pub_granted(s.get("id"), title, principal)
            except Exception: pass
            return self._sess_json({"ok": True, "sid": s.get("id"),
                                    "title": s.get("title") or title or "Sitzung"})
        if op == "session.archive":
            sid = str(args.get("sid") or "").strip()
            if not re.match(r"^[a-z0-9]{6,16}$", sid):
                return self._sess_json({"ok": False, "error": "bad sid"}, 400)
            an = bool(args.get("on", True))
            try:
                import portal_archive
                r = portal_archive.setzen(principal, sid, an, grund="Handy", akteur="mensch")
                if not r.get("ok"):
                    return self._sess_json({"ok": False, "error": r.get("error") or "fehlgeschlagen"}, 400)
                return self._sess_json({"ok": True, "on": an})
            except ImportError:
                s = _session_store(principal).set_archived(sid, an)
                if not s:
                    return self._sess_json({"ok": False, "error": "no such session"}, 404)
                return self._sess_json({"ok": True, "on": an})
            except Exception as e:
                return self._sess_json({"ok": False, "error": str(e)}, 500)
        if op == "session.rename":
            sid = str(args.get("sid") or "").strip()
            if not re.match(r"^[a-z0-9]{6,16}$", sid):
                return self._sess_json({"ok": False, "error": "bad sid"}, 400)
            try:
                s = _session_store(principal).rename(sid, str(args.get("title") or "").strip()[:120])
                if not s:
                    return self._sess_json({"ok": False, "error": "no such session"}, 404)
                return self._sess_json({"ok": True, "title": s.get("title")})
            except Exception as e:
                return self._sess_json({"ok": False, "error": str(e)}, 500)

        if op == "session.info":
            sid = str(args.get("sid") or "").strip()
            if not re.match(r"^[a-z0-9]{6,16}$", sid):
                return self._sess_json({"ok": False, "error": "bad sid"}, 400)
            try:
                s0 = _session_store(principal).get(sid)
            except Exception:
                s0 = None
            if s0 is None:
                return self._sess_json({"ok": False, "error": "no such session"}, 404)
            try:
                prov = dict(_sessprov_get(principal, sid) or {})
            except Exception:
                prov = {}
            try:
                pol = _sess_policy_get(principal, sid) or {}
            except Exception:
                pol = {}
            pcaps = (pol.get("caps") or {}) if isinstance(pol, dict) else {}
            caps = {}
            for _c in ("net_general", "net_internal", "websearch", "webfetch", "device_connect"):
                _v = pcaps.get(_c)
                if _v is not None:
                    caps[_c] = _v
            auton = prov.get("autonomy")
            try:
                if _sesscells is not None and auton is not None:
                    auton = _sesscells.normalize_level(auton)
            except Exception:
                pass
            info = {
                "title": s0.get("title") or s0.get("name"),
                "runtime": prov.get("runtime") or "claude-tmux",
                "model": prov.get("model"), "effort": prov.get("effort"),
                "autonomy": auton,
                "preset": prov.get("preset") or (pol.get("preset") if isinstance(pol, dict) else None),
                "orchestrator": bool(prov.get("orchestrator")),
                "max_concurrent": prov.get("max_concurrent"),
                "vpn_dauerjob": bool(prov.get("vpn_dauerjob")),
                "vpn": prov.get("vpn"),
                "mem_mb": prov.get("mem_mb"), "disk_mb": prov.get("disk_mb"),
                "duration_h": prov.get("duration_h"),
                "kits": list(prov.get("kits") or []),
                "caps": caps, "provisioned": bool(prov),
            }
            return self._sess_json({"ok": True, "sid": sid, "info": info})

        if op == "session.meta":
            try:
                return self._sess_json({"ok": True, **self._session_meta()})
            except Exception as e:
                return self._sess_json({"ok": False, "error": "meta: %s" % e}, 500)
        if op == "session.provision":
            try:
                return self._api_session_provision(_json.dumps(args).encode(), uid_override=principal)
            except Exception as e:
                return self._sess_json({"ok": False, "error": "provision: %s" % e}, 500)

        if op in ("decisions.list", "decisions.decide", "decisions.dismiss"):
            try:
                import portal_metafeatures as _mf
                from portal_users import user_get as _ug
                import portal_admin as _pa
            except Exception as e:
                return self._sess_json({"ok": False, "error": "decisions: %s" % e}, 500)
            try:
                _role = (_ug(principal) or {}).get("role")
            except Exception:
                _role = None
            try:
                is_admin = bool(_pa.require_admin(_role))
            except Exception:
                is_admin = False
            is_kid = (_role == "kid")
            if op == "decisions.list":
                state = str(args.get("state") or "pending").strip().lower()
                if state == "all":
                    state = None
                try:
                    cards = _mf.appr_list(principal, state=state, include_kids=is_admin)
                except Exception as e:
                    return self._sess_json({"ok": False, "error": "decisions: %s" % e}, 500)
                for c in (cards or []):
                    own = (c.get("principal") == principal)
                    mine = is_admin if c.get("an_owner") else (is_admin or (own and not is_kid))
                    c["decider"] = "self" if mine else "parent"
                    c["needs_2fa"] = bool(mine and c.get("kind") == "approval")
                return self._sess_json({"ok": True, "approvals": cards or []})
            if op == "decisions.dismiss":
                res = _mf.appr_dismiss(principal, args.get("aid"), include_kids=is_admin)
                return self._sess_json(res, 200 if res.get("ok") else 404)

            aid = args.get("aid")
            rec = _mf.appr_get(principal, aid, include_kids=is_admin)
            if not rec:
                return self._sess_json({"ok": False, "error": "Unbekannte oder schon entschiedene Anfrage."}, 404)
            decision = str(args.get("decision") or "").strip().lower() or None
            if rec.get("kind") == "approval":
                if decision not in ("approve", "deny"):
                    return self._sess_json({"ok": False, "error": "decision approve|deny erforderlich."}, 400)

                ok2, reason = self._verify_winthin_totp(str(args.get("totp") or "").strip())
                if not ok2:
                    r = (reason or "").lower()
                    if "not armed" in r:
                        msg = ("Kein 2FA/Handy-Code eingerichtet — im Portal unter "
                               "„Off-LAN Freigaben & 2FA“ einrichten. Es wurde NICHTS entschieden.")
                    elif "locked" in r:
                        msg = ("2FA ist nach zu vielen Fehlversuchen kurz gesperrt. Es wurde NICHTS "
                               "entschieden — die Anfrage bleibt offen und danach normal entscheidbar.")
                    else:
                        msg = ("2FA-Code abgelaufen oder falsch — es wurde NICHTS entschieden. "
                               "Der Code wechselt alle 30 s: einen frischen vom Handy holen und erneut bestätigen.")
                    return self._sess_json({"ok": False, "need_2fa": True, "undecided": True,
                                            "state": "pending", "error": msg}, 403)
            res = _mf.appr_answer(principal, aid, answer=args.get("answer"),
                                  decision=decision, include_kids=is_admin)
            return self._sess_json(res, 200 if res.get("ok") else 400)

        if op in ("files.list", "files.get"):
            sid = str(args.get("sid") or "").strip()
            bad = self._bad_sid(sid, principal)
            if bad:
                return bad
            rec = _share_pub(sid, principal=principal)
            fpath = (rec or {}).get("path")
            if not fpath or not _os.path.isdir(fpath):
                return self._sess_json({"ok": False, "error": "Kein Austausch-Ordner für diese Session"}, 404)
            fbase = _os.path.realpath(fpath)
            if op == "files.list":
                p2 = self._sess_files_resolve(fbase, str(args.get("sub") or ""))
                if p2 is None:
                    return self._sess_json({"ok": False, "error": "Pfad außerhalb des Ordners"}, 403)
                if not _os.path.isdir(p2):
                    return self._sess_json({"ok": False, "error": "Ordner nicht gefunden"}, 404)
                out = []
                try:
                    with _os.scandir(p2) as it:
                        for e in it:
                            if e.name.startswith(".pn-") or e.name in (".sync-state", ".DS_Store"):
                                continue
                            try:
                                st = e.stat(follow_symlinks=False)
                            except OSError:
                                continue
                            out.append({"name": e.name, "dir": e.is_dir(follow_symlinks=False),
                                        "size": int(st.st_size), "mtime": int(st.st_mtime)})
                except OSError as e:
                    return self._sess_json({"ok": False, "error": "Lesen fehlgeschlagen: %s" % e}, 500)
                out.sort(key=lambda r: (not r["dir"], r["name"].lower()))
                rel = "" if p2 == fbase else p2[len(fbase) + 1:]
                return self._sess_json({"ok": True, "sub": rel, "entries": out[:500],
                                        "truncated": len(out) > 500})

            import base64 as _b64f, mimetypes as _mt
            p2 = self._sess_files_resolve(fbase, str(args.get("path") or ""))
            if p2 is None or not _os.path.isfile(p2):
                return self._sess_json({"ok": False, "error": "Datei nicht gefunden"}, 404)
            CHUNK = 256 * 1024
            MAXGET = 512 * 1024 * 1024
            try:
                size = _os.path.getsize(p2)
            except OSError as e:
                return self._sess_json({"ok": False, "error": str(e)}, 500)
            if size > MAXGET:
                return self._sess_json({"ok": False, "error":
                    "Datei zu groß fürs Handy (%d MB) — über das Netzlaufwerk holen." % (size // (1024 * 1024))}, 413)
            try:
                offset = max(0, int(args.get("offset") or 0))
            except Exception:
                offset = 0
            try:
                ln = int(args.get("len") or CHUNK)
            except Exception:
                ln = CHUNK
            ln = max(1, min(ln, CHUNK))
            if offset > size:
                return self._sess_json({"ok": False, "error": "offset > size"}, 400)
            try:
                with open(p2, "rb") as f:
                    f.seek(offset)
                    data = f.read(ln)
            except OSError as e:
                return self._sess_json({"ok": False, "error": str(e)}, 500)
            ctype = _mt.guess_type(p2)[0] or "application/octet-stream"
            eof = (offset + len(data)) >= size
            return self._sess_json({"ok": True, "path": str(args.get("path") or ""),
                                    "name": _os.path.basename(p2), "size": size, "offset": offset,
                                    "got": len(data), "chunk": _b64f.b64encode(data).decode(),
                                    "eof": eof, "ctype": ctype})

        if op == "session.warm":
            sid = str(args.get("sid") or "").strip()
            bad = self._bad_sid(sid, principal)
            if bad:
                return bad
            warm = False
            try:
                import pn_cell_session as _cs
                warm = bool(_cs.get_manager().is_warm(principal, sid))
            except Exception:
                warm = False
            if not warm:
                try:
                    _relay_warm_kick(principal, sid)
                except Exception:
                    pass
            return self._sess_json({"ok": True, "warm": warm})

        if op == "push.pubkey":
            try:
                import pn_webpush as _wp
                return self._sess_json({"ok": True, "key": _wp.vapid_public_key()})
            except Exception as e:
                return self._sess_json({"ok": False, "error": "push: %s" % e}, 500)
        if op == "push.subscribe":
            sub = args.get("sub") if isinstance(args.get("sub"), dict) else None
            if not sub:
                return self._sess_json({"ok": False, "error": "kein Abo"}, 400)
            try:
                import pn_webpush as _wp
                n = _wp.abo_anlegen(principal, sub)
                return self._sess_json({"ok": True, "count": n})
            except ValueError as e:
                return self._sess_json({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                return self._sess_json({"ok": False, "error": "push: %s" % e}, 500)
        if op == "push.unsubscribe":
            ep = str(args.get("endpoint") or "")
            try:
                import pn_webpush as _wp
                _wp.abo_entfernen(principal, ep)
                return self._sess_json({"ok": True})
            except Exception as e:
                return self._sess_json({"ok": False, "error": "push: %s" % e}, 500)
        if op == "files.put":
            import base64 as _b64u
            sid = str(args.get("sid") or "").strip()
            bad = self._bad_sid(sid, principal)
            if bad:
                return bad
            rec = _share_pub(sid, principal=principal)
            fpath = (rec or {}).get("path")
            if not fpath or not _os.path.isdir(fpath):
                return self._sess_json({"ok": False, "error": "Kein Austausch-Ordner für diese Session"}, 404)
            fbase = _os.path.realpath(fpath)
            d = self._sess_files_resolve(fbase, str(args.get("sub") or ""))
            if d is None or not _os.path.isdir(d):
                return self._sess_json({"ok": False, "error": "Zielordner nicht gefunden"}, 404)
            name = _os.path.basename(str(args.get("name") or "").strip())
            if not name or name.startswith("."):
                return self._sess_json({"ok": False, "error": "Dateiname fehlt/ungültig"}, 400)
            dest = _os.path.join(d, name)
            tmp = dest + ".pn-up"
            CHUNK = 256 * 1024
            MAXPUT = 256 * 1024 * 1024
            try:
                offset = max(0, int(args.get("offset") or 0))
            except Exception:
                offset = 0
            try:
                data = _b64u.b64decode(str(args.get("chunk") or ""), validate=True)
            except Exception:
                return self._sess_json({"ok": False, "error": "bad chunk"}, 400)
            if len(data) > 2 * CHUNK:
                return self._sess_json({"ok": False, "error": "chunk too large"}, 400)
            if offset + len(data) > MAXPUT:
                try: _os.remove(tmp)
                except OSError: pass
                return self._sess_json({"ok": False, "error":
                    "Datei zu groß fürs Handy (>256 MiB) — über das Netzlaufwerk laden."}, 413)
            try:
                cur = _os.path.getsize(tmp) if _os.path.exists(tmp) else 0
            except OSError:
                cur = 0
            if offset == 0:
                mode = "wb"
            elif offset == cur:
                mode = "r+b"
            else:
                return self._sess_json({"ok": False, "error": "offset mismatch", "expected": cur}, 409)
            try:
                with open(tmp, mode) as f:
                    f.seek(offset)
                    f.write(data)
            except OSError as e:
                return self._sess_json({"ok": False, "error": "Schreiben fehlgeschlagen: %s" % e}, 500)
            if bool(args.get("eof")):
                try:
                    _os.replace(tmp, dest)
                    size = _os.path.getsize(dest)
                except OSError as e:
                    return self._sess_json({"ok": False, "error": "Abschluss fehlgeschlagen: %s" % e}, 500)
                return self._sess_json({"ok": True, "name": name, "size": size, "done": True})
            return self._sess_json({"ok": True, "name": name, "offset": offset + len(data), "done": False})
        return self._sess_json({"ok": False, "error": "unknown op %r" % op}, 400)

    def _model_label(self, m):
        if not m:
            return None
        low = str(m).lower()
        for key, lab in (("opus", "Opus 4.8"), ("sonnet", "Sonnet 5"),
                         ("haiku", "Haiku 4.5"), ("fable", "Fable 5")):
            if key in low:
                return lab
        base = str(m).split("/")[-1].split("-")[0].strip()
        return base[:16] or None

    def _bounded_call(self, fn, timeout, default):
        import threading as _th
        box = [default]
        def _run():
            try:
                box[0] = fn()
            except Exception:
                pass
        t = _th.Thread(target=_run, daemon=True)
        t.start(); t.join(timeout)
        return box[0]

    def _relay_activity(self, principal, sid):
        now = time.time()
        try:
            la = (_session_store(principal).get(sid) or {}).get("last_active")
        except Exception:
            la = None
        try:
            import pn_cell_session as _cs
            cell = _cs.get_manager().get(principal, sid)
        except Exception:
            cell = None
        if cell is None:
            return {"warm": False, "running": False, "working": False, "last_active": la}
        if not bool(self._bounded_call(cell.alive, 1.5, False)):
            return {"warm": False, "running": False, "working": False, "last_active": la}
        try:
            paused = bool((_sessprov_get(principal, sid) or {}).get("paused"))
        except Exception:
            paused = False
        health = None; toks = None; jact = None
        try:
            import pn_session_watchdog as _wd
            health = _wd.health(principal, sid) if hasattr(_wd, "health") else None
            toks = _wd.tokens_of(principal, sid) if hasattr(_wd, "tokens_of") else None
            jact = _wd.jsonl_activity_of(principal, sid) if hasattr(_wd, "jsonl_activity_of") else None
        except Exception:
            pass
        prog = None
        try:
            if hasattr(cell, "progress_cache"):
                prog = cell.progress_cache()
        except Exception:
            prog = None
        obs = None
        try:
            ob = getattr(cell, "_observer", None)
            if ob and ob.get("text"):
                obs = {"text": ob["text"][:240], "problem": bool(ob.get("problem"))}
        except Exception:
            obs = None
        grew = (jact or {}).get("grew_ts") if isinstance(jact, dict) else None
        prog_age = prog.get("age_s") if isinstance(prog, dict) else None
        if grew:
            active_age = max(0, int(now - float(grew)))
        elif prog_age is not None:
            active_age = int(prog_age)
        elif la:
            active_age = max(0, int(now - float(la)))
        else:
            active_age = None
        FRESH, STALL = 240, 1500
        working = None if active_age is None else (active_age <= FRESH)
        preview = None
        if isinstance(prog, dict) and prog.get("tail"):
            for ln in reversed(str(prog["tail"]).splitlines()):
                ln = ln.strip("#-*> \t")
                if ln:
                    preview = ln[:160]
                    break
        if not preview and obs:
            preview = obs["text"][:160]
        st = str((health or {}).get("state") or "")
        restarting = st in ("restarting", "restart", "failed", "stalled")
        return {"warm": True, "running": (not paused), "paused": paused,
                "working": working, "active_age_s": active_age, "age_s": prog_age,
                "tokens_ctx": toks, "preview": preview,
                "problem": bool(obs and obs.get("problem")),
                "restarting": restarting,
                "stale": (active_age is not None and active_age >= STALL),
                "last_active": la}

    def _bus_turns(self, principal, sid, since=0):

        if portal_channels is None or _chan_ctx is None:
            return []
        try:

            turns = portal_channels.bus_turns_indexed(_chan_ctx(), principal, sid)
        except Exception:
            return []
        if since > 0:
            kept = [t for t in turns if t["i"] >= since]

            _now = _nf_time.time()
            for t in turns:
                if t["i"] < since and t.get("edited") and (_now - float(t.get("ts_edit") or 0)) < 900:
                    kept.append({"i": t["i"], "edit_of": t.get("seq"), "seq": t.get("seq"),
                                 "role": t.get("role"), "text": t["text"],
                                 "ts": t.get("ts_edit") or t.get("ts"),
                                 "sticky": t.get("sticky"), "edited": True})
            turns = kept
        return turns

    def _max_sessions_limit(self, uid):

        if pn_req is None:
            return 0
        try:
            r = pn_req({"verb": "get-policy", "target_principal": uid}) or {}
            return int((r.get("policy") or {}).get("max_sessions") or 0)
        except Exception:
            return 0

    def _sessions_new(self, raw):
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return self._sess_json({"ok": False, "error":
                "Der Anfrage-Body muss ein JSON-Objekt sein."}, 400)

        try:
            cockpit_ok = bool(self.caps().get("cockpit"))
        except Exception:
            cockpit_ok = True
        if not cockpit_ok:
            return self._sess_json({"ok": False, "error": self._NO_COCKPIT_MSG,
                                    "cap": "cockpit", "kind": "cockpit"}, 503)

        _uid = self._principal()
        try:
            _is_adm = self._is_admin()
        except Exception:
            _is_adm = False
        if not _is_adm:
            _lim = self._max_sessions_limit(_uid)
            if _lim:
                try:
                    _act = sum(1 for r in _session_store(_uid).list()
                               if r.get("state") not in ("deleted",) and not r.get("archived"))
                except Exception:
                    _act = 0
                if _act >= _lim:
                    return self._sess_json({"ok": False, "limit": _lim, "active": _act,
                        "error": ("Session-Limit erreicht (%d gleichzeitige Sitzungen). "
                                  "Archiviere eine Sitzung oder bitte den Admin um mehr." % _lim)}, 429)
        try:
            s = _session_store(self._principal()).create(body.get("title"))

            try:
                _share_pub_granted(s.get("id"), body.get("title"), self._principal())
            except Exception:
                pass
            return self._sess_json({"ok": True, "id": s.get("id"), "tmux": s.get("tmux"), "session": s})
        except Exception:
            _traceback_log("sessions new")
            return self._sess_json({"ok": False, "error":
                "Sitzung konnte nicht angelegt werden."}, 500)

    def _sessions_rename(self, raw):
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        sid = str(body.get("sid") or "").strip()

        if sid == "__voice__":
            return self._sess_json({"ok": False, "error":
                "Die Sprachsession trägt ihren festen Namen — Umbenennen ist hier nicht vorgesehen."}, 400)
        bad = self._bad_sid(sid)
        if bad:
            return bad
        try:
            s = _session_store(self._principal()).rename(sid, body.get("title", ""))
            if not s:
                return self._sess_json({"ok": False, "error": self._UNKNOWN_SID_MSG, "sid": sid}, 404)
            return self._sess_json({"ok": True, "session": s})
        except Exception as e:
            return self._sess_json({"ok": False, "error": str(e)}, 500)

    def _sessions_keep(self, raw):
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        sid = str(body.get("sid") or "").strip()

        if sid == "__voice__":
            return self._sess_json({"ok": True, "sid": sid, "kept": True,
                                    "note": "Die Sprachsession ist immer behalten."})
        bad = self._bad_sid(sid)
        if bad:
            return bad
        try:
            s = _session_store(self._principal()).mark_kept(sid)
            if not s:
                return self._sess_json({"ok": False, "error": self._UNKNOWN_SID_MSG, "sid": sid}, 404)
            return self._sess_json({"ok": True, "session": s})
        except Exception as e:
            return self._sess_json({"ok": False, "error": str(e)}, 500)

    def _sessions_archive(self, raw):

        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        sid = str(body.get("sid") or "").strip()

        if sid == "__voice__":
            return self._sess_json({"ok": False, "error":
                "Die Sprachsession bleibt immer warm — Archivieren ist für sie ausgeschaltet."}, 400)
        if not re.match(r"^[a-z0-9]{6,16}$", sid):
            return self._sess_json({"ok": False, "error": "bad sid"}, 400)
        an = bool(body.get("on", True))
        try:

            import portal_archive
            r = portal_archive.setzen(self._principal(), sid, an,
                                      grund=str(body.get("grund") or "vom Menschen gedrueckt"),
                                      akteur="mensch")
            if not r.get("ok"):
                return self._sess_json(r, 404 if "Unbekannte" in str(r.get("error")) else 400)
            return self._sess_json({"ok": True, "session": r.get("session"),
                                    "flaechen": r.get("flaechen")})
        except ImportError:
            s = _session_store(self._principal()).set_archived(sid, an)
            if not s:
                return self._sess_json({"ok": False, "error": self._UNKNOWN_SID_MSG, "sid": sid}, 404)
            return self._sess_json({"ok": True, "session": s})
        except Exception as e:
            return self._sess_json({"ok": False, "error": str(e)}, 500)

    def _sessions_download(self, query):
        sid = urllib.parse.parse_qs(query).get("sid", [""])[0]
        if not re.match(r"^[a-z0-9]{6,16}$", sid):
            return self._sess_json({"ok": False, "error": "bad sid"}, 400)
        try:
            st = _session_store(self._principal())
            import portal_sessions as _ps
            rec = next((s for s in st._load() if s.get("id") == sid), None)
            if rec is not None and rec.get("state") == _ps.STATE_DELETED:
                return self._sess_json({"ok": False, "error": "session deleted"}, 404)
            p = st.transcript_path(sid)
            data = None
            if os.path.exists(p):
                data = open(p, "rb").read()
            else:
                tm = st.tmux_name(sid)
                if subprocess.run(["tmux", "has-session", "-t", tm], capture_output=True).returncode == 0:
                    data = subprocess.run(["tmux", "capture-pane", "-p", "-S", "-100000", "-t", tm],
                                          capture_output=True).stdout
            if not data:
                return self._sess_json({"ok": False, "error": "no transcript"}, 404)
            return self.send_html(data, 200, [("Content-Type", "text/plain; charset=utf-8"),
                                              ("Content-Disposition", "attachment; filename=session-%s.log" % sid)])
        except Exception as e:
            return self._sess_json({"ok": False, "error": str(e)}, 500)

    def _cellfs_forbidden(self, rp):
        low = rp.lower()
        if any(s in low for s in self._CELLFS_DENY):
            return True

        try:
            if os.path.realpath(rp).startswith(os.path.realpath(DATA_DIR)):
                return True
        except Exception:
            pass
        return False

    def _cellfs_policy(self, query, principal):

        try:
            q = urllib.parse.parse_qs(query or "")
        except Exception:
            q = {}
        sid = (q.get("session", [None])[0] or q.get("sid", [None])[0])
        if not sid:
            try:
                sid = self.headers.get("X-Pn-Session-Sid", "") or None
            except Exception:
                sid = None
        if not sid:
            try:
                sid = _voice_session_for(principal)
            except Exception:
                sid = "default"
        sid = re.sub(r"[^A-Za-z0-9_.-]", "", str(sid or "default"))[:64] or "default"
        try:
            st = self._policy_store()

            kind = "cockpit" if (st.get(principal, "cockpit", sid) or None) else "voice"
            return st.effective(principal, kind, sid, global_floor=self._policy_floor())
        except Exception:
            return _policy.new_policy("minimal") if _policy else {"caps": {}}

    def _cellfs_ceiling(self, principal):

        try:
            if self._is_admin():
                return None
        except Exception:
            pass
        roots = []
        try:
            roots.append(os.path.realpath(_tresor_dir(principal)))
        except Exception:
            pass
        try:
            import pn_mediashare as _pm
            _mgr = _share_mgr()
            _suid = _pm._sso_uid(principal)
            if _mgr is not None and _suid:
                roots.append(os.path.realpath(_mgr.user_dir(_suid)))
        except Exception:
            pass
        try:
            uf = self._policy_store().get_user_floor(principal) or {}
            fcaps = uf.get("caps") or {}
            for key in ("fs_read", "fs_write"):
                for row in (fcaps.get(key) or []):
                    gp = row.get("path") if isinstance(row, dict) else row
                    if gp and str(gp).startswith("/"):
                        try:
                            roots.append(os.path.realpath(gp))
                        except Exception:
                            pass
        except Exception:
            pass
        return roots

    @staticmethod
    def _cellfs_within(rp, roots):
        for r in (roots or []):
            if r and (rp == r or rp.startswith(r.rstrip("/") + "/")):
                return True
        return False

    def _cellfs_allowed(self, rp, need, principal, query):

        pol = self._cellfs_policy(query, principal)
        caps = (pol or {}).get("caps") or {}
        grants = list(caps.get(need) or [])
        if need == "fs_read":
            try:
                grants.append({"path": _tresor_dir(principal), "mode": "ro"})
            except Exception:
                pass
        for row in grants:
            gp = row.get("path") if isinstance(row, dict) else row
            if not gp or not str(gp).startswith("/"):
                continue
            try:
                gr = os.path.realpath(gp)
            except Exception:
                continue
            if rp == gr or rp.startswith(gr.rstrip("/") + "/"):

                ceiling = self._cellfs_ceiling(principal)
                if ceiling is not None and not self._cellfs_within(rp, ceiling):
                    continue
                return True
        return False

    def _cellfs_roots(self, query, principal):

        pol = self._cellfs_policy(query, principal)
        caps = (pol or {}).get("caps") or {}
        roots = {}
        def add(path, mode):
            if not path or not str(path).startswith("/"):
                return
            try:
                rp = os.path.realpath(path)
            except Exception:
                return
            _ceil = self._cellfs_ceiling(principal)
            if _ceil is not None and not self._cellfs_within(rp, _ceil):
                return
            if not os.path.isdir(rp):
                return
            cur = roots.get(rp)
            if cur is None:
                roots[rp] = {"path": rp, "mode": mode, "name": os.path.basename(rp.rstrip("/")) or rp}
            elif mode == "rw":
                cur["mode"] = "rw"
        tz = None
        try:
            tz = os.path.realpath(_tresor_dir(principal)); add(tz, "ro")
        except Exception:
            pass
        for row in (caps.get("fs_read") or []):
            add(row.get("path") if isinstance(row, dict) else row, "ro")
        for row in (caps.get("fs_write") or []):
            add(row.get("path") if isinstance(row, dict) else row, "rw")
        rl = list(roots.values())
        scope_root = tz if (tz and tz in roots) else (rl[0]["path"] if rl else None)
        return {"ok": True, "scope": "session", "scope_root": scope_root, "roots": rl}

    def _api_cellfs(self, op, query, raw):
        principal = self._principal()
        body = None
        if op in ("write", "rename", "mkdir"):
            body = self._json_obj()
            if body is None:
                return
            p = body.get("path", "")
            content = None
            if op == "write":
                try:
                    content = base64.b64decode(body.get("content_b64", "") or "")
                except Exception:
                    return self._sess_json({"ok": False, "error": "bad content"}, 400)
        else:
            p = urllib.parse.parse_qs(query).get("path", [""])[0]
            content = None
        if op == "ls" and not p:
            return self._sess_json(self._cellfs_roots(query, principal))
        if not p or not str(p).startswith("/"):
            return self._sess_json({"ok": False, "error": "absoluter Pfad nötig"}, 400)
        if "\x00" in p:
            return self._sess_json({"ok": False, "error": "ungültiger Pfad"}, 400)
        rp = os.path.realpath(p)
        if self._cellfs_forbidden(rp):
            return self._sess_json({"ok": False, "error": "Pfad ist gesperrt (sensibel)"}, 403)

        _need = "fs_write" if op in ("write", "rename", "mkdir") else "fs_read"
        if not self._cellfs_allowed(rp, _need, principal, query):
            try:
                _prov_log("cellfs.deny", principal, json.dumps({"op": op, "path": rp, "need": _need}), {"wire": "cellfs"})
            except Exception:
                pass
            try:

                sys.stderr.write("[cellfs-deny] wer=%s op=%s need=%s path=%s\n"
                                 % (str(principal)[:60], op, _need, rp[:200]))
            except Exception:
                pass
            return self._sess_json({"ok": False, "error":
                "Pfad nicht in der Session-Allowlist (deny-by-default) — erst in den Rechten freigeben"}, 403)
        try:
            if op == "ls":

                if os.path.isfile(rp):
                    st = os.stat(rp)
                    return self._sess_json({"ok": True, "entries": [{"name": os.path.basename(rp), "dir": False, "size": st.st_size, "mtime": int(st.st_mtime)}]})
                entries = []
                for n in sorted(os.listdir(rp))[:2000]:
                    fp = os.path.join(rp, n)
                    try:
                        st = os.stat(fp)
                        _d = os.path.isdir(fp)
                        entries.append({"name": n, "dir": _d,
                                        "size": (0 if _d else st.st_size), "mtime": int(st.st_mtime)})
                    except OSError:
                        continue
                return self._sess_json({"ok": True, "path": rp, "entries": entries})
            if op == "read":
                if not os.path.isfile(rp):
                    return self._sess_json({"ok": False, "error": "keine Datei"}, 404)
                _q = urllib.parse.parse_qs(query or "")
                try:
                    _off = int((_q.get("offset", ["0"])[0]) or 0)
                    _ln = int((_q.get("length", ["0"])[0]) or 0)
                except ValueError:
                    return self._sess_json({"ok": False, "error": "offset/length ungültig"}, 400)
                if _off < 0 or _ln < 0 or _ln > self._CELLFS_MAX:
                    return self._sess_json({"ok": False, "error": "offset/length ungültig"}, 400)
                if _ln == 0 and os.path.getsize(rp) > self._CELLFS_MAX:

                    return self._sess_json({"ok": False, "error": "Datei zu groß (>8 MB) — offset/length nutzen"}, 413)
                with open(rp, "rb") as f:
                    if _off:
                        f.seek(_off)
                    data = f.read(_ln) if _ln else f.read()
                return self.send_html(data, 200, [("Content-Type", "application/octet-stream")])
            if op == "stat":
                st = os.stat(rp)
                return self._sess_json({"ok": True, "path": rp, "dir": os.path.isdir(rp),
                                        "size": st.st_size, "mtime": int(st.st_mtime)})
            if op == "write":
                if len(content) > self._CELLFS_MAX:
                    return self._sess_json({"ok": False, "error": "zu groß (>8 MB)"}, 413)
                d = os.path.dirname(rp)
                if not os.path.isdir(d):
                    return self._sess_json({"ok": False, "error": "Zielordner existiert nicht"}, 400)
                if body.get("append"):
                    with open(rp, "ab") as f:
                        f.write(content)
                else:
                    tmp = rp + ".cellfs.tmp"
                    with open(tmp, "wb") as f:
                        f.write(content)
                    os.replace(tmp, rp)
                _mt = body.get("mtime")
                if _mt:
                    try:
                        os.utime(rp, (float(_mt), float(_mt)))
                    except (OSError, ValueError):
                        pass
                return self._sess_json({"ok": True, "path": rp, "written": len(content)})
            if op == "mkdir":
                os.makedirs(rp, exist_ok=True)
                return self._sess_json({"ok": True, "path": rp})
            if op == "rename":
                dst = str(body.get("dst") or "")
                if not dst.startswith("/") or "\x00" in dst:
                    return self._sess_json({"ok": False, "error": "ungültiges Ziel"}, 400)
                rd = os.path.realpath(dst)
                if self._cellfs_forbidden(rd) or not self._cellfs_allowed(rd, "fs_write", principal, query):
                    return self._sess_json({"ok": False, "error": "Ziel nicht in der Session-Allowlist"}, 403)
                if not os.path.isfile(rp):
                    return self._sess_json({"ok": False, "error": "keine Datei"}, 404)
                os.replace(rp, rd)
                return self._sess_json({"ok": True, "path": rd})
        except PermissionError:
            return self._sess_json({"ok": False, "error": "keine Berechtigung"}, 403)
        except FileNotFoundError:
            return self._sess_json({"ok": False, "error": "nicht gefunden"}, 404)
        except Exception:
            _traceback_log("cellfs %s" % op)
            return self._sess_json({"ok": False, "error": "Dateizugriff fehlgeschlagen"}, 500)

    def _api_session_board(self):

        uid = self._principal()
        out = []

        _vpn_by_id = {}
        try:
            for _e in (_vpn_registry() or []):
                if isinstance(_e, dict) and _e.get("id"):
                    _vpn_by_id[str(_e.get("id"))] = _e
        except Exception:
            _vpn_by_id = {}
        try:
            recs = _session_store(uid, "cockpit").list()
        except Exception:

            return self._sess_json({"ok": False, "error": "Sitzungsliste momentan nicht lesbar"})

        try:
            _tls = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"],
                                  capture_output=True)
            _tmux_live = ({ln.strip() for ln in _tls.stdout.decode("utf-8", "replace").splitlines()
                           if ln.strip()} if _tls.returncode == 0 else set())
        except Exception:
            _tmux_live = set()

        try:
            import pn_cell_session as _cs0
            _warm_cells = _cs0.get_manager().warm_set(uid)
        except Exception:
            _warm_cells = set()
        for s in recs:
            if s.get("state") == "deleted":
                continue
            sid = s.get("id"); tmux = s.get("tmux") or ""

            warm = bool(tmux) and not s.get("archived") and tmux in _tmux_live
            if not warm:

                warm = sid in _warm_cells
            prov = _sessprov_get(uid, sid)
            try:
                preset = (_sess_policy_get(uid, sid) or {}).get("preset")
            except Exception:
                preset = None
            vpn = prov.get("vpn")
            out.append({
                "sid": sid, "title": s.get("title"), "state": s.get("state"),
                "created": s.get("created"), "last_active": s.get("last_active"),
                "kept": bool(s.get("kept_until")), "tmux": tmux, "warm": warm,
                "archived": bool(s.get("archived")),
                "res": (_cell_resources(uid, sid) if warm else None),
                "model": prov.get("model"), "effort": prov.get("effort"),
                "autonomy": (_sesscells.normalize_level(prov.get("autonomy")) if (_sesscells and prov.get("autonomy") is not None) else prov.get("autonomy")),
                "duration_h": prov.get("duration_h"),
                "expires": prov.get("expires"), "vpn": vpn,
                "vpn_state": ("set" if vpn else None),

                "vpn_cluster": bool(vpn and _vpn_is_cluster(_vpn_by_id.get(str(vpn)))),
                "preset": prov.get("preset") or preset, "provisioned": bool(prov),
                "orchestrator": bool(prov.get("orchestrator")),
                "max_concurrent": prov.get("max_concurrent"),
                "mem_mb": prov.get("mem_mb"), "disk_mb": prov.get("disk_mb"),
                "kits": list(prov.get("kits") or []),

                "runtime": prov.get("runtime") or "claude-tmux",
                "paused": bool(prov.get("paused")) and warm,
                "health": (_watchdog_health(uid, sid) if _watchdog_health else None),
                "progress": self._prog_row(uid, sid, warm),
                "observer": self._obs_row(uid, sid, warm),
                "unread": _unread_count(uid, sid),
                "created": s.get("created"),
                "msgs": _board_msg_counts(uid).get(sid, 0),
                "share": _share_pub_granted(sid, s.get("title"), uid),
            })

        if VOICE_CELL:
            try:
                vsess = _voice_session_for(uid)
                vwarm = False
                try:
                    _vmgr = _voice_cellmgr()
                    vwarm = bool(_vmgr.is_warm(uid, vsess)) if hasattr(_vmgr, "is_warm") else False
                except Exception:
                    vwarm = False
                try:
                    vpol = _policy.apply_floor(self._policy_store().get(uid, "voice", "default"),
                                               self._policy_floor()) if _policy else {}
                except Exception:
                    vpol = {}
                vcaps = (vpol.get("caps") or {}) if isinstance(vpol, dict) else {}
                try:
                    vres = _cell_resources(uid, vsess) if vwarm else None
                except Exception:
                    vres = None
                out.append({
                    "sid": "__voice__", "voice": True, "title": "🎙 Sprach-Assistent",
                    "state": "active", "created": None, "last_active": None,
                    "kept": True, "tmux": "", "warm": vwarm, "archived": False,
                    "res": vres,
                    "model": vcaps.get("model") or os.environ.get("PN_VOICE_MODEL", "opus"),
                    "effort": vcaps.get("effort") or "medium",
                    "autonomy": None, "duration_h": None, "expires": None, "vpn": None, "vpn_state": None,
                    "preset": (vpol.get("preset") if isinstance(vpol, dict) else None),
                    "provisioned": True, "orchestrator": True, "max_concurrent": None,
                    "runtime": "voice-repl", "paused": False,
                    "health": (_watchdog_health(uid, vsess) if _watchdog_health else None),
                    "caps": {"net_general": vcaps.get("net_general"), "net_internal": vcaps.get("net_internal"),
                             "websearch": vcaps.get("websearch"), "webfetch": vcaps.get("webfetch"),
                             "device_connect": vcaps.get("device_connect"), "devices": vcaps.get("devices")},
                })
            except Exception:
                _traceback_log("session board voice")
        try:
            auton = {str(k): v for k, v in (_sesscells.AUTONOMY_LABELS.items() if _sesscells else [])}
        except Exception:
            auton = {}
        try:
            presets = list(_policy.PRESETS.keys()) if _policy else []
        except Exception:
            presets = []
        vpns = []
        try:
            for e in _vpn_registry():
                vpns.append({"id": e.get("id"), "name": e.get("name") or e.get("id"),
                             "backend": e.get("backend"), "gateway": e.get("gateway"),
                             "operator_gated": e.get("operator_gated"),
                             "cluster": _vpn_is_cluster(e)})
        except Exception:
            vpns = []
        return self._sess_json({
            "ok": True, "principal": uid, "sessions": out,
            "models": _models_live(), "efforts": _SESS_EFFORTS, "runtimes": _SESS_RUNTIMES,

            "runtime_models": {r.get("id"): _models_live(r.get("id")) for r in (_SESS_RUNTIMES or [])},
            "default_model": _default_models()[0], "default_meta_model": _default_models()[1],
            "autonomy_levels": auton,

            "autonomy_short": ({str(k): v for k, v in _sesscells.AUTONOMY_SHORT.items()}
                               if (_sesscells and hasattr(_sesscells, "AUTONOMY_SHORT")) else {}),
            "autonomy_experience": ({str(k): v for k, v in _sesscells.AUTONOMY_EXPERIENCE.items()}
                                    if (_sesscells and hasattr(_sesscells, "AUTONOMY_EXPERIENCE")) else {}),
            "autonomy_order": (list(_sesscells.LEVELS) if (_sesscells and hasattr(_sesscells, "LEVELS")) else []),
            "autonomy_default": (_sesscells.DEFAULT_AUTONOMY if _sesscells else 2),
            "presets": presets,

            "preset_meta": (getattr(_policy, "PRESET_META", {}) if _policy else {}),
            "default_preset": (_policy.DEFAULT_PRESET if _policy else "standard"),
            "vpns": vpns})

    def _kits_offer(self):

        try:
            import pn_software_shelf as _shelf
            cat = (_shelf.catalog() or {}).get("kits", {}) or {}
            out = []
            for kid in sorted(cat.keys()):
                try:
                    if not _shelf.kit_img(kid):
                        continue
                except Exception:
                    continue
                e = cat.get(kid) or {}
                out.append({"id": kid,
                            "label": e.get("label") or e.get("name") or kid,
                            "zweck": e.get("zweck") or e.get("purpose") or ""})
            return out
        except Exception:
            return []

    def _session_meta(self):

        try:
            auton = {str(k): v for k, v in (_sesscells.AUTONOMY_LABELS.items() if _sesscells else [])}
        except Exception:
            auton = {}
        try:
            presets = list(_policy.PRESETS.keys()) if _policy else []
        except Exception:
            presets = []
        vpns = []
        try:
            for e in _vpn_registry():
                vpns.append({"id": e.get("id"), "name": e.get("name") or e.get("id"),
                             "backend": e.get("backend"), "gateway": e.get("gateway"),
                             "operator_gated": e.get("operator_gated"), "cluster": _vpn_is_cluster(e)})
        except Exception:
            vpns = []
        try:
            defm = _default_models()
        except Exception:
            defm = (None, None)
        try:
            rt_models = {r.get("id"): _models_live(r.get("id")) for r in (_SESS_RUNTIMES or [])}
        except Exception:
            rt_models = {}
        return {
            "models": _models_live(), "efforts": _SESS_EFFORTS, "runtimes": _SESS_RUNTIMES,
            "runtime_models": rt_models, "default_model": defm[0], "default_meta_model": defm[1],
            "autonomy_levels": auton,
            "autonomy_short": ({str(k): v for k, v in _sesscells.AUTONOMY_SHORT.items()}
                               if (_sesscells and hasattr(_sesscells, "AUTONOMY_SHORT")) else {}),
            "autonomy_experience": ({str(k): v for k, v in _sesscells.AUTONOMY_EXPERIENCE.items()}
                                    if (_sesscells and hasattr(_sesscells, "AUTONOMY_EXPERIENCE")) else {}),
            "autonomy_order": (list(_sesscells.LEVELS) if (_sesscells and hasattr(_sesscells, "LEVELS")) else []),
            "autonomy_default": (_sesscells.DEFAULT_AUTONOMY if _sesscells else 2),
            "presets": presets, "preset_meta": (getattr(_policy, "PRESET_META", {}) if _policy else {}),
            "default_preset": (_policy.DEFAULT_PRESET if _policy else "standard"), "vpns": vpns,
            "caps_offer": ["net_general", "net_internal", "websearch", "webfetch", "device_connect"],
            "kits_offer": self._kits_offer(), "kits_max": 6,

            "mem_range": [1024, 12288], "disk_range": [512, 16384], "max_concurrent_max": 64,
        }

    def _api_session_provision(self, raw, uid_override=None):

        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        uid = uid_override or self._principal()
        sid = str(body.get("sid") or "").strip()

        if sid == "__voice__":
            return self._sess_json({"ok": False, "error":
                "Die Ausstattung der Sprachsession läuft über „🛡 Rechte & Ausstattung“ "
                "(Voice-Policy, wirkt sofort ohne Neustart) — nicht über diesen Pfad."}, 400)

        bad = self._bad_sid(sid, uid)
        if bad:
            return bad
        patch = {}
        for k in ("model", "effort", "vpn", "title", "preset"):
            if k in body and body[k] is not None:
                patch[k] = body[k]
        if "runtime" in body and body["runtime"] is not None:
            patch["runtime"] = body["runtime"] if body["runtime"] in _SESS_RUNTIME_IDS else "claude-tmux"
        if "autonomy" in body and body["autonomy"] is not None:
            try:

                patch["autonomy"] = (_sesscells.normalize_level(body["autonomy"])
                                     if _sesscells else max(0, min(5, int(body["autonomy"]))))
            except Exception:
                pass
        if "orchestrator" in body:
            patch["orchestrator"] = bool(body.get("orchestrator"))
        if "vpn_dauerjob" in body and body["vpn_dauerjob"] is not None:

            patch["vpn_dauerjob"] = bool(body.get("vpn_dauerjob"))
        if "max_concurrent" in body and body["max_concurrent"] is not None:
            try:
                patch["max_concurrent"] = max(0, min(64, int(body["max_concurrent"])))
            except Exception:
                pass
        _kits_unknown = []
        if "kits" in body and body["kits"] is not None:

            _want, _ok = [], []
            for _k in (body["kits"] or []):
                _k = str(_k or "").strip()
                if _k and _k not in _want:
                    _want.append(_k)
            try:
                import pn_software_shelf as _shelf
                for _k in _want:
                    (_ok if _shelf.kit_img(_k) else _kits_unknown).append(_k)
                patch["kits"] = _ok[:KIT_DECKEL]
                _kits_unknown += _ok[KIT_DECKEL:]
            except Exception:
                _traceback_log("session provision kits")
                _kits_unknown = list(_want)
        if "mem_mb" in body and body["mem_mb"] is not None:
            try:
                patch["mem_mb"] = max(1024, min(12288, int(body["mem_mb"])))
            except Exception:
                pass
        if "disk_mb" in body and body["disk_mb"] is not None:
            try:
                patch["disk_mb"] = max(512, min(16384, int(body["disk_mb"])))
            except Exception:
                pass
        if "duration_h" in body and body["duration_h"] is not None:
            try:
                dh = float(body["duration_h"])
                patch["duration_h"] = dh
                patch["expires"] = (time.time() + dh * 3600) if dh > 0 else 0
            except Exception:
                pass
        prov = _sessprov_set(uid, sid, patch)
        if body.get("title"):
            try:
                _session_store(uid, "cockpit").rename(sid, str(body["title"]))
            except Exception:
                pass

        try:
            _share_pub_granted(sid, body.get("title"), uid)
        except Exception:
            pass
        if _policy and (body.get("preset") or isinstance(body.get("caps"), dict)):
            try:
                preset = str(body.get("preset") or _policy.DEFAULT_PRESET)
                pol = _policy.new_policy(preset)
                if isinstance(body.get("caps"), dict):
                    pol.setdefault("caps", {})
                    _bcaps = body["caps"]

                    if "secrets" not in _bcaps:
                        try:
                            _prev = ((_sess_policy_get(uid, sid) or {}).get("caps", {}) or {}).get("secrets")
                            if _prev:
                                pol["caps"]["secrets"] = _prev
                        except Exception:
                            pass
                    pol["caps"].update(_bcaps)

                    if isinstance(pol["caps"].get("secrets"), list):
                        pol["caps"]["secrets"] = [str(x) for x in pol["caps"]["secrets"] if str(x).strip() and str(x).strip() != "*"]
                    pol["preset"] = "custom"
                try:
                    floor = self._policy_floor()
                except Exception:
                    floor = {}
                saved = _policy.apply_floor(_policy.validate(pol), floor)
                _sess_policy_store().set(uid, "cockpit", sid, saved)
                prov = _sessprov_set(uid, sid, {"preset": saved.get("preset")})
            except Exception:
                _traceback_log("session provision policy")
        if _policy and "orchestrator" in body:

            try:
                cur = _sess_policy_get(uid, sid) or _policy.new_policy(_policy.DEFAULT_PRESET)
                cur.setdefault("caps", {})
                cur["caps"]["orchestrate"] = "allow" if bool(body.get("orchestrator")) else "deny"
                try:
                    floor = self._policy_floor()
                except Exception:
                    floor = {}
                _sess_policy_store().set(uid, "cockpit", sid, _policy.apply_floor(_policy.validate(cur), floor))
            except Exception:
                _traceback_log("session provision orchestrate cap")

        try:
            _share_pub_granted(sid, body.get("title"), uid, neu_pruefen=True)
        except Exception:
            _traceback_log("session provision share regrant")
        if _sesscells and "autonomy" in patch:
            try:
                reg = _sesscell_reg()
                if reg and reg.get(uid, sid) is None:
                    reg.provision(uid, sid, autonomy=patch["autonomy"])
                elif reg:
                    reg.set_autonomy(uid, sid, patch["autonomy"])
            except Exception:
                pass
        _prov_rebooted = False; _prov_live_only = False
        _prov_wieder_an = None
        if body.get("restart"):
            try:
                tn = _session_store(uid, "cockpit").tmux_name(sid)
                subprocess.run(["tmux", "kill-session", "-t", tn], capture_output=True)
                _sessprov_set(uid, sid, {"paused": False})
            except Exception:
                pass

            try:
                import pn_cell_session as _cs
                mgr = _cs.get_manager()
                if mgr.is_warm(uid, sid):
                    enf_new = _cockpit_policy_enf(uid, sid)
                    cell = mgr.get(uid, sid)
                    old = dict(cell.policy or {}) if cell is not None else {}
                    if cell is not None:
                        cell.update_policy(enf_new)

                    _boot_keys = ("model", "effort", "autonomy", "runtime", "mem_mb", "kits")
                    if any(old.get(k) != enf_new.get(k) for k in _boot_keys):
                        _cell_power(uid, sid, False)
                        _prov_rebooted = True
                        try:
                            _an = _cell_power(uid, sid, True)
                        except Exception as _e:
                            _an = {"ok": False, "reason": "Wiederanlauf: %s" % _e}
                        _prov_wieder_an = _an
                    else:
                        _prov_live_only = True
            except Exception:
                _traceback_log("session provision cell recycle")
        try:
            _meta_ensure_for_session(uid, sid, _sessprov_get(uid, sid))
        except Exception:
            _traceback_log("meta ensure for session")
        _prov_log("session.provision", uid, json.dumps({"sid": sid, "prov": patch}), {"wire": "api"})
        _antwort = {"ok": True, "sid": sid, "prov": prov,
                    "rebooted": _prov_rebooted, "live_only": _prov_live_only,
                    "kits_unknown": _kits_unknown}
        if _prov_wieder_an is not None:
            _antwort["wieder_an"] = bool(_prov_wieder_an.get("ok"))
            if not _prov_wieder_an.get("ok"):
                _antwort["wieder_an_grund"] = _prov_wieder_an.get("reason")
        return self._sess_json(_antwort)

    def _api_session_pause(self, raw):

        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        uid = self._principal()
        sid = str(body.get("sid") or "").strip()
        if sid == "__voice__":
            return self._sess_json({"ok": False, "error":
                "Die Sprachsession bleibt immer warm — Pausieren ist für sie ausgeschaltet."}, 400)

        bad = self._bad_sid(sid, uid)
        if bad:
            return bad
        paused = bool(body.get("paused", True))
        _sessprov_set(uid, sid, {"paused": paused})
        try:
            if sid in _meta_load():
                _meta_update(lambda d: (d[sid].__setitem__("state", "paused" if paused else "running")
                                        if sid in d else None))
        except Exception:
            pass

        if paused:
            _session_pause_notify(uid, sid, True)
            hard = _session_hard_freeze(uid, sid, True)
            hard = _cell_freeze(uid, sid, True) or hard
        else:
            hard = _session_hard_freeze(uid, sid, False)
            hard = _cell_freeze(uid, sid, False) or hard
            _session_pause_notify(uid, sid, False)
        _prov_log("session.pause", uid, json.dumps({"sid": sid, "paused": paused, "hard": bool(hard)}), {"wire": "api"})
        return self._sess_json({"ok": True, "sid": sid, "paused": paused, "hard": bool(hard)})

    def _api_session_power(self, raw):

        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        uid = self._principal()
        sid = str(body.get("sid") or "").strip()
        if sid == "__voice__":
            return self._sess_json({"ok": False, "error":
                "Die Sprachsession bleibt immer warm — die Box hält sie automatisch bereit, "
                "ein Aus-/Einschalten von Hand gibt es hier bewusst nicht."}, 400)

        bad = self._bad_sid(sid, uid)
        if bad:
            return bad
        on = bool(body.get("on"))
        if on:
            frisch = _relay_warm_kick(uid, sid)
            _prov_log("session.power", uid,
                      json.dumps({"sid": sid, "on": True, "ok": None, "hintergrund": True}),
                      {"wire": "api"})
            return self._sess_json({
                "ok": True, "sid": sid, "on": True,
                "gestartet": bool(frisch), "laeuft_schon": not frisch,
                "hinweis": ("Die Zelle faehrt hoch. Der Gast ist in Sekunden da; die "
                            "Werkzeugkisten brauchen laenger — der Fortschritt steht im "
                            "Lebenslauf der Sitzung.")})
        res = _cell_power(uid, sid, on)
        ok = bool(res.get("ok")) if isinstance(res, dict) else bool(res)
        reason = res.get("reason") if isinstance(res, dict) else None
        _sessprov_set(uid, sid, {"paused": False})
        _prov_log("session.power", uid, json.dumps({"sid": sid, "on": on, "ok": ok}), {"wire": "api"})
        out = {"ok": ok, "sid": sid, "on": on}
        if not ok and reason:
            out["reason"] = reason
        return self._sess_json(out)

    def _api_session_desktop(self, raw):

        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        uid = self._principal()
        sid = str(body.get("sid") or "").strip()

        if sid == "__voice__":
            return self._sess_json({"ok": False, "reason":
                "Die Sprachzelle wird ohne Desktop-Profil bereitgehalten — ein Desktop lässt sich "
                "für sie (noch) nicht aktivieren."}, 400)
        bad = self._bad_sid(sid, uid)
        if bad:
            return bad
        on = bool(body.get("on"))
        res = _cell_desktop(uid, sid, on) or {}
        _prov_log("session.desktop", uid, json.dumps({"sid": sid, "on": on, "ok": bool(res.get("ok"))}),
                  {"wire": "api"})
        out = {"ok": bool(res.get("ok")), "sid": sid, "on": on}
        if res.get("reason"):
            out["reason"] = res["reason"]
        return self._sess_json(out)

    def _api_session_desktop_get(self, query):

        q = urllib.parse.parse_qs(query or "")
        uid = self._principal()
        sid = str((q.get("sid") or [""])[0] or "").strip()
        if not sid:
            return self._sess_json({"ok": False, "error": "sid fehlt"}, 400)
        st = _cell_desktop_status(uid, sid) or {}
        st["ok"] = True
        return self._sess_json(st)

    def _api_session_app(self, raw):

        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        uid = self._principal()
        sid = str(body.get("sid") or "").strip()
        if sid == "__voice__":
            return self._sess_json({"ok": False, "reason":
                "Programme brauchen einen aktiven Desktop — die Sprachsession hat keinen."}, 400)
        bad = self._bad_sid(sid, uid)
        if bad:
            return bad
        op = str(body.get("op") or "install")
        app_id = str(body.get("app_id") or "").strip()
        res = _cell_app(uid, sid, op, app_id) or {}
        _prov_log("session.app", uid, json.dumps({"sid": sid, "op": op, "app": app_id,
                                                  "ok": bool(res.get("ok"))}), {"wire": "api"})
        out = {"ok": bool(res.get("ok")), "sid": sid, "op": op, "app_id": app_id}
        if res.get("reason"):
            out["reason"] = res["reason"]
        return self._sess_json(out)

    def _api_session_app_get(self, query):

        q = urllib.parse.parse_qs(query or "")
        uid = self._principal()
        sid = str((q.get("sid") or [""])[0] or "").strip()
        if not sid:
            return self._sess_json({"ok": False, "error": "sid fehlt"}, 400)
        st = _cell_app_status(uid, sid) or {}
        st["ok"] = True
        return self._sess_json(st)

    def _api_appsearch(self, query):

        q = urllib.parse.parse_qs(query or "")
        results, err = _flathub_search(str((q.get("q") or [""])[0] or ""))
        out = {"ok": err is None, "results": results}
        if err:
            out["error"] = err
        return self._sess_json(out)

    def _api_session_unread(self):

        principal = self._principal()
        counts = _unread_counts(principal)
        try:
            titles = {str(s.get("id")): (s.get("title") or "")
                      for s in _session_store(principal).list()}
        except Exception:
            titles = {}
        out, total = {}, 0
        for sid, row in counts.items():
            r = dict(row)

            r["title"] = titles.get(sid) or ("📢 Meldungen" if sid == "meldungen" else sid)
            out[sid] = r
            total += int(row.get("unread") or 0)
        return self._sess_json({"ok": True, "total": total, "sessions": out})

    def _api_session_seen(self, raw):

        try:
            body = json.loads(raw or b"{}") or {}
        except Exception:
            body = {}
        principal = self._principal()
        if body.get("all"):
            n = 0
            for _s, _dq in (_unread_ingest(principal) or {}).items():
                if _dq:
                    try:
                        _unread_seen_set(principal, _s, int(_dq[-1][0]))
                        n += 1
                    except Exception:
                        pass
            _UNREAD_CACHE.pop(principal, None)
            return self._sess_json({"ok": True, "all": True, "marked": n})
        sid = str(body.get("sid") or "")
        if not sid:
            return self._sess_json({"ok": False, "error": "sid required"}, 400)
        seq = body.get("seq")
        if seq is None:
            dq = _unread_ingest(principal).get(sid)
            seq = dq[-1][0] if dq else 0
        try:
            _unread_seen_set(principal, sid, int(seq))
        except Exception:
            return self._sess_json({"ok": False, "error": "seen store"}, 500)
        _UNREAD_CACHE.pop(principal, None)
        return self._sess_json({"ok": True, "sid": sid, "seq": int(seq)})

    def _bi_signals(self, uid):

        sig = []
        try:
            import portal_metafeatures as _mf
            pend = _mf.appr_list(uid, state="pending")
            if pend:
                nq = sum(1 for p in pend if p.get("kind") != "approval")
                na = sum(1 for p in pend if p.get("kind") == "approval")
                bits = []
                if nq:
                    bits.append("%d Frage(n)" % nq)
                if na:
                    bits.append("%d Freigabe(n) mit 2FA" % na)
                sig.append("BRAUCHT DICH: %s offen im Entscheidungs-Fenster." % " und ".join(bits))
        except Exception:
            pass
        try:
            counts = _unread_counts(uid) if "_unread_counts" in globals() else {}
            nalert = sum(int((v or {}).get("alert") or 0) for v in counts.values())
            nunread = sum(int((v or {}).get("unread") or 0) for v in counts.values())
            if nalert:
                sig.append("%d Alarm-Nachricht(en) ungelesen (rot)." % nalert)
            elif nunread:
                sig.append("%d ungelesene Nachricht(en) im Messenger." % nunread)
        except Exception:
            pass
        try:
            import portal_metasessions as _pm
            ms = _pm._meta_load() or {}
            idle = [m for m in ms.values() if m.get("owner") == uid and m.get("state") == "paused"]
            run = [m for m in ms.values() if m.get("owner") == uid and m.get("state") == "running"]
            if idle:
                sig.append("%d Orchestrator im Standby (wartet auf resume)." % len(idle))
            if run:
                sig.append("%d Orchestrator laeuft." % len(run))
        except Exception:
            pass
        try:
            import portal_metafeatures as _mf2
            conv = [c for c in _mf2.conv_list(uid) if c.get("state") == "working"]
            if conv:
                sig.append("%d moderierte Konversation(en) laufen gerade." % len(conv))
        except Exception:
            pass
        try:
            sess = self._bi_sessions(uid)
            warm = sum(1 for s in sess if s.get("warm"))
            if warm:
                sig.append("%d von %d Sitzung(en) sind gerade aktiv." % (warm, len(sess)))
            la = os.getloadavg()[0]; ncpu = os.cpu_count() or 1
            if la / max(1, ncpu) > 0.85:
                sig.append("Systemlast hoch (%.1f auf %d Kernen)." % (la, ncpu))
        except Exception:
            pass
        return sig

    def _bi_features(self):

        return [
            {"name": "Medienserver", "hint": "Dateien/Medien im ganzen LAN teilen (Windows-Netzlaufwerk + DLNA)"},
            {"name": "Sprachsteuerung", "hint": "Die Box freihaendig per Sprache bedienen"},
            {"name": "Uni-/Cluster-VPN pro Session", "hint": "Jede Sitzung mit eigenem VPN-Tunnel isoliert arbeiten lassen"},
            {"name": "Auf den Fernseher spiegeln", "hint": "Eine Sitzung live auf einen TV/Chromecast casten"},
            {"name": "Austausch-Ordner", "hint": "~/austausch je Sitzung wird mit deinem Netzlaufwerk synchron gehalten"},
        ]

    def _bi_sessions(self, uid):
        try:
            import pn_cell_session as _cs
            mgr = _cs.get_manager()
            out = []
            for s in _session_store(uid, "cockpit").list():
                if s.get("state") == "deleted":
                    continue
                sid = s.get("id")
                out.append({"id": sid, "title": s.get("title") or sid,
                            "warm": bool(sid and mgr.is_warm(uid, sid)),
                            "created": s.get("created"), "last_active": s.get("last_active")})
            out.sort(key=lambda r: (r.get("last_active") or ""), reverse=True)
            return out
        except Exception:
            return []

    def _api_session_hovercard(self, query):

        import urllib.parse
        try:
            import portal_board_intel as _bi
        except Exception:
            return self._sess_json({"ok": False, "state": "off"})
        uid = self._principal()
        sid = urllib.parse.parse_qs(query or "").get("sid", [""])[0]
        sid = re.sub(r"[^A-Za-z0-9_.-]", "", str(sid))[:64]
        if not sid or portal_channels is None or _chan_ctx is None:
            return self._sess_json({"ok": False, "state": "off"})
        title = ""
        try:
            s = _session_store(uid, "cockpit").get(sid)
            title = (s or {}).get("title") or ""
        except Exception:
            pass
        return self._sess_json(_bi.session_summary(portal_channels.bus_read, _chan_ctx(), uid, sid, title))

    def _api_board_overview(self):

        try:
            import portal_board_intel as _bi
        except Exception:
            return self._sess_json({"ok": False, "state": "off"})
        uid = self._principal()
        if portal_channels is None or _chan_ctx is None:
            return self._sess_json({"ok": False, "state": "off"})
        return self._sess_json(_bi.overview(portal_channels.bus_read, _chan_ctx(), uid,
                                            self._bi_sessions(uid), self._bi_features(),
                                            signals=self._bi_signals(uid)))

    def _api_board_workload(self):

        try:
            import portal_board_intel as _bi
        except Exception:
            return self._sess_json({"ok": False, "state": "off"})
        uid = self._principal()
        snap = {}
        try:
            la = os.getloadavg(); ncpu = os.cpu_count() or 1
            sessions = self._bi_sessions(uid)
            counts = _board_msg_counts(uid)
            snap = {"load_1min": round(la[0], 2), "kerne": ncpu,
                    "auslastung_pct": min(100, int(round(la[0] / max(1, ncpu) * 100))),
                    "sitzungen_gesamt": len(sessions),
                    "sitzungen_aktiv": sum(1 for s in sessions if s.get("warm")),
                    "nachrichten_im_fenster": sum(counts.values())}
        except Exception:
            snap = {}
        return self._sess_json(_bi.workload(uid, snap))

    def _api_board_channel(self, query):

        import urllib.parse
        try:
            import portal_board_intel as _bi
        except Exception:
            return self._sess_json({"ok": False, "state": "off", "entries": []})
        q = urllib.parse.parse_qs(query or "")
        kind = (q.get("kind", ["board"])[0] or "board")
        level = (q.get("cad", ["haeufig"])[0] or "haeufig")
        if level not in ("echtzeit", "haeufig", "selten", "nie"):
            level = "haeufig"
        uid = self._principal()
        if kind == "work":
            snap = {}
            try:
                la = os.getloadavg()
                ncpu = os.cpu_count() or 1
                sessions = self._bi_sessions(uid)
                counts = _board_msg_counts(uid)
                snap = {"load_1min": round(la[0], 2), "kerne": ncpu,
                        "auslastung_pct": min(100, int(round(la[0] / max(1, ncpu) * 100))),
                        "sitzungen_gesamt": len(sessions),
                        "sitzungen_aktiv": sum(1 for s in sessions if s.get("warm")),
                        "nachrichten_im_fenster": sum(counts.values())}
            except Exception:
                snap = {}
            return self._sess_json(_bi.work_channel(uid, snap, level=level))
        if portal_channels is None or _chan_ctx is None:
            return self._sess_json({"ok": False, "state": "off", "entries": []})
        return self._sess_json(_bi.board_channel(portal_channels.bus_read, _chan_ctx(), uid,
                                                 self._bi_sessions(uid), self._bi_features(),
                                                 level=level))

    def _api_session_say(self, raw):

        if portal_channels is None:
            return self._sess_json({"ok": False, "error": "channels unavailable"}, 503)
        payload, code = portal_channels.session_say(_chan_ctx(), self._principal(), raw)
        return self._sess_json(payload, code)

    def _api_session_erklaer(self, raw):

        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        uid = self._principal()
        sid = str(body.get("sid") or "").strip()
        poll = bool(body.get("poll"))
        if sid == "__voice__":
            return self._sess_json({"ok": False, "error":
                "Die Sprachsession erklärt sich im Gespräch selbst — der Beobachter ist für Arbeits-Sessions."}, 400)
        bad = self._bad_sid(sid, uid)
        if bad:
            return bad
        try:
            import pn_cell_session as _cs
        except Exception as e:
            return self._sess_json({"ok": False, "error": "cell mod: %s" % e}, 503)
        cell = _cs.get_manager().get(uid, sid)
        if cell is None or not cell.alive() or not hasattr(cell, "observer_start"):
            return self._sess_json({"ok": False, "error":
                "Die microVM dieser Session ist aus — zum Erklären muss sie laufen (▶ An)."}, 409)
        key = (uid, sid)
        now = time.time()
        with _ERKLAER_LK:
            st = _ERKLAER.get(key)
            if st and st.get("done") and (now - st["done"] > _ERKLAER_KEEP_S or not poll):
                _ERKLAER.pop(key, None); st = None
        if st and st.get("done"):
            return self._sess_json(dict(st["payload"], cached=True))
        if st:
            res = None
            try:
                res = cell.observer_collect()
            except Exception:
                res = None
            if res is None:
                if now - st["ts"] > _ERKLAER_STALL_S:
                    with _ERKLAER_LK:
                        _ERKLAER.pop(key, None)
                    return self._sess_json({"ok": False, "running": False, "error":
                        "Der Beobachter hat nicht geantwortet — bitte noch einmal starten."})
                if poll:
                    return self._sess_json({"ok": True, "sid": sid, "running": True})
                return self._sess_json({"ok": False, "sid": sid, "running": True,
                                        "error": "Für diese Session läuft schon ein Beobachter."}, 409)
            out = (res.get("out") or "").strip()
            if not out:
                err = " ".join((res.get("err") or "").split())[:200]
                payload = {"ok": False, "sid": sid, "running": False,
                           "error": ("Der Beobachter hat nichts geliefert" + (" (%s)" % err if err else "") + ".")}
                with _ERKLAER_LK:
                    _ERKLAER[key] = {"done": now, "payload": payload}
                return self._sess_json(payload)

            first, _, rest = out.partition("\n")
            is_status = first.upper().startswith("STATUS")
            problem = is_status and "problem" in first.lower()
            comment = (rest if is_status else out).strip() or out
            try:
                cell._observer = {"ts": now, "text": comment[:2000], "problem": problem}
            except Exception:
                pass
            if portal_channels is not None:
                try:
                    portal_channels.bus_append(_chan_ctx(), uid, sid, "message", role="observer",
                                               text=("❗ " if problem else "") + comment[:1500],
                                               notify=("alert" if problem else "ambient"))
                except Exception:
                    pass
            payload = {"ok": True, "sid": sid, "running": False, "text": comment[:2000],
                       "problem": problem}
            with _ERKLAER_LK:
                _ERKLAER[key] = {"done": now, "payload": payload}
            _prov_log("session.erklaer", uid, json.dumps({"sid": sid, "problem": problem}), {"wire": "api"})
            return self._sess_json(payload)

        if poll:
            return self._sess_json({"ok": True, "sid": sid, "running": False})

        try:
            import pn_session_watchdog as _wd
            prompt, model = _wd.OBS_PROMPT, _wd.OBSERVER_MODEL
        except Exception:
            return self._sess_json({"ok": False, "error": "Beobachter-Vorlage nicht verfügbar"}, 503)
        try:
            path = cell._incell_active_jsonl()
        except Exception:
            path = None
        if not path:
            return self._sess_json({"ok": False, "error":
                "Noch kein Gesprächs-Transcript in der Zelle — sobald der Agent arbeitet, gibt es hier etwas zu erklären."}, 409)
        with _ERKLAER_LK:
            if key in _ERKLAER:
                return self._sess_json({"ok": False, "sid": sid, "running": True,
                                        "error": "Für diese Session läuft schon ein Beobachter."}, 409)
            _ERKLAER[key] = {"ts": now}
        started = False
        try:
            started = bool(cell.observer_start(prompt, path, model))
        except Exception:
            started = False
        if not started:
            with _ERKLAER_LK:
                _ERKLAER.pop(key, None)
            return self._sess_json({"ok": False, "error": "Beobachter-Start in der Zelle fehlgeschlagen."}, 503)
        return self._sess_json({"ok": True, "sid": sid, "running": True, "started": True})

    def _api_cellprobe(self, query):

        import urllib.parse as _up
        q = _up.parse_qs(query)
        sid = (q.get("sid", [""])[0] or "").strip()
        say = q.get("say", [None])[0]
        principal = self._principal()
        if not sid:
            return self._sess_json({"ok": False, "error": "sid required"}, 400)

        bad = self._bad_sid(sid, principal)
        if bad:
            return bad
        try:
            import pn_cell_session as _cs
        except Exception as e:
            return self._sess_json({"ok": False, "error": "cell mod: %s" % e}, 503)
        mgr = _cs.get_manager()
        out = {"ok": True, "sid": sid}
        try:
            cell = mgr.get(principal, sid)
            out["tracked"] = cell is not None
            if cell is None or not cell.alive():
                cell = mgr.ensure(principal, sid, portal_url=_portal_base_url(),
                                  portal_token=_voice_agent_token(principal),
                                  policy=_cockpit_policy_enf(principal, sid))
            out["alive"] = bool(cell and cell.alive())
            if not (cell and cell.alive()):

                try:
                    out["reason"] = mgr.boot_reason(principal, sid)
                except Exception:
                    out["reason"] = None
                return self._sess_json(out, 200)

            try:
                _ok, _v = cell._run("/bin/claude --version 2>&1 | head -1; echo __CV__", "__CV__", 25)
                _v = " ".join((_v or "").split("__CV__")[0].split())
                out["claude"] = {"ok": bool(re.search(r"\d+\.\d+\.\d+", _v or "")), "version": _v[:200]}
            except Exception as e:
                out["claude"] = {"ok": False, "version": "", "error": str(e)}

            try:
                out["llm_lane"] = _cs.llm_lane_reason() or "ok"
            except Exception:
                pass
            if not cell.term_on:
                out["started_terminal"] = bool(cell.start_terminal())
                try:
                    _tc = cell.term_conn
                    if _tc is not None:
                        cell._drain_until_quiet(_tc, hard=14.0, quiet=1.3)
                except Exception:
                    pass
            out["term_on"] = bool(getattr(cell, "term_on", False))
            if not out["term_on"]:
                try:
                    out["term_reason"] = cell.term_reason()
                except Exception:
                    out["term_reason"] = None
            path = cell._incell_active_jsonl()
            out["jsonl_path"] = path
            size0 = cell._incell_jsonl_size(path) if path else None
            out["jsonl_size"] = size0
            if say:
                off0 = size0 or 0
                try:
                    _tc = cell.term_conn
                    _tc.setblocking(True)
                    _tc.sendall(say.encode()); time.sleep(0.35); _tc.sendall(b"\r")
                    out["injected"] = True
                except Exception as e:
                    out["inject_err"] = str(e)
                collected = []; t0 = time.time(); last = time.time()
                while time.time() - t0 < 90:
                    time.sleep(0.8)
                    if path is None:
                        path = cell._incell_active_jsonl(); off0 = 0
                        if path is None:
                            continue
                    try:
                        texts = cell._incell_assistant_tail(path, off0)
                    except Exception as e:
                        out["tail_err"] = str(e); break
                    if len(texts) > len(collected):
                        collected = texts; last = time.time()
                    elif collected and (time.time() - last) > 2.0:
                        break
                out["reply"] = "\n".join(collected).strip()
                out["jsonl_size_after"] = cell._incell_jsonl_size(path) if path else None
            elif path:
                try:
                    tail = cell._incell_assistant_tail(path, max(0, (size0 or 0) - 6000))
                    out["last_assistant"] = tail[-2:] if tail else []
                except Exception as e:
                    out["tail_err"] = str(e)
        except Exception as e:
            out["ok"] = False; out["error"] = str(e)
            _traceback_log("cellprobe")
        return self._sess_json(out, 200)

    def _api_session_kill(self, raw):

        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        uid = self._principal()
        sid = str(body.get("sid") or "").strip()
        if sid == "__voice__":
            return self._sess_json({"ok": False, "error":
                "Die Sprachsession lässt sich nicht löschen — sie wird automatisch bereitgehalten."}, 400)
        if not re.match(r"^[a-z0-9]{6,16}$", sid):
            return self._sess_json({"ok": False, "error": "bad sid"}, 400)

        import portal_delete_guard as _dg
        _2fa_ok, _2fa_resp = _dg.require_2fa(uid, str(body.get("totp") or body.get("code") or ""),
                                             None, prov_log=_prov_log, action="session.kill")
        if not _2fa_ok:
            return self._sess_json(_2fa_resp, 403)
        workers = []
        try:
            meta = _meta_load().get(sid)
            if meta:
                _meta_update(lambda d: d[sid].__setitem__("state", "stopped") if sid in d else None)
                for t in (meta.get("tasks") or []):
                    if t.get("state") not in ("running", "pending"):
                        continue
                    wsid = t.get("sid")
                    if wsid and re.match(r"^[a-z0-9]{6,16}$", str(wsid)):
                        w_cell = _cell_kill_erase(uid, wsid)
                        if _session_hard_kill(uid, wsid) or w_cell:
                            workers.append(wsid)
                        try:
                            _session_store(uid, "cockpit").delete(wsid)
                            _sessprov_del(uid, wsid)
                        except Exception:
                            _traceback_log("session kill worker record")
                    elif t.get("tmux"):
                        _kill_tmux_tree(t.get("tmux"))
                    if t.get("state") == "pending":
                        t["state"] = "stopped"
                _meta_update(lambda d: d.__setitem__(sid, meta) if sid in d else None)
        except Exception:
            _traceback_log("session kill meta")

        erased = _cell_kill_erase(uid, sid)
        stopped = _session_hard_kill(uid, sid) or erased

        deleted = False
        try:
            deleted = bool(_session_store(uid, "cockpit").delete(sid))
            _sessprov_del(uid, sid)
        except Exception:
            _traceback_log("session kill record delete")
        try:
            if sid in _meta_load():
                _meta_update(lambda d: d.pop(sid, None))
        except Exception:
            pass
        _prov_log("session.kill", uid, json.dumps({"sid": sid, "workers": workers, "erased": bool(erased), "deleted": deleted}), {"wire": "api"})

        if not (stopped or erased or deleted or workers):
            return self._sess_json({"ok": False, "error": self._UNKNOWN_SID_MSG, "sid": sid,
                                    "stopped": False, "erased": False, "deleted": False,
                                    "workers_killed": []}, 404)
        return self._sess_json({"ok": True, "sid": sid, "stopped": bool(stopped), "erased": bool(erased),
                                "deleted": deleted, "workers_killed": workers})

    def _api_sessions_live(self):

        uid = self._principal()
        try:
            current = _voice_session_for(uid)
        except Exception:
            current = "voice"
        sessions = []
        try:
            mgr = _voice_cellmgr()
            seen = set()
            for sname in (mgr.sessions_for(uid) if hasattr(mgr, "sessions_for") else []):
                c = mgr.cell(uid, sname) if hasattr(mgr, "cell") else None
                sessions.append({"session": sname, "warm": bool(mgr.is_warm(uid, sname)),
                                 "current": (sname == current),
                                 "last": getattr(c, "last", None) if c else None})
                seen.add(sname)
            if current not in seen:
                sessions.insert(0, {"session": current, "warm": False, "current": True, "last": None})
        except Exception:
            sessions = [{"session": current, "warm": False, "current": True, "last": None}]
        pol = {}
        try:
            if _policy:
                pol = _policy.apply_floor(self._policy_store().get(uid, "voice", "default"),
                                          self._policy_floor())
        except Exception:
            pol = {}
        return self._sess_json({"ok": True, "current": current, "sessions": sessions,
                                "policy": pol, "kind": "voice", "sid": "default"})

    def _sessions_live_page(self):
        p = os.path.join(os.path.dirname(os.path.realpath(__file__)), "sessions_live.html")
        try:
            _html = open(p, "rb").read().decode("utf-8", "replace")

            try:
                import portal_i18n as _i18n
                _lang = _i18n.ui_lang(getattr(self, "cfg", None), self.headers.get("Cookie", ""))
                _html = _i18n.inject_switcher(_html, _lang, selector=False)
            except Exception:
                pass
            return self.send_html(_html)
        except Exception:
            return self.send_html("sessions view not deployed", 404)

    def _obs_row(self, uid, sid, warm):

        if not warm:
            return None
        try:
            import pn_cell_session as _cs1
            cell = _cs1.get_manager().get(uid, sid)
            ob = getattr(cell, "_observer", None) if cell is not None else None
            if ob and ob.get("text"):
                return {"age_s": int(max(0, time.time() - float(ob.get("ts") or 0))),
                        "text": ob["text"][:400], "problem": bool(ob.get("problem"))}
        except Exception:
            pass
        return None

    def _prog_row(self, uid, sid, warm):

        if not warm:
            return None
        try:
            import pn_cell_session as _cs1
            cell = _cs1.get_manager().get(uid, sid)
            if cell is not None and hasattr(cell, "progress_cache"):
                return cell.progress_cache()
        except Exception:
            pass
        return None

    def _api_session_progress(self, query):

        sid, asked = self._q_session(urllib.parse.parse_qs(query))
        uid = self._principal()
        bad = self._bad_sid(sid, uid)
        if bad:
            return
        cell = None
        try:
            import pn_cell_session as _cs1
            cell = _cs1.get_manager().get(uid, sid)
        except Exception:
            cell = None
        if cell is None or not cell.alive():
            return self._sess_json({"ok": True, "sid": sid, "progress": None,
                                    "note": "Zelle ist aus — das Protokoll liegt auf dem Session-Delta "
                                            "und ist beim naechsten Start wieder da."})
        try:
            prog = cell.read_progress()
        except Exception:
            prog = None
        return self._sess_json({"ok": True, "sid": sid, "progress": prog})

    def _api_session_summary(self, query):

        if _vext is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        q = urllib.parse.parse_qs(query)
        target = q.get("target", ["cockpit"])[0] or "cockpit"
        if target != "cockpit":

            return self._vext_json({"ok": False, "error": _HOSTSHELL_GONE, "target": target}, 400)
        session, asked = self._q_session(q)
        force = q.get("force", ["0"])[0] in ("1", "true", "yes")
        principal = self._principal()
        kind = "cockpit"
        if asked:
            if not session:
                return self._vext_json({"ok": False, "error": "Kein Session-Kennzeichen angegeben."}, 400)
            if self._sid_known(principal, session, kind) is False:
                return self._vext_json({"ok": False, "error": self._UNKNOWN_SID_MSG,
                                        "sid": session}, 404)
        payload, _code = _vext.transcript(_vext_ctx(), principal, target=target, since=0, session=session)
        turns = [t for t in payload.get("turns", []) if (t.get("text") or "").strip()]
        if not turns and session:
            turns = self._bus_turns(principal, session)
        sig = payload.get("next") or len(turns)
        key = (principal, kind, session or "")
        if not turns:
            return self._vext_json({"ok": True, "summary": "", "turns_n": 0,
                                    "note": "Neue Session \u2014 noch kein Verlauf."}, 200)
        with _SESSION_SUMMARY_LOCK:
            c = _SESSION_SUMMARY_CACHE.get(key)
        if c and c.get("sig") == sig and not force:
            return self._vext_json({"ok": True, "summary": c["summary"], "turns_n": len(turns),
                                    "cached": True, "ts": c["ts"]}, 200)
        tail = turns[-24:]
        convo = "\n".join(
            ("%s: %s" % ("Du" if t.get("role") == "user" else "Assistent",
                         (t.get("text") or "").strip().replace("\n", " ")))[:600]
            for t in tail)
        system = ("Du fasst den Verlauf einer Arbeitssession fuer den zurueckkehrenden Nutzer zusammen, "
                  "damit er sofort wieder ins Thema findet. Antworte auf Deutsch in 2 bis 4 kurzen "
                  "Saetzen, kein Markdown, keine Aufzaehlung. Sage: woran zuletzt gearbeitet wurde und "
                  "was der aktuelle Stand bzw. der offene naechste Schritt ist.")
        prompt = "Verlauf (aelteste zuerst):\n\n" + convo + "\n\nZusammenfassung:"
        try:
            import pn_session_watchdog as _wd
            system = system + _wd.reply_lang_note()
        except Exception:
            pass
        r = self._llm_run(prompt, system, "", 60)
        if not r.get("ok"):
            lu = next((t.get("text") for t in reversed(tail) if t.get("role") == "user"), "") or ""
            la = next((t.get("text") for t in reversed(tail) if t.get("role") == "assistant"), "") or ""
            prev = ("Zuletzt \u2014 du: \u201e%s\u201c \u00b7 Assistent: \u201e%s\u201c"
                    % (lu.strip()[:160], la.strip()[:220])).strip()
            return self._vext_json({"ok": True, "summary": prev, "turns_n": len(turns),
                                    "fallback": True}, 200)
        summ = (r.get("text") or "").strip()
        now = time.time()
        with _SESSION_SUMMARY_LOCK:
            _SESSION_SUMMARY_CACHE[key] = {"sig": sig, "summary": summ, "ts": now}
        return self._vext_json({"ok": True, "summary": summ, "turns_n": len(turns),
                                "cached": False, "ts": now}, 200)

    def _may_list_vmcells(self):

        if self.authed():
            return True
        ent = self._apikey_entry()
        if ent is not None and _apikeys is not None:
            for scope in ("/ws/vnc", "/api/displays"):
                if _apikeys.scope_ok(ent, scope):
                    return True
        return False

    def _api_vmcells(self):

        cells = []
        try:
            with open(os.path.join(DATA_DIR, "vmcells.json")) as f:
                reg = json.load(f) or {}
        except (OSError, ValueError):
            reg = {}
        if isinstance(reg, dict):
            for cid, e in reg.items():
                if not isinstance(e, dict):
                    continue
                sock = e.get("sock", "")
                if not (sock and os.path.exists(sock)):
                    continue
                cells.append({"id": cid, "name": e.get("name", cid),
                              "w": e.get("w", 0), "h": e.get("h", 0), "kind": "gui"})

        try:
            uid = self._principal()
            import pn_cell_session as _cs
            mgr = _cs.get_manager()
            have = {c.get("id") for c in cells}
            _warm = mgr.warm_set(uid)
            for s in _session_store(uid, "cockpit").list():
                if s.get("state") == "deleted":
                    continue
                sid = s.get("id")
                if not sid or sid in have:
                    continue
                if sid in _warm:
                    cells.append({"id": sid, "name": s.get("title") or sid,
                                  "kind": "session", "headless": True})
        except Exception:
            pass
        return self._vext_json({"ok": True, "cells": cells})

    def _api_overview(self):

        uid = self._principal()
        active = 0; recent = []
        try:
            import pn_cell_session as _cs
            mgr = _cs.get_manager()
            _warm = mgr.warm_set(uid)
            for s in _session_store(uid, "cockpit").list():
                if s.get("state") == "deleted":
                    continue
                sid = s.get("id")
                warm = bool(sid and sid in _warm)
                if warm:
                    active += 1
                recent.append({"kind": "session", "sid": sid, "title": s.get("title") or sid,
                               "warm": warm, "last_active": s.get("last_active")})
        except Exception:
            pass
        recent.sort(key=lambda r: (r.get("last_active") or ""), reverse=True)
        try:
            la = os.getloadavg(); ncpu = os.cpu_count() or 1
            load1 = round(la[0], 2); load_pct = min(100, int(round(la[0] / max(1, ncpu) * 100)))
        except Exception:
            load1 = 0.0; ncpu = 1; load_pct = 0
        mem = {}
        try:
            info = {}
            for line in open("/proc/meminfo"):
                k, _, v = line.partition(":")
                try: info[k] = int(v.strip().split()[0])
                except Exception: pass
            tot = info.get("MemTotal", 0); avail = info.get("MemAvailable", 0)
            if tot:
                mem = {"total_mb": tot // 1024, "used_pct": int((tot - avail) / tot * 100)}
        except Exception:
            pass
        return self._sess_json({"ok": True, "cells_enabled": _cells_enabled(), "active_vms": active, "ncpu": ncpu,
                                "load1": load1, "load_pct": load_pct, "mem": mem,
                                "recent": recent[:6]})

    def _api_session_cells(self):

        if _sesscells is None:
            return self._vext_json({"ok": True, "cells": []})
        reg = _sesscell_reg()
        cells = [{"session": c.get("session"), "cell": c.get("cell"), "state": c.get("state"),
                  "autonomy": c.get("autonomy"), "last_active": c.get("last_active"),
                  "evict_reason": c.get("evict_reason")}
                 for c in reg.list_live(self._principal())]
        return self._vext_json({"ok": True, "cells": cells})

    def _api_session_notify(self, query):

        if _sesscells is None:
            return self._vext_json({"ok": True, "next": 0, "events": []})
        reg = _sesscell_reg()
        q = urllib.parse.parse_qs(query)
        try:
            since = int(q.get("since", ["0"])[0])
        except Exception:
            since = 0
        me = self._principal()
        events = []
        try:
            with open(reg.notify_path) as f:
                for i, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if ev.get("principal") == me:
                        ev["i"] = i
                        events.append(ev)
        except OSError:
            pass
        nxt = (events[-1]["i"] + 1) if events else since
        events = [e for e in events if e["i"] >= since]
        return self._vext_json({"ok": True, "next": nxt, "events": events})

_SESSION_FILE_KINDS = [
    (".py", "Python-Skript"), (".sh", "Shell-Skript"), (".bash", "Shell-Skript"),
    (".rs", "Rust-Quelltext"), (".c", "C-Quelltext"), (".h", "C-Kopfdatei"),
    (".cpp", "C++-Quelltext"), (".js", "JavaScript"), (".ts", "TypeScript"),
    (".json", "JSON-Daten"), (".yaml", "YAML-Konfiguration"), (".yml", "YAML-Konfiguration"),
    (".toml", "TOML-Konfiguration"), (".ini", "Konfiguration"), (".cfg", "Konfiguration"),
    (".md", "Notiz/Dokumentation"), (".txt", "Textdatei"), (".csv", "Tabelle (CSV)"),
    (".png", "Bild"), (".jpg", "Bild"), (".jpeg", "Bild"), (".gif", "Bild"),
    (".pdf", "PDF-Dokument"), (".zip", "Archiv"), (".tar", "Archiv"), (".gz", "Archiv"),
    (".log", "Protokoll"), (".sql", "SQL-Skript"), (".html", "Webseite"), (".css", "Stylesheet"),
]

def _file_gist(rel, size, head, binary, excerpt):

    import re as _re
    low = rel.lower()
    kind = "Datei"
    for ext, label in _SESSION_FILE_KINDS:
        if low.endswith(ext):
            kind = label
            break
    if binary and kind == "Datei":
        kind = "Programm/Binaerdatei"
    if size >= 1e6:
        human = "%.1f MB" % (size / 1e6)
    elif size >= 1e3:
        human = "%.0f kB" % (size / 1e3)
    else:
        human = "%d B" % size
    gist = ""
    if not binary:
        for line in (excerpt or "").splitlines()[:12]:
            t = line.strip()
            if not t:
                continue
            if t.startswith("#!"):
                gist = "startet mit %s" % t[2:].strip()
                break
            t2 = _re.sub(r'^(#+|//+|/\*+|--|;+|"""|\'\'\')\s*', "", t).strip(' *"\'')
            if t2 and len(t2) > 3 and not t2.startswith(("import ", "from ", "use ", "package ")):
                gist = t2[:160]
                break
    return ("%s, %s — %s" % (kind, human, gist)) if gist else ("%s, %s" % (kind, human))
