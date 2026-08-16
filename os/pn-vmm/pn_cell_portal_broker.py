#!/usr/bin/env python3

import os, sys, socket, ssl, time, threading, struct, urllib.parse, json as _json
from collections import OrderedDict

MAX_FRAME = 8 << 20
MAX_STREAMS = 256

PORTAL_URL = os.environ.get("PN_PORTAL_URL", "https://127.0.0.1:8076")
TOKEN = os.environ.get("PN_PORTAL_TOKEN", "")
SESSION_SID = os.environ.get("PN_SESSION_SID", "")
LOG = os.environ.get("PN_PORTAL_BROKER_LOG", "/tmp/pn-portal-broker.log")

def _envnum(name, default, cast=float):
    try:
        return cast(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return cast(default)

CACHE_TTL = _envnum("PN_BROKER_GET_CACHE_TTL", 2.0)
CACHE_MAX = max(1, _envnum("PN_BROKER_GET_CACHE_MAX", 2048, int))
CACHE_BODY_MAX = 256 * 1024
CACHE_PATHS = ("/api/cellfs/ls", "/api/cellfs/stat")
LOG_LEVEL = (os.environ.get("PN_BROKER_LOG_LEVEL", "kompakt") or "kompakt").strip().lower()
if LOG_LEVEL not in ("voll", "kompakt", "aus"):
    LOG_LEVEL = "kompakt"
PROXY_RPS = _envnum("PN_BROKER_PROXY_RPS", 20.0)
PROXY_BURST = max(1.0, _envnum("PN_BROKER_PROXY_BURST", 200.0))
ALLOWED_VERBS = [v.strip() for v in os.environ.get("PN_ALLOWED_VERBS", "").split(",") if v.strip()]
ALLOW_STATE = os.environ.get("PN_ALLOW_STATE", "1") == "1"
ALLOWED_DISPLAYS = [d.strip() for d in os.environ.get("PN_ALLOWED_DISPLAYS", "").split(",") if d.strip()]
ALLOWED_DEVICES = [d.strip() for d in os.environ.get("PN_ALLOWED_DEVICES", "").split(",") if d.strip()]
DEVICE_CONNECT = os.environ.get("PN_DEVICE_CONNECT", "deny")
try:
    FS_READ = [r.get("path") if isinstance(r, dict) else r for r in _json.loads(os.environ.get("PN_FS_READ", "[]"))]
except Exception:
    FS_READ = []
try:
    FS_WRITE = [r.get("path") if isinstance(r, dict) else r for r in _json.loads(os.environ.get("PN_FS_WRITE", "[]"))]
except Exception:
    FS_WRITE = []

POLICY_FILE = os.environ.get("PN_POLICY_FILE", "")
_POL_CACHE = {"mtime": None, "val": None}

def _env_policy():
    return {"verbs": ALLOWED_VERBS, "state": ALLOW_STATE, "displays": ALLOWED_DISPLAYS,
            "devices": ALLOWED_DEVICES, "connect": DEVICE_CONNECT, "fs_read": FS_READ, "fs_write": FS_WRITE}

def _live_policy():
    if not POLICY_FILE:
        return _env_policy()
    try:
        mt = os.stat(POLICY_FILE).st_mtime
    except OSError:
        return _env_policy()
    if _POL_CACHE["mtime"] == mt and _POL_CACHE["val"] is not None:
        return _POL_CACHE["val"]
    try:
        enf = _json.load(open(POLICY_FILE))
        val = {"verbs": list(enf.get("portal_verbs") or []),
               "state": enf.get("portal_state") == "allow",
               "displays": list(enf.get("displays") or []),
               "devices": list(enf.get("devices") or []),
               "connect": enf.get("device_connect", "deny"),
               "fs_read": [r.get("path") if isinstance(r, dict) else r for r in (enf.get("fs_read") or [])],
               "fs_write": [r.get("path") if isinstance(r, dict) else r for r in (enf.get("fs_write") or [])]}
    except Exception:
        val = _env_policy()
    _POL_CACHE["mtime"] = mt; _POL_CACHE["val"] = val
    return val

_HOME = os.path.expanduser("~")
_XDG_RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
_PND_SOCK = os.environ.get("PN_PND_SOCK", os.path.join(_XDG_RUNTIME, "pnd.sock"))
_XDG_DATA = os.environ.get("XDG_DATA_HOME", os.path.join(_HOME, ".local", "share"))
_PN_LOG_DIR = os.path.join(_XDG_DATA, "portioneer", "logs")
PN_SESSION_CELL = os.environ.get("PN_SESSION_CELL", "")
PN_PRINCIPAL = os.environ.get("PN_PRINCIPAL", "owner")
COMPUTE_ENABLED_ENV = os.environ.get("PN_COMPUTE_ENABLED", "0") == "1"
_COMPUTE_TAG = "cell:" + (PN_SESSION_CELL or "unknown")
_COMPUTE_CLASS = "cell.compute"
_COMPUTE_TERMINAL = ("done", "failed", "cancelled", "timeout", "rejected", "killed", "error")
_COMPUTE_ALLOWED_KEYS = {"cmd", "mem_mib", "cpu_pct", "timeout_s"}
_COMPUTE_MAX_ARGV, _COMPUTE_MAX_ARGLEN, _COMPUTE_MAX_RESULT = 256, 8192, 256 * 1024
_COMPUTE_STATUS = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
                   429: "Too Many Requests", 502: "Bad Gateway"}
