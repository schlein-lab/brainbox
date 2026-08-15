
from __future__ import annotations

import bisect
import collections
import contextlib
import fcntl
import json
import os
import threading
import time

BUS_NAME = "session-bus.jsonl"
CURSORS_NAME = "session-bus-cursors.json"
SEQ_NAME = "session-bus.seq"
LOCK_NAME = "session-bus.lock"
NOTIFY_NAME = "session-notify.jsonl"
GEN_NAME = "session-bus.gen"
ARCHIVE_DIR = "bus-archiv"

ROTATE_MB = float(os.environ.get("PN_BUS_ROTATE_MB", "32"))
KEEP_S = float(os.environ.get("PN_BUS_KEEP_S", "259200"))
REROTATE_GROW = 4 * 1024 * 1024

POLL_S = float(os.environ.get("PN_BUS_POLL_S", "3.0"))
ACTIVE_WINDOW_S = float(os.environ.get("PN_BUS_ACTIVE_S", "3600"))
MAX_TEXT = 16000

def _p(ctx, name):
    return os.path.join(ctx["data_dir"], name)

@contextlib.contextmanager
def _locked(ctx):
    os.makedirs(ctx["data_dir"], exist_ok=True)
    lf = open(_p(ctx, LOCK_NAME), "w")
    try:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        finally:
            lf.close()

def _next_seq(ctx):

    p = _p(ctx, SEQ_NAME)
    try:
        n = int(open(p).read().strip() or "0")
    except (OSError, ValueError):
        n = 0
    n += 1
    tmp = "%s.tmp.%d" % (p, os.getpid())
    with open(tmp, "w") as f:
        f.write(str(n))
    os.replace(tmp, p)
    return n

TOPIC_RESOLVER = None

