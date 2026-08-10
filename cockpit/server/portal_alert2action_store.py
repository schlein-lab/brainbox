

import os, json, time, threading, socket, ipaddress, secrets
import urllib.request, urllib.parse, urllib.error

import portal_jobs_persist as pjp
import portal_alert2action as engine

_LOCK = threading.RLock()

_UA = "Brainbox-Alert2Action/1.0 (+persoenliche Automation; Heimserver)"
_MAX_BYTES = 2_000_000
_CACHE_MAX = 512_000
_FETCH_TIMEOUT = 20
_ALLOW_PRIVATE_ENV = "PN_A2A_ALLOW_PRIVATE"

_MIN_INTERVAL_S = 60
_TICK_WAKE_S = 30

_BODY_CACHE = {}

_CTX = {}

def _uid_dir(uid):
    return pjp.user_dir(uid)

def list_watches(uid):
    ws = engine.load(uid, _uid_dir)
    return sorted(ws, key=lambda w: -(w.get("created") or 0))

def _save(uid, watches):
    engine.save(uid, _uid_dir, watches)

def _find(watches, wid):
    return next((w for w in watches if w.get("id") == wid), None)

_SIGNAL_KINDS = ("text_contains", "text_absent", "number_cmp", "diff_any", "llm")
_ACTION_KINDS = ("notify", "agent_session")

def _validate_url(url):
    url = str(url or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return None, "URL muss mit http:// oder https:// beginnen"
    return url[:2000], ""

def _public_view(w):

    st = w.get("state") or {}
    return {
        "id": w.get("id"), "name": w.get("name"), "url": w.get("url"),
        "enabled": bool(w.get("enabled", True)),
        "interval_s": int((w.get("fetch") or {}).get("interval_s") or 900),
        "signal": {"kind": (w.get("signal") or {}).get("kind"),
                   "pattern": (w.get("signal") or {}).get("pattern"),
                   "number": (w.get("signal") or {}).get("number"),
                   "llm": {"condition": ((w.get("signal") or {}).get("llm") or {}).get("condition")}},
        "action": {"kind": (w.get("action") or {}).get("kind"),
                   "brief": ((w.get("action") or {}).get("agent") or {}).get("brief")},
        "phase": st.get("phase"), "health": st.get("health") or "unknown",
        "health_reason": st.get("health_reason") or "",
        "last_check": st.get("last_check") or 0, "last_change": st.get("last_change") or 0,
        "last_fire": st.get("last_fire") or 0, "fire_seq": st.get("fire_seq") or 0,
        "pending": [p for p in (w.get("pending_actions") or []) if p.get("state") == "awaiting_approval"],
    }

def _apply_config(w, name=None, url=None, interval_s=None, signal=None, action=None):
    if name is not None:
        w["name"] = str(name or "Watch").strip()[:120] or "Watch"
    if url is not None:
        w["url"] = url
    if interval_s is not None:
        try:
            w.setdefault("fetch", {})["interval_s"] = max(_MIN_INTERVAL_S, int(interval_s))
        except Exception:
            pass
    if isinstance(signal, dict):
        sig = w.setdefault("signal", {})
        kind = str(signal.get("kind") or sig.get("kind") or "text_contains")
        if kind not in _SIGNAL_KINDS:
            kind = "text_contains"
        sig["kind"] = kind
        if "pattern" in signal:
            sig["pattern"] = str(signal.get("pattern") or "")[:400]
        if isinstance(signal.get("number"), dict):
            op = str(signal["number"].get("op") or "<")
            try:
                val = float(signal["number"].get("value") or 0)
            except Exception:
                val = 0.0
            sig["number"] = {"op": op if op in ("<", "<=", ">", ">=", "==") else "<", "value": val}
        if isinstance(signal.get("llm"), dict):
            llm = sig.setdefault("llm", {"diff_gated": True, "require_evidence": True})
            if "condition" in signal["llm"]:
                llm["condition"] = str(signal["llm"].get("condition") or "")[:600]
            if "diff_gated" in signal["llm"]:
                llm["diff_gated"] = bool(signal["llm"].get("diff_gated"))
            if "require_evidence" in signal["llm"]:
                llm["require_evidence"] = bool(signal["llm"].get("require_evidence"))
    if isinstance(action, dict):
        act = w.setdefault("action", {})
        kind = str(action.get("kind") or act.get("kind") or "notify")
        if kind not in _ACTION_KINDS:
            kind = "notify"
        act["kind"] = kind
        if "brief" in action or isinstance(action.get("agent"), dict):
            brief = action.get("brief")
            if brief is None and isinstance(action.get("agent"), dict):
                brief = action["agent"].get("brief")
            act.setdefault("agent", {})["brief"] = str(brief or "")[:4000]
    return w

def add(uid, name, url, interval_s=None, signal=None, action=None):
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "error": "Name fehlt"}
    curl, err = _validate_url(url)
    if err:
        return {"ok": False, "error": err}
    w = engine.default_watch(name, curl)
    w["created"] = time.time()
    _apply_config(w, name=name, url=curl, interval_s=interval_s, signal=signal, action=action)
    with _LOCK:
        ws = engine.load(uid, _uid_dir)
        ws.append(w)
        _save(uid, ws)
    return {"ok": True, "watch": _public_view(w)}