_compute_seen = set()
_compute_lock = threading.Lock()
_CPOL_CACHE = {"mtime": None, "val": None}

def _pnd_rpc(req, timeout=6.0):

    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(timeout)
        c.connect(_PND_SOCK)
        c.sendall((_json.dumps(req, separators=(",", ":")) + "\n").encode())
        buf = b""
        while b"\n" not in buf and len(buf) < (1 << 20):
            d = c.recv(65536)
            if not d:
                break
            buf += d
        c.close()
        return _json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace") or "{}")
    except Exception as e:
        return {"ok": False, "error": "pnd (Governor) nicht erreichbar (%s)" % type(e).__name__}

def _compute_policy():

    env_val = {"enabled": COMPUTE_ENABLED_ENV,
               "mem_max_mib": int(os.environ.get("PN_COMPUTE_MEM_MAX_MIB", "0") or 0),
               "cpu_max_pct": int(os.environ.get("PN_COMPUTE_CPU_MAX_PCT", "0") or 0),
               "timeout_max_s": int(os.environ.get("PN_COMPUTE_TIMEOUT_MAX_S", "0") or 0),
               "max_concurrent": int(os.environ.get("PN_COMPUTE_MAX_CONCURRENT", "0") or 0)}
    if not POLICY_FILE:
        return env_val
    try:
        mt = os.stat(POLICY_FILE).st_mtime
    except OSError:
        return env_val
    if _CPOL_CACHE["mtime"] == mt and _CPOL_CACHE["val"] is not None:
        return _CPOL_CACHE["val"]
    val = env_val
    try:
        enf = _json.load(open(POLICY_FILE))
        val = {"enabled": bool(enf.get("compute_enabled")),
               "mem_max_mib": int(enf.get("compute_mem_max_mib") or 0),
               "cpu_max_pct": int(enf.get("compute_cpu_max_pct") or 0),
               "timeout_max_s": int(enf.get("compute_timeout_max_s") or 0),
               "max_concurrent": int(enf.get("compute_max_concurrent") or 0)}
    except Exception:
        val = env_val
    _CPOL_CACHE["mtime"] = mt
    _CPOL_CACHE["val"] = val
    return val

def _compute_owns(j):

    return bool(j) and j.get("client_tag") == _COMPUTE_TAG

def _compute_outstanding():

    with _compute_lock:
        ids = list(_compute_seen)
    live = 0
    for jid in ids:
        r = _pnd_rpc({"verb": "job", "id": int(jid)})
        j = r.get("job") if r.get("ok") else None
        if j is None or j.get("state") in _COMPUTE_TERMINAL:
            with _compute_lock:
                _compute_seen.discard(jid)
        else:
            live += 1
    return live

