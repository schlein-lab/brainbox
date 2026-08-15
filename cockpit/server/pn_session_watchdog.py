#!/usr/bin/env python3

from __future__ import annotations

import os
import threading
import time

def _envf(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)

def _envi(name, default):
    try:
        return int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return int(default)

def _envb(name, default):
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() not in ("0", "false", "no", "off", "")

TICK_S = _envf("PN_WATCH_TICK_S", 20)
SEAT_FAIL_MAX = _envi("PN_WATCH_SEAT_FAILS", 2)
MAX_RESTARTS = _envi("PN_WATCH_MAX_RESTARTS", 3)
TERM_GRACE_S = _envf("PN_TERM_GRACE_S", 30.0)
TURN_STALL_S = _envf("PN_TURN_STALL_S", 300)
STALL_CPU_PCT = _envf("PN_WATCH_STALL_CPU_PCT", 1.0)
KEEP_WARM_S = _envf("PN_WATCH_KEEP_WARM_S", 45 * 60)
ENABLED = _envb("PN_WATCH_ENABLED", True)
STALL_ON = _envb("PN_WATCH_STALL", False)
IDLE_SWEEP_ON = _envb("PN_IDLE_SWEEP", True)
RECONCILE_ON = _envb("PN_WATCH_RECONCILE", True)
RECONCILE_EVERY_S = _envf("PN_WATCH_RECONCILE_EVERY_S", 30 * 60)
RECONCILE_GRACE_S = _envf("PN_WATCH_RECONCILE_GRACE_S", 10 * 60)
ORPHAN_SWEEP_ON = _envb("PN_WATCH_ORPHAN_SWEEP", False)
PROACTIVE_ON = _envb("PN_WATCH_PROACTIVE", False)

_LOCK = threading.Lock()
_HEALTH = {}
_RESTARTS = {}
_TERM_FAILS = {}

_SEAT_FAIL = {}
_TERM_KICK_AT = {}
_JSONL = {}
_CPU = {}
_RESTARTING = set()
_STARTED = False
_CTX = None

PROGRESS_S = _envf("PN_PROGRESS_PROBE_S", 180.0)
_PROG_TS = {}

def _probe_progress(cell, key, now):

    if PROGRESS_S <= 0 or not hasattr(cell, "read_progress"):
        return
    if now - _PROG_TS.get(key, 0) < PROGRESS_S:
        return
    _PROG_TS[key] = now
    try:
        _bounded(cell.read_progress, 9.0, None)
    except Exception:
        pass

OBSERVER_S = _envf("PN_OBSERVER_S", 300.0)

REFUSAL_ON = _envb("PN_WATCH_REFUSAL", True)
REFUSAL_NUDGE_S = _envf("PN_REFUSAL_NUDGE_S", 90.0)
REFUSAL_MAX_NUDGES = _envi("PN_REFUSAL_MAX_NUDGES", 3)
REFUSAL_TAIL = _envi("PN_REFUSAL_TAIL_BYTES", 262144)

_REFUSAL_MARKERS = (
    "unable to respond to this request, which appears to violate",
    "safeguards flagged this message",
)

_REFUSAL_NUDGES = {1: "Weiter.",
                   2: "/compact",
                   3: "Bitte mach mit dem naechsten Schritt weiter."}
_REF = {}
OBSERVER_MODEL = os.environ.get("PN_OBSERVER_MODEL", "sonnet")
_OBS = {}

OBS_STATE_PATH = os.path.join(os.path.expanduser("~"), ".local", "share", "brainbox-portal",
                              "watchdog_observer.json")

def _obs_load():
    import json as _json
    try:
        with open(OBS_STATE_PATH, "r", encoding="utf-8") as f:
            d = _json.load(f)
    except Exception:
        return
    for k, st in (d.items() if isinstance(d, dict) else []):
        if "|" in k and isinstance(st, dict):
            uid, _, sid = k.partition("|")
            _OBS[(uid, sid)] = {"ts": float(st.get("ts") or 0.0), "size": int(st.get("size", -1)),
                                "running": bool(st.get("running")),
                                "problem_ts": float(st.get("problem_ts") or 0.0)}

def _obs_save():
    import json as _json
    try:
        os.makedirs(os.path.dirname(OBS_STATE_PATH), exist_ok=True)
        tmp = OBS_STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump({"%s|%s" % k: v for k, v in _OBS.items()}, f)
        os.replace(tmp, OBS_STATE_PATH)
    except Exception:
        pass
OBS_PROMPT = (
    "Du bist der stille KOMMENTATOR einer Claude-Session - eine zweite Meinung fuer den Besitzer. "
    "Angehaengt sind die juengsten Zuege ihres JSONL-Transcripts.\n"
    "So funktioniert dein Output (WICHTIG): Deine erste Zeile 'STATUS: ...' wird MASCHINELL "
    "geparst; bei 'STATUS: problem' wird der Besitzer AUTOMATISCH benachrichtigt. Du kannst und "
    "musst niemanden selbst informieren - du hast keine Werkzeuge und brauchst auch keine. "
    "Vom Melden/Informieren schreibst du im Kommentar deshalb NIE.\n"
    "STATUS: problem gilt NUR, wenn die Session an der IDENTISCHEN Huerde festhaengt und keine "
    "neuen Loesungswege mehr versucht (ohne Eingriff nicht loesbar, z. B. fehlende Rechte/"
    "Netzroute/Werkzeuge). Solange NEUE Ansaetze probiert werden: STATUS: ok - egal wie viele "
    "Versuche es schon waren. Bedenke: dein Transcript-Ausschnitt kann einige Minuten alt sein; "
    "zeigt das Ende bereits einen Durchbruch, ist es ok.\n"
    "Dein Kommentar danach (2-4 kurze Saetze, verstaendlich ohne Jargon): Erzaehle beschreibend "
    "und reflektierend, WAS gerade passiert, was erreicht wurde und was als naechstes ansteht - "
    "gern mit einer Beobachtung, die dem Besitzer eine neue Perspektive auf seine Session gibt. "
    "Ob die Session vorankommt oder haengt, steht AUSSCHLIESSLICH in der STATUS-Zeile - im "
    "Kommentar diskutierst du das NICHT.\n"
    "Antworte EXAKT in diesem Format:\n"
    "STATUS: ok ODER STATUS: problem\n"
    "<dein Kommentar>\n"
    "Gib niemals Geheimniswerte (Schluessel, Passwoerter, Tokens) aus.")

