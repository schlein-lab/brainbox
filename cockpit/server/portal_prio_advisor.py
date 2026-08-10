
import os, json, time, threading

DATA_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "brainbox-portal")
_prov_log = None
llm_run_core = None
pn_req = None

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v
    g["_ADVISOR_STATE_PATH"] = os.path.join(g["DATA_DIR"], "prio-advisor.json")

_ADV_INTERVAL_S = 180
_ADV_MAX_MOVES = 5
_ADV_PRIO_MIN = 40
_ADV_PRIO_MAX = 160
_ADV_MIN_QUEUE = 4
_ADV_MODEL = "fast"
_ADV_SYSTEM = ("Du bist der Scheduling-Advisor einer HPC-artigen Brainbox-Job-Queue. Deine Empfehlung "
               "wird als Daten angewandt; die eigentliche Zuteilung bleibt deterministisch. Antworte "
               "IMMER ausschliesslich mit gueltigem JSON, nie mit Prosa.")
_ADVISOR = {"started": False, "enabled": None, "last": None, "decisions": [],
            "lock": threading.Lock(), "cycle": threading.Lock()}
_ADVISOR_STATE_PATH = os.path.join(DATA_DIR, "prio_advisor.json")

def _advisor_load():
    try:
        with open(_ADVISOR_STATE_PATH) as f:
            return json.load(f) or {}
    except Exception:
        return {}

def _advisor_save(d):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = _ADVISOR_STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, _ADVISOR_STATE_PATH)
    except Exception:
        pass

def _advisor_enabled():
    if _ADVISOR["enabled"] is None:
        _ADVISOR["enabled"] = bool(_advisor_load().get("enabled", False))
    return bool(_ADVISOR["enabled"])

def _advisor_note(msg, considered=0, moved=0, llm_status=None):
    with _ADVISOR["lock"]:
        _ADVISOR["last"] = {"ts": time.time(), "note": msg, "considered": considered,
                            "moved": moved, "llm_status": llm_status}

def _advisor_record(jid, frm, to, reason, applied, cand):
    with _ADVISOR["lock"]:
        _ADVISOR["decisions"].insert(0, {"ts": time.time(), "id": jid, "from": frm, "to": to,
                                         "reason": (reason or "")[:160], "applied": bool(applied),
                                         "task": cand.get("task"), "user": cand.get("user")})
        del _ADVISOR["decisions"][60:]

def _advisor_parse(text):

    text = (text or "").strip()
    i, j = text.find("["), text.rfind("]")
    if i < 0 or j <= i:
        return []
    try:
        arr = json.loads(text[i:j + 1])
    except (ValueError, TypeError):
        return []
    out = []
    if isinstance(arr, list):
        for m in arr[:200]:
            if isinstance(m, dict) and m.get("id") is not None and m.get("prio") is not None:
                out.append(m)
    return out[:64]

