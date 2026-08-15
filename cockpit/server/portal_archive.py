

from __future__ import annotations

import glob
import json
import os
import re
import threading
import time

DATA_DIR = os.environ.get("PN_PORTAL_DATA",
                          os.path.join(os.path.expanduser("~"), ".local", "share", "brainbox-portal"))
USERS_DIR = os.path.join(DATA_DIR, "users")
JOURNAL = os.path.join(DATA_DIR, "archiv-journal.jsonl")
ZELL_STORE = os.path.join(DATA_DIR, "session-cells", "session-cells.json")
LAUFDIR = os.environ.get("PN_CELL_RUNDIR", "/tmp/pn-cells")

NIE = ("__voice__", "__system__")
LEBENDE_ZUSTAENDE = ("warm", "starting", "running", "booting", "resuming")

RUHE_H = float(os.environ.get("PN_ARCHIV_RUHE_H", "2"))

VERLASSEN_TAGE = float(os.environ.get("PN_ARCHIV_VERLASSEN_TAGE", "14"))
TAKT_S = float(os.environ.get("PN_ARCHIV_TAKT_S", "900"))
AUTOMATIK_AN = str(os.environ.get("PN_ARCHIV_AUTOMATIK", "1")).lower() not in ("0", "false", "no", "off")

_RU_RE = re.compile(r"REPRO_RU_ID:\s*([A-Za-z0-9._:-]+)")
_SID_RE = re.compile(r"^[a-z0-9]{6,16}$")
_LOCK = threading.Lock()

def journal(**eintrag):

    try:
        eintrag.setdefault("ts", round(time.time(), 3))
        eintrag.setdefault("zeit", time.strftime("%Y-%m-%dT%H:%M:%S"))
        os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
        with open(JOURNAL, "a", encoding="utf-8") as f:
            f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _store(principal, kind="cockpit"):
    import portal_jobs_persist as _pjp
    return _pjp._session_store(principal, kind)

def principals():

    try:
        return sorted(n for n in os.listdir(USERS_DIR)
                      if os.path.isfile(os.path.join(USERS_DIR, n, "sessions.json")))
    except OSError:
        return []

def _zell_akte():
    try:
        d = json.load(open(ZELL_STORE))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def laufende_zellen():

    lebt = set()
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open("/proc/%s/comm" % pid) as f:
                    if f.read().strip() not in ("pn-vmm", "pn_vmm"):
                        continue
                with open("/proc/%s/cmdline" % pid, "rb") as f:
                    cl = f.read().decode("utf-8", "replace")
            except OSError:
                continue
            for m in re.finditer(r"(sc-[A-Za-z0-9._-]+?)(?:-delta|-work|-keystore)\.img", cl):
                lebt.add(m.group(1))
    except OSError:
        pass

    if os.environ.get("PN_LAUFSPUR_ZAEHLT") == "1":

        try:
            for n in os.listdir(LAUFDIR):
                if n.startswith("sc-"):
                    lebt.add(n)
        except OSError:
            pass
    return lebt

def lebt(principal, sid, akte=None, laufend=None):

    akte = _zell_akte() if akte is None else akte
    rec = akte.get("%s/%s" % (principal, sid)) or {}
    zelle = str(rec.get("cell") or "")
    try:
        import pn_cell_session as _cs
        c = _cs.get_manager().get(principal, sid)
        if c is not None and c.alive():
            return True
    except Exception:
        pass
    if zelle:
        laufend = laufende_zellen() if laufend is None else laufend
        if zelle in laufend:
            return True
    if rec.get("node") and (rec.get("state") or "") in LEBENDE_ZUSTAENDE:
        return True
    return False

def repro_index(pfad=None):

    p = pfad or os.environ.get("PN_REPRO_QUEUE_DB")
    if not p:
        treffer = glob.glob("/data/shares/users/*/sessions/*/reprofleet/queue.db")
        if not treffer:
            return None
        p = max(treffer, key=lambda x: os.path.getmtime(x))
    if not os.path.isfile(p):
        return None
    try:
        import sqlite3
        c = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=5)
        try:
            rows = c.execute("SELECT ru_id, state, status, done_utc FROM room_queue").fetchall()
        finally:
            c.close()
    except Exception:
        return None
    return {str(r[0]): (str(r[1] or ""), str(r[2] or ""), r[3]) for r in rows}

