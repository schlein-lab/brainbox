
import errno
import fcntl
import hashlib
import json
import os
import pty
import pwd
import re
import secrets
import select
import shutil
import signal
import struct
import subprocess
import termios
import threading
import time

T_LOGIN_FIRST = 20
T_LOGIN_VERIFY = 25
T_LOGIN_PROBE = 75
T_LOGIN_KILL_GRACE = 3
T_LOGIN_GC_TICK = 30
LOGIN_SESSION_TTL = 1800
LOGIN_MAX_RAW = 262144
LOGIN_MAX_INPUT = 4096
LOGIN_SCREEN_LINES = 300

LOGIN_PROVIDERS = {
    "claude": {
        "label": "Claude",
        "tool": "claude",
        "argv": ("auth", "login", "--claudeai"),
        "verify": ("auth", "status", "--json"),
        "cred": ".claude/.credentials.json",
    },

    "codex": {
        "label": "Codex (OpenAI)",
        "tool": "codex",
        "argv": ("login", "--device-auth"),
        "verify": ("login", "status"),
        "verify_mode": "exitcode",
        "probe_argv": ("exec", "--skip-git-repo-check", "-s", "read-only",
                        "Antworte mit genau einem Wort: ok"),
        "cred": ".codex/auth.json",
    },
}

RE_LOGIN_URL = re.compile(r"https://[^\s\"'<>\x07\x1b\\]*"
                          r"(?:oauth|authorize|login|device)"
                          r"[^\s\"'<>\x07\x1b\\]*")

RE_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
RE_CSI = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])")

RE_ESC_PARTIAL = re.compile(r"\x1b(?:\[[0-9;?]*|\][^\x07\x1b]*)?\Z")

RE_SECRETISH = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")

_LOGIN_LOCK = threading.Lock()
_LOGIN_SESSIONS = {}
_GC_THREAD = None

_LLMPOOL = None
_llmpool_mod = None
_prov_log = None
PNCTL = "/usr/local/bin/pnctl"

def configure(LLMPOOL=None, llmpool_mod=None, prov_log=None):
    global _LLMPOOL, _llmpool_mod, _prov_log
    _LLMPOOL = LLMPOOL
    _llmpool_mod = llmpool_mod
    _prov_log = prov_log
    _login_gc_start()

def cred_fp(home):

    if not home:
        return ""
    h = hashlib.sha256()
    try:
        with open(os.path.join(home, ".claude", ".credentials.json"), "rb") as f:
            h.update(f.read())
    except OSError:
        h.update(b"-")
    try:
        with open(os.path.join(home, ".claude.json")) as f:
            acct = (json.load(f) or {}).get("oauthAccount") or {}
        h.update(json.dumps(acct, sort_keys=True).encode())
    except (OSError, ValueError):
        h.update(b"-")
    return h.hexdigest()

def _detect_tool(name, home):

    for cand in (os.path.join(home, ".local", "bin", name),
                 os.path.join(os.path.expanduser("~"), ".local", "bin", name)):
        if os.access(cand, os.X_OK):
            return cand
    return shutil.which(name)

def _login_raw(sess):
    with sess["lock"]:
        return sess["raw"]

def _login_screen(raw):

    text = raw or ""
    lines = [""]
    row = col = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b":
            m = RE_CSI.match(text, i)
            if m:
                params, final = m.group(1), m.group(2)
                if final == "K":
                    cur = lines[row]
                    if params == "1":
                        lines[row] = " " * min(col, len(cur)) + cur[col:]
                    elif params == "2":
                        lines[row] = ""
                    else:
                        lines[row] = cur[:col]
                i = m.end()
                continue
            m = RE_OSC.match(text, i)
            if m:

                i = m.end()
                continue
            if RE_ESC_PARTIAL.match(text, i):
                break
            i += 2
            continue
        if ch == "\r":
            col = 0
        elif ch == "\n":
            row += 1
            col = 0
            while len(lines) <= row:
                lines.append("")
        elif ch == "\b":
            col = max(0, col - 1)
        elif ch == "\t":
            col = (col // 8 + 1) * 8
        elif ch >= " " and ch != "\x7f":
            cur = lines[row]
            if len(cur) < col:
                cur += " " * (col - len(cur))
            lines[row] = cur[:col] + ch + cur[col + 1:]
            col += 1
        i += 1
    if len(lines) > LOGIN_SCREEN_LINES:
        lines = lines[-LOGIN_SCREEN_LINES:]
    return [RE_SECRETISH.sub("sk-ant-<verborgen>", ln).rstrip() for ln in lines]

def _login_url(raw):

    best = None
    for m in RE_LOGIN_URL.finditer(raw or ""):
        u = m.group(0).rstrip(".,);:'\"")
        if "authorize" in u:
            return u
        if best is None:
            best = u
    return best

def _login_child(binpath, argv, home):

    try:

        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 60, 400, 0, 0))
    except Exception:
        pass
    try:
        os.chdir(home)
    except Exception:
        os._exit(126)

    env = {
        "HOME": home,
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
        "COLUMNS": "400",
        "LINES": "60",
    }
    os.environ.clear()
    os.environ.update(env)
    try:
        os.execv(binpath, [binpath] + list(argv))
    except Exception:
        os._exit(127)

