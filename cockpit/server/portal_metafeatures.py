

import json
import os
import re
import secrets
import threading
import time

DATA_DIR = None
_chan_ctx = None
_adapter_ctx = None
_session_store = None
_prov_log = None
_traceback_log = None
_load_cfg = None
DEFAULT_PRINCIPAL = "owner"

_APPR_FILE = "approvals.json"
_CONV_FILE = "conversations.json"
_APPR_LOCK = threading.Lock()
_CONV_LOCK = threading.Lock()
_CONV_THREADS = {}
_CONV_MAX_RUNNING = 2
_CONV_MAX_TURNS_HARD = 16
_ASK_TIMEOUT_S = 420
_BC_MAX_SIDS = 8

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        g[k] = v

def _tb(where):
    try:
        if callable(_traceback_log):
            _traceback_log(where)
    except Exception:
        pass

def _path(name):
    return os.path.join(DATA_DIR, name)

def _load(name):
    try:
        with open(_path(name), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}

def _update(name, lock, fn):

    with lock:
        d = _load(name)
        fn(d)
        tmp = "%s.tmp.%d" % (_path(name), os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, _path(name))
        return d

def _bus(principal, sid, text, notify="normal", topic=None, role="system"):
    try:
        import portal_channels as _pc
        kw = {"role": role, "text": text, "notify": notify}
        if topic is not None:
            kw["topic"] = topic
        return _pc.bus_append(_chan_ctx(), principal, sid, "message", **kw)
    except Exception:
        _tb("mf bus")
        return None

def _card(principal, sid, topic, text):
    try:
        import portal_channels as _pc
        return _pc.bus_status(_chan_ctx(), principal, sid, topic, text)
    except Exception:
        _tb("mf card")
        return None

def _cell_for(owner, sid):
    try:
        import pn_cell_session as _cs
        mgr = _cs.get_manager()
        c = mgr.get(owner, sid) if mgr is not None else None
        if c is not None and c.alive():
            return c
    except Exception:
        _tb("mf cell_for")
    return None

def _last_reply(owner, sid):
    try:
        import portal_metasessions as _pm
        return _pm._meta_worker_result(owner, sid) or ""
    except Exception:
        return ""

def _ask(owner, sid, text, timeout=_ASK_TIMEOUT_S):

    c = _cell_for(owner, sid)
    if c is None:
        return None, "Die Zelle der Session %s laeuft nicht — Session zuerst wecken." % sid
    before = _last_reply(owner, sid)
    try:
        if not c.submit(text):
            return None, "Der Agent in der Zelle hat die Nachricht nicht angenommen."
    except Exception as e:
        return None, str(e)[:200]
    t0 = time.time()
    saw_busy = False
    while time.time() - t0 < timeout:
        time.sleep(6.0)
        try:
            p = c._incell_active_jsonl()
            busy = True if not p else bool(c._incell_turn_busy(p))
        except Exception:
            busy = None
        if busy:
            saw_busy = True
        if busy is False and (saw_busy or time.time() - t0 > 30):
            after = _last_reply(owner, sid)
            if after and after != before:
                return after, None
    return None, "Keine Antwort innerhalb von %d Sekunden." % int(timeout)

_KID_ESC_LOCK = threading.Lock()
_KID_ESC_SENT = set()
_KID_ESC_DEFAULT_MAIL = ""

def _cfg():
    try:
        return dict(_load_cfg() or {}) if callable(_load_cfg) else {}
    except Exception:
        return {}

def _cfg_flag(cfg, key, default=True):
    v = cfg.get(key, default)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ("0", "false", "no", "off", "aus", "nein", "")

def _kid_role(uid):

    try:
        import portal_users as _pu
        u = _pu.user_get(uid) or {}
        return u.get("role"), (u.get("name") or u.get("display") or uid)
    except Exception:
        return None, uid

