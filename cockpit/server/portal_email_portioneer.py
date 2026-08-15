
import os, sys, json, socket, signal, subprocess, time
import re, threading, html
import shutil as _shutil

HOME = os.path.expanduser("~")
CFG_DIR = os.path.join(HOME, ".config", "brainbox-portal")
DATA_DIR = os.path.join(HOME, ".local", "share", "brainbox-portal")

try:
    import llmpool as _llmpool_mod
except Exception:
    _llmpool_mod = None

_MAILER_UNAVAILABLE = ""
try:
    import mailer as _mailer
except Exception as e:
    _mailer = None
    _MAILER_UNAVAILABLE = "mailer.py nicht ladbar (%s: %s)" % (type(e).__name__, e)
if _mailer is not None:

    _miss = [n for n in ("send", "probe_senders") if not callable(getattr(_mailer, n, None))]
    if _miss:
        _MAILER_UNAVAILABLE = "mailer.py unvollständig — fehlt: %s" % ", ".join(_miss)
        _mailer = None
if _MAILER_UNAVAILABLE:
    print("[portal] SUBSYSTEM AUS: zentraler E-Mail-Versand deaktiviert — %s. Betroffen: "
          "Passwort-Reset, E-Mail-Bestätigung, Auftrags-Benachrichtigungen."
          % _MAILER_UNAVAILABLE, file=sys.stderr, flush=True)

Handler = None
_durable_vault = None
_uid_safe = None
job_log = None
load_cfg = None
send_email = None
vault_read = None

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

SYSTEM_PRINCIPAL = "__system__"

def system_secret(name):

    return vault_read(SYSTEM_PRINCIPAL, name)

def system_secret_set(name, value, kind=None):
    if _durable_vault is None:
        return False
    try:
        _durable_vault.set(SYSTEM_PRINCIPAL, name, str(value), kind=kind); return True
    except Exception:
        return False

def mailjet_credentials_present():

    return bool(system_secret("mailjet_apikey") and system_secret("mailjet_apisecret"))

def mailjet_configured():

    return bool(_mailer is not None and mailjet_credentials_present())

def mailjet_sender():
    return system_secret("mailjet_sender") or "support@brainarbeit.com"

def mailer_unavailable_detail():

    return ("Zentraler E-Mail-Versand nicht verfügbar: %s. Das ist ein Installationsfehler dieser "
            "Box, keine Fehlkonfiguration — bitte das Update bzw. die Neuinstallation einspielen."
            % (_MAILER_UNAVAILABLE or "Grund beim Start nicht erfasst"))

def mailjet_send(to, subject, text, html=None, headers=None, reply_to=None):

    if _mailer is None:
        return False, mailer_unavailable_detail()
    key = system_secret("mailjet_apikey"); sec = system_secret("mailjet_apisecret")
    if not (key and sec):
        miss = [n for n, v in (("API-Key", key), ("API-Secret", sec)) if not v]
        return False, ("Mailjet nicht konfiguriert: %s %s. Bitte unter Admin → E-Mail hinterlegen."
                       % (" und ".join(miss), "fehlt" if len(miss) == 1 else "fehlen"))
    return _mailer.send(key, sec, mailjet_sender(), to, subject, text, html=html,
                        sender_name=(system_secret("mailjet_sender_name") or "Brainarbeit"),
                        reply_to=reply_to, headers=headers)

def notify_email(to, subject, text, html=None):

    if mailjet_configured():
        ok, detail = mailjet_send(to, subject, text, html=html)
        if ok:
            return True, "mailjet:" + detail
        smtp_ok = send_email(to, subject, text)
        return smtp_ok, "mailjet-failed(%s)->smtp:%s" % (detail, "ok" if smtp_ok else "fail")
    ok = send_email(to, subject, text)
    if _mailer is None and mailjet_credentials_present():

        return ok, "mailer-unavailable(%s)->smtp:%s" % (_MAILER_UNAVAILABLE, "ok" if ok else "fail")
    return ok, "smtp:" + ("ok" if ok else "fail")

PR = os.path.join(HOME, ".local", "bin", "phantom-room")
ROOM_STATE = os.path.join(HOME, ".local", "state", "phantom-rooms")

