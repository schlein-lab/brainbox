
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import threading
import time

HOSTSHELL_GONE_REASON = ("Host-Shell entfernt — Arbeit läuft in Session-Zellen, "
                         "Box-Verwaltung per SSH.")

_META_TYPES = {"queue-operation", "last-prompt", "attachment", "summary", "system"}

_SYS_TAGS = ("system-reminder", "ide_opened_file", "ide_selection", "command-name",
             "command-message", "command-args", "local-command-stdout", "local-command-stderr",
             "user-prompt-submit-hook", "user-memory-input", "local-command-caveat")
_SYS_RE = re.compile(r"(?is)<(%s)\b.*?</\1>" % "|".join(_SYS_TAGS))
_SYS_OPEN_RE = re.compile(r"(?is)<(?:%s)\b[^>]*>" % "|".join(_SYS_TAGS))

def _clean_user_prose(text: str) -> str:

    if not text:
        return ""
    t = _SYS_RE.sub("", text)
    t = _SYS_OPEN_RE.sub("", t)
    return t.strip()

def project_dir(home: str, cwd: str) -> str:

    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd or "")
    return os.path.join(home, ".claude", "projects", slug)

def newest_jsonl(pdir: str):

    try:
        files = [os.path.join(pdir, f) for f in os.listdir(pdir) if f.endswith(".jsonl")]
    except OSError:
        return None
    return max(files, key=os.path.getmtime) if files else None

def cockpit_cwd(tmux_name: str, fallback: str) -> str:

    try:
        r = subprocess.run(["tmux", "display-message", "-p", "-t", tmux_name,
                            "-F", "#{pane_current_path}"], capture_output=True, text=True, timeout=5)
        cwd = (r.stdout or "").strip()
        if cwd:
            return cwd
    except Exception:
        pass
    return fallback

def pane_claude(tmux_name: str):

    try:
        r = subprocess.run(["tmux", "display-message", "-p", "-t", tmux_name,
                            "-F", "#{pane_pid}"], capture_output=True, text=True, timeout=5)
        root = int((r.stdout or "").strip())
    except Exception:
        return None, None, None
    try:
        btime = 0
        with open("/proc/stat") as f:
            for ln in f:
                if ln.startswith("btime "):
                    btime = int(ln.split()[1])
                    break
        hz = os.sysconf("SC_CLK_TCK")
        queue, seen = [root], set()
        while queue:
            pid = queue.pop(0)
            if pid in seen:
                continue
            seen.add(pid)
            try:
                with open("/proc/%d/comm" % pid) as f:
                    comm = f.read().strip()
            except OSError:
                continue
            if comm == "claude":
                with open("/proc/%d/stat" % pid) as f:
                    after = f.read().rsplit(") ", 1)[1].split()
                start = btime + int(after[19]) / hz
                cwd = os.readlink("/proc/%d/cwd" % pid)
                return pid, start, cwd
            try:
                ch = subprocess.run(["pgrep", "-P", str(pid)],
                                    capture_output=True, text=True, timeout=3)
                queue += [int(x) for x in (ch.stdout or "").split()]
            except Exception:
                pass
    except Exception:
        pass
    return None, None, None

def _pane_match_jsonl(tmux_name: str, cands: list):

    try:
        r = subprocess.run(["tmux", "capture-pane", "-p", "-t", tmux_name, "-S", "-400"],
                           capture_output=True, text=True, timeout=5)
        lines = (r.stdout or "").splitlines()
    except Exception:
        return None
    probes = []
    for ln in reversed(lines):
        t = re.sub(r"\s+", " ", ln.strip().strip("│╭╰╮╯─┃┆>· ")).strip()
        if len(t) >= 24 and re.search(r"[A-Za-z]{4}", t):
            probes.append(t)
        if len(probes) >= 6:
            break
    if not probes:
        return None
    best = None
    for born, mt, p in sorted(cands, key=lambda c: -c[1])[:12]:
        try:
            with open(p, "rb") as f:
                f.seek(max(0, os.path.getsize(p) - 786432))
                data = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        score = 0
        for t in probes:
            if (t in data or json.dumps(t)[1:-1] in data
                    or json.dumps(t, ensure_ascii=False)[1:-1] in data):
                score += 1
        if score and (best is None or score > best[0]):
            best = (score, p)
    return best[1] if best else None

def _sessionmap_jsonl(claude_pid: int):

    path = os.path.expanduser("~/.claude/session-map.jsonl")
    best = None
    try:
        with open(path) as f:
            for ln in f:
                try:
                    rec = json.loads(ln)
                except ValueError:
                    continue
                if rec.get("claude_pid") == claude_pid and rec.get("transcript_path"):
                    if best is None or rec.get("ts", 0) >= best.get("ts", 0):
                        best = rec
    except OSError:
        return None
    if best and os.path.exists(best["transcript_path"]):
        return best["transcript_path"]
    return None