def _utc_ts(s):

    try:
        import calendar
        return calendar.timegm(time.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return None

def befund(s, welt):

    sid = s.get("id")
    titel = s.get("title") or ""
    if sid in NIE or not _SID_RE.match(str(sid or "")):
        return False, "geschuetzt"
    if s.get("state") == "deleted" or s.get("archived"):
        return False, "schon erledigt"
    if welt["lebt"](sid):
        return False, "die Zelle laeuft"
    offene_kinder = [k for k in welt["kinder"].get(sid, []) if k not in welt["archiviert"]]
    if offene_kinder:
        return False, "wartet auf %d noch offene Kind-Sitzung(en)" % len(offene_kinder)

    jetzt = welt["jetzt"]
    still_h = (jetzt - (s.get("last_active") or s.get("created") or jetzt)) / 3600.0

    m = _RU_RE.search(titel)
    if m:
        ru = m.group(1)
        idx = welt["repro"]
        if idx is None:
            return None, "the fleet-Warteschlange nicht lesbar — es wird nichts angefasst"
        eintrag = idx.get(ru)
        if eintrag is None:
            if still_h / 24.0 >= VERLASSEN_TAGE:
                return True, ("Auftrag %s steht in keiner Warteschlange mehr und die Sitzung ist "
                              "seit %.0f Tagen still" % (ru, still_h / 24.0))
            return False, "Auftrag %s (noch) unbekannt, erst %.1f h still" % (ru, still_h)
        zustand, ergebnis, fertig = eintrag
        if zustand != "done":
            return False, "Auftrag %s ist %s" % (ru, zustand or "?")
        seit = fertig and _utc_ts(fertig)
        ruhe_h = ((jetzt - seit) / 3600.0) if seit else still_h
        if ruhe_h < RUHE_H:
            return False, "Auftrag %s erst vor %.1f h fertig geworden" % (ru, ruhe_h)
        return True, ("Reproduktionsauftrag %s ist abgeschlossen (%s%s)"
                      % (ru, ergebnis or "ohne Ergebnisvermerk",
                         (", %s" % fertig) if fertig else ""))

    if still_h / 24.0 >= VERLASSEN_TAGE:
        return True, ("seit %.0f Tagen keine Zelle und keine Aktivitaet" % (still_h / 24.0))
    return False, "erst %.1f Tage still (Schwelle %.0f)" % (still_h / 24.0, VERLASSEN_TAGE)

def eltern_verdrahten():

    try:
        import pn_mediashare as _ms
    except Exception:
        return False
    if callable(getattr(_ms, "parent_of", None)):
        return True
    try:
        import portal_metasessions as _pms
        _ms.parent_of = _pms.meta_parent_of
        return callable(_ms.parent_of)
    except Exception:
        return False

def _share_mgr():
    eltern_verdrahten()
    try:
        import portal_routes_session as prs
        m = prs._share_mgr()
        if m is not None:
            return m
    except Exception:
        pass
    try:
        os.environ.setdefault("PN_MEDIASHARE_NO_DAEMONS", "1")
        import pn_mediashare
        return pn_mediashare.ShareManager()
    except Exception:
        return None

def medien_zustand(sid, mgr=None):

    mgr = mgr or _share_mgr()
    if mgr is None:
        return False, None
    try:
        rec = (mgr._load() or {}).get(str(sid))
    except Exception:
        return False, None
    if not isinstance(rec, dict):
        return False, None
    return True, bool(rec.get("archived"))

def medien_nachziehen(principal, sid, soll, welt=None, mgr=None, stapel=False):

    mgr = mgr or _share_mgr()
    if mgr is None:
        return False, "kein Medienserver erreichbar"
    da, ist = medien_zustand(sid, mgr)
    if not da:
        return False, "kein Ordner auf dem Medienserver"
    if bool(ist) == bool(soll):
        return False, "schon %s" % ("archiviert" if soll else "aktiv")
    if soll:
        if welt is not None and welt["lebt"](sid):
            return False, "Zelle laeuft — Ordner bleibt vorerst liegen"
        if welt is not None:
            offen = [k for k in welt["kinder"].get(sid, []) if k not in welt["archiviert"]]
            if offen:
                return False, "%d Kind-Ablage(n) nisten noch darin" % len(offen)
        try:
            r = _ms_ruf(mgr.archive_share, sid, stapel)
        except Exception as e:
            return False, "Verschieben fehlgeschlagen: %s" % e
        return bool(r), ("Ordner nach %s verschoben" % (r or {}).get("path", "?")) if r else "nichts bewegt"
    try:
        r = _ms_ruf(mgr.unarchive_share, sid, stapel)
    except Exception as e:
        return False, "Zurueckholen fehlgeschlagen: %s" % e
    return bool(r), ("Ordner nach %s zurueckgeholt" % (r or {}).get("path", "?")) if r else "nichts bewegt"

def _ms_ruf(fn, sid, stapel):

    try:
        return fn(sid, apply_now=not stapel)
    except TypeError:
        return fn(sid)

def medien_apply(mgr=None):

    mgr = mgr or _share_mgr()
    if mgr is None:
        return False
    try:
        mgr.apply()
        return True
    except Exception:
        return False

def pfade_heilen(mgr=None, trocken=False):

    mgr = mgr or _share_mgr()
    if mgr is None:
        return {"geprueft": 0, "geheilt": 0, "offen": 0}
    eltern_verdrahten()
    try:
        import pn_mediashare as _ms
        reg = mgr._load() or {}
    except Exception:
        return {"geprueft": 0, "geheilt": 0, "offen": 0}
    st = {"geprueft": 0, "geheilt": 0, "offen": 0, "taten": []}
    for sid, rec in list(reg.items()):
        if not isinstance(rec, dict) or not rec.get("sid"):
            continue
        p = rec.get("path")
        if not p or os.path.isdir(p):
            continue
        st["geprueft"] += 1
        try:
            psid = _ms.parent_of(sid) if callable(_ms.parent_of) else None
        except Exception:
            psid = None
        ppfad = ((reg.get(str(psid)) or {}).get("path")) if psid else None
        ziel = os.path.join(ppfad, "children", os.path.basename(p.rstrip("/"))) if ppfad else None
        if not (ziel and os.path.isdir(ziel)):
            st["offen"] += 1
            continue
        st["taten"].append({"sid": sid, "war": p, "ist": ziel})
        if not trocken:
            with mgr._lock:
                frisch = mgr._load()
                r = frisch.get(sid)
                if isinstance(r, dict) and not os.path.isdir(r.get("path") or ""):
                    r = dict(r)
                    r["path"] = ziel
                    r["pfad_geheilt_am"] = time.time()
                    frisch[sid] = r
                    mgr._save(frisch)
        st["geheilt"] += 1
    return st

def telegram_zustand(principal, sid):

    try:
        import pn_chanadapter as ca
        b = ca.get_binding(_chan_ctx(), principal, "telegram")
    except Exception:
        return False, None
    tid = (b.get("topics") or {}).get(str(sid))
    if not tid:
        return False, None
    return True, bool((b.get("closed") or {}).get(str(sid)))

def _chan_ctx():

    try:
        import portal_jobs_persist as _pjp
        return _pjp._chan_ctx()
    except Exception:
        return {"dir": DATA_DIR}

def welt_bauen(principal, sessions=None):

    sessions = sessions if sessions is not None else _store(principal).list()
    akte, laufend = _zell_akte(), laufende_zellen()
    kinder = {}
    eltern_verdrahten()
    try:
        import pn_mediashare as _ms
        eltern = _ms.parent_of
    except Exception:
        eltern = None
    if callable(eltern):
        for s in sessions:
            try:
                p = eltern(s.get("id"))
            except Exception:
                p = None
            if p and str(p) != str(s.get("id")):
                kinder.setdefault(str(p), []).append(str(s.get("id")))
    return {
        "jetzt": time.time(),
        "repro": repro_index(),
        "kinder": kinder,
        "archiviert": {str(s.get("id")) for s in sessions if s.get("archived")},
        "lebt": lambda sid: lebt(principal, sid, akte, laufend),
    }

def setzen(principal, sid, an=True, grund="", akteur="mensch", welt=None, stapel=False):

    sid = str(sid or "")
    if sid in NIE:
        return {"ok": False, "error": "Diese Sitzung ist vom Archivieren ausgenommen."}
    with _LOCK:
        s = _store(principal).set_archived(sid, bool(an))
        if not s:
            return {"ok": False, "error": "Unbekannte Sitzung — es gibt hier nichts mit diesem Kennzeichen."}
        welt = welt or welt_bauen(principal)
        welt["archiviert"] = (welt["archiviert"] | {sid}) if an else (welt["archiviert"] - {sid})
        med_ge, med_txt = medien_nachziehen(principal, sid, bool(an), welt, stapel=stapel)

    tg_txt = _telegram_anstossen(principal, sid, bool(an), s.get("title"))
    eintrag = {"tat": "archiviert" if an else "entarchiviert", "principal": principal, "sid": sid,
               "titel": s.get("title"), "grund": grund, "akteur": akteur,
               "medienserver": med_txt, "telegram": tg_txt}
    journal(**eintrag)
    return {"ok": True, "session": s, "flaechen": {"portal": "gesetzt", "medienserver": med_txt,
                                                   "telegram": tg_txt, "clients": "folgen der Liste"}}

def _telegram_anstossen(principal, sid, an, titel=None):
    try:
        import portal_channels as pc
        pc.bus_append(_chan_ctx(), principal, sid, "lifecycle",
                      event=("archived" if an else "unarchived"), title=titel)
        return "auf den Bus gelegt (Thema wird %s)" % ("geschlossen" if an else "wieder geoeffnet")
    except Exception as e:
        return "Bus nicht erreichbar (%s) — der Abgleich zieht nach" % type(e).__name__

def abgleich(principal=None, trocken=False):

    ergebnis = {"geprueft": 0, "medien_archiviert": 0, "medien_zurueck": 0, "wartet": 0, "fehler": 0,
                "taten": []}
    mgr = _share_mgr()

    ergebnis["pfade_geheilt"] = pfade_heilen(mgr, trocken=trocken).get("geheilt", 0)
    for p in ([principal] if principal else principals()):
        try:
            sessions = _store(p).list()
        except Exception:
            ergebnis["fehler"] += 1
            continue
        welt = welt_bauen(p, sessions)

        sessions = sorted(sessions, key=lambda s: len(welt["kinder"].get(str(s.get("id")), [])))
        for s in sessions:
            sid = str(s.get("id") or "")
            if sid in NIE or s.get("state") == "deleted":
                continue
            ergebnis["geprueft"] += 1
            soll = bool(s.get("archived"))
            da, ist = medien_zustand(sid, mgr)
            if not da or bool(ist) == soll:
                continue
            if trocken:
                ergebnis["taten"].append({"sid": sid, "soll": soll, "wuerde": "Medienordner angleichen"})
                continue
            ge, txt = medien_nachziehen(p, sid, soll, welt, mgr, stapel=True)
            if ge:
                ergebnis["medien_archiviert" if soll else "medien_zurueck"] += 1
                ergebnis["taten"].append({"sid": sid, "soll": soll, "text": txt})
                journal(tat="abgleich", principal=p, sid=sid, titel=s.get("title"),
                        soll="archiviert" if soll else "aktiv", medienserver=txt)
            else:
                ergebnis["wartet"] += 1
    if not trocken and (ergebnis["medien_archiviert"] or ergebnis["medien_zurueck"]):
        medien_apply(mgr)
    return ergebnis

def selbstarchivierung(trocken=False, principal=None, grenze=None):

    bericht = {"geprueft": 0, "archiviert": 0, "unklar": 0, "bleibt": 0, "taten": [], "gruende": {}}
    for p in ([principal] if principal else principals()):
        try:
            sessions = _store(p).list()
        except Exception:
            continue
        welt = welt_bauen(p, sessions)
        for s in sessions:
            if s.get("archived") or s.get("state") == "deleted":
                continue
            bericht["geprueft"] += 1
            ja, warum = befund(s, welt)
            if ja is None:
                bericht["unklar"] += 1
                bericht["gruende"][warum] = bericht["gruende"].get(warum, 0) + 1
                continue
            if not ja:
                bericht["bleibt"] += 1
                kurz = re.sub(r"[0-9]+([.,][0-9]+)?", "N", warum)
                bericht["gruende"][kurz] = bericht["gruende"].get(kurz, 0) + 1
                continue
            bericht["taten"].append({"sid": s.get("id"), "titel": s.get("title"), "grund": warum})
            if not trocken:
                r = setzen(p, s.get("id"), True, grund=warum, akteur="automatik", welt=welt,
                           stapel=True)
                if not r.get("ok"):
                    continue
                welt["archiviert"].add(str(s.get("id")))
            bericht["archiviert"] += 1
            if grenze and bericht["archiviert"] >= grenze:
                if not trocken:
                    medien_apply()
                return bericht
    if not trocken and bericht["archiviert"]:
        medien_apply()
    return bericht

def _schleife():
    while True:
        try:
            if AUTOMATIK_AN:
                b = selbstarchivierung(grenze=200)
                if b.get("archiviert"):
                    journal(tat="automatik", archiviert=b["archiviert"], geprueft=b["geprueft"])
            abgleich()
        except Exception as e:
            journal(tat="automatik-fehler", fehler="%s: %s" % (type(e).__name__, e))
        time.sleep(TAKT_S)

def archiv_worker_start():

    t = threading.Thread(target=_schleife, name="pn-archiv", daemon=True)
    t.start()
    return t

def bericht(principal=None):
    zeilen, summe = [], {"aktiv": 0, "archiviert": 0, "drift": 0}
    mgr = _share_mgr()
    for p in ([principal] if principal else principals()):
        try:
            sessions = _store(p).list()
        except Exception:
            continue
        for s in sessions:
            if s.get("state") == "deleted":
                continue
            sid = str(s.get("id") or "")
            soll = bool(s.get("archived"))
            summe["archiviert" if soll else "aktiv"] += 1
            da, ist = medien_zustand(sid, mgr)
            hat_tg, zu = telegram_zustand(p, sid)
            drift = []
            if da and bool(ist) != soll:
                drift.append("Medienserver=%s" % ("archiviert" if ist else "aktiv"))
            if hat_tg and bool(zu) != soll:
                drift.append("Telegram=%s" % ("zu" if zu else "offen"))
            if drift:
                summe["drift"] += 1
                zeilen.append({"principal": p, "sid": sid, "titel": s.get("title"),
                               "soll": "archiviert" if soll else "aktiv", "drift": drift})
    return {"summe": summe, "drift": zeilen}

def main():
    import argparse
    import sys
    _srv = os.path.dirname(os.path.abspath(__file__))
    if _srv not in sys.path:
        sys.path.insert(0, _srv)
    ap = argparse.ArgumentParser(description="Archiv-Zustand einer Sitzung ueber alle Flaechen")
    ap.add_argument("--bericht", action="store_true", help="Soll/Ist je Flaeche, nur lesen")
    ap.add_argument("--abgleich", action="store_true", help="Wirklichkeit dem Wunsch angleichen")
    ap.add_argument("--automatik", action="store_true", help="endgueltige Sitzungen archivieren")
    ap.add_argument("--archivieren", metavar="SID")
    ap.add_argument("--entarchivieren", metavar="SID")
    ap.add_argument("--principal", default=None)
    ap.add_argument("--grenze", type=int, default=None)
    ap.add_argument("--pfade-heilen", action="store_true",
                    help="Datensaetze, deren Ordner gewandert ist, wieder auf ihn zeigen lassen")
    ap.add_argument("--trocken", action="store_true", help="nur zeigen, nichts tun")
    a = ap.parse_args()
    pr = a.principal or "owner"
    if a.pfade_heilen:
        st = pfade_heilen(trocken=a.trocken)
        print("%stote Pfade=%d geheilt=%d offen=%d" % ("TROCKEN " if a.trocken else "",
                                                       st["geprueft"], st["geheilt"], st["offen"]))
        for t in st.get("taten", [])[:20]:
            print("   %s\n      war %s\n      ist %s" % (t["sid"], t["war"], t["ist"]))
    elif a.archivieren:
        print(json.dumps(setzen(pr, a.archivieren, True, grund="von Hand", akteur="cli"),
                         ensure_ascii=False, indent=1))
    elif a.entarchivieren:
        print(json.dumps(setzen(pr, a.entarchivieren, False, grund="von Hand", akteur="cli"),
                         ensure_ascii=False, indent=1))
    elif a.automatik:
        b = selbstarchivierung(trocken=a.trocken, principal=a.principal, grenze=a.grenze)
        print("%sgeprueft=%d archiviert=%d bleibt=%d unklar=%d"
              % ("TROCKEN " if a.trocken else "", b["geprueft"], b["archiviert"], b["bleibt"], b["unklar"]))
        for g, n in sorted(b["gruende"].items(), key=lambda x: -x[1])[:12]:
            print("   %4d  %s" % (n, g))
        for t in b["taten"][:10]:
            print("   → %s  %s" % (t["sid"], t["grund"]))
        if len(b["taten"]) > 10:
            print("   … und %d weitere" % (len(b["taten"]) - 10))
    elif a.abgleich:
        r = abgleich(principal=a.principal, trocken=a.trocken)
        print(json.dumps(r, ensure_ascii=False, indent=1)[:4000])
    else:
        r = bericht(principal=a.principal)
        print("aktiv=%d archiviert=%d abweichend=%d" % (r["summe"]["aktiv"], r["summe"]["archiviert"],
                                                        r["summe"]["drift"]))
        for z in r["drift"][:30]:
            print("   %s  %s  soll=%s  %s" % (z["sid"], (z["titel"] or "")[:40], z["soll"],
                                              ", ".join(z["drift"])))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
