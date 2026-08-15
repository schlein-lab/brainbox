

import json
import os
import sqlite3
import threading
import time

SWEEP_INTERVAL_S = int(os.environ.get("PN_SESSION_SWEEP_INTERVAL_S", "300"))

GNADENFRIST_S = int(os.environ.get("PN_SESSION_SWEEP_GRACE_S", "300"))
QUEUE_DB = os.environ.get("PN_QUEUE_DB") or os.path.expanduser(
    "~/.local/share/portioneer/queue.db")

SESSION_SOURCE = "session"
_STARTED = [False]
_LOCK = threading.Lock()

def _zeilen():

    try:
        c = sqlite3.connect("file:%s?mode=ro" % QUEUE_DB, uri=True)
    except Exception:
        return []
    try:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(
            "select id, cmd, client_tag, scope_unit, started_at, mem_estimate from jobs "
            "where state='running' and source=? and scope_unit like 'session:node=%' "
            "order by started_at", (SESSION_SOURCE,))]
    except Exception:
        return []
    finally:
        try:
            c.close()
        except Exception:
            pass

def _zellkennung(zeile):

    try:
        c = json.loads(zeile.get("cmd") or "")
        if isinstance(c, list) and len(c) > 1 and c[1]:
            return str(c[1])
    except Exception:
        pass
    return zeile.get("client_tag")

def _node_conn(nid):
    try:
        import portal_placement as _pp
        return _pp.node_endpoint(nid), _pp.node_token(nid)
    except Exception:
        return None, None

def _node_json(endpoint, token, pfad, timeout=8):

    import urllib.error
    import urllib.request
    try:
        req = urllib.request.Request((endpoint or "").rstrip("/") + pfad,
                                     headers={"X-Node-Token": token or ""})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, None
    except Exception:
        return 0, None

def _detach(job_id, grund):

    try:
        import sys as _s
        for _base in (os.environ.get("PNLIB_HOME"), os.path.expanduser("~/portioneer")):
            if _base and os.path.isdir(os.path.join(_base, "pnlib")) and _base not in _s.path:
                _s.path.insert(0, _base)
        from pnlib import ipc as _ipc
        return _ipc.send_request({"verb": "session-detach", "job_id": int(job_id),
                                  "state": "done", "reason": grund}, timeout=6.0)
    except Exception as e:
        return {"ok": False, "error": "pnd unreachbar: %s" % e}

def sweep_once(dry=False):

    stats = {"zeilen": 0, "lebt": 0, "geschlossen": 0, "stumm": 0, "jung": 0, "fehler": 0,
             "mib_frei": 0}
    zeilen = _zeilen()
    stats["zeilen"] = len(zeilen)
    if not zeilen:
        return stats

    jetzt = time.time()
    gesund = {}
    for z in zeilen:
        node = (z.get("scope_unit") or "").split("=", 1)[-1]

        begonnen = z.get("started_at")
        if begonnen is None:
            begonnen = jetzt
        if (jetzt - begonnen) < GNADENFRIST_S:
            stats["jung"] += 1
            continue
        if node not in gesund:
            ep, tok = _node_conn(node)
            st, h = _node_json(ep, tok, "/health")
            gesund[node] = (ep, tok, h if (st == 200 and isinstance(h, dict) and h.get("ok")) else None)
        ep, tok, h = gesund[node]
        if h is None:
            stats["stumm"] += 1
            continue
        zid = _zellkennung(z)
        if not zid:
            stats["fehler"] += 1
            continue
        st, obj = _node_json(ep, tok, "/cells/%s" % zid)
        zustand = (obj or {}).get("state") or ("gone" if st == 404 else None)
        if zustand == "running":
            stats["lebt"] += 1
            continue
        if zustand not in ("gone", "exited"):
            stats["fehler"] += 1
            continue
        if dry:
            stats["geschlossen"] += 1
            stats["mib_frei"] += (z.get("mem_estimate") or 0)
            continue
        r = _detach(z["id"], "Zelle auf %s ist fort (%s) — Aufraeumer" % (node, zustand))
        if isinstance(r, dict) and r.get("ok"):
            stats["geschlossen"] += 1
            stats["mib_frei"] += (z.get("mem_estimate") or 0)
        else:
            stats["fehler"] += 1
    return stats

def _loop():
    time.sleep(20)
    while True:
        try:
            s = sweep_once()
            if s.get("geschlossen") or s.get("fehler"):
                import sys
                sys.stderr.write(
                    "[session-sweep] Zeilen=%(zeilen)d lebt=%(lebt)d geschlossen=%(geschlossen)d "
                    "(%(mib_frei)d MiB frei) stumm=%(stumm)d jung=%(jung)d fehler=%(fehler)d\n" % s)
        except Exception:
            pass
        time.sleep(SWEEP_INTERVAL_S)

def session_rows_sweep_start():
    with _LOCK:
        if _STARTED[0]:
            return False
        _STARTED[0] = True
    threading.Thread(target=_loop, name="session-rows-sweep", daemon=True).start()
    return True

if __name__ == "__main__":
    import sys

    _srv = os.path.dirname(os.path.abspath(__file__))
    if _srv not in sys.path:
        sys.path.insert(0, _srv)
    trocken = "--dry" in sys.argv
    st = sweep_once(dry=trocken)
    print(("TROCKENLAUF " if trocken else "") +
          "Zeilen=%(zeilen)d lebt=%(lebt)d geschlossen=%(geschlossen)d (%(mib_frei)d MiB) "
          "stumm=%(stumm)d jung=%(jung)d fehler=%(fehler)d" % st)