def _compute_submit(job):

    pol = _compute_policy()
    if not pol["enabled"]:
        return 403, {"ok": False, "policy": "compute-disabled",
                     "error": "Rechenauftrag an die Box ist für diese Sitzung nicht freigegeben "
                              "(Standard: aus). Der Besitzer muss 'Lokales Rechnen' freischalten."}
    if not isinstance(job, dict):
        return 400, {"ok": False, "error": "Ungültige Anfrage (JSON-Objekt erwartet)."}
    extra = set(job) - _COMPUTE_ALLOWED_KEYS
    if extra:
        return 400, {"ok": False, "policy": "compute-fields",
                     "error": "Nicht erlaubte Felder: %s. Profil, Sandbox, Netz und Identität werden "
                              "HOST-seitig festgelegt und können nicht angefordert werden. Erlaubt: "
                              "cmd, mem_mib, cpu_pct, timeout_s." % ", ".join(sorted(map(str, extra)))}
    cmd = job.get("cmd")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) for x in cmd):
        return 400, {"ok": False, "error": "cmd muss eine nicht-leere Liste von Strings sein "
                                            "(argv — es gibt keine Shell)."}
    if len(cmd) > _COMPUTE_MAX_ARGV or any(len(x) > _COMPUTE_MAX_ARGLEN for x in cmd):
        return 400, {"ok": False, "error": "cmd zu groß (max %d Argumente, je max %d Zeichen)."
                                           % (_COMPUTE_MAX_ARGV, _COMPUTE_MAX_ARGLEN)}
    ceil_mem, ceil_cpu, ceil_to = pol["mem_max_mib"], pol["cpu_max_pct"], pol["timeout_max_s"]
    if ceil_mem <= 0 or ceil_to <= 0:
        return 403, {"ok": False, "policy": "compute-ceiling",
                     "error": "Kein Rechen-Budget freigegeben (RAM- oder Zeit-Obergrenze ist 0). "
                              "Der Besitzer muss Obergrenzen setzen."}

    def _as_int(key):
        v = job.get(key)
        if v is None:
            return None, None
        try:
            return int(v), None
        except (TypeError, ValueError):
            return None, "%s muss eine ganze Zahl sein." % key
    mem, e1 = _as_int("mem_mib")
    cpu, e2 = _as_int("cpu_pct")
    to,  e3 = _as_int("timeout_s")
    for e in (e1, e2, e3):
        if e:
            return 400, {"ok": False, "error": e}

    if mem is not None and mem > ceil_mem:
        return 403, {"ok": False, "policy": "compute-ceiling",
                     "error": "Angeforderter RAM %d MiB übersteigt die Obergrenze dieser Sitzung "
                              "(%d MiB). Kleiner anfordern." % (mem, ceil_mem)}
    if ceil_cpu > 0 and cpu is not None and cpu > ceil_cpu:
        return 403, {"ok": False, "policy": "compute-ceiling",
                     "error": "Angeforderte CPU %d%% übersteigt die Obergrenze (%d%%)." % (cpu, ceil_cpu)}
    if to is not None and to > ceil_to:
        return 403, {"ok": False, "policy": "compute-ceiling",
                     "error": "Angeforderte Laufzeit %ds übersteigt die Obergrenze (%ds)." % (to, ceil_to)}
    mem = max(1, min(mem if mem is not None else ceil_mem, ceil_mem))
    to = max(1, min(to if to is not None else ceil_to, ceil_to))
    if ceil_cpu > 0:
        cpu = min(cpu if cpu is not None else ceil_cpu, ceil_cpu)
    else:
        cpu = None
    limit = max(1, pol["max_concurrent"])
    if _compute_outstanding() >= limit:
        return 429, {"ok": False, "policy": "compute-concurrency",
                     "error": "Zu viele gleichzeitige Rechenaufträge dieser Sitzung (Grenze %d). "
                              "Warte, bis einer fertig ist." % limit}
    sub = {"verb": "submit", "cmd": list(cmd), "cwd": _HOME, "class": _COMPUTE_CLASS,
           "mem": int(mem), "mem_max": int(mem), "timeout": int(to),
           "tag": _COMPUTE_TAG, "source": "cli",
           "env": {"PATH": "/usr/local/bin:/usr/bin:/bin"}}
    if cpu is not None:
        sub["cpu_quota"] = int(cpu)
    r = _pnd_rpc(sub, timeout=15.0)
    if not r.get("ok"):
        return 502, {"ok": False, "policy": "pnd-refused", "pnd_reason": r.get("reason"),
                     "advice": r.get("advice"),
                     "error": "Der Governor (pnd) hat den Job abgelehnt: %s" % (r.get("error") or "unbekannt")}
    jid = r.get("id")
    try:
        with _compute_lock:
            _compute_seen.add(int(jid))
    except (TypeError, ValueError):
        pass
    log("COMPUTE_SUBMIT cell=%s job=%s mem=%dMiB cpu=%s timeout=%ds isolated argv0=%r"
        % (PN_SESSION_CELL, jid, mem, cpu, to, (cmd[0] if cmd else "")[:60]))
    return 200, {"ok": True, "job_id": jid, "pos": r.get("pos"), "eta": r.get("eta"),
                 "mem_mib": mem, "cpu_pct": cpu, "timeout_s": to, "class": _COMPUTE_CLASS,
                 "net": "isolated", "sandboxed": True, "principal": PN_PRINCIPAL,
                 "note": "Läuft als eingehegter, netz-isolierter pnd-Job in pn.slice — NICHT in der Zelle."}

