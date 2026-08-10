
import json
import math
import os
import sqlite3
import time

DAY = 86400.0

def _cfg(key, default):
    v = os.environ.get(key, "").strip()
    try:
        return type(default)(v) if v else default
    except ValueError:
        return default

def config(cores, mem_total_mib):
    box_seconds_per_day = cores * DAY
    return {
        "cores": float(cores),
        "mem_total": float(mem_total_mib),

        "overhead_s": _cfg("PN_JOB_OVERHEAD_S", 2.0),

        "backlog_budget": _cfg("PN_BACKLOG_BUDGET_S", 8.0 * 3600.0 * float(cores)),

        "max_queued_rows": _cfg("PN_MAX_QUEUED_ROWS", 2000),
        "halflife_s": _cfg("PN_FAIRSHARE_HALFLIFE_S", 3.0 * DAY),
        "window_s": _cfg("PN_FAIRSHARE_WINDOW_S", 14.0 * DAY),

        "floor_s": _cfg("PN_BACKLOG_FLOOR_S", 900.0),
        "fairshare_weight": _cfg("PN_FAIRSHARE_PRIO_WEIGHT", 60),
        "age_weight": _cfg("PN_AGE_PRIO_WEIGHT", 40),
        "age_max_s": _cfg("PN_AGE_MAX_S", 6.0 * 3600.0),
        "_box_day": box_seconds_per_day,
    }

CPU_WIDTH_UNSET = 4.5

def dominant_share(prof, cfg):

    q = prof.get("cpu_quota_pct")
    width = (q / 100.0) if q else CPU_WIDTH_UNSET
    cpu_frac = min(1.0, width / cfg["cores"])
    mem_frac = min(1.0, float(prof.get("mem") or 0) / cfg["mem_total"])
    return max(cpu_frac, mem_frac, 1e-6)

def reserve(prof, cfg):

    wall = float(prof.get("timeout_s") or 0) or 600.0
    return dominant_share(prof, cfg) * max(wall, cfg["overhead_s"])

def debt(prof, actual_s, cfg):

    return dominant_share(prof, cfg) * max(float(actual_s or 0.0), cfg["overhead_s"])

def account_of(principal, client_tag):

    return "%s/%s" % (principal or "?", (client_tag or "default"))

def _profile(blob):
    try:
        return json.loads(blob) if blob else {}
    except Exception:
        return {}

def decayed_usage(db_path, cfg, now=None):

    now = now or time.time()
    out = {}
    try:
        cx = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=0.5)
    except sqlite3.OperationalError:
        return out
    try:
        rows = cx.execute(
            "select principal, client_tag, profile, started_at, finished_at from jobs "
            "where finished_at is not null and started_at is not null and finished_at > ?",
            (now - cfg["window_s"],)).fetchall()
    except sqlite3.Error:
        cx.close()
        return out
    for pr, tag, prof, st, fin in rows:
        w = 0.5 ** ((now - fin) / cfg["halflife_s"])
        out[account_of(pr, tag)] = out.get(account_of(pr, tag), 0.0) + w * debt(_profile(prof), fin - st, cfg)
    try:
        arows = cx.execute(
            "select principal, client_tag, stich_ts, last_abgezinst from fairshare_aggregat "
            "where stich_ts > ?", (now - cfg["window_s"],)).fetchall()
    except sqlite3.Error:
        arows = []
    for pr, tag, stich, wert in arows:
        a = account_of(pr, tag)
        out[a] = out.get(a, 0.0) + wert * 0.5 ** ((now - stich) / cfg["halflife_s"])
    cx.close()
    return out

def pending_work(db_path, cfg, now=None):

    now = now or time.time()
    out, rows_by_acct = {}, {}
    try:
        cx = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=0.5)
        rows = cx.execute("select principal, client_tag, profile, state from jobs "
                          "where state in ('queued','running')").fetchall()
        cx.close()
    except sqlite3.Error:
        return out, rows_by_acct
    for pr, tag, prof, state in rows:
        a = account_of(pr, tag)
        out[a] = out.get(a, 0.0) + reserve(_profile(prof), cfg)
        rows_by_acct[a] = rows_by_acct.get(a, 0) + 1
    return out, rows_by_acct

def max_min_allocation(demands, weights, budget, floor=0.0):

    accounts = list(demands)
    alloc = dict((a, 0.0) for a in accounts)
    remaining = float(budget)
    unsatisfied = set(accounts)
    while unsatisfied and remaining > 1e-9:
        wsum = sum(weights.get(a, 1.0) for a in unsatisfied)
        if wsum <= 0:
            break
        progressed = False
        for a in sorted(unsatisfied):
            share = remaining * weights.get(a, 1.0) / wsum
            want = demands[a] - alloc[a]
            give = min(share, want)
            if give > 1e-9:
                alloc[a] += give
                progressed = True
        remaining = budget - sum(alloc.values())
        newly = {a for a in unsatisfied if alloc[a] >= demands[a] - 1e-9}
        if not newly and not progressed:
            break
        unsatisfied -= newly
    for a in accounts:
        alloc[a] = max(alloc[a], min(floor, demands[a]))
    return alloc

def allowance(account, active_accounts, weights, cfg):

    if not active_accounts:
        return cfg["backlog_budget"]
    peers = set(active_accounts) | {account}
    wsum = sum(weights.get(a, 1.0) for a in peers)
    return max(cfg["floor_s"], cfg["backlog_budget"] * weights.get(account, 1.0) / wsum)