def update(uid, wid, name=None, url=None, interval_s=None, signal=None, action=None):
    if url is not None:
        curl, err = _validate_url(url)
        if err:
            return {"ok": False, "error": err}
        url = curl
    with _LOCK:
        ws = engine.load(uid, _uid_dir)
        w = _find(ws, str(wid or ""))
        if not w:
            return {"ok": False, "error": "unbekannter Wächter"}
        _apply_config(w, name=name, url=url, interval_s=interval_s, signal=signal, action=action)
        _save(uid, ws)
        return {"ok": True, "watch": _public_view(w)}

def pause(uid, wid, enabled):
    with _LOCK:
        ws = engine.load(uid, _uid_dir)
        w = _find(ws, str(wid or ""))
        if not w:
            return {"ok": False, "error": "unbekannter Wächter"}
        w["enabled"] = bool(enabled)
        _save(uid, ws)
        return {"ok": True, "watch": _public_view(w)}

def rearm(uid, wid):
    with _LOCK:
        ws = engine.load(uid, _uid_dir)
        w = _find(ws, str(wid or ""))
        if not w:
            return {"ok": False, "error": "unbekannter Wächter"}
        engine.rearm(w)
        _save(uid, ws)
        return {"ok": True, "watch": _public_view(w)}

def delete(uid, wid):
    wid = str(wid or "")
    with _LOCK:
        ws = engine.load(uid, _uid_dir)
        n = len(ws)
        ws = [w for w in ws if w.get("id") != wid]
        if len(ws) == n:
            return {"ok": False, "error": "unbekannter Wächter"}
        _save(uid, ws)
    _BODY_CACHE.pop(wid, None)
    return {"ok": True}

def approve(uid, wid, pid):

    with _LOCK:
        ws = engine.load(uid, _uid_dir)
        w = _find(ws, str(wid or ""))
        if not w:
            return {"ok": False, "error": "unbekannter Wächter"}
        pend = next((p for p in (w.get("pending_actions") or [])
                     if p.get("id") == str(pid or "") and p.get("state") == "awaiting_approval"), None)
        if not pend:
            return {"ok": False, "error": "keine offene Freigabe"}
        brief = pend.get("brief") or ((w.get("action") or {}).get("agent") or {}).get("brief") or ""
        note_id = None
        try:
            import portal_thoughts as _th
            r = _th.add(uid, ("[Alert-to-Action: %s]\n%s\n\n(Signal-Beleg: %s)"
                              % (w.get("name") or "Wächter", brief, pend.get("evidence") or "")))
            note_id = (r.get("note") or {}).get("id") if r.get("ok") else None
        except Exception as e:
            return {"ok": False, "error": "Übergabe fehlgeschlagen: %s" % (str(e)[:120])}
        pend["state"] = "approved"
        pend["approved_at"] = time.time()
        pend["thought_id"] = note_id
        _save(uid, ws)
        return {"ok": True, "thought_id": note_id,
                "note": "Freigegeben — der Auftrag liegt jetzt im Gedanken-Eingang; starte ihn dort über den Ausstatten-Wizard."}

def dismiss_pending(uid, wid, pid):
    with _LOCK:
        ws = engine.load(uid, _uid_dir)
        w = _find(ws, str(wid or ""))
        if not w:
            return {"ok": False, "error": "unbekannter Wächter"}
        pend = next((p for p in (w.get("pending_actions") or []) if p.get("id") == str(pid or "")), None)
        if not pend:
            return {"ok": False, "error": "keine offene Freigabe"}
        pend["state"] = "dismissed"
        pend["dismissed_at"] = time.time()
        _save(uid, ws)
        return {"ok": True}

