#!/usr/bin/env python3

import os, sys, json, time, glob, subprocess, threading, queue, re, uuid, shutil

HOME = os.path.expanduser("~")

def resolve_claude():
    for c in (os.environ.get("PHANTOM_CLAUDE_BIN"),
              os.path.join(HOME, ".local/bin/claude"),
              shutil.which("claude"), "/usr/bin/claude"):
        if c and os.path.exists(c):
            return c
    return os.path.join(HOME, ".local/bin/claude")

CLAUDE    = resolve_claude()
ROOM_DIR  = os.environ.get("PHANTOM_ROOM_DIR",   "/tmp/phantom-room")
STATE_DIR = os.environ.get("PHANTOM_ROOM_STATE", os.path.join(HOME, ".local/state/phantom-room"))
WORK      = os.environ.get("PHANTOM_ROOM_WORKDIR", os.path.join(HOME, "room-workspace"))
SETTINGS  = os.environ.get("PHANTOM_ROOM_SETTINGS", os.path.join(HOME, ".claude/room-settings.json"))
PERM_MODE = os.environ.get("PHANTOM_ROOM_PERM", "default")
BASE_FLAGS = ["--permission-mode", PERM_MODE, "--settings", SETTINGS, "--add-dir", WORK, "--output-format", "json"]

SUBMIT      = os.path.join(ROOM_DIR, "room.submit")
FEED        = os.path.join(ROOM_DIR, "room.feed")
TITLE       = os.path.join(ROOM_DIR, "room.title")
ACTIVE_FILE = os.path.join(STATE_DIR, "active.json")
NAMES_FILE  = os.path.join(STATE_DIR, "names.json")
SUBMIT_POS  = os.path.join(STATE_DIR, "submit.pos")
ERRLOG      = os.path.join(STATE_DIR, "roomd.err")
TAIL_N      = 80
HIST_TURNS  = 40
HIST_BYTES  = 24000

for d in (ROOM_DIR, STATE_DIR, WORK, os.path.join(STATE_DIR, "archive")):
    os.makedirs(d, exist_ok=True)

lock = threading.Lock()
active_sid = None
epoch = 0
fork_next = False
pending_user = []
status_msg = ""
work_q = queue.Queue()

def log(m):
    sys.stderr.write(m.rstrip() + "\n"); sys.stderr.flush()

def project_dir():
    enc = re.sub(r'[^a-zA-Z0-9]', '-', WORK)
    return os.path.join(HOME, ".claude/projects", enc)

def jsonl_path(sid):
    return os.path.join(project_dir(), sid + ".jsonl")

def list_sessions():
    out = []
    for p in glob.glob(os.path.join(project_dir(), "*.jsonl")):
        sid = os.path.basename(p)[:-6]
        try: st = os.stat(p)
        except OSError: continue
        out.append({"sid": sid, "mtime": st.st_mtime, "size": st.st_size, "path": p})
    out.sort(key=lambda s: s["mtime"], reverse=True)
    return out

def load_names():
    try:
        with open(NAMES_FILE) as f: return json.load(f)
    except Exception: return {}

def save_names(d):
    try:
        with open(NAMES_FILE + ".tmp", "w") as f: json.dump(d, f)
        os.replace(NAMES_FILE + ".tmp", NAMES_FILE)
    except Exception as e: log(f"save_names: {e}")

def session_name(sid, path=None):
    nm = load_names()
    if sid in nm: return nm[sid]
    path = path or jsonl_path(sid)
    title = None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try: o = json.loads(line)
                except Exception: continue
                t = o.get("type")
                if t == "ai-title" and o.get("aiTitle"):
                    title = o["aiTitle"]
                elif t == "user" and title is None:
                    m = o.get("message") or {}; c = m.get("content")
                    txt = c if isinstance(c, str) else "".join(
                        b.get("text", "") for b in (c or []) if isinstance(b, dict) and b.get("type") == "text")
                    txt = (txt or "").strip()
                    if txt and not txt.startswith("<") and "Caveat" not in txt and "<command" not in txt:
                        title = txt[:48]
    except Exception: pass
    return title or sid[:8]

def load_active():
    global epoch
    try:
        with open(ACTIVE_FILE) as f: d = json.load(f)
        epoch = int(d.get("epoch", 0))
        return d.get("session_id") or None
    except Exception:
        return None

