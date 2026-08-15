#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import threading
import time

session_exists = None

def _truthy(v):
    return str(v or "").strip().lower() in ("1", "true", "yes", "on", "enterprise")

def _cfg():

    ent = _truthy(os.environ.get("PN_MEDIA_ENTERPRISE"))
    grace_default = 300.0 if ent else 3600.0
    ttl_default = 7.0 if ent else 30.0

    def _num(name, default):
        try:
            return float(os.environ.get(name) or default)
        except (TypeError, ValueError):
            return float(default)

    return {
        "enterprise": ent,
        "orphan_grace_s": _num("PN_MEDIA_ORPHAN_GRACE_S", grace_default),
        "ttl_days": _num("PN_MEDIA_TTL_DAYS", ttl_default),
        "delete": _truthy(os.environ.get("PN_MEDIA_JANITOR_DELETE")),
    }

def _manager():
    import pn_mediashare
    os.environ.setdefault("PN_MEDIASHARE_NO_DAEMONS", os.environ.get("PN_MEDIASHARE_NO_DAEMONS", ""))
    return pn_mediashare.ShareManager()

def _data_dir_of(mgr):

    return os.path.dirname(mgr.registry) or "."

def _live_session_index(data_dir):

    live = set()
    read_any = False
    usersdir = os.path.join(data_dir, "users")
    try:
        entries = os.listdir(usersdir)
    except OSError:
        return live, False
    for name in entries:
        sj = os.path.join(usersdir, name, "sessions.json")
        try:
            with open(sj) as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        read_any = True
        if isinstance(data, list):
            for s in data:
                if isinstance(s, dict) and s.get("state") != "deleted" and s.get("id"):
                    live.add(str(s.get("id")))
    return live, read_any

def _resolve_exists(sid, rec, idx, have_index):

    if callable(session_exists):
        try:
            r = session_exists(sid, rec.get("owner_uid"))
            if r is not None:
                return bool(r)
        except Exception:
            pass
    if not have_index:
        return None
    return str(sid) in idx

def _row(rec, sid, now, reason, **extra):
    row = {
        "sid": sid,
        "title": rec.get("title"),
        "sso": bool(rec.get("sso")),
        "owner_uid": rec.get("owner_uid"),
        "path": rec.get("path"),
        "age_days": round((now - float(rec.get("created") or now)) / 86400.0, 2),
        "reason": reason,
    }
    row.update(extra)
    return row

def classify(mgr, now=None, cfg=None):

    now = time.time() if now is None else float(now)
    cfg = cfg or _cfg()
    reg = mgr._load()
    recs = {k: v for k, v in reg.items() if k != "_users" and isinstance(v, dict)}
    idx, have_index = _live_session_index(_data_dir_of(mgr))
    report = {
        "scanned": len(recs),
        "session_index_size": len(idx),
        "index_trusted": have_index,
        "enterprise": cfg["enterprise"],
        "ttl_days": cfg["ttl_days"],
        "orphan_grace_s": cfg["orphan_grace_s"],
        "orphans": [],
        "broken": [],
        "stale": [],
        "archived_already": [],
    }
    for sid, rec in recs.items():
        if rec.get("archived"):
            report["archived_already"].append(sid)
            continue
        path = rec.get("path")

        if not path or not os.path.isdir(path):
            exists = _resolve_exists(sid, rec, idx, have_index)
            row = _row(rec, sid, now, "no-path-field" if not path else "missing-dir",
                       session_gone=(exists is False))
            report["broken"].append(row)
            continue
        age = now - float(rec.get("created") or now)
        exists = _resolve_exists(sid, rec, idx, have_index)

        if exists is False and age >= cfg["orphan_grace_s"]:
            report["orphans"].append(_row(rec, sid, now, "session-gone"))
            continue

        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            mtime = float(rec.get("created") or now)
        idle_days = (now - mtime) / 86400.0
        if idle_days >= cfg["ttl_days"]:
            report["stale"].append(_row(rec, sid, now, "idle", idle_days=round(idle_days, 1)))
    return report

_GEWARNT = set()