def _box_lang_code():

    try:
        for _ln in open("/etc/brainbox/site.conf", encoding="utf-8", errors="replace"):
            _t = _ln.strip()
            if _t.startswith("LANG_UI"):
                return _t.split("=", 1)[1].strip().strip("'\"").lower()[:5]
    except Exception:
        pass
    return "de"

def reply_lang_note(keep=""):

    _l = _box_lang_code() or "de"
    if not _l or _l.startswith("de"):
        return ""
    _names = {"en": "English", "fr": "French", "es": "Spanish", "it": "Italian",
              "pt": "Portuguese", "nl": "Dutch", "pl": "Polish", "tr": "Turkish", "ru": "Russian"}
    _nm = _names.get(_l[:2], _l)
    _k = (" " + keep) if keep else ""
    return ("\n\nIMPORTANT: Write your reply in %s, even though the instructions above are "
            "written in German and may ask you to answer in German.%s" % (_nm, _k))

OBS_PROMPT = OBS_PROMPT + reply_lang_note("Keep the 'STATUS:' line exactly as specified above.")

def _observe(ctx, cell, key, now):

    if OBSERVER_S <= 0 or not hasattr(cell, "observer_start"):
        return
    principal, sid = key
    if "voice" in str(sid):
        return
    st = _OBS.setdefault(key, {"ts": 0.0, "size": -1, "running": False, "problem_ts": 0.0})
    if st["running"]:
        res = _bounded(cell.observer_collect, 9.0, None)
        if res is None:
            if now - st["ts"] > 420:
                st["running"] = False
                _obs_save()
            return
        st["running"] = False
        _obs_save()
        _obs_result(ctx, cell, key, res, now)
        return
    if now - st["ts"] < OBSERVER_S:
        return
    try:
        path = _bounded(cell._incell_active_jsonl, 8.0, None)
        size = _bounded(lambda: cell._incell_jsonl_size(path), 8.0, None) if path else None
    except Exception:
        return
    if not path or size is None:
        return
    if st["size"] < 0:
        st["size"] = size; st["ts"] = now
        _obs_save()
        return
    if size == st["size"]:
        return
    st["size"] = size
    st["ts"] = now
    if _bounded(lambda: cell.observer_start(OBS_PROMPT, path, OBSERVER_MODEL), 9.0, False):
        st["running"] = True
    _obs_save()

def _obs_result(ctx, cell, key, res, now):

    principal, sid = key
    out = (res.get("out") or "").strip()
    if not out:
        err = " ".join((res.get("err") or "").split())[:200]
        if err:
            print("[observer] %s/%s Lauf ohne Ausgabe (stderr: %s)" % (principal, sid, err), flush=True)
        return
    first, _, rest = out.partition("\n")
    is_status = first.upper().startswith("STATUS")
    problem = is_status and "problem" in first.lower()
    comment = (rest if is_status else out).strip() or out
    try:
        cell._observer = {"ts": now, "text": comment[:2000], "problem": problem}
    except Exception:
        pass
    cctx = _chanctx(ctx)
    if cctx is not None:
        try:
            import portal_channels
            portal_channels.bus_append(cctx, principal, sid, "message", role="observer",
                                       text=("\u2757 " if problem else "") + comment[:1500],
                                       notify=("alert" if problem else "ambient"))
        except Exception:
            pass
    if problem and now - _OBS.get(key, {}).get("problem_ts", 0) > 1800:
        _OBS[key]["problem_ts"] = now
        _obs_save()
        _bus(ctx, principal, sid, "observer_problem", "attention", comment[:300])

def _is_refusal(text):

    t = (text or "").lstrip()
    if not t.startswith("API Error"):
        return False
    low = t.lower()
    return any(m in low for m in _REFUSAL_MARKERS)

def _refusal_map(ctx):

    cc = _chanctx(ctx)
    if cc is None:
        return {}
    import json as _json
    try:
        import portal_channels as _pc
        path = os.path.join(cc.get("data_dir") or "", _pc.BUS_NAME)
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > REFUSAL_TAIL:
                f.seek(size - REFUSAL_TAIL)
                f.readline()
            data = f.read()
    except (OSError, ValueError, ImportError):
        return {}
    out = {}
    for line in data.split(b"\n"):
        if not line.strip():
            continue
        try:
            d = _json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if d.get("kind") != "message" or d.get("role") != "assistant":
            continue
        sid = d.get("sid")
        if sid:
            out[sid] = (float(d.get("ts") or 0.0), str(d.get("text") or ""))
    return out

def _refusal_nudge(ctx, principal, sid, n):

    cc = _chanctx(ctx)
    if cc is None:
        return False
    ok = False
    try:
        import portal_channels as _pc
        payload, code = _pc.session_say(cc, principal,
                                        {"sid": sid, "text": _REFUSAL_NUDGES.get(n, "Weiter."),
                                         "origin": "watchdog"})
        ok = bool(payload.get("ok")) and int(code) == 200
    except Exception:
        ok = False
    _bus(ctx, principal, sid, "refusal_nudge", "attention",
         "Modell-Ablehnung erkannt - Anstupser %d/%d %s"
         % (n, REFUSAL_MAX_NUDGES, "gesendet" if ok else "FEHLGESCHLAGEN"))
    return ok