def _login_exit_code(status):
    if status is None:
        return None
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return -os.WTERMSIG(status)
    return None

def _login_verify(spec, home):

    out = {"connected": False, "cred": False, "method": "", "detail": ""}
    try:
        out["cred"] = os.path.exists(os.path.join(home, spec["cred"]))
    except Exception:
        out["cred"] = False
    tool = _detect_tool(spec["tool"], home)
    if not tool:
        out["detail"] = "Programm nicht gefunden"
        return out
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env["HOME"] = home
    try:
        r = subprocess.run([tool] + list(spec["verify"]), capture_output=True,
                           text=True, timeout=T_LOGIN_VERIFY, env=env)
        rc, sout = r.returncode, r.stdout
    except Exception as e:
        out["detail"] = "Statusabfrage fehlgeschlagen (%s)" % type(e).__name__
        return out
    if spec.get("verify_mode") == "exitcode":

        out["connected"] = (rc == 0) and out["cred"]
        out["detail"] = "" if out["connected"] else ((sout or "").strip()[:200] or "nicht angemeldet")
        return out
    data = None
    blob = (sout or "").strip()
    if blob:
        try:
            data = json.loads(blob)
        except ValueError:
            m = re.search(r"\{.*\}", blob, re.S)
            if m:
                try:
                    data = json.loads(m.group(0))
                except ValueError:
                    data = None
    if isinstance(data, dict):
        out["connected"] = bool(data.get("loggedIn"))
        out["method"] = str(data.get("authMethod") or "")
        if not out["connected"]:
            out["detail"] = "nicht angemeldet"
        return out
    out["detail"] = "Statusabfrage nicht verstanden (rc=%s)" % rc
    return out

def _login_probe(home, spec=None):

    _tool_name = (spec or {}).get("tool") or "claude"
    tool = _detect_tool(_tool_name, home)
    if not tool:
        return "CLI nicht gefunden"
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env["HOME"] = home
    _probe_argv = list((spec or {}).get("probe_argv") or ("-p", "Antworte mit genau einem Wort: ok"))
    try:
        r = subprocess.run([tool] + _probe_argv,
                           capture_output=True, text=True, timeout=T_LOGIN_PROBE, env=env,
                           cwd=home)
    except subprocess.TimeoutExpired:
        return "Probe-Antwort kam nicht rechtzeitig (Box ausgelastet?) — Konto vermutlich ok"
    except Exception as e:
        return "Probe fehlgeschlagen (%s)" % type(e).__name__
    blob = ((r.stdout or "") + " " + (r.stderr or "")).lower()
    if re.search(r"authenticat|unauthor|expired|\b401\b|\b403\b|invalid.*token|login", blob) \
            and not (r.returncode == 0 and (r.stdout or "").strip()):
        return "Token abgelehnt — das Konto kann NICHT antworten (401/abgelaufen)"
    if r.returncode == 0 and (r.stdout or "").strip():
        return None

    m = re.search(r'"message"\s*:\s*"([^"]{10,300})"', (r.stderr or "") + (r.stdout or ""))
    if m:
        return "Probe fehlgeschlagen: %s" % m.group(1)
    return "Probe ohne Antwort (rc=%s)" % r.returncode

def _login_adopt(sess):

    notes = []
    aid = sess.get("aid")
    try:
        if (sess.get("provider") or "claude") != "claude":
            return "provider %s: kein Pool-Slot zu aktivieren" % (sess.get("provider"),)
        if _LLMPOOL is not None and aid:
            _LLMPOOL.set_enabled(aid, True)
            _LLMPOOL.reload()
            notes.append("pool:%s enabled" % aid)
    except Exception as e:
        notes.append("pool: %s" % type(e).__name__)
    try:
        subprocess.run([PNCTL, "restart", "pn-llmd"], capture_output=True, timeout=20)
        notes.append("pn-llmd restarted")
    except Exception:
        pass
    return "; ".join(notes)