def pr_send(room, text):
    fifo = os.path.join(ROOM_STATE, room, "input.fifo")
    line = "@both " + re.sub(r"\s+", " ", text).strip() + "\n"
    for _ in range(40):
        try:
            fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK); os.write(fd, line.encode()); os.close(fd); return True
        except OSError:
            time.sleep(1)
    return False

def feed_size(room):
    f = os.path.join(ROOM_STATE, room, "feed.log")
    return os.path.getsize(f) if os.path.exists(f) else 0

def _agents_busy(room, n=2):
    for i in range(1, n + 1):
        out = subprocess.run(["tmux", "capture-pane", "-p", "-t", f"phr-{room}-a{i}"],
                             capture_output=True, text=True).stdout or ""
        if "esc to interrupt" in out.lower():
            return True
    return False

def wait_seal(room, jid, since_off, timeout, min_elapsed=0, quiet=False, quiesce=50):

    feed = os.path.join(ROOM_STATE, room, "feed.log")
    start = time.time(); off = since_off; last_activity = time.time()
    while time.time() - start < timeout:
        time.sleep(3)
        chunk = ""
        try:
            with open(feed) as f:
                f.seek(off); chunk = f.read(); off = f.tell()
        except Exception:
            pass
        if chunk.strip():
            last_activity = time.time()
        for line in chunk.splitlines():
            if not quiet:
                job_log(jid, "  " + line[:240])
            if ("versiegelt" in line or "Auto-Runden-Cap" in line) and (time.time() - start) >= min_elapsed:
                return off
        if ((time.time() - start) >= max(min_elapsed, 30)
                and (time.time() - last_activity) >= quiesce and not _agents_busy(room)):
            if not quiet:
                job_log(jid, f"  [quiesce] {room}: Agenten ruhen, Feed still — Phase beendet")
            return off
    if not quiet:
        job_log(jid, f"[timeout] {room} nicht versiegelt in {timeout}s — fahre fort")
    return off

def room_phase(room, jid, task, work_timeout=1800):

    wait_seal(room, jid, since_off=0, timeout=150, quiet=True)
    off = feed_size(room)
    pr_send(room, task)
    wait_seal(room, jid, since_off=off, timeout=work_timeout, min_elapsed=20)

def job_link(jid):
    cfg = Handler.cfg
    scheme = "https" if cfg.get("cert") else "http"
    return f"{scheme}://{cfg.get('lan_ip')}:{cfg.get('port')}/job/{jid}"

PN_BIN = os.path.join(HOME, ".local", "bin", "pn")
CLAUDE_BIN = os.path.join(HOME, ".local", "bin", "claude")

if _llmpool_mod is not None:
    try:
        _LLMPOOL = _llmpool_mod.LLMPool(os.path.join(CFG_DIR, "llmpool.json"),
                                        os.path.join(DATA_DIR, "llmpool_state.json"), HOME)
    except Exception:
        _LLMPOOL = None
else:
    _LLMPOOL = None

_LLM_SEM = threading.Semaphore(getattr(_LLMPOOL, "max_concurrent", 4) if _LLMPOOL else 4)
PORTAL_BIN = os.path.join(HOME, ".local", "bin", "brainbox-portal")
PN_SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "pnd.sock")

PN_ACCT_DB = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.join(HOME, ".local", "share")),
                          "portioneer", "acct.db")

def _pnlib_ipc():

    for base in (os.environ.get("PNLIB_HOME"),
                 os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                              "engine"),
                 os.path.expanduser("~/portioneer")):
        if base and os.path.isdir(os.path.join(base, "pnlib")) and base not in sys.path:
            sys.path.insert(0, base)
    from pnlib import ipc as _ipc
    return _ipc

