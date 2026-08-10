

import time

try:
    import portal_insights
except Exception:
    portal_insights = None

try:
    import portal_commentary
except Exception:
    portal_commentary = None

def _bus_tail_events(bus_read, ctx, uid, cap_bytes=262144):

    try:
        import os
        size = os.path.getsize(os.path.join(ctx["data_dir"], "session-bus.jsonl"))
    except Exception:
        size = 0
    start = max(0, size - cap_bytes)
    try:
        evs, _ = bus_read(ctx, start, principal=uid, limit=8000)
    except Exception:
        evs = []
    out = []
    for ev in evs:
        if ev.get("kind") == "message" and ev.get("role") in ("user", "assistant"):
            out.append({"sid": str(ev.get("sid") or ""), "role": ev.get("role"),
                        "text": str(ev.get("text") or ""), "ts": float(ev.get("ts") or 0)})
    return out

def _fmt_turns(turns, limit=16):
    lines = []
    for t in turns[-limit:]:
        who = "NUTZER" if t["role"] == "user" else "AGENT"
        lines.append("%s: %s" % (who, t["text"][:400]))
    return "\n".join(lines)

_SUMMARY_INSTR = (
    "Fasse den folgenden Sitzungsmitschnitt fuer eine Hover-Vorschau zusammen. Antworte als JSON "
    '{"topic": "<EIN kurzer Satz: worum geht es in dieser Sitzung>", '
    '"status": "<EIN kurzer Satz: was ist der letzte Stand / was wurde zuletzt getan>"}. '
    "Deutsch, sachlich, hoechstens ca. 20 Woerter pro Satz. Kein Markdown.")

def session_summary(bus_read, ctx, uid, sid, title=""):
    if portal_insights is None:
        return {"ok": False, "state": "off"}

    def _data():
        turns = [t for t in _bus_tail_events(bus_read, ctx, uid) if t["sid"] == sid]
        body = _fmt_turns(turns, 16)
        return ("Titel: %s\n\n%s" % (title, body)) if body else ""

    prov = None
    try:
        prov = portal_insights.session_provider(uid, sid)
    except Exception:
        prov = None
    r = portal_insights.get("summary", "%s:%s" % (uid, sid), "haiku", _SUMMARY_INSTR, _data,
                            max_age=24 * 3600, provider=prov)
    return {"ok": True, "state": r["state"], "ts": r["ts"], **(r["data"] or {})}

_OVERVIEW_INSTR = (
    "Du bist der Lage-Assistent einer selbstgehosteten KI-Agenten-Box und brichst dem Besitzer "
    "den aktuellen Zustand seines Systems auf das Wesentliche herunter. Unten stehen HARTE SIGNALE "
    "(offene Entscheidungen, Alarme, Sitzungen, Auslastung), seine letzten Sitzungen und verfuegbare "
    "Funktionen. Antworte als JSON: "
    '{"headline": "<KONKRETE, VARIABLE Kopfzeile aus dem AKTUELLEN Zustand — z.B. \'3 Entscheidungen '
    'warten auf dich\' oder \'Alles ruhig, 2 Sitzungen arbeiten\'. NIEMALS eine feste Begruessungs-'
    'floskel wie \'Willkommen zurueck\' oder \'Schoen dass du wieder da bist\' — die Kopfzeile MUSS '
    'sich mit der Lage aendern und den wichtigsten Fakt nennen>", '
    '"situation": "<1-2 Saetze: woran wurde zuletzt gearbeitet, was ist der Stand, was hat sich '
    'veraendert>", '
    '"recommendations": [<0-3 KONKRETE Handlungsempfehlungen als kurze Saetze, nach Dringlichkeit '
    'sortiert; jede nennt WAS und WARUM. Leere Liste, wenn wirklich nichts ansteht — dann ist Ruhe '
    'die richtige Meldung>], '
    '"proactive": "<optional 1 Satz: ein vorausschauender Vorschlag oder eine ungenutzte passende '
    'Funktion, sanft — oder leer>"}. '
    "Deutsch, sachlich-freundlich wie ein ruhiger Operationsassistent, kein Marketing, kein Markdown. "
    "Erfinde keine Signale, die unten nicht stehen.")

