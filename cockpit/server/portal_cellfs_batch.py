
import json
import os
import sys
import urllib.parse

MAX_PATHS = 500
MAX_ENTRIES = 2000

def _deny_trail(handler, principal, rp):

    try:
        import portal_routes_session as _prs
        if callable(getattr(_prs, "_prov_log", None)):
            _prs._prov_log("cellfs.deny", principal,
                           json.dumps({"op": "ls_batch", "path": rp, "need": "fs_read"}),
                           {"wire": "cellfs"})
    except Exception:
        pass
    try:
        sys.stderr.write("[cellfs-deny] wer=%s op=ls_batch need=fs_read path=%s\n"
                         % (str(principal)[:60], rp[:200]))
    except Exception:
        pass

def _ls_one(handler, principal, query, p):

    if not p:
        r = handler._cellfs_roots(query, principal)
        r["req_path"] = p
        return r
    if not str(p).startswith("/"):
        return {"ok": False, "error": "absoluter Pfad nötig", "req_path": p}
    if "\x00" in p:
        return {"ok": False, "error": "ungültiger Pfad", "req_path": p}
    rp = os.path.realpath(p)
    if handler._cellfs_forbidden(rp):
        return {"ok": False, "error": "Pfad ist gesperrt (sensibel)", "req_path": p}
    if not handler._cellfs_allowed(rp, "fs_read", principal, query):
        _deny_trail(handler, principal, rp)
        return {"ok": False, "req_path": p, "error":
                "Pfad nicht in der Session-Allowlist (deny-by-default) — erst in den Rechten freigeben"}
    try:
        if os.path.isfile(rp):
            st = os.stat(rp)
            return {"ok": True, "req_path": p, "entries": [
                {"name": os.path.basename(rp), "dir": False, "size": st.st_size,
                 "mtime": int(st.st_mtime)}]}
        entries = []
        for n in sorted(os.listdir(rp))[:MAX_ENTRIES]:
            fp = os.path.join(rp, n)
            try:
                st = os.stat(fp)
                _d = os.path.isdir(fp)
                entries.append({"name": n, "dir": _d,
                                "size": (0 if _d else st.st_size), "mtime": int(st.st_mtime)})
            except OSError:
                continue
        return {"ok": True, "req_path": p, "path": rp, "entries": entries}
    except PermissionError:
        return {"ok": False, "error": "keine Berechtigung", "req_path": p}
    except FileNotFoundError:
        return {"ok": False, "error": "nicht gefunden", "req_path": p}
    except Exception:
        try:
            import portal_routes_session as _prs
            if callable(getattr(_prs, "_traceback_log", None)):
                _prs._traceback_log("cellfs ls_batch")
        except Exception:
            pass
        return {"ok": False, "error": "Dateizugriff fehlgeschlagen", "req_path": p}

def ls_batch(handler, query, raw):

    principal = handler._principal()
    body = handler._json_obj()
    if body is None:
        return
    paths = body.get("paths")
    if not isinstance(paths, list) or not all(isinstance(x, str) for x in paths):
        return handler._sess_json({"ok": False, "error": "paths muss eine Liste von Strings sein"}, 400)
    if len(paths) > MAX_PATHS:
        return handler._sess_json({"ok": False, "error": "zu viele Pfade (max %d pro Aufruf)"
                                   % MAX_PATHS}, 413)

    qeff = query or ""
    _qs = urllib.parse.parse_qs(qeff)
    if body.get("session") and not (_qs.get("session") or _qs.get("sid")):
        qeff = (qeff + "&" if qeff else "") + "session=" + urllib.parse.quote(str(body["session"]))
    results = [_ls_one(handler, principal, qeff, p) for p in paths]
    return handler._sess_json({"ok": True, "count": len(results), "results": results})