def pn_req(req, timeout=10):

    try:
        return _pnlib_ipc().send_request(req, timeout=timeout, path=PN_SOCK)
    except FileNotFoundError:
        return {"ok": False, "error": "pnd not running"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def pn_available():
    return os.path.exists(PN_SOCK) and pn_req({"verb": "ping"}, timeout=3).get("ok")

def llm_lane_reason():

    missing = [p for p in (PN_BIN, CLAUDE_BIN) if not os.path.exists(p)]
    if missing:
        return ("Der LLM-Starter fehlt in dieser Installation (%s) — die Box kann deshalb gerade "
                "keine Antwort erzeugen. Das liegt NICHT am Claude-Konto: bitte das Update bzw. die "
                "Neuinstallation der Box einspielen."
                % ", ".join(os.path.basename(p) for p in missing))
    if _LLMPOOL is not None:
        try:
            snap = _LLMPOOL.snapshot()
            if snap.get("degraded"):
                return (snap.get("status_de")
                        or "Kein Claude-Konto verbunden: bitte im Portal ein Konto anmelden.")
        except Exception:
            pass
    return ""

_LLM_TIER_DEFAULT = {"fast": "haiku", "cheap": "haiku", "quality": "sonnet", "smart": "sonnet"}

def _llm_tier_map():

    m = dict(_LLM_TIER_DEFAULT)
    try:
        from portal_session_svc import sess_models
        for ent in sess_models():
            for tier in (ent.get("tiers") or []):
                m[str(tier).lower()] = str(ent.get("id"))
    except Exception:
        pass
    try:
        raw = os.environ.get("PN_LLM_TIER_MAP", "").strip()
        if raw:
            env_map = json.loads(raw)
            if isinstance(env_map, dict):
                m.update({str(k).lower(): str(v) for k, v in env_map.items()})
    except Exception:
        pass
    return m

_REFUSAL_PATTERNS = (
    re.compile(r'"stop_reason"\s*:\s*"refusal"'),
    re.compile(r'"subtype"\s*:\s*"model_refusal[a-z_]*"'),
    re.compile(r'"api_refusal_category"\s*:\s*"'),
    re.compile(r"can'?t help with this\.\s*Start a new session", re.I),
    re.compile(r"anthropic\.com/legal/aup", re.I),
)
_REFUSAL_CAT = re.compile(r'"api_refusal_category"\s*:\s*"([a-z_]+)"')

def refusal_category(raw):

    blob = raw or ""
    if not any(rx.search(blob) for rx in _REFUSAL_PATTERNS):
        return ""
    m = _REFUSAL_CAT.search(blob)
    return m.group(1) if m else "unspecified"

def llm_run_core(prompt, system="", model="", timeout=120):

    prompt = (prompt or "").strip()
    system = (system or "").strip()
    if not prompt:
        return {"ok": False, "status": 400, "text": "", "error": "prompt required"}
    if len(prompt) > 200000 or len(system) > 16000:
        return {"ok": False, "status": 413, "text": "", "error": "prompt too long"}
    try:
        timeout = max(20, min(int(timeout or 120), 300))
    except (TypeError, ValueError):
        timeout = 120
    full = (("System:\n" + system + "\n\n") if system else "") + prompt
    model = str(model or "").strip().lower()
    model = _llm_tier_map().get(model, model)

    stream = _llmpool_mod is not None
    claude_args = ["-p", full]
    if stream:
        claude_args += ["--output-format", "stream-json", "--verbose"]
    if model and re.match(r"^[a-z0-9][a-z0-9._-]{0,40}$", model):
        claude_args += ["--model", model]
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
    if not _LLM_SEM.acquire(blocking=False):
        return {"ok": False, "status": 503, "text": "", "error": "llm busy — too many concurrent requests"}
    try:
        tried = set()

        attempts_left = max(1, _LLMPOOL.enabled_count()) if _LLMPOOL else 1
        last = {"ok": False, "status": 502, "text": "", "error": "llm error"}
        while attempts_left > 0:
            attempts_left -= 1
            acct = _LLMPOOL.pick(exclude=tried) if _LLMPOOL else None
            home = acct["home"] if acct else HOME
            aid = acct["id"] if acct else None
            if aid is not None:
                tried.add(aid)

            try:
                os.makedirs(os.path.join(home, ".claude", "session-env"), exist_ok=True)
            except OSError:
                pass

            cmd = [PN_BIN, "run", "--mem", "500", "--latency", "realtime",
                   "--timeout", str(timeout), "--tag", "llm.chat",
                   "--", "/usr/bin/env", "HOME=" + home, CLAUDE_BIN] + claude_args

            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, env=env)
            except Exception as e:
                _proc_spawn_fehler = e
                proc = None
            if proc is not None:
                try:
                    _out, _err = proc.communicate(timeout=timeout + 30)
                    pr = subprocess.CompletedProcess(cmd, proc.returncode, _out, _err)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        proc.communicate(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        try:
                            proc.communicate(timeout=5)
                        except Exception:
                            pass
                    if aid is not None:
                        _LLMPOOL.record(aid, ok=False)
                    return {"ok": False, "status": 504, "text": "", "error": "llm timeout"}
            if proc is None:
                e = _proc_spawn_fehler
                if aid is not None:
                    _LLMPOOL.record(aid, ok=False)

                print("[portal] llm_run_core: Start fehlgeschlagen (%s: %s) cmd=%s"
                      % (type(e).__name__, e, cmd[0]), file=sys.stderr, flush=True)
                return {"ok": False, "status": 500, "text": "",
                        "error": (llm_lane_reason()
                                  or "Die Box kann gerade keine Antwort erzeugen. "
                                     "Bitte einen Moment später erneut versuchen.")}
            if stream:
                text, events, is_err = _llmpool_mod.parse_stream(pr.stdout or "")
                rl = _llmpool_mod.looks_rate_limited(pr.returncode, pr.stderr, text, events)
            else:
                text, events, is_err, rl = (pr.stdout or "").strip(), [], False, False

            _blob = (text or "") + " " + (pr.stdout or "") + " " + (pr.stderr or "")
            auth_reason = _llmpool_mod.auth_reason_for(_blob)

            refusal = refusal_category(_blob) if (pr.returncode != 0 or is_err) else ""
            good = (pr.returncode == 0 and not is_err and bool(text) and not rl
                    and not auth_reason and not refusal)
            if aid is not None:
                _LLMPOOL.record(aid, ok=good, rate_events=events, was_rate_limited=rl,
                                auth_reason=auth_reason)
            if good:
                return {"ok": True, "status": 200, "text": text.strip(), "error": "", "account": aid}

            if refusal:

                return {"ok": False, "status": 451, "text": "",
                        "error": ("refused (%s): Das Modell hat diese Anfrage INHALTLICH abgelehnt. "
                                  "Das ist eine Antwort, kein Aussetzer — eine Wiederholung liefert "
                                  "dasselbe Ergebnis und kostet nur Kontingent." % refusal),
                        "refusal": refusal, "account": aid}
            if auth_reason:

                last = {"ok": False, "status": 502, "text": "",
                        "error": _llmpool_mod.auth_status_de(auth_reason),
                        "auth_reason": auth_reason}
            elif rl:
                last = {"ok": False, "status": 429, "text": "", "error": "llm rate-limited — all Max accounts busy"}
            else:

                detail = re.sub(r"/home/[^\s:'\"]+", "<path>", (pr.stderr or "")[-300:])
                detail = re.sub(r"(sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}"
                                r"|gh[pousr]_[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,})",
                                "<redacted>", detail)
                last = {"ok": False, "status": 502, "text": "", "error": "llm error", "detail": detail}
            if not (_LLMPOOL and _LLMPOOL.multi()):
                break
        return last
    finally:
        _LLM_SEM.release()

import shutil as _shutil
SEAT_RUNTIME = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "phantom-seat")
SEAT_WL = "screen-0"
SEAT_STREAM = "127.0.0.1:8092"
SEAT_VNC = "127.0.0.1:5900"
SEAT_CTL = os.path.join(SEAT_RUNTIME, "phantom.ctl")

