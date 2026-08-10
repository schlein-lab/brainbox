

import os
import sqlite3
import sys
import threading
import time

SYNC_INTERVAL_S = int(os.environ.get("PN_RELAY_IDENT_SYNC_INTERVAL_S", "20"))
QUEUE_DB = os.environ.get("PN_QUEUE_DB") or os.path.expanduser(
    "~/.local/share/portioneer/queue.db")
ID_METHOD = "device-channel"
_STARTED = [False]
_LOCK = threading.Lock()

def _log(msg):
    try:
        sys.stderr.write("[relay-ident-sync] %s\n" % msg)
        sys.stderr.flush()
    except Exception:
        pass

def _registry():

    for _p in (os.environ.get("PNLIB_HOME"), os.path.expanduser("~/portioneer")):
        if _p and os.path.isdir(os.path.join(_p, "relaylib")):
            if _p in sys.path:
                sys.path.remove(_p)
            sys.path.insert(0, _p)
            break
    from relaylib import registry
    return registry

def reconcile_once():

    bound = pruned = 0

    try:
        reg = _registry()
        rcx = reg.connect()
        rows = rcx.execute(
            "SELECT device_did, principal, revoked_at FROM alliances").fetchall()
        try:
            rcx.close()
        except Exception:
            pass
    except Exception as e:
        _log("relay.db nicht lesbar (%s: %s) — nächster Tick" % (type(e).__name__, e))
        return (0, 0)

    from pnlib import db
    qcx = None
    try:
        qcx = sqlite3.connect(QUEUE_DB, timeout=15)
        qcx.execute("PRAGMA busy_timeout=15000")
        for r in rows:
            did, principal, revoked_at = r[0], r[1], r[2]
            if not did or not principal:
                continue
            cur = qcx.execute(
                "SELECT principal, verified FROM identities WHERE method=? AND selector=?",
                (ID_METHOD, did)).fetchone()
            if revoked_at is None:

                if (not cur) or cur[0] != principal or not cur[1]:
                    db.bind_identity(qcx, ID_METHOD, did, principal, verified=1)
                    bound += 1
            else:

                if cur is not None:
                    qcx.execute(
                        "DELETE FROM identities WHERE method=? AND selector=?",
                        (ID_METHOD, did))
                    qcx.commit()
                    pruned += 1
    except Exception as e:
        _log("queue.db-Abgleich fehlgeschlagen (%s: %s)" % (type(e).__name__, e))
    finally:
        if qcx is not None:
            try:
                qcx.close()
            except Exception:
                pass
    if bound or pruned:
        _log("abgeglichen: %d gebunden, %d entfernt" % (bound, pruned))
    return (bound, pruned)

def _loop():
    time.sleep(2.0)
    while True:
        try:
            reconcile_once()
        except Exception as e:
            _log("Tick-Fehler %s: %s" % (type(e).__name__, e))
        time.sleep(max(5, SYNC_INTERVAL_S))

def relay_ident_sync_start():

    with _LOCK:
        if _STARTED[0]:
            return False
        _STARTED[0] = True
    threading.Thread(target=_loop, name="relay-ident-sync", daemon=True).start()
    return True

if __name__ == "__main__":
    print(reconcile_once())