def _kid_esc_telegram(text):

    try:
        import pn_chanadapter as _ca
    except Exception:
        return False, "chanadapter import failed"

    ctx = None
    try:
        if callable(_adapter_ctx):
            ctx = _adapter_ctx()
        elif callable(_chan_ctx):
            ctx = _chan_ctx()
    except Exception:
        ctx = None
    if not ctx:
        return False, "no chan ctx"
    try:
        token = _ca.get_token(ctx, DEFAULT_PRINCIPAL, "telegram")
        b = _ca.get_binding(ctx, DEFAULT_PRINCIPAL, "telegram") or {}
        chat_id = b.get("chat_id")
        if not token or not chat_id:
            return False, "owner telegram not enrolled"
        ok = _ca.TelegramTransport().send(token, chat_id, text)
        return bool(ok), ("sent" if ok else "send returned falsy")
    except Exception as e:
        return False, "telegram error: %s" % type(e).__name__

def _kid_esc_mail(to, subject, body):

    if not str(to or "").strip():
        return False, "no kid_escalation_email configured"
    try:
        import portal_email_portioneer as _ep
        ok, detail = _ep.mailjet_send(to, subject, body)
        return bool(ok), str(detail)[:200]
    except Exception as e:
        return False, "mail error: %s" % type(e).__name__

def _maybe_kid_escalation(uid, aid, question):

    role, name = _kid_role(uid)
    if role != "kid":
        return None
    cfg = _cfg()
    if not _cfg_flag(cfg, "kid_escalation_notify", True):
        return None
    with _KID_ESC_LOCK:
        if aid in _KID_ESC_SENT:
            return None
        _KID_ESC_SENT.add(aid)
        if len(_KID_ESC_SENT) > 4096:
            _KID_ESC_SENT.clear()
            _KID_ESC_SENT.add(aid)
    q = str(question or "").strip()
    tg_text = ("\U0001F476 %s braucht eine Freigabe: %s\n"
               "— im Portal unter ‚Braucht dich‘ freigeben." % (name, q[:600]))
    to = str(cfg.get("kid_escalation_email") or "").strip() or _KID_ESC_DEFAULT_MAIL
    subject = "Freigabe nötig: %s braucht dich" % name
    mail_body = ("%s hat im Brainarbeit-Portal eine Freigabe angefragt:\n\n  %s\n\n"
                 "Bitte im Portal unter ‚Braucht dich‘ entscheiden "
                 "(Genehmigung mit 2FA-Code im Cockpit)." % (name, q[:1500]))
    ok_tg, _d_tg = _kid_esc_telegram(tg_text)
    ok_mail, _d_mail = _kid_esc_mail(to, subject, mail_body)

    if _cfg_flag(cfg, "kid_escalation_nabu", False):
        try:
            import json as _json
            import urllib.request as _rq
            _req = _rq.Request("http://127.0.0.1:8098/show",
                               data=_json.dumps({"kind": "text", "text":
                                   "%s braucht eine Freigabe im Portal." % name}).encode(),
                               headers={"Content-Type": "application/json"})
            _rq.urlopen(_req, timeout=90).read()
        except Exception:
            pass
    try:
        if callable(_prov_log):
            _prov_log("kid.escalation", uid,
                      json.dumps({"aid": aid, "telegram": bool(ok_tg), "mail": bool(ok_mail)})[:200],
                      {"wire": "agent"})
    except Exception:
        pass
    return (ok_tg, ok_mail)

