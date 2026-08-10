

import os, sys, json, time, threading

_LOCK = threading.RLock()

def _envf(name, default):
    try:
        return float(os.environ.get(name, "") or default)
    except Exception:
        return default

def _state_dir():

    for c in (os.environ.get("PN_WD_STATE_DIR"),
              os.path.expanduser("~/.local/share/brainbox-portal"),
              os.path.expanduser("~/.local/share/brainarbeit"),
              "/tmp"):
        if not c:
            continue
        if os.path.isdir(c):
            return c
        elter = os.path.dirname(os.path.normpath(c)) or "/"
        while elter and not os.path.isdir(elter):
            neu = os.path.dirname(elter)
            if neu == elter:
                break
            elter = neu
        if elter and os.access(elter, os.W_OK | os.X_OK):
            return c
    return "/tmp"

HB_PATH        = os.environ.get("PN_WD_HEARTBEAT_PATH") or os.path.join(_state_dir(), "watchdog-heartbeat.json")
META_TICK_S    = _envf("PN_WD_META_TICK_S", 60.0)
STALE_ABS_S    = _envf("PN_WD_STALE_ABS_S", 0.0)
DEADMAN_CFG    = os.environ.get("PN_WD_DEADMAN_CFG") or os.path.join(_state_dir(), "watchdog-deadman.json")
PING_MIN_S     = _envf("PN_WD_PING_MIN_S", 55.0)
RENOTIFY_S     = _envf("PN_WD_RENOTIFY_S", 3600.0)
CANARY_EVERY_S = _envf("PN_WD_CANARY_EVERY_S", 7 * 86400.0)

_STARTED = False
_LAST_PING = {"ts": 0.0}
_ALERTS = {}
_META = {"last_primary_seq": -1, "seq_since_change_ts": 0.0, "last_canary": 0.0, "tick_s": 60.0}

