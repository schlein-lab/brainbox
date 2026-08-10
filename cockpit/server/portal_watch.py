
import json
import os
import sys
import threading
import time
import urllib.parse

MAX_PATHS = 200
MAX_WATCHED = 5000
SCAN_INTERVAL_S = 2.0
WATCH_TTL_S = 300.0
TIMEOUT_MAX_S = 25.0

SCANNER_AUTOSTART = True

_COND = threading.Condition()
_SEQ = 0
_EINTRAEGE = {}
_SCANNER = [None]

def _stat_pfad(rp):

    try:
        st = os.stat(rp)
        return st.st_mtime_ns, int(st.st_mtime), False
    except OSError:
        return None, None, True

def _registrieren(rp, jetzt=None):

    jetzt = time.time() if jetzt is None else jetzt
    with _COND:
        e = _EINTRAEGE.get(rp)
        if e is not None:
            e["benutzt"] = jetzt
            return True
        if len(_EINTRAEGE) >= MAX_WATCHED:
            return False
        mtime_ns, mtime, gone = _stat_pfad(rp)

        _EINTRAEGE[rp] = {"mtime_ns": mtime_ns, "mtime": mtime, "gone": gone,
                          "seq": _SEQ, "benutzt": jetzt}
        return True

def _scan_once(jetzt=None):

    global _SEQ
    jetzt = time.time() if jetzt is None else jetzt
    with _COND:
        arbeit = [(rp, e["mtime_ns"], e["gone"]) for rp, e in _EINTRAEGE.items()
                  if jetzt - e["benutzt"] <= WATCH_TTL_S]
        veraltet = [rp for rp, e in _EINTRAEGE.items() if jetzt - e["benutzt"] > WATCH_TTL_S]
        for rp in veraltet:
            _EINTRAEGE.pop(rp, None)
    befunde = []
    for rp, alt_ns, alt_gone in arbeit:
        mtime_ns, mtime, gone = _stat_pfad(rp)
        if mtime_ns != alt_ns or gone != alt_gone:
            befunde.append((rp, mtime_ns, mtime, gone))
    if not befunde:
        return 0
    with _COND:
        for rp, mtime_ns, mtime, gone in befunde:
            e = _EINTRAEGE.get(rp)
            if e is None:
                continue
            _SEQ += 1
            e.update(mtime_ns=mtime_ns, mtime=mtime, gone=gone, seq=_SEQ)
        _COND.notify_all()
    return len(befunde)

def _scanner_schleife():
    while True:
        time.sleep(SCAN_INTERVAL_S)
        try:
            _scan_once()
        except Exception:

            try:
                sys.stderr.write("[watch] Sammelscan-Fehler (nächster Sweep in %.0fs)\n"
                                 % SCAN_INTERVAL_S)
            except Exception:
                pass

def _scanner_sicherstellen():
    if not SCANNER_AUTOSTART:
        return
    with _COND:
        t = _SCANNER[0]
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=_scanner_schleife, name="watch-scan", daemon=True)
        _SCANNER[0] = t
    t.start()

def _deny_trail(handler, principal, rp):

    try:
        import portal_routes_session as _prs
        if callable(getattr(_prs, "_prov_log", None)):
            _prs._prov_log("cellfs.deny", principal,
                           json.dumps({"op": "watch", "path": rp, "need": "fs_read"}),
                           {"wire": "cellfs"})
    except Exception:
        pass
    try:
        sys.stderr.write("[cellfs-deny] wer=%s op=watch need=fs_read path=%s\n"
                         % (str(principal)[:60], rp[:200]))
    except Exception:
        pass

def _pruefen(handler, principal, query, p):

    if not p or not str(p).startswith("/"):
        return None, "absoluter Pfad nötig"
    if "\x00" in p:
        return None, "ungültiger Pfad"
    rp = os.path.realpath(p)
    if handler._cellfs_forbidden(rp):
        return None, "Pfad ist gesperrt (sensibel)"
    if not handler._cellfs_allowed(rp, "fs_read", principal, query):
        _deny_trail(handler, principal, rp)
        return None, ("Pfad nicht in der Session-Allowlist (deny-by-default) — "
                      "erst in den Rechten freigeben")
    return rp, None

def watch(handler, query, raw):

    principal = handler._principal()
    body = handler._json_obj()
    if body is None:
        return
    paths = body.get("paths")
    if not isinstance(paths, list) or not all(isinstance(x, str) for x in paths):
        return handler._sess_json({"ok": False, "error": "paths muss eine Liste von Strings sein"}, 400)
    if len(paths) > MAX_PATHS:
        return handler._sess_json({"ok": False, "error": "zu viele Pfade (max %d pro Watch)"
                                   % MAX_PATHS}, 413)
    since = body.get("since")
    if isinstance(since, bool) or (since is not None and not isinstance(since, int)):
        return handler._sess_json({"ok": False, "error": "since muss eine Zahl oder null sein"}, 400)
    try:
        timeout_s = float(body.get("timeout_s", TIMEOUT_MAX_S))
    except (TypeError, ValueError):
        timeout_s = TIMEOUT_MAX_S
    timeout_s = max(0.0, min(timeout_s, TIMEOUT_MAX_S))

    qeff = query or ""
    _qs = urllib.parse.parse_qs(qeff)
    if body.get("session") and not (_qs.get("session") or _qs.get("sid")):
        qeff = (qeff + "&" if qeff else "") + "session=" + urllib.parse.quote(str(body["session"]))

    angenommen = []
    denied = []
    for p in paths:
        rp, fehler = _pruefen(handler, principal, qeff, p)
        if rp is None:
            denied.append({"path": p, "error": fehler})
            continue
        if not _registrieren(rp):
            denied.append({"path": p, "error": "watch voll (max %d beobachtete Pfade auf dem "
                                               "Portal) — später erneut oder ls_batch nutzen"
                                               % MAX_WATCHED})
            continue
        angenommen.append((p, rp))
    _scanner_sicherstellen()

    def _stand(nur_seit=None):

        aus = []
        for req, rp in angenommen:
            e = _EINTRAEGE.get(rp)
            if e is None:
                continue
            if nur_seit is not None and e["seq"] <= nur_seit:
                continue
            aus.append({"path": req, "mtime": e["mtime"], "gone": e["gone"]})
        return aus

    antwort = None
    with _COND:

        if since is None or since > _SEQ:
            antwort = {"ok": True, "seq": _SEQ, "changes": _stand(None)}
            if since is not None:
                antwort["resync"] = True
        else:

            frist = time.monotonic() + timeout_s
            while True:
                changes = _stand(since)
                rest = frist - time.monotonic()
                if changes or rest <= 0:
                    antwort = {"ok": True, "seq": _SEQ, "changes": changes}
                    break

                _COND.wait(min(rest, 1.0))

    if denied:
        antwort["denied"] = denied
    return handler._sess_json(antwort)