def _advisor_cycle():

    if not _advisor_enabled():
        return "disabled"
    if not _ADVISOR["cycle"].acquire(blocking=False):
        _advisor_note("cycle already running — skipped")
        return "busy"
    try:
        lst = pn_req({"verb": "list", "limit": 200})
        if not lst.get("ok"):
            _advisor_note("pnd unavailable — skipped")
            return "pnd-down"
        now = lst.get("now") or time.time()
        jobs = [j for j in lst.get("jobs", [])
                if j.get("state") == "queued" and j.get("source") != "filler"]
        if len(jobs) < _ADV_MIN_QUEUE:
            _advisor_note("queue shallow (%d) — nothing to advise" % len(jobs), considered=len(jobs))
            return "shallow"
        cand = []
        for j in jobs[:40]:
            cand.append({"id": j.get("id"),
                         "task": j.get("task_type") or j.get("client_tag") or "job",
                         "user": j.get("submitter_principal") or j.get("principal") or "?",
                         "prio": int(j.get("prio") or 100),
                         "age_min": int(max(0, now - (j.get("submitted_at") or now)) // 60)})
        prompt = ("NIEDRIGERE prio = FRUEHER dran (Band %d..%d). Wartende Jobs als JSON unten. Schlage "
                  "NUR dort Umsortierungen vor, wo sie Gesamt-Latenz/Fairness klar verbessern: kurze/"
                  "interaktive Jobs und lange Wartende vorziehen, grosse Batch-Jobs ohne Eile "
                  "zurueckstellen. Hoechstens %d Aenderungen. Gib AUSSCHLIESSLICH ein JSON-Array "
                  "zurueck, jedes Element {\"id\":<int>,\"prio\":<int %d..%d>,\"reason\":\"<kurz>\"}. "
                  "Nichts ausserhalb des Arrays.\nJobs: %s"
                  % (_ADV_PRIO_MIN, _ADV_PRIO_MAX, _ADV_MAX_MOVES, _ADV_PRIO_MIN, _ADV_PRIO_MAX,
                     json.dumps(cand, ensure_ascii=False)))
        r = llm_run_core(prompt, system=_ADV_SYSTEM, model=_ADV_MODEL, timeout=60)
        st = r.get("status")
        if st == 503:
            _advisor_note("LLM busy (503) — skipped", considered=len(jobs), llm_status=503)
            return "llm-busy"
        if not r.get("ok"):
            _advisor_note("LLM %s — skipped" % st, considered=len(jobs), llm_status=st)
            return "llm-error"
        moves = _advisor_parse(r.get("text") or "")
        ids = {c["id"] for c in cand}
        curp = {c["id"]: c["prio"] for c in cand}
        cmap = {c["id"]: c for c in cand}
        applied = 0
        attempts = 0
        seen = set()
        for m in moves:
            if attempts >= _ADV_MAX_MOVES:
                break
            jid = m.get("id")
            if jid not in ids or jid in seen:
                continue
            try:
                newp = int(m.get("prio"))
            except (TypeError, ValueError):
                continue
            newp = max(_ADV_PRIO_MIN, min(_ADV_PRIO_MAX, newp))
            seen.add(jid)
            if newp == curp.get(jid):
                continue
            attempts += 1
            rr = pn_req({"verb": "admin-reprioritize", "id": jid, "prio": newp})
            ok = bool(rr.get("ok"))
            _advisor_record(jid, curp.get(jid), (rr.get("prio") if ok else newp),
                            m.get("reason", ""), ok, cmap.get(jid, {}))
            _prov_log("prio.advise", "__system__", str(jid),
                      {"from": curp.get(jid), "to": newp, "reason": (m.get("reason") or "")[:200],
                       "applied": ok, "account": r.get("account")})
            if ok:
                applied += 1
        note = "cycle: %d queued, %d applied / %d versucht" % (len(jobs), applied, attempts)
        _advisor_note(note, considered=len(jobs), moved=applied, llm_status=200)
        return note
    finally:
        _ADVISOR["cycle"].release()

def _advisor_status():
    with _ADVISOR["lock"]:
        return {"ok": True, "enabled": _advisor_enabled(), "interval_s": _ADV_INTERVAL_S,
                "max_moves": _ADV_MAX_MOVES, "prio_band": [_ADV_PRIO_MIN, _ADV_PRIO_MAX],
                "last": _ADVISOR.get("last"), "decisions": list(_ADVISOR["decisions"])[:40]}

def _advisor_toggle(body):
    action = str((body or {}).get("action") or "").lower()
    if action in ("enable", "on", "disable", "off"):
        en = action in ("enable", "on")
        _ADVISOR["enabled"] = en
        _advisor_save({"enabled": en})
        _prov_log("prio.advisor_toggle", "__system__", action, {"enabled": en})
        return {"ok": True, "enabled": en}
    if action in ("run", "run-now", "runnow"):
        if not _advisor_enabled():
            return {"ok": False, "msg": "advisor is off — enable it first"}
        note = _advisor_cycle()
        return {"ok": True, "ran": True, "note": note, "last": _ADVISOR.get("last")}
    return {"ok": False, "msg": "action must be enable/disable/run"}

def start_prio_advisor():

    if _ADVISOR["started"]:
        return
    _ADVISOR["started"] = True

    def loop():
        time.sleep(150)
        while True:
            try:
                _advisor_cycle()
            except Exception:
                pass
            time.sleep(_ADV_INTERVAL_S)
    threading.Thread(target=loop, daemon=True).start()