def _probe_refusal(ctx, cell, key, now, refmap):

    principal, sid = key
    if "voice" in str(sid):
        return
    ent = refmap.get(sid)
    if not ent:
        return
    ts, text = ent
    st = _REF.get(key)
    if not _is_refusal(text):
        if st:
            _bus(ctx, principal, sid, "refusal_cleared", "warm",
                 "Turn laeuft wieder (nach %d Anstupser(n))" % st.get("n", 0))
            _REF.pop(key, None)
        return
    st = _REF.setdefault(key, {"n": 0, "ts": 0.0, "at": 0.0, "alert_ts": 0.0})
    if now - st["ts"] < REFUSAL_NUDGE_S:
        return
    if st["n"] >= REFUSAL_MAX_NUDGES:

        if now - st.get("alert_ts", 0.0) > 1800:
            st["alert_ts"] = now
            _bus(ctx, principal, sid, "refusal_stuck", "attention",
                 "Session wird vom Modell-Klassifikator dauerhaft abgelehnt (%d Anstupser ohne "
                 "Erfolg). Kontext verdichten oder Aufgabe frisch aufsetzen." % st["n"])
        return
    st["n"] += 1
    st["ts"] = now
    st["at"] = ts
    _refusal_nudge(ctx, principal, sid, st["n"])

def _now():
    return time.time()

def _bounded(fn, timeout, default):
    box = {"v": default, "done": False}
    def _run():
        try:
            box["v"] = fn()
        except Exception:
            box["v"] = default
        finally:
            box["done"] = True
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    return box["v"] if box["done"] else default

def _mgr(ctx):
    try:
        m = ctx.get("cell_manager")
        return m() if callable(m) else m
    except Exception:
        return None

def _chanctx(ctx):
    try:
        c = ctx.get("chan_ctx")
        return (c() if callable(c) else c) if c else None
    except Exception:
        return None

def _bus(ctx, principal, sid, event, state, reason=""):

    cc = _chanctx(ctx)
    if not cc:
        return
    try:
        import portal_channels as _pc
        _pc.bus_append(cc, principal, sid, "lifecycle", event=event, state=state,
                       reason=(reason or "")[:400], title=str(sid))
    except Exception:
        pass

def _kick(ctx, key, code=4002, msg=b"[Session-VM reagiert nicht - starte neu ...]"):

    fn = ctx.get("kick_attached") if ctx else None
    try:
        if callable(fn):
            return fn(key, code, msg)
        import portal_terminal as _pt
        return _pt.kick_attached(key, code, msg)
    except Exception:
        return False

def _has_attach(key):

    try:
        import portal_terminal as _pt
        with _pt._CELL_TERM_LK:
            c = _pt._CELL_TERM_ATTACHED.get(key)
        return c is not None and not isinstance(c, _pt._HolderConn)
    except Exception:
        return False

def _budget_ok(key, now):
    with _LOCK:
        hist = [t for t in _RESTARTS.get(key, []) if now - t < 3600]
        _RESTARTS[key] = hist
        return len(hist) < MAX_RESTARTS

def _budget_note(key, now):
    with _LOCK:
        _RESTARTS.setdefault(key, []).append(now)
        return len([t for t in _RESTARTS[key] if now - t < 3600])

def _set_health(key, state, reason, restarts):
    with _LOCK:
        _HEALTH[key] = {"state": state, "reason": reason, "restarts": restarts}

def _clear_health(key, clear_budget=True):
    with _LOCK:
        if _HEALTH.get(key) is not None:
            _HEALTH[key] = None
        _SEAT_FAIL[key] = 0
        if clear_budget:
            _RESTARTS[key] = []

def health(uid, sid):

    with _LOCK:
        return _HEALTH.get((uid, sid))

def _vmm_cpu_pct(pid, now):

    try:
        with open("/proc/%d/stat" % pid) as f:
            parts = f.read().rsplit(") ", 1)[1].split()
        busy = int(parts[11]) + int(parts[12])
    except Exception:
        return None
    clk = os.sysconf("SC_CLK_TCK") or 100
    prev = _CPU.get(pid)
    _CPU[pid] = (busy, now)
    if prev and now > prev[1]:
        return max(0.0, 100.0 * (busy - prev[0]) / clk / (now - prev[1]))
    return None

def _listener_present(path):

    try:
        with open("/proc/net/unix") as f:
            for line in f:
                parts = line.split()
                if parts and parts[-1] == path:
                    return True
        return False
    except Exception:
        return True

def _probe(ctx, cell, key, now):

    proc = getattr(cell, "proc", None)
    if proc is None:
        return ("off", "")
    if proc.poll() is not None:
        return ("dead", "VM-Prozess beendet")

    br = getattr(cell, "broker", None)
    if br is not None:
        try:
            if br.poll() is not None:
                return ("dead", "LLM-Broker beendet")
        except Exception:
            pass

    nb = getattr(cell, "net_broker", None)
    _net_dead = False
    if nb is not None:
        try:
            if nb.poll() is not None:
                _net_dead = True
        except Exception:
            pass
    else:
        nsp = getattr(cell, "net_sock", None)
        try:
            if nsp and os.path.exists(nsp) and not _listener_present(nsp):
                _net_dead = True
        except Exception:
            pass
    if _net_dead:
        return ("dead", "Netz-Broker beendet (kein Egress) - Zelle wird neu gestartet")

    seat = _bounded(cell.seat_echo, min(8.0, max(4.0, TICK_S / 3.0)), None)
    if seat is False:
        n = _SEAT_FAIL.get(key, 0) + 1
        _SEAT_FAIL[key] = n
        if n >= SEAT_FAIL_MAX:
            return ("dead", "Seat antwortet nicht (Gast eingefroren)")
    else:
        _SEAT_FAIL[key] = 0

    if getattr(cell, "term_on", False):
        alive = _bounded(cell.term_runner_alive, min(9.0, max(5.0, TICK_S / 2.0)), True)
        if alive is False:
            return ("term-dead", "In-Cell-Terminal beendet")
        with _LOCK:
            _TERM_FAILS[key] = 0
            _TERM_KICK_AT.pop(key, None)

    if STALL_ON:
        v = _probe_stall(cell, key, now)
        if v:
            return ("dead", v)
    return ("ok", "")