def save_active(sid):
    try:
        d = {"session_id": sid or None, "epoch": epoch, "jsonl": (jsonl_path(sid) if sid else None), "cwd": WORK}
        with open(ACTIVE_FILE + ".tmp", "w") as f: json.dump(d, f)
        os.replace(ACTIVE_FILE + ".tmp", ACTIVE_FILE)
    except Exception as e: log(f"save_active: {e}")

def newest_sid():
    s = list_sessions()
    return s[0]["sid"] if s else None

def load_submit_pos(default):

    try:
        return int(open(SUBMIT_POS).read().strip())
    except Exception:
        return default

def save_submit_pos(p):
    try:
        with open(SUBMIT_POS + ".tmp", "w") as f: f.write(str(p))
        os.replace(SUBMIT_POS + ".tmp", SUBMIT_POS)
    except Exception as e:
        log(f"save_submit_pos: {e}")

def set_status(m):
    global status_msg
    with lock: status_msg = m

def esc(s): return s.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", " ")

def fmt_tool(name, inp):
    if not isinstance(inp, dict): return name
    if name.startswith("mcp__"):
        name = name.replace("mcp__", "").replace("__", ".")
    if name == "Bash": return "$ " + (inp.get("command", "") or "").split("\n")[0][:200]
    if name == "Read": return "read " + str(inp.get("file_path", ""))
    if name in ("Edit", "Write", "NotebookEdit"): return name.lower() + " " + str(inp.get("file_path", ""))
    if name in ("Grep", "Glob"): return "search " + str(inp.get("pattern", inp.get("query", "")))
    if name == "TodoWrite": return "todos (%d items)" % len(inp.get("todos", []))
    desc = inp.get("description") or inp.get("prompt") or ""
    return (name + (": " + desc if desc else "")).strip()

ANSI = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')

def clamp_result(s):
    s = ANSI.sub("", s)
    if len(s) <= 1400 + 600: return s
    return s[:1400] + "\n…\n" + s[-600:]

PARSE_TAIL_BYTES = 600000

def parse_items(path):

    items = []
    if not path or not os.path.exists(path): return items
    try:
        sz = os.path.getsize(path)
        with open(path, "rb") as f:
            if sz > PARSE_TAIL_BYTES:
                f.seek(sz - PARSE_TAIL_BYTES)
                f.readline()
            raw = f.read()
        for line in raw.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line: continue
            try: o = json.loads(line)
            except Exception: continue
            if o.get("type") not in ("user", "assistant"): continue
            m = o.get("message") or {}; role = m.get("role"); c = m.get("content")
            blocks = c if isinstance(c, list) else ([{"type": "text", "text": c}] if isinstance(c, str) else [])
            for b in blocks:
                if not isinstance(b, dict): continue
                bt = b.get("type")
                if bt == "text":
                    txt = b.get("text", "")
                    if role == "user":
                        low = txt.lstrip()
                        if low.startswith("<") or "<system-reminder>" in txt or "<ide_" in txt \
                                or "Caveat:" in txt or "<local-command" in txt or "<command" in txt:
                            continue
                        items.append(("user", "text", txt))
                    else:
                        items.append(("agent0", "text", txt))
                elif bt == "thinking":
                    t = b.get("thinking") or ""
                    if t.strip(): items.append(("agent0", "thinking", t))
                elif bt == "tool_use":
                    items.append(("agent0", "tool", fmt_tool(b.get("name", ""), b.get("input", {}))))
                elif bt == "tool_result":
                    cont = b.get("content", "")
                    if isinstance(cont, list):
                        cont = "".join(x.get("text", "") for x in cont if isinstance(x, dict))
                    cont = clamp_result(str(cont).strip())
                    if cont:
                        pre = "⚠ " if b.get("is_error") else ""
                        items.append(("agent0", "result", pre + cont))
    except Exception as e:
        log(f"parse_items: {e}")
    return items

def feed_target():
    if active_sid:
        p = jsonl_path(active_sid)
        if os.path.exists(p): return p
    s = list_sessions()
    return s[0]["path"] if s else None

def build_feed_data():
    items = parse_items(feed_target())
    user_texts = {t for sp, kd, t in items if sp == "user"}
    now = time.time()
    with lock:

        for entry in list(pending_user):
            ln, ts = entry
            if ln.strip() in user_texts or (now - ts) > 90:
                pending_user.remove(entry)
        items += [("user", "text", ln) for ln, ts in pending_user]
        st = status_msg
    items = items[-TAIL_N:]
    if st: items.append(("sys", "text", st))
    return "".join("%s\t%s\t%s\n" % (sp, kd, esc(tx)) for sp, kd, tx in items)