def bus_append(ctx, principal, sid, kind, **fields):

    try:
        rec = {"seq": None, "ts": round(time.time(), 3), "principal": principal, "sid": sid, "kind": kind}
        for k, v in fields.items():
            if k == "text" and isinstance(v, str) and len(v) > MAX_TEXT:
                v = v[:MAX_TEXT] + " …(gekürzt)"
            rec[k] = v

        if "topic" not in rec:
            tf = (ctx.get("topic_for") if isinstance(ctx, dict) else None) or TOPIC_RESOLVER
            if callable(tf):
                try:
                    rec["topic"] = tf(principal, sid)
                except Exception:
                    rec["topic"] = None
        if kind == "message" and "notify" not in rec:
            if rec.get("role") in ("observer", "system"):
                rec["notify"] = "ambient"
            else:

                try:
                    import pn_sprachregelung as _spr
                    rec["notify"] = _spr.kanal_vorgabe()
                except Exception:
                    rec["notify"] = "normal"

        _drole, _dtext = rec.get("role"), rec.get("text")
        _guard = (kind == "message" and isinstance(_dtext, str)
                  and _drole in ("user", "assistant") and _dtext.strip())
        with _locked(ctx):
            if _guard and (_drole == "assistant" or rec.get("origin") == "cell"):
                try:
                    if _is_duplicate(_recent_bus_keys(ctx, principal, sid), _drole, _dtext):
                        return None
                except Exception:
                    pass
            rec["seq"] = _next_seq(ctx)
            with open(_p(ctx, BUS_NAME), "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

            if _guard:
                try:
                    _note_seen(ctx, principal, sid, _drole, _dtext)
                except Exception:
                    pass
        return rec["seq"]
    except Exception:
        return None

STATUS_NAME = "status-cards.json"

def bus_status(ctx, principal, sid, topic, text):

    try:
        key = "%s/%s" % (principal, topic)
        p = _p(ctx, STATUS_NAME)
        try:
            with open(p) as f:
                m = json.load(f)
            if not isinstance(m, dict):
                m = {}
        except (OSError, ValueError):
            m = {}
        ref = m.get(key)
        if ref:
            return bus_append(ctx, principal, sid, "edit", ref_seq=int(ref), text=text, topic=topic)
        seq = bus_append(ctx, principal, sid, "message", role="system", text=text,
                         topic=topic, sticky=True, notify="ambient")
        if seq:
            m[key] = int(seq)
            tmp = "%s.tmp.%d" % (p, os.getpid())
            with open(tmp, "w") as f:
                json.dump(m, f)
            os.replace(tmp, p)
        return seq
    except Exception:
        return None

def bus_read(ctx, since_byte=0, principal=None, limit=1000):

    path = _p(ctx, BUS_NAME)
    out = []
    try:
        size = os.path.getsize(path)
    except OSError:
        return out, 0
    if since_byte < 0:
        since_byte = 0
    if since_byte > size:

        return out, size
    pos = since_byte
    try:
        with open(path, "rb") as f:
            f.seek(since_byte)
            data = f.read()
    except OSError:
        return out, since_byte

    nl = data.rfind(b"\n")
    if nl == -1:
        return out, since_byte
    consumed = data[:nl + 1]
    pos = since_byte + len(consumed)
    for line in consumed.split(b"\n"):
        if not line.strip():
            continue
        try:
            ev = json.loads(line.decode("utf-8", "replace"))
        except Exception:
            continue
        if principal is not None and ev.get("principal") != principal:
            continue
        out.append(ev)
        if len(out) >= limit:

            pos = since_byte + consumed.find(line) + len(line) + 1
            break
    return out, pos

def _gen_meta(ctx):

    try:
        with open(_p(ctx, GEN_NAME)) as f:
            d = json.load(f)
        if isinstance(d, dict):
            return {"gen": int(d.get("gen") or 0), "ts": float(d.get("ts") or 0),
                    "size_after": int(d.get("size_after") or 0)}
        return {"gen": int(d), "ts": 0.0, "size_after": 0}
    except (OSError, ValueError, TypeError):
        return {"gen": 0, "ts": 0.0, "size_after": 0}

def bus_gen(ctx):

    return _gen_meta(ctx)["gen"]

TURNIDX_MAX = int(os.environ.get("PN_BUS_TURNIDX_SESSIONS", "64"))
_TURNIDX = collections.OrderedDict()
_TURNIDX_GUARD = threading.Lock()

import portal_zustand as _zst
_zst.register("portal_channels._TURNIDX", "cache", __name__, ref=_TURNIDX,
              beschreibung="Turn-Index des /api/transcript-Hot-Path: je (principal, sid) fertig gefaltete Turns + Byte-Offset; LRU ueber TURNIDX_MAX (Standard 64). Verlust kostet EINEN Vollaufbau je Session.",
              neustart="rekonstruiert", schreiber="Request-Threads via _turnidx_entry()")

def _turnidx_entry(principal, sid):
    key = (principal, sid)
    with _TURNIDX_GUARD:
        e = _TURNIDX.get(key)
        if e is None:
            e = _TURNIDX[key] = {"lock": threading.Lock(), "off": 0, "gen": 0,
                                 "turns": [], "by_seq": {}}
        _TURNIDX.move_to_end(key)
        while len(_TURNIDX) > max(1, TURNIDX_MAX):
            _TURNIDX.popitem(last=False)
        return e

def _turnidx_fold(e, sid, ev):

    if ev.get("sid") != sid:
        return
    if ev.get("kind") == "edit":
        try:
            t = e["by_seq"].get(int(ev.get("ref_seq") or 0))
        except Exception:
            return
        txt = ev.get("text") or ""
        if t is not None and txt:
            t["text"] = txt
            t["edited"] = True
            t["ts_edit"] = ev.get("ts")
        return
    if ev.get("kind") != "message":
        return
    role = ev.get("role")
    text = (ev.get("text") or "").strip()
    if (role not in ("user", "assistant", "observer") and not ev.get("sticky")) or not text:
        return
    t = {"i": len(e["turns"]), "role": role, "text": text, "ts": ev.get("ts"),
         "model": ev.get("model"), "seq": ev.get("seq")}
    if ev.get("sticky"):
        t["sticky"] = True
    if ev.get("topic") is not None:
        t["topic"] = ev.get("topic")
    e["turns"].append(t)
    try:
        e["by_seq"][int(ev.get("seq") or 0)] = t
    except Exception:
        pass

def bus_turns_indexed(ctx, principal, sid):

    e = _turnidx_entry(principal, sid)
    with e["lock"]:
        for _versuch in range(3):
            gen = bus_gen(ctx)
            try:
                size = os.path.getsize(_p(ctx, BUS_NAME))
            except OSError:
                size = 0
            if e["gen"] != gen or size < e["off"]:

                e["off"] = 0
                e["gen"] = gen
                e["turns"] = []
                e["by_seq"] = {}
            off = e["off"]
            while size > off:
                chunk, noff = bus_read(ctx, off, principal, limit=5000)
                for ev in chunk:
                    _turnidx_fold(e, sid, ev)
                if noff <= off:
                    break
                off = noff
            e["off"] = off
            if bus_gen(ctx) == gen:
                break

            e["gen"] = -1
        return [dict(t) for t in e["turns"]]

def _turnidx_apply_rotation(remap, new_gen):

    with _TURNIDX_GUARD:
        entries = list(_TURNIDX.values())
    for e in entries:
        with e["lock"]:
            if e["gen"] == new_gen - 1:
                e["off"] = remap(e["off"])
                e["gen"] = new_gen

def _rotate_keep_set(ctx, lines, now, keep_s):

    keep = set()
    try:
        reg = ctx.get("sesscell_reg") and ctx["sesscell_reg"]()
        for r in (reg.list_live() if reg else []):
            keep.add((r.get("principal"), r.get("session")))
    except Exception:
        pass
    cutoff = now - keep_s
    for _off, ev in lines:
        if ev is None:
            continue
        try:
            if float(ev.get("ts") or 0) >= cutoff:
                keep.add((ev.get("principal"), ev.get("sid")))
        except Exception:
            continue
    return keep

def _rotate_fix_bindings(ctx, remap):

    bpath = os.path.join(ctx["data_dir"], "channel-bindings.json")
    if not os.path.exists(bpath):
        return
    lf = open(os.path.join(ctx["data_dir"], "channel-bindings.lock"), "w")
    try:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            with open(bpath) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(d, dict):
            return
        changed = False
        for chans in d.values():
            if not isinstance(chans, dict):
                continue
            for b in chans.values():
                if isinstance(b, dict) and "bus_off" in b:
                    try:
                        b["bus_off"] = remap(int(b.get("bus_off") or 0))
                        changed = True
                    except Exception:
                        continue
        if changed:
            tmp = "%s.tmp.%d" % (bpath, os.getpid())
            with open(tmp, "w") as f:
                json.dump(d, f, indent=1)
            os.replace(tmp, bpath)
    finally:
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        finally:
            lf.close()

def bus_rotate(ctx, keep_s=None):

    keep_s = KEEP_S if keep_s is None else float(keep_s)
    try:
        with _locked(ctx):
            path = _p(ctx, BUS_NAME)
            try:
                with open(path, "rb") as f:
                    raw = f.read()
            except OSError:
                return None

            nl = raw.rfind(b"\n")
            if nl == -1:
                return None
            body, tail = raw[:nl + 1], raw[nl + 1:]
            now = time.time()
            lines = []
            off = 0
            for ln in body.split(b"\n")[:-1]:
                rec = None
                if ln.strip():
                    try:
                        rec = json.loads(ln.decode("utf-8", "replace"))
                    except Exception:
                        rec = None
                lines.append((off, rec, ln))
                off += len(ln) + 1
            keep = _rotate_keep_set(ctx, [(o, r) for o, r, _l in lines], now, keep_s)
            kept_old, new_chunks, new_off = [], [], 0
            for old_off, rec, ln in lines:
                if rec is None and not ln.strip():
                    continue
                if rec is None or (rec.get("principal"), rec.get("sid")) in keep:
                    kept_old.append((old_off, new_off))
                    new_chunks.append(ln + b"\n")
                    new_off += len(ln) + 1
            new_body = b"".join(new_chunks) + tail
            if len(new_body) >= len(raw):

                meta = _gen_meta(ctx)
                tmpg = "%s.tmp.%d" % (_p(ctx, GEN_NAME), os.getpid())
                with open(tmpg, "w") as f:
                    json.dump({"gen": meta["gen"], "ts": now, "size_after": len(raw)}, f)
                os.replace(tmpg, _p(ctx, GEN_NAME))
                return None

            adir = os.path.join(ctx["data_dir"], ARCHIVE_DIR)
            os.makedirs(adir, exist_ok=True)
            aname = "session-bus-%s.jsonl" % time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
            apath = os.path.join(adir, aname)
            i = 1
            while os.path.exists(apath):
                apath = os.path.join(adir, aname[:-6] + ".%d.jsonl" % i)
                i += 1
            with open(apath, "wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())

            tmp = "%s.tmp.%d" % (path, os.getpid())
            with open(tmp, "wb") as f:
                f.write(new_body)
                f.flush()
                os.fsync(f.fileno())

            old_offs = [o for o, _n in kept_old]

            def _remap(v, _offs=old_offs, _pairs=kept_old, _old_end=nl + 1, _new_end=new_off):
                try:
                    v = int(v)
                except Exception:
                    return 0
                if v <= 0:
                    return 0
                if v >= _old_end:
                    return _new_end
                j = bisect.bisect_left(_offs, v)
                return _pairs[j][1] if j < len(_pairs) else _new_end

            _rotate_fix_bindings(ctx, _remap)
            os.replace(tmp, path)
            meta = _gen_meta(ctx)
            new_gen = meta["gen"] + 1
            tmpg = "%s.tmp.%d" % (_p(ctx, GEN_NAME), os.getpid())
            with open(tmpg, "w") as f:
                json.dump({"gen": new_gen, "ts": now, "size_after": len(new_body)}, f)
            os.replace(tmpg, _p(ctx, GEN_NAME))
            _turnidx_apply_rotation(_remap, new_gen)
            return {"rotated": True, "gen": new_gen, "archive": apath,
                    "old_bytes": len(raw), "new_bytes": len(new_body),
                    "kept_sessions": len(keep)}
    except Exception:
        return None

def maybe_rotate(ctx):

    try:
        size = os.path.getsize(_p(ctx, BUS_NAME))
    except OSError:
        return None
    if size < ROTATE_MB * 1024 * 1024:
        return None
    meta = _gen_meta(ctx)
    if meta["size_after"] and size < meta["size_after"] + REROTATE_GROW:
        return None
    return bus_rotate(ctx)

def _load_cursors(ctx):
    try:
        with open(_p(ctx, CURSORS_NAME)) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}

def _save_cursors(ctx, cur):
    tmp = "%s.tmp.%d" % (_p(ctx, CURSORS_NAME), os.getpid())
    with open(tmp, "w") as f:
        json.dump(cur, f)
    os.replace(tmp, _p(ctx, CURSORS_NAME))

def session_say(ctx, principal, body):

    try:
        req = json.loads(body.decode("utf-8", "replace") or "{}") if isinstance(body, (bytes, bytearray)) else (body or {})
    except Exception:
        return {"ok": False, "error": "bad json"}, 400
    sid = str(req.get("sid") or "").strip()
    text = req.get("text")
    if not sid or not isinstance(text, str) or not text.strip():
        return {"ok": False, "error": "sid and non-empty text required"}, 400
    kind = str(req.get("kind") or "cockpit")
    origin = str(req.get("origin") or "portal")[:24]
    try:
        store = ctx["session_store"](principal, kind)
        rec = store.get(sid)
    except Exception as e:
        return {"ok": False, "error": "store: %s" % e}, 500
    if not rec:

        return {"ok": False, "error": "unknown session for this principal"}, 404
    tmux = rec.get("tmux")
    if not tmux:
        return {"ok": False, "error": "session has no tmux target"}, 409

    try:
        reg = ctx.get("sesscell_reg") and ctx["sesscell_reg"]()
        cell_rec = reg.get(principal, sid) if reg else None
        if cell_rec and cell_rec.get("state") == "evicted":
            return {"ok": False, "error": "session evicted; re-provision first"}, 409
    except Exception:
        pass

    live = ctx.get("session_live")
    if live is not None:
        try:
            if not live(principal, sid, tmux):
                return {"ok": False, "closed": True,
                        "error": "Session ist geschlossen — bitte im Portal öffnen, dann kommt deine Nachricht an."}, 409
        except Exception:
            pass
    else:
        alive = ctx.get("session_alive")
        if alive is not None:
            try:
                if not alive(tmux):
                    return {"ok": False, "closed": True,
                            "error": "Session ist geschlossen — bitte im Portal öffnen, dann kommt deine Nachricht an."}, 409
            except Exception:
                pass
    deliver = ctx.get("deliver")
    if deliver is not None:
        try:
            ok, derr = deliver(principal, sid, tmux, text)
        except Exception as e:
            ok, derr = False, "deliver: %s" % e
        if not ok:
            return {"ok": False, "error": derr or "delivery failed"}, 502
    else:
        try:
            ctx["inject"](tmux, text)
        except Exception as e:
            return {"ok": False, "error": "inject: %s" % e}, 500
    with contextlib.suppress(Exception):
        store.touch(sid)
    with contextlib.suppress(Exception):
        if ctx.get("prov_log"):
            ctx["prov_log"]("session.say", principal, text[:200], {"sid": sid, "via": kind})

    bus_append(ctx, principal, sid, "message", role="user", text=text, origin=origin)

    with contextlib.suppress(Exception):
        _note_seen(ctx, principal, sid, "user", text)
    return {"ok": True, "sid": sid}, 200

def _session_title(ctx, principal, sid):

    for kind in ("cockpit", "voice"):
        with contextlib.suppress(Exception):
            rec = ctx["session_store"](principal, kind).get(sid)
            if rec and rec.get("title"):
                return rec["title"]
    if str(sid).startswith("voice-"):

        with contextlib.suppress(Exception):
            import portal_voice_core as _vc
            if _vc.VOICE_PERSIST and str(sid) == _vc._voice_sess_name():
                return "🎙 Sprach-Session (dauerhaft)"
        return "🎙 Sprach-Session %s" % str(sid)[len("voice-"):]
    return sid

def _mirror_lifecycle(ctx, state):

    path = _p(ctx, NOTIFY_NAME)
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    off = state.get("notify_off", 0)
    if off > size:
        off = 0
    if off == size:
        return
    try:
        with open(path, "rb") as f:
            f.seek(off)
            data = f.read()
    except OSError:
        return
    nl = data.rfind(b"\n")
    if nl == -1:
        return
    for line in data[:nl + 1].split(b"\n"):
        if not line.strip():
            continue
        try:
            ev = json.loads(line.decode("utf-8", "replace"))
        except Exception:
            continue
        principal = ev.get("principal")
        sid = ev.get("session")
        if not principal or not sid:
            continue
        title = _session_title(ctx, principal, sid)
        bus_append(ctx, principal, sid, "lifecycle",
                   event=ev.get("event"), state=ev.get("state"), title=title)
    state["notify_off"] = off + nl + 1

def _dedup_key(role, text):

    return "%s\x00%s" % (role, " ".join((text or "").split())[:600])

def _recent_bus_keys(ctx, principal, sid, limit=60):

    keys = _SEEN.get("%s/%s" % (principal, sid))
    if keys is not None:
        return keys
    keys = collections.deque(maxlen=200)
    path = _p(ctx, BUS_NAME)
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - 900000))
            f.readline()
            rows = [l for l in f.read().decode("utf-8", "replace").splitlines() if l.strip()]
    except OSError:
        rows = []
    for ln in rows[-4000:]:
        try:
            ev = json.loads(ln)
        except Exception:
            continue
        if ev.get("sid") != sid or ev.get("principal") != principal:
            continue
        if ev.get("kind") == "message" and ev.get("role") in ("user", "assistant"):
            keys.append(_dedup_key(ev.get("role"), ev.get("text")))
    _SEEN["%s/%s" % (principal, sid)] = keys
    return keys

