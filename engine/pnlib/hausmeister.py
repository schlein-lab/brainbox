
from __future__ import annotations
import json
import os
import sqlite3
import time

from pnlib import fairshare

TERMINAL = ("done", "failed", "cancelled", "timeout", "rejected")

EVENT_TTL_D = 2.0
JOB_TTL_D = 2.0

JOB_TTL_MIN_D = 0.5

BATCH = 5000
VACUUM_PAGES = 2000
PAUSE_S = 0.3

def _envf(name, dflt):
    try:
        return float(os.environ.get(name, "") or dflt)
    except (TypeError, ValueError):
        return dflt

def cfg():

    batch = int(_envf("PN_HAUSMEISTER_BATCH", BATCH))
    return {
        "event_ttl_s": max(0.0, _envf("PN_EVENT_TTL_D", EVENT_TTL_D)) * 86400.0,
        "job_ttl_s": max(JOB_TTL_MIN_D, _envf("PN_JOB_TTL_D", JOB_TTL_D)) * 86400.0,
        "batch": max(1, min(batch, 5000)),
        "vacuum_pages": max(0, int(_envf("PN_HAUSMEISTER_VACUUM_PAGES", VACUUM_PAGES))),
        "pause_s": max(0.0, _envf("PN_HAUSMEISTER_PAUSE_S", PAUSE_S)),
    }

def archiv_pfad(db_path):

    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "queue-archiv.db")

def _cols(c, table, schema="main"):
    return [r[1] for r in c.execute("PRAGMA %s.table_info(%s)" % (schema, table)).fetchall()]

def _ensure_archiv(c):

    c.execute("CREATE TABLE IF NOT EXISTS a.job_events ("
              "id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, ts REAL NOT NULL, "
              "kind TEXT NOT NULL, data TEXT, topic TEXT, ausgelagert_utc TEXT)")
    c.execute("CREATE INDEX IF NOT EXISTS a.idx_arch_job ON job_events(job_id)")
    live = _cols(c, "jobs")
    if not c.execute("SELECT 1 FROM a.sqlite_master WHERE type='table' AND name='jobs'").fetchone():
        decl = ", ".join(("id INTEGER PRIMARY KEY" if n == "id" else n) for n in live)
        c.execute("CREATE TABLE a.jobs (%s, ausgelagert_utc TEXT)" % decl)
    else:
        have = set(_cols(c, "jobs", "a"))
        for n in live:
            if n not in have:
                c.execute("ALTER TABLE a.jobs ADD COLUMN %s" % n)
    c.execute("CREATE INDEX IF NOT EXISTS a.idx_arch_jobs_state ON jobs(state)")

def _stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

FS_SPALTEN = ("principal", "client_tag", "profile", "started_at", "finished_at")

def fs_umgebung():

    cores = mem = None
    try:
        from pnlib import meters
        snap = meters.snapshot()
        cores, mem = snap.get("cpu_count"), snap.get("mem_total")
    except Exception:
        pass
    if not cores:
        cores = os.cpu_count() or 4
    if not mem:
        mem = 4096
        try:
            with open("/proc/meminfo", encoding="ascii") as f:
                for zeile in f:
                    if zeile.startswith("MemTotal:"):
                        mem = int(zeile.split()[1]) // 1024
                        break
        except (OSError, ValueError, IndexError):
            pass
    return fairshare.config(cores, mem)

def _ensure_aggregat(c):
    c.execute("CREATE TABLE IF NOT EXISTS fairshare_aggregat ("
              "principal TEXT NOT NULL, client_tag TEXT NOT NULL, tag INTEGER NOT NULL, "
              "stich_ts REAL NOT NULL, jobs INTEGER NOT NULL DEFAULT 0, "
              "last_abgezinst REAL NOT NULL DEFAULT 0, halbwert_s REAL, "
              "PRIMARY KEY (principal, client_tag, tag))")

