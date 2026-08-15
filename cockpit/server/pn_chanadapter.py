
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import threading
import time
import urllib.request

BINDINGS_NAME = "channel-bindings.json"
LOCK_NAME = "channel-bindings.lock"
SUPERVISOR_TICK_S = float(os.environ.get("PN_CHAN_SUP_S", "5.0"))
POLL_TIMEOUT_S = int(os.environ.get("PN_CHAN_POLL_S", "12"))
TG_LIMIT = 4000

TG_ABGLEICH_S = float(os.environ.get("PN_TG_ABGLEICH_S", "60"))
TG_ABGLEICH_MAX = int(os.environ.get("PN_TG_ABGLEICH_MAX", "25"))

NIE_SCHLIESSEN = ("__voice__", "__system__")

def _bpath(ctx, name):
    return os.path.join(ctx["data_dir"], name)

@contextlib.contextmanager
def _locked(ctx):
    os.makedirs(ctx["data_dir"], exist_ok=True)
    lf = open(_bpath(ctx, LOCK_NAME), "w")
    try:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        finally:
            lf.close()

def _load(ctx):
    try:
        with open(_bpath(ctx, BINDINGS_NAME)) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}

def _save(ctx, d):
    tmp = "%s.tmp.%d" % (_bpath(ctx, BINDINGS_NAME), os.getpid())
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, _bpath(ctx, BINDINGS_NAME))

def get_binding(ctx, principal, channel):
    return (_load(ctx).get(principal) or {}).get(channel) or {}

def update_binding(ctx, principal, channel, mutate):

    with _locked(ctx):
        d = _load(ctx)
        b = d.setdefault(principal, {}).setdefault(channel, {})
        mutate(b)
        _save(ctx, d)
        return dict(b)

def verbose_on(ctx, principal, channel):

    try:
        return bool(get_binding(ctx, principal, channel).get("verbose", True))
    except Exception:
        return True

def _enrolled_principals(ctx):

    out = []
    d = _load(ctx)
    for principal, chans in d.items():
        for channel, b in (chans or {}).items():
            if b.get("enrolled") and get_token(ctx, principal, channel):
                out.append((principal, channel))
    return out

def _token_key(principal, channel):
    return "%s\x00chan:%s:token" % (principal, channel)

def _dname(channel):
    return "chan:%s:token" % channel

def _durable(ctx):
    dv = ctx.get("durable_vault")
    return dv() if dv else None

def set_token(ctx, principal, channel, token):

    if isinstance(token, str):
        token = token.encode()
    dv = _durable(ctx)
    if dv is not None:
        dv.set(principal, _dname(channel), token, kind="bot_token")
        return
    v = ctx["ephemeral_vault"]()
    if v is None:
        raise RuntimeError("no vault available")
    v.store(_token_key(principal, channel), token)

def get_token(ctx, principal, channel):
    dv = _durable(ctx)
    if dv is not None:
        try:
            if dv.has(principal, _dname(channel)):
                return dv.fetch(principal, _dname(channel)).decode()
        except Exception:
            pass
    v = ctx.get("ephemeral_vault")
    v = v() if v else None
    if v is None:
        return None
    k = _token_key(principal, channel)
    try:
        return v.fetch(k).decode() if v.has(k) else None
    except Exception:
        return None

def forget_token(ctx, principal, channel):
    dv = _durable(ctx)
    if dv is not None:
        with contextlib.suppress(Exception):
            dv.delete(principal, _dname(channel))
    v = ctx.get("ephemeral_vault")
    v = v() if v else None
    with contextlib.suppress(Exception):
        if v is not None and hasattr(v, "_secrets"):
            v._secrets.pop(_token_key(principal, channel), None)

class Transport:

    name = "base"
    supports_topics = False

    def validate(self, token):
        return {"ok": False, "error": "not implemented"}

    def poll(self, token, offset, timeout):
        return [], offset

    def send(self, token, chat_id, text, thread_id=None):
        return False

    def ensure_topic(self, token, chat_id, name):
        return None

    def rename_topic(self, token, chat_id, thread_id, name):
        return False

    def close_topic(self, token, chat_id, thread_id):
        return "weg"

    def reopen_topic(self, token, chat_id, thread_id):

        return "weg"

    def delete_topic(self, token, chat_id, thread_id):

        return "weg"