def _note_seen(ctx, principal, sid, role, text):
    _recent_bus_keys(ctx, principal, sid).append(_dedup_key(role, text))

def _is_duplicate(seen, role, text):

    key = _dedup_key(role, text)
    if key in seen:
        return True
    norm = " ".join((text or "").split())
    if len(norm) < 40:
        return False
    prefix = "%s\x00" % role
    for k in seen:
        if not k.startswith(prefix):
            continue
        other = k[len(prefix):]
        if len(other) < 40:
            continue
        if norm.startswith(other) or other.startswith(norm) or other in norm:
            return True
    return False

def _emit_new_turns(ctx, principal, sid, cursors):

    key = "%s/%s" % (principal, sid)
    try:
        mgr = ctx["cell_manager"]()
        cell = mgr.get(principal, sid)
        if not cell or not cell.alive():
            return 0
        path = cell._incell_active_jsonl()
        if not path:
            return 0
        size = cell._incell_jsonl_size(path)
    except Exception:
        return 0
    prev = cursors.get(key)
    if prev is None or prev.get("path") != path:
        cursors[key] = {"path": path, "off": size}
        return 0
    off = int(prev.get("off", 0))
    if size <= off:
        return 0
    try:
        res = cell.bus_tail(off)
    except Exception:
        return 0
    _prov = ctx.get("prov_log")
    _emitted = 0
    turns = res.get("turns")
    if turns is None:
        turns = [{"role": "assistant", "text": t,
                  "model": (res.get("models") or [None] * 99)[i] if res.get("models") else None}
                 for i, t in enumerate(res.get("texts") or [])]
    seen = _recent_bus_keys(ctx, principal, sid)
    for turn in turns:
        t = (turn.get("text") or "").strip()
        role = turn.get("role")
        if not t or role not in ("user", "assistant"):
            continue
        if _is_duplicate(seen, role, t):
            continue
        k = _dedup_key(role, t)
        bus_append(ctx, principal, sid, "message", role=role, text=t,
                   model=turn.get("model"), origin=("cell" if role == "user" else None))
        seen.append(k)
        _emitted += 1
        if role == "assistant" and _prov:
            try:
                _prov("llm.turn", principal, "", {"sid": sid, "reply_chars": len(t)})
            except Exception:
                pass
    cursors[key] = {"path": path, "off": int(res.get("off", size))}
    return _emitted

