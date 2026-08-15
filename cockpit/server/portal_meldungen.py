
import json
import os
import threading
import time

DATA_DIR = os.environ.get("PN_PORTAL_DATA",
                          os.path.expanduser("~/.local/share/brainbox-portal"))
MELDUNGEN_SID = "meldungen"
CURSOR_PATH = os.path.join(DATA_DIR, "meldungen-cursor.json")

_ALARM_ZUSTAENDE = ("walltime-warn", "approval-request", "oom-retry")
_WISSENS_ZUSTAENDE = ("timeout", "error", "failed", "dead-lettered", "oom")

def _postfach_dir():

    env = os.environ.get("PN_NOTIFY_POSTFACH")
    if env:
        return env
    try:
        import sys as _s
        for _p in ("/home/brainbox/portioneer", os.path.expanduser("~/portioneer")):
            if _p not in _s.path and os.path.isdir(_p):
                _s.path.append(_p)
        from pnlib import notifycfg as _ncfg
        return _ncfg.POSTFACH_DIR
    except Exception:
        return os.path.expanduser("~/.local/share/portioneer/notify/postfach")

def _chan_ctx():
    try:
        import portal_jobs_persist as _pjp
        return _pjp._chan_ctx()
    except Exception:
        return {"dir": DATA_DIR}

def _cursor_lesen():
    try:
        with open(CURSOR_PATH) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _cursor_schreiben(d):
    try:
        tmp = CURSOR_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CURSOR_PATH)
    except OSError:
        pass

def _einordnen(rec):

    text = str(rec.get("text") or "").strip()
    if not text:
        return None
    meta = rec.get("meta") or {}
    sid = str(meta.get("sid") or "").strip() or MELDUNGEN_SID
    zustand = str(rec.get("state") or "")
    if zustand in _ALARM_ZUSTAENDE:
        return sid, "alert", text
    if zustand in _WISSENS_ZUSTAENDE:
        return sid, "normal", text
    return None

def _empfaenger_im_portal(postfach_principal):

    if postfach_principal != "admin":
        return [postfach_principal]
    try:
        import portal_users
        ziele = [u.get("uid") for u in portal_users.user_list()
                 if u.get("role") in ("owner", "admin") and u.get("status") != "deleted"]
        ziele = [z for z in ziele if z]
        if ziele:
            return ziele
    except Exception:
        pass
    return ["owner", "admin"]

def _runde(cur, erste_runde=False):

    d = _postfach_dir()
    try:
        namen = sorted(n for n in os.listdir(d)
                       if n.endswith(".jsonl") and ".probelauf-" not in n)
    except OSError:
        return 0
    import portal_channels
    zugestellt = 0
    geaendert = False
    for name in namen:
        principal = name[:-len(".jsonl")]
        pfad = os.path.join(d, name)
        try:
            st = os.stat(pfad)
        except OSError:
            continue
        merk = cur.get(principal) or {}
        off, ino = int(merk.get("off") or 0), merk.get("ino")
        if principal not in cur:
            if erste_runde:

                cur[principal] = {"off": st.st_size, "ino": st.st_ino}
                geaendert = True
                continue

            merk = {"off": 0, "ino": st.st_ino}
            off, ino = 0, st.st_ino
        if ino != st.st_ino or st.st_size < off:
            off = 0
        if st.st_size <= off:
            continue
        try:
            with open(pfad, "rb") as f:
                f.seek(off)
                rest = f.read()
        except OSError:
            continue

        ende = rest.rfind(b"\n")
        if ende < 0:
            continue
        block, off_neu = rest[:ende + 1], off + ende + 1
        for zeile in block.splitlines():
            try:
                rec = json.loads(zeile.decode("utf-8"))
            except Exception:
                continue
            aus = _einordnen(rec)
            if not aus:
                continue
            sid, notify, text = aus
            for ziel in _empfaenger_im_portal(principal):
                try:
                    portal_channels.bus_append(_chan_ctx(), ziel, sid, "message",
                                               role="system", text=text, notify=notify,
                                               quelle="meldeweg", job_id=rec.get("job_id"))
                    zugestellt += 1
                except Exception:
                    pass
                if notify == "alert":

                    try:
                        import pn_webpush
                        pn_webpush.push_melden(ziel, {
                            "title": "Brainbox: Freigabe/Alarm",
                            "body": text[:160],
                            "url": "/?view=approvals",
                            "tag": "meldeweg-%s" % (rec.get("job_id") or sid),
                        })
                    except Exception:
                        pass
        cur[principal] = {"off": off_neu, "ino": st.st_ino}
        geaendert = True
    if geaendert:
        _cursor_schreiben(cur)
    return zugestellt

def meldungen_lesen(principal, limit=200):

    zeilen = []
    quellen = [principal]

    try:
        import portal_users
        u = portal_users.user_get(principal)
        if u and u.get("role") in ("owner", "admin") and "admin" not in quellen:
            quellen.append("admin")
    except Exception:
        if principal == "owner":
            quellen.append("admin")
    for q in quellen:
        try:
            with open(os.path.join(_postfach_dir(), "%s.jsonl" % q), encoding="utf-8") as f:
                zeilen.extend(f.read().splitlines())
        except OSError:
            pass
    turns = []
    for i, z in enumerate(zeilen[-limit:]):
        try:
            rec = json.loads(z)
        except Exception:
            continue
        text = str(rec.get("text") or "").strip()
        if not text:
            continue
        wann = rec.get("ereignis_ts") or rec.get("ts") or 0
        turns.append({"i": i, "role": "assistant", "text": text, "ts": wann})
    return turns

_GESTARTET = False

def meldungen_worker_start():

    global _GESTARTET
    if _GESTARTET:
        return
    _GESTARTET = True

    def _lauf():
        cur = _cursor_lesen()
        erste = True
        while True:
            try:
                _runde(cur, erste_runde=erste)
                erste = False
            except Exception:
                pass
            time.sleep(10.0)

    threading.Thread(target=_lauf, daemon=True, name="meldungen-folger").start()
