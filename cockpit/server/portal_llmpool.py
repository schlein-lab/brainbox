
import os, sys, json, hashlib, subprocess, time
import re, threading, shutil
import shutil as _shutil

HOME = os.path.expanduser("~")
DATA_DIR = os.path.join(HOME, ".local", "share", "brainbox-portal")
DEFAULT_PRINCIPAL = "owner"
JOBS_DIR = os.path.join(DATA_DIR, "jobs")

PN_BIN = None
PORTAL_BIN = None
PR = None
_DBLOCK = None
_LLMPOOL = None
_cell_firefox_procs = None
_present_firefox = None
_prov_log = None
_spawn_marionette_firefox = None
db = None
job_get = None
job_link = None
job_log = None
job_update = None
mailjet_sender = None
notify_email = None
pn_available = None
room_phase = None
seat_ctl = None
seat_running = None
seat_start = None
system_secret = None
user_get = None

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

_OAUTH_SESS = {}
_OAUTH_LOCK = threading.Lock()
_OAUTH_URL_RE = re.compile(r"https://[A-Za-z0-9.\-]*claude\.(?:ai|com)/[^\s'\"]*(?:oauth|authorize)[^\s'\"]*")

def _oauth_hash(aid):

    return hashlib.sha256(("llmpool:" + str(aid)).encode()).hexdigest()[:20]

def _oauth_cell_uid(aid):
    return "llmoauth_" + _oauth_hash(aid)

def _oauth_sess_name(aid):
    return "llmoauth-" + _oauth_hash(aid)

def _tmux(*args, timeout=8):
    try:
        return subprocess.run(["tmux"] + list(args), capture_output=True, text=True, timeout=timeout)
    except Exception:
        class _R:
            returncode = 1; stdout = ""; stderr = "tmux failed"
        return _R()

def _oauth_pane(sess):

    r = _tmux("capture-pane", "-t", sess, "-p", "-S", "-200", "-J")
    return r.stdout if getattr(r, "returncode", 1) == 0 else ""

def _oauth_logged_in(home):

    if not home:
        return False
    try:
        with open(os.path.join(home, ".claude.json")) as f:
            if (json.load(f) or {}).get("oauthAccount"):
                return True
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.join(home, ".claude", ".credentials.json")) as f:
            return bool(((json.load(f) or {}).get("claudeAiOauth") or {}).get("accessToken"))
    except (OSError, ValueError):
        return False

_APP_HINTS = {
    "BROWSER": "firefox", "TERMINAL": "foot", "FILES": "nautilus",
    "CODE": "code", "CLAUDE": "code", "SETTINGS": "control-center", "CALENDAR": "calendar",
}

def _running_cid_for(hint, uid=DEFAULT_PRINCIPAL):

    if not hint:
        return None
    h = hint.lower()
    htok = [t for t in re.split(r"[.\-_ /]", h) if len(t) >= 3]
    best = None
    for ln in seat_ctl("list", uid=uid).splitlines():
        m = re.match(r"cid=(\d+)", ln)
        if not m:
            continue
        cid = int(m.group(1))
        am = re.search(r'app_id="([^"]*)"', ln)
        tm = re.search(r'title="([^"]*)"', ln)
        hay = ((am.group(1) if am else "") + " " + (tm.group(1) if tm else "")).lower()
        if (h and h in hay) or any(t in hay for t in htok):
            if best is None or cid > best:
                best = cid
    return best

def _spawn_hint(exec_cmd):

    m = re.search(r"snap-run\s+(\S+)", exec_cmd)
    if m:
        return m.group(1).split(".")[0]
    for tok in exec_cmd.split():
        if "=" in tok:
            continue
        return os.path.basename(tok)
    return None

def seat_launch(prog, uid=DEFAULT_PRINCIPAL):

    prog = re.sub(r"[^A-Za-z]", "", prog).upper()[:16] or "BROWSER"
    if not seat_running(uid):
        r = seat_start(uid)
        if not r.get("ok"):
            return r
    if prog == "BROWSER":

        if not _cell_firefox_procs(uid):
            r = _spawn_marionette_firefox(uid=uid)
            if not r.get("ok"):
                return r
        cid = _present_firefox(uid)
        return {"ok": True, "focused": cid}
    cid = _running_cid_for(_APP_HINTS.get(prog), uid=uid)
    if cid is not None:
        return {"ok": True, "reply": seat_ctl(f"focus {cid}", uid=uid), "focused": cid}
    return {"ok": True, "reply": seat_ctl(f"launch {prog}", uid=uid)}