def _addr_blocked(ipstr):

    try:
        ip = ipaddress.ip_address(ipstr)
    except Exception:
        return True, "keine IP"
    if (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified):
        return True, "privat/loopback (%s)" % ipstr
    return False, ""

def _host_blocked(host):

    if not host:
        return True, "kein Host"
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception as e:
        return True, "DNS-Fehler (%s)" % (str(e)[:60])
    for fam, _t, _p, _c, sockaddr in infos:
        blocked, why = _addr_blocked(sockaddr[0])
        if blocked:
            return True, why
    return False, ""

def _ssrf_ok(url):
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https"):
        return False, "nur http/https"
    if os.environ.get(_ALLOW_PRIVATE_ENV) == "1":
        return True, ""
    blocked, why = _host_blocked(p.hostname)
    if blocked:
        return False, "SSRF-Schutz: %s" % why
    return True, ""

def make_fetch_fn(watch):

    wid = watch.get("id") or ""
    st = watch.setdefault("state", {})

    def fetch(url):
        ok, why = _ssrf_ok(url)
        if not ok:
            raise ValueError(why)
        headers = {"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml,text/plain,*/*"}
        have_cache = wid in _BODY_CACHE
        if have_cache and st.get("etag"):
            headers["If-None-Match"] = st["etag"]
        if have_cache and st.get("last_modified"):
            headers["If-Modified-Since"] = st["last_modified"]
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as r:
                status = r.getcode() or 200
                et = r.headers.get("ETag"); lm = r.headers.get("Last-Modified")
                raw = r.read(_MAX_BYTES + 1)[:_MAX_BYTES]
                body = raw.decode("utf-8", "replace")
                st["etag"] = et or None
                st["last_modified"] = lm or None
                if len(raw) <= _CACHE_MAX:
                    _BODY_CACHE[wid] = body
                else:
                    _BODY_CACHE.pop(wid, None)
                return (status, body)
        except urllib.error.HTTPError as e:
            if e.code == 304 and wid in _BODY_CACHE:

                return (200, _BODY_CACHE[wid])
            return (int(e.code or 0), "")

    return fetch

_JUDGE_INSTR = (
    "Du bist ein strenger Signal-Richter fuer die Web-Ueberwachung eines Heimservers. Du bekommst den "
    "NEUEN Seitentext und eine Bedingung. Antworte NUR mit JSON "
    "{\"met\": true|false, \"evidence\": \"<woertliches Zitat aus dem NEUEN Text oder leer>\"}. "
    "'met' nur true, wenn die Bedingung im NEUEN Text eindeutig erfuellt ist. 'evidence' MUSS ein "
    "woertlicher Teilstring des NEUEN Textes sein (sonst leer). Im Zweifel: met=false.")

def _make_judge(uid):
    def judge(condition, new_text, prev_text):
        try:
            import portal_insights as pi
        except Exception:
            return {"met": False, "evidence": ""}
        data = ("BEDINGUNG:\n%s\n\nNEUER TEXT:\n%s" % (str(condition or ""), (new_text or "")[:6000]))
        try:
            ok, text = pi._run_claude(os.environ.get("PN_A2A_MODEL", "sonnet"), _JUDGE_INSTR, data)
        except Exception:
            return {"met": False, "evidence": ""}
        if not ok or not text:
            return {"met": False, "evidence": ""}
        try:
            d = json.loads(text[text.index("{"):text.rindex("}") + 1])
            return {"met": bool(d.get("met")), "evidence": str(d.get("evidence") or "")}
        except Exception:
            return {"met": False, "evidence": ""}
    return judge

def run_check(uid, watch, now=None):

    fetch = make_fetch_fn(watch)
    judge = None
    if ((watch.get("signal") or {}).get("kind")) == "llm":
        judge = _make_judge(uid)
    return engine.check_once(watch, fetch, judge_fn=judge, now=now)

def test_now(uid, wid):

    with _LOCK:
        ws = engine.load(uid, _uid_dir)
        w = _find(ws, str(wid or ""))
        if not w:
            return {"ok": False, "error": "unbekannter Wächter"}
        probe = json.loads(json.dumps(w))
    ok, why = _ssrf_ok(probe.get("url") or "")
    if not ok:
        return {"ok": False, "error": why, "health": "blocked"}
    res = run_check(uid, probe, now=time.time())
    st = probe.get("state") or {}
    return {"ok": True, "health": res.get("health"), "health_reason": st.get("health_reason") or "",
            "met": bool(res.get("met")), "evidence": (res.get("evidence") or "")[:300],
            "phase": res.get("phase"), "http_error": res.get("error"),
            "would_fire": bool(res.get("would_fire") or res.get("fired")),
            "note": "Nur Test — es wurde nichts ausgelöst und der Zustand nicht verändert."}

