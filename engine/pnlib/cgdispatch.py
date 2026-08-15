
from __future__ import annotations
import errno as _errno
import os
import signal
import sys
import threading
import time

from . import cgsandbox

CG_ROOT = "/sys/fs/cgroup"

_MIB = 1024 * 1024

def unit_name(job_id: int) -> str:

    return f"pn-job-{job_id}.service"

def _leaf_name(job_id: int) -> str:
    return f"pn-job-{job_id}"

def batch_tier_dir() -> str | None:

    d = os.environ.get("PN_CG_BATCH_DIR")
    if d:
        return d
    root = os.environ.get("PN_CG_ROOT", CG_ROOT)

    shared = os.path.join(root, "pn.slice", "batch")
    if os.path.isdir(shared):
        return shared
    cand = os.path.join(root, "pn-batch.slice")
    return cand if os.path.isdir(cand) else None

def interactive_tier_dir() -> str | None:

    d = os.environ.get("PN_CG_INTERACTIVE_DIR")
    if d:
        return d if os.path.isdir(d) else None
    root = os.environ.get("PN_CG_ROOT", CG_ROOT)
    cand = os.path.join(root, "pn.slice", "interactive")
    return cand if os.path.isdir(cand) else None

def tier_dir(track: str | None = None) -> str | None:

    if track == "interactive":
        d = interactive_tier_dir()
        if d:
            return d
    return batch_tier_dir()

def _tier_dirs() -> list:
    return [d for d in (batch_tier_dir(), interactive_tier_dir()) if d]

def _write(path: str, value) -> bool:
    try:
        with open(path, "w") as f:
            f.write(str(value))
        return True
    except OSError:
        return False

def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None

def parse_props(props: list[str]) -> dict:

    def _mem(v):
        v = v.strip()
        mult = 1
        if v[-1:] in "KMGTkmgt":
            mult = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}[v[-1].upper()]
            v = v[:-1]
        try:
            return int(float(v) * mult)
        except ValueError:
            return None

    out = {
        "mem_low": None,
        "mem_high": None, "mem_max": None, "cpu_weight": None, "cpu_quota_pct": None,
        "pids_max": None, "no_new_privs": False, "protect_system_strict": False,
        "protect_home_ro": False, "private_tmp": False, "rw_paths": [], "inaccessible": [],
        "seccomp_filter": False, "address_families": None, "restrict_af": False,
        "cell_trusted": False,
    }
    for p in props or []:
        k, _, v = p.partition("=")
        k = k.strip()
        v = v.strip()
        if k == "MemoryHigh":
            out["mem_high"] = _mem(v)
        elif k == "MemoryLow":

            out["mem_low"] = _mem(v)
        elif k == "MemoryMax":
            out["mem_max"] = _mem(v)
        elif k == "CPUWeight":
            try:
                out["cpu_weight"] = int(v)
            except ValueError:
                pass
        elif k == "CPUQuota":
            try:
                out["cpu_quota_pct"] = int(v.rstrip("%"))
            except ValueError:
                pass
        elif k == "TasksMax":
            try:
                out["pids_max"] = int(v)
            except ValueError:
                pass
        elif k == "NoNewPrivileges" and v.lower() in ("yes", "true", "1"):
            out["no_new_privs"] = True
        elif k == "ProtectSystem" and v.lower() == "strict":
            out["protect_system_strict"] = True
        elif k == "ProtectHome" and v.lower() in ("read-only", "yes", "true"):
            out["protect_home_ro"] = True
        elif k == "PrivateTmp" and v.lower() in ("yes", "true", "1"):
            out["private_tmp"] = True
        elif k == "ReadWritePaths":
            out["rw_paths"].extend(_expand(x) for x in v.split() if x)
        elif k == "InaccessiblePaths":
            out["inaccessible"].extend(_expand(x) for x in v.split() if x)
        elif k == "SystemCallFilter":
            out["seccomp_filter"] = True
        elif k == "RestrictAddressFamilies":
            out["restrict_af"] = True
            out["address_families"] = [f for f in v.split() if f]
        elif k == "X-PnCell" and v.lower() in ("1", "yes", "true"):

            out["cell_trusted"] = True
        elif k == "X-PnTrusted" and v.lower() in ("1", "yes", "true"):

            out["cell_trusted"] = True
    return out