def overview(bus_read, ctx, uid, sessions, features, signals=None):
    if portal_insights is None:
        return {"ok": False, "state": "off"}

    def _data():
        turns = _bus_tail_events(bus_read, ctx, uid)
        last_by_sid = {}
        for t in turns:
            last_by_sid[t["sid"]] = t
        rows = []
        now = time.time()
        for s in sessions[:12]:
            sid = str(s.get("id") or s.get("sid") or "")
            age = ""
            if s.get("created"):
                d = (now - float(s["created"])) / 86400.0
                age = ("%.0f Tage alt" % d) if d >= 1 else "heute"
            lt = last_by_sid.get(sid)
            last = (lt["text"][:120] if lt else "")
            rows.append("- %s (%s)%s%s" % (s.get("title") or sid, age,
                        " [aktiv]" if s.get("warm") else "",
                        (": " + last) if last else ""))
        feat = "\n".join("- %s: %s" % (f["name"], f["hint"]) for f in (features or []))
        sig = "\n".join("- %s" % x for x in (signals or [])) or "- (keine besonderen Signale)"
        return ("HARTE SIGNALE JETZT:\n%s\n\nLETZTE SITZUNGEN:\n%s\n\nVERFUEGBARE FUNKTIONEN:\n%s"
                % (sig, "\n".join(rows), feat))

    _skey = "|".join(sorted(signals or []))[:400]
    r = portal_insights.get("overview", "%s#%s" % (uid, _skey), "sonnet", _OVERVIEW_INSTR,
                            _data, max_age=25 * 60)
    return {"ok": True, "state": r["state"], "ts": r["ts"], **(r["data"] or {})}

_WORKLOAD_INSTR = (
    "Kommentiere die aktuelle Auslastung dieses Heim-Servers in 1-2 freundlichen deutschen Saetzen "
    "fuer den Besitzer (Ton wie ein ruhiger Betriebsassistent). Nenne, ob alles im gruenen Bereich "
    "ist, woher die Last kommt und ob etwas Aufmerksamkeit braucht. Antworte als JSON "
    '{"comment": "<1-2 Saetze>"}. Kein Markdown.')

