

import os, json, time, threading, secrets

import portal_jobs_persist as pjp

_LOCK = threading.Lock()

def _path(uid):
    return os.path.join(pjp.user_dir(uid), "thoughts.json")

def _load(uid):
    try:
        v = json.load(open(_path(uid)))
        return v if isinstance(v, list) else []
    except Exception:
        return []

def _save(uid, notes):
    p = _path(uid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    json.dump(notes, open(tmp, "w"), ensure_ascii=False)
    os.replace(tmp, p)

def _find(notes, nid):
    return next((n for n in notes if n.get("id") == nid), None)

def list_notes(uid):
    notes = [n for n in _load(uid) if n.get("state") != "discarded"]
    return sorted(notes, key=lambda n: -(n.get("created") or 0))

def add(uid, text):
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "leer"}
    n = {"id": "th" + secrets.token_hex(5), "created": time.time(),
         "raw_text": text[:4000], "state": "captured"}
    with _LOCK:
        notes = _load(uid)
        notes.append(n)
        _save(uid, notes)
    return {"ok": True, "note": n}

def update(uid, nid, action, prompt=None, title=None, sid=None):
    with _LOCK:
        notes = _load(uid)
        n = _find(notes, nid)
        if not n:
            return {"ok": False, "error": "unbekannte Notiz"}
        if action == "discard":
            n["state"] = "discarded"
            n["discarded_at"] = time.time()
        elif action == "restore":
            n["state"] = "refined" if n.get("refined") else "captured"
        elif action == "set_prompt":
            n.setdefault("refined", {})["prompt"] = str(prompt or "")[:8000]
            if title:
                n["refined"]["title"] = str(title)[:120]
            if n.get("state") == "captured":
                n["state"] = "refined"
        elif action == "drop_draft":
            n.pop("refined", None)
            n["state"] = "captured"
        elif action == "mark_spawned":
            n["state"] = "spawned"
            n["sid"] = str(sid or "")[:40]
        else:
            return {"ok": False, "error": "unbekannte Aktion"}
        _save(uid, notes)
        return {"ok": True, "note": n}

_REFINE_INSTR = (
    "Du bist der Ausformulierer des Gedanken-Eingangs eines Heim-Server-Portals. Aus einer ROHEN, "
    "schnell hingeworfenen Notiz machst du einen vollstaendigen, in sich abgeschlossenen Auftrag "
    "fuer eine autonome Agent-Session (die Session sieht NUR deinen Auftragstext, nicht den Chat). "
    "Ehrliche Annahmen statt Rueckfragen; was wirklich offen bleibt, gehoert in open_questions. "
    "Der Auftrag endet IMMER mit dem Satz: 'Arbeite in Werkstufen: erstelle ZUERST einen Plan mit "
    "deinen offenen Fragen als Entwurf und warte auf Freigabe, bevor du ausfuehrst.' "
    "Antworte NUR mit JSON: {\"title\": \"<Titel, max 8 Worte>\", \"prompt\": \"<vollstaendiger "
    "Auftrag>\", \"open_questions\": [\"...\"]}")

def refine(uid, nid, hint=""):

    with _LOCK:
        notes = _load(uid)
        n = _find(notes, nid)
        if not n:
            return {"ok": False, "error": "unbekannte Notiz"}
        if n.get("state") == "refining":
            return {"ok": False, "error": "laeuft bereits"}
        n["state"] = "refining"
        n.pop("refine_error", None)
        _save(uid, notes)
    raw = n.get("raw_text") or ""
    hint = str(hint or "").strip()[:500]

    def _go():
        ok, text = False, ""
        try:
            import portal_insights as pi
            data = raw
            if hint:
                data += "\n\n[Steuer-Hinweis des Nutzers fuer die Ausformulierung: %s]" % hint
            ok, text = pi._run_claude(os.environ.get("PN_THOUGHTS_MODEL", "sonnet"),
                                      _REFINE_INSTR, data)
        except Exception as e:
            ok, text = False, str(e)
        ref = None
        if ok and text:
            try:
                d = json.loads(text[text.index("{"):text.rindex("}") + 1])
                if str(d.get("prompt") or "").strip():
                    ref = {"title": str(d.get("title") or "")[:120],
                           "prompt": str(d.get("prompt"))[:8000],
                           "open_questions": [str(q)[:300] for q in (d.get("open_questions") or [])][:8],
                           "at": time.time()}
            except Exception:
                ref = None
        with _LOCK:
            notes2 = _load(uid)
            m = _find(notes2, nid)
            if m is not None and m.get("state") == "refining":
                if ref:
                    m["refined"] = ref
                    m["state"] = "refined"
                else:
                    m["state"] = "captured"
                    m["refine_error"] = (text or "Ausformulierung fehlgeschlagen")[:200]
                _save(uid, notes2)

    threading.Thread(target=_go, daemon=True).start()
    return {"ok": True, "state": "refining"}

def send_to_session(uid, nid):

    with _LOCK:
        n = _find(_load(uid), nid)
    if not n:
        return {"ok": False, "error": "unbekannte Notiz"}
    sid = n.get("sid")
    if not sid:
        return {"ok": False, "error": "Notiz hat noch keine Session"}
    prompt = ((n.get("refined") or {}).get("prompt")) or n.get("raw_text") or ""
    try:
        import pn_cell_session as cs
        cell = cs.get_manager().get(uid, sid)
    except Exception as e:
        return {"ok": False, "error": str(e), "retry": True}
    if cell is None:
        return {"ok": False, "error": "Zelle (noch) nicht vorhanden", "retry": True}
    if not cell.submit(prompt):
        return {"ok": False, "error": "Uebergabe in die Zelle fehlgeschlagen", "retry": True}
    with _LOCK:
        notes = _load(uid)
        m = _find(notes, nid)
        if m is not None:
            m["sent"] = True
            m["sent_at"] = time.time()
            _save(uid, notes)
    return {"ok": True}
