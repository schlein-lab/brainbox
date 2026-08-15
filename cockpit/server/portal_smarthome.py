

import json
import os
import socket
import ssl
import struct
import sys
import threading
import time
import urllib.error
import urllib.request

try:
    import pn_governed as _pg
except Exception:
    _pg = None

HOME = os.path.expanduser("~")
DATA_DIR = (os.environ.get("BRAINBOX_DATA_DIR")
            or os.path.join(HOME, ".local", "share", "brainbox-portal"))
STATE_PATH = os.path.join(DATA_DIR, "smarthome.json")
OBSERVE_PATH = os.path.join(DATA_DIR, "smarthome-observe.json")
INCIDENTS_PATH = os.path.join(DATA_DIR, "smarthome-incidents.jsonl")
TOKEN_PATH = os.path.join(DATA_DIR, "smarthome-ingest.token")
WORKER = os.path.abspath(__file__)
TICK_S = 30
INTERVAL_S = 30
WALLTIME = 43200
OBSERVER_TAG = "smarthome:observer"

_LOCK = threading.RLock()
_STARTED = False
_ACTIVE = ("queued", "running", "blocked", "staged")
_TERMINAL = ("done", "failed", "cancelled", "timeout", "rejected")
_PROBE_TIMEOUT = 3.0

_PINNED_NAMES = frozenset({"zigbee2mqtt", "smarthome-kiosk"})
_ZIGBEE_OWNER_RECO = ("PINNED: USB-Radio (Sonoff ZBDongle, ezsp/EFR32, /dev/ttyUSB0). Netz-"
                      "Identitaet (AES-128-Netzschluessel, PAN/ext-PAN, Kanal, Frame-Counter) "
                      "liegt im Dongle-NVRAM. Kein active/active-Koordinator, RF-range-bound. "
                      "Owner-Aktion fuer Portabilitaet: EINMALIGER Hardware-Umbau auf LAN-"
                      "Koordinator (SLZB-06 PoE / ser2net socat tcp://ip:6638). Danach ist z2m-"
                      "Software portabel; die Radio-Box bleibt relocierbarer SPOF, strikt active/"
                      "passive mit Fencing (Standby verbindet erst bei bestaetigtem Primary-Tod, "
                      "sonst Frame-Counter-Race/Mesh-Korruption). coordinator_backup.json ist "
                      "Voraussetzung zum Restore ohne Re-Pairing und muss off-SD gesichert sein.")

def _default_descriptor():

    return {"enabled": False, "observe_only": True, "paused": False,
            "worker_job_id": None, "host": None, "arch": None,
            "components": [], "state_stores": [], "last_incident": None}

def _load():
    try:
        with open(STATE_PATH) as f:
            d = json.load(f)
        if isinstance(d, dict):
            base = _default_descriptor()
            base.update(d)
            base["observe_only"] = bool(base.get("observe_only", True))
            if not isinstance(base.get("components"), list):
                base["components"] = []
            return base
    except (OSError, ValueError):
        pass
    return _default_descriptor()

def _save(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = "%s.tmp.%d" % (STATE_PATH, os.getpid())
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)

def _load_observe():
    try:
        with open(OBSERVE_PATH) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}