def _atomic_write(path, data):

    tmp = "%s.tmp.%d" % (path, threading.get_ident())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)

def write_title():
    try:
        name = session_name(active_sid) if active_sid else "neue Sitzung"
        line = "ROOM · %s · /s" % name[:40]
        cur = None
        try: cur = open(TITLE).read()
        except Exception: pass
        if cur != line:
            _atomic_write(TITLE, line)
    except Exception as e: log(f"write_title: {e}")

def write_feed_now():
    try:
        _atomic_write(FEED, build_feed_data())
    except Exception as e: log(f"write_feed_now: {e}")

def feed_loop():
    last = None
    while True:
        try:
            data = build_feed_data()
            if data != last:
                _atomic_write(FEED, data)
                last = data
            write_title()
        except Exception as e:
            log(f"feed_loop: {e}")
        time.sleep(0.3)

def build_history_primer(sid):
    turns = []
    for sp, kd, tx in parse_items(jsonl_path(sid)):
        tag = {"user": "[USER]", "agent0": "[CLAUDE]"}.get(sp, "[%s]" % sp)
        if kd == "tool": tag = "[CLAUDE→tool]"
        elif kd == "result": tag = "[tool→result]"
        elif kd == "thinking": continue
        turns.append("%s %s" % (tag, tx.strip()))
    body = "\n".join(turns[-HIST_TURNS:])
    if len(body) > HIST_BYTES:
        body = "…(ältere Nachrichten weggelassen)\n" + body[-HIST_BYTES:]
    return body

def parse_sid(stdout):
    try: return json.loads((stdout or "").strip()).get("session_id")
    except Exception: return None

def run_claude(msg, target_sid, run_epoch):
    global active_sid, fork_next

    if run_epoch != epoch:
        with lock:
            pending_user[:] = [e for e in pending_user if e[0] != msg]
        write_feed_now()
        return
    fork = fork_next; fork_next = False

    with lock:
        if target_sid is None and active_sid:
            target_sid = active_sid
    def st(m):
        if run_epoch == epoch:
            set_status(m)
    st("· Claude denkt nach …"); write_feed_now()
    new_sid = None
    try:
        if target_sid and os.path.exists(jsonl_path(target_sid)):
            cmd = [CLAUDE, "--resume", target_sid] + (["--fork-session"] if fork else []) + ["-p", msg] + BASE_FLAGS
            r = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, timeout=1800)
            if r.returncode != 0:
                log(f"resume {target_sid} rc={r.returncode}; fallback new+history. err={(r.stderr or '')[:200]}")
                seed = build_history_primer(target_sid)
                new_sid = str(uuid.uuid4())
                prompt = (seed + "\n\n---\n\n" + msg) if seed else msg
                cmd = [CLAUDE, "--session-id", new_sid, "-p", prompt,
                       "--append-system-prompt",
                       "Du setzt eine unterbrochene Sitzung fort. Der obige Verlauf ist Kontext, "
                       "kein neuer Auftrag; beantworte nur die letzte Nutzernachricht."] + BASE_FLAGS
                r = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, timeout=1800)
                st("frühere Sitzung war weg — als neue Sitzung mit Verlauf fortgesetzt")
        else:
            cmd = [CLAUDE, "-p", msg] + BASE_FLAGS
            r = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            st("⚠ Claude-Fehler (rc=%s)" % r.returncode)
            log(f"claude rc={r.returncode} err={(r.stderr or '')[:300]}")
            return

        sid = parse_sid(r.stdout) or new_sid
        if not sid:
            log("run_claude: no session_id parsed; keeping active_sid")
            sid = active_sid
        with lock:
            if sid and run_epoch == epoch:
                active_sid = sid; save_active(sid)

        if sid and (target_sid is None or new_sid or fork):
            nm = load_names()
            if "__pending_name__" in nm:
                nm[sid] = nm.pop("__pending_name__"); save_names(nm)
        st("")
    except subprocess.TimeoutExpired:
        st("⚠ Timeout (30 min)")
    except Exception as e:
        st("⚠ %s" % e); log(f"run_claude: {e}")
    finally:

        with lock:
            pending_user[:] = [e for e in pending_user if e[0] != msg]
        write_feed_now()

def worker():
    while True:
        msg, target_sid, run_epoch = work_q.get()
        try: run_claude(msg, target_sid, run_epoch)
        except Exception as e: log(f"worker: {e}")

def bump_epoch():
    global epoch, fork_next
    with lock:
        epoch += 1
        fork_next = False
        pending_user.clear()