def _sessionmap_by_tmux(tmux_name: str):

    if not tmux_name:
        return None
    path = os.path.expanduser("~/.claude/session-map.jsonl")
    best = None
    try:
        with open(path) as f:
            for ln in f:
                try:
                    rec = json.loads(ln)
                except ValueError:
                    continue
                if rec.get("tmux") == tmux_name and rec.get("transcript_path"):
                    if best is None or rec.get("ts", 0) >= best.get("ts", 0):
                        best = rec
    except OSError:
        return None
    if best and os.path.exists(best["transcript_path"]):
        return best["transcript_path"]
    return None

def session_jsonl(pdir: str, proc_start: float, tmux_name: str = None, claude_pid: int = None):

    if claude_pid:
        hit = _sessionmap_jsonl(claude_pid)
        if hit:
            return hit
    try:
        names = [os.path.join(pdir, f) for f in os.listdir(pdir) if f.endswith(".jsonl")]
    except OSError:
        return None
    if not names:
        return None
    cands = []
    try:
        r = subprocess.run(["stat", "-c", "%W\t%Y\t%n"] + names,
                           capture_output=True, text=True, timeout=5)
        for ln in (r.stdout or "").splitlines():
            try:
                born, mt, p = ln.split("\t", 2)
                cands.append((float(born), float(mt), p))
            except ValueError:
                continue
    except Exception:
        cands = [(0.0, os.path.getmtime(p), p) for p in names]
    exact = [c for c in cands if c[0] and (proc_start - 5) <= c[0] <= (proc_start + 120)]
    if exact:
        return max(exact, key=lambda c: c[1])[2]
    if tmux_name:
        hit = _pane_match_jsonl(tmux_name, cands)
        if hit:
            return hit
    later = [c for c in cands if c[0] and c[0] >= (proc_start - 5)]
    if later:
        return max(later, key=lambda c: c[1])[2]
    return max(cands, key=lambda c: c[1])[2] if cands else None

def _iso_to_unix(ts: str):

    if not ts:
        return None
    try:
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        import datetime
        return datetime.datetime.fromisoformat(s).timestamp()
    except Exception:
        return None

def _blocks_text(content, compact_tools: bool = True):

    if isinstance(content, str):
        return content.strip(), False, []
    prose = []
    had_tool = False
    names = []
    if isinstance(content, list):
        for blk in content:
            if not isinstance(blk, dict):
                if isinstance(blk, str) and blk.strip():
                    prose.append(blk.strip())
                continue
            bt = blk.get("type")
            if bt == "text":
                t = (blk.get("text") or "").strip()
                if t:
                    prose.append(t)
            elif bt == "tool_use":
                had_tool = True
                names.append(blk.get("name") or "tool")
            elif bt == "tool_result":
                had_tool = True

    return "\n\n".join(prose).strip(), had_tool, names

def parse_turns(path: str, since: int = 0):

    turns = []
    try:
        f = open(path, "rb")
    except OSError:
        return {"turns": [], "next": since}
    with f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            typ = ev.get("type")
            if typ in _META_TYPES or typ not in ("user", "assistant"):
                continue
            if ev.get("isSidechain"):
                continue
            msg = ev.get("message") or {}
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            prose, had_tool, names = _blocks_text(msg.get("content"))
            if role == "user":
                prose = _clean_user_prose(prose)
            ts = _iso_to_unix(ev.get("timestamp"))

            if prose:
                turns.append({"role": role, "text": prose, "ts": ts})

    out = []
    for i, t in enumerate(turns):
        t2 = {"i": i, "role": t["role"], "text": t["text"], "ts": t["ts"]}
        if i >= since:
            out.append(t2)
    return {"turns": out, "next": len(turns)}

def transcript(ctx: dict, principal: str, target: str = "cockpit", since: int = 0, session: str = None):

    home = ctx["home"]

    if target != "cockpit":
        return {"ok": False, "error": HOSTSHELL_GONE_REASON, "target": target,
                "next": since, "turns": []}, 400
    kind = "cockpit"

    tmux_name = None
    explicit = bool(session)
    if explicit and ctx.get("session_tmux_by_id"):
        try:
            tmux_name = ctx["session_tmux_by_id"](principal, kind, session)
        except Exception:
            tmux_name = None
        if not tmux_name:

            return {"ok": True, "next": since, "turns": [], "note": "unknown session"}, 200
    if not tmux_name:
        tmux_name = ctx["session_tmux"](principal, kind)

    _pid, _started, _pcwd = pane_claude(tmux_name)
    cwd = _pcwd or cockpit_cwd(tmux_name, home)
    pdir = project_dir(home, cwd)
    if _started:
        path = session_jsonl(pdir, _started, tmux_name, _pid)
    elif explicit:

        path = _sessionmap_by_tmux(tmux_name)
    else:
        path = newest_jsonl(pdir)
    if not path:
        return {"ok": True, "next": since, "turns": [], "note": "no transcript for this session yet"}, 200
    res = parse_turns(path, since=since)
    return {"ok": True, "next": res["next"], "turns": res["turns"]}, 200