def _fairshare_einfrieren(c, ids, fs_cfg):

    m = ",".join("?" * len(ids))
    H = fs_cfg["halflife_s"]
    akku = {}
    for pr, tg, prof, st, fin in c.execute(
            "SELECT principal, client_tag, profile, started_at, finished_at FROM jobs "
            "WHERE id IN (%s) AND started_at IS NOT NULL AND finished_at IS NOT NULL" % m, ids):
        tag = int(fin // 86400.0)
        stich = (tag + 1) * 86400.0
        schuld = fairshare.debt(fairshare._profile(prof), fin - st, fs_cfg)

        k = (pr or "?", tg or "default", tag)
        e = akku.setdefault(k, [0, 0.0])
        e[0] += 1
        e[1] += schuld * 0.5 ** ((stich - fin) / H)
    for (pr, tg, tag), (n, wert) in akku.items():
        c.execute("INSERT INTO fairshare_aggregat "
                  "(principal, client_tag, tag, stich_ts, jobs, last_abgezinst, halbwert_s) "
                  "VALUES (?,?,?,?,?,?,?) "
                  "ON CONFLICT(principal, client_tag, tag) DO UPDATE SET "
                  "jobs = jobs + excluded.jobs, "
                  "last_abgezinst = last_abgezinst + excluded.last_abgezinst, "
                  "halbwert_s = excluded.halbwert_s",
                  (pr, tg, tag, (tag + 1) * 86400.0, n, wert, H))

def _weiter_ok(weiter):

    if weiter is None:
        return True
    try:
        return weiter() is not False
    except Exception:
        return True

def events_auslagern(c, grenze_ts, batch, weiter=None, pause_s=0.0):

    marker = ",".join("?" * len(TERMINAL))
    wo = ("FROM job_events e WHERE e.ts < ? AND e.job_id IN "
          "(SELECT id FROM jobs WHERE state IN (%s))" % marker)
    args = [grenze_ts] + list(TERMINAL)
    ecols = [s for s in ("id", "job_id", "ts", "kind", "data", "topic")
             if s in _cols(c, "job_events")]
    eph = ",".join(ecols)
    bewegt = 0
    while True:
        if not _weiter_ok(weiter):
            break
        ids = [r[0] for r in c.execute("SELECT e.id " + wo + " LIMIT ?",
                                       args + [batch]).fetchall()]
        if not ids:
            break
        m = ",".join("?" * len(ids))
        c.execute("BEGIN IMMEDIATE")
        c.execute("INSERT OR REPLACE INTO a.job_events (%s, ausgelagert_utc) "
                  "SELECT %s, ? FROM job_events WHERE id IN (%s)" % (eph, eph, m),
                  [_stamp()] + ids)
        c.execute("DELETE FROM job_events WHERE id IN (%s)" % m, ids)
        c.commit()
        bewegt += len(ids)
        if pause_s:
            time.sleep(pause_s)
    return bewegt

def _geschuetzte_ids(c):

    marker = ",".join("?" * len(TERMINAL))
    cols = _cols(c, "jobs")
    geschuetzt = set()
    if "deps" in cols:
        for (raw,) in c.execute(
                "SELECT deps FROM jobs WHERE state NOT IN (%s) AND deps IS NOT NULL" % marker,
                list(TERMINAL)).fetchall():
            try:
                v = json.loads(raw)
                if isinstance(v, list):
                    geschuetzt.update(int(x) for x in v)
            except (ValueError, TypeError):
                continue
    if "parent_job" in cols:
        for (p,) in c.execute(
                "SELECT DISTINCT parent_job FROM jobs WHERE state NOT IN (%s) "
                "AND parent_job IS NOT NULL" % marker, list(TERMINAL)).fetchall():
            geschuetzt.add(int(p))
    return geschuetzt

def jobs_auslagern(c, grenze_ts, batch, weiter=None, pause_s=0.0, fs_cfg=None):

    marker = ",".join("?" * len(TERMINAL))
    cols = _cols(c, "jobs")
    mit_fs = set(FS_SPALTEN) <= set(cols)
    if mit_fs:
        fs_cfg = fs_cfg or fs_umgebung()
        _ensure_aggregat(c)
    wo = ["state IN (%s)" % marker, "COALESCE(finished_at, submitted_at) < ?"]
    args = list(TERMINAL) + [grenze_ts]
    if "workspace_path" in cols:
        wo.append("workspace_path IS NULL")
    if "group_id" in cols:
        wo.append("(group_id IS NULL OR group_id NOT IN "
                  "(SELECT DISTINCT group_id FROM jobs WHERE state NOT IN (%s) "
                  "AND group_id IS NOT NULL))" % marker)
        args += list(TERMINAL)
    sql = "SELECT id FROM jobs WHERE " + " AND ".join(wo) + " ORDER BY id LIMIT ?"
    jph = ",".join(cols)
    ecols = [s for s in ("id", "job_id", "ts", "kind", "data", "topic")
             if s in _cols(c, "job_events")]
    eph = ",".join(ecols)
    jobs_bewegt = events_bewegt = 0

    jbatch = max(1, min(batch, 1000))
    while True:
        if not _weiter_ok(weiter):
            break
        geschuetzt = _geschuetzte_ids(c)
        ids = [r[0] for r in c.execute(sql, args + [jbatch]).fetchall()
               if r[0] not in geschuetzt]
        if not ids:
            break
        m = ",".join("?" * len(ids))
        st = _stamp()
        c.execute("BEGIN IMMEDIATE")
        c.execute("INSERT OR REPLACE INTO a.jobs (%s, ausgelagert_utc) "
                  "SELECT %s, ? FROM jobs WHERE id IN (%s)" % (jph, jph, m), [st] + ids)
        c.execute("INSERT OR REPLACE INTO a.job_events (%s, ausgelagert_utc) "
                  "SELECT %s, ? FROM job_events WHERE job_id IN (%s)" % (eph, eph, m),
                  [st] + ids)
        if mit_fs:
            _fairshare_einfrieren(c, ids, fs_cfg)
        c.execute("DELETE FROM job_events WHERE job_id IN (%s)" % m, ids)
        n_ev = c.execute("SELECT changes()").fetchone()[0]
        c.execute("DELETE FROM jobs WHERE id IN (%s)" % m, ids)
        c.commit()
        jobs_bewegt += len(ids)
        events_bewegt += n_ev
        if pause_s:
            time.sleep(pause_s)
    return jobs_bewegt, events_bewegt

def platz_zurueckgeben(c, pages):

    av = c.execute("PRAGMA auto_vacuum").fetchone()[0]
    if av == 2:
        if pages:
            c.execute("PRAGMA incremental_vacuum(%d)" % int(pages))
            c.commit()
        return "incremental"
    if av == 1:
        return "full"
    return ("offline-vacuum-noetig: auto_vacuum=NONE — einmalig im Wartungsfenster "
            "(pnd angehalten): PRAGMA auto_vacuum=INCREMENTAL; VACUUM;")

def lauf(db_path, weiter=None, drucken=None, fs_cfg=None):

    say = drucken or (lambda *a: None)
    k = cfg()
    now = time.time()
    c = sqlite3.connect(db_path, timeout=30)
    try:
        c.execute("PRAGMA busy_timeout=15000")
        c.execute("ATTACH DATABASE ? AS a", (archiv_pfad(db_path),))
        _ensure_archiv(c)
        ev = events_auslagern(c, now - k["event_ttl_s"], k["batch"],
                              weiter=weiter, pause_s=k["pause_s"])
        jb, jev = jobs_auslagern(c, now - k["job_ttl_s"], k["batch"],
                                 weiter=weiter, pause_s=k["pause_s"], fs_cfg=fs_cfg)

        agg_n = agg_j = 0
        try:
            fenster = (fs_cfg or fairshare.config(1, 1))["window_s"]
            c.execute("DELETE FROM fairshare_aggregat WHERE stich_ts <= ?", (now - fenster,))
            c.commit()
            agg_n, agg_j = c.execute(
                "SELECT COUNT(*), COALESCE(SUM(jobs),0) FROM fairshare_aggregat").fetchone()
        except sqlite3.Error:
            pass
        vac = platz_zurueckgeben(c, k["vacuum_pages"])
        rest_j = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        rest_e = c.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]
        say("hausmeister: %d Ereignisse + %d Jobs (mit %d Ereignissen) archiviert; "
            "Betrieb hält %d Jobs / %d Ereignisse; fairshare-Aggregat %d Zeilen (%d Jobs); "
            "Platz: %s" % (ev, jb, jev, rest_j, rest_e, agg_n, agg_j, vac))
        return {"events": ev, "jobs": jb, "job_events": jev, "vacuum": vac,
                "rest_jobs": rest_j, "rest_events": rest_e,
                "aggregat_zeilen": agg_n, "aggregat_jobs": agg_j}
    finally:
        c.close()
