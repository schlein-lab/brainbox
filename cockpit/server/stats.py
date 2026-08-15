
import json
import os
import threading
import time

LLM_VERBS = ("llm.chat", "llm.v1_openai", "llm.v1_anthropic", "llm.turn")

def _midnight(now):
    lt = time.localtime(now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))

def _blank_win():
    return {"events": 0, "llm_calls": 0, "llm_chars": 0, "by_user": {}}

def _add(win, principal, is_llm, chars):
    win["events"] += 1
    u = win["by_user"].setdefault(principal, {"events": 0, "llm_calls": 0, "llm_chars": 0})
    u["events"] += 1
    if is_llm:
        win["llm_calls"] += 1
        win["llm_chars"] += chars
        u["llm_calls"] += 1
        u["llm_chars"] += chars

_EV_KEEP_S = 8 * 86400
_DAY_KEEP_D = 32
_RESULT_TTL = float(os.environ.get("PN_STATS_RESULT_TTL", "5"))
_PROV_LOCK = threading.Lock()
_PROV_STATE = {}

import portal_zustand as _zst
_zst.register("stats._PROV_STATE", "cursor", __name__, ref=_PROV_STATE, ttl_s=5.0,
              beschreibung="inkrementeller Provenance-Aggregatzustand je Pfad: Datei-Signatur + Byte-Offset hinter der letzten vollen Zeile + Tages-Summen; Verlust => VOLL-Neueinlesen des Logs (Doppelarbeit, keine Doppelzustellung); Ergebnis-TTL = _RESULT_TTL",
              neustart="rekonstruiert", schreiber="Statistik-Leser unter _PROV_LOCK")

def _prov_blank():
    return {"sig": None,
            "offset": 0,
            "lines": 0,
            "verbs": {},
            "total": _blank_win(),
            "days": {},
            "recent": [],
            "ev_floor": 0.0,
            "day_floor": 0.0,
            "now_max": 0.0,
            "tail_ev": None,
            "result": None}

def _parse_event(raw):

    raw = raw.strip()
    if not raw or raw[0] != "{":
        return None
    try:
        o = json.loads(raw)
    except (ValueError, TypeError):
        return None
    principal = str(o.get("principal") or "owner")
    verb = str(o.get("verb") or "")
    ts = float(o.get("ts") or 0)
    if ts > 1e12:
        ts /= 1000.0
    meta = o.get("meta") if isinstance(o.get("meta"), dict) else {}
    is_llm = verb in LLM_VERBS
    chars = 0
    if is_llm:
        try:
            chars = int(meta.get("reply_chars") or 0) + int(meta.get("prompt_chars") or 0)
        except (TypeError, ValueError):
            chars = 0
    return (ts, principal, verb, is_llm, chars)

def _ingest_event(st, ev):
    ts, principal, verb, is_llm, chars = ev
    st["lines"] += 1
    st["verbs"][verb] = st["verbs"].get(verb, 0) + 1
    _add(st["total"], principal, is_llm, chars)
    if ts >= st["day_floor"]:
        dk = time.strftime("%Y-%m-%d", time.localtime(ts))
        b = st["days"].get(dk)
        if b is None:
            lt = time.localtime(ts)
            end = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, 0, 0, 0, 0, 0, -1))
            b = st["days"][dk] = {"end": end, "events": 0, "llm_calls": 0, "llm_chars": 0}
        b["events"] += 1
        if is_llm:
            b["llm_calls"] += 1
            b["llm_chars"] += chars
    if ts >= st["ev_floor"]:
        st["recent"].append((ts, principal, is_llm, chars))

def _ingest_bytes(st, prov_path):

    with open(prov_path, "rb") as f:
        f.seek(st["offset"])
        blob = f.read()
    nl = blob.rfind(b"\n")
    if nl >= 0:
        for raw in blob[:nl + 1].decode("utf-8").split("\n"):
            ev = _parse_event(raw)
            if ev is not None:
                _ingest_event(st, ev)
        st["offset"] += nl + 1
    tail = blob[nl + 1:]
    st["tail_ev"] = None
    if tail:
        try:
            st["tail_ev"] = _parse_event(tail.decode("utf-8"))
        except UnicodeDecodeError:
            st["tail_ev"] = None