def ask_owner(uid, sid, question, options=None, urgent=False, kind=None, an_owner=False):

    question = str(question or "").strip()[:2000]
    if not question:
        return {"ok": False, "error": "empty", "spoken": "Keine Frage uebergeben."}
    kind = "approval" if str(kind or "").strip().lower() == "approval" else "question"
    opts = []
    if isinstance(options, (list, tuple)):
        for o in options[:6]:
            o = str(o or "").strip()[:80]
            if o:
                opts.append(o)
    aid = "q" + secrets.token_hex(4)
    rec = {"id": aid, "principal": uid, "sid": str(sid), "question": question, "options": opts,
           "kind": kind, "urgent": bool(urgent), "created": time.time(), "state": "pending",
           "answer": None, "decision": None,

           "an_owner": bool(an_owner)}
    _update(_APPR_FILE, _APPR_LOCK, lambda d: d.__setitem__(aid, rec))
    if kind == "approval":
        txt = ("\U0001F510 Freigabe-Anfrage an den Besitzer: %s\n"
               "(Genehmigung nur mit 2FA im Cockpit; Kennung %s)" % (question, aid))
    else:
        txt = "\U0001F64B Frage an den Besitzer: %s" % question
        if opts:
            txt += "\nOptionen: %s" % " | ".join(opts)
        txt += "\n(Antwort kommt als Nachricht des Besitzers zurueck; Kennung %s)" % aid
    _bus(uid, sid, txt, notify=("alert" if urgent else "normal"))
    try:
        if callable(_prov_log):
            _prov_log("agent.ask_owner", uid,
                      json.dumps({"sid": sid, "aid": aid, "kind": kind})[:200], {"wire": "agent"})
    except Exception:
        pass

    if kind == "approval":
        try:
            _maybe_kid_escalation(uid, aid, question)
        except Exception:
            _tb("mf kid_escalation")
    return {"ok": True, "aid": aid, "kind": kind, "state": "pending",
            "spoken": "Frage an den Besitzer gestellt (%s). Frage spaeter mit ask_owner_result nach "
                      "oder arbeite weiter — die Antwort kommt auch als Nachricht in deine Session." % aid}

def _kid_principals():

    try:
        import portal_users as _pu
        return [u.get("uid") for u in (_pu.user_list() or [])
                if u.get("role") == "kid" and u.get("uid")]
    except Exception:
        return []

def appr_get(uid, aid, include_kids=False):

    rec = _load(_APPR_FILE).get(str(aid or ""))
    if not rec or rec.get("state") != "pending":
        return None
    if rec.get("principal") != uid and not (include_kids and (
            rec.get("principal") in _kid_principals() or rec.get("an_owner"))):
        return None
    return rec

def appr_dismiss(uid, aid, include_kids=False):

    allowed = {uid} | (set(_kid_principals()) if include_kids else set())
    box = {}

    def _u(d):
        rec = d.get(str(aid or ""))
        if rec and rec.get("principal") in allowed and rec.get("state") == "pending":
            rec["state"] = "dismissed"
            rec["dismissed_at"] = time.time()
            box["rec"] = rec
    _update(_APPR_FILE, _APPR_LOCK, _u)
    if not box.get("rec"):
        return {"ok": False, "error": "Unbekannte oder schon entschiedene Anfrage."}
    return {"ok": True, "aid": str(aid), "state": "dismissed"}

def _appr_janitor():

    now = time.time()
    if now - _APPR_JAN_TS[0] < 3600:
        return
    _APPR_JAN_TS[0] = now

    def _u(d):
        for k in list(d.keys()):
            rec = d.get(k) or {}
            st = rec.get("state")
            if st == "pending" and now - (rec.get("created") or now) > 7 * 86400:
                rec["state"] = "expired"
                rec["expired_at"] = now
            elif st in ("answered", "dismissed", "expired"):
                done = (rec.get("answered_at") or rec.get("dismissed_at")
                        or rec.get("expired_at") or rec.get("created") or now)
                if now - done > 30 * 86400:
                    d.pop(k, None)
    _update(_APPR_FILE, _APPR_LOCK, _u)

_APPR_JAN_TS = [0.0]

def ask_owner_result(uid, sid, aid):
    rec = _load(_APPR_FILE).get(str(aid or ""))
    if not rec or rec.get("principal") != uid or rec.get("sid") != str(sid):
        return {"ok": False, "error": "unknown", "spoken": "Keine solche Frage von dieser Session."}
    return {"ok": True, "aid": rec["id"], "state": rec.get("state"), "answer": rec.get("answer"),
            "spoken": ("Antwort: %s" % rec["answer"]) if rec.get("answer")
                      else "Noch keine Antwort des Besitzers."}

def _source_title(uid, sid):

    sid = str(sid or "").strip()
    if not sid:
        return ""
    try:
        import portal_channels as _pc
        t = _pc._session_title(_chan_ctx(), uid, sid)
        if t and str(t).strip():
            return str(t).strip()
    except Exception:
        pass
    return sid