def _compute_status(jid):
    r = _pnd_rpc({"verb": "job", "id": int(jid)})
    j = r.get("job") if r.get("ok") else None
    if not j:
        return 404, {"ok": False, "error": "Job %s nicht gefunden." % jid}
    if not _compute_owns(j):
        return 403, {"ok": False, "error": "Job %s gehört nicht zu dieser Sitzung." % jid}
    if j.get("state") in _COMPUTE_TERMINAL:
        with _compute_lock:
            _compute_seen.discard(int(jid))
    return 200, {"ok": True, "job_id": jid, "state": j.get("state"), "exit_code": j.get("exit_code"),
                 "pos": r.get("pos"), "eta": r.get("eta"),
                 "mem_peak_mib": j.get("mem_peak"), "cpu_s": j.get("cpu_s"), "isolated": True}

def _compute_result(jid):
    r = _pnd_rpc({"verb": "job", "id": int(jid)})
    j = r.get("job") if r.get("ok") else None
    if not j:
        return 404, {"ok": False, "error": "Job %s nicht gefunden." % jid}
    if not _compute_owns(j):
        return 403, {"ok": False, "error": "Job %s gehört nicht zu dieser Sitzung." % jid}

    def _tail(p):
        try:
            with open(p, "rb") as f:
                f.seek(0, 2)
                sz = f.tell()
                f.seek(max(0, sz - _COMPUTE_MAX_RESULT))
                return f.read().decode("utf-8", "replace"), sz
        except OSError:
            return "", 0
    out, out_sz = _tail(os.path.join(_PN_LOG_DIR, "job-%d.out" % int(jid)))
    err, err_sz = _tail(os.path.join(_PN_LOG_DIR, "job-%d.err" % int(jid)))
    return 200, {"ok": True, "job_id": jid, "state": j.get("state"), "exit_code": j.get("exit_code"),
                 "stdout": out, "stdout_bytes": out_sz, "stdout_truncated": out_sz > _COMPUTE_MAX_RESULT,
                 "stderr": err, "stderr_bytes": err_sz,
                 "note": "stdout/stderr des Jobs (Tail, max %d Bytes). Große Ein-/Ausgabedateien "
                         "kommen mit dem Scratch-Volume (G5)." % _COMPUTE_MAX_RESULT}

def _compute_cancel(jid):
    r = _pnd_rpc({"verb": "job", "id": int(jid)})
    j = r.get("job") if r.get("ok") else None
    if not j:
        return 404, {"ok": False, "error": "Job %s nicht gefunden." % jid}
    if not _compute_owns(j):
        return 403, {"ok": False, "error": "Job %s gehört nicht zu dieser Sitzung." % jid}
    rc = _pnd_rpc({"verb": "cancel", "id": int(jid)})
    with _compute_lock:
        _compute_seen.discard(int(jid))
    return (200 if rc.get("ok") else 502), {"ok": bool(rc.get("ok")), "job_id": jid,
            "state": rc.get("state"),
            "error": None if rc.get("ok") else (rc.get("error") or "Abbruch fehlgeschlagen")}

def _compute_send(client, status, obj):
    body = _json.dumps(obj, ensure_ascii=False).encode("utf-8")
    hdr = ("HTTP/1.1 %d %s\r\nContent-Type: application/json; charset=utf-8\r\n"
           "Content-Length: %d\r\nConnection: close\r\n\r\n"
           % (status, _COMPUTE_STATUS.get(status, "OK"), len(body))).encode()
    try:
        client.sendall(hdr + body)
    except OSError:
        pass