def _expand(p: str) -> str:

    if p.startswith("%h"):
        p = os.path.expanduser("~") + p[2:]
    return os.path.expanduser(p)

def _qos_faktor() -> float:

    try:
        f = float(os.environ.get("PN_QOS_HIGH_FAKTOR", "4.0"))
    except (TypeError, ValueError):
        return 4.0
    return f

def _qos_high_bytes(knobs: dict, track: str | None, task_type: str | None):

    f = _qos_faktor()
    if f <= 0:
        return None
    mem = knobs.get("mem_low")
    if not mem:
        return None
    if track == "interactive" or knobs.get("cell_trusted"):
        return None
    if isinstance(task_type, str) and task_type.startswith("session."):
        return None
    return int(int(mem) * f)

def _write_high_nie_senken(leaf: str, value: int, qos: bool) -> bool:

    path = os.path.join(leaf, "memory.high")
    cur = _read(path)
    if cur is not None:
        cur = cur.strip()
        if cur and cur != "max":
            try:
                if int(cur) > int(value):
                    return True
            except ValueError:
                pass
    if _write(path, int(value)):
        return True
    if qos:
        try:
            sys.stderr.write("pn cgroup-direct: qos memory.high=%s auf %s nicht schreibbar — "
                             "Job laeuft ungedrosselt weiter\n" % (int(value), leaf))
        except Exception:
            pass
    return False

def ensure_leaf(job_id: int, knobs: dict, track: str | None = None,
                task_type: str | None = None) -> str:

    tier = tier_dir(track)
    if not tier:
        raise OSError(f"tier cgroup dir not found for track={track!r} "
                      f"(PN_CG_BATCH_DIR/{CG_ROOT}/pn.slice/batch)")

    _write(os.path.join(tier, "cgroup.subtree_control"), "+memory +cpu +pids")
    leaf = os.path.join(tier, _leaf_name(job_id))
    os.makedirs(leaf, exist_ok=True)

    if knobs.get("mem_low"):

        _write(os.path.join(leaf, "memory.low"), int(knobs["mem_low"]))
    high = knobs.get("mem_high")
    qos = _qos_high_bytes(knobs, track, task_type)
    if qos:
        high = qos
    if high:
        _write_high_nie_senken(leaf, int(high), bool(qos))
    if knobs.get("mem_max"):
        _write(os.path.join(leaf, "memory.max"), int(knobs["mem_max"]))
        _write(os.path.join(leaf, "memory.oom.group"), "1")

        swap = os.environ.get("PN_JOB_SWAP_MAX", "0")
        _write(os.path.join(leaf, "memory.swap.max"), swap)
    if knobs.get("cpu_weight"):
        _write(os.path.join(leaf, "cpu.weight"), int(knobs["cpu_weight"]))
    if knobs.get("cpu_quota_pct"):

        _write(os.path.join(leaf, "cpu.max"), f"{int(knobs['cpu_quota_pct']) * 1000} 100000")
    _write(os.path.join(leaf, "pids.max"), int(knobs.get("pids_max") or 512))
    return leaf

class _JobProc:
    def __init__(self, pid: int, rc_path: str):
        self.pid = pid
        self._rc_path = rc_path
        self._returncode = None
        self._lock = threading.Lock()

    def poll(self):
        with self._lock:
            if self._returncode is not None:
                return self._returncode
            try:
                wpid, status = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                self._returncode = -1
                return self._returncode
            if wpid == 0:
                return None
            self._returncode = _exit_code(status)
            return self._returncode

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            rc = self.poll()
            if rc is not None:
                return rc
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("wait timed out")
            time.sleep(0.05)

def _exit_code(status: int) -> int:
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return -1

RC_SANDBOX_REFUSED = 126