def latest_assistant_text(ctx: dict, principal: str, session: str = None, target: str = "cockpit"):

    payload, _ = transcript(ctx, principal, target=target, since=0, session=session)
    asst = [t for t in payload.get("turns", []) if t.get("role") == "assistant" and t.get("text")]
    return asst[-1]["text"] if asst else ""

_READ_MAX = 200_000

def _scoped_roots(ctx: dict, principal: str):

    roots = []
    try:
        roots.append(os.path.realpath(ctx["user_dir"](principal)))
    except Exception:
        pass
    for k in ("attach_dir", "artifacts_dir"):
        d = ctx.get(k)
        if d:
            roots.append(os.path.realpath(d))

    for d in (ctx.get("read_allow_roots") or []):
        roots.append(os.path.realpath(d))
    return [r for r in roots if r]

def _within(path: str, roots) -> bool:
    rp = os.path.realpath(path)
    return any(rp == r or rp.startswith(r + os.sep) for r in roots)

def resolve_ref(ctx: dict, principal: str, ref: dict):

    if not isinstance(ref, dict):
        return None, "ref must be an object"
    kind = ref.get("kind")
    val = ref.get("value")
    if kind in ("file", "path"):
        if not isinstance(val, str) or not val:
            return None, "ref.value (path) required"
        roots = _scoped_roots(ctx, principal)
        if not _within(val, roots):
            return None, "path not in your scope"
        try:
            with open(os.path.realpath(val), "rb") as f:
                data = f.read(_READ_MAX + 1)
        except OSError as e:
            return None, "cannot read: %s" % e
        return data[:_READ_MAX].decode("utf-8", "replace"), None
    if kind == "object":

        if not isinstance(val, str) or not val:
            return None, "ref.value (object id) required"
        resolver = ctx.get("object_resolve")
        if resolver:
            try:
                p = resolver(principal, val)
            except Exception as e:
                return None, "object resolve failed: %s" % e
            if not p:
                return None, "unknown object"
            roots = _scoped_roots(ctx, principal)
            if not _within(p, roots):
                return None, "object not in your scope"
            try:
                with open(os.path.realpath(p), "rb") as f:
                    return f.read(_READ_MAX).decode("utf-8", "replace"), None
            except OSError as e:
                return None, "cannot read object: %s" % e
        return None, "object refs not supported here"
    if kind == "session-reply":
        which = ref.get("which", "latest")
        if which != "latest":
            return None, "only which=latest supported"
        txt = latest_assistant_text(ctx, principal, session=val if val else None)
        return (txt or ""), None
    return None, "unknown ref kind %r" % kind

def read(ctx: dict, principal: str, body: dict):

    if not isinstance(body, dict):
        return {"ok": False, "error": "bad body"}, 400
    if isinstance(body.get("text"), str):
        text = body["text"]
    elif isinstance(body.get("ref"), dict):
        text, err = resolve_ref(ctx, principal, body["ref"])
        if err:
            return {"ok": False, "error": err}, 400
    else:
        return {"ok": False, "error": "provide exactly one of text|ref"}, 400
    text = (text or "")[:_READ_MAX]
    audio_b64 = None
    audio_err = None
    tts = ctx.get("tts")
    if not text.strip():
        audio_err = "Kein Text zum Vorlesen."
    elif not tts:
        audio_err = "Keine Sprachausgabe eingerichtet — Text ohne Ton."
    else:
        try:
            wav = tts(text[:3000])
            if wav:
                audio_b64 = base64.b64encode(wav).decode("ascii")
            else:
                audio_err = "Sprachausgabe lieferte keinen Ton."
        except Exception as e:
            audio_err = "Sprachausgabe fehlgeschlagen: %s" % (str(e) or e.__class__.__name__)
    out = {"ok": True, "text": text, "spoken": audio_b64 is not None}
    if audio_b64 is not None:
        out["audio_b64"] = audio_b64
        out["mime"] = "audio/wav"
    else:
        out["audio_error"] = audio_err
    return out, 200