def _http_json(url, payload, timeout):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(1 << 20).decode("utf-8", "replace"))

def _chunks(text, n=TG_LIMIT):
    text = text or ""
    return [text[i:i + n] for i in range(0, len(text), n)] or [""]

class TelegramTransport(Transport):
    name = "telegram"
    supports_topics = True
    API = "https://api.telegram.org/bot%s/%s"

    def _call(self, token, method, params, timeout=15):
        try:
            res = _http_json(self.API % (token, method), params, timeout)
            return res if res.get("ok") else None
        except Exception:
            return None

    def validate(self, token):
        r = self._call(token, "getMe", {}, timeout=10)
        if not r:
            return {"ok": False, "error": "getMe failed (bad token or no net)"}
        u = r.get("result") or {}
        return {"ok": True, "username": u.get("username"), "id": u.get("id")}

    def poll(self, token, offset, timeout):
        r = self._call(token, "getUpdates",
                       {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]},
                       timeout=timeout + 8)
        if not r:
            return [], offset
        msgs, new_off = [], offset
        for upd in r.get("result") or []:
            new_off = max(new_off, int(upd.get("update_id", 0)) + 1)
            m = upd.get("message") or {}
            chat = m.get("chat") or {}
            base = {"chat_id": chat.get("id"), "chat_type": chat.get("type"),
                    "thread_id": m.get("message_thread_id"), "from": (m.get("from") or {}).get("id")}
            ftc = m.get("forum_topic_created")
            if ftc:
                msgs.append(dict(base, topic_created=ftc.get("name") or ""))
                continue
            text = m.get("text")
            if text is None:
                continue
            msgs.append(dict(base, text=text))
        return msgs, new_off

    def send(self, token, chat_id, text, thread_id=None):
        ok = True
        for part in _chunks(text):
            p = {"chat_id": chat_id, "text": part, "disable_web_page_preview": True}
            if thread_id:
                p["message_thread_id"] = thread_id
            ok = bool(self._call(token, "sendMessage", p)) and ok
        return ok

    def ensure_topic(self, token, chat_id, name):
        r = self._call(token, "createForumTopic", {"chat_id": chat_id, "name": (name or "session")[:120]})
        if not r:
            return None
        return (r.get("result") or {}).get("message_thread_id")

    def rename_topic(self, token, chat_id, thread_id, name):
        try:
            res = _http_json(self.API % (token, "editForumTopic"),
                             {"chat_id": chat_id, "message_thread_id": thread_id, "name": (name or "")[:120]}, 15)
        except Exception:
            return False
        if res.get("ok"):
            return True

        return "NOT_MODIFIED" in (res.get("description") or "").upper()

    _SCHON_SO = ("NOT_MODIFIED", "TOPIC_CLOSED", "TOPIC_NOT_MODIFIED", "ALREADY")
    _GIBT_ES_NICHT = ("TOPIC_ID_INVALID", "MESSAGE_THREAD_NOT_FOUND", "THREAD NOT FOUND",
                      "TOPIC_DELETED", "MESSAGE THREAD NOT FOUND")

    def _topic_call(self, token, methode, chat_id, thread_id):

        try:
            res = _http_json(self.API % (token, methode),
                             {"chat_id": chat_id, "message_thread_id": thread_id}, 15)
        except Exception:
            return "spaeter"
        if res.get("ok"):
            return "ok"
        d = (res.get("description") or "").upper()
        if any(m in d for m in self._SCHON_SO):
            return "schon"
        if any(m in d for m in self._GIBT_ES_NICHT):
            return "weg"
        return "spaeter"

    def close_topic(self, token, chat_id, thread_id):
        return self._topic_call(token, "closeForumTopic", chat_id, thread_id)

    def reopen_topic(self, token, chat_id, thread_id):
        return self._topic_call(token, "reopenForumTopic", chat_id, thread_id)

    def delete_topic(self, token, chat_id, thread_id):

        if int(thread_id or 0) <= 1:
            return "weg"
        return self._topic_call(token, "deleteForumTopic", chat_id, thread_id)