def dispatch(job_id: int, argv: list[str], cwd: str, env: dict, props: list[str],
             out_path: str, err_path: str, rc_path: str, track: str | None = None):

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    knobs = parse_props(props)

    try:

        leaf = ensure_leaf(job_id, knobs, track,
                           task_type=(env or {}).get("PN_TASK_TYPE"))
    except OSError as e:
        _fail(err_path, rc_path, f"cgroup leaf setup failed: {e}")
        return None

    if cgsandbox.landlock_available() < cgsandbox._LL_MIN_ABI:
        _fail(err_path, rc_path,
              f"Landlock ABI < {cgsandbox._LL_MIN_ABI} — refusing (write-confinement incomplete)")
        return None
    if cgsandbox.AUDIT_ARCH == 0 or cgsandbox.NR_seccomp < 0 or not cgsandbox._NR:
        _fail(err_path, rc_path, f"seccomp unsupported arch {cgsandbox._MACH} — refusing")
        return None

    priv_tmp = os.path.join(leaf_scratch_root(), _leaf_name(job_id), "tmp")
    try:
        os.makedirs(priv_tmp, exist_ok=True)
        os.chmod(priv_tmp, 0o700)
    except OSError as e:
        _fail(err_path, rc_path, f"private tmp setup failed: {e}")
        return None

    rw_paths = [p for p in knobs["rw_paths"] if p]
    rw_paths.append(priv_tmp)

    child_env = _scrub_env(env, priv_tmp)

    try:
        if os.path.exists(rc_path):
            os.unlink(rc_path)
    except OSError:
        pass

    try:
        out_fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        err_fd = os.open(err_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        rc_fd = os.open(rc_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        null_fd = os.open(os.devnull, os.O_RDONLY)
    except OSError as e:
        _fail(err_path, rc_path, f"could not open job io files: {e}")
        return None

    pid = os.fork()
    if pid == 0:

        try:
            os.setsid()
        except OSError:
            pass
        try:
            _child(job_id, leaf, argv, cwd, child_env, knobs, rw_paths,
                   out_fd, err_fd, rc_fd, null_fd)
        except BaseException as e:
            _child_refuse(rc_fd, err_fd, f"sandbox apply failed: {type(e).__name__}: {e}")

        os._exit(RC_SANDBOX_REFUSED)

    for fd in (out_fd, err_fd, rc_fd, null_fd):
        try:
            os.close(fd)
        except OSError:
            pass
    proc = _JobProc(pid, rc_path)

    t = threading.Thread(target=_reaper, args=(proc, rc_path), daemon=True)
    t.start()
    return proc

def leaf_scratch_root() -> str:

    d = os.environ.get("PN_JOB_SCRATCH_ROOT")
    if d:
        return d
    base = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return os.path.join(base, "pn-jobs")

_ENV_PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TZ", "USER", "LOGNAME")

_ENV_NEVER = ("NOTIFY_SOCKET", "WATCHDOG_PID", "WATCHDOG_USEC",
              "LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES")

def _scrub_env(job_env: dict, priv_tmp: str) -> dict:

    out = {}
    for k in _ENV_PASSTHROUGH:
        v = os.environ.get(k)
        if v is not None:
            out[k] = v
    out.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    out.setdefault("HOME", priv_tmp)
    out["TMPDIR"] = priv_tmp
    out["TMP"] = priv_tmp
    out["TEMP"] = priv_tmp
    for k, v in (job_env or {}).items():
        if k in _ENV_NEVER:
            continue
        out[k] = v
    return out

def _reaper(proc: "_JobProc", rc_path: str):
    try:
        rc = proc.wait()
    except Exception:
        return

    cur = _read(rc_path)
    if cur is None or cur.strip() == "":
        _write(rc_path, str(rc if rc is not None else -1))

def _child(job_id, leaf, argv, cwd, env, knobs, rw_paths, out_fd, err_fd, rc_fd, null_fd):

    try:
        os.dup2(null_fd, 0)
        os.dup2(out_fd, 1)
        os.dup2(err_fd, 2)
    except OSError:
        pass

    if not _write(os.path.join(leaf, "cgroup.procs"), str(os.getpid())):
        _child_refuse(rc_fd, err_fd,
                      f"could not join cgroup leaf {leaf}/cgroup.procs — refusing (ungoverned)")

    procs = _read(os.path.join(leaf, "cgroup.procs")) or ""
    if str(os.getpid()) not in procs.split():
        _child_refuse(rc_fd, err_fd, "cgroup placement not confirmed — refusing")

    try:
        os.chdir(cwd)
    except OSError:
        pass

    try:
        cgsandbox.set_no_new_privs()
        cgsandbox.drop_capabilities()

        if not knobs.get("cell_trusted"):
            cgsandbox.apply_landlock(rw_paths, inaccessible=knobs.get("inaccessible"))

        if knobs.get("cell_trusted"):

            pass
        else:
            if knobs.get("restrict_af") and knobs.get("address_families"):
                fams = cgsandbox.af_ints(knobs["address_families"])
            else:
                fams = cgsandbox.af_ints(["AF_INET", "AF_INET6"])
            cgsandbox.install_seccomp(fams)
    except cgsandbox.SandboxError as e:
        _child_refuse(rc_fd, err_fd, f"sandbox not fully applied: {e} — refusing (fail-closed)")

    argv = list(argv)
    if not argv:
        _child_refuse(rc_fd, err_fd, "empty argv — refusing")

    try:
        _auftrag = os.fork()
    except OSError:
        _auftrag = -1
    if _auftrag > 0:

        for _sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            try:
                signal.signal(_sig, signal.SIG_IGN)
            except (OSError, ValueError):
                pass
        _code = RC_SANDBOX_REFUSED
        try:
            while True:
                try:
                    _, _status = os.waitpid(_auftrag, 0)
                    break
                except InterruptedError:
                    continue
            _code = _exit_code(_status)
        except BaseException:
            pass
        try:
            os.write(rc_fd, str(_code).encode())
        except OSError:
            pass
        os._exit(_code)

    _close_inherited_fds()

    try:
        os.execvpe(argv[0], argv, env)
    except OSError as e:

        try:
            os.write(2, f"=== pn cgroup-direct: exec failed: {e} ===\n".encode())
        except OSError:
            pass
        os._exit(127)

def _close_inherited_fds():

    try:

        os.close_range(3, 2**31 - 1)
        return
    except (AttributeError, OSError):
        pass
    try:
        soft = os.sysconf("SC_OPEN_MAX")
    except (ValueError, OSError):
        soft = 4096
    for fd in range(3, int(soft)):
        try:
            os.close(fd)
        except OSError:
            pass

def _fd_write(fd, s):
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, str(s).encode())
    except OSError:
        pass

def _fd_note(fd, msg):
    try:
        os.write(fd, f"=== pn cgroup-direct: {msg} ===\n".encode())
    except OSError:
        pass

def _stderr_note(err_path, msg):
    try:
        with open(err_path, "a") as f:
            f.write(f"=== pn cgroup-direct: {msg} ===\n")
    except OSError:
        pass

def _child_refuse(rc_fd, err_fd, msg):

    _fd_write(rc_fd, str(RC_SANDBOX_REFUSED))
    _fd_note(err_fd, f"REFUSED: {msg}")
    os._exit(RC_SANDBOX_REFUSED)

def _fail(err_path, rc_path, msg):

    _write(rc_path, str(RC_SANDBOX_REFUSED))
    _stderr_note(err_path, f"REFUSED: {msg}")

def read_rc(rc_path: str):
    try:
        with open(rc_path) as f:
            return int(f.read().strip())
    except Exception:
        return None

def _leaf_for(job_id: int) -> str | None:
    for tier in _tier_dirs():
        leaf = os.path.join(tier, _leaf_name(job_id))
        if os.path.isdir(leaf):
            return leaf
    return None

def unit_status(job_id: int) -> dict:

    leaf = _leaf_for(job_id)
    if not leaf:
        return {"active": False, "result": "gone", "code": None, "loaded": False, "substate": ""}
    procs = _read(os.path.join(leaf, "cgroup.procs")) or ""
    active = bool(procs.strip())
    code = read_rc_for(job_id)
    return {"active": active, "result": "running" if active else "exited",
            "code": code, "loaded": True, "substate": "running" if active else "dead"}

def read_rc_for(job_id: int):

    return None

def stop_unit(job_id: int):

    leaf = _leaf_for(job_id)
    if not leaf:
        return
    grace = float(os.environ.get("PN_STOP_GRACE_S", "5"))
    _signal_leaf(leaf, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        procs = _read(os.path.join(leaf, "cgroup.procs")) or ""
        if not procs.strip():
            break
        time.sleep(0.1)

    if not _write(os.path.join(leaf, "cgroup.kill"), "1"):
        _signal_leaf(leaf, signal.SIGKILL)

    for _ in range(20):
        procs = _read(os.path.join(leaf, "cgroup.procs")) or ""
        if not procs.strip():
            break
        time.sleep(0.05)
    try:
        os.rmdir(leaf)
    except OSError:
        pass

def _signal_leaf(leaf: str, sig):
    procs = _read(os.path.join(leaf, "cgroup.procs")) or ""
    for tok in procs.split():
        try:
            os.kill(int(tok), sig)
        except (OSError, ValueError):
            pass

def cleanup_unit(job_id: int, wait_s: float = 1.0) -> bool:

    leaf = _leaf_for(job_id)
    removed = leaf is None
    if leaf is not None and os.path.basename(leaf) == _leaf_name(job_id):
        deadline = time.monotonic() + max(0.0, wait_s)
        while True:
            procs = _read(os.path.join(leaf, "cgroup.procs")) or ""
            if not procs.strip():
                try:
                    os.rmdir(leaf)
                    removed = True
                    break
                except FileNotFoundError:
                    removed = True
                    break
                except OSError:
                    pass
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
    if removed:
        import shutil
        shutil.rmtree(os.path.join(leaf_scratch_root(), _leaf_name(job_id)),
                      ignore_errors=True)
    return removed

def sweep_stale_leaves(keep_ids) -> int:

    keep = {int(i) for i in (keep_ids or ())}
    n = 0
    for tier in _tier_dirs():
        try:
            ents = os.listdir(tier)
        except OSError:
            continue
        for ent in ents:
            if not ent.startswith("pn-job-"):
                continue
            try:
                jid = int(ent[len("pn-job-"):])
            except ValueError:
                continue
            if jid in keep:
                continue
            if cleanup_unit(jid, wait_s=0.0):
                n += 1

    import shutil
    try:
        for ent in os.listdir(leaf_scratch_root()):
            if not ent.startswith("pn-job-"):
                continue
            try:
                jid = int(ent[len("pn-job-"):])
            except ValueError:
                continue
            if jid in keep:
                continue
            shutil.rmtree(os.path.join(leaf_scratch_root(), ent), ignore_errors=True)
    except OSError:
        pass
    return n

def job_cgroup_stats(job_id: int) -> dict:

    out = {"mem_peak": None, "cpu_s": None}
    leaf = _leaf_for(job_id)
    if not leaf:
        return out
    v = _read(os.path.join(leaf, "memory.peak"))
    if v:
        v = v.strip()
        if v and v != "max":
            try:
                out["mem_peak"] = int(v) // _MIB
            except ValueError:
                pass
    txt = _read(os.path.join(leaf, "cpu.stat")) or ""
    for line in txt.splitlines():
        if line.startswith("usage_usec"):
            try:
                out["cpu_s"] = round(int(line.split()[1]) / 1e6, 3)
            except (ValueError, IndexError):
                pass
            break
    return out

def live_units() -> set:

    tiers = _tier_dirs()
    if not tiers:
        return set()
    s = set()
    try:
        for tier in tiers:
            for ent in os.listdir(tier):
                if not ent.startswith("pn-job-"):
                    continue
                leaf = os.path.join(tier, ent)
                procs = _read(os.path.join(leaf, "cgroup.procs")) or ""
                if procs.strip():

                    s.add(ent + ".service")
    except OSError:
        return set()
    return s