APP_DIRS = [
    os.path.join(HOME, ".local/share/applications"),
    "/usr/local/share/applications",
    "/usr/share/applications",
    "/var/lib/snapd/desktop/applications",
    "/var/lib/flatpak/exports/share/applications",
    os.path.join(HOME, ".local/share/flatpak/exports/share/applications"),
]
_FIELD_CODES = re.compile(r"%[fFuUdDnNickvm]")
_DEAD_EXECS = ("/usr/bin/false", "/bin/false", "false", "/usr/bin/true", "/bin/true", "true")
SNAP_RUN_BIN = os.path.join(HOME, ".local/bin/snap-run")
_APP_CACHE = {"apps": None}

def _desktop_parse(path):
    entry, in_group = {}, False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if line.startswith("[") and line.endswith("]"):
                    if in_group:
                        break
                    in_group = line == "[Desktop Entry]"
                    continue
                if not in_group or "=" not in line or line.lstrip().startswith("#"):
                    continue
                k, _, v = line.partition("=")
                entry.setdefault(k.strip(), v.strip())
    except OSError:
        return None
    return entry

def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes")

def _clean_exec(s):
    return re.sub(r"\s+", " ", _FIELD_CODES.sub("", s or "")).strip()

def _snap_name(exec_str):
    m = re.search(r"\bsnap\s+run\b((?:\s+--?\S+)*)\s+(\S+)", exec_str or "")
    if m:
        return m.group(2)
    m = re.search(r"/snap/bin/(\S+)", exec_str or "")
    return m.group(1) if m else None

def apps_index(refresh=False):

    if _APP_CACHE["apps"] is not None and not refresh:
        return _APP_CACHE["apps"]
    import glob
    seen, apps = set(), []
    snap_run = SNAP_RUN_BIN if os.path.exists(SNAP_RUN_BIN) else "snap-run"
    for d in APP_DIRS:
        if not os.path.isdir(d):
            continue
        for path in sorted(glob.glob(os.path.join(d, "*.desktop"))):
            appid = os.path.basename(path)[:-len(".desktop")]
            if appid in seen:
                continue
            e = _desktop_parse(path)
            if not e or e.get("Type", "Application") != "Application":
                continue
            if _truthy(e.get("NoDisplay")) or _truthy(e.get("Hidden")):
                continue
            raw = e.get("Exec", "")
            if not raw or _clean_exec(raw) in _DEAD_EXECS:
                continue
            is_snap = ("/var/lib/snapd/desktop/applications/" in path
                       or "/snap/bin/" in raw or bool(re.search(r"(^|\s|/)snap\s+run\b", raw)))
            snap_name = _snap_name(raw) if is_snap else None
            tryexec = e.get("TryExec")
            if tryexec and not is_snap and not (
                    (os.path.isabs(tryexec) and os.path.exists(tryexec)) or _shutil.which(tryexec)):
                continue
            launch = f"{snap_run} {snap_name}" if (is_snap and snap_name) else _clean_exec(raw)
            seen.add(appid)
            apps.append({"id": appid, "name": e.get("Name") or appid,
                         "comment": e.get("Comment", ""),
                         "categories": [c for c in e.get("Categories", "").split(";") if c],
                         "icon": e.get("Icon", ""), "exec": launch, "snap": is_snap})
    apps.sort(key=lambda a: a["name"].lower())
    _APP_CACHE["apps"] = apps
    return apps

def seat_spawn(exec_cmd, uid=DEFAULT_PRINCIPAL):

    exec_cmd = (exec_cmd or "").strip()

    exec_cmd = exec_cmd.replace("\n", " ").replace("\r", " ").strip()
    if not exec_cmd:
        return {"ok": False, "error": "empty command"}
    if not seat_running(uid):
        r = seat_start(uid)
        if not r.get("ok"):
            return r
    cid = _running_cid_for(_spawn_hint(exec_cmd), uid=uid)
    if cid is not None:
        return {"ok": True, "reply": seat_ctl(f"focus {cid}", uid=uid), "focused": cid}
    reply = seat_ctl(f"spawn {exec_cmd}", uid=uid)
    return {"ok": not reply.startswith("error"), "reply": reply}