def _warnung_einmal(text):

    if text in _GEWARNT:
        return
    _GEWARNT.add(text)
    try:
        import sys as _s
        _s.stderr.write("[media-janitor] %s\n" % text)
    except Exception:
        pass

def sweep(mgr=None, dry_run=True, mode=None, now=None, cfg=None):

    if mgr is None:
        mgr = _manager()
    cfg = cfg or _cfg()
    if cfg.get("delete") or mode == "delete":

        _warnung_einmal("PN_MEDIA_JANITOR_DELETE ist gesetzt, wird aber IGNORIERT: der Kehrer "
                        "loescht seit 01.08.2026 nichts mehr. Loeschen braucht einen Menschen mit "
                        "2FA. Es wird stattdessen archiviert (umkehrbar).")
    mode = "archive"
    rep = classify(mgr, now=now, cfg=cfg)
    rep["dry_run"] = bool(dry_run)
    rep["mode"] = mode
    rep["archived"] = []
    rep["removed"] = []
    rep["errors"] = []
    if dry_run:
        return rep
    for row in rep["orphans"]:
        sid = row["sid"]
        try:

            if mgr.archive_share(sid):
                rep["archived"].append(sid)
        except Exception as e:
            rep["errors"].append({"sid": sid, "err": str(e)})

    return rep

def _log_report(rep, data_dir=None):

    try:
        dd = data_dir or os.path.join(os.path.expanduser("~"), ".local", "share", "brainbox-portal")
        line = {
            "ts": time.time(),
            "dry_run": rep.get("dry_run"),
            "mode": rep.get("mode"),
            "scanned": rep.get("scanned"),
            "orphans": len(rep.get("orphans", [])),
            "broken": len(rep.get("broken", [])),
            "stale": len(rep.get("stale", [])),
            "archived": rep.get("archived", []),
            "removed": rep.get("removed", []),
            "index_trusted": rep.get("index_trusted"),
        }
        with open(os.path.join(dd, "media-janitor.log"), "a") as f:
            f.write(json.dumps(line) + "\n")
    except Exception:
        pass

_started = False
_start_lock = threading.Lock()

def janitor_start(mgr=None, interval=None, dry_run=None, mode=None):

    global _started
    with _start_lock:
        if _started:
            return False
        _started = True
    if mgr is None:
        mgr = _manager()
    if interval is None:
        try:
            interval = float(os.environ.get("PN_MEDIA_JANITOR_INTERVAL_S") or 3600.0)
        except (TypeError, ValueError):
            interval = 3600.0
    if dry_run is None:
        dry_run = _truthy(os.environ.get("PN_MEDIA_JANITOR_DRYRUN"))
    interval = max(60.0, float(interval))
    data_dir = _data_dir_of(mgr)

    def loop():
        time.sleep(min(120.0, interval))
        while True:
            try:
                rep = sweep(mgr, dry_run=dry_run, mode=mode)
                _log_report(rep, data_dir)
            except Exception:
                pass
            time.sleep(interval)

    threading.Thread(target=loop, name="pn-media-janitor", daemon=True).start()
    return True

