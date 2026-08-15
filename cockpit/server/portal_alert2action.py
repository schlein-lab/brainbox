

import os, re, json, time, threading, hashlib

_LOCK = threading.RLock()

def _store_path(uid, user_dir):
    return os.path.join(user_dir(uid), "alert2action.json")

def load(uid, user_dir):
    try:
        with open(_store_path(uid, user_dir), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, list) else []
    except Exception:
        return []

def save(uid, user_dir, watches):
    p = _store_path(uid, user_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(watches, f, ensure_ascii=False)
    os.replace(tmp, p)

def default_watch(name, url):
    return {
        "id": "w" + hashlib.sha1(("%s%s" % (name, url)).encode()).hexdigest()[:10],
        "name": str(name or "Watch")[:120], "url": str(url or ""), "enabled": True,
        "fetch": {"mode": "http", "interval_s": 900, "jitter_frac": 0.2},
        "extract": {"selectors": [], "post_filters": ["strip_html", "collapse_ws"]},
        "signal": {"kind": "text_contains", "pattern": "", "number": {"op": "<", "value": 0.0},
                   "llm": {"condition": "", "diff_gated": True, "require_evidence": True}},
        "debounce": {"confirm_consecutive": 2, "rearm": "manual", "rearm_cooldown_s": 21600,
                     "max_fires": 1},
        "action": {"kind": "notify", "notify": {"channels": ["portal", "telegram"]},
                   "agent": {"brief": "", "grants": []},
                   "gate": {"level": "L4_confirm", "spend_cap_eur": 0}},
        "health": {"expected_change_period_d": 14},
        "state": {"phase": "ARMED", "fire_seq": 0, "last_hash": None, "consec": 0,
                  "last_check": 0, "last_change": 0, "last_fire": 0, "health": "ok",
                  "health_reason": "", "dry_run_until": 0},
    }

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

def extract(html, selectors=None, post_filters=None):

    text = html or ""
    post_filters = post_filters or ["strip_html", "collapse_ws"]
    if "strip_html" in post_filters:
        text = _TAG.sub(" ", text)
    if "collapse_ws" in post_filters:
        text = _WS.sub(" ", text).strip()
    return text

def eval_signal(signal, extract_new, extract_prev=None):

    kind = (signal or {}).get("kind") or "text_contains"
    s = extract_new or ""
    if kind == "text_contains":
        pat = str(signal.get("pattern") or "")
        met = bool(pat) and (pat.lower() in s.lower())
        ev = pat if met else ""
        return {"met": met, "confidence": 1.0 if pat else 0.0, "evidence": ev, "needs_llm": False}
    if kind == "text_absent":
        pat = str(signal.get("pattern") or "")
        met = bool(pat) and (pat.lower() not in s.lower())
        return {"met": met, "confidence": 1.0 if pat else 0.0, "evidence": pat, "needs_llm": False}
    if kind == "number_cmp":
        num = signal.get("number") or {}
        op = num.get("op", "<"); target = float(num.get("value", 0))
        m = re.search(r"(-?\d[\d.,]*)", s)
        if not m:
            return {"met": False, "confidence": 0.0, "evidence": "", "needs_llm": False}
        raw = m.group(1).replace(".", "").replace(",", ".") if raw_is_de(m.group(1)) else m.group(1).replace(",", "")
        try:
            val = float(raw)
        except Exception:
            return {"met": False, "confidence": 0.0, "evidence": m.group(1), "needs_llm": False}
        met = {"<": val < target, "<=": val <= target, ">": val > target,
               ">=": val >= target, "==": val == target}.get(op, False)
        return {"met": met, "confidence": 1.0, "evidence": "%s %s %s" % (val, op, target), "needs_llm": False}
    if kind == "diff_any":
        prev = extract_prev
        met = (prev is not None) and (s != prev)
        return {"met": met, "confidence": 1.0, "evidence": "geaendert" if met else "", "needs_llm": False}
    if kind == "llm":
        return {"met": None, "confidence": 0.0, "evidence": "", "needs_llm": True}
    return {"met": False, "confidence": 0.0, "evidence": "", "needs_llm": False}

def raw_is_de(token):

    last_dot = token.rfind("."); last_com = token.rfind(",")
    return last_com > last_dot

def debounce_step(watch, met, now):

    st = watch.setdefault("state", {})
    db = watch.get("debounce") or {}
    k = max(1, int(db.get("confirm_consecutive", 2)))
    phase = st.get("phase", "ARMED")

    if phase == "DISARMED":
        return False
    if phase == "COOLDOWN":

        if met:
            st["cooldown_false_since"] = 0
            return False
        since = st.get("cooldown_false_since") or now
        st["cooldown_false_since"] = since
        if now - since >= int(db.get("rearm_cooldown_s", 21600)):
            st["phase"] = "ARMED"; st["consec"] = 0
        return False

    if not met:
        st["phase"] = "ARMED"; st["consec"] = 0
        return False
    st["consec"] = int(st.get("consec", 0)) + 1
    st["phase"] = "PENDING"
    if st["consec"] >= k:
        st["phase"] = "DISARMED" if (db.get("rearm") == "manual") else "COOLDOWN"
        st["consec"] = 0
        st["cooldown_false_since"] = 0
        st["fire_seq"] = int(st.get("fire_seq", 0)) + 1
        st["last_fire"] = now
        return True
    return False

def rearm(watch, now=None):
    st = watch.setdefault("state", {})
    st["phase"] = "ARMED"; st["consec"] = 0; st["cooldown_false_since"] = 0
    return watch

def check_once(watch, fetch_fn, judge_fn=None, now=None):

    now = time.time() if now is None else now
    st = watch.setdefault("state", {})
    st["last_check"] = now
    res = {"fired": False, "met": False, "health": "ok", "evidence": "", "phase": st.get("phase")}
    try:
        status, body = fetch_fn(watch.get("url"))
    except Exception as e:
        st["health"] = "fetch_error"; st["health_reason"] = str(e)[:200]
        st["err_streak"] = int(st.get("err_streak", 0)) + 1
        res["health"] = "fetch_error"; res["error"] = str(e)[:200]
        return res
    if status in (429, 403):
        st["health"] = "banned_suspected"; st["health_reason"] = "HTTP %s" % status
        st["err_streak"] = int(st.get("err_streak", 0)) + 1
        res["health"] = "banned_suspected"
        return res
    if status and status >= 400:
        st["health"] = "http_error"; st["health_reason"] = "HTTP %s" % status
        st["err_streak"] = int(st.get("err_streak", 0)) + 1
        res["health"] = "http_error"
        return res
    st["err_streak"] = 0
    ex = watch.get("extract") or {}
    new = extract(body, ex.get("selectors"), ex.get("post_filters"))
    if not new.strip():

        st["health"] = "selector_empty"; st["health_reason"] = "Extraktion leer"
        res["health"] = "selector_empty"
        return res
    st["health"] = "ok"; st["health_reason"] = ""
    prev_hash = st.get("last_hash")
    new_hash = hashlib.sha1(new.encode("utf-8")).hexdigest()
    changed = (prev_hash is not None) and (new_hash != prev_hash)
    prev_text = st.get("last_text")
    st["last_hash"] = new_hash
    st["last_text"] = new[:8000]
    if new_hash != prev_hash:
        st["last_change"] = now

    sig = watch.get("signal") or {}
    ev = eval_signal(sig, new, prev_text)
    if ev.get("needs_llm"):

        if sig.get("llm", {}).get("diff_gated", True) and not changed:
            met, evidence = False, ""
        elif judge_fn is not None:
            try:
                j = judge_fn(sig.get("llm", {}).get("condition", ""), new, prev_text) or {}
            except Exception:
                j = {}
            met = bool(j.get("met"))
            evidence = str(j.get("evidence") or "")

            if met and sig.get("llm", {}).get("require_evidence", True) and evidence and evidence not in new:
                met = False
        else:
            met = False; evidence = ""
    else:
        met = bool(ev.get("met")); evidence = ev.get("evidence", "")

    res["met"] = met; res["evidence"] = evidence

    dry = now < (st.get("dry_run_until") or 0)
    fired = debounce_step(watch, met, now)
    res["phase"] = st.get("phase")
    if fired and not dry:
        res["fired"] = True
    elif fired and dry:
        res["would_fire"] = True
    return res

def _selftest():
    ok = True
    def ck(name, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print("  [%s] %s" % ("PASS" if cond else "FAIL", name))

    r = eval_signal({"kind": "text_contains", "pattern": "Anmeldung"}, "Jetzt Anmeldung offen")
    ck("text_contains trifft", r["met"] and not r["needs_llm"])
    r = eval_signal({"kind": "text_contains", "pattern": "Anmeldung"}, "geschlossen")
    ck("text_contains trifft nicht", not r["met"])
    r = eval_signal({"kind": "number_cmp", "number": {"op": "<", "value": 100}}, "Preis: 79,99 EUR")
    ck("number_cmp de-Zahl < Ziel", r["met"])
    r = eval_signal({"kind": "number_cmp", "number": {"op": "<", "value": 50}}, "Preis: 79,99 EUR")
    ck("number_cmp de-Zahl nicht < Ziel", not r["met"])
    r = eval_signal({"kind": "diff_any"}, "B", "A")
    ck("diff_any erkennt Aenderung", r["met"])
    r = eval_signal({"kind": "llm", "llm": {"condition": "x"}}, "irgendwas")
    ck("llm -> needs_llm", r["needs_llm"] and r["met"] is None)

    w = default_watch("t", "http://x")
    w["signal"] = {"kind": "text_contains", "pattern": "offen"}
    seq = []
    def fetch_seq(vals):
        it = iter(vals)
        def f(url):
            return (200, next(it))
        return f

    bodies = ["zu", "zu", "offen", "offen", "offen"]
    fires = []
    t = 1000.0
    for i, b in enumerate(bodies):
        res = check_once(w, fetch_seq([b]), now=t + i * 10)
        fires.append(res["fired"])
    ck("k=2: erst beim 2. 'offen' feuern", fires == [False, False, False, True, False])
    ck("nach Feuer DISARMED", w["state"]["phase"] == "DISARMED")

    rearm(w)
    r1 = check_once(w, fetch_seq(["offen"]), now=t + 100)
    r2 = check_once(w, fetch_seq(["offen"]), now=t + 110)
    ck("nach Re-Arm feuert es wieder", r2["fired"])

    w2 = default_watch("h", "http://x")
    w2["signal"] = {"kind": "text_contains", "pattern": "x"}
    res = check_once(w2, lambda u: (200, "<html><body>   </body></html>"), now=2000.0)
    ck("leere Extraktion -> selector_empty", res["health"] == "selector_empty" and not res["fired"])

    res = check_once(w2, lambda u: (429, "blocked"), now=2001.0)
    ck("429 -> banned_suspected", res["health"] == "banned_suspected")

    w3 = default_watch("l", "http://x")
    w3["signal"] = {"kind": "llm", "llm": {"condition": "offen?", "diff_gated": True, "require_evidence": False}}
    judged = {"n": 0}
    def judge(cond, new, prev):
        judged["n"] += 1
        return {"met": True, "evidence": ""}
    check_once(w3, lambda u: (200, "seite A"), judge_fn=judge, now=3000.0)
    check_once(w3, lambda u: (200, "seite A"), judge_fn=judge, now=3010.0)
    ck("diff-gated: Judge bei unveraenderter Seite nicht gefragt", judged["n"] == 0)
    check_once(w3, lambda u: (200, "seite B"), judge_fn=judge, now=3020.0)
    ck("diff-gated: Judge bei Aenderung gefragt", judged["n"] == 1)

    w4 = default_watch("e", "http://x")
    w4["signal"] = {"kind": "llm", "llm": {"condition": "c", "diff_gated": False, "require_evidence": True}}
    w4["debounce"]["confirm_consecutive"] = 1
    def judge_bad(cond, new, prev):
        return {"met": True, "evidence": "NICHT-IM-TEXT"}
    res = check_once(w4, lambda u: (200, "realer inhalt"), judge_fn=judge_bad, now=4000.0)
    ck("require_evidence: erfundene Evidenz feuert nicht", not res["fired"])

    print("\nSELFTEST:", "ALL GREEN" if ok else "FAILURES")
    return 0 if ok else 1

if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("portal_alert2action — Engine-Kern; --selftest zum Pruefen.")
