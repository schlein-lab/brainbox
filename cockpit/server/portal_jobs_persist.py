
import os, sys, json, secrets, subprocess, time
import re, threading

HOME = os.path.expanduser("~")
DATA_DIR = os.path.join(HOME, ".local", "share", "brainbox-portal")

try:
    from durable import DurableVault as _DurableVault
except Exception:
    _DurableVault = None
try:
    import apikeys as _apikeys_mod
except Exception:
    _apikeys_mod = None

Handler = None
_cockpit_policy_enf = None
_inject = None
_portal_base_url = None
_prov_log = None
_secret_vault = None
_sesscell_reg = None
_traceback_log = None
_voice_agent_token = None
job_link = None
llm_run_core = None
pn_req = None

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

JOBS_DIR = os.path.join(DATA_DIR, "jobs")
DB_PATH = os.path.join(DATA_DIR, "jobs.db")
_DBLOCK = threading.Lock()
JOB_COLS = ["id", "created", "prompt", "email", "status", "room", "mode", "artifacts", "attachments", "log", "principal", "priority"]

JOB_PRIORITIES = ("interactive", "batch")
JOB_PRIORITY_DEFAULT = "batch"

def db():
    import sqlite3
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("""CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, created REAL, prompt TEXT,
        email TEXT, status TEXT, room TEXT, mode TEXT, artifacts TEXT, attachments TEXT, log TEXT)""")

    if "principal" not in [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]:
        conn.execute("ALTER TABLE jobs ADD COLUMN principal TEXT"); conn.commit()

    if "priority" not in [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]:
        conn.execute("ALTER TABLE jobs ADD COLUMN priority TEXT"); conn.commit()
    return conn

def job_create(prompt, email, attachments=None, mode="commission", principal=None, priority=None):
    jid = secrets.token_hex(8)
    priority = priority if priority in JOB_PRIORITIES else JOB_PRIORITY_DEFAULT
    with _DBLOCK:
        c = db(); c.execute(
            "INSERT INTO jobs(id,created,prompt,email,status,room,mode,artifacts,attachments,log,principal,priority)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (jid, time.time(), prompt, email or "", "queued", "", mode, "[]",
             json.dumps(attachments or []), "", principal, priority)); c.commit(); c.close()
    return jid

def _job_owns(job, principal, scope_all):

    return scope_all or (job is not None and job.get("principal") == principal)

def job_get(jid, principal=None, scope_all=False):
    with _DBLOCK:
        c = db(); r = c.execute("SELECT " + ",".join(JOB_COLS) + " FROM jobs WHERE id=?", (jid,)).fetchone(); c.close()
    if not r:
        return None
    d = dict(zip(JOB_COLS, r))
    d["artifacts"] = json.loads(d["artifacts"] or "[]"); d["attachments"] = json.loads(d["attachments"] or "[]")

    if principal is not None and not _job_owns(d, principal, scope_all):
        return None
    return d

def job_list(principal=None, scope_all=False):
    with _DBLOCK:
        c = db()
        q = "SELECT id,created,status,room,mode,substr(prompt,1,90),principal FROM jobs"
        if principal is not None and not scope_all:
            rows = c.execute(q + " WHERE principal=? ORDER BY created DESC LIMIT 100", (principal,)).fetchall()
        else:
            rows = c.execute(q + " ORDER BY created DESC LIMIT 100").fetchall()
        c.close()
    return [{"id": r[0], "created": r[1], "status": r[2], "room": r[3], "mode": r[4],
             "prompt": r[5], "principal": r[6]} for r in rows]

def job_update(jid, **kw):
    with _DBLOCK:
        c = db()
        for k, v in kw.items():
            if k in ("artifacts", "attachments"):
                v = json.dumps(v)
            c.execute(f"UPDATE jobs SET {k}=? WHERE id=?", (v, jid))
        c.commit(); c.close()

def job_log(jid, line):
    with _DBLOCK:
        c = db(); c.execute("UPDATE jobs SET log=COALESCE(log,'')||? WHERE id=?", (line + "\n", jid)); c.commit(); c.close()

USERS_DIR = os.path.join(DATA_DIR, "users")
LINKS_MAX = 40