def _probe_stall(cell, key, now):
    try:
        path = _bounded(cell._incell_active_jsonl, 8.0, None)
        if not path:
            return None
        size = _bounded(lambda: cell._incell_jsonl_size(path), 8.0, None)
        if size is None:
            return None
        st = _JSONL.get(key)
        if not st or st.get("path") != path or size > st.get("size", -1):
            _JSONL[key] = {"path": path, "size": size, "grew_ts": now}
            return None
        if now - st.get("grew_ts", now) < TURN_STALL_S:
            return None
        busy = _bounded(lambda: cell._incell_turn_busy(path), 8.0, False)
        if not busy:
            return None
        cpu = _vmm_cpu_pct(cell.proc.pid, now) if getattr(cell, "proc", None) else None
        if cpu is not None and cpu >= STALL_CPU_PCT:
            return None
        return "Turn hängt (>%ds ohne Fortschritt, VMM idle)" % int(TURN_STALL_S)
    except Exception:
        return None

def _restart_core(ctx, cell, key, reason):

    with _LOCK:
        if key in _RESTARTING:
            return False
        _RESTARTING.add(key)
    principal, sid = key
    n = _budget_note(key, _now())
    _set_health(key, "restarting", reason, n)
    _bus(ctx, principal, sid, "died", "restarting", reason)
    _kick(ctx, key, 4002, ("[Session-VM: %s - starte neu ...]" % reason).encode("utf-8", "replace"))
    try:
        was_term = bool(getattr(cell, "term_on", False))

        if _bounded(cell.seat_echo, 4.0, False):
            _bounded(cell.sync, 6.0, False)
        try:
            cell._teardown(reboot=False)
        except Exception:
            pass
        ok = False
        try:
            ok = bool(cell.boot())
        except Exception:
            ok = False
        if ok and was_term:
            try:
                cell.start_terminal(system=getattr(cell, "_term_system", None))
            except Exception:
                pass
        if ok:
            _clear_health(key, clear_budget=False)
            _bus(ctx, principal, sid, "recovered", "warm", reason)
        else:
            _set_health(key, "failed", "Neustart fehlgeschlagen: " + reason, n)
            _bus(ctx, principal, sid, "restart-failed", "failed", reason)
        return ok
    finally:
        with _LOCK:
            _RESTARTING.discard(key)

def _mark_failed(ctx, cell, key, reason):
    principal, sid = key
    with _LOCK:
        n = len(_RESTARTS.get(key, []))
    _set_health(key, "failed", reason, n)
    _bus(ctx, principal, sid, "restart-failed", "failed", reason)
    _kick(ctx, key, 4003, ("[Session mehrfach gestorben - bitte manuell pruefen: %s]" % reason).encode("utf-8", "replace"))

def restart_now(uid, sid):

    ctx = _CTX
    if ctx is None:
        return False
    mgr = _mgr(ctx)
    cell = mgr.get(uid, sid) if mgr else None
    if cell is None or getattr(cell, "proc", None) is None:
        return False
    key = (uid, sid)
    if not _budget_ok(key, _now()):
        _mark_failed(ctx, cell, key, "Restart-Budget erschöpft")
        return False
    return _restart_core(ctx, cell, key, "manueller Neustart")

def _worth_keeping(cell, key):

    if _has_attach(key):
        return True
    last = float(getattr(cell, "last", 0) or 0)
    return bool(last) and (_now() - last) < KEEP_WARM_S

def _ensure_drain(cell, key):

    try:
        if not getattr(cell, "term_on", False) or getattr(cell, "term_conn", None) is None:
            return
        import portal_terminal as _pt
        with _pt._CELL_TERM_LK:
            if _pt._CELL_TERM_ATTACHED.get(key) is not None:
                return
        _pt._ensure_cell_holder(key[0], key[1])
    except Exception:
        pass

def tick(ctx, now=None):

    now = now or _now()
    mgr = _mgr(ctx)
    if mgr is None:
        return 0
    try:
        items = list(getattr(mgr, "_cells", {}).items())
    except Exception:
        items = []
    dead = 0

    refmap = _refusal_map(ctx) if REFUSAL_ON else {}
    for key, cell in items:
        try:
            principal, sid = key
            verdict, reason = _probe(ctx, cell, key, now)
            if verdict == "off":
                continue
            if verdict == "ok":
                _ensure_drain(cell, key)
                _clear_health(key)
                if STALL_ON:
                    _probe_stall(cell, key, now)
                _probe_progress(cell, key, now)
                if REFUSAL_ON:
                    _probe_refusal(ctx, cell, key, now, refmap)
                _observe(ctx, cell, key, now)
                continue
            if verdict == "term-dead":

                if (now - _TERM_KICK_AT.get(key, 0.0)) < TERM_GRACE_S:
                    continue
                with _LOCK:
                    n = _TERM_FAILS.get(key, 0)
                if n >= MAX_RESTARTS:
                    _mark_failed(ctx, cell, key, "In-Cell-Terminal stirbt wiederholt")
                    continue
                with _LOCK:
                    _TERM_FAILS[key] = n + 1
                if _has_attach(key):
                    _TERM_KICK_AT[key] = now
                    _kick(ctx, key, 4002, b"[In-Cell-Terminal neu - verbinde gleich neu ...]")
                try:
                    cell.term_on = False
                except Exception:
                    pass
                continue

            dead += 1
            if not _worth_keeping(cell, key):
                _bus(ctx, principal, sid, "died", "off", reason)
                _kick(ctx, key, 4002, ("[Session-VM: %s]" % reason).encode("utf-8", "replace"))
                try:
                    cell._teardown(reboot=False)
                except Exception:
                    pass
                continue
            if not _budget_ok(key, now):
                _mark_failed(ctx, cell, key, reason)
                continue
            with _LOCK:
                busy = key in _RESTARTING
            if busy:
                continue
            threading.Thread(target=_restart_core, args=(ctx, cell, key, reason),
                             daemon=True, name="wd-restart-%s" % sid).start()
        except Exception:
            pass

    if IDLE_SWEEP_ON:
        try:
            mgr.idle_sweep(now)
        except Exception:
            pass

        try:
            reg = ctx.get("sesscell_reg")
            reg = reg() if callable(reg) else reg
            if reg is not None:
                reg.apply_idle(now)
        except Exception:
            pass

    try:
        mgr.clock_sweep(now)
    except Exception:
        pass

    try:
        _waisen_ein, _waisen_arch = mgr.waisen_einsammeln()
        if _waisen_ein or _waisen_arch:
            print("[watchdog] waisen: %d Zelle(n) eingesammelt, %d Laufordner archiviert"
                  % (_waisen_ein, _waisen_arch))
    except Exception:
        pass
    try:
        _keepalive_tick(ctx, now)
    except Exception:
        pass
    try:
        import pn_watchdog_deadman as _dm
        _dm.beat(len(items), dead, "primary")
    except Exception:
        pass
    return dead