SEAT_SIZE = "960x600"
SEAT_BIN_REL = "components/phantom/target/release/phantom"

def _seat_bin_candidates():

    out = []
    env = (os.environ.get("PHANTOM_BIN") or "").strip()
    if env:
        out.append(env)

    try:
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        out.append(os.path.join(repo, SEAT_BIN_REL))
    except Exception:
        pass
    out.append(os.path.join(HOME, "brainarbeit", SEAT_BIN_REL))
    for root in ("/opt/brainarbeit", "/srv/brainarbeit", "/usr/local/lib/brainarbeit",
                 "/usr/lib/brainarbeit"):
        out.append(os.path.join(root, SEAT_BIN_REL))
        out.append(os.path.join(root, "bin", "phantom"))
    out += ["/usr/local/bin/phantom", "/usr/bin/phantom"]
    out.append(_shutil.which("phantom") or "")
    return out

def _seat_bin():

    for c in _seat_bin_candidates():
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return ""

def _seat_bin_diag():

    seen = [c for c in _seat_bin_candidates() if c]
    noexec = [c for c in seen if os.path.isfile(c) and not os.access(c, os.X_OK)]
    if noexec:
        return ("Die Bildschirm-Komponente (phantom) ist vorhanden, aber nicht ausführbar: %s. "
                "Bitte Dateirechte reparieren (chmod +x) oder das Systemabbild neu einspielen." % noexec[0])
    return ("Die Bildschirm-Komponente (phantom) fehlt in dieser Installation — der Bildschirm kann "
            "deshalb nicht starten. Sie wird beim Systemabbild mitgeliefert; bitte das Update bzw. die "
            "Neuinstallation der Box einspielen (gesucht in: %s)." % ", ".join(seen[:4]))