def _selftest():
    import tempfile
    import pn_mediashare
    os.environ["PN_MEDIASHARE_NO_DAEMONS"] = "1"
    d = tempfile.mkdtemp(prefix="media-janitor-")
    root = os.path.join(d, "shares")
    reg = os.path.join(d, "mediashares.json")
    mgr = pn_mediashare.ShareManager(root=root, registry=reg)

    live = mgr.ensure_share("aliveaaaa001", title="Lebt", principal="tenant")
    orph = mgr.ensure_share("orphanbbb002", title="Verwaist", principal="tenant")
    leg = mgr.ensure_share("legacyccc003", title="Legacy-verwaist")
    for r in (live, orph, leg):
        with open(os.path.join(r["path"], "beweis.txt"), "w") as f:
            f.write("payload")

    _reg = mgr._load()
    for sid in ("orphanbbb002", "legacyccc003", "aliveaaaa001"):
        _reg[sid]["created"] = time.time() - 10 * 86400
    mgr._save(_reg)

    udir = os.path.join(d, "users", "tenant")
    os.makedirs(udir, exist_ok=True)
    with open(os.path.join(udir, "sessions.json"), "w") as f:
        json.dump([{"id": "aliveaaaa001", "state": "active"}], f)

    cfg = _cfg()
    rep = classify(mgr, cfg=cfg)
    assert rep["index_trusted"] is True, rep
    orphan_sids = {r["sid"] for r in rep["orphans"]}
    assert orphan_sids == {"orphanbbb002", "legacyccc003"}, rep["orphans"]
    assert "aliveaaaa001" not in orphan_sids, rep

    before = json.dumps(mgr._load(), sort_keys=True)
    dr = sweep(mgr, dry_run=True)
    assert dr["archived"] == [] and dr["removed"] == [], dr
    assert json.dumps(mgr._load(), sort_keys=True) == before, "dry-run mutated the registry!"
    assert os.path.isdir(orph["path"]) and os.path.isdir(leg["path"]), "dry-run moved files!"

    d2 = tempfile.mkdtemp(prefix="media-janitor-empty-")
    mgr2 = pn_mediashare.ShareManager(root=os.path.join(d2, "shares"),
                                      registry=os.path.join(d2, "mediashares.json"))
    mgr2.ensure_share("lonelyddd004", title="Ohne Store", principal="tenant")
    r2 = classify(mgr2)
    assert r2["index_trusted"] is False and r2["orphans"] == [], r2

    en = sweep(mgr, dry_run=False, mode="archive")
    assert set(en["archived"]) == {"orphanbbb002", "legacyccc003"}, en
    assert os.path.isdir(live["path"]), "live share was touched!"
    reg_after = mgr._load()
    ap = reg_after["orphanbbb002"]["archived_path"]
    assert reg_after["orphanbbb002"].get("archived") and os.path.isfile(os.path.join(ap, "beweis.txt")), ap
    assert "archiv" in ap
    lp = reg_after["legacyccc003"]["archived_path"]
    assert "_archiv" in lp and os.path.isfile(os.path.join(lp, "beweis.txt")), lp

    assert {r["sid"] for r in mgr.list()} == {"aliveaaaa001"}, mgr.list()
    assert {r["sid"] for r in mgr.list_for("tenant")} == {"aliveaaaa001"}, mgr.list_for("tenant")

    en2 = sweep(mgr, dry_run=False)
    assert en2["archived"] == [] and en2["removed"] == [], en2

    import shutil as _sh
    _sh.rmtree(live["path"], ignore_errors=True)
    with open(os.path.join(udir, "sessions.json"), "w") as f:
        json.dump([], f)
    rb = classify(mgr)
    assert any(x["sid"] == "aliveaaaa001" and x.get("session_gone") for x in rb["broken"]), rb["broken"]
    eb = sweep(mgr, dry_run=False)
    assert eb["removed"] == [], ("der Kehrer hat geloescht", eb)
    assert mgr.get("aliveaaaa001") is not None, "der Datensatz wurde entfernt"

    os.environ["PN_MEDIA_JANITOR_DELETE"] = "1"
    try:
        ed = sweep(mgr, dry_run=False)
        assert ed["removed"] == [], ("Schalter hat doch geloescht", ed)
        assert ed["mode"] == "archive", ed
        assert mgr.get("aliveaaaa001") is not None
    finally:
        os.environ.pop("PN_MEDIA_JANITOR_DELETE", None)

    em = sweep(mgr, dry_run=False, mode="delete")
    assert em["removed"] == [] and em["mode"] == "archive", em

    _sh.rmtree(d, ignore_errors=True)
    _sh.rmtree(d2, ignore_errors=True)
    print("pn_media_janitor selftest: ALL GREEN")

if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    elif "--sweep" in sys.argv:
        print(json.dumps(sweep(dry_run=True), indent=2, ensure_ascii=False))
    elif "--enforce" in sys.argv:
        print(json.dumps(sweep(dry_run=False), indent=2, ensure_ascii=False))
    else:
        print("usage: pn_media_janitor.py --sweep | --enforce | --selftest")