def _compute_serve(client, method, path, body):

    p = path.split("?", 1)[0]
    qs = urllib.parse.parse_qs(path.split("?", 1)[1]) if "?" in path else {}
    op = p[len("/pncompute/"):].strip("/")

    def _qid(extra=None):
        v = (extra or {}).get("id") or (qs.get("id") or [None])[0]
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    try:
        if op == "submit" and method == "POST":
            try:
                jd = _json.loads(body.decode("utf-8", "replace") or "{}")
            except Exception:
                return _compute_send(client, 400, {"ok": False, "error": "Ungültiges JSON."})
            return _compute_send(client, *_compute_submit(jd))
        if op == "status" and method == "GET":
            jid = _qid()
            return _compute_send(client, *( (400, {"ok": False, "error": "id fehlt."}) if jid is None
                                           else _compute_status(jid)))
        if op == "result" and method == "GET":
            jid = _qid()
            return _compute_send(client, *( (400, {"ok": False, "error": "id fehlt."}) if jid is None
                                           else _compute_result(jid)))
        if op == "cancel" and method == "POST":
            try:
                jb = _json.loads(body.decode("utf-8", "replace") or "{}")
            except Exception:
                jb = {}
            jid = _qid(jb)
            return _compute_send(client, *( (400, {"ok": False, "error": "id fehlt."}) if jid is None
                                           else _compute_cancel(jid)))
        if op == "list" and method == "GET":
            r = _pnd_rpc({"verb": "list", "limit": 50})
            jobs = [{"job_id": j.get("id"), "state": j.get("state"), "exit_code": j.get("exit_code")}
                    for j in (r.get("jobs") or []) if _compute_owns(j)]
            return _compute_send(client, 200, {"ok": True, "jobs": jobs})
        return _compute_send(client, 404, {"ok": False, "error": "Unbekannter /pncompute-Endpunkt."})
    except Exception as e:
        log("COMPUTE_ERR %r" % e)
        return _compute_send(client, 502, {"ok": False, "error": "interner Fehler im Compute-Broker."})

_DEVICE_ARG_KEYS = ("device", "target", "sink", "printer", "speaker", "tv", "cast", "display")
_PAIR_RE = None
import re as _re
_PAIR_RE = _re.compile(r"(pair|enroll|connect_device|adddevice|add_device|discover|couple)", _re.I)

def _path_under(want, roots):

    w = os.path.normpath(str(want or ""))
    if not w.startswith("/"):
        return False
    for r in roots:
        if not r:
            continue
        rn = os.path.normpath(r)
        if w == rn or w.startswith(rn.rstrip("/") + "/"):
            return True
    return False

def _policy_gate(method, path, body):

    pol = _live_policy()
    p = path.split("?", 1)[0]
    if p == "/api/agent/state":
        return None if pol["state"] else "Zustand lesen ist für diese Session nicht freigegeben."
    if p == "/api/agent/exec":
        try:
            j = _json.loads(body.decode("utf-8", "replace") or "{}")
        except Exception:
            j = {}
        verb = str(j.get("verb") or ""); args = j.get("args") or {}
        if verb in ("ask_owner", "ask_owner_result"):
            return None
        if not ("*" in pol["verbs"] or verb in pol["verbs"]):
            return "Aktion '%s' ist für diese Session nicht freigegeben (Allowlist)." % (verb or "?")

        if isinstance(args, dict):
            dev = next((str(args[k]) for k in _DEVICE_ARG_KEYS if args.get(k)), None)
            if dev and not ("*" in pol["devices"] or dev in pol["devices"]):
                return "Gerät '%s' ist für diese Session nicht freigegeben." % dev

        if _PAIR_RE.search(verb) and pol["connect"] == "deny":
            return "Neue Geräte zu verbinden ist für diese Session nicht freigegeben."
        return None
    if p.startswith("/api/cellfs/"):
        op = p.rsplit("/", 1)[1]
        try:
            b = _json.loads(body.decode("utf-8", "replace") or "{}") if body else {}
        except Exception:
            b = {}
        want = b.get("path") or urllib.parse.parse_qs(path.split("?", 1)[1] if "?" in path else "").get("path", [""])[0]
        roots = pol["fs_write"] if op == "write" else (pol["fs_read"] + pol["fs_write"])
        if _path_under(want, roots):
            return None
        return "Pfad '%s' ist für diese Session nicht freigegeben (%s)." % (want or "?", "schreiben" if op == "write" else "lesen")
    if p.startswith("/api/displays/show"):
        try:
            disp = str((_json.loads(body.decode("utf-8", "replace") or "{}")).get("display") or "")
        except Exception:
            disp = ""
        if "*" in pol["displays"] or disp in pol["displays"]:
            return None
        return "Anzeige '%s' ist für diese Session nicht freigegeben." % (disp or "?")
    if p == "/api/displays" and method == "GET":
        return None
    return "Dieser Portal-Endpunkt ist für Sessions nicht freigegeben."