def cmd_sessions():
    s = list_sessions()
    if not s:
        set_status("Keine Sitzungen. Tippe einfach los — die erste Nachricht startet eine."); return
    lines = ["Sitzungen — /resume N wechseln, /new neu:"]
    for i, e in enumerate(s[:9], 1):
        age = int(time.time() - e["mtime"])
        ago = "%dm" % (age // 60) if age < 3600 else ("%dh" % (age // 3600) if age < 86400 else "%dd" % (age // 86400))
        mark = "  ●aktiv" if e["sid"] == active_sid else ""
        lines.append("  %d. %s  (%s)%s" % (i, session_name(e["sid"], e["path"]), ago, mark))
    set_status("\n".join(lines))

def cmd_resume(arg):
    global active_sid, fork_next
    fork = arg.endswith(" neu") or arg.endswith(" fork")
    n = arg.replace(" neu", "").replace(" fork", "").strip()
    s = list_sessions()
    try: idx = int(n)
    except Exception:
        set_status("Nutzung: /resume N  (N aus /sessions)"); return
    if not (1 <= idx <= len(s)):
        set_status("Keine Sitzung %d" % idx); return
    bump_epoch()
    active_sid = s[idx - 1]["sid"]; save_active(active_sid)
    fork_next = fork
    set_status(("→ Fork beim nächsten Senden von " if fork else "→ ") + session_name(active_sid))

def cmd_new(name):
    global active_sid
    bump_epoch()
    active_sid = None; save_active(None)
    if name:
        nm = load_names(); nm["__pending_name__"] = name; save_names(nm)
    set_status("Neue Sitzung — tippe deine Nachricht." + (" (Name: %s)" % name if name else ""))

def cmd_name(name):
    if not active_sid:
        set_status("Keine aktive Sitzung zum Benennen."); return
    nm = load_names(); nm[active_sid] = name; save_names(nm)
    set_status("Benannt: %s" % name)

def handle_command(line):
    parts = line.strip().split(None, 1)
    cmd = parts[0].lower(); arg = parts[1].strip() if len(parts) > 1 else ""
    if cmd in ("/sessions", "/s", "/ls"): cmd_sessions()
    elif cmd in ("/resume", "/r"): cmd_resume(arg)
    elif cmd in ("/new", "/n"): cmd_new(arg)
    elif cmd in ("/name", "/rename"): cmd_name(arg) if arg else set_status("Nutzung: /name <titel>")
    elif cmd in ("/help", "/h", "/?"):
        set_status("Befehle:  /sessions   /resume N [neu]   /new [name]   /name <titel>   /help")
    else:
        set_status("Unbekannt: %s  (/help)" % cmd)
    write_feed_now()

def submit_loop():
    pos = load_submit_pos(os.path.getsize(SUBMIT) if os.path.exists(SUBMIT) else 0)
    log("submit_loop: tailing %s from %d" % (SUBMIT, pos))
    while True:
        try:
            if os.path.exists(SUBMIT):
                sz = os.path.getsize(SUBMIT)

                if sz < pos: pos = sz; save_submit_pos(pos)
                if sz > pos:
                    with open(SUBMIT, encoding="utf-8", errors="replace") as f:
                        f.seek(pos); lines = [ln.rstrip("\n") for ln in f]; pos = f.tell()
                    save_submit_pos(pos)
                    for ln in lines:
                        if not ln.strip(): continue
                        if ln.lstrip().startswith("/"):
                            handle_command(ln)
                        else:
                            with lock:
                                pending_user.append((ln, time.time())); tgt = active_sid; ep = epoch
                            write_feed_now()
                            work_q.put((ln, tgt, ep))
        except Exception as e:
            log(f"submit_loop: {e}")
        time.sleep(0.2)

if __name__ == "__main__":
    active_sid = load_active()
    if active_sid and not os.path.exists(jsonl_path(active_sid)):
        active_sid = newest_sid(); save_active(active_sid)
    elif not active_sid:
        active_sid = newest_sid()
        if active_sid: save_active(active_sid)
    log("roomd: claude=%s active=%s work=%s perm=%s" % (CLAUDE, active_sid, WORK, PERM_MODE))
    threading.Thread(target=feed_loop, daemon=True).start()
    threading.Thread(target=worker, daemon=True).start()
    write_feed_now(); write_title()
    if not list_sessions():
        set_status("LLM-OS Room bereit. Tippe eine Nachricht — Claude steuert das System.  /help")
    submit_loop()