def _pnprog(done, total, msg=""):

    if not os.environ.get("PN_JOB_ID"):
        return
    try:
        subprocess.run([PN_BIN, "progress", str(done), str(total)] + (["--msg", msg] if msg else []),
                       capture_output=True, timeout=10)
    except Exception:
        pass

def run_commission(jid):

    job = job_get(jid); goal = job["prompt"]
    cwd = os.path.join(JOBS_DIR, jid)
    os.makedirs(os.path.join(cwd, "artifacts"), exist_ok=True)
    os.makedirs(os.path.join(cwd, "inbox"), exist_ok=True)
    open(os.path.join(cwd, "goal.txt"), "w").write(goal)
    for a in (job.get("attachments") or []):
        try:
            shutil.copy(a, os.path.join(cwd, "inbox", os.path.basename(a)))
        except Exception:
            pass
    room = f"auftrag-{jid[:6]}"
    job_update(jid, status="building", room=room)
    _pnprog(1, 3, f"build · {room}")
    job_log(jid, f"[1/3 BUILD] Room {room} (2 Builder) — Ziel 1:1 in goal.txt")
    subprocess.run([PR, "new", room, "--agents", "2", "--cwd", cwd, "--force", "--cap", "16"], capture_output=True)
    room_phase(room, jid,
        "ZIEL (steht 1:1 in goal.txt): " + goal + " — Anhaenge liegen in inbox/. Baut das Artefakt in DIESEM Ordner. "
        "Iteriert GENAU ZWEIMAL: bauen, kritisch gegen das ZIEL pruefen, verbessern. Endergebnis nach artifacts/ "
        "(Dateiname mit Zeitstempel). Notiert kurz in BUILD.md was ihr gebaut habt. Nach den 2 Iterationen beendet mit dem DONE-Token.")
    job_update(jid, status="reviewing")
    _pnprog(2, 3, "review")
    job_log(jid, "[2/3 REVIEW] Builder runter — FRISCHE Reviewer hoch (sauberer Kontext)")
    subprocess.run([PR, "delete", room], capture_output=True)
    rroom = f"{room}-rev"; job_update(jid, room=rroom)
    subprocess.run([PR, "new", rroom, "--agents", "2", "--cwd", cwd, "--force", "--cap", "16"], capture_output=True)
    room_phase(rroom, jid,
        "ZIEL (goal.txt): " + goal + " — Das Artefakt liegt in artifacts/. Ihr seid FRISCHE Reviewer OHNE Bau-Kontext. "
        "Prueft STRENG, ob das Artefakt das Ziel WIRKLICH erfuellt. Bugfixt + optimiert direkt in artifacts/. Wenn euch "
        "Spezialwissen fehlt, fuegt einen self-trainierenden Spezialisten hinzu: phantom-room addagent " + rroom + " --role <domain>. "
        "Gebt ERST frei, wenn es optimiert ist UND das Ziel erfuellt. Schreibt VERDICT.md (Ziel erfuellt? Was optimiert? Wie viele Punkte gefunden?). "
        "Dann beendet mit dem DONE-Token.")
    adir = os.path.join(cwd, "artifacts")
    arts = sorted(os.listdir(adir)) if os.path.isdir(adir) else []
    job_update(jid, status="done", artifacts=arts)
    _pnprog(3, 3, f"done · {len(arts)} Artefakt(e)")
    job_log(jid, f"[3/3 DONE] {len(arts)} Artefakt(e) freigegeben")
    if job.get("email"):
        ok, _md = notify_email(job["email"], "Brainarbeit: dein Auftrag ist fertig",
            "Dein Auftrag wurde von mehreren Agenten gebaut, von FRISCHEN Reviewern verifiziert und optimiert.\n\n"
            "ZIEL:\n" + goal[:600] + "\n\nArtefakte ansehen + herunterladen:\n" + job_link(jid) + "\n\n— Brainarbeit")
        job_log(jid, ("[email] gesendet an %s (%s)" % (job["email"], _md)) if ok else ("[email] nicht gesendet (%s)" % _md))
    subprocess.run([PR, "stop", rroom], capture_output=True)

def _portal_interp():

    return sys.executable or _shutil.which("python3") or _shutil.which("python") or "/usr/bin/python3"