_u = urllib.parse.urlparse(PORTAL_URL)
UP_HOST = _u.hostname or "127.0.0.1"
UP_PORT = _u.port or (443 if _u.scheme == "https" else 80)
UP_TLS = (_u.scheme == "https")

def log(m):

    if LOG_LEVEL == "aus":
        return
    line = "[%.3f] %s" % (time.time(), m)
    try:
        open(LOG, "a").write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)

def log_voll(m):

    if LOG_LEVEL == "voll":
        log(m)

_stats = {"req": 0, "portal": 0, "cache": 0, "deny": 0, "block": 0,
          "throttle": 0, "fehler": 0, "resp_b": 0, "compute": 0}
_stats_lock = threading.Lock()
_stats_t0 = time.time()
_SUMMARY_S = 60.0

def _count(key, n=1):

    global _stats_t0
    line = None
    with _stats_lock:
        _stats[key] = _stats.get(key, 0) + n
        val = _stats[key]
        now = time.time()
        if LOG_LEVEL == "kompakt" and (now - _stats_t0) >= _SUMMARY_S:
            if _stats["req"] or _stats["fehler"] or _stats["throttle"] or _stats["compute"]:
                line = ("SUMME %ds req=%d portal=%d cache=%d deny=%d block=%d 429=%d fehler=%d "
                        "resp=%dB compute=%d"
                        % (int(now - _stats_t0), _stats["req"], _stats["portal"], _stats["cache"],
                           _stats["deny"], _stats["block"], _stats["throttle"], _stats["fehler"],
                           _stats["resp_b"], _stats["compute"]))
            for k in _stats:
                _stats[k] = 0
            _stats_t0 = now
    if line:
        log(line)
    return val

_cache = OrderedDict()
_cache_lock = threading.Lock()

def _cache_key(path):

    return (SESSION_SID, path)

def _cache_get(key):
    if CACHE_TTL <= 0:
        return None
    now = time.time()
    with _cache_lock:
        v = _cache.get(key)
        if v is None:
            return None
        if v[0] < now:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return v[1]

def _cache_put(key, raw):
    if CACHE_TTL <= 0 or len(raw) > CACHE_BODY_MAX:
        return
    with _cache_lock:
        _cache[key] = (time.time() + CACHE_TTL, raw)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)

def _cache_clear():
    with _cache_lock:
        _cache.clear()

def _resp_cacheable(raw):

    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep or not head.startswith(b"HTTP/"):
        return False
    if b" 200" not in head.split(b"\r\n", 1)[0]:
        return False
    m = _re.search(rb"^content-length:[ \t]*(\d+)\r?$", head, _re.I | _re.M)
    if not m:
        return False
    return len(body) == int(m.group(1))

_bucket = {"tokens": PROXY_BURST, "ts": time.time()}
_bucket_lock = threading.Lock()

def _budget_take():

    if PROXY_RPS <= 0:
        return True
    with _bucket_lock:
        now = time.time()
        _bucket["tokens"] = min(PROXY_BURST, _bucket["tokens"] + (now - _bucket["ts"]) * PROXY_RPS)
        _bucket["ts"] = now
        if _bucket["tokens"] >= 1.0:
            _bucket["tokens"] -= 1.0
            return True
        return False

def read_headers(sock):
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = sock.recv(4096)
        if not d:
            break
        buf += d
        if len(buf) > (1 << 20):
            break
    head, _, rest = buf.partition(b"\r\n\r\n")
    return head, rest