def _nice_prefix():

    return ["nice", "-n", "15"] if _shutil.which("nice") else []

ADMIN_RESERVED_CORES = 1

def _reserved_cores():

    ncpu = os.cpu_count() or 2
    n = ADMIN_RESERVED_CORES
    try:
        v = load_cfg().get("admin_reserved_cores")
        if v is not None:
            n = int(v)
    except Exception:
        pass
    return min(max(0, n), max(0, ncpu - 1))

def _ensure_tenant_cap():

    try:
        ncpu = os.cpu_count() or 2
        cap = max(1, ncpu - _reserved_cores()) * 100
        if _shutil.which("systemctl"):
            subprocess.run(["systemctl", "--user", "set-property", "pn-batch.slice", "CPUQuota=%d%%" % cap],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass

_SCOPE_N = [0]; _SCOPE_OK = None
def _scope_capable():

    global _SCOPE_OK
    if _SCOPE_OK is not None:
        return _SCOPE_OK
    if not _shutil.which("systemd-run"):
        _SCOPE_OK = False
        return _SCOPE_OK
    try:
        pr = subprocess.run(["systemd-run", "--user", "--scope", "--collect", "--quiet", "/bin/true"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        _SCOPE_OK = (pr.returncode == 0)
    except Exception:
        _SCOPE_OK = False
    return _SCOPE_OK

DISPLAY_CPU_WEIGHT = int(os.environ.get("PP_DISPLAY_CPU_WEIGHT", "800"))
APP_CPU_WEIGHT     = int(os.environ.get("PP_APP_CPU_WEIGHT", "100"))

def _reweight_display_cells():

    if not _shutil.which("systemctl"):
        return
    try:
        out = subprocess.run(["systemctl", "--user", "list-units", "--type=scope", "--no-legend",
                              "--plain", "phantom-*-comp-*.scope"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return
    for line in out.splitlines():
        toks = line.split()
        unit = toks[0] if toks else ""
        if unit.startswith("phantom-") and "-comp-" in unit:
            try:
                subprocess.run(["systemctl", "--user", "set-property", unit,
                                "CPUWeight=%d" % DISPLAY_CPU_WEIGHT],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            except Exception:
                pass

_CGMOVE_BIN = "/usr/local/bin/pn-cgmove"

def _pnlib_cgdispatch():

    for base in (os.environ.get("PNLIB_HOME"),
                 os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                              "engine"),
                 os.path.expanduser("~/portioneer")):
        if base and os.path.isdir(os.path.join(base, "pnlib")) and base not in sys.path:
            sys.path.insert(0, base)
    try:
        from pnlib import cgdispatch as _cgd
        return _cgd
    except Exception:
        return None

def _cg_gc_cell_leaves(tier):

    try:
        for ent in os.listdir(tier):
            if not ent.startswith("phantom-"):
                continue
            leaf = os.path.join(tier, ent)
            try:
                with open(os.path.join(leaf, "cgroup.procs")) as f:
                    if f.read().strip():
                        continue
                os.rmdir(leaf)
            except OSError:
                pass
    except OSError:
        pass

def _cg_contains(leaf, pid):
    try:
        with open(os.path.join(leaf, "cgroup.procs")) as f:
            return str(pid) in f.read().split()
    except OSError:
        return False

def _cgdirect_launch(uid, base_argv, env, kind, sink):

    cgd = _pnlib_cgdispatch()
    try:
        tier = cgd.batch_tier_dir() if cgd else None
    except Exception:
        tier = None
    if not tier or not os.path.isfile(_CGMOVE_BIN):
        return None
    _SCOPE_N[0] += 1
    leaf_name = "phantom-%s-%s-%d" % (_uid_safe(uid), kind, _SCOPE_N[0])
    leaf = os.path.join(tier, leaf_name)
    cw = DISPLAY_CPU_WEIGHT if kind == "comp" else APP_CPU_WEIGHT
    try:
        _cg_gc_cell_leaves(tier)
        os.makedirs(leaf, exist_ok=True)
        with open(os.path.join(leaf, "cpu.weight"), "w") as f:
            f.write(str(cw))
    except OSError as e:
        print("[portal] seat-cgroup: Leaf %s nicht anlegbar (%s) — cgroup-direct Start entfaellt" % (leaf, e))
        return None

    hold = ["/bin/sh", "-c", 'kill -STOP $$; exec "$0" "$@"'] + base_argv
    try:
        p = subprocess.Popen(hold, env=env, stdout=sink, stderr=sink, start_new_session=True)
    except Exception as e:
        print("[portal] seat-cgroup: Start von %s fehlgeschlagen: %s" % (kind, e))
        return None
    moved = False
    try:
        for _ in range(100):
            try:
                with open("/proc/%d/stat" % p.pid) as f:
                    st = f.read().rsplit(") ", 1)[1].split(" ", 1)[0]
            except (OSError, IndexError):
                st = "?"
            if st in ("T", "t") or p.poll() is not None:
                break
            time.sleep(0.02)
        r = subprocess.run(["sudo", "-n", _CGMOVE_BIN, "--seat", str(p.pid), leaf_name],
                           capture_output=True, text=True, timeout=15)
        moved = (r.returncode == 0) and _cg_contains(leaf, p.pid)
        if not moved:
            print("[portal] seat-cgroup: Migration PID %d -> %s fehlgeschlagen (rc=%s): %s"
                  % (p.pid, leaf, r.returncode, ((r.stderr or "") + (r.stdout or "")).strip()[:200]))
    except Exception as e:
        print("[portal] seat-cgroup: Migration PID %d -> %s fehlgeschlagen: %s" % (p.pid, leaf, e))
    if moved:
        try:
            os.kill(p.pid, signal.SIGCONT)
        except OSError:
            pass
        print("[portal] seat-cgroup: %s-Zelle PID %d gedeckelt in %s (cpu.weight=%d, Tier-Kappen von pn-init)"
              % (kind, p.pid, leaf, cw))
        return p

    try:
        p.kill()
        p.wait(timeout=5)
    except Exception:
        pass
    try:
        os.rmdir(leaf)
    except OSError:
        pass
    return None

def _cell_launch(uid, base_argv, env, kind, fallback_prefix=None, log_path=None):

    base_argv = list(base_argv)

    sink = subprocess.DEVNULL
    if log_path:
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            sink = open(log_path, "wb", buffering=0)
        except Exception:
            sink = subprocess.DEVNULL
    if _scope_capable():
        _SCOPE_N[0] += 1
        setenvs = ["--setenv=%s=%s" % (k, v) for k, v in env.items() if os.environ.get(k) != v]
        unit = "phantom-%s-%s-%d" % (_uid_safe(uid), kind, _SCOPE_N[0])

        cw = DISPLAY_CPU_WEIGHT if kind == "comp" else APP_CPU_WEIGHT
        run = (["systemd-run", "--user", "--scope", "--collect", "--quiet",
                "--slice=pn-batch.slice", "--unit=" + unit, "--property=CPUWeight=%d" % cw] + setenvs + base_argv)
        try:
            p = subprocess.Popen(run, env=dict(os.environ),
                                 stdout=sink, stderr=sink, start_new_session=True)

            for _ in range(10):
                rc = p.poll()
                if rc is not None:
                    break
                time.sleep(0.05)
            if p.poll() not in (None, 0):
                raise OSError("systemd-run --user failed rc=%s" % p.poll())
            return p
        except Exception:
            pass
    else:
        p = _cgdirect_launch(uid, base_argv, env, kind, sink)
        if p is not None:
            return p

    print("[portal] seat-cgroup: WARNUNG — %s-Zelle startet UNGEDECKELT (kein systemd-Scope, kein "
          "cgroup-direct Leaf verfuegbar). Nur nice/no-storm schuetzen die Box jetzt." % kind)
    return subprocess.Popen((fallback_prefix or []) + base_argv, env=env,
                            stdout=sink, stderr=sink, start_new_session=True)