class SlackTransport(Transport):

    name = "slack"
    supports_topics = False
    API = "https://slack.com/api/%s"

    def _call(self, token, method, params, timeout=15):
        try:
            data = json.dumps(params).encode()
            req = urllib.request.Request(self.API % method, data=data, method="POST",
                                         headers={"Content-Type": "application/json; charset=utf-8",
                                                  "Authorization": "Bearer %s" % token})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                res = json.loads(r.read(1 << 20).decode("utf-8", "replace"))
            return res if res.get("ok") else None
        except Exception:
            return None

    def validate(self, token):
        r = self._call(token, "auth.test", {}, timeout=10)
        if not r:
            return {"ok": False, "error": "auth.test failed"}
        return {"ok": True, "username": r.get("user"), "id": r.get("user_id")}

    def send(self, token, chat_id, text, thread_id=None):
        ok = True
        for part in _chunks(text):
            p = {"channel": chat_id, "text": part}
            if thread_id:
                p["thread_ts"] = thread_id
            ok = bool(self._call(token, "chat.postMessage", p)) and ok
        return ok

class WhatsAppTransport(Transport):

    name = "whatsapp"
    supports_topics = False
    API = "https://graph.facebook.com/v21.0/%s/messages"

    def send(self, token, chat_id, text, thread_id=None):

        try:
            phone_id, to = str(chat_id).split(":", 1)
        except ValueError:
            return False
        ok = True
        for part in _chunks(text):
            try:
                _http_json_auth(self.API % phone_id,
                                {"messaging_product": "whatsapp", "to": to,
                                 "type": "text", "text": {"body": part}}, token, 15)
            except Exception:
                ok = False
        return ok

    def validate(self, token):
        return {"ok": True, "username": "whatsapp-cloud"}

def _http_json_auth(url, payload, bearer, timeout):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer %s" % bearer})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(1 << 20).decode("utf-8", "replace"))

def default_channels():
    return {"telegram": TelegramTransport(), "slack": SlackTransport(), "whatsapp": WhatsAppTransport()}

def enroll(ctx, principal, channel, creds):

    tp = (ctx.get("channels") or default_channels()).get(channel)
    if tp is None:
        return {"ok": False, "error": "unknown channel %r" % channel}, 400
    token = (creds or {}).get("token")
    if not isinstance(token, str) or len(token) < 8:
        return {"ok": False, "error": "token required"}, 400
    v = tp.validate(token)
    if not v.get("ok"):
        return {"ok": False, "error": "token rejected: %s" % v.get("error")}, 400
    try:
        set_token(ctx, principal, channel, token)
    except Exception as e:
        return {"ok": False, "error": "vault: %s" % e}, 503
    meta = {"enrolled": True, "username": v.get("username")}
    if channel == "whatsapp" and (creds or {}).get("phone_id"):
        meta["phone_id"] = creds["phone_id"]
    update_binding(ctx, principal, channel, lambda b: b.update(meta))
    with contextlib.suppress(Exception):
        if ctx.get("prov_log"):
            ctx["prov_log"]("channel.enroll", principal, channel, {"username": v.get("username")})
    return {"ok": True, "channel": channel, "username": v.get("username"),
            "hint": "Bot in deine Forum-Gruppe legen und dort /bind senden"}, 200

def set_chat(ctx, principal, channel, chat_id, inbound=None):

    def _m(b):
        b["chat_id"] = chat_id
        if inbound is not None:
            b["inbound"] = bool(inbound)
    update_binding(ctx, principal, channel, _m)
    return {"ok": True, "chat_id": chat_id, "inbound": inbound}, 200

def disable(ctx, principal, channel):

    forget_token(ctx, principal, channel)
    update_binding(ctx, principal, channel, lambda b: b.update({"enrolled": False}))
    return {"ok": True}, 200

def status(ctx, principal):

    d = (_load(ctx).get(principal) or {})
    out = {}
    for channel in default_channels():
        b = d.get(channel) or {}
        out[channel] = {
            "enrolled": bool(b.get("enrolled")),
            "live_token": bool(get_token(ctx, principal, channel)),
            "chat_bound": bool(b.get("chat_id")),
            "username": b.get("username"),
            "topics": len(b.get("topics") or {}),
            "verbose": bool(b.get("verbose", True)),
        }
    return {"ok": True, "channels": out}, 200