def fair_factor(u, s):

    if s <= 0:
        return 0.0
    return 2.0 ** (-(u / s))

def priority_delta(f, wait_s, cfg):

    age = min(1.0, float(wait_s or 0) / cfg["age_max_s"])
    return -int(round(cfg["fairshare_weight"] * f + cfg["age_weight"] * age))

def shape_report(db_path, cfg, now=None, window_s=None, min_jobs=50):

    now = now or time.time()
    win = window_s or cfg["window_s"]
    try:
        cx = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=0.5)
        rows = cx.execute(
            "select principal, client_tag, profile, started_at, finished_at from jobs "
            "where finished_at is not null and started_at is not null and finished_at > ?",
            (now - win,)).fetchall()
        cx.close()
    except sqlite3.Error:
        return {}
    agg = {}
    for pr, tag, prof, st, fin in rows:
        a = account_of(pr, tag)
        p = _profile(prof)
        d = agg.setdefault(a, {"runs": [], "declared": 0.0, "n": 0, "share": dominant_share(p, cfg)})
        d["runs"].append(fin - st)
        d["declared"] += float(p.get("timeout_s") or 0)
        d["n"] += 1
    out = {}
    for a, d in agg.items():
        if d["n"] < min_jobs:
            continue
        runs = sorted(d["runs"])
        med = runs[len(runs) // 2]
        total_run = sum(runs)
        overhead = d["n"] * cfg["overhead_s"]
        out[a] = {
            "jobs": d["n"],
            "median_runtime_s": round(med, 2),
            "declared_walltime_s": round(d["declared"] / d["n"]),
            "walltime_accuracy": round(med / max(1.0, d["declared"] / d["n"]), 4),
            "overhead_fraction": round(min(1.0, overhead / max(1e-6, total_run)), 3),
            "machine_hours_spent": round(d["share"] * total_run / 3600.0, 2),
            "machine_hours_overhead": round(d["share"] * overhead / 3600.0, 2),
            "advice": _advice(med, d["declared"] / d["n"], overhead, total_run),
        }
    return out

def _advice(median_s, declared_s, overhead_s, total_run_s):
    tips = []
    if median_s < 60:
        tips.append("Median-Laufzeit %.1fs: buendle die Aufgaben zu einem Job (ein Job, viele Tasks) "
                    "— HPC-Faustregel ist >=10 Minuten pro Job." % median_s)
    if declared_s and median_s / declared_s < 0.05:
        tips.append("Deklarierte Wallzeit %ds, tatsaechlich %.1fs (%.1f%%): setz --timeout realistisch, "
                    "sonst frisst die Reservierung dein Backlog-Budget und der Job wird nie gebackfillt."
                    % (declared_s, median_s, 100.0 * median_s / declared_s))
    if total_run_s > 0 and overhead_s / total_run_s > 0.25:
        tips.append("%.0f%% der Maschinenzeit dieser Familie ist reine Job-Verwaltung."
                    % (100.0 * overhead_s / total_run_s))
    return tips

BAN_LADDER_S = (3600.0, 7200.0, 14400.0, 86400.0, 604800.0)

def _ban_path(cfg=None):
    home = os.environ.get("PN_DATA_DIR") or os.path.expanduser("~/.local/share/portioneer")
    return os.path.join(home, "floodban.json")

def _ban_load():
    try:
        with open(_ban_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _ban_save(state):
    p = _ban_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1)
        os.replace(tmp, p)
    except OSError:
        pass

def ban_check(account, now=None):

    now = now or time.time()
    st = _ban_load().get(account) or {}
    until = float(st.get("banned_until") or 0.0)
    return (until if until > now else 0.0), int(st.get("level") or 0)

def ban_strike(account, now=None, window_s=600.0, strikes_to_ban=10):

    now = now or time.time()
    state = _ban_load()
    st = state.setdefault(account, {"strikes": [], "level": 0, "banned_until": 0.0})
    if float(st.get("banned_until") or 0) > now:
        return float(st["banned_until"]), int(st["level"])
    hits = [t for t in (st.get("strikes") or []) if now - float(t) <= window_s]
    hits.append(now)
    st["strikes"] = hits
    if len(hits) >= strikes_to_ban:
        lvl = int(st.get("level") or 0)
        st["banned_until"] = now + BAN_LADDER_S[min(lvl, len(BAN_LADDER_S) - 1)]
        st["level"] = lvl + 1
        st["strikes"] = []
        st["last_ban"] = now
        _ban_save(state)
        return float(st["banned_until"]), int(st["level"])
    _ban_save(state)
    return 0.0, int(st.get("level") or 0)

def ban_forgive(account, now=None, clean_s=7 * DAY):

    now = now or time.time()
    state = _ban_load()
    st = state.get(account)
    if not st or not st.get("level"):
        return
    if float(st.get("banned_until") or 0) > now:
        return
    if now - float(st.get("last_ban") or 0) >= clean_s:
        st["level"] = max(0, int(st["level"]) - 1)
        st["last_ban"] = now
        _ban_save(state)

def ban_lift(account):

    state = _ban_load()
    if account in state:
        state[account] = {"strikes": [], "level": 0, "banned_until": 0.0}
        _ban_save(state)
        return True
    return False

def human_duration(seconds):
    s = int(max(0, seconds))
    if s >= 86400:
        return "%dd %dh" % (s // 86400, (s % 86400) // 3600)
    if s >= 3600:
        return "%dh %dm" % (s // 3600, (s % 3600) // 60)
    return "%dm" % max(1, s // 60)