def _orphan_sweep(ctx):
    import signal as _sig
    try:
        import pn_cell_session as _cs
        voldir = os.path.realpath(_cs.VOL_DIR)
    except Exception:
        return
    mgr = _mgr(ctx)
    tracked = set()
    try:
        for c in list(getattr(mgr, "_cells", {}).values()):
            p = getattr(c, "proc", None)
            if p is not None:
                tracked.add(p.pid)
    except Exception:
        pass
    killed = 0
    me = os.getpid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        ip = int(pid)
        if ip == me or ip in tracked:
            continue
        try:
            env = open("/proc/%s/environ" % pid, "rb").read().decode("utf-8", "replace")
        except OSError:
            continue
        if "PN_VMM_BLK=" not in env or voldir not in env:
            continue
        try:
            os.kill(ip, _sig.SIGKILL)
            killed += 1
        except OSError:
            pass
    if killed:
        try:
            cc = _chanctx(ctx)
            if cc:
                import portal_channels as _pc
                _pc.bus_append(cc, "owner", "__system__", "lifecycle", event="orphans-reaped",
                               state="off", reason="%d verwaiste Session-VM(s) nach Portal-Neustart beendet" % killed)
        except Exception:
            pass
    return killed

def _proactive_reboot(ctx):

    reg = ctx.get("sesscell_reg")
    reg = reg() if callable(reg) else reg
    mgr = _mgr(ctx)
    if reg is None or mgr is None:
        return
    now = _now()
    try:
        live = reg.list_live()
    except Exception:
        return
    for r in live:
        try:
            if r.get("state") != "warm":
                continue
            if now - float(r.get("last_active", 0) or 0) > 15 * 60:
                continue
            p, s = r.get("principal"), r.get("session")
            if not p or not s:
                continue
            c = mgr.ensure(p, s)
            if c is not None and c.alive():
                try:
                    c.start_terminal(system=getattr(c, "_term_system", None))
                except Exception:
                    pass
        except Exception:
            pass

_RECONCILE_TS = 0.0

def _fern_uebersicht():

    try:
        import pn_cell_fern as _pcf
        return _pcf.fern_zellen_uebersicht()
    except Exception:
        return {}, False

def _lokal_verwurzelt(r):

    try:
        cn = r.get("cell")
        anker = r.get("keystore_vol") or r.get("work_vol")
        if not cn or not anker:
            return False
        return os.path.exists(os.path.join(os.path.dirname(anker), cn + "-delta.img"))
    except Exception:
        return False

def reconcile_registry(ctx, now=None, force=False):

    global _RECONCILE_TS
    if not RECONCILE_ON:
        return (0, 0)
    now = now or _now()
    if not force and (now - _RECONCILE_TS) < RECONCILE_EVERY_S:
        return (0, 0)
    _RECONCILE_TS = now
    reg = ctx.get("sesscell_reg")
    try:
        reg = reg() if callable(reg) else reg
    except Exception:
        reg = None
    if reg is None:
        return (0, 0)
    try:
        import pn_session_cells as _sc
        import portal_archive as _pa
        eintraege = reg.list_all()
        laufend = _pa.laufende_zellen()
        akte = {"%s/%s" % (r.get("principal"), r.get("session")): r for r in eintraege}
    except Exception:
        return (0, 0)
    susp = evct = 0
    fern_blick = [None]
    for r in eintraege:
        try:
            if r.get("state") != _sc.WARM:
                continue
            p, s = r.get("principal"), r.get("session")
            if not p or not s:
                continue
            frisch = max(float(r.get("created") or 0), float(r.get("last_active") or 0),
                         float(r.get("state_changed") or 0))
            if (now - frisch) < RECONCILE_GRACE_S:
                continue
            if _pa.lebt(p, s, akte=akte, laufend=laufend):
                continue
            hat_vol = any(r.get(vk) and os.path.exists(r.get(vk))
                          for vk in ("keystore_vol", "work_vol"))
            if hat_vol:
                reg._transition(p, s, _sc.SUSPENDED, reason="reboot-reconcile", now=now)
                susp += 1
            else:

                if fern_blick[0] is None:
                    fern_blick[0] = _fern_uebersicht()
                zellen, alle_frisch = fern_blick[0]
                f = zellen.get(str(r.get("cell") or ""))
                if f is not None and (f.get("eintrag") or {}).get("state") == "running":
                    reg._transition(p, s, _sc.SUSPENDED, reason="reboot-reconcile", now=now)
                    try:
                        if hasattr(reg, "set_node"):
                            reg.set_node(p, s, f.get("node"))
                    except Exception:
                        pass
                    susp += 1
                    continue
                if not alle_frisch and not _lokal_verwurzelt(r):
                    continue
                reg._transition(p, s, _sc.EVICTED, reason="reboot-reconcile", now=now)
                evct += 1
        except Exception:
            continue
    if susp or evct:
        try:
            print("[watchdog] akte-abgleich: %d warm -> suspended, %d warm -> evicted (reboot-reconcile)"
                  % (susp, evct), flush=True)
        except Exception:
            pass
    return (susp, evct)

