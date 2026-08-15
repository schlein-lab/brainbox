

import json
import os
import threading
import time

DATA_DIR = None
_prov_log = None

_SCHED_NAME = "backup-schedule.json"
_STATE_NAME = "backup-laststate.json"
_LOCK = threading.Lock()
_DEFAULT_SCHED = {"enabled": True, "hour": 3, "minute": 30, "keep": 14, "include_shares": False}

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

def _p(name):
    return os.path.join(DATA_DIR, name)

def _load(name, dflt):
    try:
        with open(_p(name)) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else dict(dflt)
    except (OSError, ValueError):
        return dict(dflt)

def _save(name, d):
    tmp = "%s.tmp.%d" % (_p(name), os.getpid())
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, _p(name))

def _sched():
    s = _load(_SCHED_NAME, _DEFAULT_SCHED)
    for k, v in _DEFAULT_SCHED.items():
        s.setdefault(k, v)
    return s

def _run_backup(label, include_shares=False, keep=14, include_llm=False):

    import pn_backup
    jid = None
    try:
        import portal_jobs_persist as _jp
        jid = _jp.job_create("Zustands-Backup (%s)" % (label or "manuell"), None, None,
                             mode="backup", principal="owner")
        _jp.job_log(jid, "[backup] starte konsistentes Zustands-Bundle …")
    except Exception:
        _jp = None
    res = pn_backup.make_bundle(label=label, include_shares=include_shares, include_llm=include_llm)
    try:
        if res.get("ok"):
            pn_backup.prune(keep=keep)
    except Exception:
        pass
    try:
        _save(_STATE_NAME, {"ts": int(time.time()), "result": res, "label": label})
    except Exception:
        pass
    try:
        if _jp and jid:
            if res.get("ok"):
                mb = (res.get("bytes") or 0) / 1e6
                _jp.job_log(jid, "[backup] fertig: %s (%.1f MB, %d Dateien, DBs konsistent)"
                            % (os.path.basename(res.get("path", "")), mb, res.get("n_files", 0)))
                _jp.job_update(jid, status="done")
            else:
                _jp.job_log(jid, "[backup] FEHLER: %s" % res.get("error"))
                _jp.job_update(jid, status="error")
    except Exception:
        pass
    if callable(_prov_log):
        try:
            _prov_log("backup.run", "owner",
                      json.dumps({"ok": res.get("ok"), "label": label, "jid": jid})[:300],
                      {"wire": "system"})
        except Exception:
            pass
    return res, jid

_worker_started = False

def backup_worker_start():
    global _worker_started
    if _worker_started:
        return
    _worker_started = True

    def _loop():
        last_fire_day = None
        while True:
            try:
                s = _sched()
                if s.get("enabled"):
                    now = time.localtime()
                    day = (now.tm_year, now.tm_yday)
                    if (now.tm_hour == int(s.get("hour", 3))
                            and now.tm_min == int(s.get("minute", 30))
                            and day != last_fire_day):
                        last_fire_day = day
                        _run_backup("naechtlich", bool(s.get("include_shares")),
                                    int(s.get("keep", 14)))
            except Exception:
                pass
            time.sleep(30)

    threading.Thread(target=_loop, name="pn-backup-worker", daemon=True).start()

class BackupRoutes:
    def _bak_json(self, obj, code=200):
        return self.send_html(json.dumps(obj, ensure_ascii=False), code,
                              [("Content-Type", "application/json")])

    def _api_backup_get(self):

        if not self._is_admin():
            return self._bak_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        import pn_backup
        return self._bak_json({"ok": True, "bundles": pn_backup.list_bundles(),
                               "schedule": _sched(), "last": _load(_STATE_NAME, {}),
                               "dest": pn_backup.DEST_DEFAULT})

    def _api_backup_now(self, raw):

        if not self._is_admin():
            return self._bak_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        label = str(body.get("label") or "manuell")[:80]
        shares = bool(body.get("shares"))
        llm = bool(body.get("llm"))

        threading.Thread(target=lambda: _run_backup(label, shares, int(_sched().get("keep", 14)), llm),
                         name="pn-backup-now", daemon=True).start()
        return self._bak_json({"ok": True, "started": True,
                               "note": "Backup laeuft; Stand unter GET /api/backup (last)."})

    def _api_backup_schedule(self, raw):

        if not self._is_admin():
            return self._bak_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        s = _sched()
        if "enabled" in body:
            s["enabled"] = bool(body["enabled"])
        for k, lo, hi in (("hour", 0, 23), ("minute", 0, 59), ("keep", 1, 365)):
            if k in body:
                try:
                    s[k] = max(lo, min(hi, int(body[k])))
                except (TypeError, ValueError):
                    pass
        if "include_shares" in body:
            s["include_shares"] = bool(body["include_shares"])
        with _LOCK:
            _save(_SCHED_NAME, s)
        if callable(_prov_log):
            try:
                _prov_log("backup.schedule", "owner", json.dumps(s)[:200], {"wire": "http"})
            except Exception:
                pass
        return self._bak_json({"ok": True, "schedule": s})

    def _api_backup_restore(self, raw):

        if not self._is_admin():
            return self._bak_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        import pn_backup
        name = str(body.get("name") or body.get("path") or "")
        if not name:
            return self._bak_json({"ok": False, "error": "name/path fehlt"}, 400)
        path = name if os.path.isabs(name) else os.path.join(pn_backup.DEST_DEFAULT,
                                                              os.path.basename(name))
        if not os.path.exists(path):
            return self._bak_json({"ok": False, "error": "Bundle nicht gefunden: %s" % path}, 404)
        apply = bool(body.get("apply"))
        if apply:

            ok2, reason = self._verify_winthin_totp(str(body.get("totp") or "").strip())
            if not ok2:
                msg = ("Kein 2FA/Handy-Code eingerichtet — unter Freigaben/Relay koppeln."
                       if reason == "kein-2fa" else "2FA-Code ungueltig — Restore NICHT ausgefuehrt.")
                return self._bak_json({"ok": False, "need_2fa": True, "error": msg}, 403)
        res = pn_backup.restore_bundle(path, apply=apply)
        if callable(_prov_log) and apply:
            try:
                _prov_log("backup.restore", "owner",
                          json.dumps({"path": path, "ok": res.get("ok")})[:200], {"wire": "http"})
            except Exception:
                pass
        return self._bak_json(res, 200 if res.get("ok") else 400)