def set_verbose(ctx, principal, channel, on):

    channel = str(channel or "telegram")
    b = update_binding(ctx, principal, channel, lambda bb: bb.__setitem__("verbose", bool(on)))
    return {"ok": True, "channel": channel, "verbose": bool(b.get("verbose"))}, 200

def _adopt_topic_as_session(ctx, principal, channel, b, tid):

    name = ((b.get("pending_topics") or {}).get(tid) or "").strip()
    try:
        store = ctx["session_store"](principal, "cockpit")
        rec = store.create(title=name or None)
        sid = rec["id"]
    except Exception:
        return None

    def _mut(bb):
        bb.setdefault("topics", {})[sid] = int(tid)
        bb.setdefault("rev", {})[str(tid)] = sid
        if name:
            bb.setdefault("names", {})[str(tid)] = name
        (bb.get("pending_topics") or {}).pop(tid, None)
    update_binding(ctx, principal, channel, _mut)
    return sid

def _handle_inbound(ctx, principal, channel, tp, token, msg):

    b = get_binding(ctx, principal, channel)

    if msg.get("topic_created") is not None and msg.get("thread_id"):
        if b.get("chat_id") and msg.get("chat_id") == b.get("chat_id"):
            update_binding(ctx, principal, channel,
                           lambda bb, k=str(msg["thread_id"]), n=msg.get("topic_created"):
                           bb.setdefault("pending_topics", {}).__setitem__(k, n))
        return
    text = (msg.get("text") or "").strip()
    first = text.split()[0].split("@")[0] if text else ""
    if first == "/bind":
        set_chat(ctx, principal, channel, msg.get("chat_id"))
        tp.send(token, msg.get("chat_id"), "✅ Gebunden. Deine Sessions erscheinen hier als Themen.")
        return
    if first in ("/start", "/help"):
        tp.send(token, msg.get("chat_id"),
                "Brainarbeit: jede Session = ein Thema. Schreib in ein Thema (auch ein neu erstelltes!), "
                "um mit einer Session zu reden. /bind bindet diese Gruppe.")
        return

    if b.get("chat_id") and msg.get("chat_id") == b.get("chat_id") and msg.get("thread_id"):
        tid = str(msg.get("thread_id"))
        sid = (b.get("rev") or {}).get(tid)
        if not sid:
            sid = _adopt_topic_as_session(ctx, principal, channel, b, tid)
            if not sid:
                tp.send(token, b["chat_id"], "⚠️ konnte keine Session anlegen", thread_id=msg.get("thread_id"))
                return
        if not text:
            return
        payload, code = ctx["say"](ctx, principal, {"sid": sid, "text": text, "origin": channel})
        if code != 200:
            tp.send(token, b["chat_id"], "⚠️ konnte nicht zustellen: %s" % payload.get("error"),
                    thread_id=msg.get("thread_id"))

def _best_topic_name(ctx, principal, sid, ev_title=None):

    name = (ev_title or "").strip()
    if not name:
        try:
            import portal_channels as _pc
            name = (_pc._session_title(ctx, principal, sid) or "").strip()
        except Exception:
            name = ""
    if not name or name == str(sid):
        name = "Session %s" % str(sid)[:6]
    return name[:120]

def thema_erwuenscht(sid):

    try:
        import pn_mediashare as _ms
        p = _ms.parent_of(sid) if callable(getattr(_ms, "parent_of", None)) else None
    except Exception:
        return True
    return not (p and str(p) != str(sid))

def _topic_for(ctx, principal, channel, tp, token, chat_id, sid, title):

    b = get_binding(ctx, principal, channel)
    topics = b.get("topics") or {}
    name = _best_topic_name(ctx, principal, sid, title)
    if sid in topics:
        tid = topics[sid]
        if tp.supports_topics and (b.get("names") or {}).get(str(tid)) != name:
            if tp.rename_topic(token, chat_id, tid, name):
                update_binding(ctx, principal, channel,
                               lambda bb: bb.setdefault("names", {}).__setitem__(str(tid), name))
        return tid
    if not tp.supports_topics:
        return None
    tid = tp.ensure_topic(token, chat_id, name)
    if tid is None:
        return None

    def _mut(bb):
        bb.setdefault("topics", {})[sid] = tid
        bb.setdefault("rev", {})[str(tid)] = sid
        bb.setdefault("names", {})[str(tid)] = name
    update_binding(ctx, principal, channel, _mut)
    return tid