def appr_list(uid, state=None, include_kids=False):
    _appr_janitor()
    allowed = {uid} | (set(_kid_principals()) if include_kids else set())
    out = []
    for rec in _load(_APPR_FILE).values():

        if rec.get("principal") not in allowed and not (include_kids and rec.get("an_owner")):
            continue
        if state and rec.get("state") != state:
            continue

        rec["source"] = _source_title(rec.get("principal"), rec.get("sid"))
        out.append(rec)
    out.sort(key=lambda r: r.get("created") or 0, reverse=True)
    return out[:50]

def appr_answer(uid, aid, answer=None, decision=None, include_kids=False):

    answer = str(answer or "").strip()[:2000]
    decision = str(decision or "").strip().lower() or None
    allowed = {uid} | (set(_kid_principals()) if include_kids else set())
    box = {}

    def _u(d):
        rec = d.get(str(aid or ""))
        if not rec or rec.get("state") != "pending":
            return

        if rec.get("an_owner"):
            if not include_kids:
                return
        elif rec.get("principal") not in allowed:
            return
        if rec.get("kind") == "approval":
            if decision not in ("approve", "deny"):
                return
            verdict = "GENEHMIGT (2FA-verifiziert)" if decision == "approve" else "ABGELEHNT"
            rec["answer"] = verdict + ((" — " + answer) if answer else "")
            rec["decision"] = decision
        else:
            if not answer:
                return
            rec["answer"] = answer
        rec["state"] = "answered"
        rec["answered_at"] = time.time()
        box["rec"] = rec
    _update(_APPR_FILE, _APPR_LOCK, _u)
    rec = box.get("rec")
    if not rec:
        return {"ok": False, "error": "unknown_or_answered_or_bad_input"}
    delivered = False
    try:
        import portal_channels as _pc
        label = ("ENTSCHEIDUNG DES BESITZERS zu deiner Freigabe-Anfrage"
                 if rec.get("kind") == "approval" else "ANTWORT DES BESITZERS auf deine Frage")

        payload, code = _pc.session_say(_chan_ctx(), rec["principal"], {
            "sid": rec["sid"],
            "text": "%s %s (%s): %s" % (label, rec["id"], rec["question"][:120], rec["answer"])})
        delivered = bool(code == 200 and isinstance(payload, dict) and payload.get("ok"))
    except Exception:
        _tb("mf appr deliver")
    return {"ok": True, "aid": rec["id"], "decision": rec.get("decision"),
            "delivered": delivered}

def conv_known(uid, cid):
    rec = _load(_CONV_FILE).get(str(cid or ""))
    return bool(rec and rec.get("owner") == uid)

def conv_list(uid):
    out = []
    for rec in _load(_CONV_FILE).values():
        if rec.get("owner") != uid:
            continue
        row = {k: rec.get(k) for k in ("id", "title", "state", "turn", "max_turns",
                                       "a_sid", "b_sid", "created", "ended", "error")}
        row["task"] = (rec.get("task") or "")[:200]
        out.append(row)
    out.sort(key=lambda r: r.get("created") or 0, reverse=True)
    return out[:20]

def conv_get(uid, cid):
    rec = _load(_CONV_FILE).get(str(cid or ""))
    if not rec or rec.get("owner") != uid:
        return None
    return rec

def _conv_set(cid, **kw):
    def _u(d):
        rec = d.get(cid)
        if rec:
            rec.update(kw)
    return _update(_CONV_FILE, _CONV_LOCK, _u)