def _save_observe(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = "%s.tmp.%d" % (OBSERVE_PATH, os.getpid())
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    os.replace(tmp, OBSERVE_PATH)

def _ingest_token():

    try:
        with open(TOKEN_PATH) as f:
            t = f.read().strip()
        if t:
            return t
    except OSError:
        pass
    t = os.urandom(24).hex()
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.write(fd, t.encode())
        os.close(fd)
    except OSError:
        pass
    return t

def _is_pinned(comp):
    tier = str(comp.get("tier") or "").upper()
    return bool(comp.get("pinned")) or tier.startswith("PINNED") or comp.get("name") in _PINNED_NAMES

def classify(comp):

    if _is_pinned(comp):
        return "pinned"
    tier = str(comp.get("tier") or "").lower()
    if "verify" in tier:
        return "verify"
    if "rpo" in tier or ">0" in tier or "stateful" in tier:
        return "stateful-portable"
    return "stateless-portable"

def _human_class(cls):
    return {"pinned": "pinned — Owner-Aktion noetig",
            "stateful-portable": "portabel-RPO>0 — Replikation Vorbedingung",
            "verify": "verify — Bus-Bindung unbestaetigt",
            "stateless-portable": "portabel-restartbar (RPO=0)"}.get(cls, cls)

def _probe_http(url, expect_status=200, expect_json=None, timeout=_PROBE_TIMEOUT, ca_bundle=None):

    t0 = time.time()
    ctx = None
    if url.lower().startswith("https"):
        ctx = ssl.create_default_context()
        cab = ca_bundle or os.environ.get("BRAINBOX_CA_BUNDLE")
        if cab and os.path.exists(cab):
            try:
                ctx.load_verify_locations(cab)
            except Exception:
                pass

    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "brainbox-smarthome-probe"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            code = r.getcode()
            body = r.read(65536)
        lat = (time.time() - t0) * 1000.0
        parsed = None
        try:
            parsed = json.loads(body.decode("utf-8", "replace"))
        except Exception:
            parsed = None
        ok = (code == expect_status)
        detail = "HTTP %s" % code
        if ok and expect_json:
            if not isinstance(parsed, dict):
                ok, detail = False, "erwartetes JSON fehlt (HTTP %s)" % code
            else:
                for k, v in expect_json.items():
                    if parsed.get(k) != v:
                        ok = False
                        detail = "%s=%r (erwartet %r)" % (k, parsed.get(k), v)
                        break
        return ok, lat, detail, parsed
    except urllib.error.HTTPError as e:
        return (e.code == expect_status), (time.time() - t0) * 1000.0, "HTTP %s" % e.code, None
    except Exception as e:
        return False, (time.time() - t0) * 1000.0, "%s: %s" % (type(e).__name__, e), None

def _probe_tcp(host, port, timeout=_PROBE_TIMEOUT):

    t0 = time.time()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            pass
        return True, (time.time() - t0) * 1000.0, "TCP %s:%s offen" % (host, port)
    except Exception as e:
        return False, (time.time() - t0) * 1000.0, "%s: %s" % (type(e).__name__, e)

def _mqtt_rl_encode(n):
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        if n > 0:
            b |= 0x80
        out.append(b)
        if n <= 0:
            break
    return bytes(out)

def _mqtt_read_packet(sock, deadline):

    sock.settimeout(max(0.1, deadline - time.time()))
    hdr = sock.recv(1)
    if not hdr:
        return None, b""
    mult = 1
    length = 0
    while True:
        sock.settimeout(max(0.1, deadline - time.time()))
        ch = sock.recv(1)
        if not ch:
            return None, b""
        b = ch[0]
        length += (b & 0x7F) * mult
        if not (b & 0x80):
            break
        mult *= 128
    body = b""
    while len(body) < length:
        sock.settimeout(max(0.1, deadline - time.time()))
        chunk = sock.recv(length - len(body))
        if not chunk:
            break
        body += chunk
    return hdr[0], body

def _probe_mqtt(host, port, topic, expect=None, timeout=_PROBE_TIMEOUT):

    t0 = time.time()
    deadline = t0 + timeout
    s = None
    try:
        s = socket.create_connection((host, int(port)), timeout=timeout)
        cid = ("bbxprobe%d" % (os.getpid() % 100000)).encode()
        var = b"\x00\x04MQTT\x04\x02\x00\x3c"
        payload = struct.pack("!H", len(cid)) + cid
        s.sendall(b"\x10" + _mqtt_rl_encode(len(var) + len(payload)) + var + payload)
        typ, _ = _mqtt_read_packet(s, deadline)
        if typ is None or (typ & 0xF0) != 0x20:
            return False, (time.time() - t0) * 1000.0, "kein CONNACK", None
        tb = topic.encode()
        sub = struct.pack("!H", 1) + struct.pack("!H", len(tb)) + tb + b"\x00"
        s.sendall(b"\x82" + _mqtt_rl_encode(len(sub)) + sub)
        msg = None
        while time.time() < deadline:
            typ, body = _mqtt_read_packet(s, deadline)
            if typ is None:
                break
            if (typ & 0xF0) == 0x30:
                tlen = struct.unpack("!H", body[:2])[0]
                raw = body[2 + tlen:]
                msg = raw.decode("utf-8", "replace").strip()
                break
        lat = (time.time() - t0) * 1000.0
        if msg is None:
            return False, lat, "SUBSCRIBE ok, aber kein retained message auf %s" % topic, None

        state = msg
        try:
            j = json.loads(msg)
            if isinstance(j, dict) and "state" in j:
                state = str(j["state"])
        except Exception:
            pass
        ok = True if expect is None else (state == expect)
        return ok, lat, "bridge/state=%s" % state, state
    except Exception as e:
        return False, (time.time() - t0) * 1000.0, "%s: %s" % (type(e).__name__, e), None
    finally:
        try:
            if s is not None:
                s.close()
        except Exception:
            pass

def _run_one_probe(spec, host):

    kind = (spec or {}).get("kind")
    if kind == "http":
        url = spec.get("url")
        if not url:
            host = spec.get("host") or host
            url = "http://%s:%s%s" % (host, spec.get("port", 80), spec.get("path", "/"))
        return _probe_http(url, spec.get("expect_status", 200), spec.get("expect_json"))
    if kind == "tcp":
        return _probe_tcp(spec.get("host") or host, spec.get("port")) + (None,)
    if kind == "mqtt":
        return _probe_mqtt(spec.get("host") or host, spec.get("port", 1883),
                           spec.get("topic"), spec.get("expect"))
    if kind == "none":
        return None, 0.0, "keine Probe (pinned/Display/process-only)", None
    return False, 0.0, "unbekannte Probe-Art %r" % kind, None

def probe_all(descriptor):

    host = descriptor.get("host")
    comps = descriptor.get("components") or []
    results = {}

    for c in comps:
        name = c.get("name")
        specs = c.get("probes")
        if not specs and c.get("probe"):
            specs = [c["probe"]]
        if not isinstance(specs, list):
            specs = []
        via = [s for s in specs if (s or {}).get("kind") == "via"]
        direct = [s for s in specs if (s or {}).get("kind") != "via"]
        if not direct and via:
            results[name] = {"_pending_via": via, "json": None}
            continue
        ok_all, lat, detail, jparsed = None, None, [], None
        for s in direct:
            ok, l, d, parsed = _run_one_probe(s, host)
            if parsed is not None and jparsed is None:
                jparsed = parsed
            detail.append(d)
            if ok is None:
                ok_all = ok_all if ok_all is not None else None
            elif ok is False:
                ok_all = False
            elif ok_all is None:
                ok_all = True
            lat = l if lat is None else lat
        results[name] = {"ok": ok_all, "latency_ms": lat, "detail": "; ".join(str(x) for x in detail),
                         "json": jparsed}

    for c in comps:
        name = c.get("name")
        r = results.get(name)
        if not (isinstance(r, dict) and "_pending_via" in r):
            continue
        detail = []
        ok_all = None
        for s in r["_pending_via"]:
            ref = results.get(s.get("ref")) or {}
            j = ref.get("json") or {}
            field, want = s.get("field"), s.get("equals")
            if not isinstance(j, dict) or field not in j:
                ok, d = False, "indirekt via %s.%s: kein Wert" % (s.get("ref"), field)
            else:
                ok = (j.get(field) == want)
                d = "indirekt via %s.%s=%r (erwartet %r)" % (s.get("ref"), field, j.get(field), want)
            detail.append(d)
            ok_all = ok if ok_all is None else (ok_all and ok)
        results[name] = {"ok": ok_all, "latency_ms": None, "detail": "; ".join(detail), "json": None}
    return results

def _root_cause(name, comp_by_name, results, _seen=None):

    _seen = _seen or set()
    if name in _seen:
        return name
    _seen.add(name)
    comp = comp_by_name.get(name) or {}
    for dep in comp.get("deps") or []:
        if dep not in comp_by_name:
            continue
        dr = results.get(dep) or {}
        if dr.get("ok") is False:
            return _root_cause(dep, comp_by_name, results, _seen)
    return name

def _emit_incident(rec):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(INCIDENTS_PATH, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass

def _recent_incidents(limit=20):
    out = []
    try:
        with open(INCIDENTS_PATH) as f:
            lines = f.readlines()[-limit:]
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
    except OSError:
        pass
    return out

def _alert_owner(rec):

    try:
        import portal_messages
        cls = rec.get("classification")
        subj = "Smarthome: %s %s" % (rec.get("component"), rec.get("state"))
        body = ("Komponente **%s** -> %s\n\nWurzelursache: %s\nKlasse: %s (%s)\n%s"
                % (rec.get("component"), rec.get("state"), rec.get("root_cause"),
                   cls, _human_class(cls),
                   ("Owner-Aktion: " + rec["owner_recommendation"]) if rec.get("owner_recommendation") else
                   "Phase-1 OBSERVE: keine automatische Aktion."))
        portal_messages.send("smarthome", "owner", subj, body)
    except Exception:
        pass

def _job_path():
    base = os.environ.get("PATH") or "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    local = os.path.join(HOME, ".local", "bin")
    return local + ":" + base if local not in base.split(":") else base

def _observer_argv():

    return ["python3", WORKER, "--worker", "--state", STATE_PATH,
            "--interval", str(INTERVAL_S), "--walltime", str(WALLTIME), "--tag", OBSERVER_TAG]

def _cmd_str(j):
    raw = j.get("cmd")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = [raw]
    return " ".join(map(str, raw)) if isinstance(raw, list) else str(raw)

def _pnd_jobs():
    if _pg is None:
        return []
    try:
        r = _pg.pn_req({"verb": "list"}, timeout=8)
    except Exception:
        return []
    return (r.get("jobs") or []) if isinstance(r, dict) else []

def _lane_active(jobs):

    for j in jobs:
        if j.get("state") in _ACTIVE and OBSERVER_TAG in _cmd_str(j):
            return True
    return False

def _fire(jobs):

    if _pg is None:
        return False, None, "pnd nicht verfuegbar"
    if _lane_active(jobs):
        return False, None, "laeuft schon — uebersprungen"
    cmd = _observer_argv()
    req = {"verb": "submit", "cmd": cmd, "cwd": HOME,
           "class": "inventory",
           "mem": 256, "prio": 80, "timeout": WALLTIME,
           "latency": "realtime", "tag": OBSERVER_TAG, "cpu_quota": 50,
           "disk_min": None, "mem_max": None, "room": None, "idempotent": None,
           "source": "internal",
           "env": {"PATH": _job_path(), "HOME": HOME,
                   "BRAINBOX_DATA_DIR": DATA_DIR,
                   "SMARTHOME_INGEST_TOKEN": _ingest_token()}}
    try:
        r = _pg.pn_req(req, timeout=15)
    except Exception as e:
        return False, None, "submit-Fehler: %s" % e
    if isinstance(r, dict) and r.get("ok"):
        return True, r.get("id"), "eingereiht — Job %s" % r.get("id")
    err = (r.get("error") if isinstance(r, dict) else None) or "pnd hat den Job abgelehnt"
    return False, None, err

def tick():

    with _LOCK:
        d = _load()
        if not d.get("enabled"):
            return {"fired": 0, "enabled": False}
        if d.get("paused"):
            return {"fired": 0, "enabled": True, "paused": True}
        if _pg is None or not _pg.pn_available():
            return {"fired": 0, "enabled": True, "pnd": False}
        jobs = _pnd_jobs()
        wj = d.get("worker_job_id")
        jstate = None
        if wj is not None:
            j = _pg.job(wj)
            jstate = (j or {}).get("state")
        need = (not _lane_active(jobs)) and (wj is None or jstate in _TERMINAL)
        fired = 0
        if need:
            submitted, jid, note = _fire(jobs)
            d["last_note"] = note
            if submitted:
                d["worker_job_id"] = jid
                fired = 1
            _save(d)
        return {"fired": fired, "enabled": True, "worker_job_id": d.get("worker_job_id")}

def smarthome_worker_start():

    global _STARTED
    if _STARTED:
        return
    _STARTED = True

    def _loop():
        time.sleep(25)
        while True:
            try:
                tick()
            except Exception:
                pass
            time.sleep(TICK_S)

    threading.Thread(target=_loop, name="pn-smarthome", daemon=True).start()

def status():

    with _LOCK:
        d = _load()
    obs = _load_observe()
    comp_obs = obs.get("components") or {}
    wj = d.get("worker_job_id")
    wstate = None
    if wj is not None and _pg is not None:
        wstate = (_pg.job(wj) or {}).get("state")
    running = _lane_active(_pnd_jobs()) if _pg is not None else False
    out = []
    problems = 0
    comp_by_name = {c.get("name"): c for c in d.get("components") or []}
    for c in d.get("components") or []:
        name = c.get("name")
        r = comp_obs.get(name) or {}
        ok = r.get("ok")
        if ok is False:
            problems += 1
        cls = classify(c)
        out.append({
            "name": name, "title": c.get("title") or name,
            "tier": c.get("tier"), "pinned": _is_pinned(c),
            "classification": cls, "class_human": _human_class(cls),
            "deps": c.get("deps") or [], "flags": c.get("flags") or [],
            "pinned_reason": c.get("pinned_reason"),
            "ok": ok, "latency_ms": r.get("latency_ms"),
            "detail": r.get("detail"), "root_cause": r.get("root_cause") or name,
            "last_change": r.get("last_change"),
        })
    return {"ok": True,
            "enabled": bool(d.get("enabled")), "observe_only": bool(d.get("observe_only", True)),
            "paused": bool(d.get("paused")),
            "host": d.get("host"), "arch": d.get("arch"),
            "worker_job_id": wj, "worker_state": wstate, "worker_running": running,
            "now": time.time(), "count": len(out), "problems": problems,
            "components": out,
            "state_stores": d.get("state_stores") or [],
            "last_observe_ts": obs.get("ts"),
            "last_incident": d.get("last_incident"),
            "incidents_recent": _recent_incidents(20)}

def set_enabled(on):
    with _LOCK:
        d = _load()
        d["enabled"] = bool(on)
        _save(d)
    if on and _pg is not None:
        tick()
    return {"ok": True, "enabled": bool(on)}

def set_observe_only(on):

    with _LOCK:
        d = _load()
        d["observe_only"] = bool(on)
        _save(d)
    return {"ok": True, "observe_only": bool(on)}

def pause():
    with _LOCK:
        d = _load()
        d["paused"] = True
        wj = d.get("worker_job_id")
        _save(d)
    if wj is not None and _pg is not None:
        try:
            _pg.cancel(wj)
        except Exception:
            pass
    return {"ok": True, "paused": True}

def resume():
    with _LOCK:
        d = _load()
        d["paused"] = False
        _save(d)
    if _pg is not None:
        tick()
    return {"ok": True, "paused": False}

def run_now():

    with _LOCK:
        d = _load()
        if not d.get("enabled"):
            return {"ok": False, "error": "Smarthome-Observer ist deaktiviert"}
    submitted, jid, note = _fire(_pnd_jobs())
    if submitted:
        with _LOCK:
            d = _load()
            d["worker_job_id"] = jid
            d["last_note"] = note
            _save(d)
    return {"ok": bool(submitted), "job_id": jid, "note": note}

def ingest(payload, token=None):

    if token is None or token != _ingest_token():
        return {"ok": False, "error": "unautorisiert (Token)"}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "payload muss ein Objekt sein"}
    incidents = payload.get("incidents") or []
    with _LOCK:
        d = _load()
        _save_observe({"ts": time.time(), "components": payload.get("components") or {}})
        if incidents:
            d["last_incident"] = incidents[-1]
            _save(d)
    for rec in incidents:
        _emit_incident(rec)
        _alert_owner(rec)
    return {"ok": True, "stored": len(payload.get("components") or {}), "incidents": len(incidents)}

def restart_component(name, confirm=False):

    with _LOCK:
        d = _load()
    comp = next((c for c in d.get("components") or [] if c.get("name") == name), None)
    if not comp:
        return {"ok": False, "error": "Komponente nicht gefunden"}
    cls = classify(comp)
    if _is_pinned(comp):
        reco = comp.get("pinned_reason") or (_ZIGBEE_OWNER_RECO if name == "zigbee2mqtt" else
                                             "PINNED: Hardware-genagelt (Display/GPIO/serial) — kein Software-Failover.")
        return {"ok": False, "refused": True, "pinned": True, "component": name,
                "classification": cls, "owner_recommendation": reco}
    if d.get("observe_only", True):
        return {"ok": False, "gated": True, "component": name, "classification": cls,
                "error": "observe_only aktiv — Aktuation gesperrt (Phase-1 REGISTER+OBSERVE)"}
    if not confirm:
        return {"ok": False, "needs_confirm": True, "component": name, "classification": cls,
                "error": "Owner-Bestaetigung erforderlich (confirm=true)"}
    if cls == "stateful-portable":
        return {"ok": False, "gated": True, "component": name, "classification": cls,
                "error": "RPO>0: Restart-elsewhere erst NACH State-Replikation. Vorbedingung "
                         "(Streaming-Repl/Hot-Standby bzw. WAL-ship) fehlt — sonst waere die "
                         "Failover-Zusage fuer den State-Tier hohl."}
    if cls == "verify":
        return {"ok": False, "gated": True, "component": name, "classification": cls,
                "error": "Bus-Bindung unbestaetigt — erst als portable-IF-MQTT verifizieren."}

    act = comp.get("actuation") or {}
    node_id, argv = act.get("node_id"), act.get("argv")
    if not (node_id and argv):
        return {"ok": False, "component": name, "classification": cls,
                "error": "kein Aktuations-Ziel (actuation.node_id + actuation.argv) im Descriptor — "
                         "stateless-Restart braucht ein gesundes aarch64-Ziel + Start-Kommando. "
                         "Fencing-Disziplin: strikt active/passive, genau EINE Instanz am LAN-Namen."}
    try:
        import portal_migrate
        ok, res = portal_migrate.node_exec(node_id, list(argv),
                                           mem_mb=act.get("mem_mb"), timeout_s=act.get("timeout_s"),
                                           cwd=act.get("cwd"), env=act.get("env"))
    except Exception as e:
        return {"ok": False, "component": name, "error": "Node-Agent /exec fehlgeschlagen: %s" % e}
    return {"ok": bool(ok), "component": name, "classification": cls, "node_id": node_id, "result": res}

def _worker_probe_cycle(prev_components):

    d = _load()
    results = probe_all(d)
    comp_by_name = {c.get("name"): c for c in d.get("components") or []}
    now = time.time()
    components = {}
    incidents = []
    for c in d.get("components") or []:
        name = c.get("name")
        r = results.get(name) or {}
        ok = r.get("ok")
        rc = _root_cause(name, comp_by_name, results) if ok is False else name
        prev = (prev_components or {}).get(name) or {}
        last_change = prev.get("last_change")
        if prev.get("ok") != ok:
            last_change = now
        cur = {"ok": ok, "latency_ms": r.get("latency_ms"), "detail": r.get("detail"),
               "root_cause": rc, "last_change": last_change,
               "tier": c.get("tier"), "classification": classify(c), "pinned": _is_pinned(c)}
        components[name] = cur

        if ok is False and prev.get("ok") is not False and rc == name:
            cls = classify(c)
            rec = {"ts": now, "component": name, "state": "FAIL",
                   "root_cause": rc, "tier": c.get("tier"), "pinned": _is_pinned(c),
                   "classification": cls, "class_human": _human_class(cls),
                   "detail": r.get("detail")}
            if cls == "pinned":
                rec["owner_recommendation"] = (c.get("pinned_reason")
                                               or (_ZIGBEE_OWNER_RECO if name == "zigbee2mqtt" else
                                                   "Hardware-genagelt — Owner-Aktion, kein Auto-Failover."))
            incidents.append(rec)
        elif ok is True and prev.get("ok") is False:
            incidents.append({"ts": now, "component": name, "state": "RECOVER",
                              "classification": classify(c)})
    return components, incidents

def worker_loop(interval=INTERVAL_S, walltime=WALLTIME):

    start = time.time()
    portal = os.environ.get("SMARTHOME_PORTAL_URL") or _detect_portal_url()
    token = os.environ.get("SMARTHOME_INGEST_TOKEN") or _ingest_token()
    prev = (_load_observe().get("components") or {})
    while time.time() - start < (walltime - interval - 30):
        try:
            components, incidents = _worker_probe_cycle(prev)
            prev = components
            payload = {"components": components, "incidents": incidents, "ts": time.time()}
            _save_observe({"ts": time.time(), "components": components})
            if portal:
                _post_ingest(portal, token, payload)
        except Exception as e:
            sys.stderr.write("smarthome worker cycle error: %s\n" % e)
        time.sleep(interval)

def _detect_portal_url():
    for env in ("SMARTHOME_PORTAL_URL", "PORTAL_URL", "BRAINBOX_PORTAL_URL"):
        v = os.environ.get(env)
        if v:
            return v.rstrip("/")
    return None

def _post_ingest(portal, token, payload):
    try:
        body = json.dumps({"token": token, "payload": payload}).encode()
        req = urllib.request.Request(portal.rstrip("/") + "/api/smarthome/ingest",
                                     data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        ctx = None
        if portal.lower().startswith("https"):
            ctx = ssl.create_default_context()
            cab = os.environ.get("BRAINBOX_CA_BUNDLE")
            if cab and os.path.exists(cab):
                ctx.load_verify_locations(cab)
        urllib.request.urlopen(req, timeout=5, context=ctx).read()
    except Exception:
        pass

def _selftest():

    import http.server
    import threading as _th

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/health":
                b = b'{"db":"ok"}'
            elif self.path == "/brainfail":
                self.send_response(500); self.end_headers(); return
            else:
                b = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    _th.Thread(target=srv.serve_forever, daemon=True).start()

    desc = {
        "enabled": False, "observe_only": True, "host": "127.0.0.1", "arch": "test",
        "components": [
            {"name": "smarthome-backend", "tier": "portable",
             "deps": ["postgres"],
             "probes": [{"kind": "http", "url": "http://127.0.0.1:%d/health" % port,
                         "expect_json": {"db": "ok"}}]},
            {"name": "postgres", "tier": "portable-RPO>0", "deps": ["network"],
             "probes": [{"kind": "via", "ref": "smarthome-backend", "field": "db", "equals": "ok"}]},
            {"name": "smarthome-frontend", "tier": "portable", "deps": ["network"],
             "probes": [{"kind": "http", "url": "http://127.0.0.1:%d/" % port}]},
            {"name": "deadservice", "tier": "portable", "deps": ["network"],
             "probes": [{"kind": "tcp", "host": "127.0.0.1", "port": 9}]},
            {"name": "zigbee2mqtt", "tier": "PINNED", "deps": ["mosquitto", "serial"],
             "pinned_reason": "USB-Radio", "probes": [{"kind": "none"}]},
            {"name": "smarthome-kiosk", "tier": "PINNED", "deps": [],
             "probes": [{"kind": "none"}]},
        ],
    }

    global STATE_PATH
    _orig = STATE_PATH
    import tempfile
    tmpd = tempfile.mkdtemp()
    STATE_PATH = os.path.join(tmpd, "smarthome.json")
    with open(STATE_PATH, "w") as f:
        json.dump(desc, f)
    results = probe_all(desc)
    STATE_PATH = _orig

    checks = []
    checks.append(("backend ok", results["smarthome-backend"]["ok"] is True))
    checks.append(("postgres via-derived ok", results["postgres"]["ok"] is True))
    checks.append(("frontend ok", results["smarthome-frontend"]["ok"] is True))
    checks.append(("deadservice fail", results["deadservice"]["ok"] is False))

    cbn = {c["name"]: c for c in desc["components"]}
    checks.append(("zigbee pinned", classify(cbn["zigbee2mqtt"]) == "pinned"))
    checks.append(("kiosk pinned", classify(cbn["smarthome-kiosk"]) == "pinned"))
    checks.append(("backend stateless-portable", classify(cbn["smarthome-backend"]) == "stateless-portable"))
    checks.append(("postgres stateful-portable", classify(cbn["postgres"]) == "stateful-portable"))

    STATE_PATH = os.path.join(tmpd, "smarthome.json")
    r_zig = restart_component("zigbee2mqtt", confirm=True)
    checks.append(("zigbee restart REFUSED", r_zig.get("refused") is True and r_zig.get("pinned") is True))
    checks.append(("zigbee owner reco present", bool(r_zig.get("owner_recommendation"))))

    r_back = restart_component("smarthome-backend", confirm=True)
    checks.append(("backend gated by observe_only", r_back.get("gated") is True))
    STATE_PATH = _orig

    desc2 = json.loads(json.dumps(desc))
    for c in desc2["components"]:
        if c["name"] == "postgres":
            c["probes"] = [{"kind": "tcp", "host": "127.0.0.1", "port": 9}]
        if c["name"] == "smarthome-backend":
            c["probes"] = [{"kind": "http", "url": "http://127.0.0.1:%d/brainfail" % port}]
            c["deps"] = ["postgres"]
    res2 = probe_all(desc2)
    cbn2 = {c["name"]: c for c in desc2["components"]}
    rc = _root_cause("smarthome-backend", cbn2, res2)
    checks.append(("root-cause = postgres (no symptom flood)", rc == "postgres"))

    srv.shutdown()
    ok = all(v for _, v in checks)
    for label, v in checks:
        print("  [%s] %s" % ("PASS" if v else "FAIL", label))
    print("SELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1

def _main(argv):
    args = argv[1:]
    if "--selftest" in args:
        return _selftest()
    if "--worker" in args:
        def _get(flag, dflt):
            return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else dflt
        global STATE_PATH
        STATE_PATH = _get("--state", STATE_PATH)
        worker_loop(int(_get("--interval", INTERVAL_S)), int(_get("--walltime", WALLTIME)))
        return 0
    if "--probe-once" in args:
        def _get(flag, dflt):
            return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else dflt
        STATE_PATH2 = _get("--state", STATE_PATH)
        globals()["STATE_PATH"] = STATE_PATH2
        d = _load()
        comps, incidents = _worker_probe_cycle(_load_observe().get("components") or {})
        _save_observe({"ts": time.time(), "components": comps})
        print(json.dumps({"host": d.get("host"), "components": comps, "incidents": incidents},
                         indent=1, ensure_ascii=False))
        return 0

    print(json.dumps(status(), indent=1, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    sys.exit(_main(sys.argv))