def _handle_event(ctx, principal, channel, tp, token, chat_id, ev):

    sid = ev.get("sid")

    if tp.supports_topics and not thema_erwuenscht(sid):
        b0 = get_binding(ctx, principal, channel)
        if str(sid) not in (b0.get("topics") or {}):
            return True
    if ev.get("kind") == "lifecycle":
        event = ev.get("event")
        if event in ("provisioned", "reprovisioned"):
            tid = _topic_for(ctx, principal, channel, tp, token, chat_id, sid, ev.get("title"))
            return not (tp.supports_topics and tid is None)
        if event == "renamed" and tp.supports_topics:
            tid = (get_binding(ctx, principal, channel).get("topics") or {}).get(sid)
            if tid:
                name = _best_topic_name(ctx, principal, sid, ev.get("title"))
                if tp.rename_topic(token, chat_id, tid, name):
                    update_binding(ctx, principal, channel,
                                   lambda bb: bb.setdefault("names", {}).__setitem__(str(tid), name))
        elif event == "evicted" and tp.supports_topics:
            tid = (get_binding(ctx, principal, channel).get("topics") or {}).get(sid)
            if tid:
                tp.send(token, chat_id, "— Session beendet (evicted) —", thread_id=tid)
                tp.close_topic(token, chat_id, tid)

        elif event in ("archived", "unarchived") and tp.supports_topics:

            _tg_thema_setzen(ctx, principal, channel, tp, token, chat_id, sid,
                             event == "archived", ev.get("title"))
        return True

    if ev.get("origin") == channel:
        return True
    if ev.get("kind") == "edit":
        return True
    if ev.get("notify") == "ambient" and not verbose_on(ctx, principal, channel):
        return True

    text = ev.get("text")
    if not text:
        return True
    tid = _topic_for(ctx, principal, channel, tp, token, chat_id, sid, ev.get("title"))
    if tp.supports_topics and tid is None:
        return False
    if ev.get("role") == "user":
        text = "🗣 %s" % text
    prefix = "" if tp.supports_topics else "[%s] " % (str(sid)[:8])
    tp.send(token, chat_id, prefix + text, thread_id=tid)
    return True

def _tg_thema_setzen(ctx, principal, channel, tp, token, chat_id, sid, zu, titel=None):

    b = get_binding(ctx, principal, channel)
    tid = (b.get("topics") or {}).get(str(sid))
    if not tid:
        return False

    if not zu and not (b.get("closed") or {}).get(str(sid)):
        return False
    try:
        if zu:

            lage = tp.delete_topic(token, chat_id, tid)
            if lage == "spaeter":

                lage = tp.close_topic(token, chat_id, tid)
            elif lage == "ok":
                lage = "weg"
        else:
            lage = tp.reopen_topic(token, chat_id, tid)
            if lage == "ok":
                tp.send(token, chat_id, "↩ Wiederhergestellt — die Sitzung ist wieder aktiv.",
                        thread_id=tid)
    except Exception:
        lage = "spaeter"
    if lage == "spaeter":
        return False
    if lage == "weg":

        def _park(bb, k=str(sid), t=tid):
            bb.setdefault("topics_geparkt", {})[k] = {
                "thread_id": t, "ts": time.time(),
                "grund": ("beim Archivieren geloescht (Owner 01.08.: Telegram ist nur Kommunikation)"
                          if zu else "Thema in Telegram nicht mehr vorhanden")}
            (bb.get("topics") or {}).pop(k, None)
            (bb.get("rev") or {}).pop(str(t), None)
        update_binding(ctx, principal, channel, _park)
        return True
    update_binding(ctx, principal, channel,
                   lambda bb, k=str(sid), v=bool(zu): bb.setdefault("closed", {}).__setitem__(k, v))
    return True

