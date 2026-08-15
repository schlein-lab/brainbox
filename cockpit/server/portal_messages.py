
import html as _html
import json
import os
import re
import sqlite3
import threading
import time

_LOCK = threading.Lock()
_DB = None
_ADMIN_BOX = "__admins__"
_SITE = "Brainbox"

def md_render(text):

    out = []
    in_ul = False
    for raw in (text or "").split("\n"):
        line = _html.escape(raw, quote=False)
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
        line = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", line)
        line = re.sub(r"(https?://[^\s<]+)",
                      r'<a href="\1" target="_blank" rel="noopener">\1</a>', line)
        if re.match(r"^\s*[-*]\s+", line):
            if not in_ul:
                out.append("<ul>"); in_ul = True
            out.append("<li>%s</li>" % re.sub(r"^\s*[-*]\s+", "", line))
            continue
        if in_ul:
            out.append("</ul>"); in_ul = False
        out.append(line + "<br>" if line.strip() else "")
    if in_ul:
        out.append("</ul>")
    return "\n".join(out)

_notify_email = None
_send_mail = None
_auth_token_new = None
_auth_token_consume = None
_user_list = None
_user_get = None
_user_update = None

def configure(data_dir=None, notify_email=None, auth_token_new=None,
              auth_token_consume=None, user_list=None, user_get=None,
              user_update=None, send_mail=None):
    global _DB, _notify_email, _auth_token_new, _auth_token_consume
    global _user_list, _user_get, _user_update, _send_mail
    if data_dir:
        _DB = os.path.join(data_dir, "messages.db")
    _notify_email = notify_email
    _send_mail = send_mail
    _auth_token_new = auth_token_new
    _auth_token_consume = auth_token_consume
    _user_list = user_list
    _user_get = user_get
    _user_update = user_update