def run_commission_governed(jid):

    room = f"auftrag-{jid[:6]}"
    if not pn_available():

        job_log(jid, "[pn] Governor (pnd) nicht erreichbar — dieser Auftrag laeuft ausnahmsweise "
                     "direkt auf der Box (ohne Warteschlange/Ressourcen-Deckel). Sobald pnd wieder "
                     "laeuft, laufen Auftraege wieder governt.")
        run_commission(jid)
        return
    principal = (job_get(jid) or {}).get("principal") or DEFAULT_PRINCIPAL
    try:
        import pn_governed as _G
        broker_ok = _G.broker_available()
    except Exception as _e:
        _G, broker_ok = None, False
    if broker_ok:
        r = _G.pn_req_as(principal, {"verb": "submit", "task_type": "commission.run",
                                     "params": {"jid": jid}})
        if r.get("ok"):
            gid = r.get("id")
            if r.get("state") == "staged":

                job_update(jid, status="wartet_freigabe")
                job_log(jid, f"[freigabe] Dieser Auftrag wartet auf DEINE Freigabe — du hast für "
                             f"Aufträge 'Freigabe vor Ausführung' eingestellt. Gib ihn in deiner "
                             f"Freigabe-Ansicht frei, dann läuft er governt. (governte Zeile #{gid}, "
                             f"dir zugeordnet)")
                return
            job_log(jid, f"[pn] governt als '{principal}' eingereicht (K5-B on-behalf-of, "
                         f"commission.run #{gid}, Room {room}) — nicht mehr als admin.")

            _wait_governed_terminal(principal, gid, jid)
            return
        job_log(jid, f"[pn] Broker-Einreichung fehlgeschlagen ({r.get('error')}) — Rücktritt auf den "
                     f"admin-Pfad (governt, aber als admin statt als '{principal}').")
    else:
        job_log(jid, f"[pn] 4003-Broker nicht erreichbar — Rücktritt auf den admin-Pfad (governt, "
                     f"aber als admin statt als '{principal}'; die Freigabe-Präferenz greift dann nicht).")

    r = subprocess.run([PN_BIN, "run", "--class", "commission", "--room", room,
                        "--tag", f"comm:{jid[:6]}", "--", _portal_interp(), PORTAL_BIN, "_commission", jid],
                       capture_output=True, text=True)
    st = (job_get(jid) or {}).get("status")
    if st in ("done", "error") or r.returncode == 0:
        return

    detail = ((r.stderr or "") + " " + (r.stdout or "")).strip()
    if detail:
        detail = re.sub(r"\s+", " ", detail)[:400]
    job_log(jid, f"[pn] Governed-Lauf fehlgeschlagen (rc={r.returncode}) — KEIN Host-Fallback. "
                 f"Der Auftrag wird nicht ungoverned ausgefuehrt; bitte erneut einreichen oder pnd "
                 f"pruefen." + (f" Detail: {detail}" if detail else ""))
    job_update(jid, status="error")

_GOV_TERMINAL = frozenset({"done", "failed", "cancelled", "rejected", "error"})

def _wait_governed_terminal(principal, gid, jid, _poll_s=2.0):

    try:
        import pn_governed as _G
    except Exception:
        return
    while True:
        r = _G.pn_req_as(principal, {"verb": "job", "id": gid})
        if not r.get("ok"):

            return
        st = ((r.get("job") or {}).get("state") or "")
        if st in _GOV_TERMINAL:
            return
        time.sleep(_poll_s)

WORKER_STARTED = {"v": False}
def start_worker():
    if WORKER_STARTED["v"]:
        return
    WORKER_STARTED["v"] = True
    def loop():
        jid = None
        while True:
            try:
                with _DBLOCK:
                    c = db(); r = c.execute("SELECT id FROM jobs WHERE status='queued' AND mode='commission' ORDER BY CASE priority WHEN 'interactive' THEN 0 ELSE 1 END, created LIMIT 1").fetchone(); c.close()
                if not r:
                    time.sleep(2); continue
                jid = r[0]; job_update(jid, status="starting")
                run_commission_governed(jid)
            except Exception as e:
                if jid:
                    try:
                        job_update(jid, status="error"); job_log(jid, "FEHLER: " + str(e))
                    except Exception:
                        pass
                time.sleep(2)
    threading.Thread(target=loop, daemon=True).start()

def _alert_email():

    try:
        o = user_get("owner")
        if o and o.get("email"):
            return o["email"]
    except Exception:
        pass
    return system_secret("alert_email") or mailjet_sender()