def _archiv_haken(ctx, principal):

    try:
        import portal_jobs_persist as _pjp
        return {str(s.get("id")): bool(s.get("archived"))
                for s in _pjp._session_store(principal).list()
                if s.get("state") != "deleted"}
    except Exception:
        return None

def _reconcile_topic_states(ctx, principal, channel, tp, token, chat_id, state):

    if not tp.supports_topics:
        return
    jetzt = time.time()
    if jetzt - float(state.get("states_reconciled_at") or 0) < TG_ABGLEICH_S:
        return
    state["states_reconciled_at"] = jetzt
    soll = _archiv_haken(ctx, principal)
    if soll is None:
        return
    b = get_binding(ctx, principal, channel)
    ist = b.get("closed") or {}
    getan = 0
    for sid in list(b.get("topics") or {}):
        if str(sid) in NIE_SCHLIESSEN:
            continue
        wunsch = soll.get(str(sid))
        if wunsch is None:
            continue

        if not wunsch and not ist.get(str(sid)):
            continue
        if _tg_thema_setzen(ctx, principal, channel, tp, token, chat_id, sid, wunsch):
            getan += 1
            if getan >= TG_ABGLEICH_MAX:
                break

def _reconcile_topic_names(ctx, principal, channel, tp, token, chat_id):

    b = get_binding(ctx, principal, channel)
    for sid, tid in list((b.get("topics") or {}).items()):
        name = _best_topic_name(ctx, principal, sid, None)
        if (b.get("names") or {}).get(str(tid)) == name:
            continue
        with contextlib.suppress(Exception):
            if tp.rename_topic(token, chat_id, tid, name):
                update_binding(ctx, principal, channel,
                               lambda bb, t=str(tid), n=name: bb.setdefault("names", {}).__setitem__(t, n))

def worker_pass(ctx, principal, channel, tp, state):

    token = get_token(ctx, principal, channel)
    if not token:
        return
    b = get_binding(ctx, principal, channel)

    if not state.get("names_reconciled") and tp.supports_topics and b.get("chat_id"):
        _reconcile_topic_names(ctx, principal, channel, tp, token, b.get("chat_id"))
        state["names_reconciled"] = True
    if tp.supports_topics and b.get("chat_id"):
        with contextlib.suppress(Exception):
            _reconcile_topic_states(ctx, principal, channel, tp, token, b.get("chat_id"), state)

    if b.get("inbound", True):
        with contextlib.suppress(Exception):
            msgs, new_off = tp.poll(token, int(b.get("offset", 0)), POLL_TIMEOUT_S)
            if new_off != b.get("offset"):
                update_binding(ctx, principal, channel, lambda bb: bb.update({"offset": new_off}))
            for m in msgs:
                _handle_inbound(ctx, principal, channel, tp, token, m)

    b = get_binding(ctx, principal, channel)
    chat_id = b.get("chat_id")
    if not chat_id:
        return
    off = int(b.get("bus_off", 0))
    for _ in range(500):
        try:
            events, nxt = ctx["bus_read"](ctx, off, principal=principal, limit=1)
        except Exception:
            break
        if not events:
            break
        try:
            ok = _handle_event(ctx, principal, channel, tp, token, chat_id, events[0])
        except Exception:
            ok = False
        if not ok:
            break
        off = nxt
        update_binding(ctx, principal, channel, lambda bb, o=off: bb.update({"bus_off": o}))

_SUP = {"thread": None, "workers": {}, "stop": {}}

def _worker_loop(ctx, principal, channel, tp):
    key = "%s/%s" % (principal, channel)
    state = {}
    while not _SUP["stop"].get(key):
        worker_pass(ctx, principal, channel, tp, state)
        time.sleep(0.5)

