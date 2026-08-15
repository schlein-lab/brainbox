

import json
import os
import threading
import time
import urllib.error
import urllib.request

POLL_INTERVAL_S = 60

LEASE_INTERVAL_S = float(os.environ.get("PN_LEASE_INTERVAL_S", "10") or 0)
LEASE_TIMEOUT_S = float(os.environ.get("PN_LEASE_TIMEOUT_S", "3") or 3)
LEASE_MISS_VERDACHT = 3
LEASE_404_BACKOFF_S = float(os.environ.get("PN_LEASE_404_BACKOFF_S", "600") or 600)

_STARTED = [False]
_LOCK = threading.Lock()

_LEASE = {}
_LEASE_LK = threading.Lock()

def _reg_and_probe():

    try:
        import portal_routes_device as prd
        return getattr(prd, "_WORKER_REG", None), getattr(prd, "_node_health_get", None)
    except Exception:
        return None, None

def poll_once():

    stats = {"probed": 0, "online": 0, "offline": 0}
    reg, probe = _reg_and_probe()
    if reg is None or probe is None:
        return stats
    try:
        rows = reg.list() or []
    except Exception:
        return stats
    for row in rows:
        wid = (row or {}).get("id")
        if not wid:
            continue
        try:
            rec = reg.get(wid)
            if not rec:
                continue
            stats["probed"] += 1
            ok, info = probe(rec.get("endpoint"), rec.get("token"))
            if ok and isinstance(info, dict):
                facts = dict(rec.get("facts") or {})
                facts["health"] = info
                caps = info.get("caps") if isinstance(info.get("caps"), dict) else None
                reg.update_health(wid, facts=facts, state="online", caps=caps)
                stats["online"] += 1
            else:
                reg.mark_offline(wid)
                stats["offline"] += 1
        except Exception:
            try:
                reg.mark_offline(wid)
            except Exception:
                pass
            stats["offline"] += 1
    return stats

def _lease_state(wid):

    with _LEASE_LK:
        st = _LEASE.get(wid)
        if st is None:
            st = {"misses": 0, "nolease_until": 0.0}
            _LEASE[wid] = st
        return st

def _lease_get(endpoint, token, timeout=None):

    if not endpoint:
        return None, None
    try:
        req = urllib.request.Request(endpoint.rstrip("/") + "/lease",
                                     headers={"X-Node-Token": token or ""}, method="GET")
        with urllib.request.urlopen(req, timeout=(timeout or LEASE_TIMEOUT_S)) as r:
            body = r.read(4096).decode("utf-8", "replace")
        try:
            obj = json.loads(body)
        except ValueError:
            obj = None
        return 200, obj if isinstance(obj, dict) else None
    except urllib.error.HTTPError as e:
        return int(getattr(e, "code", 0) or 0), None
    except Exception:
        return None, None

def _reg_txn(reg, wid, mutate):

    try:
        with reg._lock:
            d = reg._load()
            rec = d.get(wid)
            if rec is None:
                return None
            mutate(rec)
            reg._save(d)
            return rec
    except AttributeError:
        return None

def lease_once(now=None):

    stats = {"probed": 0, "ok": 0, "miss": 0, "verdacht": 0, "nolease": 0}
    reg, _ = _reg_and_probe()
    if reg is None:
        return stats
    if now is None:
        now = time.time()
    try:
        rows = reg.list() or []
    except Exception:
        return stats
    for row in rows:
        wid = (row or {}).get("id")
        if not wid:
            continue
        try:
            st = _lease_state(wid)
            if st["nolease_until"] > now:
                stats["nolease"] += 1
                continue
            rec = reg.get(wid)
            if not rec or not rec.get("endpoint"):
                continue
            stats["probed"] += 1
            code, obj = _lease_get(rec.get("endpoint"), rec.get("token"))
            if code == 200 and isinstance(obj, dict):
                st["misses"] = 0
                st["nolease_until"] = 0.0
                lease = {"seq": obj.get("seq"), "ts": obj.get("ts"),
                         "cells_n": obj.get("cells_n"), "load1": obj.get("load1"), "at": now}

                def _fold(r, lease=lease):
                    facts = r.get("facts")
                    if not isinstance(facts, dict):
                        facts = {}
                        r["facts"] = facts
                    facts["lease"] = lease
                    r["last_seen"] = time.time()
                    if r.get("state") == "verdaechtig":
                        r["state"] = "online"

                if _reg_txn(reg, wid, _fold) is None:

                    facts = dict(rec.get("facts") or {})
                    facts["lease"] = lease
                    reg.update_health(wid, facts=facts,
                                      state=("online" if rec.get("state") == "verdaechtig"
                                             else None))
                stats["ok"] += 1
            elif code == 404:
                st["misses"] = 0
                st["nolease_until"] = now + LEASE_404_BACKOFF_S
                stats["nolease"] += 1
            else:
                st["misses"] += 1
                stats["miss"] += 1
                if st["misses"] >= LEASE_MISS_VERDACHT and rec.get("state") == "online":

                    def _verd(r):
                        if r.get("state") == "online":
                            r["state"] = "verdaechtig"

                    if _reg_txn(reg, wid, _verd) is not None:
                        stats["verdacht"] += 1
        except Exception:
            pass
    return stats

def _loop():
    time.sleep(5)
    while True:
        try:
            poll_once()
        except Exception:
            pass
        time.sleep(POLL_INTERVAL_S)

def _lease_loop():
    time.sleep(7)
    while True:
        try:
            lease_once()
        except Exception:
            pass
        time.sleep(LEASE_INTERVAL_S)

def nodes_poll_start():

    with _LOCK:
        if _STARTED[0]:
            return False
        _STARTED[0] = True
    threading.Thread(target=_loop, name="nodes-health-poll", daemon=True).start()
    if LEASE_INTERVAL_S > 0:
        threading.Thread(target=_lease_loop, name="nodes-lease-poll", daemon=True).start()
    return True