def _read():
    try:
        with open(HB_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _write(d):
    tmp = HB_PATH + ".tmp"
    try:

        os.makedirs(os.path.dirname(HB_PATH) or "/", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, HB_PATH)
        return True
    except Exception:
        return False

def beat(cells=None, dead=None, src="primary", now=None):

    now = time.time() if now is None else now
    try:
        with _LOCK:
            d = _read()
            e = d.get(src) or {}
            e = {"seq": int(e.get("seq", 0)) + 1, "ts": now, "pid": os.getpid(),
                 "cells": cells, "dead": dead}
            d[src] = e
            _write(d)
        _maybe_ping(now)
        return True
    except Exception:
        return False

def heartbeat():

    now = time.time()
    d = _read()
    prim = d.get("primary") or {}
    meta = d.get("meta") or {}
    age_p = (now - prim.get("ts", 0)) if prim.get("ts") else None
    age_m = (now - meta.get("ts", 0)) if meta.get("ts") else None
    with _LOCK:
        alerts = {k: dict(v) for k, v in _ALERTS.items()}
    return {"ok": True, "now": now, "primary": prim, "meta": meta,
            "age_primary_s": age_p, "age_meta_s": age_m,
            "stale_threshold_s": _stale_threshold(), "external_url_set": bool(get_config()["urls"]),
            "incidents": alerts}

def get_config():

    urls = []
    e = os.environ.get("PN_WD_DEADMAN_URL", "").strip()
    if e:
        urls += [u.strip() for u in e.split(",") if u.strip()]
    try:
        with open(DEADMAN_CFG, encoding="utf-8") as f:
            d = json.load(f)
        for u in (d.get("urls") or []):
            if u and u not in urls:
                urls.append(str(u))
    except Exception:
        d = {}
    return {"urls": urls, "raw_file": (d if isinstance(d, dict) else {})}

def _read_cfg_file():

    try:
        with open(DEADMAN_CFG, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _write_cfg_file(d):
    tmp = DEADMAN_CFG + ".tmp"
    os.makedirs(os.path.dirname(DEADMAN_CFG), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, DEADMAN_CFG)

def set_config(urls):
    urls = [str(u).strip() for u in (urls or []) if str(u).strip()]
    try:
        d = _read_cfg_file()
        d["urls"] = urls
        _write_cfg_file(d)
        return {"ok": True, "urls": urls}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def get_email_config():

    d = get_config()["raw_file"]
    email = str(d.get("alert_email") or "").strip()
    on = d.get("email_on")
    if on is not None:
        on = bool(on)
    return {"alert_email": email, "email_on": on}

def set_email_config(alert_email=None, email_on=None):

    try:
        d = _read_cfg_file()
        if alert_email is not None:
            d["alert_email"] = str(alert_email).strip()
        if email_on is not None:
            d["email_on"] = bool(email_on)
        _write_cfg_file(d)
        return {"ok": True, **get_email_config()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _maybe_ping(now):
    urls = get_config()["urls"]
    if not urls:
        return
    if now - _LAST_PING["ts"] < PING_MIN_S:
        return
    _LAST_PING["ts"] = now
    def _go():
        import urllib.request
        for u in urls:
            try:
                req = urllib.request.Request(u, method="GET",
                                             headers={"User-Agent": "pn-watchdog-deadman/1"})
                urllib.request.urlopen(req, timeout=8).read(64)
            except Exception:
                pass
    threading.Thread(target=_go, name="wd-deadman-ping", daemon=True).start()

def _stale_threshold():
    if STALE_ABS_S > 0:
        return STALE_ABS_S
    return max(180.0, 3.0 * float(_META.get("tick_s") or 60.0))

def _alert(alert_fn, cause, text, severity, now):

    with _LOCK:
        rec = _ALERTS.get(cause)
        fire = False
        if rec is None:
            rec = {"first": now, "last": now, "count": 1}
            _ALERTS[cause] = rec
            fire = True
        else:
            if rec["count"] < 2 and (now - rec["last"]) >= RENOTIFY_S:
                rec["count"] += 1
                rec["last"] = now
                fire = True
    if fire and alert_fn is not None:
        try:
            alert_fn(cause, text, severity)
        except Exception:
            pass

def _clear_incident(cause):
    with _LOCK:
        _ALERTS.pop(cause, None)

def _meta_iter(ctx, tick_fn, alert_fn, now=None):

    now = time.time() if now is None else now
    d = _read()
    prim = d.get("primary") or {}
    seq = int(prim.get("seq", -1))
    ts = float(prim.get("ts", 0) or 0)

    if seq != _META["last_primary_seq"]:
        _META["last_primary_seq"] = seq
        _META["seq_since_change_ts"] = now

    age = now - ts if ts else 1e9
    no_progress = now - (_META["seq_since_change_ts"] or now)
    stale = (age > _stale_threshold()) or (no_progress > _stale_threshold())

    if stale:
        txt = ("Session-Watchdog: Primaertick steht (Alter %ds, ohne Fortschritt %ds, seq %s). "
               "Meta-Watchdog uebernimmt die Aufsicht und alarmiert." % (int(age), int(no_progress), seq))
        _alert(alert_fn, "watchdog_primary_stale", txt, "critical", now)

        if tick_fn is not None:
            try:
                tick_fn(ctx)
            except Exception:
                pass
        beat(src="meta", now=now)
    else:
        _clear_incident("watchdog_primary_stale")

    if CANARY_EVERY_S > 0 and (now - (_META["last_canary"] or 0)) >= CANARY_EVERY_S:
        _META["last_canary"] = now
        if alert_fn is not None:
            try:
                alert_fn("watchdog_canary",
                         "Watchdog-Kanarienvogel: Meldeweg-Test. Kommt das an, ist der Alarmkanal "
                         "gesund. (Automatischer woechentlicher Selbsttest.)", "info")
            except Exception:
                pass
    return {"stale": stale, "age": age, "seq": seq}

def start(ctx, tick_fn, tick_s=60.0, alert_fn=None):

    global _STARTED
    with _LOCK:
        if _STARTED:
            return None
        _STARTED = True
        _META["tick_s"] = float(tick_s or 60.0)
        _META["last_canary"] = time.time()

    def _loop():
        time.sleep(12)
        while True:
            try:
                _meta_iter(ctx, tick_fn, alert_fn)
            except Exception:

                pass
            time.sleep(max(15.0, META_TICK_S))

    t = threading.Thread(target=_loop, name="pn-watchdog-deadman", daemon=True)
    t.start()
    return t

def _selftest():
    ok = True
    def ck(name, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print("  [%s] %s" % ("PASS" if cond else "FAIL", name))

    global HB_PATH, STALE_ABS_S, RENOTIFY_S, CANARY_EVERY_S, _ALERTS, _META
    import tempfile
    HB_PATH = os.path.join(tempfile.mkdtemp(), "hb.json")
    STALE_ABS_S = 100.0
    RENOTIFY_S = 1000.0
    CANARY_EVERY_S = 7 * 86400.0
    _ALERTS = {}
    _META = {"last_primary_seq": -1, "seq_since_change_ts": 0.0, "last_canary": 1_000_000.0, "tick_s": 30.0}

    fired = []
    def alert_fn(cause, text, severity):
        fired.append((cause, severity))

    ticks = {"n": 0}
    def tick_fn(ctx):
        ticks["n"] += 1

    t0 = 1_000_000.0

    beat(cells=3, dead=0, src="primary", now=t0)
    beat(cells=3, dead=0, src="primary", now=t0 + 30)
    hb = _read()
    ck("beat schreibt primary", hb.get("primary", {}).get("seq") == 2)
    ck("heartbeat() liefert Alter", heartbeat()["primary"]["seq"] == 2)

    r = _meta_iter(None, tick_fn, alert_fn, now=t0 + 31)
    ck("frisch: nicht stale", not r["stale"])
    ck("frisch: kein Alarm", len(fired) == 0)
    ck("frisch: kein Fallback-Tick", ticks["n"] == 0)

    r = _meta_iter(None, tick_fn, alert_fn, now=t0 + 500)
    ck("stale erkannt", r["stale"])
    ck("genau 1 kritischer Alarm", fired.count(("watchdog_primary_stale", "critical")) == 1)
    ck("Fallback-Tick lief", ticks["n"] == 1)
    ck("meta-beat gesetzt", _read().get("meta", {}).get("seq") == 1)

    r = _meta_iter(None, tick_fn, alert_fn, now=t0 + 600)
    ck("Dedup: kein zweiter Alarm im Fenster", fired.count(("watchdog_primary_stale", "critical")) == 1)
    ck("Fallback-Tick lief erneut", ticks["n"] == 2)

    r = _meta_iter(None, tick_fn, alert_fn, now=t0 + 500 + RENOTIFY_S + 1)
    ck("Re-Notify genau einmal", fired.count(("watchdog_primary_stale", "critical")) == 2)

    r = _meta_iter(None, tick_fn, alert_fn, now=t0 + 500 + 2 * RENOTIFY_S + 2)
    ck("kein dritter Alarm", fired.count(("watchdog_primary_stale", "critical")) == 2)

    beat(src="primary", now=t0 + 500 + 2 * RENOTIFY_S + 3)
    r = _meta_iter(None, tick_fn, alert_fn, now=t0 + 500 + 2 * RENOTIFY_S + 4)
    ck("Erholung: nicht stale", not r["stale"])
    ck("Erholung: Incident geloescht", "watchdog_primary_stale" not in _ALERTS)

    r = _meta_iter(None, tick_fn, alert_fn, now=t0 + 500 + 2 * RENOTIFY_S + 4 + STALE_ABS_S + 50)
    ck("neuer Stall feuert wieder", fired.count(("watchdog_primary_stale", "critical")) == 3)

    _META["last_canary"] = 0.0
    r = _meta_iter(None, tick_fn, alert_fn, now=t0 + 999999999)
    ck("Kanarien-Alarm feuert", any(c == "watchdog_canary" for c, _ in fired))

    print("\nSELFTEST:", "ALL GREEN" if ok else "FAILURES")
    return 0 if ok else 1

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("pn_watchdog_deadman — import me; start(ctx, tick_fn, tick_s, alert_fn); --selftest to verify.")