def _supervise_once(ctx):
    channels = ctx.get("channels") or default_channels()
    want = set(_enrolled_principals(ctx))

    for principal, channel in want:
        key = "%s/%s" % (principal, channel)
        t = _SUP["workers"].get(key)
        if t is not None and t.is_alive():
            continue
        tp = channels.get(channel)
        if tp is None:
            continue
        _SUP["stop"][key] = False
        th = threading.Thread(target=_worker_loop, args=(ctx, principal, channel, tp),
                              name="pn-chan-%s" % key, daemon=True)
        th.start()
        _SUP["workers"][key] = th

    for key, th in list(_SUP["workers"].items()):
        principal, channel = key.split("/", 1)
        if (principal, channel) not in want:
            _SUP["stop"][key] = True
            _SUP["workers"].pop(key, None)

def start_adapter(ctx):

    if ctx.get("channels") is None:
        ctx["channels"] = default_channels()
    if _SUP["thread"] is not None and _SUP["thread"].is_alive():
        return _SUP["thread"]

    def _loop():
        while True:
            with contextlib.suppress(Exception):
                _supervise_once(ctx)
            time.sleep(SUPERVISOR_TICK_S)

    t = threading.Thread(target=_loop, name="pn-chan-supervisor", daemon=True)
    t.start()
    _SUP["thread"] = t
    return t