def _notify_body(watch, evidence, staged=False):
    lines = ["Der Web-Wächter '%s' hat ausgelöst." % (watch.get("name") or "Wächter"),
             "", "URL:    %s" % (watch.get("url") or ""),
             "Signal: %s" % ((watch.get("signal") or {}).get("kind") or "?")]
    if evidence:
        lines.append("Beleg:  %s" % (str(evidence)[:300]))
    if staged:
        lines += ["", "Aktion: Eine Agent-Session soll in deinem Auftrag handeln (z. B. ein Formular "
                  "ausfüllen). Das läuft NICHT automatisch — bitte im Portal FREIGEBEN. Ohne deine "
                  "Freigabe wird nichts Kontobezogenes ausgeführt."]
    lines += ["", "— automatische Meldung von Alert-to-Action."]
    return "\n".join(lines)

def _notify(uid, watch, evidence, staged=False):

    subject = ("[Brainbox Alert] Freigabe nötig: %s" if staged else "[Brainbox Alert] %s") % (
        watch.get("name") or "Wächter")
    body = _notify_body(watch, evidence, staged=staged)
    sent = {"bus": False, "email": False}
    ms = _CTX.get("messages_send")
    if ms:
        try:
            ms("__system__", uid, subject, body, want_email=False)
            sent["bus"] = True
        except Exception:
            pass
    mj = _CTX.get("mailjet_send"); ug = _CTX.get("user_get")
    to = None
    if ug:
        try:
            to = (ug(uid) or {}).get("email")
        except Exception:
            to = None
    if mj and to:
        try:
            ok, _detail = mj(to, subject, body)
            sent["email"] = bool(ok)
        except Exception:
            pass
    return sent

def _stage_agent(uid, watch, evidence):

    pend = {"id": "pa" + secrets.token_hex(5), "at": time.time(),
            "evidence": str(evidence or "")[:300],
            "brief": ((watch.get("action") or {}).get("agent") or {}).get("brief") or "",
            "state": "awaiting_approval"}
    watch.setdefault("pending_actions", []).append(pend)
    if len(watch["pending_actions"]) > 20:
        watch["pending_actions"] = watch["pending_actions"][-20:]
    sent = _notify(uid, watch, evidence, staged=True)
    return {"pending": pend, "notified": sent}

def dispatch_fire(uid, watch, res):

    action = watch.get("action") or {}
    kind = action.get("kind") or "notify"
    ev = res.get("evidence") or ""
    if kind == "agent_session":
        out = _stage_agent(uid, watch, ev)
        return {"kind": "agent_session", "staged": True, "notified": out.get("notified")}
    sent = _notify(uid, watch, ev, staged=False)
    return {"kind": "notify", "notified": sent}

_TICK_STARTED = False
_TICK_LOCK = threading.Lock()
_LOCK_FD = None

def _all_uids_with_watches():
    out = []
    try:
        base = pjp.USERS_DIR
        for name in os.listdir(base):
            if os.path.exists(os.path.join(base, name, "alert2action.json")):
                out.append(name)
    except Exception:
        pass
    return out

def _merge_runtime(dst, src):

    dst["state"] = src.get("state") or {}
    if src.get("pending_actions") is not None:
        dst["pending_actions"] = src.get("pending_actions")

def _tick_uid(uid, now):

    with _LOCK:
        ws = engine.load(uid, _uid_dir)
        due = []
        for w in ws:
            if not w.get("enabled", True):
                continue
            st = w.get("state") or {}
            interval = max(_MIN_INTERVAL_S, int((w.get("fetch") or {}).get("interval_s") or 900))
            if now - (st.get("last_check") or 0) < interval:
                continue
            due.append(w)
    if not due:
        return

    for w in due:
        try:
            res = run_check(uid, w, now=now)
        except Exception as e:
            w.setdefault("state", {})["health"] = "fetch_error"
            w["state"]["health_reason"] = str(e)[:200]
            continue
        if res.get("fired"):
            try:
                dispatch_fire(uid, w, res)
            except Exception:
                pass

    with _LOCK:
        ws2 = engine.load(uid, _uid_dir)
        by_id = {w.get("id"): w for w in ws2}
        for w in due:
            tgt = by_id.get(w.get("id"))
            if tgt is not None:
                _merge_runtime(tgt, w)
        _save(uid, ws2)

