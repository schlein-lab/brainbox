

import json
import shlex
import os
import threading
import time

try:
    import pn_governed as _pg
except Exception:
    _pg = None

try:
    from _sitedev import addr as _dev_addr
except Exception:
    def _dev_addr(role, default=None):
        return default

HOME = os.path.expanduser("~")
DATA_DIR = (os.environ.get("BRAINBOX_DATA_DIR")
            or os.path.join(HOME, ".local", "share", "brainbox-portal"))
STATE = os.path.join(DATA_DIR, "pipeline.json")
BATCH = os.path.join(HOME, ".local", "bin", "pn-batch-run")

def _node_ready(node_id):

    try:
        import portal_placement as _plc
        for n in _plc.nodes():
            if n.get("id") == node_id:
                return n.get("state") == "online" and not n.get("draining")
    except Exception:
        return False
    return False

def _llm_url():

    url = os.environ.get("PN_OFFLOAD_LLM_URL")
    if url is not None:
        return url
    return "https://%s:8077" % _self_lan_ip()

def _self_lan_ip():

    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return "127.0.0.1"

def _offload_env():

    env = {"BRAINARBEIT_URL": _llm_url()}
    _purl = os.environ.get("PN_OFFLOAD_PORTAL_URL", "").strip()
    if _purl:
        env["PORTAL_URL"] = _purl
    _keyf = os.environ.get("PN_OFFLOAD_KEY_FILE", "").strip()
    if _keyf:
        try:
            env["BRAINARBEIT_KEY"] = open(os.path.expanduser(_keyf)).read().strip()
        except OSError:
            pass
    _envf = os.environ.get("PN_OFFLOAD_ENV_FILE", "").strip()
    if _envf:
        try:
            for _ln in open(os.path.expanduser(_envf)):
                _ln = _ln.strip()
                if _ln and not _ln.startswith("#") and "=" in _ln:
                    _k, _v = _ln.split("=", 1)
                    env[_k.strip()] = _v.strip().strip('"').strip("'")
        except OSError:
            pass
    return env

_BOXHOME = HOME

def _portable_node_cmd(argv):

    parts = []
    for a in argv:
        if a.startswith(_BOXHOME + "/"):
            parts.append('"$HOME/%s"' % a[len(_BOXHOME) + 1:])
        elif "=" in a and not a.startswith("/"):
            k, v = a.split("=", 1)
            if v.startswith(_BOXHOME + "/"):
                parts.append('%s="$HOME/%s"' % (k, v[len(_BOXHOME) + 1:]))
            else:
                parts.append(shlex.quote(a))
        else:
            parts.append(shlex.quote(a))
    inner = " ".join(parts)
    return ["bash", "-lc",
            'cd "${PN_WORKER_DIR:-$HOME}" 2>/dev/null; export PATH="$HOME/.local/bin:$PATH"; exec ' + inner]

_LOCK = threading.RLock()
_STARTED = False
RUN_GATE = None

_ACTIVE = ("queued", "running", "blocked", "staged")

def _job_path():

    base = os.environ.get("PATH") or "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    local = os.path.join(HOME, ".local", "bin")
    return local + ":" + base if local not in base.split(":") else base

def _load():
    try:
        with open(STATE) as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("enabled", False)
            d.setdefault("lanes", [])
            if isinstance(d["lanes"], list):
                return d
    except (OSError, ValueError):
        pass
    return {"enabled": False, "lanes": []}

def _save(d):
    tmp = "%s.tmp.%d" % (STATE, os.getpid())
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    os.replace(tmp, STATE)

def _pnd_jobs():

    if _pg is None:
        return []
    try:
        r = _pg.pn_req({"verb": "list"}, timeout=8)
    except Exception:
        return []
    return (r.get("jobs") or []) if isinstance(r, dict) else []

def _cmd_str(j):
    raw = j.get("cmd")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = [raw]
    return " ".join(map(str, raw)) if isinstance(raw, list) else str(raw)

_DEDUPKEY = []

def _dedupkey_mod():

    if _DEDUPKEY:
        return _DEDUPKEY[0]
    mod = None
    try:
        from pnlib import dedupkey as _dk
        mod = _dk
    except ImportError:
        import importlib.util
        for base in (os.environ.get("PNLIB_HOME"),
                     os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                         os.path.abspath(__file__)))), "engine"),
                     os.path.expanduser("~/portioneer")):
            if not base:
                continue
            pfad = os.path.join(base, "pnlib", "dedupkey.py")
            if not os.path.isfile(pfad):
                continue
            try:
                spec = importlib.util.spec_from_file_location("_pn_dedupkey", pfad)
                cand = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(cand)
                mod = cand
                break
            except Exception:
                continue
    _DEDUPKEY.append(mod)
    return mod