_emit_new_assistant = _emit_new_turns

def catch_up(principal, sid, max_age_s=4.0):

    ctx = _PRODUCER.get("ctx")
    cursors = _PRODUCER.get("cursors")
    if ctx is None or cursors is None:
        return 0
    key = "%s/%s" % (principal, sid)
    now = time.time()
    if now - _CATCHUP.get(key, 0.0) < max_age_s:
        return 0
    _CATCHUP[key] = now
    try:
        n = _emit_new_turns(ctx, principal, sid, cursors)
    except Exception:
        return 0
    if n:
        with contextlib.suppress(Exception):
            _save_cursors(ctx, cursors)
    return n

def _cell_alive(ctx, r):

    try:
        mgr = ctx["cell_manager"]()
        c = mgr.get(r.get("principal"), r.get("session")) if mgr else None
        return bool(c and c.alive())
    except Exception:
        return False

def producer_tick(ctx, state, cursors):

    _mirror_lifecycle(ctx, state)
    emitted = 0
    reg = ctx.get("sesscell_reg") and ctx["sesscell_reg"]()
    if not reg:
        return emitted
    now = time.time()
    try:
        live = reg.list_live()
    except Exception:
        live = []
    for r in live:
        if r.get("state") != "warm":
            continue

        if (now - float(r.get("last_active", 0)) > ACTIVE_WINDOW_S) and not _cell_alive(ctx, r):
            continue
        emitted += _emit_new_turns(ctx, r.get("principal"), r.get("session"), cursors) or 0
    return emitted