_URL_RE = re.compile(rb"https?://[^\x00-\x20\"'<>`\\)\]}]+")

def _uid_safe(uid):
    return re.sub(r"[^A-Za-z0-9_.-]", "", str(uid or "owner"))[:64] or "owner"

def user_dir(uid):
    d = os.path.join(USERS_DIR, _uid_safe(uid))
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d

def _session_store(principal, kind="cockpit"):

    import portal_sessions
    return portal_sessions.SessionStore(user_dir(principal), principal, kind)

def _host_tmux_alive(tmux):
    return bool(tmux) and subprocess.run(
        ["tmux", "has-session", "-t", tmux], capture_output=True).returncode == 0

def _session_live(principal, sid, tmux):

    if _host_tmux_alive(tmux):
        return True
    try:
        import pn_cell_session as _cs
        c = _cs.get_manager().get(principal, sid)
        if c and c.alive():
            return True
    except Exception:
        pass

    try:
        reg = _sesscell_reg()
        rec = reg.get(principal, sid) if reg else None
        if rec and rec.get("state") not in ("evicted",):
            return True
    except Exception:
        pass

    try:
        if _session_store(principal, "cockpit").get(sid):
            return True
    except Exception:
        pass
    return False

_DELIVERY = {}
_DELIVERY_GUARD = threading.Lock()
_DELIVERY_MAX = 512
_DELIVERY_FAIL_TTL = 120.0

import portal_zustand as _zst
_zst.register("portal_jobs_persist._DELIVERY", "cursor", __name__, ref=_DELIVERY, ttl_s=120.0,
              beschreibung="Zustell-Hauptbuch je (principal, sid): queued|delivered|no_reply|failed (Deckel 512). Verlust => Statuspolls antworten None und der Fail-Fast-Schutz fehlt: eine Wiederholung nach gescheitertem Wecken meldet wieder ok:true (Doppelzustellungs-Risiko dieser API-Klasse)",
              neustart="verfaellt", schreiber="_delivery_set() unter _DELIVERY_GUARD")