def _dedup_key(lane):

    argv = lane.get("argv") or []
    dk = _dedupkey_mod()
    if dk is not None:
        key = dk.lane_key(argv, HOME)
        if key:
            return key
    else:
        toks = [t for t in argv
                if "=" not in t and t != "/usr/bin/env" and not t.endswith("/env")]
        if toks:
            return " ".join(toks)
    return "pipeline:" + str(lane.get("name") or "")

def _lane_active(lane, jobs):
    key = _dedup_key(lane)
    if not key:
        return False
    dk = _dedupkey_mod()
    for j in jobs:
        if j.get("state") not in _ACTIVE:
            continue
        cmd = _cmd_str(j)
        if dk.matches(key, cmd, HOME) if dk is not None else (key in cmd):
            return True
    return False

def _fire(lane, jobs):

    if _pg is None:
        return False, None, "pnd nicht verfügbar"
    if _lane_active(lane, jobs):
        return False, None, "läuft noch — übersprungen"
    argv = list(lane.get("argv") or [])
    if not argv:
        return False, None, "leeres argv"
    deadline = str(int(lane.get("deadline", 1500)))
    idle = str(int(lane.get("idle_tick", 6)))
    _node = lane.get("node")
    _off = bool(_node) and _node_ready(_node)

    cmd = _portable_node_cmd(argv) if _off else ([BATCH, deadline, idle, "--"] + argv)

    req = {"verb": "submit", "cmd": cmd, "cwd": ("/" if _off else HOME),
           "class": lane.get("class"), "mem": int(lane.get("mem", 900)),
           "prio": None, "timeout": int(lane.get("timeout", 1800)),
           "latency": None, "tag": "pipeline:" + str(lane.get("name")),

           "cpu_quota": min(int(lane.get("cpu_quota") or 100), 200),
           "disk_min": None, "mem_max": None, "room": None, "idempotent": None,
           "source": "filler",
           "env": (_offload_env()
                   if _off else {"PATH": _job_path(), "HOME": HOME})}
    if _off:
        req["node"] = _node
    try:
        r = _pg.pn_req(req, timeout=15)
    except Exception as e:
        return False, None, "submit-Fehler: %s" % e
    if isinstance(r, dict) and r.get("ok"):
        return True, r.get("id"), "eingereiht — Job %s" % r.get("id")
    err = (r.get("error") if isinstance(r, dict) else None) or "pnd hat den Job abgelehnt"
    return False, None, err

LEER_DECKEL = 6
LEER_MINDEST_S = 12

def _leer_schwelle(lane):

    try:
        takt = int(lane.get("idle_tick") or 6)
    except (TypeError, ValueError):
        takt = 6
    return max(2 * takt, LEER_MINDEST_S)

def _job_aus_db(jid):

    pfad = os.path.join(HOME, ".local", "share", "portioneer", "queue.db")
    try:
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % pfad, uri=True, timeout=5)
        try:
            r = con.execute("SELECT state, started_at, finished_at FROM jobs WHERE id=?",
                            (jid,)).fetchone()
        finally:
            con.close()
    except Exception:
        return None
    if not r:
        return None
    return {"id": jid, "state": r[0], "started_at": r[1], "finished_at": r[2]}

def _war_leer(lane, jobs):

    jid = lane.get("last_job_id")
    if not jid or jid == lane.get("leer_geprueft"):
        return None
    j = next((x for x in jobs if x.get("id") == jid), None)
    if j is None:

        j = _job_aus_db(jid)
    if not j or j.get("state") not in ("done", "failed", "timeout", "cancelled"):
        return None
    try:
        dauer = float(j.get("finished_at")) - float(j.get("started_at"))
    except (TypeError, ValueError):
        return None
    lane["leer_geprueft"] = jid
    return dauer < _leer_schwelle(lane)

def _naechster_takt(lane, now):

    grund = int(lane.get("every_s") or 600)
    faktor = min(1 + int(lane.get("leer_serie") or 0), LEER_DECKEL)
    return now + grund * faktor