def conv_start(uid, a_sid, b_sid, task, title=None, max_turns=8, role_a=None, role_b=None):
    a_sid, b_sid = str(a_sid or "").strip(), str(b_sid or "").strip()
    task = str(task or "").strip()
    if not a_sid or not b_sid or a_sid == b_sid:
        return {"ok": False, "error": "Zwei VERSCHIEDENE Sessions angeben."}
    if not task:
        return {"ok": False, "error": "Kein Auftrag angegeben."}
    running = sum(1 for r in _load(_CONV_FILE).values()
                  if r.get("owner") == uid and r.get("state") == "working")
    if running >= _CONV_MAX_RUNNING:
        return {"ok": False, "error": "Maximal %d Konversationen gleichzeitig — eine beenden."
                % _CONV_MAX_RUNNING}
    for sid in (a_sid, b_sid):
        if _cell_for(uid, sid) is None:
            return {"ok": False, "error": "Die Zelle von %s laeuft nicht — Session zuerst oeffnen/wecken." % sid}
    cid = "c" + secrets.token_hex(5)
    rec = {"id": cid, "owner": uid, "title": (str(title or "").strip() or task[:60]),
           "a_sid": a_sid, "b_sid": b_sid, "task": task[:4000],
           "role_a": (str(role_a or "").strip() or
                      "Du INSTRUIERST: zerlege die Aufgabe, stelle praezise Anforderungen, pruefe "
                      "die Lieferungen kritisch und fordere Nachbesserung, bis das Deliverable steht."),
           "role_b": (str(role_b or "").strip() or
                      "Du LIEFERST: setze die Anforderungen konkret um und liefere pruefbare "
                      "Ergebnisse — keine Gegenfragen-Kaskaden, liefere den besten Stand."),
           "max_turns": max(2, min(_CONV_MAX_TURNS_HARD, int(max_turns or 8))),
           "state": "working", "turn": 0, "next": "a", "delta_free": 0,
           "created": time.time(), "ended": None, "artifact": None, "error": None,
           "last_text": None}
    _update(_CONV_FILE, _CONV_LOCK, lambda d: d.__setitem__(cid, rec))
    t = threading.Thread(target=_conv_loop, args=(cid,), name="pn-conv-%s" % cid, daemon=True)
    _CONV_THREADS[cid] = t
    t.start()
    try:
        if callable(_prov_log):
            _prov_log("conversation.start", uid,
                      json.dumps({"cid": cid, "a": a_sid, "b": b_sid})[:200], {"wire": "http"})
    except Exception:
        pass
    return {"ok": True, "cid": cid}

def conv_stop(uid, cid):
    rec = conv_get(uid, cid)
    if not rec:
        return {"ok": False, "error": "Unbekannte Konversation."}
    if rec.get("state") != "working":
        return {"ok": True, "cid": cid, "state": rec.get("state")}
    _conv_set(cid, state="stopped", ended=time.time())
    _card(uid, cid, "conv:%s" % cid, "■ Konversation gestoppt (Owner) — Turn %s/%s"
          % (rec.get("turn"), rec.get("max_turns")))
    return {"ok": True, "cid": cid, "state": "stopped"}

def _conv_frame(rec, side):
    me_role = rec["role_a"] if side == "a" else rec["role_b"]
    partner = "B" if side == "a" else "A"
    last = rec.get("last_text")
    return ("[MODERIERTE KONVERSATION %s — Turn %d/%d — du bist %s]\n"
            "Deine Rolle: %s\n"
            "Gemeinsame Aufgabe: %s\n\n"
            "%s\n\n"
            "PROTOKOLL (Pflicht): Antworte KOMPAKT und direkt an %s. Beginne mit einer Zeile "
            "'DELTA: <was du gegenueber dem letzten Stand geaendert oder bestritten hast>' "
            "(oder woertlich 'DELTA: KEINS'). Ist das Deliverable fertig, beginne stattdessen mit "
            "'FERTIG:' gefolgt vom VOLLSTAENDIGEN Deliverable. Keine Hoeflichkeitsfloskeln."
            % (rec["id"], rec["turn"] + 1, rec["max_turns"], side.upper(), me_role,
               rec["task"],
               ("%s sagte zuletzt:\n%s" % (partner, last)) if last else "(Konversationsbeginn — du eroeffnest.)",
               partner))