def _prune(st, now):
    st["now_max"] = max(st["now_max"], now)
    ev_cut = st["now_max"] - _EV_KEEP_S
    if ev_cut - st["ev_floor"] > 3600:
        st["recent"] = [e for e in st["recent"] if e[0] >= ev_cut]
        st["ev_floor"] = ev_cut
    day_cut = _midnight(st["now_max"] - (_DAY_KEEP_D - 1) * 86400)
    if day_cut > st["day_floor"]:
        for dk in [k for k, b in st["days"].items() if b["end"] <= day_cut]:
            del st["days"][dk]
        st["day_floor"] = day_cut

def _copy_win(w):
    return {"events": w["events"], "llm_calls": w["llm_calls"], "llm_chars": w["llm_chars"],
            "by_user": {u: dict(v) for u, v in w["by_user"].items()}}

def _build_out(st, now, days, top):

    today0 = _midnight(now)
    week0 = now - 7 * 86400
    series_from = _midnight(now - (days - 1) * 86400)

    today, week = _blank_win(), _blank_win()
    for (ts, principal, is_llm, chars) in st["recent"]:
        if ts >= today0:
            _add(today, principal, is_llm, chars)
        if ts >= week0:
            _add(week, principal, is_llm, chars)

    total, verbs, lines = st["total"], st["verbs"], st["lines"]
    tail = st["tail_ev"]
    tail_dk = None
    if tail is not None:
        ts, principal, verb, is_llm, chars = tail
        lines += 1
        verbs = dict(verbs)
        verbs[verb] = verbs.get(verb, 0) + 1
        total = _copy_win(total)
        _add(total, principal, is_llm, chars)
        if ts >= today0:
            _add(today, principal, is_llm, chars)
        if ts >= week0:
            _add(week, principal, is_llm, chars)
        if ts >= series_from:
            tail_dk = time.strftime("%Y-%m-%d", time.localtime(ts))

    series_list = []
    for i in range(days):
        ts = series_from + i * 86400
        dk = time.strftime("%Y-%m-%d", time.localtime(ts))
        b = st["days"].get(dk)
        d = {"events": b["events"], "llm_calls": b["llm_calls"], "llm_chars": b["llm_chars"]} \
            if b is not None else {"events": 0, "llm_calls": 0, "llm_chars": 0}
        if tail_dk == dk:
            d["events"] += 1
            if tail[3]:
                d["llm_calls"] += 1
                d["llm_chars"] += tail[4]
        series_list.append({"day": dk, "label": time.strftime("%d.%m.", time.localtime(ts)), **d})

    def top_users(win):
        us = [{"user": u, **v} for u, v in win["by_user"].items()]
        us.sort(key=lambda x: (x["llm_calls"], x["events"]), reverse=True)
        return us[:top]

    def win_out(w):
        return {"events": w["events"], "llm_calls": w["llm_calls"], "llm_chars": w["llm_chars"],
                "llm_tokens_est": w["llm_chars"] // 4, "users": len(w["by_user"]),
                "top_users": top_users(w)}

    top_verbs = sorted(verbs.items(), key=lambda kv: kv[1], reverse=True)[:16]
    return {
        "ok": True,
        "entries": lines,
        "today": win_out(today),
        "week": win_out(week),
        "total": win_out(total),
        "series": series_list,
        "verbs": [{"verb": k, "count": v} for k, v in top_verbs],
        "generated": now,
    }

def aggregate(prov_path, now=None, days=14, top=12):

    explicit_now = now is not None
    now = time.time() if now is None else now
    with _PROV_LOCK:
        try:
            fst = os.stat(prov_path)
        except FileNotFoundError:
            _PROV_STATE.pop(prov_path, None)
            return _build_out(_prov_blank(), now, days, top)
        except OSError as e:
            _PROV_STATE.pop(prov_path, None)
            return {"ok": False, "error": "provenance nicht lesbar: %s" % e, "generated": now}

        sig = (fst.st_ino, fst.st_size, fst.st_mtime)
        st = _PROV_STATE.get(prov_path)
        week0 = now - 7 * 86400
        series_from = _midnight(now - (days - 1) * 86400)
        if st is not None and (
                fst.st_ino != st["sig"][0]
                or fst.st_size < st["offset"]
                or fst.st_mtime < st["sig"][2]
                or week0 < st["ev_floor"]
                or series_from < st["day_floor"]):
            st = None
        if st is not None and sig == st["sig"]:
            res = st["result"]
            if (res is not None and not explicit_now and res["sig"] == sig
                    and (time.time() - res["t"]) < _RESULT_TTL):
                return dict(res["out"])
        else:
            if st is None:
                st = _prov_blank()
                st["ev_floor"] = now - _EV_KEEP_S
                st["day_floor"] = _midnight(now - (_DAY_KEEP_D - 1) * 86400)
                st["now_max"] = now
            try:
                _ingest_bytes(st, prov_path)
            except OSError as e:
                _PROV_STATE.pop(prov_path, None)
                return {"ok": False, "error": "provenance nicht lesbar: %s" % e, "generated": now}
            except Exception:
                _PROV_STATE.pop(prov_path, None)
                raise
            st["sig"] = sig
            _PROV_STATE[prov_path] = st
        _prune(st, now)
        out = _build_out(st, now, days, top)
        if not explicit_now:
            st["result"] = {"t": time.time(), "sig": sig, "out": out}
        return dict(out)