RENEWAL_WATCH_STARTED = {"v": False}
def start_renewal_watch():

    if RENEWAL_WATCH_STARTED["v"] or _LLMPOOL is None:
        return
    RENEWAL_WATCH_STARTED["v"] = True

    def loop():
        time.sleep(90)
        while True:
            try:
                for r in _LLMPOOL.renewals_due(days=2):
                    to = _alert_email()
                    if not to:
                        continue
                    when = time.strftime("%d.%m.%Y", time.localtime(r["renewal_ts"]))
                    who = r.get("email") or r.get("id")
                    subj = "Brainarbeit: Claude-Max-Abo '%s' erneuert sich am %s" % (who, when)
                    body = ("Hallo,\n\nDas Claude-Max-Abo im Pool-Konto '%s' (%s, Tarif %s) erneuert sich "
                            "in ~%.0f Tag(en), am %s.\n\nEntscheide, ob du diesen Monat erneuern willst, mit "
                            "weniger auskommst oder ein weiteres Abo dazubuchst (Konto 4,5,6,…).\nPool verwalten: "
                            "im Portal unter Admin → LLM-Pool.\n\n— Brainarbeit"
                            % (r.get("id"), who, r.get("tier") or "?", r.get("days_left") or 0, when))
                    ok, _ = notify_email(to, subj, body)
                    if ok:
                        _LLMPOOL.mark_alerted(r["id"], r["renewal_ts"])
                        _prov_log("llm.renewal_alert", "__system__", r.get("id"), {"to": to, "when": when})
            except Exception:
                pass
            time.sleep(6 * 3600)
    threading.Thread(target=loop, daemon=True).start()

import portal_prio_advisor
from portal_prio_advisor import start_prio_advisor, _advisor_status, _advisor_toggle

_CLARIFY_SYSTEM = (
    "Du bist ein praeziser Auftrags-Klaerer fuer eine Entwickler-Agentengruppe. Lies die Konversation. "
    "Wenn das Ziel KONKRET genug ist, um es ohne weitere Rueckfragen zu bauen, antworte mit EINER Zeile: "
    "READY: <der praezise, vollstaendige Auftrag in 1-4 Saetzen>. Sonst stelle GENAU EINE knappe Rueckfrage "
    "(eine Zeile, beginne mit FRAGE:). Keine weiteren Worte.")

_CLARIFY_FAIL_DE = {
    429: "Alle Claude-Konten sind gerade am Limit — bitte später noch einmal versuchen.",
    500: "Der Klärer ließ sich nicht starten — die Modell-Lane der Box antwortet nicht.",
    503: "Der Klärer ist gerade ausgelastet — bitte gleich noch einmal versuchen.",
    504: "Der Klärer hat zu lange gebraucht — bitte noch einmal versuchen.",
}

def _llm_run():

    from portal_email_portioneer import llm_run_core
    return llm_run_core

def _llm_status_de(fallback):

    try:
        if _LLMPOOL is not None:
            snap = _LLMPOOL.snapshot()
            if snap.get("degraded") and snap.get("status_de"):
                return snap["status_de"]
    except Exception:
        pass
    return fallback

def clarify_turn(history):

    convo = "\n".join(("NUTZER: " if h.get("role") == "user" else "DU: ") + h.get("text", "")
                      for h in history)
    r = _llm_run()("KONVERSATION:\n" + convo, system=_CLARIFY_SYSTEM, timeout=60)
    if not r.get("ok"):
        status = r.get("status") or 502
        if r.get("auth_reason"):

            msg = _llm_status_de(r.get("error") or _CLARIFY_FAIL_DE[500])
        else:
            msg = _CLARIFY_FAIL_DE.get(status, "Der Klärer konnte nicht antworten.")
        out = {"ok": False, "error": msg, "status": status}

        hint = _llm_status_de(None)
        if hint and hint != msg:
            out["hint"] = hint
        if r.get("error") and r["error"] != msg:
            out["detail"] = r["error"]
        return out
    out = (r.get("text") or "").strip()
    if "READY:" in out:
        return {"ok": True, "ready": True, "spec": out.split("READY:", 1)[1].strip()}
    if "FRAGE:" in out:
        return {"ok": True, "question": out.split("FRAGE:", 1)[1].strip()}

    return {"ok": True, "question": "Kannst du dein Ziel genauer beschreiben?", "unparsed": out[:400]}

import portal_webapp
from portal_webapp import page_html, job_page_html