def _delivery_set(principal, sid, state, detail=None, reason=None):

    rec = {"state": state, "ts": time.time(), "detail": detail, "reason": reason}
    with _DELIVERY_GUARD:
        if len(_DELIVERY) >= _DELIVERY_MAX and (principal, sid) not in _DELIVERY:
            for k in sorted(_DELIVERY, key=lambda k: _DELIVERY[k]["ts"])[:_DELIVERY_MAX // 4]:
                _DELIVERY.pop(k, None)
        _DELIVERY[(principal, sid)] = rec
    return rec

def delivery_status(principal, sid):

    with _DELIVERY_GUARD:
        rec = _DELIVERY.get((principal, sid))
        return dict(rec) if rec else None

def _gast_klemmt(principal, sid):
    import os as _os
    try:
        reg = _sesscell_reg() if _sesscell_reg else None
        rec = reg.get(principal, sid) if reg else None
        zelle = (rec or {}).get("cell")
        if not zelle:
            return ""
        run_dir = _os.environ.get("PN_CELL_RUN_DIR", "/tmp/pn-cells")
        pfad = _os.path.join(run_dir, zelle, "vmm.out")
        st = _os.stat(pfad)
        if time.time() - st.st_mtime > 180:
            return ""
        with open(pfad, "rb") as f:
            f.seek(max(0, st.st_size - 8192))
            schwanz = f.read().decode("utf-8", "replace")
    except Exception:
        return ""
    for muster, satz in (
            ("BUG: workqueue lockup",
             "Die Gast-Konsole meldet gerade eine verklemmte Workqueue -- "
             "die Nachricht steckt vermutlich in der Bahn fest und ist nicht angekommen."),
            ("rcu_preempt detected stalls",
             "Die Gast-Konsole meldet gerade einen RCU-Stall -- der Gast haengt, "
             "die Nachricht ist vermutlich nicht angekommen."),
            ("Out of memory",
             "Die Gast-Konsole meldet gerade Speichermangel im Gast.")):
        if muster in schwanz:
            return " " + satz
    return ""

def _delivery_blocked(principal, sid):

    rec = delivery_status(principal, sid)
    if not rec or rec.get("state") != "failed":
        return None
    if (time.time() - float(rec.get("ts") or 0)) > _DELIVERY_FAIL_TTL:
        return None
    return rec.get("detail") or "letzte Zustellung ist fehlgeschlagen"

_WAKE_LOCKS = {}
_WAKE_LOCKS_GUARD = threading.Lock()
_zst.register("portal_jobs_persist._WAKE_LOCKS", "singleton", __name__, ref=_WAKE_LOCKS,
              beschreibung="Per-Session-Serialisierer fuer Wecken+Zustellen (ein Lock je (principal, sid))",
              neustart="verfaellt", schreiber="_wake_lock()")

def _wake_lock(principal, sid):

    k = (principal, sid)
    with _WAKE_LOCKS_GUARD:
        lk = _WAKE_LOCKS.get(k)
        if lk is None:
            lk = threading.Lock(); _WAKE_LOCKS[k] = lk
        return lk

_GENERIC_TITLE_RE = re.compile(r"^(?:new session|neue session|session|chat|sitzung|\d{1,8}|[0-9a-f]{6,16})$", re.I)

def _is_generic_title(title, sid):

    t = (title or "").strip()
    if not t or t == str(sid):
        return True
    return bool(_GENERIC_TITLE_RE.match(t))

def _gen_title(user_text, reply):

    ut = re.sub(r"\s+", " ", (user_text or "")).strip()
    rp = re.sub(r"\s+", " ", (reply or "")).strip()
    fallback = ut[:48].strip() or None
    try:
        system = ("Erzeuge einen KURZEN, sprechenden Titel (3 bis 6 Woerter) fuer eine Chat-Session aus "
                  "der ersten Nutzer-Nachricht und der Antwort. Nur der Titel, KEINE Anfuehrungszeichen, "
                  "kein Satzzeichen am Ende, keine Vorrede.")
        prompt = "Nutzer: %s\nAssistent: %s\n\nTitel:" % (ut[:400], rp[:400])
        r = llm_run_core(prompt, system, "", 30)
        if r.get("ok"):
            t = (r.get("text") or "").strip().splitlines()[0].strip().strip('"“”').strip() if (r.get("text") or "").strip() else ""
            if t:
                return t[:60]
    except Exception:
        pass
    return fallback

def _autotitle_session(principal, sid, user_text, reply):

    try:
        store = _session_store(principal, "cockpit")
        rec = store.get(sid)
    except Exception:
        return
    if not rec or not _is_generic_title(rec.get("title"), sid):
        return
    title = _gen_title(user_text, reply)
    if not title or title == (rec.get("title") or "").strip():
        return
    try:
        store.rename(sid, title)
    except Exception:
        return
    try:
        import portal_channels as _pc
        _pc.bus_append(_chan_ctx(), principal, sid, "lifecycle", event="renamed", title=title)
    except Exception:
        pass

_BRIEF_GEMELDET = set()

def _deliver_to_session(principal, sid, tmux, text):

    if _host_tmux_alive(tmux):
        try:
            _inject(tmux, text)
            _delivery_set(principal, sid, "delivered", detail="host tmux")
            return True, None
        except Exception as e:
            _delivery_set(principal, sid, "failed", detail="inject(tmux): %s" % e)
            return False, "inject(tmux): %s" % e

    _blocked = _delivery_blocked(principal, sid)
    if _blocked:
        return False, _blocked

    def _wake_ask_post():
        with _wake_lock(principal, sid):
            try:
                import pn_cell_session as _cs2
                import portal_channels as _pc
                mgr = _cs2.get_manager()
                cell2 = mgr.get(principal, sid)
                if cell2 is None or not cell2.alive():
                    cell2 = mgr.ensure(principal, sid, portal_url=_portal_base_url(),
                                       portal_token=_voice_agent_token(principal),
                                       policy=_cockpit_policy_enf(principal, sid))
                if not (cell2 and cell2.alive()):
                    _why = None
                    try:
                        _why = mgr.boot_reason(principal, sid)
                    except Exception:
                        pass
                    raise RuntimeError("microVM konnte nicht starten"
                                       + (": %s" % _why if _why else ""))
                try:
                    _r = _sesscell_reg() if _sesscell_reg else None
                    if _r is not None: _r.attach(principal, sid)
                except Exception: pass

                _sys = None
                try:
                    import portal_metasessions as _pm
                    _sys = _pm._cockpit_cell_brief(principal, sid)
                except Exception as _e:

                    _sys = None
                    if sid not in _BRIEF_GEMELDET:
                        _BRIEF_GEMELDET.add(sid)
                        sys.stderr.write("[pn-chat] %s/%s: KEIN Systembrief — die Sitzung laeuft "
                                         "ungefuehrt (%s: %s)\n"
                                         % (principal, sid, type(_e).__name__, _e))
                res = cell2.ask(text, system=_sys)
                if not isinstance(res, dict):
                    res = {}
                reply = (res.get("text") or "").strip()
                if reply:
                    _delivery_set(principal, sid, "delivered", detail="cell")

                    _mdl = res.get("model")
                    if not _mdl:
                        try:
                            import portal_session_svc as _psvc
                            _mdl = _psvc.sess_model_label(_psvc._sessprov_get(principal, sid))
                        except Exception:
                            _mdl = None
                    _pc.bus_append(_chan_ctx(), principal, sid, "message", role="assistant",
                                   text=reply, model=_mdl)
                    try:

                        _pc.note_reply_delivered(principal, sid, res.get("path"), res.get("off"),
                                                 text=reply)
                    except Exception:
                        pass
                else:
                    _delivery_set(principal, sid, "no_reply")
                    _klemmt = ""
                    try:
                        _klemmt = _gast_klemmt(principal, sid)
                    except Exception:
                        _klemmt = ""
                    _text = (("(Die Session ist wach, hat aber nicht rechtzeitig "
                              "geantwortet.%s)" % _klemmt) if _klemmt else
                             "(Die Session ist wach und arbeitet noch — bei einem langen "
                             "Zug ist das normal. Ihre Antwort erscheint hier, sobald sie "
                             "fertig ist.)")
                    _pc.bus_append(_chan_ctx(), principal, sid, "message", role="system",
                                   text=_text,
                                   notify=("alert" if _klemmt else "normal"))
                try:
                    _autotitle_session(principal, sid, text, reply)
                except Exception:
                    pass
            except Exception as e:
                _delivery_set(principal, sid, "failed", detail=str(e))
                _traceback_log("cell deliver bg")
                try:
                    import portal_channels as _pc
                    _pc.bus_append(_chan_ctx(), principal, sid, "message", role="system",
                                   text="⚠️ Konnte die Session nicht wecken: %s" % e,
                                   notify="normal")
                except Exception:
                    pass

    _delivery_set(principal, sid, "queued")
    threading.Thread(target=_wake_ask_post, name="cell-wake-%s" % sid, daemon=True).start()
    return True, None

_TOPIC_CACHE = {}
_TOPIC_TTL = 30.0

def _topic_for_sid(principal, sid):

    key = (str(principal), str(sid))
    now = time.time()
    hit = _TOPIC_CACHE.get(key)
    if hit and now - hit[0] < _TOPIC_TTL:
        return hit[1]
    topic = None
    try:
        import portal_session_svc as _psvc
        prov = _psvc._sessprov_get(principal, sid) or {}
        if prov.get("meta_id"):
            tid = prov.get("meta_tid")
            topic = ("%s:%s" % (prov["meta_id"], tid)) if tid is not None else str(prov["meta_id"])
        elif prov.get("orchestrator"):
            topic = str(sid)
    except Exception:
        topic = None
    if len(_TOPIC_CACHE) > 4096:
        _TOPIC_CACHE.clear()
    _TOPIC_CACHE[key] = (now, topic)
    return topic

try:
    import portal_channels as _pc_reg
    _pc_reg.TOPIC_RESOLVER = _topic_for_sid
except Exception:
    pass

def _chan_ctx():

    import pn_cell_session as _cs
    return {
        "data_dir": DATA_DIR,
        "topic_for": _topic_for_sid,
        "inject": _inject,
        "session_store": _session_store,
        "sesscell_reg": _sesscell_reg,
        "cell_manager": _cs.get_manager,
        "session_alive": lambda t: _host_tmux_alive(t),

        "session_live": _session_live,
        "deliver": _deliver_to_session,
        "prov_log": _prov_log,
    }

def _adapter_ctx():

    import portal_channels as _pc
    ctx = _chan_ctx()
    ctx["durable_vault"] = lambda: _durable_vault
    ctx["ephemeral_vault"] = lambda: _secret_vault
    ctx["say"] = _pc.session_say
    ctx["bus_read"] = _pc.bus_read
    ctx["channels"] = None
    return ctx

try:
    from zkvault import (ZeroKnowledgeVault as _ZKVault, ConflictError as _ZKConflict,
                         TooBigError as _ZKTooBig)
    import zkvault as _zkmod
except Exception as _zke:
    sys.stderr.write("zk vault disabled: %s\n" % _zke)
    _ZKVault = _zkmod = None
    _ZKConflict = _ZKTooBig = Exception

try:
    from zkrelease import ReleaseRegistry as _RelReg
except Exception as _zre:
    sys.stderr.write("zk release disabled: %s\n" % _zre)
    _RelReg = None

try:
    from zklink import LinkRelay as _LinkRelay
except Exception as _zle:
    sys.stderr.write("zk link disabled: %s\n" % _zle)
    _LinkRelay = None

_durable_vault = None
_zkvault = None
_zkrelease = None
_zklink = None
_apikeys = None
_stores_bereit = False

def init_stores():

    global _durable_vault, _zkvault, _zkrelease, _zklink, _apikeys, _stores_bereit
    if _stores_bereit:
        return
    _stores_bereit = True
    try:
        _durable_vault = _DurableVault(DATA_DIR) if _DurableVault else None
    except Exception as _dve:
        sys.stderr.write("durable vault disabled: %s\n" % _dve)
        _durable_vault = None
    try:
        _zkvault = _ZKVault(DATA_DIR) if _ZKVault else None
    except Exception as _zke2:
        sys.stderr.write("zk vault disabled: %s\n" % _zke2)
        _zkvault = None
    try:
        _zkrelease = _RelReg() if _RelReg else None
    except Exception as _zre2:
        sys.stderr.write("zk release disabled: %s\n" % _zre2)
        _zkrelease = None
    try:
        _zklink = _LinkRelay() if _LinkRelay else None
    except Exception as _zle2:
        sys.stderr.write("zk link disabled: %s\n" % _zle2)
        _zklink = None

    try:
        _apikeys = _apikeys_mod.KeyStore(DATA_DIR) if _apikeys_mod else None
    except Exception as _ake:
        sys.stderr.write("api keys disabled: %s\n" % _ake)
        _apikeys = None

def vault_read(uid, name):

    if _durable_vault is None:
        return None
    try:
        return _durable_vault.fetch(uid, name).decode("utf-8", "replace")
    except Exception:
        return None

def vault_use(uid, name, consumer):

    if _durable_vault is None:
        return None
    try:
        return _durable_vault.use(uid, name, consumer)
    except Exception:
        return None

def tmux_session(uid, kind):

    kind = re.sub(r"[^a-z]", "", str(kind))[:12] or "shell"
    return "%s-%s" % (_uid_safe(uid), kind)

def _links_file(uid):
    return os.path.join(user_dir(uid), "links.json")

def links_load(uid):
    try:
        with open(_links_file(uid)) as f:
            return json.load(f)
    except Exception:
        return []

def links_add(uid, url, source="terminal"):

    url = (url or "").strip().rstrip(".,;:!)]}>\"'")
    if not (url.startswith("http://") or url.startswith("https://")) or len(url) > 2048:
        return
    try:
        lst = [x for x in links_load(uid) if x.get("url") != url]
        lst.insert(0, {"url": url, "ts": time.time(), "source": source})
        with open(_links_file(uid), "w") as f:
            json.dump(lst[:LINKS_MAX], f)
    except Exception:
        pass

def links_clear(uid):
    try:
        with open(_links_file(uid), "w") as f:
            json.dump([], f)
    except Exception:
        pass

PIPELINES_FILE = os.path.join(DATA_DIR, "pipelines.json")
_PLOCK = threading.Lock()

def pipelines_load():
    try:
        with open(PIPELINES_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def pipelines_save(lst):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = PIPELINES_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(lst, f, indent=2)
    os.replace(tmp, PIPELINES_FILE)

def pipeline_find(pid):
    for p in pipelines_load():
        if p.get("id") == pid:
            return p
    return None

def pipeline_add(name, typ, spec, mem=None, cls=None, owner=None):
    pid = secrets.token_hex(6)
    with _PLOCK:
        lst = pipelines_load()
        lst.append({"id": pid, "name": (name or "Pipeline").strip()[:80], "type": typ,
                    "spec": spec, "mem": mem, "class": cls, "active": False, "owner": owner,
                    "job_id": None, "created": time.time(), "last_started": None})
        pipelines_save(lst)
    return pid

def pipelines_for(principal, is_admin=False):

    return [p for p in pipelines_load()
            if is_admin or (p.get("owner") == principal)]

def pipeline_update(pid, **kw):
    with _PLOCK:
        lst = pipelines_load()
        for p in lst:
            if p.get("id") == pid:
                p.update(kw)
        pipelines_save(lst)

def pipeline_delete(pid):
    with _PLOCK:
        pipelines_save([p for p in pipelines_load() if p.get("id") != pid])

def pipeline_start(pid):

    p = pipeline_find(pid)
    if not p:
        return {"ok": False, "error": "unbekannte Pipeline"}
    if p.get("type") == "commission":
        jid = job_create(p.get("spec") or "", None, None, "commission", principal=p.get("owner"))
        pipeline_update(pid, active=True, job_id=jid, last_started=time.time())
        return {"ok": True, "kind": "commission", "job_id": jid, "link": job_link(jid)}

    return {"ok": False, "geschlossen": True, "kind": "task",
            "error": "Pipelines mit rohem Befehl sind geschlossen — auf dem Wirt wird nichts "
                     "mehr ausgefuehrt. Nutze eine commission-Pipeline (laeuft in einer Zelle)."}

def pipeline_pause(pid):

    p = pipeline_find(pid)
    if not p:
        return {"ok": False, "error": "unbekannte Pipeline"}
    cancelled = None
    if p.get("type") != "commission" and p.get("job_id") is not None:
        try:
            cancelled = pn_req({"verb": "cancel", "id": int(p["job_id"])})
        except Exception:
            cancelled = None
    pipeline_update(pid, active=False)
    return {"ok": True, "cancelled": cancelled}

def term_send(target, text):

    try:
        subprocess.run(["tmux", "has-session", "-t", target], capture_output=True, check=False)
        subprocess.run(["tmux", "new-session", "-d", "-s", target, "-x", "200", "-y", "50"],
                       capture_output=True)
        subprocess.run(["tmux", "setenv", "-g", "BROWSER", "phantom-open"], capture_output=True)
        subprocess.run(["tmux", "send-keys", "-t", target, "-l", text], capture_output=True)
        subprocess.run(["tmux", "send-keys", "-t", target, "Enter"], capture_output=True)
        return {"ok": True, "target": target}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def send_email(to, subject, body):
    cfg = Handler.cfg; user = cfg.get("email_user"); pw = cfg.get("email_pass")
    if not (user and pw and to):
        return False
    import smtplib
    from email.mime.text import MIMEText
    msg = MIMEText(body, _charset="utf-8"); msg["Subject"] = subject; msg["From"] = user; msg["To"] = to
    try:
        s = smtplib.SMTP(cfg.get("email_host") or "smtp.gmail.com", 587, timeout=30); s.starttls(); s.login(user, pw)
        s.sendmail(user, [to], msg.as_string()); s.quit(); return True
    except Exception as e:
        print("email error:", e); return False

def send_email_as(uid, to, subject, body):

    user = vault_read(uid, "gmail_user"); pw = vault_read(uid, "gmail_app_password")
    if not (user and pw and to):
        return False, "no per-user gmail creds for %s" % uid
    import smtplib
    from email.mime.text import MIMEText
    msg = MIMEText(body, _charset="utf-8"); msg["Subject"] = subject; msg["From"] = user; msg["To"] = to
    try:
        s = smtplib.SMTP("smtp.gmail.com", 587, timeout=30); s.starttls(); s.login(user, pw)
        s.sendmail(user, [to], msg.as_string()); s.quit(); return True, "sent"
    except Exception as e:
        return False, "smtp error: %s" % type(e).__name__