def parse_req(head):
    lines = head.split(b"\r\n")
    method, path, _ = lines[0].split(b" ", 2)
    headers = []
    for l in lines[1:]:
        if b":" in l:
            k, v = l.split(b":", 1)
            headers.append((k.strip(), v.strip()))
    return method.decode(), path.decode(), headers

def rewrite(method, headers):

    drop = {b"authorization", b"x-api-key", b"host", b"connection",
            b"content-length", b"accept-encoding", b"cookie",
            b"transfer-encoding", b"expect"}
    out, clen = [], 0
    for k, v in headers:
        kl = k.lower()
        if kl == b"content-length":
            clen = int(v)
        if kl in drop or kl.startswith(b"x-pn-"):
            continue
        out.append((k, v))
    out.append((b"Host", ("%s:%d" % (UP_HOST, UP_PORT)).encode()))
    if TOKEN:
        out.append((b"Cookie", b"pp_session=" + TOKEN.encode()))
    if SESSION_SID:
        out.append((b"X-Pn-Session-Sid", SESSION_SID.encode()))
    out.append((b"Connection", b"close"))
    out.append((b"Accept-Encoding", b"identity"))
    return clen, out

def handle(client):
    try:
        head, rest = read_headers(client)
        if not head:
            client.close(); return
        method, path, headers = parse_req(head)

        _pathonly = path.split("?", 1)[0]
        _bad = (".." in _pathonly) or any(ord(c) < 0x21 for c in path)
        for _hk, _hv in headers:
            if b"\r" in _hk or b"\n" in _hk or b"\r" in _hv or b"\n" in _hv:
                _bad = True
                break
        if _bad:
            _count("block")
            log("BLOCKED %s %s (traversal/control char in request)" % (method, path))
            client.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            client.close(); return
        _count("req")
        clen, out = rewrite(method, headers)
        body = rest
        while len(body) < clen:
            d = client.recv(min(65536, clen - len(body)))
            if not d:
                break
            body += d

        if path.split("?", 1)[0].startswith("/pncompute/"):
            _count("compute")
            log_voll("COMPUTE %s %s body=%dB (cell=%s)" % (method, path.split("?")[0], len(body), PN_SESSION_CELL))
            _compute_serve(client, method, path, body)
            client.close()
            return
        deny = _policy_gate(method, path, body)
        if deny is not None:
            _count("deny")
            log("DENY %s %s (%s)" % (method, path.split("?")[0], deny))
            payload = _json.dumps({"ok": False, "error": deny, "policy": "session-allowlist"}).encode()
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Type: application/json\r\n"
                           b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(payload) + payload)
            client.close()
            return

        ckey = None
        if method == "GET" and not body and path.split("?", 1)[0] in CACHE_PATHS:
            ckey = _cache_key(path)
            hit = _cache_get(ckey)
            if hit is not None:
                _count("cache")
                log_voll("CACHE %s %s -> %dB (TTL %.1fs, ohne Portal-Roundtrip)"
                         % (method, path, len(hit), CACHE_TTL))
                client.sendall(hit)
                return
        if method != "GET":
            _cache_clear()

        if not _budget_take():
            nthr = _count("throttle")
            if LOG_LEVEL == "voll" or nthr == 1:
                log("THROTTLE %s %s -> 429 (Budget %g req/s, Burst %g — Amok-Ventil)"
                    % (method, path.split("?")[0], PROXY_RPS, PROXY_BURST))
            payload = _json.dumps({"ok": False, "policy": "broker-rate-budget",
                                   "error": "Zu viele Portal-Anfragen in kurzer Zeit (Sicherheitsventil "
                                            "%g/s, Burst %g). In 1s erneut versuchen." % (PROXY_RPS, PROXY_BURST)},
                                  ensure_ascii=False).encode("utf-8")
            client.sendall(b"HTTP/1.1 429 Too Many Requests\r\nContent-Type: application/json; charset=utf-8\r\n"
                           b"Retry-After: 1\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(payload)
                           + payload)
            return
        line = ("%s %s HTTP/1.1\r\n" % (method, path)).encode()
        hdrs = b"\r\n".join(k + b": " + v for k, v in out)
        tail = (b"\r\nContent-Length: %d\r\n\r\n" % len(body)) if method in ("POST", "PUT", "PATCH") else b"\r\n\r\n"
        req = line + hdrs + tail + body
        _count("portal")
        log_voll("REQ %s %s body=%dB -> %s (session cookie injected=%s)"
                 % (method, path, len(body), PORTAL_URL, "yes" if TOKEN else "NO"))
        raw = socket.create_connection((UP_HOST, UP_PORT), timeout=130)
        if UP_TLS:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            up = ctx.wrap_socket(raw, server_hostname=UP_HOST)
        else:
            up = raw
        up.sendall(req)
        total = 0
        chunks = [] if ckey is not None else None
        while True:
            d = up.recv(65536)
            if not d:
                break
            client.sendall(d)
            total += len(d)
            if chunks is not None:
                if total <= CACHE_BODY_MAX:
                    chunks.append(d)
                else:
                    chunks = None
        up.close()
        _count("resp_b", total)
        log_voll("RESP streamed %dB back to cell" % total)
        if ckey is not None and chunks:
            raw2 = b"".join(chunks)
            if _resp_cacheable(raw2):
                _cache_put(ckey, raw2)
    except Exception as e:
        _count("fehler")
        log("HANDLE_ERR %r" % e)
        try:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        except OSError:
            pass
    finally:
        try:
            client.close()
        except OSError:
            pass

