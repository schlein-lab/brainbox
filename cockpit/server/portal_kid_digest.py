
import threading
import time

_chan_ctx = None
_session_store = None
portal_channels = None
user_list = None
user_get = None
messages_send = None
notify_email = None
mailjet_configured = None
cfg_live = None
_prov_log = None

_WINDOW_S = 24 * 3600
_DIGEST_FROM = "__system__"
_worker_started = False

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

def _log(msg):
    try:
        print("[kid-digest] %s" % msg)
    except Exception:
        pass

def _bus_messages(uid, cap_bytes=524288):

    if portal_channels is None or _chan_ctx is None:
        return []
    try:
        import os
        ctx = _chan_ctx()
        size = os.path.getsize(os.path.join(ctx["data_dir"], "session-bus.jsonl"))
    except Exception:
        return []
    try:
        evs, _ = portal_channels.bus_read(ctx, max(0, size - cap_bytes), principal=uid, limit=8000)
    except Exception:
        evs = []
    out = []
    for ev in evs:
        if ev.get("kind") == "message" and (ev.get("text") or "").strip():
            out.append({"sid": str(ev.get("sid") or ""), "role": str(ev.get("role") or ""),
                        "text": str(ev.get("text") or ""), "ts": float(ev.get("ts") or 0)})
    return out

def _summary_via_board_intel(uid, sid, title, wait_s):

    try:
        import portal_board_intel as _bi
    except Exception:
        return None
    if portal_channels is None or _chan_ctx is None:
        return None
    deadline = time.time() + max(0, wait_s)
    while True:
        try:
            r = _bi.session_summary(portal_channels.bus_read, _chan_ctx(), uid, sid, title) or {}
        except Exception:
            return None
        topic = (r.get("topic") or "").strip()
        status = (r.get("status") or "").strip()
        if r.get("state") == "fresh" and (topic or status):
            return " ".join(x for x in (topic, status) if x)
        if r.get("state") in ("off", "empty") or time.time() >= deadline:
            return None
        time.sleep(5)

def _fallback_line(msgs):

    tail = msgs[-3:]
    if not tail:
        return None
    who = {"user": "Kind", "assistant": "Agent", "observer": "Beobachter"}
    parts = []
    for m in tail:
        t = m["text"].strip().replace("\n", " ")
        parts.append("%s: „%s“" % (who.get(m["role"], m["role"] or "?"), t[:160]))
    return "(ohne LLM-Zusammenfassung) zuletzt — " + " · ".join(parts)

def build_digest(wait_s=60):

    kids = [u for u in (user_list() or []) if u.get("role") == "kid"]
    now = time.time()
    sections = []
    total_sessions = 0
    for k in kids:
        uid = k.get("uid") or ""
        if not uid:
            continue
        label = (k.get("name") or "").strip() or uid
        try:
            sessions = _session_store(uid, "cockpit").list() or []
        except Exception:
            sessions = []
        msgs = _bus_messages(uid)
        recent = {}
        for m in msgs:
            if m["ts"] >= now - _WINDOW_S and m["sid"]:
                recent.setdefault(m["sid"], []).append(m)
        active = [s for s in sessions
                  if (s.get("last_active") or 0) >= now - _WINDOW_S or s.get("id") in recent]
        lines = []
        for s in active:
            sid = s.get("id") or ""
            title = (s.get("title") or "").strip() or sid
            summ = _summary_via_board_intel(uid, sid, title, wait_s)
            if not summ:
                summ = _fallback_line(recent.get(sid) or [m for m in msgs if m["sid"] == sid])
            if not summ:
                summ = "aktiv, aber noch kein Gesprächsverlauf aufgezeichnet."
            la = s.get("last_active") or 0
            when = time.strftime("%H:%M", time.localtime(la)) if la else "?"
            lines.append("- %s: %s (zuletzt aktiv %s)" % (title, summ, when))
            total_sessions += 1
        if not lines:
            lines = ["- keine Session-Aktivität in den letzten 24 Stunden."]
        head = ("Was %s heute gemacht hat:" % label) if label == uid \
            else ("Was %s (%s) heute gemacht hat:" % (label, uid))
        sections.append({"uid": uid, "name": label, "sessions": len(active),
                         "text": head + "\n" + "\n".join(lines)})
    datum = time.strftime("%d.%m.%Y", time.localtime(now))
    text = ("Eltern-Digest vom %s — Aktivität der Kinder-Konten (letzte 24 Stunden)\n\n" % datum
            + "\n\n".join(s["text"] for s in sections)) if sections else ""
    return {"ok": True, "kids": len(sections), "active_sessions": total_sessions,
            "sections": sections, "text": text}