_PRODUCER = {"thread": None, "cursors": None, "ctx": None}

_SEEN = {}

_CATCHUP = {}
_zst.register("portal_channels._PRODUCER", "singleton", __name__, ref=_PRODUCER,
              beschreibung="Producer-Handle {thread, cursors, ctx}; die cursors (Bus-Zustell-Fortschritt) liegen persistent in DATA_DIR/session-bus-cursors.json",
              neustart="persistiert", schreiber="Producer-Thread; Cursor-Datei atomar")
_zst.register("portal_channels._SEEN", "cursor", __name__, ref=_SEEN,
              beschreibung="Dubletten-Schutz beim Durchschleifen des Zell-JSONL: je Session ein Ring zuletzt gebuster (Rolle, Text)-Schluessel. Verlust => DOPPELZUSTELLUNG auf den Bus, bis der Ring aus dem Bus-Tail neu gefuellt ist.",
              neustart="rekonstruiert", schreiber="Producer-Thread + note_reply_delivered() (Zustell-Pfad)")
_zst.register("portal_channels._CATCHUP", "cache", __name__, ref=_CATCHUP,
              beschreibung="Drossel fuer catch_up() im Lese-Pfad (Client-Polls duerfen die Seat-Lane nicht fluten)",
              neustart="verfaellt", schreiber="catch_up()")