def tick(now=None):
    now = time.time() if now is None else now
    for uid in _all_uids_with_watches():
        try:
            _tick_uid(uid, now)
        except Exception:
            pass

def _acquire_singleton():

    global _LOCK_FD
    try:
        import fcntl
        os.makedirs(pjp.DATA_DIR, exist_ok=True)
        fd = open(os.path.join(pjp.DATA_DIR, "alert2action.tick.lock"), "w")
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _LOCK_FD = fd
        return True
    except Exception:
        return False

def start(ctx=None):

    global _TICK_STARTED, _CTX
    if isinstance(ctx, dict):
        _CTX = ctx
    with _TICK_LOCK:
        if _TICK_STARTED:
            return None
        if os.environ.get("PN_A2A_TICKER", "1") == "0":
            return None
        if not _acquire_singleton():
            return None
        _TICK_STARTED = True

    def _loop():
        time.sleep(12)
        while True:
            try:
                tick()
            except Exception:
                pass
            time.sleep(_TICK_WAKE_S)

    t = threading.Thread(target=_loop, name="pn-alert2action", daemon=True)
    t.start()
    return t

def _selftest():
    ok = True
    def ck(name, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print("  [%s] %s" % ("PASS" if cond else "FAIL", name))

    ck("block 127.0.0.1", _addr_blocked("127.0.0.1")[0])
    ck("block 10.0.0.5", _addr_blocked("10.0.0.5")[0])
    ck("block 192.168.1.23 (RFC1918 LAN)", _addr_blocked("192.168.1.23")[0])
    ck("block 169.254.1.1 (link-local)", _addr_blocked("169.254.1.1")[0])
    ck("block ::1", _addr_blocked("::1")[0])
    ck("allow 8.8.8.8", not _addr_blocked("8.8.8.8")[0])
    ck("allow 93.184.216.34 (example.com)", not _addr_blocked("93.184.216.34")[0])
    ck("localhost host blocked", _host_blocked("localhost")[0])

    w = engine.default_watch("t", "http://127.0.0.1/x")
    try:
        make_fetch_fn(w)("http://127.0.0.1/x"); threw = False
    except Exception:
        threw = True
    ck("fetch_fn wirft bei loopback", threw)

    calls = {"bus": [], "mail": []}
    global _CTX
    _CTX = {"messages_send": (lambda *a, **k: calls["bus"].append((a, k))),
            "mailjet_send": (lambda to, s, t: (calls["mail"].append((to, s)), (True, "ok"))[1]),
            "user_get": (lambda uid: {"email": "owner@example.org"})}
    wn = engine.default_watch("notify-test", "http://x")
    wn["action"] = {"kind": "notify"}
    out = dispatch_fire("owner", wn, {"fired": True, "evidence": "Anmeldung offen"})
    ck("notify -> bus gerufen", len(calls["bus"]) == 1)
    ck("notify -> mailjet_send gerufen", len(calls["mail"]) == 1 and calls["mail"][0][0] == "owner@example.org")
    ck("notify -> ehrlich beide zugestellt", out["notified"] == {"bus": True, "email": True})

    calls["bus"].clear(); calls["mail"].clear()
    wa = engine.default_watch("agent-test", "http://x")
    wa["action"] = {"kind": "agent_session", "agent": {"brief": "Formular ausfuellen"}}
    out = dispatch_fire("owner", wa, {"fired": True, "evidence": "Slot frei"})
    ck("agent_session -> staged (kein Auto-Run)", out.get("staged") is True)
    ck("agent_session -> pending_action vorgemerkt",
       len([p for p in (wa.get("pending_actions") or []) if p["state"] == "awaiting_approval"]) == 1)
    ck("agent_session -> Owner benachrichtigt (bus+mail)", len(calls["bus"]) == 1 and len(calls["mail"]) == 1)

    wc = engine.default_watch("c", "http://x")
    _apply_config(wc, signal={"kind": "bloedsinn"}, action={"kind": "haxx"})
    ck("unbekannte signal.kind -> text_contains", wc["signal"]["kind"] == "text_contains")
    ck("unbekannte action.kind -> notify", wc["action"]["kind"] == "notify")

    print("\nA2A-STORE SELFTEST:", "ALL GREEN" if ok else "FAILURES")
    return 0 if ok else 1

if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("portal_alert2action_store — Plumbing (Store/Fetch/Ticker/Actions); --selftest zum Pruefen.")