KEEPALIVE_PATH = os.path.join(os.path.expanduser("~"), ".local", "share", "brainbox-portal",
                              "watchdog_keepalive.json")
KA_RETRY_S = _envf("PN_KEEPALIVE_RETRY_S", 120.0)
_KA_LOCK = threading.Lock()
_KA_TRY_TS = {}
_KA_INFLIGHT = set()

def _ka_load():

    import json as _json
    try:
        with open(KEEPALIVE_PATH, "r", encoding="utf-8") as f:
            data = _json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _ka_save(data):

    import json as _json
    try:
        os.makedirs(os.path.dirname(KEEPALIVE_PATH), exist_ok=True)
    except Exception:
        pass
    tmp = "%s.tmp.%d" % (KEEPALIVE_PATH, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, KEEPALIVE_PATH)

def keepalive_set(uid, sid, hours_or_none, note=""):

    uid = str(uid or "").strip()
    sid = str(sid or "").strip()
    if not uid or not sid or "/" in uid:
        raise ValueError("uid und sid noetig (uid ohne '/')")
    until = None
    if hours_or_none is not None:
        h = float(hours_or_none)
        if h <= 0:
            raise ValueError("hours > 0 noetig (Entfernen: keepalive_off)")
        until = _now() + h * 3600.0
    key = uid + "/" + sid
    with _KA_LOCK:
        data = _ka_load()
        old = data.get(key) if isinstance(data.get(key), dict) else {}
        ent = {"until": until,
               "note": (str(note or "").strip() or old.get("note", ""))[:300],
               "created": old.get("created") or _now()}
        data[key] = ent
        _ka_save(data)
        _KA_TRY_TS.pop(key, None)
    return dict(ent, key=key)

def keepalive_off(uid, sid):

    key = "%s/%s" % (str(uid or "").strip(), str(sid or "").strip())
    with _KA_LOCK:
        data = _ka_load()
        had = key in data
        if had:
            data.pop(key, None)
            _ka_save(data)
        _KA_TRY_TS.pop(key, None)
    return had

def keepalive_list():

    now = _now()
    with _KA_LOCK:
        data = _ka_load()
    out = []
    for key in sorted(data):
        ent = data.get(key) or {}
        uid, _, sid = key.partition("/")
        until = ent.get("until")
        try:
            until = None if until is None else float(until)
        except (TypeError, ValueError):
            until = 0.0
        out.append({"key": key, "uid": uid, "sid": sid, "until": until,
                    "forever": until is None,
                    "expired": (until is not None and now >= until),
                    "remaining_s": (None if until is None else max(0, int(until - now))),
                    "note": ent.get("note", ""), "created": ent.get("created")})
    return out

def _ka_active(now):

    with _KA_LOCK:
        data = _ka_load()
        active, dropped = {}, []
        for key, ent in data.items():
            until = (ent if isinstance(ent, dict) else {}).get("until")
            try:
                expired = until is not None and now >= float(until)
            except (TypeError, ValueError):
                expired = True
            if expired:
                dropped.append(key)
            else:
                active[key] = ent
        if dropped:
            _ka_save(active)
            for key in dropped:
                _KA_TRY_TS.pop(key, None)
    return active

def _ka_helpers(ctx):

    ctx = ctx or {}
    base = ctx.get("portal_base_url")
    tok = ctx.get("voice_agent_token")
    pol = ctx.get("cockpit_policy_enf")
    if not (callable(base) and callable(tok) and callable(pol)):
        import sys as _sys
        _pvc = _sys.modules.get("portal_voice_core")
        if _pvc is not None:
            base = base if callable(base) else getattr(_pvc, "_portal_base_url", None)
            tok = tok if callable(tok) else getattr(_pvc, "_voice_agent_token", None)
            pol = pol if callable(pol) else getattr(_pvc, "_cockpit_policy_enf", None)
    return base, tok, pol

def _ka_ensure(ctx, uid, sid, now, sync=False):

    mgr = _mgr(ctx)
    if mgr is None:
        return False
    key = uid + "/" + sid
    try:
        cell = mgr.get(uid, sid)
        if cell is not None and cell.alive():
            return True
    except Exception:
        pass
    with _KA_LOCK:
        if key in _KA_INFLIGHT or (now - _KA_TRY_TS.get(key, 0.0)) < KA_RETRY_S:
            return False
        _KA_TRY_TS[key] = now
        _KA_INFLIGHT.add(key)

    def _boot():
        try:
            base, tok, pol = _ka_helpers(ctx)
            cell = mgr.ensure(uid, sid,
                              portal_url=(base() if callable(base) else None),
                              portal_token=(tok(uid) if callable(tok) else None),
                              policy=(pol(uid, sid) if callable(pol) else None))
            if cell is not None and cell.alive():
                _bus(ctx, uid, sid, "keepalive-ensured", "on",
                     "Wachhalte-Register: Session-Zelle laeuft (wieder)")
        except Exception:
            pass
        finally:
            with _KA_LOCK:
                _KA_INFLIGHT.discard(key)

    if sync:
        _boot()
    else:
        threading.Thread(target=_boot, daemon=True, name="wd-keepalive-%s" % sid).start()
    return True

def _keepalive_boot(ctx):

    now = _now()
    active = _ka_active(now)
    for key in sorted(active):
        uid, _, sid = key.partition("/")
        if not uid or not sid:
            continue
        try:
            _ka_ensure(ctx, uid, sid, now, sync=True)
        except Exception:
            pass
    return len(active)

def _keepalive_tick(ctx, now):

    for key in _ka_active(now):
        uid, _, sid = key.partition("/")
        if not uid or not sid:
            continue
        try:
            _ka_ensure(ctx, uid, sid, now)
        except Exception:
            pass