def _account_email(home):
    try:
        if _llmpool_mod is not None and _LLMPOOL is not None:
            return (_llmpool_mod.read_account_info(home, _LLMPOOL.usage_path, ttl=0)
                    or {}).get("email") or ""
    except Exception:
        pass
    try:
        with open(os.path.join(home, ".claude.json")) as f:
            return ((json.load(f) or {}).get("oauthAccount") or {}).get("emailAddress") or ""
    except (OSError, ValueError):
        return ""

def _login_drain(sess):

    fd = sess["fd"]
    while not sess.get("stop"):
        try:
            r, _, _ = select.select([fd], [], [], 0.3)
        except (OSError, ValueError):
            break
        if not r:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError as e:
            if e.errno == errno.EAGAIN:
                continue
            break
        if not chunk:
            break
        with sess["lock"]:
            sess["raw"] += chunk.decode("utf-8", "replace")
            if len(sess["raw"]) > LOGIN_MAX_RAW:
                sess["raw"] = sess["raw"][-(LOGIN_MAX_RAW // 2):]
    sess["eof"] = True
    if sess.get("stop"):
        return
    try:
        _wpid, status = os.waitpid(sess["pid"], 0)
    except OSError:
        status = None
    else:
        sess["reaped"] = True
    sess["exit"] = _login_exit_code(status)
    try:
        result = _login_verify(sess["spec"], sess["home"])
    except Exception as e:
        result = {"connected": False, "cred": False, "method": "",
                  "detail": "Prüfung fehlgeschlagen (%s)" % e}
    if result.get("connected"):
        result["email"] = _account_email(sess["home"])
        result["switched"] = cred_fp(sess["home"]) != sess.get("baseline")
        probe = _login_probe(sess["home"], sess.get("spec"))
        result["usable"] = probe is None
        if probe is not None:
            result["detail"] = probe
        if result["usable"]:
            try:
                sess["adopted"] = _login_adopt(sess)
            except Exception as e:
                sess["adopted"] = "Übernahme fehlgeschlagen: %s" % e
        if _prov_log is not None:
            try:
                _prov_log("llm.login_done", sess.get("principal") or "?",
                          sess.get("aid") or "?", {"email": result.get("email"),
                                                   "switched": result.get("switched")})
            except Exception:
                pass
    sess["verify"] = result

def _login_kill_tree(pid, fd):

    if not pid or pid <= 0:
        return
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None

    def _signal(sig):
        sent = False
        if pgid is not None:
            try:
                os.killpg(pgid, sig)
                sent = True
            except OSError:
                pass
        if not sent:
            try:
                os.kill(pid, sig)
            except OSError:
                pass

    _signal(signal.SIGTERM)
    deadline = time.time() + T_LOGIN_KILL_GRACE
    reaped = False
    while time.time() < deadline:
        try:
            w, _ = os.waitpid(pid, os.WNOHANG)
        except OSError:
            reaped = True
            break
        if w:
            reaped = True
            break
        time.sleep(0.1)
    if not reaped:
        _signal(signal.SIGKILL)
        for _ in range(30):
            try:
                w, _ = os.waitpid(pid, os.WNOHANG)
            except OSError:
                break
            if w:
                break
            time.sleep(0.1)

    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass

def _login_reap(sid, reason="superseded"):

    with _LOGIN_LOCK:
        sess = _LOGIN_SESSIONS.pop(sid, None)
    if not sess:
        return False
    sess["stop"] = True
    sess["reason"] = reason
    th = sess.get("thread")
    if th is not None and th is not threading.current_thread():
        try:
            th.join(timeout=2)
        except RuntimeError:
            pass
    _login_kill_tree(sess.get("pid"), sess.get("fd"))
    return True

def _login_reap_all(reason="superseded"):

    with _LOGIN_LOCK:
        sids = list(_LOGIN_SESSIONS)
    for sid in sids:
        _login_reap(sid, reason)
    return len(sids)

def _login_gc():
    now = time.time()
    with _LOGIN_LOCK:
        stale = [s for s, v in _LOGIN_SESSIONS.items()
                 if now - v["created"] > LOGIN_SESSION_TTL]
    for sid in stale:
        _login_reap(sid, "expired")
    return len(stale)

def _login_gc_loop():
    while True:
        time.sleep(T_LOGIN_GC_TICK)
        try:
            _login_gc()
        except Exception:
            pass

def _login_gc_start():
    global _GC_THREAD
    if _GC_THREAD is not None and _GC_THREAD.is_alive():
        return _GC_THREAD
    _GC_THREAD = threading.Thread(target=_login_gc_loop, daemon=True, name="login-gc")
    _GC_THREAD.start()
    return _GC_THREAD

def _login_state(sess):

    raw = _login_raw(sess)
    running = not sess.get("eof")
    st = {"lines": _login_screen(raw),
          "url": _login_url(raw),
          "running": running,
          "exit": sess.get("exit")}
    v = sess.get("verify")
    if v is not None:
        st["checking"] = False
        st["connected"] = bool(v.get("connected"))
        st["cred"] = bool(v.get("cred"))
        st["method"] = v.get("method") or ""
        st["verify_detail"] = v.get("detail") or ""
        st["email"] = v.get("email") or ""
        st["switched"] = bool(v.get("switched"))
        st["usable"] = v.get("usable")
        st["adopted"] = sess.get("adopted") or ""
    else:

        st["checking"] = not running
        st["connected"] = None
    return st

def login_start(home, aid=None, principal=None, provider="claude"):

    spec = LOGIN_PROVIDERS.get(provider if isinstance(provider, str) and provider else "claude")
    if not spec:
        return {"ok": False, "reason": "unknown_provider", "detail": "unbekannter Anbieter"}
    if not home or not os.path.isdir(home):
        return {"ok": False, "reason": "no_home", "detail": "Konto-Verzeichnis fehlt"}
    _login_gc()
    superseded = _login_reap_all()
    tool = _detect_tool(spec["tool"], home)
    if not tool:
        return {"ok": False, "reason": "no_cli",
                "detail": "%s ist auf dieser Box nicht installiert" % spec["label"]}
    baseline = cred_fp(home)
    try:
        pid, fd = pty.fork()
    except OSError as e:
        return {"ok": False, "reason": "spawn_failed",
                "detail": "Start fehlgeschlagen (%s)" % e}
    if pid == 0:
        _login_child(tool, spec["argv"], home)
        os._exit(127)
    sid = secrets.token_urlsafe(18)
    sess = {"pid": pid, "fd": fd, "raw": "", "spec": spec, "home": home,
            "aid": aid, "principal": principal, "baseline": baseline,
            "provider": provider, "created": time.time(),
            "lock": threading.Lock(), "stop": False, "eof": False,
            "reaped": False, "exit": None, "verify": None, "adopted": "",
            "thread": None}
    th = threading.Thread(target=_login_drain, args=(sess,), daemon=True,
                          name="login-drain")
    sess["thread"] = th
    th.start()
    with _LOGIN_LOCK:
        _LOGIN_SESSIONS[sid] = sess

    deadline = time.time() + T_LOGIN_FIRST
    while time.time() < deadline:
        if _login_url(_login_raw(sess)) or sess.get("eof"):
            break
        time.sleep(0.15)
    out = _login_state(sess)
    out.update({"ok": True, "session": sid, "label": spec["label"],
                "superseded": superseded})
    return out

def login_poll(sid):
    with _LOGIN_LOCK:
        sess = _LOGIN_SESSIONS.get(sid)
    if not sess:
        return {"ok": False, "reason": "no_session",
                "detail": "dieser Anmelde-Versuch gilt nicht mehr"}
    out = _login_state(sess)
    out["ok"] = True
    return out

def login_input(sid, text, key):

    with _LOGIN_LOCK:
        sess = _LOGIN_SESSIONS.get(sid)
    if not sess:
        return {"ok": False, "reason": "no_session",
                "detail": "dieser Anmelde-Versuch gilt nicht mehr"}
    if sess.get("eof"):
        return {"ok": False, "reason": "ended",
                "detail": "das Anmelde-Programm läuft nicht mehr"}
    text = text if isinstance(text, str) else ""
    if len(text) > LOGIN_MAX_INPUT:
        return {"ok": False, "reason": "too_long", "detail": "Eingabe zu lang"}
    for ch in text:
        if ch != "\t" and (ch < " " or ch == "\x7f"):
            return {"ok": False, "reason": "bad_input",
                    "detail": "Eingabe enthält Steuerzeichen"}
    keymap = {"": b"", "enter": b"\r", "backspace": b"\x7f",
              "interrupt": b"\x03"}
    key = key if isinstance(key, str) else ""
    if key not in keymap:
        return {"ok": False, "reason": "bad_key", "detail": "unbekannte Taste"}
    payload = text.encode("utf-8", "replace") + keymap[key]
    if not payload:
        return {"ok": True, "sent": 0}
    try:
        os.write(sess["fd"], payload)
    except OSError as e:
        return {"ok": False, "reason": "write_failed",
                "detail": "Eingabe fehlgeschlagen (%s)" % type(e).__name__}
    return {"ok": True, "sent": len(payload)}

def login_cancel(sid):
    ok = _login_reap(sid, "cancelled")
    return {"ok": True, "stopped": bool(ok)}