def _conn():
    c = sqlite3.connect(_DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, from_uid TEXT, to_uid TEXT,
        subject TEXT DEFAULT '', body TEXT DEFAULT '', read INTEGER DEFAULT 0,
        mailed TEXT DEFAULT '')""")
    try:

        c.execute("ALTER TABLE messages ADD COLUMN thread_id INTEGER")
    except Exception:
        pass
    return c

def _row(r):
    return {"id": r[0], "ts": r[1], "from": r[2], "to": r[3],
            "subject": r[4], "body": r[5], "read": bool(r[6]), "mailed": r[7],
            "thread": r[8] or r[0]}

def _mail_one(uid, subject, body, base_url="", from_uid="", msg_id=None, parent_id=None):

    u = _user_get(uid) if _user_get else None
    if not u or not (u.get("email") or "").strip():
        return "no-email"
    if int(u.get("email_optout") or 0):
        return "optout"
    if (u.get("status") or "active") != "active":
        return "inactive"
    try:
        tok = _auth_token_new(uid, "msg-optout", email=u["email"], ttl=90 * 86400)
        off = "%s/msg/optout?token=%s" % ((base_url or "").rstrip("/"), tok) if base_url else ""
    except Exception:
        off = ""
    portal = (base_url or "").rstrip("/")
    foot_t = ("\n\n—\nDiese Nachricht kam über deine Brainbox."
              + ("\nIm Portal antworten: %s/#mail" % portal if portal else "")
              + ("\nKeine E-Mail-Kopien mehr erhalten: %s" % off if off else ""))
    foot_h = ('<hr style="border:none;border-top:1px solid #ddd;margin:16px 0">'
              '<p style="color:#777;font-size:12px">Diese Nachricht kam über deine Brainbox.'
              + (' · <a href="%s/#mail">Im Portal antworten</a>' % portal if portal else "")
              + (' · <a href="%s">Keine E-Mail-Kopien mehr</a>' % off if off else "") + "</p>")
    subj = "[%s] %s" % (_SITE, (subject or "Nachricht"))
    html = ('<div style="font:15px/1.5 system-ui,sans-serif;max-width:640px">%s%s</div>'
            % (md_render(body), foot_h))
    headers = {}
    if off:
        headers["List-Unsubscribe"] = "<%s>" % off
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    host = re.sub(r"^https?://|[:/].*$", "", portal) or "brainbox"
    if msg_id:
        headers["Message-ID"] = "<msg-%s@%s>" % (msg_id, host)
    if parent_id:
        headers["In-Reply-To"] = "<msg-%s@%s>" % (parent_id, host)
        headers["References"] = "<msg-%s@%s>" % (parent_id, host)
    reply_to = ""
    try:
        sender = _user_get(from_uid) if (from_uid and _user_get) else None
        reply_to = (sender or {}).get("email") or ""
    except Exception:
        pass
    try:
        if _send_mail is not None:
            ok, detail = _send_mail(u["email"], subj, body + foot_t, html=html,
                                    headers=headers or None, reply_to=reply_to or None)
        else:
            ok, detail = _notify_email(u["email"], subj, body + foot_t, html=html)
        return ("sent:" if ok else "fail:") + str(detail)[:120]
    except Exception as e:
        return "fail:%s" % type(e).__name__

def send(from_uid, to, subject, body, want_email=False, base_url="", reply_to=None):

    subject = (subject or "").strip()[:200]
    body = (body or "").strip()[:20000]
    if not body:
        return {"ok": False, "error": "leere Nachricht"}
    parent = None
    if reply_to:
        with _LOCK:
            c = _conn()
            try:
                r = c.execute("SELECT id,ts,from_uid,to_uid,subject,body,read,mailed,thread_id"
                              " FROM messages WHERE id=?", (int(reply_to),)).fetchone()
            finally:
                c.close()
        if r:
            parent = _row(r)
            if not subject:
                subject = parent["subject"] or ""
            if subject and not subject.lower().startswith("re:"):
                subject = "Re: " + subject

            if to not in ("all",) and parent["from"] and parent["from"] != from_uid:
                to = parent["from"]
    if to == "all":
        uids = [u["uid"] for u in (_user_list() if _user_list else [])
                if u.get("uid") and u["uid"] != from_uid]
    else:
        uids = [to]
    if not uids:
        return {"ok": False, "error": "keine Empfänger"}
    now = time.time()
    results = {}
    with _LOCK:
        c = _conn()
        try:
            for uid in uids:
                cur = c.execute(
                    "INSERT INTO messages(ts,from_uid,to_uid,subject,body,read,mailed,thread_id)"
                    " VALUES(?,?,?,?,?,0,'',?)",
                    (now, from_uid, uid, subject, body, parent["thread"] if parent else None))
                mid = cur.lastrowid
                if parent is None:
                    c.execute("UPDATE messages SET thread_id=? WHERE id=?", (mid, mid))
                mailed = ""
                if want_email and uid != _ADMIN_BOX:
                    mailed = _mail_one(uid, subject, body, base_url=base_url, from_uid=from_uid,
                                       msg_id=mid, parent_id=(parent or {}).get("id"))
                    c.execute("UPDATE messages SET mailed=? WHERE id=?", (mailed, mid))
                results[uid] = mailed or "portal"
            c.commit()
        finally:
            c.close()
    return {"ok": True, "recipients": len(uids), "delivery": results}

def inbox(uid, is_admin=False, limit=200):

    with _LOCK:
        c = _conn()
        try:
            if is_admin:
                rx = c.execute("SELECT id,ts,from_uid,to_uid,subject,body,read,mailed,thread_id"
                               " FROM messages WHERE to_uid=? OR to_uid=? ORDER BY ts DESC LIMIT ?",
                               (uid, _ADMIN_BOX, limit)).fetchall()
            else:
                rx = c.execute("SELECT id,ts,from_uid,to_uid,subject,body,read,mailed,thread_id"
                               " FROM messages WHERE to_uid=? ORDER BY ts DESC LIMIT ?", (uid, limit)).fetchall()
            tx = c.execute("SELECT id,ts,from_uid,to_uid,subject,body,read,mailed,thread_id"
                           " FROM messages WHERE from_uid=? ORDER BY ts DESC LIMIT ?", (uid, limit)).fetchall()
        finally:
            c.close()
    received = [_row(r) for r in rx]
    return {"ok": True, "received": received, "sent": [_row(r) for r in tx],
            "unread": sum(1 for m in received if not m["read"])}

def mark_read(uid, ids, is_admin=False):
    if not ids:
        return {"ok": True, "marked": 0}
    ids = [int(i) for i in ids][:500]
    ph = ",".join("?" * len(ids))
    with _LOCK:
        c = _conn()
        try:
            if is_admin:
                cur = c.execute("UPDATE messages SET read=1 WHERE id IN (%s) AND (to_uid=? OR to_uid=?)" % ph,
                                (*ids, uid, _ADMIN_BOX))
            else:
                cur = c.execute("UPDATE messages SET read=1 WHERE id IN (%s) AND to_uid=?" % ph,
                                (*ids, uid))
            c.commit()
            return {"ok": True, "marked": cur.rowcount}
        finally:
            c.close()

def optout(token):

    try:
        rec = _auth_token_consume(token, "msg-optout")
    except Exception:
        rec = None
    uid = (rec or {}).get("uid") if isinstance(rec, dict) else rec
    if not uid:
        return {"ok": False, "error": "Link ungültig oder abgelaufen"}
    try:
        _user_update(uid, email_optout=1)
    except Exception as e:
        return {"ok": False, "error": "Abmeldung fehlgeschlagen (%s)" % type(e).__name__}
    return {"ok": True, "uid": uid}