_TOKEN_TICK = {"last": 0.0}
_TOKEN_EVERY_S = _envf("PN_TOKEN_REFRESH_EVERY_S", 600.0)
_TOKEN_MARGIN_S = _envf("PN_TOKEN_REFRESH_MARGIN_S", 1800.0)

def _token_freshness_tick(now=None):
    now = time.time() if now is None else now
    if now - _TOKEN_TICK["last"] < _TOKEN_EVERY_S:
        return None
    _TOKEN_TICK["last"] = now
    try:
        import llmpool
    except Exception:
        return None
    if not hasattr(llmpool, "refresh_enabled_accounts"):
        return None
    res = llmpool.refresh_enabled_accounts(margin_s=_TOKEN_MARGIN_S)
    for r in res:
        if r.get("status") in ("refreshed", "failed"):
            print("[watchdog] token-refresh %s: %s" % (r.get("id"), r.get("status")), flush=True)
    return res

_USAGE_TICK = {"last": 0.0}
_USAGE_EVERY_S = _envf("PN_USAGE_REFRESH_EVERY_S", 600.0)

def _usage_freshness_tick(now=None):
    now = time.time() if now is None else now
    if now - _USAGE_TICK["last"] < _USAGE_EVERY_S:
        return None
    _USAGE_TICK["last"] = now
    try:
        import llmpool
    except Exception:
        return None
    if not hasattr(llmpool, "refresh_usage_accounts"):
        return None
    try:
        res = llmpool.refresh_usage_accounts()
    except Exception as e:
        print("[watchdog] usage-refresh error: %s" % type(e).__name__, flush=True)
        return None
    if res.get("updated"):
        print("[watchdog] usage-refresh: %d Konten aktualisiert" % res["updated"], flush=True)
    return res

def _owner_email(ctx):

    try:
        import portal_users
        accts = portal_users.user_list() or []
    except Exception:
        return ""
    for want in ("owner", "admin"):
        for a in accts:
            if a.get("role") == want and (a.get("email") or "").strip():
                return a["email"].strip()
    return ""

def _dm_email_alert(ctx, cause, text, severity):

    try:
        import pn_watchdog_deadman as _dm
        ec = _dm.get_email_config()
    except Exception:
        return
    to = (ec.get("alert_email") or "").strip()
    on = ec.get("email_on")
    if not to:
        to = _owner_email(ctx)
    if on is None:
        on = bool(to)
    if not to or not on:
        return
    try:
        import portal_email_portioneer as _mail
        if not _mail.mailjet_configured():
            return
    except Exception:
        return
    label = {"critical": "KRITISCH", "info": "Info", "attention": "Achtung"}.get(severity, severity or "Alarm")
    subject = "[Brainbox Watchdog] %s: %s" % (label, cause)
    body = ("Der Session-Watchdog (Rotalarm) dieser Brainbox hat ausgeloest.\n\n"
            "Ursache:  %s\nSchwere:  %s\n\n%s\n\n"
            "-- automatische Meldung des Dead-Man-Watchdogs (Zusatzschiene E-Mail; der Bus-/Off-Box-"
            "Alarm bleibt die primaere Schiene)." % (cause, severity, text))
    try:
        ok, detail = _mail.mailjet_send(to, subject, body)
        print("[watchdog] rotalarm-email cause=%s to=%s ok=%s (%s)" % (cause, to, ok, detail), flush=True)
    except Exception as e:
        print("[watchdog] rotalarm-email FEHLER cause=%s: %s" % (cause, type(e).__name__), flush=True)

def start(ctx):

    global _STARTED, _CTX
    with _LOCK:
        if _STARTED or not ENABLED:
            return None
        _STARTED = True
        _CTX = ctx

    def _loop():
        time.sleep(8)
        _obs_load()
        try:
            _keepalive_boot(ctx)
        except Exception:
            pass
        try:
            reconcile_registry(ctx, force=True)
        except Exception:
            pass
        if ORPHAN_SWEEP_ON:
            try:
                _orphan_sweep(ctx)
            except Exception:
                pass
        if PROACTIVE_ON:
            try:
                _proactive_reboot(ctx)
            except Exception:
                pass
        while True:
            try:
                tick(ctx)
            except Exception:
                pass
            try:
                reconcile_registry(ctx)
            except Exception:
                pass
            try:
                _token_freshness_tick()
            except Exception:
                pass
            try:
                _usage_freshness_tick()
            except Exception:
                pass
            time.sleep(TICK_S)

    t = threading.Thread(target=_loop, name="pn-session-watchdog", daemon=True)
    t.start()
    try:
        import pn_watchdog_deadman as _dm
        def _dm_alert(cause, text, severity):

            try:
                _bus(ctx, "owner", "__system__", "watchdog_" + cause,
                     "critical" if severity == "critical" else "attention", text[:400])
            except Exception:
                pass
            if severity == "critical":

                try:
                    cc = _chanctx(ctx)
                    if cc is not None:
                        import portal_channels as _pc
                        _pc.bus_append(cc, "owner", "__system__", "message", role="system",
                                       text="\U0001F6A8 ROTALARM [%s]: %s" % (cause, text[:400]),
                                       notify="alert")
                except Exception:
                    pass

            try:
                _dm_email_alert(ctx, cause, text, severity)
            except Exception:
                pass
            if severity == "critical":
                try:
                    cc = _chanctx(ctx)
                    if cc is not None:
                        import portal_channels as _pc
                        if hasattr(_pc, "nabu_announce"):
                            _pc.nabu_announce(cc, "owner", text[:200])
                except Exception:
                    pass
        _dm.start(ctx, tick, TICK_S, alert_fn=_dm_alert)
    except Exception:
        pass
    return t