def note_reply_delivered(principal, sid, path, off, text=None):

    ctx = _PRODUCER.get("ctx")
    if ctx is not None and text:
        with contextlib.suppress(Exception):
            _note_seen(ctx, principal, sid, "assistant", text)

def start_producer(ctx):

    if _PRODUCER["thread"] is not None and _PRODUCER["thread"].is_alive():
        return _PRODUCER["thread"]
    state = {}
    cursors = _load_cursors(ctx)
    _PRODUCER["cursors"] = cursors
    _PRODUCER["ctx"] = ctx

    with contextlib.suppress(Exception):
        state["notify_off"] = os.path.getsize(_p(ctx, NOTIFY_NAME))

    with contextlib.suppress(Exception):
        maybe_rotate(ctx)

    def _loop():
        _save_i = 0
        while True:
            try:
                _n = producer_tick(ctx, state, cursors)
                _save_i += 1
                if _n or _save_i % 5 == 0:
                    _save_cursors(ctx, cursors)
                maybe_rotate(ctx)
            except Exception:
                pass
            time.sleep(POLL_S)

    t = threading.Thread(target=_loop, name="pn-bus-producer", daemon=True)
    t.start()
    _PRODUCER["thread"] = t
    return t

def _selftest():
    import tempfile
    ok = True

    def ck(n, c):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", n))

    d = tempfile.mkdtemp()

    class _Store:
        def __init__(self, principal): self.principal = principal; self._s = {}
        def get(self, sid): return self._s.get(sid)
        def touch(self, sid): self._s.get(sid, {})["last_active"] = 1
        def add(self, sid, tmux): self._s[sid] = {"id": sid, "tmux": tmux, "title": "T-" + sid}

    stores = {"alice": _Store("alice"), "bob": _Store("bob")}
    stores["alice"].add("s1", "alice_x-cockpit-s1")
    injected = []
    ctx = {
        "data_dir": d,
        "inject": lambda tmux, text: injected.append((tmux, text)),
        "session_store": lambda principal, kind="cockpit": stores.get(principal, _Store(principal)),
        "sesscell_reg": lambda: None,
        "cell_manager": lambda: None,
        "prov_log": lambda *a, **k: None,
    }

    pay, code = session_say(ctx, "bob", json.dumps({"sid": "s1", "text": "hi"}).encode())
    ck("cross-principal say REFUSED (404)", code == 404 and not injected)

    pay, code = session_say(ctx, "alice", json.dumps({"sid": "s1", "text": "hallo"}).encode())
    ck("owner say delivered", code == 200 and injected and injected[0][0] == "alice_x-cockpit-s1")

    _, c2 = session_say(ctx, "alice", json.dumps({"sid": "s1"}).encode())
    ck("empty text rejected", c2 == 400)

    calls = {"live": [], "deliver": []}
    def _mk_ctx(live_ret, deliver_ret):
        c = dict(ctx)
        c["inject"] = lambda tmux, text: injected.append(("SHOULD-NOT-RUN", text))
        c["session_live"] = lambda p, s, t: (calls["live"].append((p, s, t)) or live_ret)
        c["deliver"] = lambda p, s, t, txt: (calls["deliver"].append((p, s, t, txt)) or deliver_ret)
        return c

    calls["deliver"].clear()
    pay, code = session_say(_mk_ctx(False, (True, None)), "alice", json.dumps({"sid": "s1", "text": "x"}).encode())
    ck("cell live=False -> 409 closed", code == 409 and pay.get("closed") and not calls["deliver"])

    n0 = len(injected)
    pay, code = session_say(_mk_ctx(True, (True, None)), "alice", json.dumps({"sid": "s1", "text": "wake"}).encode())
    ck("cell deliver ok -> 200 via deliver", code == 200 and calls["deliver"] and calls["deliver"][-1][3] == "wake"
       and len(injected) == n0)

    pay, code = session_say(_mk_ctx(True, (False, "microVM konnte nicht starten")), "alice",
                            json.dumps({"sid": "s1", "text": "y"}).encode())
    ck("cell deliver fail -> 502", code == 502 and not pay.get("ok"))

    bus_append(ctx, "alice", "s1", "message", role="assistant", text="A1")
    bus_append(ctx, "bob", "s9", "message", role="assistant", text="B1")
    bus_append(ctx, "alice", "s1", "message", role="assistant", text="A2")
    evs, nxt = bus_read(ctx, 0, principal="alice")
    texts = [e.get("text") for e in evs if e.get("kind") == "message" and e.get("role") == "assistant"]
    ck("alice sees only alice assistant msgs", texts == ["A1", "A2"])
    ck("bob's msg not leaked to alice", all(e.get("principal") == "alice" for e in evs))
    ck("byte cursor advances", nxt > 0)
    evs2, _ = bus_read(ctx, nxt, principal="alice")
    ck("no re-read past cursor", evs2 == [])
    seqs = [e["seq"] for e in bus_read(ctx, 0)[0]]
    ck("seq monotonic", seqs == sorted(seqs) and len(set(seqs)) == len(seqs))

    def _n(sid, role="assistant", text=None):
        return len([e for e in bus_read(ctx, 0)[0]
                    if e.get("sid") == sid and e.get("role") == role
                    and (text is None or e.get("text") == text)])

    LONG = ("```\n12\n7zip bat delta duckdb fd fzf hyperfine jq pandoc rclone ripgrep yq\n```"
            " — lang genug fuer die Praefix-Regel.")
    bus_append(ctx, "alice", "d1", "message", role="assistant", text=LONG, model="claude-opus-4-8")
    bus_append(ctx, "alice", "d1", "message", role="assistant", text=LONG, model="Claude")
    ck("identische Antwort nur einmal auf dem Bus", _n("d1") == 1)
    bus_append(ctx, "alice", "d1", "message", role="assistant", text=LONG + "\n\nNachsatz.")
    ck("nachgereichte Gesamtfassung schluckt der Bus", _n("d1") == 1)
    bus_append(ctx, "alice", "d1", "message", role="assistant",
               text="Etwas voellig anderes, ausreichend lang fuer die Praefix-Regel.")
    ck("echte neue Antwort kommt durch", _n("d1") == 2)
    bus_append(ctx, "alice", "d1", "message", role="user", text="weiter")
    bus_append(ctx, "alice", "d1", "message", role="user", text="weiter")
    ck("Nutzer darf wortgleich wiederholen", _n("d1", "user") == 2)
    bus_append(ctx, "alice", "d2", "message", role="assistant", text=LONG)
    ck("andere Session bleibt unberuehrt", _n("d2") == 1)
    import threading as _th
    RACE = "Rennen-Text, lang genug fuer die Praefix-Regel des Dubletten-Schutzes."
    _ts = [_th.Thread(target=bus_append, args=(ctx, "alice", "d3", "message"),
                      kwargs={"role": "assistant", "text": RACE}) for _ in range(20)]
    [t.start() for t in _ts]; [t.join() for t in _ts]
    ck("20 gleichzeitige Schreiber -> eine Nachricht", _n("d3", "assistant", RACE) == 1)

    print("\nSELFTEST:", "ALL GREEN" if ok else "FAILURES")
    return 0 if ok else 1

if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("portal_channels — import me; --selftest to verify.")