def _conv_loop(cid):

    try:
        while True:
            rec = _load(_CONV_FILE).get(cid)
            if not rec or rec.get("state") != "working":
                return
            uid = rec["owner"]
            side = rec.get("next") or "a"
            sid = rec["a_sid"] if side == "a" else rec["b_sid"]
            _card(uid, cid, "conv:%s" % cid, "\U0001F91D %s · Turn %d/%d · %s antwortet …"
                  % (rec["title"], rec["turn"] + 1, rec["max_turns"], side.upper()))
            reply, err = _ask(uid, sid, _conv_frame(rec, side))
            rec = _load(_CONV_FILE).get(cid)
            if not rec or rec.get("state") != "working":
                return
            if reply is None:
                _conv_set(cid, state="failed", ended=time.time(), error=err)
                _card(uid, cid, "conv:%s" % cid, "✗ Konversation fehlgeschlagen: %s" % err)
                _bus(uid, cid, "✗ Konversation '%s' fehlgeschlagen: %s" % (rec["title"], err),
                     notify="normal", topic=cid)
                return
            turn = int(rec.get("turn") or 0) + 1
            _bus(uid, cid, "[%s] %s" % (side.upper(), reply), notify="ambient", topic=cid,
                 role="assistant")
            m = re.search(r"^\s*FERTIG\s*:\s*(.*)$", reply, re.S | re.M)
            if m:
                art = (m.group(1) or reply).strip()[:8000]
                _conv_set(cid, state="completed", ended=time.time(), turn=turn, artifact=art,
                          last_text=reply[:4000])
                _card(uid, cid, "conv:%s" % cid, "✓ %s · fertig nach %d Turns" % (rec["title"], turn))
                _bus(uid, cid, "✓ Konversation '%s' abgeschlossen (%d Turns). Deliverable:\n%s"
                     % (rec["title"], turn, art[:1500]), notify="normal", topic=cid)
                return
            dm = re.search(r"^\s*DELTA\s*:\s*(.+)$", reply, re.M)
            delta_free = int(rec.get("delta_free") or 0)
            delta_free = (delta_free + 1) if (dm and dm.group(1).strip().upper().startswith("KEINS")) else \
                         (delta_free + 1 if not dm else 0)
            forced = None
            if delta_free >= 2:
                forced = "zwei Delta-freie Turns in Folge (Einigkeit ohne Fortschritt)"
            elif turn >= int(rec.get("max_turns") or 8):
                forced = "max_turns erreicht"
            if forced:
                _conv_set(cid, state="completed", ended=time.time(), turn=turn,
                          artifact=reply.strip()[:8000], last_text=reply[:4000],
                          error="force-closed: %s" % forced)
                _card(uid, cid, "conv:%s" % cid, "✓ %s · force-closed (%s) nach %d Turns"
                      % (rec["title"], forced, turn))
                _bus(uid, cid, "✓ Konversation '%s' force-closed (%s). Letzter Stand:\n%s"
                     % (rec["title"], forced, reply.strip()[:1500]), notify="normal", topic=cid)
                return
            _conv_set(cid, turn=turn, next=("b" if side == "a" else "a"),
                      delta_free=delta_free, last_text=reply[:4000])
    except Exception:
        _tb("conv loop")
        try:
            _conv_set(cid, state="failed", ended=time.time(), error="interner Fehler (Log)")
        except Exception:
            pass

def broadcast(uid, sids, text, title=None):
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "Kein Text."}
    sids = [str(s).strip() for s in (sids or []) if str(s or "").strip()][:_BC_MAX_SIDS]
    if not sids:
        return {"ok": False, "error": "Keine Sessions angegeben."}
    bid = "b" + secrets.token_hex(4)
    frame = ("[FLEET-APPELL %s vom Besitzer] %s\n\n"
             "Antworte mit GENAU EINER kompakten Status-Zeile (kein Roman)." % (bid, text))

    def _one(sid):
        reply, err = _ask(uid, sid, frame, timeout=300)
        _card(uid, sid, "broadcast:%s" % bid,
              "\U0001F4E3 Appell %s: %s" % (bid, (reply or ("keine Antwort — %s" % err))[:400]))

    for sid in sids:
        threading.Thread(target=_one, args=(sid,), name="pn-bc-%s-%s" % (bid, sid[:8]),
                         daemon=True).start()
    try:
        if callable(_prov_log):
            _prov_log("broadcast.start", uid, json.dumps({"bid": bid, "n": len(sids)})[:200],
                      {"wire": "http"})
    except Exception:
        pass
    return {"ok": True, "bid": bid, "queued": len(sids),
            "note": "Antworten erscheinen als Sticky-Karten (Topic broadcast:%s) auf den Kanaelen "
                    "der Sessions." % bid}