def workload(uid, snapshot):
    if portal_insights is None:
        return {"ok": False, "state": "off"}

    def _data():
        import json

        p = int(snapshot.get("auslastung_pct") or 0)
        stufe = "niedrig" if p < 40 else ("mittel" if p < 75 else "hoch")
        s = dict(snapshot)
        s["auslastung_stufe"] = stufe
        s.pop("load_1min", None)
        s.pop("auslastung_pct", None)
        s["nachrichten_im_fenster"] = (int(s.get("nachrichten_im_fenster") or 0) // 20) * 20
        return json.dumps(s, ensure_ascii=False)

    r = portal_insights.get("workload", uid, "sonnet", _WORKLOAD_INSTR, _data, max_age=1800)
    return {"ok": True, "state": r["state"], "ts": r["ts"], **(r["data"] or {})}

_BOARD_CH_INSTR = (
    "Du bist der ruhige Fortschritts-Kommentator einer Heim-Server-Box ('Brainbox'). Unten stehen "
    "die letzten Sitzungen des Nutzers (Titel, Alter, letzte Aktivitaet), verfuegbare Funktionen "
    "und — falls vorhanden — deine frueheren Notizen als Verlaufs-Kontext. "
    "Schreibe EINE neue, kurze Statusnotiz fuer den laufenden Verlauf: wo steht die Box insgesamt, "
    "was ist der Stand in den einzelnen Sitzungen/Kanaelen, und was waeren sinnvolle naechste "
    "Schritte. Du siehst die Maschine als Ganzes UND ihre Entwicklung: erkenne Trends "
    "(etwas haengt wiederholt, wird langsamer, faellt immer wieder aus) und sprich Probleme AKTIV "
    "an, statt sie nur zu beschreiben. Erkennst du ein Problem, mache im next-Feld EINEN konkreten, "
    "umsetzbaren Loesungsvorschlag, beginnend mit 'Vorschlag:' (was genau, an welcher Stelle). "
    "Wiederhole nicht, was in deinen frueheren Notizen schon steht — bring Neues oder eskaliere "
    "begruendet, wenn ein bekanntes Problem anhaelt. Antworte als JSON "
    '{"headline":"<3-6 Woerter Ueberschrift>","text":"<2-4 Saetze: Lage je Kanal>",'
    '"next":"<1 Satz: naechster Schritt ODER Vorschlag: ...>"}. Deutsch, sachlich, kein Markdown, '
    "nicht aufdringlich.")

_WORK_CH_INSTR = (
    "Kommentiere die aktuelle Auslastung dieses Heim-Servers als EINE kurze Notiz fuers laufende "
    "Betriebs-Log (Ton: ruhiger Betriebsassistent). Woher kommt die Last, ist alles im gruenen "
    "Bereich, braucht etwas Aufmerksamkeit. Nutze deine frueheren Notizen (falls unten angehaengt) "
    "als Verlauf: verschlechtert sich etwas ueber mehrere Notizen oder kehrt ein Problem wieder, "
    "benenne die wahrscheinliche Ursache und mache EINEN konkreten Vorschlag ('Vorschlag: ...') "
    "statt es nur zu beobachten. Antworte als JSON "
    '{"headline":"<3-6 Woerter>","text":"<1-3 Saetze, ggf. mit Vorschlag>"}. Deutsch, kein Markdown.')

def board_channel(bus_read, ctx, uid, sessions, features, level="haeufig"):

    if portal_commentary is None:
        return {"ok": True, "entries": [], "state": "off", "count": 0, "level": level}

    def _data():
        turns = _bus_tail_events(bus_read, ctx, uid)
        last_by_sid = {}
        for t in turns:
            last_by_sid[t["sid"]] = t
        rows = []
        now = time.time()
        for s in sessions[:12]:
            sid = str(s.get("id") or s.get("sid") or "")
            age = ""
            if s.get("created"):
                d = (now - float(s["created"])) / 86400.0
                age = ("%.0f Tage alt" % d) if d >= 1 else "heute"
            lt = last_by_sid.get(sid)
            last = (lt["text"][:160] if lt else "")
            rows.append("- %s (%s)%s%s" % (s.get("title") or sid, age,
                        " [aktiv]" if s.get("warm") else "", (": " + last) if last else ""))
        feat = "\n".join("- %s: %s" % (f["name"], f["hint"]) for f in (features or []))
        return ("SITZUNGEN:\n%s\n\nFUNKTIONEN:\n%s" % ("\n".join(rows), feat)) if rows else ""

    return portal_commentary.feed("board", uid, "sonnet", _BOARD_CH_INSTR, _data, level=level)

def work_channel(uid, snapshot, level="haeufig"):

    if portal_commentary is None:
        return {"ok": True, "entries": [], "state": "off", "count": 0, "level": level}

    def _data():
        import json as _j
        p = int(snapshot.get("auslastung_pct") or 0)
        stufe = "niedrig" if p < 40 else ("mittel" if p < 75 else "hoch")
        s = dict(snapshot)
        s["auslastung_stufe"] = stufe
        s.pop("load_1min", None)
        s.pop("auslastung_pct", None)
        s["nachrichten_im_fenster"] = (int(s.get("nachrichten_im_fenster") or 0) // 20) * 20

        s["fenster"] = int(time.time() // 1200)
        return _j.dumps(s, ensure_ascii=False)

    return portal_commentary.feed("work", uid, "sonnet", _WORK_CH_INSTR, _data, level=level)