class ActionBus:

    def __init__(self, cap: int = 256):
        self._lock = threading.Lock()
        self._by_uid = {}
        self._cap = cap

    def push(self, uid: str, verb: str, args: dict = None, ts: float = None):
        with self._lock:
            st = self._by_uid.setdefault(uid, {"next": 0, "items": []})
            i = st["next"]
            item = {"i": i, "verb": verb, "args": args or {}, "ts": ts if ts is not None else time.time()}
            st["items"].append(item)
            st["next"] = i + 1
            if len(st["items"]) > self._cap:
                st["items"] = st["items"][-self._cap:]
            return i

    def since(self, uid: str, n: int = 0):
        with self._lock:
            st = self._by_uid.get(uid)
            if not st:
                return {"actions": [], "next": 0}
            acts = [a for a in st["items"] if a["i"] >= n]
            return {"actions": acts, "next": st["next"]}

class DisplayRegistry:
    def __init__(self, path: str, push_fn=None):

        self.path = path
        self.push_fn = push_fn
        self._lock = threading.Lock()

    def _load(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, d):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = "%s.tmp.%d" % (self.path, os.getpid())
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, self.path)

    def _seed(self, d):

        if "local" not in d:
            d["local"] = {"id": "local", "name": "Dieses Fenster", "kind": "local",
                          "state": "idle", "driver": "client"}
        return d

    def _assign_terminals(self, d):

        used = {r.get("terminal") for r in d.values() if isinstance(r.get("terminal"), int)}
        changed = False
        for did in sorted(d):
            r = d[did]
            if did == "local" or r.get("kind") == "local":
                continue
            if not isinstance(r.get("terminal"), int):
                n = 1
                while n in used:
                    n += 1
                r["terminal"] = n
                used.add(n)
                changed = True
        return d, changed

    def _decorate(self, rec):

        rec = dict(rec)
        t = rec.get("terminal")
        rec["label"] = rec.get("label") or (("Terminal %d" % t) if isinstance(t, int) else rec.get("name"))
        return rec

    def _ordered(self, d):
        return sorted(d, key=lambda k: (d[k].get("terminal") if isinstance(d[k].get("terminal"), int) else 999, k))

    def list(self):
        with self._lock:
            d = self._seed(self._load())
            d, changed = self._assign_terminals(d)
            if changed:
                self._save(d)
            return [self._decorate(d[k]) for k in self._ordered(d)]

    def get(self, did):
        with self._lock:
            d = self._seed(self._load())
            d, changed = self._assign_terminals(d)
            if changed:
                self._save(d)
            r = d.get(did)
            return self._decorate(r) if r else None

    def set_label(self, did, label):

        with self._lock:
            d = self._seed(self._load())
            d, _ = self._assign_terminals(d)
            if did not in d:
                return None
            lab = (label or "").strip()[:60]
            if lab:
                d[did]["label"] = lab
            else:
                d[did].pop("label", None)
            self._save(d)
            return self._decorate(d[did])

    def resolve(self, text):

        if not text:
            return None
        s = str(text).strip().lower()
        with self._lock:
            d = self._seed(self._load())
            d, changed = self._assign_terminals(d)
            if changed:
                self._save(d)
        for did in d:
            if did.lower() == s:
                return did
        m = re.search(r"(?:terminal\s*)?(\d+)", s)
        if m and ("terminal" in s or s.isdigit()):
            num = int(m.group(1))
            for did, r in d.items():
                if r.get("terminal") == num:
                    return did
        for did, r in d.items():
            lab = (r.get("label") or "").lower(); nm = (r.get("name") or "").lower()
            if s == lab or s == nm:
                return did
        for did, r in d.items():
            nm = (r.get("name") or "").lower(); lab = (r.get("label") or "").lower()
            if len(s) >= 3 and (s in nm or s in lab or (nm and nm in s)):
                return did
        return None

    def register(self, did, name, kind, driver, endpoint=None):
        with self._lock:
            d = self._seed(self._load())
            d[did] = {"id": did, "name": name, "kind": kind, "state": "idle",
                      "driver": driver, "endpoint": endpoint}
            self._save(d)
            return d[did]

    def _set_state(self, did, state, content=None):
        d = self._seed(self._load())
        if did in d:
            d[did]["state"] = state
            d[did]["content"] = content
            d[did]["updated"] = time.time()
            self._save(d)
            return d[did]
        return None

    def show(self, uid, did, resolved, where_local_verb="display", kiosk_post=None):

        with self._lock:
            disp = self._seed(self._load()).get(did)
        if not disp:
            return None, "unknown display %r" % did
        if disp["driver"] == "client":
            if self.push_fn is None:
                return None, "no push path"
            self.push_fn(uid, where_local_verb, {"ref": resolved, "where": "local"})
            with self._lock:
                self._set_state(did, "showing", resolved)
            return {"driven": "client", "display": did}, None
        if disp["driver"] == "http":
            if kiosk_post is None:
                return None, "no kiosk driver"
            ok, info = kiosk_post(disp.get("endpoint"), "/show", resolved)

            shown = bool(ok and (not isinstance(info, dict) or info.get("shown_on_tv", True)))
            with self._lock:
                self._set_state(did, "showing" if shown else ("unreachable" if ok else disp["state"]),
                                resolved if shown else None)
            if ok:
                res = {"driven": "kiosk", "endpoint": disp.get("endpoint"), "info": info, "shown": shown}
                if not shown:
                    res["warning"] = (info.get("note") if isinstance(info, dict) else None) \
                        or "Ziel scheint aus/nicht erreichbar"
                return res, None
            return None, "kiosk error: %s" % info
        return None, "unknown driver %r" % disp["driver"]

    def restore_idle(self, uid, did, kiosk_post=None):
        with self._lock:
            disp = self._seed(self._load()).get(did)
        if not disp:
            return None, "unknown display %r" % did
        if disp["driver"] == "client":
            if self.push_fn is not None:
                self.push_fn(uid, "restore-idle", {"where": "local"})
            with self._lock:
                self._set_state(did, "idle", None)
            return {"driven": "client", "display": did}, None
        if disp["driver"] == "http":
            ok, info = (kiosk_post(disp.get("endpoint"), "/idle", {}) if kiosk_post else (False, "no driver"))
            with self._lock:
                self._set_state(did, "idle" if ok else disp["state"], None)
            return ({"driven": "kiosk", "info": info}, None) if ok else (None, "kiosk error: %s" % info)
        return None, "unknown driver"