def _selftest():
    import tempfile
    ok = True

    def ck(n, c):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", n))

    d = tempfile.mkdtemp()

    class FakeVault:
        def __init__(self): self._secrets = {}
        def store(self, k, v): self._secrets[k] = v
        def has(self, k): return k in self._secrets
        def fetch(self, k): return self._secrets[k]

    class FakeTG(Transport):
        name = "telegram"; supports_topics = True
        def __init__(self): self.sent = []; self.topics = 0; self.inbox = []
        def validate(self, token): return {"ok": True, "username": "fakebot"}
        def poll(self, token, offset, timeout):
            ms = self.inbox; self.inbox = []
            return ms, offset + len(ms)
        def send(self, token, chat_id, text, thread_id=None):
            self.sent.append((chat_id, thread_id, text)); return True
        def ensure_topic(self, token, chat_id, name):
            self.sent.append((chat_id, None, "[topic:%s]" % name)); self.topics += 1; return 100 + self.topics

    tg = FakeTG()
    vault = FakeVault()
    said = []
    import portal_channels as pc

    class _FakeStore:
        def __init__(self): self._s = {}; self._n = 0
        def create(self, title=None, sid=None):
            self._n += 1; _sid = sid or ("new%d" % self._n)
            self._s[_sid] = {"id": _sid, "title": (title or "New session"), "tmux": "t-" + _sid}
            return self._s[_sid]
        def get(self, sid): return self._s.get(sid)
        def rename(self, sid, title):
            if sid in self._s: self._s[sid]["title"] = title
            return self._s.get(sid)
    _stores = {}
    def _mk_store(principal, kind="cockpit"):
        return _stores.setdefault((principal, kind), _FakeStore())

    ctx = {
        "data_dir": d,
        "ephemeral_vault": lambda: vault,
        "channels": {"telegram": tg},
        "session_store": _mk_store,
        "prov_log": lambda *a, **k: None,

        "say": lambda ctx, principal, body: (said.append((principal, body["sid"], body["text"], body.get("origin"))) or
                                             (pc.bus_append(ctx, principal, body["sid"], "message",
                                                            role="user", text=body["text"], origin=body.get("origin")) and None) or
                                             ({"ok": True}, 200)),
        "bus_read": pc.bus_read,
    }

    p, c = enroll(ctx, "alice", "telegram", {"token": "abc12345"})
    ck("enroll ok + token in RAM vault", c == 200 and get_token(ctx, "alice", "telegram") == "abc12345")
    enroll(ctx, "bob", "telegram", {"token": "zzz99999"})
    ck("two principals enrolled", set(_enrolled_principals(ctx)) == {("alice", "telegram"), ("bob", "telegram")})

    pc.bus_append(ctx, "alice", "s1", "lifecycle", event="provisioned", title="Build X")
    pc.bus_append(ctx, "alice", "s1", "message", role="assistant", text="Hallo von der Session")
    worker_pass(ctx, "alice", "telegram", tg, {})
    ck("no delivery before /bind", tg.sent == [] and get_binding(ctx, "alice", "telegram").get("bus_off", 0) == 0)

    tg.inbox = [{"chat_id": -100, "chat_type": "supergroup", "thread_id": None, "text": "/bind", "from": 1}]
    worker_pass(ctx, "alice", "telegram", tg, {})
    ck("bind set chat_id", get_binding(ctx, "alice", "telegram").get("chat_id") == -100)

    worker_pass(ctx, "alice", "telegram", tg, {})
    delivered = [s for s in tg.sent if s[2] == "Hallo von der Session"]
    ck("assistant text delivered to a topic", len(delivered) == 1 and delivered[0][1] == 101)
    ck("exactly one topic created", tg.topics == 1)

    tg.inbox = [{"chat_id": -100, "chat_type": "supergroup", "thread_id": 101, "text": "mach weiter", "from": 1}]
    worker_pass(ctx, "alice", "telegram", tg, {})
    ck("topic message -> session_say", ("alice", "s1", "mach weiter", "telegram") in said)

    tg.inbox = [
        {"chat_id": -100, "chat_type": "supergroup", "thread_id": 777, "topic_created": "Steuer 2024", "from": 1},
        {"chat_id": -100, "chat_type": "supergroup", "thread_id": 777, "text": "hilf mir bei X", "from": 1},
    ]
    worker_pass(ctx, "alice", "telegram", tg, {})
    _new = (get_binding(ctx, "alice", "telegram").get("rev") or {}).get("777")
    ck("new topic -> new session minted", bool(_new))
    ck("new session titled after the topic", bool(_new) and _mk_store("alice").get(_new).get("title") == "Steuer 2024")
    ck("new-topic message routed to the new session",
       any(s[0] == "alice" and s[1] == _new and s[2] == "hilf mir bei X" for s in said))

    tg.inbox = [{"chat_id": -999, "chat_type": "supergroup", "thread_id": 5, "text": "hi", "from": 2}]
    _before_sids = set((get_binding(ctx, "alice", "telegram").get("rev") or {}).keys())
    worker_pass(ctx, "alice", "telegram", tg, {})
    ck("foreign-chat message mints nothing",
       set((get_binding(ctx, "alice", "telegram").get("rev") or {}).keys()) == _before_sids)

    before = len(tg.sent)
    worker_pass(ctx, "alice", "telegram", tg, {})
    echoed = [s for s in tg.sent[before:] if "mach weiter" in s[2]]
    ck("no echo of own telegram message", echoed == [])

    tg2 = FakeTG()
    ctx2 = dict(ctx); ctx2["channels"] = {"telegram": tg2}
    set_chat(ctx2, "bob", "telegram", -200)
    worker_pass(ctx2, "bob", "telegram", tg2, {})
    leaked = [s for s in tg2.sent if "Session" in s[2] or "weiter" in s[2]]
    ck("bob's chat gets NONE of alice's messages", leaked == [])

    class FlakyTG(FakeTG):
        def __init__(self): super().__init__(); self.allow = False
        def ensure_topic(self, token, chat_id, name):
            return super().ensure_topic(token, chat_id, name) if self.allow else None
    ftg = FlakyTG()
    ctx3 = dict(ctx); ctx3["channels"] = {"telegram": ftg}
    enroll(ctx3, "carol", "telegram", {"token": "ccc12345"})
    set_chat(ctx3, "carol", "telegram", -300)
    pc.bus_append(ctx3, "carol", "c1", "lifecycle", event="provisioned", title="Carol job")
    pc.bus_append(ctx3, "carol", "c1", "message", role="assistant", text="carol-reply")
    worker_pass(ctx3, "carol", "telegram", ftg, {})
    ck("blocked while bot lacks topic right",
       get_binding(ctx3, "carol", "telegram").get("bus_off", 0) == 0 and ftg.topics == 0)
    ftg.allow = True
    worker_pass(ctx3, "carol", "telegram", ftg, {})
    ck("self-heals once right granted (topic + msg + cursor advances)",
       ftg.topics == 1 and any(s[2] == "carol-reply" for s in ftg.sent)
       and get_binding(ctx3, "carol", "telegram").get("bus_off", 0) > 0)

    print("\nSELFTEST:", "ALL GREEN" if ok else "FAILURES")
    return 0 if ok else 1

if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("pn_chanadapter — import me; --selftest to verify.")
