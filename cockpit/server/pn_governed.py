
import os, json, re, time, socket

HOME = os.path.expanduser("~")
PN_BIN = os.path.join(HOME, ".local", "bin", "pn")
PN_SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid()),
                       "pnd.sock")
PN_STATE = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.join(HOME, ".local", "share")),
                        "portioneer")
PN_LOG_DIR = os.path.join(PN_STATE, "logs")

TERMINAL = ("done", "failed", "cancelled", "timeout", "rejected")

def _pnlib_ipc():

    import sys
    for base in (os.environ.get("PNLIB_HOME"),
                 os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                              "engine"),
                 os.path.expanduser("~/portioneer")):
        if base and os.path.isdir(os.path.join(base, "pnlib")) and base not in sys.path:
            sys.path.insert(0, base)
    try:
        from pnlib import ipc as _ipc
        return _ipc
    except Exception:
        return None

def pn_req(req, timeout=10):

    ipc = _pnlib_ipc()
    if ipc is None:
        return {"ok": False, "error": "pnlib.ipc unavailable"}
    try:
        return ipc.send_request(req, timeout=timeout, path=PN_SOCK)
    except FileNotFoundError:
        return {"ok": False, "error": "pnd not running"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def pn_list(limit=100, timeout=10, mindest=10, fields=None, cmd_max=None):

    wunsch = max(int(limit or 100), 1)
    lim = wunsch
    while True:
        req = {"verb": "list", "limit": lim}
        if fields:
            req["fields"] = list(fields)
        if cmd_max:
            req["cmd_max"] = int(cmd_max)
        r = pn_req(req, timeout=timeout)
        if r.get("ok"):
            if lim < wunsch:
                r["gekuerzt"] = {"angefragt": wunsch, "geliefert": lim,
                                 "grund": "pnd-Rahmengrenze (MAX_FRAME)"}
            return r
        if "MAX_FRAME" not in str(r.get("error") or "") or lim <= mindest:
            return r
        lim = max(mindest, lim // 2)

def pn_available():

    return os.path.exists(PN_SOCK) and pn_req({"verb": "ping"}, timeout=3).get("ok")

BROKER_SOCK = os.environ.get("PND_BROKERD_IN_SOCK", "/run/portioneer/broker.sock")
_WEB_SEL_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")

def web_selector(uid):

    u = str(uid or "").strip().lower()
    if u.startswith("web:"):
        u = u[4:]
    return ("web:" + u) if u and _WEB_SEL_RE.match(u) else None

def pn_req_as(web_uid, req, timeout=10):

    sel = web_selector(web_uid)
    if sel is None:
        return {"ok": False, "error": "kein gueltiger web:<uid>-Selektor: %r" % (web_uid,)}
    ipc = _pnlib_ipc()
    if ipc is None:
        return {"ok": False, "error": "pnlib.ipc unavailable"}
    r = dict(req)
    r["_selector"] = sel
    r.pop("_method", None)
    try:
        return ipc.send_request(r, timeout=timeout, path=BROKER_SOCK)
    except FileNotFoundError:
        return {"ok": False, "error": "pn-brokerd nicht erreichbar (broker.sock fehlt)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def broker_available():

    if not os.path.exists(BROKER_SOCK):
        return False
    return bool(pn_req_as("owner", {"verb": "my-jobs", "limit": 1}, timeout=3).get("ok"))

WEB_USER_TASK_TYPES = ("task_type:commission.build", "task_type:commission.run")

def ensure_web_principal(uid, extra_caps=()):

    sel = web_selector(uid)
    if sel is None:
        return {"ok": False, "error": "kein gueltiger web:<uid> aus %r" % (uid,)}
    r = pn_req({"verb": "admin-ensure-principal", "name": sel, "kind": "user",
                "note": "web-session tenant"})
    if not r.get("ok"):
        return {"ok": False, "error": "ensure-principal: %s" % r.get("error"), "principal": sel}
    pn_req({"verb": "admin-bind-identity", "method": "web-session", "selector": sel,
            "target_principal": sel, "verified": 1})
    for cap in tuple(WEB_USER_TASK_TYPES) + tuple(extra_caps):
        pn_req({"verb": "admin-grant", "target_principal": sel, "cap": cap})
    return {"ok": True, "principal": sel}

def submit(cmd, *, mem, timeout_s, tag, latency=None, cpu_quota=None, prio=None, klass=None):

    req = {"verb": "submit", "cmd": list(cmd), "cwd": HOME,
           "class": klass, "mem": int(mem), "prio": prio, "timeout": int(timeout_s),
           "latency": latency, "tag": tag,
           "cpu_quota": (int(cpu_quota) if cpu_quota else None),
           "disk_min": None, "mem_max": None, "room": None, "idempotent": None,
           "source": "cli", "env": {"PATH": os.environ.get("PATH", "")}}
    return pn_req(req, timeout=15)

def job(jid):

    r = pn_req({"verb": "job", "id": int(jid)}, timeout=5)
    return r.get("job") if r.get("ok") else None

def cancel(jid):
    return pn_req({"verb": "cancel", "id": int(jid)}, timeout=5)

def job_tmp(jid):

    root = os.environ.get("PN_JOB_SCRATCH_ROOT") or os.path.join(
        os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid()), "pn-jobs")
    return os.path.join(root, "pn-job-%d" % int(jid), "tmp")

def job_out(jid):
    return os.path.join(PN_LOG_DIR, "job-%d.out" % int(jid))

def job_err(jid):
    return os.path.join(PN_LOG_DIR, "job-%d.err" % int(jid))

def log_tail(jid, limit=4000):

    for p in (job_err(jid), job_out(jid)):
        try:
            with open(p, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - limit))
                lines = [ln for ln in f.read().decode("utf-8", "replace").splitlines() if ln.strip()]
            if lines:
                return lines[-1]
        except OSError:
            continue
    return ""

def run_capture(cmd, *, mem, timeout_s, tag, latency=None, cpu_quota=None, wait_s=120):

    r = submit(cmd, mem=mem, timeout_s=timeout_s, tag=tag, latency=latency,
               cpu_quota=cpu_quota)
    if not r.get("ok"):
        return None, b"", ("Der Governor (pnd) hat den Job abgelehnt: %s"
                           % (r.get("error") or "unbekannt"))
    jid = int(r["id"])
    j = None
    deadline = time.time() + max(5, wait_s)
    while time.time() < deadline:
        j = job(jid)
        if j is not None and j.get("state") in TERMINAL:
            break
        time.sleep(0.4)
    state = (j or {}).get("state")
    if state not in TERMINAL:
        cancel(jid)
        return None, b"", ("Job %d wurde nicht rechtzeitig fertig (Warteschlange voll?) — "
                           "abgebrochen." % jid)
    out = b""
    try:
        with open(job_out(jid), "rb") as f:
            out = f.read()
    except OSError:
        pass
    rc = (j or {}).get("exit_code")
    if state != "done" or (rc not in (None, 0)):
        tail = log_tail(jid)
        return rc, out, ("Job %d endete als %s (rc=%s)%s."
                         % (jid, state, rc, (": " + tail[:160]) if tail else ""))
    return 0, out, None