class WorkerRegistry:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def _load(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, d):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = "%s.tmp.%d" % (self.path, os.getpid())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(d, indent=2).encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, self.path)

    @staticmethod
    def public(rec):

        if not isinstance(rec, dict):
            return rec
        return {k: v for k, v in rec.items() if k != "token"}

    def list(self):

        with self._lock:
            d = self._load()
            return [self.public(d[k]) for k in sorted(d)]

    def get(self, wid):

        with self._lock:
            return self._load().get(wid)

    def register(self, wid, name, endpoint, token, caps=None):

        with self._lock:
            d = self._load()
            prev = d.get(wid) or {}
            d[wid] = {"id": wid, "name": name, "endpoint": endpoint, "token": token,
                      "caps": caps if isinstance(caps, dict) else (prev.get("caps") or {}),
                      "state": prev.get("state") or "offline",
                      "last_seen": prev.get("last_seen"),
                      "facts": prev.get("facts") or {}}
            self._save(d)
            return d[wid]

    def remove(self, wid):
        with self._lock:
            d = self._load()
            if wid not in d:
                return False
            d.pop(wid, None)
            self._save(d)
            return True

    def update_health(self, wid, facts=None, state=None, caps=None):

        with self._lock:
            d = self._load()
            rec = d.get(wid)
            if rec is None:
                return None
            if facts is not None:
                rec["facts"] = facts
            if isinstance(caps, dict):
                rec["caps"] = caps
            if state:
                rec["state"] = state
            rec["last_seen"] = time.time()
            self._save(d)
            return rec

    def mark_offline(self, wid):

        with self._lock:
            d = self._load()
            rec = d.get(wid)
            if rec is None:
                return None
            rec["state"] = "offline"
            self._save(d)
            return rec