def _uwin():
    return {"jobs": 0, "wall_s": 0.0, "llm_calls": 0.0, "llm_tokens": 0.0, "cpu_s": 0.0,
            "mem_peak": 0.0, "by_user": {}}

def _uadd(win, principal, wall_s, llm_calls, cpu_s, mem_peak, llm_tokens=0.0):
    win["jobs"] += 1
    win["wall_s"] += wall_s
    win["llm_calls"] += llm_calls
    win["llm_tokens"] += llm_tokens
    win["cpu_s"] += cpu_s
    win["mem_peak"] = max(win["mem_peak"], mem_peak)
    u = win["by_user"].setdefault(principal, {"jobs": 0, "wall_s": 0.0, "llm_calls": 0.0,
                                              "llm_tokens": 0.0, "cpu_s": 0.0, "mem_peak": 0.0})
    u["jobs"] += 1
    u["wall_s"] += wall_s
    u["llm_calls"] += llm_calls
    u["llm_tokens"] += llm_tokens
    u["cpu_s"] += cpu_s
    u["mem_peak"] = max(u["mem_peak"], mem_peak)

def usage_history(acct_db_path, now=None, days=14, top=12):

    import sqlite3
    now = time.time() if now is None else now
    today0 = _midnight(now)
    week0 = now - 7 * 86400
    series_from = _midnight(now - (days - 1) * 86400)
    today, week, total = _uwin(), _uwin(), _uwin()
    series = {}
    rows = 0
    have_cpu = have_mem = False
    table_ok = False
    try:
        cx = sqlite3.connect("file:%s?mode=ro" % acct_db_path, uri=True, timeout=2)
        try:
            cx.execute("PRAGMA busy_timeout=200")

            try:
                cur = cx.execute("SELECT principal, mem_peak, cpu_s, wall_s, llm_calls, ts, "
                                 "llm_tokens FROM job_actuals")
            except sqlite3.OperationalError:
                cur = cx.execute("SELECT principal, mem_peak, cpu_s, wall_s, llm_calls, ts "
                                 "FROM job_actuals")
            table_ok = True
            for r in cur:
                principal = str(r[0] or "owner")
                mem_peak = float(r[1]) if r[1] is not None else 0.0
                cpu_s = float(r[2]) if r[2] is not None else 0.0
                wall_s = float(r[3]) if r[3] is not None else 0.0
                llm_calls = float(r[4]) if r[4] is not None else 0.0
                ts = float(r[5]) if r[5] is not None else 0.0
                llm_tokens = float(r[6]) if len(r) > 6 and r[6] is not None else 0.0
                if r[2] is not None:
                    have_cpu = True
                if r[1] is not None:
                    have_mem = True
                rows += 1
                _uadd(total, principal, wall_s, llm_calls, cpu_s, mem_peak, llm_tokens)
                if ts >= today0:
                    _uadd(today, principal, wall_s, llm_calls, cpu_s, mem_peak, llm_tokens)
                if ts >= week0:
                    _uadd(week, principal, wall_s, llm_calls, cpu_s, mem_peak, llm_tokens)
                if ts >= series_from:
                    dk = time.strftime("%Y-%m-%d", time.localtime(ts))
                    d = series.setdefault(dk, {"jobs": 0, "wall_s": 0.0, "llm_calls": 0.0, "cpu_s": 0.0})
                    d["jobs"] += 1
                    d["wall_s"] += wall_s
                    d["llm_calls"] += llm_calls
                    d["cpu_s"] += cpu_s
        finally:
            cx.close()
    except sqlite3.Error:
        pass
    except OSError:
        pass

    if not table_ok:

        return {"ok": False, "rows": 0,
                "error": "Verbrauchsdaten fehlen: der Abrechnungsdienst (pn-acctd) läuft nicht.",
                "reason": "acct.db fehlt oder hat keine job_actuals-Tabelle: %s" % acct_db_path}

    series_list = []
    for i in range(days):
        ts = series_from + i * 86400
        dk = time.strftime("%Y-%m-%d", time.localtime(ts))
        d = series.get(dk, {"jobs": 0, "wall_s": 0.0, "llm_calls": 0.0, "cpu_s": 0.0})
        series_list.append({"day": dk, "label": time.strftime("%d.%m.", time.localtime(ts)),
                            "jobs": d["jobs"], "wall_s": round(d["wall_s"], 1),
                            "llm_calls": round(d["llm_calls"], 1), "cpu_s": round(d["cpu_s"], 1)})

    def top_users(win):
        us = []
        for u, v in win["by_user"].items():
            us.append({"user": u, "jobs": v["jobs"], "wall_s": round(v["wall_s"], 1),
                       "llm_calls": round(v["llm_calls"], 1),
                       "llm_tokens": int(v.get("llm_tokens") or 0),
                       "cpu_s": round(v["cpu_s"], 1), "mem_peak": round(v["mem_peak"], 1)})
        us.sort(key=lambda x: (x["wall_s"], x["llm_calls"], x["jobs"]), reverse=True)
        return us[:top]

    def win_out(w):
        return {"jobs": w["jobs"], "wall_s": round(w["wall_s"], 1),
                "llm_calls": round(w["llm_calls"], 1),
                "llm_tokens": int(w.get("llm_tokens") or 0), "cpu_s": round(w["cpu_s"], 1),
                "mem_peak": round(w["mem_peak"], 1), "users": len(w["by_user"]),
                "top_users": top_users(w)}

    return {
        "ok": True,
        "rows": rows,
        "have_cpu": have_cpu,
        "have_mem": have_mem,
        "today": win_out(today),
        "week": win_out(week),
        "total": win_out(total),
        "series": series_list,
        "generated": now,
    }