def tick():

    with _LOCK:
        d = _load()
        if not d.get("enabled"):
            return {"fired": 0, "enabled": False}
        lanes = d.get("lanes") or []
        now = time.time()
        jobs = _pnd_jobs()
        fired = 0
        for lane in lanes:
            if lane.get("paused"):
                continue
            if float(lane.get("next_ts") or 0) > now:
                continue

            leer = _war_leer(lane, jobs)
            if leer is True:
                lane["leer_serie"] = int(lane.get("leer_serie") or 0) + 1
            elif leer is False:
                lane["leer_serie"] = 0
            submitted, jid, note = _fire(lane, jobs)
            lane["last_note"] = note
            if submitted:
                lane["last_ts"] = now
                lane["last_job_id"] = jid
                lane["runs"] = int(lane.get("runs") or 0) + 1
                fired += 1
                jobs = _pnd_jobs()

            lane["next_ts"] = _naechster_takt(lane, now)
            if int(lane.get("leer_serie") or 0) >= 2:
                lane["last_note"] = "%s · %dx nichts gefunden, Takt gestreckt auf %d s" % (
                    note, int(lane["leer_serie"]), int(lane["next_ts"] - now))
        _save(d)
        return {"fired": fired, "enabled": True, "lanes": len(lanes)}

def pipeline_worker_start():

    global _STARTED
    if _STARTED:
        return
    _STARTED = True

    def _loop():
        time.sleep(25)
        while True:
            try:
                if RUN_GATE is None or RUN_GATE():
                    tick()
            except Exception:
                pass
            time.sleep(30)

    threading.Thread(target=_loop, name="pn-pipeline", daemon=True).start()

def status():

    with _LOCK:
        d = _load()
    jobs = _pnd_jobs()
    by_id = {}
    for j in jobs:
        try:
            by_id[int(j.get("id"))] = j
        except (TypeError, ValueError):
            pass
    now = time.time()
    out = []
    active = 0
    problems = 0
    for lane in d.get("lanes") or []:
        jid = lane.get("last_job_id")
        j = by_id.get(int(jid)) if jid else None
        live = (j or {}).get("state") if j else None
        exit_code = (j or {}).get("exit_code") if j else None
        running = _lane_active(lane, jobs)
        if running:
            active += 1
        if live in ("failed", "timeout") or (live == "done" and exit_code not in (None, 0)):
            problems += 1
        out.append({
            "name": lane.get("name"), "group": lane.get("group"),
            "title": lane.get("title") or lane.get("name"),
            "every_s": lane.get("every_s"), "paused": bool(lane.get("paused")),
            "last_ts": lane.get("last_ts"), "next_ts": lane.get("next_ts"),
            "runs": lane.get("runs") or 0, "last_job_id": jid,
            "last_state": live, "last_exit": exit_code, "running": running,
            "note": lane.get("last_note"),
        })
    return {"ok": True, "enabled": bool(d.get("enabled")), "now": now,
            "count": len(out), "active": active, "problems": problems, "lanes": out}

def set_enabled(on):
    with _LOCK:
        d = _load()
        d["enabled"] = bool(on)
        if on:
            now = time.time()
            for i, lane in enumerate(d.get("lanes") or []):

                if not lane.get("next_ts") or float(lane["next_ts"]) > now + 3600:
                    lane["next_ts"] = now + i * 12
        _save(d)
    return {"ok": True, "enabled": bool(on)}

def set_lane_paused(name, paused):
    with _LOCK:
        d = _load()
        for lane in d.get("lanes") or []:
            if lane.get("name") == name:
                lane["paused"] = bool(paused)
                _save(d)
                return {"ok": True, "name": name, "paused": bool(paused)}
    return {"ok": False, "error": "lane nicht gefunden"}

def run_now(name):

    with _LOCK:
        d = _load()
        lane = next((l for l in (d.get("lanes") or []) if l.get("name") == name), None)
    if not lane:
        return {"ok": False, "error": "lane nicht gefunden"}
    submitted, jid, note = _fire(lane, _pnd_jobs())
    with _LOCK:
        d = _load()
        for l in d.get("lanes") or []:
            if l.get("name") == name:
                if submitted:
                    l["last_ts"] = time.time()
                    l["last_job_id"] = jid
                    l["runs"] = int(l.get("runs") or 0) + 1
                l["last_note"] = note
                l["next_ts"] = time.time() + int(l.get("every_s") or 600)
        _save(d)
    return {"ok": bool(submitted), "job_id": jid, "note": note}