def deliver_digest(force=False, wait_s=60):

    try:
        cfgd = (cfg_live() or {}) if cfg_live else {}
    except Exception:
        cfgd = {}
    if not force and not bool(cfgd.get("kid_digest", True)):
        return {"ok": False, "disabled": True,
                "hint": "kid_digest in config.json ist abgeschaltet"}
    d = build_digest(wait_s=wait_s)
    if not d.get("kids"):
        return {"ok": True, "kids": 0, "delivered": [],
                "note": "Keine Kinder-Konten auf dieser Box — kein Digest verschickt."}
    subject = "Eltern-Digest: was die Kinder heute gemacht haben"
    delivered = []
    try:
        r = messages_send(_DIGEST_FROM, "__admins__", subject, d["text"], want_email=False)
        delivered.append("portal:__admins__" if (r or {}).get("ok") else "portal:fail")
    except Exception as e:
        delivered.append("portal:fail:%s" % type(e).__name__)
    try:
        mail_ok = bool(mailjet_configured and mailjet_configured())
    except Exception:
        mail_ok = False
    if mail_ok:
        for a in (user_list() or []):
            if a.get("role") not in ("owner", "admin"):
                continue
            if (a.get("status") or "active") != "active":
                continue
            addr = (a.get("email") or "").strip()
            if not addr:
                continue
            if int(a.get("email_optout") or 0):
                delivered.append("mail:%s:optout" % a["uid"])
                continue
            try:
                ok, detail = notify_email(addr, subject, d["text"])
                delivered.append("mail:%s:%s" % (a["uid"], "ok" if ok else "fail"))
            except Exception as e:
                delivered.append("mail:%s:fail:%s" % (a["uid"], type(e).__name__))
    else:
        delivered.append("mail:nicht-konfiguriert")
    try:
        if _prov_log:
            _prov_log("kid_digest.deliver", "owner", subject,
                      {"kids": d["kids"], "sessions": d["active_sessions"],
                       "delivered": delivered})
    except Exception:
        pass
    return {"ok": True, "kids": d["kids"], "active_sessions": d["active_sessions"],
            "delivered": delivered, "text": d["text"]}

def worker_start():

    global _worker_started
    if _worker_started:
        return
    _worker_started = True

    def _loop():
        last_fire_day = None
        while True:
            try:
                now = time.localtime()
                day = (now.tm_year, now.tm_yday)
                try:
                    hour = int(((cfg_live() or {}) if cfg_live else {}).get("kid_digest_hour", 19))
                except Exception:
                    hour = 19
                if now.tm_hour == hour and day != last_fire_day:
                    last_fire_day = day
                    r = deliver_digest(force=False, wait_s=90)
                    _log("Tages-Digest: %s" % {k: r.get(k) for k in
                                               ("ok", "kids", "delivered", "disabled", "note")})
            except Exception as e:
                _log("Tages-Digest fehlgeschlagen: %s" % type(e).__name__)
                try:
                    if _prov_log:
                        _prov_log("kid_digest.error", "owner", str(e), {})
                except Exception:
                    pass
            time.sleep(30)

    threading.Thread(target=_loop, name="pn-kid-digest", daemon=True).start()