_USAGE_TTL = float(os.environ.get("PN_USAGE_CACHE_TTL", "8"))
_USAGE_LOCK = threading.Lock()
_USAGE_CACHE = {}
_USAGE_INFLIGHT = set()
_zst.register("stats._USAGE_CACHE", "cache", __name__, ref=_USAGE_CACHE, ttl_s=8.0,
              beschreibung="acct.db-Voll-Aggregation stale-while-revalidate (Single-Flight); auch ok:False wird gecacht und ANGEZEIGT",
              neustart="verfaellt", schreiber="_usage_refresh() (Hintergrund) unter _USAGE_LOCK")
_zst.register("stats._USAGE_INFLIGHT", "singleton", __name__, ref=_USAGE_INFLIGHT,
              beschreibung="Pfade mit laufender Hintergrund-Neuberechnung (Single-Flight)",
              neustart="verfaellt", schreiber="usage-Leser")

def _usage_refresh(acct_db_path, days, top):
    try:
        out = usage_history(acct_db_path, days=days, top=top)
        with _USAGE_LOCK:
            _USAGE_CACHE[acct_db_path] = {"t": time.time(), "out": out}
    finally:
        with _USAGE_LOCK:
            _USAGE_INFLIGHT.discard(acct_db_path)

def usage_history_cached(acct_db_path, ttl=None, days=14, top=12):

    ttl = _USAGE_TTL if ttl is None else float(ttl)
    now = time.time()
    with _USAGE_LOCK:
        row = _USAGE_CACHE.get(acct_db_path)
        if row is not None:
            out = dict(row["out"])
            if (now - row["t"]) >= ttl and acct_db_path not in _USAGE_INFLIGHT:
                _USAGE_INFLIGHT.add(acct_db_path)
                threading.Thread(target=_usage_refresh, args=(acct_db_path, days, top),
                                 name="usage-history-refresh", daemon=True).start()
            return out
    out = usage_history(acct_db_path, days=days, top=top)
    with _USAGE_LOCK:
        _USAGE_CACHE[acct_db_path] = {"t": time.time(), "out": out}
    return dict(out)