def mux_serve(conn):
    HDRM = struct.Struct("!IBI"); DATA, CLOSE = 0, 1
    wlock = threading.Lock()
    streams = {}

    def send_frame(sid, typ, payload=b""):
        with wlock:
            conn.sendall(HDRM.pack(sid, typ, len(payload)) + payload)

    def out_forwarder(sid, a):
        while True:
            try:
                d = a.recv(65536)
            except OSError:
                break
            if not d:
                break
            send_frame(sid, DATA, d)
        send_frame(sid, CLOSE)
        try:
            a.close()
        except OSError:
            pass
        streams.pop(sid, None)

    buf = b""
    while True:
        while len(buf) < HDRM.size:
            d = conn.recv(65536)
            if not d:
                return
            buf += d
        sid, typ, ln = HDRM.unpack(buf[:HDRM.size]); buf = buf[HDRM.size:]
        if ln > MAX_FRAME:
            log("OVERSIZE frame ln=%d (sid=%d) — closing mux to protect the host" % (ln, sid))
            return
        while len(buf) < ln:
            d = conn.recv(65536)
            if not d:
                return
            buf += d
        payload = buf[:ln]; buf = buf[ln:]
        if typ == DATA:
            a = streams.get(sid)
            if a is None:
                if len(streams) >= MAX_STREAMS:
                    log("STREAM_CAP %d reached — dropping sid=%d" % (MAX_STREAMS, sid))
                    continue
                a, b = socket.socketpair()
                streams[sid] = a
                threading.Thread(target=handle, args=(b,), daemon=True).start()
                threading.Thread(target=out_forwarder, args=(sid, a), daemon=True).start()
            try:
                a.sendall(payload)
            except OSError:
                pass
        elif typ == CLOSE:
            a = streams.get(sid)
            if a:
                try:
                    a.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

def _mux_bedienen(conn):
    try:
        mux_serve(conn)
    except Exception as e:
        log("MUX_SERVE_ERR %r" % e)
    finally:
        try:
            conn.close()
        except OSError:
            pass
        log("PORTAL_BROKER_UNIX_MUX_CONN_DONE (still listening)")

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--unix-mux"
    if mode == "--tcp":
        host, port = (sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1:8089").split(":")
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, int(port))); srv.listen(8)
        log("PORTAL_BROKER_TCP %s:%s -> %s" % (host, port, PORTAL_URL))
        while True:
            c, _ = srv.accept()
            threading.Thread(target=handle, args=(c,), daemon=True).start()
    elif mode == "--unix-mux":
        sock = sys.argv[2]
        if os.path.exists(sock):
            os.unlink(sock)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock); srv.listen(64); srv.settimeout(None)
        try:
            os.chmod(sock, 0o770)
        except OSError:
            pass
        log("PORTAL_BROKER_UNIX_MUX %s -> %s (session cookie injected, multiplexed)" % (sock, PORTAL_URL))

        while True:
            try:
                conn, _ = srv.accept()
            except OSError as e:
                log("ACCEPT_ERR %r" % e)
                time.sleep(0.2)
                continue
            threading.Thread(target=_mux_bedienen, args=(conn,), daemon=True).start()

if __name__ == "__main__":
    main()