class DeviceRegistry:

    _KIND_DRIVER = {
        "speaker": "sonos", "tv": "dlna", "cast": "cast", "nest-hub": "cast",
        "printer": "ipp", "hub": "http", "voice": "assist", "display": "display",
        "input-keyboard": "netevent", "input-mouse": "netevent", "input-trackball": "netevent",
    }

    def __init__(self, path, seed=None):
        self.path = path
        self.seed = seed if seed is not None else []
        self._lock = threading.Lock()

    def _seed_list(self):
        s = self.seed
        if callable(s):
            try:
                s = s() or []
            except Exception:
                s = []
        return list(s or [])

    def _load(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, d):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = "%s.tmp.%d" % (self.path, os.getpid())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(d, indent=2).encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, self.path)

    def _norm_seed(self, s):
        did = str(s.get("id") or "")
        kind = str(s.get("kind") or "device")
        name = str(s.get("label") or s.get("name") or did)
        return {"id": did, "name": name, "label": name, "kind": kind,
                "driver": self._KIND_DRIVER.get(kind, "http"), "source": "seed",
                "state": "unknown", "transport": (s.get("transport") or {}),
                "location": (s.get("location") or "")}

    def _merged(self):
        out = {}
        for s in self._seed_list():
            rec = self._norm_seed(s)
            if rec["id"]:
                out[rec["id"]] = rec
        for did, rec in self._load().items():
            base = out.get(did, {"id": did, "name": did, "label": did, "kind": "device",
                                 "driver": "http", "source": "manual", "state": "unknown",
                                 "transport": {}, "location": ""})
            base.update(rec)
            if rec.get("name") and not rec.get("label"):
                base["label"] = rec["name"]
            base.setdefault("label", base.get("name") or did)
            out[did] = base
        return out

    def list(self):
        with self._lock:
            out = self._merged()
            return [out[k] for k in sorted(out) if not out[k].get("hidden")]

    def get(self, did):
        with self._lock:
            return self._merged().get(did)

    def _put(self, did, patch):
        d = self._load()
        rec = d.get(did) or {}
        rec.update(patch)
        rec["id"] = did
        rec["updated"] = time.time()
        d[did] = rec
        self._save(d)
        return self._merged().get(did)

    def register(self, did, name, kind, transport=None, location=None, driver=None, source="manual"):
        with self._lock:
            return self._put(did, {"name": name, "label": name, "name_src": "manual", "kind": kind,
                                   "driver": driver or self._KIND_DRIVER.get(kind, "http"),
                                   "transport": (transport or {}), "location": (location or ""),
                                   "source": source, "hidden": False})

    def label(self, did, label):

        with self._lock:
            if did not in self._merged():
                return None
            if not label:
                d = self._load()
                if did in d:
                    d[did].pop("name", None)
                    d[did].pop("label", None)
                    d[did].pop("name_src", None)
                    self._save(d)
                return self._merged().get(did)
            return self._put(did, {"name": label, "label": label, "name_src": "manual"})

    _KEY_REVOKER = None

    @classmethod
    def set_key_revoker(cls, fn):
        cls._KEY_REVOKER = staticmethod(fn) if fn else None

    def forget(self, did):

        with self._lock:
            eintrag = self._merged().get(did)
            if eintrag is None:
                return False
            akid = eintrag.get("apikey_id")
            if akid:
                revoker = type(self)._KEY_REVOKER
                if revoker is None:
                    return False
                try:
                    if not revoker(akid):
                        return False
                except Exception:
                    return False
            self._put(did, {"hidden": True, "key_revoked": bool(akid)})
            return True

    _NAMEQ = {"ip": 0, "hostname": 1, "md": 1, "eureka": 2, "fn": 3, "manual": 4}

    def merge_discovered(self, items):

        n = 0
        with self._lock:
            d = self._load()
            addr_to_id = {}
            uuid_to_id = {}
            for _eid, _e in self._merged().items():
                _a = (_e.get("transport") or {}).get("addr")
                if _a:
                    addr_to_id.setdefault(_a, _eid)
                _u = str((_e.get("transport") or {}).get("uuid") or "").lower()
                if _u:
                    uuid_to_id.setdefault(_u, _eid)
            for it in (items or []):
                if not isinstance(it, dict):
                    continue
                did = re.sub(r"[^A-Za-z0-9_.:-]", "", str(it.get("id") or ""))[:64]
                if not did:
                    continue
                _addr = (it.get("transport") or {}).get("addr")
                _uuid = str((it.get("transport") or {}).get("uuid") or "").lower()
                if did not in d:
                    if _uuid and _uuid in uuid_to_id:
                        did = uuid_to_id[_uuid]
                    elif _addr and _addr in addr_to_id:
                        _old = addr_to_id[_addr]
                        _old_uuid = next((u for u, e in uuid_to_id.items() if e == _old), "")
                        if _uuid and _old_uuid and _old_uuid != _uuid:
                            pass

                        elif _uuid and _old in d and _old != did:
                            d[did] = d.pop(_old)
                            addr_to_id[_addr] = did
                            uuid_to_id[_uuid] = did
                        else:
                            did = _old
                rec = d.get(did) or {}
                rec.setdefault("source", "discovered")
                if it.get("name"):
                    _nsrc = str(it.get("name_src") or "fn")
                    _csrc = rec.get("name_src") or ("manual" if "label" in rec
                                                    else ("hostname" if rec.get("name") else "ip"))
                    _newq, _curq = self._NAMEQ.get(_nsrc, 3), self._NAMEQ.get(_csrc, 1)
                    if not rec.get("name") or (_csrc != "manual"
                                               and (_newq > _curq
                                                    or (_newq == _curq and _nsrc == _csrc))):
                        rec["name"] = str(it["name"])[:80]
                        rec["name_src"] = _nsrc
                if it.get("kind"):
                    _new, _cur = str(it["kind"])[:24], (rec.get("kind") or "")

                    if _cur == _new:
                        pass
                    elif _new == "cast" and _cur in ("nest-hub", "tv"):
                        pass

                    elif rec.get("source") == "manual":
                        rec.setdefault("kind", _new)
                    else:
                        rec["kind"] = _new
                        rec["driver"] = it.get("driver") or self._KIND_DRIVER.get(_new, "http")
                if it.get("transport"):

                    _tr = dict(rec.get("transport") or {})
                    _tr.update({k: v for k, v in it["transport"].items() if v is not None})
                    rec["transport"] = _tr
                if it.get("driver"):
                    rec.setdefault("driver", str(it["driver"])[:24])
                rec["state"] = str(it.get("state") or "online")[:16]
                rec["last_seen"] = time.time()
                rec["hidden"] = False
                rec["id"] = did
                d[did] = rec
                n += 1
            self._save(d)
        return n

    def set_state(self, did, state):
        with self._lock:
            return self._put(did, {"state": str(state)[:16]})

    def list_all(self):

        with self._lock:
            out = self._merged()
            return [out[k] for k in sorted(out)]

    def set_hidden(self, did, hidden):
        with self._lock:
            if did not in self._merged():
                return False
            self._put(did, {"hidden": bool(hidden)})
            return True

    def attach(self, did, sid):

        with self._lock:
            rec = self._merged().get(did)
            if not rec:
                return None
            cur = list(rec.get("sessions") or [])
            if sid not in cur:
                cur.append(sid)
            return self._put(did, {"sessions": cur, "hidden": False})

    def detach(self, did, sid):
        with self._lock:
            rec = self._merged().get(did)
            if not rec:
                return None
            cur = [s for s in (rec.get("sessions") or []) if s != sid]
            return self._put(did, {"sessions": cur})

    def for_session(self, sid):

        with self._lock:
            return [r for r in self._merged().values() if sid in (r.get("sessions") or [])]

    def detach_all(self, sid):

        with self._lock:
            d = self._load(); changed = 0
            for did, rec in list(d.items()):
                ss = rec.get("sessions") or []
                if sid in ss:
                    rec["sessions"] = [s for s in ss if s != sid]; changed += 1
            if changed:
                self._save(d)
            return changed