def _selftest():
    ok = True

    def ck(name, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print("  [%s] %s" % ("PASS" if cond else "FAIL", name))

    class _P:
        def __init__(self, dead=False):
            self._dead = dead
            self.pid = 4242
        def poll(self):
            return 1 if self._dead else None
        def wait(self, timeout=None):
            return 0
        def kill(self):
            pass

    class FakeCell:
        def __init__(self):
            self.principal = "owner"; self.session = "s1"; self.cell = "sc-x"
            self.proc = _P(); self.broker = _P(); self.net_broker = None; self.portal_broker = None
            self.conn = object(); self.term_on = True; self.last = time.time()
            self._term_system = None
            self._seat = True; self._runner = True
            self.booted_n = 0; self.synced = 0; self.torn = 0; self.started_sys = "UNSET"
        def alive(self):
            return self.proc is not None and self.proc.poll() is None and self.conn is not None
        def seat_echo(self):
            return self._seat
        def term_runner_alive(self):
            return self._runner
        def sync(self):
            self.synced += 1; return True
        def _teardown(self, reboot=True):
            self.torn += 1; self.proc = None; self.conn = None; self.term_on = False
        def boot(self):
            self.booted_n += 1
            self.proc = _P(); self.broker = _P(); self.conn = object(); return True
        def start_terminal(self, cmd=None, cols=120, rows=40, system=None):
            self.term_on = True; self.started_sys = system; return True

    class FakeMgr:
        def __init__(self, cells):
            self._cells = cells
        def get(self, p, s):
            return self._cells.get((p, s))
        def idle_sweep(self, now=None):
            pass

    global SEAT_FAIL_MAX, MAX_RESTARTS, IDLE_SWEEP_ON, _HEALTH, _RESTARTS, _SEAT_FAIL, _RESTARTING
    SEAT_FAIL_MAX = 2; MAX_RESTARTS = 3; IDLE_SWEEP_ON = False

    def _reset():
        _HEALTH.clear(); _RESTARTS.clear(); _SEAT_FAIL.clear(); _RESTARTING.clear(); _TERM_FAILS.clear()

    def sync_restart(ctx, cell, key, reason):
        return _restart_core(ctx, cell, key, reason)

    key = ("owner", "s1")

    _reset()
    cell = FakeCell()
    ctx = {"cell_manager": lambda: FakeMgr({key: cell})}
    v, r = _probe(ctx, cell, key, time.time())
    ck("healthy cell probes ok", v == "ok")
    ck("healthy cell has no health chip", health(*key) is None)

    _reset()
    cell = FakeCell(); cell.proc = _P(dead=True)
    ctx = {"cell_manager": lambda: FakeMgr({key: cell})}
    v, r = _probe(ctx, cell, key, time.time())
    ck("dead VM detected", v == "dead")
    ok2 = sync_restart(ctx, cell, key, r)
    ck("restart booted the cell", ok2 and cell.booted_n == 1 and cell.torn >= 1)
    ck("health cleared after recovery", health(*key) is None)

    _reset()
    cell = FakeCell(); cell.proc = _P(dead=True); cell._term_system = "PERSONA-X"
    ctx = {"cell_manager": lambda: FakeMgr({key: cell})}
    sync_restart(ctx, cell, key, "VM tot")
    ck("persona preserved on relaunch", cell.started_sys == "PERSONA-X")

    _reset()
    cell = FakeCell()
    ctx = {"cell_manager": lambda: FakeMgr({key: cell})}
    now = time.time()
    for _i in range(MAX_RESTARTS):
        ck("budget allows restart %d" % (_i + 1), _budget_ok(key, now))
        _budget_note(key, now)
    ck("budget exhausted after MAX", not _budget_ok(key, now))

    _reset()
    cell = FakeCell(); cell._seat = False
    ctx = {"cell_manager": lambda: FakeMgr({key: cell})}
    v1, _ = _probe(ctx, cell, key, time.time())
    ck("one seat fail is not fatal", v1 == "ok")
    v2, r2 = _probe(ctx, cell, key, time.time())
    ck("two seat fails -> dead", v2 == "dead")

    _reset()
    cell = FakeCell(); cell.broker = _P(dead=True)
    ctx = {"cell_manager": lambda: FakeMgr({key: cell})}
    v, r = _probe(ctx, cell, key, time.time())
    ck("dead LLM broker detected", v == "dead" and "Broker" in r)

    _reset()
    cell = FakeCell(); cell._runner = False
    ctx = {"cell_manager": lambda: FakeMgr({key: cell})}
    v, r = _probe(ctx, cell, key, time.time())
    ck("dead in-cell runner -> term-dead", v == "term-dead")

    _reset()
    cell = FakeCell(); cell.proc = None
    ctx = {"cell_manager": lambda: FakeMgr({key: cell})}
    v, r = _probe(ctx, cell, key, time.time())
    ck("stopped cell is 'off' (never restarted)", v == "off")

    _reset()
    cell = FakeCell()
    ctx = {"cell_manager": lambda: FakeMgr({key: cell})}
    escalated_at = None
    for i in range(1, 12):
        cell.term_on = True; cell._runner = False
        tick(ctx)
        if (_HEALTH.get(key) or {}).get("state") == "failed":
            escalated_at = i; break
        tick(ctx)
    ck("Mode1b: slow term crash-loop escalates to 4003 (failed)", escalated_at is not None)
    ck("Mode1b: escalates at MAX_RESTARTS+1 crashes (not never, not premature)", escalated_at == MAX_RESTARTS + 1)

    _reset()
    cell = FakeCell()
    ctx = {"cell_manager": lambda: FakeMgr({key: cell})}
    for _ in range(20):
        cell.term_on = True; cell._runner = False; tick(ctx)
        cell.term_on = True; cell._runner = True; tick(ctx)
    ck("Mode1b: recovering term never escalates (no false positive)", (_HEALTH.get(key) or {}).get("state") != "failed")

    print("\nSELFTEST:", "ALL GREEN" if ok else "FAILURES")
    return 0 if ok else 1

if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("pn_session_watchdog — import me; start(ctx) from the portal; --selftest to verify.")