def _selftest():
    import tempfile
    ok = True

    def check(name, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print("  [%s] %s" % ("PASS" if cond else "FAIL", name))

    check("project_dir escapes cwd", project_dir("/h", "/home/owner").endswith("-home-owner"))

    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.jsonl")
    rows = [
        {"type": "queue-operation", "timestamp": "2026-07-06T22:00:00.000Z"},
        {"type": "user", "isSidechain": False, "timestamp": "2026-07-06T22:00:01.000Z",
         "message": {"role": "user", "content": "Hallo Box"}},
        {"type": "assistant", "isSidechain": False, "timestamp": "2026-07-06T22:00:02.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "Hallo!  Wie kann ich helfen?"},
             {"type": "tool_use", "name": "Bash", "input": {}}]}},
        {"type": "assistant", "isSidechain": True, "timestamp": "2026-07-06T22:00:03.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "subagent noise"}]}},
        {"type": "user", "isSidechain": False, "timestamp": "2026-07-06T22:00:04.000Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "content": "42"}]}},
        {"type": "assistant", "isSidechain": False, "timestamp": "2026-07-06T22:00:05.000Z",
         "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Read"}]}},
        {"type": "assistant", "isSidechain": False, "timestamp": "2026-07-06T22:00:06.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Fertig."}]}},
    ]
    with open(p, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    res = parse_turns(p, since=0)
    roles = [t["role"] for t in res["turns"]]
    check("prose-only: meta+sidechain+tool-only dropped", roles == ["user", "assistant", "assistant"])
    check("assistant prose is clean (no tool json)", res["turns"][1]["text"] == "Hallo!  Wie kann ich helfen?")
    check("next = emitted prose count", res["next"] == 3)
    check("ts parsed to unix float", isinstance(res["turns"][0]["ts"], float))
    since2 = parse_turns(p, since=2)
    check("since filter", [t["i"] for t in since2["turns"]] == [2] and since2["next"] == 3)

    p2 = os.path.join(d, "sys.jsonl")
    with open(p2, "w") as f:
        f.write(json.dumps({"type": "user", "timestamp": "2026-07-06T22:00:00.000Z",
                "message": {"role": "user", "content": "<ide_opened_file>foo</ide_opened_file>"}}) + "\n")
        f.write(json.dumps({"type": "user", "timestamp": "2026-07-06T22:00:01.000Z",
                "message": {"role": "user", "content": "zeig mir das paper<system-reminder>noise</system-reminder>"}}) + "\n")
    r2 = parse_turns(p2, since=0)
    check("pure-system user turn dropped; real utterance cleaned",
          r2["next"] == 1 and r2["turns"][0]["text"] == "zeig mir das paper")

    udir = os.path.join(d, "users", "owner")
    os.makedirs(udir)
    with open(os.path.join(udir, "note.txt"), "w") as f:
        f.write("geheim aber meins")
    ctx = {"home": d, "user_dir": lambda u: os.path.join(d, "users", u), "attach_dir": None}
    txt, err = resolve_ref(ctx, "owner", {"kind": "file", "value": os.path.join(udir, "note.txt")})
    check("in-scope file reads", err is None and txt == "geheim aber meins")
    _, err2 = resolve_ref(ctx, "owner", {"kind": "file", "value": "/etc/passwd"})
    check("out-of-scope file refused", err2 is not None)
    _, err3 = resolve_ref(ctx, "owner", {"kind": "file", "value": os.path.join(udir, "..", "..", "etc", "x")})
    check("path traversal refused", err3 is not None)

    payload, st = read({**ctx, "tts": lambda t: b"RIFFfakewav"}, "owner", {"text": "lies mich vor"})
    check("read text -> text+audio", st == 200 and payload["text"] == "lies mich vor" and "audio_b64" in payload)
    check("read audio -> spoken+mime", payload.get("spoken") is True and payload.get("mime") == "audio/wav")

    p_no, _ = read({**ctx, "tts": None}, "owner", {"text": "ohne ton"})
    check("read w/o tts -> no mime, reason given",
          p_no.get("spoken") is False and "mime" not in p_no and "audio_b64" not in p_no
          and bool(p_no.get("audio_error")))

    def _boom(t):
        raise RuntimeError("tts kaputt")
    p_err, _ = read({**ctx, "tts": _boom}, "owner", {"text": "krachen"})
    check("read tts failure -> no mime, reason given",
          p_err.get("spoken") is False and "mime" not in p_err and "tts kaputt" in (p_err.get("audio_error") or ""))
    p_nil, _ = read({**ctx, "tts": lambda t: b""}, "owner", {"text": "leer"})
    check("read empty synth -> no mime", p_nil.get("spoken") is False and "mime" not in p_nil)

    dreg = DeviceRegistry(os.path.join(d, "devices.json"),
                          seed=[{"id": "tv-1", "name": "TV", "kind": "tv"}])
    check("label unknown -> None (no phantom)", dreg.label("__nope__", "x") is None)
    check("label unknown created nothing", dreg.get("__nope__") is None
          and [r["id"] for r in dreg.list_all()] == ["tv-1"])
    check("label known -> renamed", (dreg.label("tv-1", "Fernseher") or {}).get("label") == "Fernseher")
    check("label reset -> back to seed name", (dreg.label("tv-1", "") or {}).get("label") == "TV")
    check("hide/attach/detach/forget unknown -> falsy",
          dreg.set_hidden("__nope__", True) is False and dreg.attach("__nope__", "s1") is None
          and dreg.detach("__nope__", "s1") is None and dreg.forget("__nope__") is False)

    bus = ActionBus()
    bus.push("owner", "display", {"ref": {"kind": "object", "value": "x"}})
    bus.push("owner", "speak", {"text": "hi"})
    got = bus.since("owner", 0)
    check("bus returns both + next", len(got["actions"]) == 2 and got["next"] == 2)
    got2 = bus.since("owner", 1)
    check("bus since cursor", [a["i"] for a in got2["actions"]] == [1])

    reg = DisplayRegistry(os.path.join(d, "displays.json"),
                          push_fn=lambda uid, verb, args: bus.push(uid, verb, args))
    check("local display always present", any(x["id"] == "local" for x in reg.list()))
    r, e = reg.show("owner", "local", {"kind": "text", "text": "zeig das"})
    check("show local -> client action", e is None and r["driven"] == "client")
    check("show pushed a display action", bus.since("owner", 0)["actions"][-1]["verb"] == "display")
    reg.register("kiosk-flur", "Flur-Kiosk", "kiosk", "http", "http://127.0.0.1:8099")
    posted = {}

    def fake_post(ep, route, payload):
        posted["ep"] = ep; posted["route"] = route; posted["payload"] = payload
        return True, {"ok": True}
    r2, e2 = reg.show("owner", "kiosk-flur", {"kind": "url", "value": "https://x"}, kiosk_post=fake_post)
    check("show kiosk -> http driver", e2 is None and posted["route"] == "/show")
    check("kiosk state now showing", reg.get("kiosk-flur")["state"] == "showing")
    r3, e3 = reg.restore_idle("owner", "kiosk-flur", kiosk_post=fake_post)
    check("restore-idle kiosk", e3 is None and reg.get("kiosk-flur")["state"] == "idle")

    print("\nSELFTEST:", "ALL GREEN" if ok else "FAILURES")
    return 0 if ok else 1

if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("portal_voice_ext — import me; run --selftest to verify.")
