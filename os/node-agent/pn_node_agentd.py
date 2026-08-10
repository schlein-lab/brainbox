#!/usr/bin/env python3

import collections
import hashlib
import hmac
import io
import json
import os
import re
import select
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AGENT_VERSION = "1.5"

PORT = int(os.environ.get("PN_NODE_AGENT_PORT") or "8097")
TOKEN_FILE = os.environ.get("PN_NODE_AGENT_TOKEN_FILE") or os.path.expanduser(
    "~/.config/pn-node-agent/token")
DATA_DIR = os.environ.get("PN_NODE_AGENT_DATA") or os.path.expanduser(
    "~/.local/share/pn-node-agent")
JOBS_DIR = os.path.join(DATA_DIR, "jobs")

IMAGES_DIR = os.environ.get("PN_NODE_IMAGES_DIR") or os.path.join(DATA_DIR, "images")
CELLS_RUN_DIR = os.environ.get("PN_NODE_CELLS_DIR") or "/tmp/pn-cells-node"

def _aufgeloest(p):
    try:
        return os.path.realpath(p)
    except OSError:
        return p

_RUN_BASIS = os.path.dirname(_aufgeloest(CELLS_RUN_DIR).rstrip("/")) \
    if os.path.isabs(CELLS_RUN_DIR) else ""

def _log(text):

    try:
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), text))
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
_ALLOWED_LANES = ("seat", "llm", "term", "portal", "net", "act")

_LANE_ENV = {"portal": "PN_VMM_VSOCK_RFB"}
_SPLICE_BUF = 65536

SHIM_PROZESS = (os.environ.get("PN_NODE_SHIM_PROC") or "1").strip().lower() not in ("0", "no", "false")

READOPT_AN = (os.environ.get("PN_NODE_CELL_READOPT") or "1").strip().lower() not in ("0", "no", "false")
KARTE = "zelle.json"
SHIM_CFG = "shim.json"
SHIM_ZUSTAND = "shim.zustand.json"
_STAGE_MAX = 4 * (1 << 30)

_CFG_DIR = os.path.dirname(TOKEN_FILE)
MODE_FILE = os.path.join(_CFG_DIR, "mode")
HEARTBEAT_FILE = os.path.join(_CFG_DIR, "client-alive")
IDLE_HINT_FILE = os.path.join(_CFG_DIR, "idle-hint")
HEARTBEAT_MAX_S = float(os.environ.get("PN_NODE_HEARTBEAT_MAX_S", "30"))
IDLE_THRESHOLD_S = float(os.environ.get("PN_NODE_IDLE_THRESHOLD_S", "180"))

MAX_BODY = 1 << 20
MAX_TAIL = 1 << 20
DEFAULT_TAIL = 4096
JOB_TTL_S = 7 * 86400

ARCHIVE_DIR = os.path.join(_RUN_BASIS, "cell-archive") if _RUN_BASIS else "cell-archive"

VOL_DIR = os.path.join(_RUN_BASIS, "cell-volumes") if _RUN_BASIS else "cell-volumes"

def _delta_pfad(cell_id):

    return os.path.join(VOL_DIR, "%s-delta.img" % cell_id)

ARCHIVE_IMG_TTL_S = float(os.environ.get("PN_NODE_ARCHIVE_IMG_TTL_S") or 6 * 3600)
ARCHIVE_TTL_S = float(os.environ.get("PN_NODE_ARCHIVE_TTL_S") or 14 * 86400)
ARCHIVE_SWEEP_EVERY_S = 3600.0
KILL_GRACE_S = 10.0
DEFAULT_TIMEOUT_S = 3600.0
MAX_TIMEOUT_S = 86400.0
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_CLIENT_STATE = {"queued": "running", "running": "running", "done": "done",
                 "timeout": "error", "killed": "canceled", "error": "error"}
_TERMINAL = ("done", "timeout", "killed", "error")

_RUN_LOCK = threading.Lock()
_RUNNING = {}
_SUBMIT_LOCK = threading.Lock()
_SDRUN_CACHE = [None]

def _server_token():

    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""

def _authed(handler):

    want = _server_token()
    if not want:
        return False, 503
    got = handler.headers.get("X-Node-Token", "")
    if got and hmac.compare_digest(got.encode("utf-8"), want.encode("utf-8")):
        return True, 200
    return False, 401

def _meminfo_mb():
    total = avail = None
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) // 1024
                if total is not None and avail is not None:
                    break
    except (OSError, ValueError, IndexError):
        pass
    return total, avail

def _os_release():
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "unknown"

def _kvm_usable():

    if not os.path.exists("/dev/kvm"):
        return False
    try:
        fd = os.open("/dev/kvm", os.O_RDWR)
        os.close(fd)
        return True
    except OSError:
        return False

def _container_runtime():
    for c in ("docker", "podman"):
        if shutil.which(c):
            return c
    return None

def _cas_path(image_id, sha):

    return os.path.join(IMAGES_DIR, "%s@%s" % (image_id, sha))

def _sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()

def _manifest():

    try:
        with open(os.path.join(IMAGES_DIR, "manifest.json")) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}

def _cas_ref_present(ref):

    return bool(ref) and "@" in ref and os.path.exists(os.path.join(IMAGES_DIR, ref))

def _cell_base_staged():

    m = _manifest()
    rt = m.get("runtime") or {}
    for key in ("pn_vmm", "kernel", "initramfs"):
        if not _cas_ref_present(rt.get(key)):
            return False
    bases = m.get("bases") or {}
    return any(_cas_ref_present(ref) for ref in bases.values())

def _pn_vmm_bin():

    env = os.environ.get("PN_VMM_BIN")
    if env and os.access(env, os.X_OK):
        return env

    ref = (_manifest().get("runtime") or {}).get("pn_vmm")
    if _cas_ref_present(ref):
        p = os.path.join(IMAGES_DIR, ref)
        if os.access(p, os.X_OK):
            return p
    home = os.path.expanduser("~")
    for p in ("%s/pn-vmm-build/target/release/pn-vmm" % home,
              "%s/brainarbeit/os/pn-vmm/target/release/pn-vmm" % home,
              "%s/.local/lib/pn-vmm/pn-vmm" % home):
        if os.access(p, os.X_OK):
            return p
    w = shutil.which("pn-vmm")
    return w if w else None

def _caps():
    kvm = _kvm_usable()
    vmm = _pn_vmm_bin()
    staged = _cell_base_staged()

    kinds = ["exec"]
    if kvm and vmm and staged:
        kinds.append("cell")
    return {
        "arch": os.uname().machine,
        "kvm": kvm,

        "cells": bool(kvm and vmm),

        "cell_base_staged": bool(staged),
        "kinds": kinds,
        "pn_vmm": vmm,
        "cell_sandbox": True,
        "container": _container_runtime(),
    }

def _cgroup_budget_mb():

    basis = None
    ziel = "/sys/fs/cgroup/pn.slice/interactive/sessions"
    if os.path.isdir(ziel):
        basis = ziel
    else:
        try:
            with open("/proc/self/cgroup") as f:
                pfad = f.read().strip().split(":")[-1]
            basis = os.path.join("/sys/fs/cgroup", pfad.lstrip("/"))
        except OSError:
            return (None, None)

    def _lies(name):
        try:
            with open(os.path.join(basis, name)) as f:
                roh = f.read().strip()
        except OSError:
            return None
        if roh == "max":
            return None
        try:
            return int(roh) // (1024 * 1024)
        except ValueError:
            return None

    deckel = _lies("memory.max")
    if deckel is None:
        return (None, None)

    hart = 0
    gefunden = False
    try:
        with open(os.path.join(basis, "memory.stat")) as f:
            for zeile in f:
                teil = zeile.split()
                if len(teil) != 2:
                    continue
                if teil[0] in ("anon", "kernel", "slab", "file_dirty", "file_writeback"):
                    try:
                        hart += int(teil[1])
                        gefunden = True
                    except ValueError:
                        pass
    except OSError:
        gefunden = False
    if not gefunden:
        return (deckel, _lies("memory.current"))
    return (deckel, hart // (1024 * 1024))

def _deltas_bilanz():

    belegt = entfaltet = 0
    n = 0
    aeltestes = None
    try:
        for name in os.listdir(VOL_DIR):
            if not name.endswith("-delta.img"):
                continue
            try:
                st = os.stat(os.path.join(VOL_DIR, name))
            except OSError:
                continue
            n += 1
            belegt += st.st_blocks * 512
            entfaltet += st.st_size
            if aeltestes is None or st.st_mtime < aeltestes:
                aeltestes = st.st_mtime
    except OSError:
        return None
    return {"anzahl": n,
            "belegt_mb": belegt // (1024 * 1024),
            "entfaltet_mb": entfaltet // (1024 * 1024),
            "aeltestes_alter_s": None if aeltestes is None else int(time.time() - aeltestes)}

def _health():
    u = os.uname()
    total_mb, avail_mb = _meminfo_mb()
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        load1 = None
    try:
        with open("/proc/uptime") as f:
            uptime_s = int(float(f.read().split()[0]))
    except (OSError, ValueError, IndexError):
        uptime_s = None
    try:
        disk_free_mb = shutil.disk_usage(DATA_DIR).free // (1024 * 1024)
    except OSError:
        disk_free_mb = None
    return {
        "ok": True,
        "agent_version": AGENT_VERSION,
        "arch": u.machine,
        "os_release": _os_release(),
        "hostname": u.nodename,
        "nproc": os.cpu_count(),
        "mem_total_mb": total_mb,
        "mem_avail_mb": avail_mb,

        **(lambda d, b: {"mem_cgroup_max_mb": d, "mem_cgroup_used_mb": b,
                         "mem_budget_mb": (avail_mb if (d is None or b is None)
                                           else max(0, min(avail_mb, d - b - 512)))})(
            *_cgroup_budget_mb()),
        "disk_free_mb": disk_free_mb,

        "deltas": _deltas_bilanz(),
        "load1": load1,
        "uptime_s": uptime_s,
        "caps": _caps(),
        "mode": _node_mode(),
        "node_active": _node_active(),
        "draining": not _node_active(),
        "heartbeat": _heartbeat_state(),
        "running": len(_RUNNING),
        "running_cells": _running_cells(),

        "shim_process": SHIM_PROZESS,
        "cell_readoption": READOPT_AN,

        "lane_redial": (os.environ.get("PN_NODE_LANE_REDIAL") or "1").strip().lower()
                       not in ("0", "no", "false"),
    }

def _job_dir(job_id):
    return os.path.join(JOBS_DIR, job_id)

def _meta_path(job_id):
    return os.path.join(_job_dir(job_id), "meta.json")

def _meta_read(job_id):
    try:
        with open(_meta_path(job_id)) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None

def _meta_write(job_id, meta):
    path = _meta_path(job_id)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp, path)

def _systemd_run_usable():

    if _SDRUN_CACHE[0] is None:
        ok = False
        if shutil.which("systemd-run"):
            try:
                ok = subprocess.run(
                    ["systemd-run", "--user", "--scope", "-q", "true"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=10).returncode == 0
            except (OSError, subprocess.SubprocessError):
                ok = False
        _SDRUN_CACHE[0] = ok
    return _SDRUN_CACHE[0]

def _kill_group(pid, sig):
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, sig)
        except OSError:
            pass

def _run_job(job_id, argv, timeout_s, mem_mb, cwd, env_over):
    jd = _job_dir(job_id)
    meta = _meta_read(job_id) or {"id": job_id, "argv": argv}
    use_sdrun = _systemd_run_usable()
    cmd = list(argv)
    preexec = None
    if use_sdrun:
        cmd = ["systemd-run", "--user", "--scope", "-q", "-p", "CPUWeight=50"]
        if mem_mb:
            cmd += ["-p", "MemoryMax=%dM" % mem_mb]
        cmd += ["--"] + list(argv)
    elif mem_mb:
        limit = int(mem_mb) * 1024 * 1024

        def preexec():
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    env = dict(os.environ)
    if env_over:
        env.update(env_over)
    env["PN_JOB_ID"] = job_id
    started = time.time()
    try:
        with open(os.path.join(jd, "out"), "wb") as fo, \
                open(os.path.join(jd, "err"), "wb") as fe:
            p = subprocess.Popen(
                cmd, stdout=fo, stderr=fe, stdin=subprocess.DEVNULL,
                cwd=(cwd or None), env=env, start_new_session=True,
                preexec_fn=preexec)
    except (OSError, subprocess.SubprocessError) as e:
        meta.update({"state": "error", "rc": None, "started": started,
                     "ended": time.time(), "error": str(e)[:300]})
        _meta_write(job_id, meta)
        return
    with _RUN_LOCK:
        _RUNNING[job_id] = {"popen": p, "killed": False}
    meta.update({"state": "running", "rc": None, "pid": p.pid, "started": started,
                 "ended": None, "runner": "systemd-run" if use_sdrun else "subprocess"})
    _meta_write(job_id, meta)
    state = "done"
    try:
        rc = p.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        state = "timeout"
        _kill_group(p.pid, signal.SIGTERM)
        try:
            rc = p.wait(timeout=KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            _kill_group(p.pid, signal.SIGKILL)
            rc = p.wait()
    with _RUN_LOCK:
        entry = _RUNNING.pop(job_id, None)
    if state == "done" and entry and entry.get("killed"):
        state = "killed"
    meta.update({"state": state, "rc": rc, "ended": time.time()})
    _meta_write(job_id, meta)

def _exec_submit(body):

    argv = body.get("argv")
    if not isinstance(argv, list) or not argv or not all(
            isinstance(a, str) and a for a in argv):
        return {"ok": False, "error": "argv must be a non-empty list of strings"}, 400
    try:
        timeout_s = min(float(body.get("timeout_s") or DEFAULT_TIMEOUT_S), MAX_TIMEOUT_S)
        if timeout_s <= 0:
            timeout_s = DEFAULT_TIMEOUT_S
    except (TypeError, ValueError):
        return {"ok": False, "error": "bad timeout_s"}, 400
    mem_mb = body.get("mem_mb")
    if mem_mb is not None:
        try:
            mem_mb = int(mem_mb)
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad mem_mb"}, 400
        if mem_mb <= 0:
            mem_mb = None
    cwd = body.get("cwd")
    if cwd is not None:
        cwd = os.path.expanduser(str(cwd))
        if not os.path.isdir(cwd):
            return {"ok": False, "error": "cwd is not a directory"}, 400
    env_over = body.get("env")
    if env_over is not None:
        if not isinstance(env_over, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in env_over.items()):
            return {"ok": False, "error": "env must be a dict of strings"}, 400

    want_id = body.get("job_id")
    if want_id is not None:
        want_id = str(want_id)
        if not _ID_RE.match(want_id):
            return {"ok": False, "error": "bad job_id"}, 400

    with _SUBMIT_LOCK:
        if want_id is not None:
            existing = _meta_read(want_id)
            if existing is not None:
                return {"ok": True, "job_id": want_id, "existing": True,
                        "state": _CLIENT_STATE.get(existing.get("state"), "running")}, 200
            job_id = want_id
        else:
            job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
        os.makedirs(_job_dir(job_id), exist_ok=True)
        _meta_write(job_id, {"id": job_id, "argv": argv, "state": "queued", "rc": None,
                             "started": None, "ended": None, "timeout_s": timeout_s,
                             "mem_mb": mem_mb, "cwd": cwd})
    threading.Thread(target=_run_job, name="job-%s" % job_id,
                     args=(job_id, argv, timeout_s, mem_mb, cwd, env_over),
                     daemon=True).start()
    return {"ok": True, "job_id": job_id}, 200

def _job_kill(job_id):
    with _RUN_LOCK:
        entry = _RUNNING.get(job_id)
        if entry:
            entry["killed"] = True
    if not entry:
        meta = _meta_read(job_id)
        if meta is None:
            return {"ok": False, "error": "unknown job"}, 404
        return {"ok": False, "error": "job not running (state %r; nach einem Agent-Neustart "
                "sind Altjobs nicht mehr adressierbar)" % meta.get("state")}, 409
    p = entry["popen"]
    _kill_group(p.pid, signal.SIGTERM)

    def _hard():
        time.sleep(KILL_GRACE_S)
        if p.poll() is None:
            _kill_group(p.pid, signal.SIGKILL)
    threading.Thread(target=_hard, daemon=True).start()
    return {"ok": True, "job_id": job_id, "signalled": "SIGTERM",
            "hard_kill_after_s": KILL_GRACE_S}, 200

def _tail_file(path, nbytes):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > nbytes:
                f.seek(size - nbytes)
            return f.read(nbytes)
    except OSError:
        return b""

def _read_from(path, offset):

    try:
        size = os.path.getsize(path)
    except OSError:
        return b"", 0
    if offset < 0:
        offset = 0
    if offset >= size:
        return b"", size
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            return f.read(min(size - offset, MAX_TAIL)), size
    except OSError:
        return b"", size

def _sweep_archive(root=None):

    basis = root or os.path.expanduser(
        os.environ.get("PN_NODE_ARCHIVE_DIR")
        or os.path.join(os.path.dirname(CELLS_RUN_DIR.rstrip("/")), "cell-archive"))
    if not os.path.isdir(basis):
        return 0, 0
    now = time.time()
    bilder = eintraege = 0
    for wurzel, verzeichnisse, dateien in os.walk(basis, topdown=False):
        if wurzel == basis:
            continue
        try:
            alter = now - os.path.getmtime(wurzel)
        except OSError:
            continue

        if alter > ARCHIVE_IMG_TTL_S:
            for d in dateien:
                if d.endswith(".img"):
                    try:
                        os.remove(os.path.join(wurzel, d))
                        bilder += 1
                    except OSError:
                        pass

        if alter > ARCHIVE_TTL_S:
            try:
                shutil.rmtree(wurzel, ignore_errors=True)
                eintraege += 1
            except OSError:
                pass

    for name in list(os.listdir(basis)):
        p = os.path.join(basis, name)
        try:
            if os.path.isdir(p) and not os.listdir(p):
                os.rmdir(p)
        except OSError:
            pass
    return bilder, eintraege

VOL_SWEEP_GRACE_S = float(os.environ.get("PN_NODE_VOL_GRACE_S") or 900)

def _archive_sweeper():

    def schleife():
        while True:
            try:
                b, e = _sweep_archive()

                if b or e:
                    sys.stderr.write("[archiv] %d Bild(er) und %d Eintrag/Eintraege abgeraeumt\n"
                                     % (b, e))
            except Exception:
                pass
            time.sleep(ARCHIVE_SWEEP_EVERY_S)
    threading.Thread(target=schleife, daemon=True).start()

def _sweep_jobs():

    now = time.time()
    removed = 0
    try:
        entries = os.listdir(JOBS_DIR)
    except OSError:
        return 0
    for name in entries:
        jd = os.path.join(JOBS_DIR, name)
        if not os.path.isdir(jd):
            continue
        meta = _meta_read(name)
        if meta is not None and meta.get("state") in (None, "running", "queued"):

            ref = meta.get("started") or meta.get("ended")
        else:
            ref = (meta or {}).get("ended") or (meta or {}).get("started")
        if ref is None:
            try:
                ref = os.path.getmtime(jd)
            except OSError:
                continue
        if now - float(ref) > JOB_TTL_S:
            try:
                shutil.rmtree(jd)
                removed += 1
            except OSError:
                pass
    return removed

def _node_mode():
    try:
        m = open(MODE_FILE).read().strip().lower()
        return m if m in ("on", "idle-only", "off") else "on"
    except OSError:
        return "on"

def _heartbeat_state():

    try:
        age = time.time() - os.path.getmtime(HEARTBEAT_FILE)
    except OSError:
        return "none"
    return "fresh" if age <= HEARTBEAT_MAX_S else "stale"

def _idle_seconds():
    try:
        return float(open(IDLE_HINT_FILE).read().strip())
    except (OSError, ValueError):
        return -1.0

def _node_active():

    mode = _node_mode()
    if mode == "off":
        return False
    if _heartbeat_state() == "stale":
        return False
    if mode == "idle-only":
        return _idle_seconds() >= IDLE_THRESHOLD_S
    return True

_CELLS_LOCK = threading.Lock()
_CELLS = {}
_CID_USED = set()

def _running_cells():

    n = 0
    with _CELLS_LOCK:
        cells = list(_CELLS.values())
    for c in cells:

        sp = c.get("shim_proc")
        if sp is not None:
            try:
                sp.poll()
            except Exception:
                pass
        try:
            if c["proc"].poll() is None:
                n += 1
        except Exception:
            pass
    return n

def _alloc_node_cid():

    with _CELLS_LOCK:
        for c in range(3, 250):
            if c not in _CID_USED:
                _CID_USED.add(c)
                return c
    raise RuntimeError("keine freie guest-CID")

def _delta_want_mb(mb):
    try:
        return max(512, min(int(mb or 0) or 512, 16384))
    except (TypeError, ValueError):
        return 512

def _journal_mb(want_mb):

    return 8 if int(want_mb or 0) >= 1024 else 4

def _build_delta(delta, mb):

    seed = os.urandom(256)
    stg = tempfile.mkdtemp(prefix="pn-delta-")
    try:
        os.makedirs(os.path.join(stg, "upper"))
        os.makedirs(os.path.join(stg, "work"))
        with open(os.path.join(stg, "upper", "seed"), "wb") as f:
            f.write(seed)
        subprocess.run(["truncate", "-s", "%dM" % _delta_want_mb(mb), delta], check=True)
        subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q",
                        "-J", "size=%d" % _journal_mb(_delta_want_mb(mb)),
                        "-d", stg, delta], check=True)
    finally:
        shutil.rmtree(stg, ignore_errors=True)

def _build_work(work, mb):

    want_mb = max(64, min(int(mb or 0) or 64, 4 << 20))
    stg = tempfile.mkdtemp(prefix="pn-work-")
    try:
        os.makedirs(os.path.join(stg, "flatpak"))
        subprocess.run(["truncate", "-s", "%dM" % want_mb, work], check=True)
        subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q",
                        "-J", "size=%d" % _journal_mb(want_mb),
                        "-d", stg, work], check=True)
    finally:
        shutil.rmtree(stg, ignore_errors=True)

def _splice(src, dst, on_close):
    try:
        while True:
            data = src.recv(_SPLICE_BUF)
            if not data:
                break
            dst.sendall(data)
    except (OSError, ssl.SSLError):
        pass
    finally:
        on_close()

def _pump(a, b):

    done = threading.Event()

    def close_both():
        if not done.is_set():
            done.set()
            for s in (a, b):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
    t1 = threading.Thread(target=_splice, args=(a, b, close_both), daemon=True)
    t2 = threading.Thread(target=_splice, args=(b, a, close_both), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

def _dial_box(box_lane, cell_id, lane):

    host = box_lane.get("host")
    try:
        port = int(box_lane.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    if not host or not port:
        return None
    ca_pem = box_lane.get("ca_pem") or ""
    want_fp = str(box_lane.get("ca_sha256") or "").replace(":", "").lower()
    if not ca_pem or not want_fp:
        sys.stderr.write("[cell-shim] %s/%s: kein CA-Pin -> fail closed\n" % (cell_id, lane))
        return None

    try:
        der = ssl.PEM_cert_to_DER_cert(ca_pem)
        got_fp = hashlib.sha256(der).hexdigest()
    except (ValueError, ssl.SSLError):
        return None
    if got_fp != want_fp:
        sys.stderr.write("[cell-shim] %s/%s: CA-Fingerprint mismatch -> fail closed\n" % (cell_id, lane))
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    try:
        ctx.load_verify_locations(cadata=ca_pem)
    except (ssl.SSLError, OSError):
        return None
    try:
        raw = socket.create_connection((host, port), timeout=20)
        tls = ctx.wrap_socket(raw, server_hostname=host)
    except (OSError, ssl.SSLError) as e:
        sys.stderr.write("[cell-shim] %s/%s: box dial %s:%d fehlgeschlagen (%s)\n"
                         % (cell_id, lane, host, port, e))
        return None
    hs = json.dumps({"token": _server_token(), "cell_id": cell_id, "lane": lane}) + "\n"
    try:
        tls.sendall(hs.encode("utf-8"))
    except (OSError, ssl.SSLError):
        try:
            tls.close()
        except OSError:
            pass
        return None

    tls.settimeout(None)
    return tls

def _pump_dbg(cell_id, lane, pnvmm, box):

    cnt = {"b2g": 0, "g2b": 0}
    done = threading.Event()

    def close_both():
        if not done.is_set():
            done.set()
            for s in (pnvmm, box):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def s(src, dst, key):
        try:
            while True:
                d = src.recv(_SPLICE_BUF)
                if not d:
                    sys.stderr.write("[shim %s/%s] %s EOF (total %d)\n" % (cell_id[:24], lane, key, cnt[key]))
                    break
                dst.sendall(d)
                cnt[key] += len(d)
                sys.stderr.write("[shim %s/%s] %s +%d total=%d head=%r\n"
                                 % (cell_id[:24], lane, key, len(d), cnt[key], d[:40]))
        except (OSError, ssl.SSLError) as e:
            sys.stderr.write("[shim %s/%s] %s ERR %s (total %d)\n" % (cell_id[:24], lane, key, e, cnt[key]))
        finally:
            close_both()
    t1 = threading.Thread(target=s, args=(box, pnvmm, "b2g"), daemon=True)
    t2 = threading.Thread(target=s, args=(pnvmm, box, "g2b"), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

def _pump_zweiweg(gast, box):

    def _wer(s):
        return "gast" if s is gast else "box"

    try:
        gast.setblocking(False)
        box.setblocking(False)
    except OSError:
        return "box"
    puffer = {gast: b"", box: b""}
    partner = {gast: box, box: gast}
    grenze = 1 << 20
    try:
        while True:
            lesen = [s for s in (gast, box) if len(puffer[partner[s]]) < grenze]
            schreiben = [s for s in (gast, box) if puffer[s]]
            try:
                if getattr(box, "pending", None) and box.pending():
                    bereit_r, bereit_w = [box], schreiben
                else:
                    bereit_r, bereit_w, _ = select.select(lesen, schreiben, [], 60.0)
            except (OSError, ValueError, ssl.SSLError):
                return "box"
            for s in bereit_r:
                try:
                    data = s.recv(_SPLICE_BUF)
                except (ssl.SSLWantReadError, ssl.SSLWantWriteError, BlockingIOError):
                    continue
                except (OSError, ssl.SSLError):
                    return _wer(s)
                if not data:
                    return _wer(s)
                puffer[partner[s]] += data
            for s in bereit_w:
                if not puffer[s]:
                    continue
                try:
                    n = s.send(puffer[s])
                except (ssl.SSLWantReadError, ssl.SSLWantWriteError, BlockingIOError):
                    continue
                except (OSError, ssl.SSLError):
                    return _wer(s)
                puffer[s] = puffer[s][n:]
    finally:
        try:
            gast.setblocking(True)
        except OSError:
            pass

def _proc_start(pid):

    try:
        with io.open("/proc/%d/stat" % int(pid), encoding="utf-8", errors="replace") as f:
            s = f.read()
    except (OSError, ValueError, TypeError):
        return None
    try:
        rest = s[s.rindex(")") + 2:].split()
        return int(rest[19])
    except (ValueError, IndexError):
        return None

def _proc_zustand(pid):

    try:
        with io.open("/proc/%d/stat" % int(pid), encoding="utf-8", errors="replace") as f:
            s = f.read()
        return s[s.rindex(")") + 2:].split()[0]
    except (OSError, ValueError, TypeError, IndexError):
        return ""

def _proc_lebt(pid, start=None):

    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0 or not os.path.isdir("/proc/%d" % pid):
        return False
    if _proc_zustand(pid) == "Z":
        return False
    if start is None:
        return True
    try:
        return _proc_start(pid) == int(start)
    except (TypeError, ValueError):
        return False

def _proc_cmdline(pid):
    try:
        with io.open("/proc/%d/cmdline" % int(pid), "rb") as f:
            return f.read().decode("utf-8", "replace").replace("\x00", " ")
    except (OSError, ValueError, TypeError):
        return ""

def _umgebung(pid):

    try:
        with io.open("/proc/%d/environ" % int(pid), "rb") as f:
            roh = f.read().decode("utf-8", "replace")
    except (OSError, ValueError, TypeError):
        return {}
    aus = {}
    for eintrag in roh.split("\x00"):
        if "=" in eintrag:
            name, _, wert = eintrag.partition("=")
            aus[name] = wert
    return aus

def _unsere_vm(pid):

    lager = [x for x in {_aufgeloest(IMAGES_DIR), IMAGES_DIR} if x]
    zeile = _proc_cmdline(pid)
    for l in lager:
        if l.rstrip("/") + "/" in zeile:
            return True
    return bool(_zell_umgebung(pid))

_ENV_LANE = {}
for _l in _ALLOWED_LANES:
    _ENV_LANE[_LANE_ENV.get(_l, "PN_VMM_VSOCK_%s" % _l.upper())] = _l

def _zell_umgebung(pid):

    umg = _umgebung(pid)

    laufe = [x.rstrip("/") + "/" for x in {CELLS_RUN_DIR, _aufgeloest(CELLS_RUN_DIR)} if x]
    for name, wert in umg.items():
        if name in _ENV_LANE and any(wert.startswith(l) for l in laufe):
            return umg
    return {}

def _box_lane_pfad():
    return os.path.join(CELLS_RUN_DIR, "box-lane.json")

def _box_lane_merken(box_lane):

    try:
        if not isinstance(box_lane, dict) or not box_lane.get("host"):
            return False
        tmp = _box_lane_pfad() + ".neu"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(box_lane, f, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, _box_lane_pfad())
        return True
    except (OSError, TypeError, ValueError):
        return False

def _box_lane_letzte():
    try:
        with io.open(_box_lane_pfad(), encoding="utf-8") as f:
            b = json.load(f)
        return b if isinstance(b, dict) and b.get("host") else {}
    except (OSError, ValueError):
        return {}

def _karte_aus_umgebung(pid):

    umg = _zell_umgebung(pid)
    if not umg:
        return None
    lanes, run_dir = {}, None
    for name, wert in umg.items():
        lane = _ENV_LANE.get(name)
        if lane and wert:
            lanes[lane] = wert
            run_dir = run_dir or os.path.dirname(wert)
    if not run_dir:
        return None
    adopt = {}
    for lane in lanes:
        ap = umg.get(_LANE_ENV.get(lane, "PN_VMM_VSOCK_%s" % lane.upper()) + "_ADOPT")
        if ap:
            adopt[lane] = ap
    try:
        cid = int(umg.get("PN_VMM_VSOCK") or 0) or None
    except (TypeError, ValueError):
        cid = None
    try:
        mem_mb = int(umg.get("PN_VMM_MEM_MB") or 0) or None
    except (TypeError, ValueError):
        mem_mb = None
    return {"cell_id": os.path.basename(run_dir.rstrip("/")), "cid": cid, "mem_mb": mem_mb,
            "run_dir": run_dir, "lanes": lanes, "adopt": adopt,
            "adopt_token": umg.get("PN_VMM_ADOPT_TOKEN") or "",
            "box_lane": _box_lane_letzte(),
            "vmm": {"pid": int(pid), "start": _proc_start(pid)},
            "shim": None, "started": None, "agent": AGENT_VERSION, "aus_umgebung": True}

def _karte_pfad(run_dir):
    return os.path.join(run_dir, KARTE)

def _karte_schreiben(run_dir, karte):

    p = _karte_pfad(run_dir)
    tmp = p + ".neu"
    try:
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(karte, f, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
        return True
    except (OSError, TypeError, ValueError) as e:
        sys.stderr.write("[cells] Zellkarte %s nicht schreibbar: %s\n" % (p, e))
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False

def _karte_lesen(run_dir):
    try:
        with io.open(_karte_pfad(run_dir), encoding="utf-8") as f:
            k = json.load(f)
        return k if isinstance(k, dict) and k.get("cell_id") else None
    except (OSError, ValueError):
        return None

def _adopt_anwaehlen(pfad, token, frist=10.0):

    if not pfad or not token or not os.path.exists(pfad):
        return None
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(frist)
        s.connect(pfad)
        s.sendall((str(token) + "\n").encode("utf-8"))
        zeile = b""
        while not zeile.endswith(b"\n"):
            d = s.recv(1)
            if not d:
                raise OSError("Verbindung endete vor der Bestaetigung")
            zeile += d
            if len(zeile) > 32:
                raise OSError("keine Bestaetigung (%r)" % zeile[:32])
        if not zeile.startswith(b"PNADOPTOK"):
            raise OSError("falsche Bestaetigung (%r)" % zeile[:24])
        s.settimeout(None)
        return s
    except (OSError, socket.timeout) as e:
        sys.stderr.write("[cell-shim] Adoption ueber %s abgelehnt: %s\n" % (pfad, e))
        try:
            s.close()
        except OSError:
            pass
        return None

def _shim_starten(cell_id, run_dir, lanes_cfg, box_lane, adopt_token, modus="annehmen", fds=()):

    cfg = {"cell_id": cell_id, "run_dir": run_dir, "box_lane": box_lane,
           "adopt_token": str(adopt_token or ""), "modus": modus, "lanes": lanes_cfg}
    p = os.path.join(run_dir, SHIM_CFG)
    tmp = p + ".neu"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    log = os.path.join(run_dir, "shim.log")
    try:
        if os.path.getsize(log) > (4 << 20):
            os.replace(log, log + ".alt")
    except OSError:
        pass
    lf = open(log, "ab", 0)
    try:
        return subprocess.Popen([sys.executable, os.path.abspath(__file__), "--lane-shim", p],
                                stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
                                pass_fds=tuple(int(x) for x in fds),
                                start_new_session=True)
    finally:
        try:
            lf.close()
        except OSError:
            pass

def _shim_zustand_lesen(run_dir):
    try:
        with io.open(os.path.join(run_dir, SHIM_ZUSTAND), encoding="utf-8") as f:
            z = json.load(f)
        return z if isinstance(z, dict) else None
    except (OSError, ValueError):
        return None

def _shim_main(cfg_pfad):

    try:
        with io.open(cfg_pfad, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        sys.stderr.write("[shim] Auftrag %s unlesbar: %s\n" % (cfg_pfad, e))
        return 2
    cell_id = str(cfg.get("cell_id") or "?")
    run_dir = str(cfg.get("run_dir") or os.path.dirname(cfg_pfad))
    box_lane = cfg.get("box_lane") or {}
    token = cfg.get("adopt_token") or ""
    modus = str(cfg.get("modus") or "annehmen")
    stop_evt = threading.Event()

    def _schluss(_sig, _frame):
        sys.stderr.write("[shim %s] Halt-Signal — Ende\n" % cell_id[:28])
        sys.stderr.flush()
        os._exit(0)
    for _s in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_s, _schluss)
        except (OSError, ValueError):
            pass

    zustand = {"cell_id": cell_id, "pid": os.getpid(), "modus": modus,
               "seit": time.time(), "lanes": {}}
    z_lock = threading.Lock()

    def _melden(lane, was):
        with z_lock:
            zustand["lanes"][lane] = was
            zustand["stand"] = time.time()
            k = dict(zustand)
        p = os.path.join(run_dir, SHIM_ZUSTAND)
        try:
            with io.open(p + ".neu", "w", encoding="utf-8") as f:
                json.dump(k, f, ensure_ascii=False)
            os.replace(p + ".neu", p)
        except (OSError, ValueError):
            pass

    faeden = []
    for lane, info in sorted((cfg.get("lanes") or {}).items()):
        if not isinstance(info, dict):
            continue
        adopt_pfad = info.get("adopt")
        srv = None
        if modus != "adoptieren":
            try:
                srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, fileno=int(info["fd"]))
            except (KeyError, TypeError, ValueError, OSError) as e:
                sys.stderr.write("[shim %s/%s] Bahn-Socket nicht uebernommen: %s\n"
                                 % (cell_id[:28], lane, e))
                _melden(lane, "kein-socket")
                continue
        elif not (adopt_pfad and os.path.exists(adopt_pfad)):

            _melden(lane, "keine-buchse")
            continue
        t = threading.Thread(
            target=_lane_shim,
            args=(cell_id, lane, srv, box_lane, stop_evt),
            kwargs={"adopt_pfad": adopt_pfad, "adopt_token": token,
                    "zuerst_adoptieren": (modus == "adoptieren"), "melden": _melden},
            name="shim-%s-%s" % (cell_id[:12], lane), daemon=True)
        t.start()
        faeden.append((lane, t))
    if not faeden:
        sys.stderr.write("[shim %s] keine Bahn uebernommen — Ende\n" % cell_id[:28])
        return 1
    sys.stderr.write("[shim %s] %d Bahn(en) im Modus %s: %s\n"
                     % (cell_id[:28], len(faeden), modus, ",".join(l for l, _ in faeden)))
    sys.stderr.flush()

    geduld = time.time() + 180.0
    while True:
        if stop_evt.wait(10.0):
            break
        if not os.path.isdir(run_dir):
            sys.stderr.write("[shim %s] Laufverzeichnis fort — Ende\n" % cell_id[:28])
            break
        k = _karte_lesen(run_dir)
        vm = (k or {}).get("vmm") or {}
        if vm.get("pid"):
            if not _proc_lebt(vm.get("pid"), vm.get("start")):
                sys.stderr.write("[shim %s] Zelle beendet (pid %s) — Ende\n" % (cell_id[:28], vm.get("pid")))
                break
        elif time.time() > geduld:
            sys.stderr.write("[shim %s] keine Zellkarte nach 180 s — Ende\n" % cell_id[:28])
            break
        if not any(t.is_alive() for _l, t in faeden):
            sys.stderr.write("[shim %s] keine Bahn mehr — Ende\n" % cell_id[:28])
            break
    stop_evt.set()
    return 0

def _lane_shim(cell_id, lane, srv, box_lane, stop_evt,
               adopt_pfad=None, adopt_token="", zuerst_adoptieren=False, melden=None):

    def _sagen(was):
        if melden:
            try:
                melden(lane, was)
            except Exception:
                pass
    if zuerst_adoptieren:
        conn = _adopt_anwaehlen(adopt_pfad, adopt_token, frist=15.0)
        if conn is None:
            _sagen("adoption-abgelehnt")
            return
        _sagen("adoptiert")
    else:
        try:
            srv.settimeout(120)
            conn, _ = srv.accept()
        except (OSError, socket.timeout):
            _sagen("kein-gast")
            return
        finally:
            try:
                srv.close()
            except OSError:
                pass
        _sagen("verbunden")
    if stop_evt.is_set():
        try:
            conn.close()
        except OSError:
            pass
        return
    redial = (os.environ.get("PN_NODE_LANE_REDIAL") or "1").strip().lower() not in ("0", "no", "false")
    try:
        conn.settimeout(None)
        fehlversuche = 0
        while not stop_evt.is_set():
            tls = _dial_box(box_lane, cell_id, lane)
            if tls is None:
                if not redial:
                    return
                fehlversuche += 1
                if fehlversuche in (1, 5, 20):
                    sys.stderr.write("[cell-shim] %s/%s: Box nicht erreichbar (Versuch %d) — warte\n"
                                     % (cell_id[:40], lane, fehlversuche))
                if stop_evt.wait(min(30.0, 2.0 ** min(fehlversuche, 5))):
                    return
                continue
            t0 = time.time()
            try:
                if os.environ.get("PN_CELL_SHIM_DEBUG"):
                    _pump_dbg(cell_id, lane, conn, tls)
                    wer = "box"
                else:
                    wer = _pump_zweiweg(conn, tls)
            finally:
                try:
                    tls.close()
                except OSError:
                    pass
            if wer == "gast":

                if not redial:
                    return
                neu = _adopt_anwaehlen(adopt_pfad, adopt_token, frist=10.0)
                if neu is None:
                    _sagen("gast-fort")
                    return
                try:
                    conn.close()
                except OSError:
                    pass
                conn = neu
                conn.settimeout(None)
                fehlversuche = 0
                _sagen("wieder-adoptiert")
                sys.stderr.write("[cell-shim] %s/%s: Gast-Bahn neu adoptiert\n" % (cell_id[:40], lane))
                continue
            if not redial:
                return

            if time.time() - t0 > 5.0:
                fehlversuche = 0
                sys.stderr.write("[cell-shim] %s/%s: Box weg — Gast bleibt offen, waehle neu an\n"
                                 % (cell_id[:40], lane))
            else:
                fehlversuche += 1
            if stop_evt.wait(min(30.0, 2.0 ** min(fehlversuche, 5))):
                return
    finally:
        _sagen("beendet")
        try:
            conn.close()
        except OSError:
            pass

def _cells_start(body):

    cell_id = str(body.get("cell_id") or "")
    if not _ID_RE.match(cell_id):
        return {"ok": False, "error": "bad cell_id"}, 400
    try:
        mem_mb = int(body.get("mem_mb") or 1024)
        vcpus = max(1, int(body.get("vcpus") or 1))
    except (TypeError, ValueError):
        return {"ok": False, "error": "bad mem_mb/vcpus"}, 400

    role_paths = {}
    extras = []
    missing = []
    for img in (body.get("images") or []):
        if not isinstance(img, dict):
            return {"ok": False, "error": "bad image entry"}, 400
        role = str(img.get("role") or "")
        iid = str(img.get("id") or "")
        sha = str(img.get("sha256") or "")
        if not role or not iid or not sha:
            return {"ok": False, "error": "image needs role+id+sha256"}, 400
        p = _cas_path(iid, sha)
        if not os.path.exists(p):
            missing.append({"role": role, "id": iid, "sha256": sha})
            continue
        if role == "extra":
            extras.append(p)
        else:
            role_paths[role] = p
    if missing:
        return {"ok": False, "error": "needs-stage", "missing": missing}, 409
    for r in ("vmm", "kernel", "initrd", "base"):
        if r not in role_paths:
            return {"ok": False, "error": "missing role %s (staged?)" % r}, 409

    with _CELLS_LOCK:
        ex = _CELLS.get(cell_id)
    if ex is not None:
        try:
            alive = ex["proc"].poll() is None
        except Exception:
            alive = False
        if alive:
            return {"ok": True, "cell_id": cell_id, "cid": ex["cid"], "pid": ex["proc"].pid,
                    "lanes": ex["lanes_public"], "existing": True}, 200
        _cell_cleanup(cell_id)
    box_lane = body.get("box_lane") or {}
    if not isinstance(box_lane, dict) or not box_lane.get("host"):
        return {"ok": False, "error": "box_lane.host required"}, 400

    _box_lane_merken(box_lane)
    lanes = [l for l in (body.get("lanes") or ["seat", "llm", "term"]) if l in _ALLOWED_LANES]
    if not lanes:
        return {"ok": False, "error": "no valid lanes"}, 400
    run_dir = os.path.join(CELLS_RUN_DIR, cell_id)

    try:
        os.makedirs(VOL_DIR, exist_ok=True)
        _neu, _alt = _delta_pfad(cell_id), os.path.join(run_dir, "delta.img")
        if (not os.path.isfile(_neu)) and os.path.isfile(_alt) and os.path.getsize(_alt) > 0:
            os.replace(_alt, _neu)
            _log("Zelle %s: Arbeitsstand aus dem alten Laufverzeichnis uebernommen (%d MB)"
                 % (cell_id, os.path.getsize(_neu) // (1 << 20)))
    except OSError as _e:
        _log("Zelle %s: Arbeitsstand liess sich nicht uebernehmen (%s) — der alte bleibt liegen."
             % (cell_id, _e))
    try:
        shutil.rmtree(run_dir, ignore_errors=True)
        os.makedirs(run_dir, exist_ok=True)
        os.chmod(run_dir, 0o700)
    except OSError as e:
        return {"ok": False, "error": "run dir: %s" % e}, 500

    try:

        os.makedirs(VOL_DIR, exist_ok=True)
        delta = _delta_pfad(cell_id)
        if os.path.isfile(delta) and os.path.getsize(delta) > 0:
            print("Zelle %s: vorhandenes Delta wiederverwendet (%d MB) — der Agent macht mit "
                 "seinem Arbeitsstand weiter" % (cell_id, os.path.getsize(delta) // (1 << 20)))
        else:
            _build_delta(delta, body.get("delta_mb"))
    except (subprocess.CalledProcessError, OSError) as e:
        return {"ok": False, "error": "delta build: %s" % e}, 500
    blks = [role_paths["base"], delta]
    ro = ["0"]
    try:
        work_mb = int(body.get("work_mb") or 0)
    except (TypeError, ValueError):
        work_mb = 0
    if work_mb > 0:
        try:
            work = os.path.join(run_dir, "work.img")
            _build_work(work, work_mb)
            blks.append(work)
        except (subprocess.CalledProcessError, OSError) as e:
            return {"ok": False, "error": "work build: %s" % e}, 500
    for p in extras:
        blks.append(p)
        ro.append(str(len(blks) - 1))
    cid = _alloc_node_cid()
    env = dict(os.environ)
    env["PN_VMM_BLK"] = ",".join(blks)
    env["PN_VMM_BLK_RO"] = ",".join(ro)
    env["PN_VMM_VSOCK"] = str(cid)
    env["PN_VMM_MEM_MB"] = str(mem_mb)
    env["PN_VMM_VCPUS"] = str(vcpus)
    env["PN_VMM_ADOPT_TOKEN"] = str(body.get("adopt_token") or "")

    lane_socks = {}
    adopt_pfade = {}
    stop_evt = threading.Event()
    try:
        for lane in lanes:
            sp = os.path.join(run_dir, lane + ".sock")
            try:
                os.unlink(sp)
            except OSError:
                pass
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(sp)
            srv.listen(1)
            lane_socks[lane] = (sp, srv)
            _ev = _LANE_ENV.get(lane, "PN_VMM_VSOCK_%s" % lane.upper())
            env[_ev] = sp

            ap = os.path.join(run_dir, lane + ".adopt")
            try:
                os.unlink(ap)
            except OSError:
                pass
            adopt_pfade[lane] = ap
            env[_ev + "_ADOPT"] = ap
    except OSError as e:
        for _, srv in lane_socks.values():
            try:
                srv.close()
            except OSError:
                pass
        with _CELLS_LOCK:
            _CID_USED.discard(cid)
        return {"ok": False, "error": "lane sock: %s" % e}, 500
    ee = body.get("env_extra")
    if isinstance(ee, dict):
        for k, v in ee.items():
            env[str(k)] = str(v)

    shims = []
    shim_proc = None
    if SHIM_PROZESS:
        try:
            lanes_cfg = {lane: {"fd": srv.fileno(), "sock": sp, "adopt": adopt_pfade.get(lane)}
                         for lane, (sp, srv) in lane_socks.items()}
            shim_proc = _shim_starten(cell_id, run_dir, lanes_cfg, box_lane,
                                      env.get("PN_VMM_ADOPT_TOKEN") or "", modus="annehmen",
                                      fds=[srv.fileno() for _sp, srv in lane_socks.values()])
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            sys.stderr.write("[cells] %s: Shim-Prozess nicht startbar (%s) — Bahnen laufen im "
                             "Agenten (ein Neustart wuerde sie kosten)\n" % (cell_id, e))
            shim_proc = None
    if shim_proc is None:
        for lane, (sp, srv) in lane_socks.items():
            t = threading.Thread(target=_lane_shim, args=(cell_id, lane, srv, box_lane, stop_evt),
                                 name="shim-%s-%s" % (cell_id, lane), daemon=True)
            t.start()
            shims.append(t)
    else:

        for lane, (sp, srv) in list(lane_socks.items()):
            try:
                srv.close()
            except OSError:
                pass
            lane_socks[lane] = (sp, None)
    vmm_err = os.path.join(run_dir, "vmm.err")

    vmm_out = os.path.join(run_dir, "vmm.out")
    try:
        errf = open(vmm_err, "wb")
    except OSError:
        errf = subprocess.DEVNULL
    try:
        outf = open(vmm_out, "wb")
    except OSError:
        outf = subprocess.DEVNULL

    _vmm_start = role_paths["vmm"]
    try:
        _link = os.path.join(os.path.dirname(_vmm_start), "pn-vmm")
        if os.path.realpath(_link) != os.path.realpath(_vmm_start):
            _tmp = _link + ".neu"
            if os.path.lexists(_tmp):
                os.unlink(_tmp)
            os.symlink(os.path.basename(_vmm_start), _tmp)
            os.replace(_tmp, _link)
        _vmm_start = _link
    except OSError:
        pass
    try:
        proc = subprocess.Popen([_vmm_start, role_paths["kernel"], role_paths["initrd"]],
                                stdin=subprocess.DEVNULL, stdout=outf, stderr=errf,
                                env=env, start_new_session=True)
    except (OSError, subprocess.SubprocessError) as e:
        stop_evt.set()
        with _CELLS_LOCK:
            _CID_USED.discard(cid)
        return {"ok": False, "error": "pn-vmm spawn: %s" % e}, 500
    else:

        def _umzug(_pid, _cid):
            try:
                _mv = subprocess.run(
                    ["sudo", "-n", "/usr/local/bin/pn-cgmove", "--sessions", str(_pid)],
                    capture_output=True, text=True, timeout=20)
                _log("Zelle %s: cgroup-Umzug rc=%s %s"
                     % (_cid, _mv.returncode,
                        " ".join((_mv.stdout or _mv.stderr or "").split())[:160]))
            except Exception as _e:
                _log("Zelle %s: cgroup-Umzug nicht moeglich (%s) — sie bleibt in der Gruppe des "
                     "Agenten und zaehlt gegen dessen Speicherdeckel." % (_cid, str(_e)[:80]))
        try:
            threading.Thread(target=_umzug, args=(proc.pid, cell_id), daemon=True).start()
        except Exception:
            pass
    finally:
        for _f in (errf, outf):
            try:
                if _f is not subprocess.DEVNULL:
                    _f.close()
            except Exception:
                pass
    lanes_public = {l: sp for l, (sp, _srv) in lane_socks.items()}
    gestartet = time.time()
    with _CELLS_LOCK:
        _CELLS[cell_id] = {"proc": proc, "cid": cid, "run_dir": run_dir,
                           "lanes": lane_socks, "lanes_public": lanes_public,
                           "shims": shims, "stop": stop_evt, "vmm_err": vmm_err,
                           "vmm_out": vmm_out, "shim_proc": shim_proc,
                           "shim_pid": (shim_proc.pid if shim_proc else None),
                           "shim_start": (_proc_start(shim_proc.pid) if shim_proc else None),
                           "adopt": adopt_pfade, "mem_mb": mem_mb,
                           "started": gestartet}

    _karte_schreiben(run_dir, {
        "cell_id": cell_id, "cid": cid, "mem_mb": mem_mb, "vcpus": vcpus,
        "run_dir": run_dir, "lanes": lanes_public, "adopt": adopt_pfade,
        "adopt_token": env.get("PN_VMM_ADOPT_TOKEN") or "", "box_lane": box_lane,
        "vmm": {"pid": proc.pid, "start": _proc_start(proc.pid)},
        "shim": ({"pid": shim_proc.pid, "start": _proc_start(shim_proc.pid)} if shim_proc else None),
        "vmm_err": vmm_err, "vmm_out": vmm_out, "started": gestartet, "agent": AGENT_VERSION,
    })
    sys.stderr.write("[cells] booted %s pid=%d cid=%d mem=%dMB lanes=%s shim=%s\n"
                     % (cell_id, proc.pid, cid, mem_mb, ",".join(lanes),
                        (shim_proc.pid if shim_proc else "im-agenten")))
    return {"ok": True, "cell_id": cell_id, "cid": cid, "pid": proc.pid, "lanes": lanes_public}, 200

def _cell_liste():

    out = []
    with _CELLS_LOCK:
        items = list(_CELLS.items())
    lebende_pids = set()
    for cell_id, c in items:
        try:
            rc = c["proc"].poll()
        except Exception:
            rc = -1
        pid = None
        try:
            pid = c["proc"].pid
        except Exception:
            pass
        if rc is None and pid:
            lebende_pids.add(pid)
        shim_pid = c.get("shim_pid")
        shim_lebt = bool(shim_pid) and _proc_lebt(shim_pid, (c.get("shim_start")))
        eintrag = {"cell_id": cell_id, "state": "running" if rc is None else "exited",
                   "pid": pid, "cid": c.get("cid"), "started": c.get("started"),
                   "tracked": True,

                   "shim_pid": shim_pid if shim_lebt else None,
                   "lanes_live": shim_lebt or bool(c.get("shims")),
                   "readopted": bool(c.get("readopted"))}
        z = _shim_zustand_lesen(c.get("run_dir") or "")
        if z:

            gemeldet = z.get("lanes") or {}
            eintrag["lanes_state"] = gemeldet
            eintrag["lanes_gemeldet"] = gemeldet
            eintrag["lanes_gemeldet_ts"] = z.get("stand") or z.get("seit")
        out.append(eintrag)

    fremde = 0
    try:
        meine = os.getuid()
        for eintrag in os.listdir("/proc"):
            if not eintrag.isdigit():
                continue
            p = "/proc/" + eintrag
            try:
                if os.stat(p).st_uid != meine:
                    continue
                with open(os.path.join(p, "comm")) as f:
                    if not f.read().strip().startswith("pn-vmm"):
                        continue
            except OSError:
                continue

            if int(eintrag) not in lebende_pids and _unsere_vm(int(eintrag)):
                fremde += 1
    except OSError:
        pass
    return {"ok": True, "cells": out, "untracked_survivors": fremde,
            "run_dir_count": _zellzahl()}, 200

def _cell_status(cell_id):
    with _CELLS_LOCK:
        c = _CELLS.get(cell_id)
    if c is None:
        return {"ok": False, "error": "unknown cell", "state": "gone"}, 404
    try:
        rc = c["proc"].poll()
    except Exception:
        rc = -1
    state = "running" if rc is None else "exited"
    tail = ""
    out_tail = ""
    if rc is not None:
        try:
            tail = _tail_file(c["vmm_err"], 1200).decode("utf-8", "replace")

            try:
                out_tail = _tail_file(c.get("vmm_out") or "", 4000).decode("utf-8", "replace")
            except Exception:
                out_tail = ""
        except Exception:
            tail = ""
    return {"ok": True, "cell_id": cell_id, "state": state, "pid": c["proc"].pid,
            "cid": c["cid"], "rc": rc, "vmm_err_tail": tail,

            "vmm_out_tail": out_tail}, 200

CONSOLE_ARCHIVE = os.path.join(CELLS_RUN_DIR, "_konsolen-archiv")
CONSOLE_KEEP = max(1, int(os.environ.get("PN_NODE_CONSOLE_KEEP") or "40"))

def _archive_console(cell_id, run_dir, rueckgabe=None):

    try:
        os.makedirs(CONSOLE_ARCHIVE, exist_ok=True)
        ziel = os.path.join(CONSOLE_ARCHIVE, "%d-%s" % (int(time.time()), str(cell_id)[-24:]))
        os.makedirs(ziel, exist_ok=True)
        for name in ("vmm.out", "vmm.err", "cell.json"):
            q = os.path.join(run_dir, name)
            if os.path.isfile(q):
                try:
                    shutil.copyfile(q, os.path.join(ziel, name))
                except OSError:
                    pass

        try:
            r = rueckgabe
            if r is None:
                deutung = ("lief beim Abraeumen noch — also von aussen gestoppt "
                           "(Box-Stopp, Sweep oder Neustart des Agenten)")
            elif r == 0:
                deutung = "sauber beendet (Rueckgabewert 0)"
            elif isinstance(r, int) and r < 0:
                deutung = ("durch Signal %d getoetet — bei 9 ist das der OOM-Killer oder ein "
                           "externes kill" % abs(r))
            else:
                deutung = "mit Rueckgabewert %s beendet" % r
            with open(os.path.join(ziel, "ENDE.txt"), "w") as fh:
                fh.write("zelle: %s\nzeitpunkt: %s\nrueckgabewert: %s\ndeutung: %s\n"
                         % (cell_id, time.strftime("%Y-%m-%dT%H:%M:%S"), r, deutung))
        except Exception:
            pass

        eintraege = sorted(d for d in os.listdir(CONSOLE_ARCHIVE)
                           if os.path.isdir(os.path.join(CONSOLE_ARCHIVE, d)))
        for alt in eintraege[:-CONSOLE_KEEP]:
            shutil.rmtree(os.path.join(CONSOLE_ARCHIVE, alt), ignore_errors=True)
    except Exception:
        pass

def _cell_cleanup(cell_id):

    with _CELLS_LOCK:
        c = _CELLS.pop(cell_id, None)
        if c is not None:
            _CID_USED.discard(c["cid"])
    if c is None:
        return False
    try:
        c["stop"].set()
    except Exception:
        pass
    p = c["proc"]
    try:
        if p.poll() is None:
            _kill_group(p.pid, signal.SIGTERM)

            def _hard():
                time.sleep(KILL_GRACE_S)
                if p.poll() is None:
                    _kill_group(p.pid, signal.SIGKILL)
            threading.Thread(target=_hard, daemon=True).start()
    except Exception:
        pass

    sp = c.get("shim_pid")
    if sp:
        try:
            os.kill(int(sp), signal.SIGTERM)
        except OSError:
            pass
    for _lane, srv in (c.get("lanes") or {}).values():
        if srv is None:
            continue
        try:
            srv.close()
        except OSError:
            pass

    try:
        rueckgabe = p.poll()
    except Exception:
        rueckgabe = None
    _archive_console(cell_id, c["run_dir"], rueckgabe)

    try:
        _alt = os.path.join(c["run_dir"], "delta.img")
        _neu = _delta_pfad(cell_id)
        if os.path.isfile(_alt) and not os.path.exists(_neu):
            os.makedirs(os.path.dirname(_neu), exist_ok=True)
            os.replace(_alt, _neu)
            sys.stderr.write("[delta] %s an den bestaendigen Ort gerettet\n" % cell_id)
    except Exception as _e:

        sys.stderr.write("[delta] %s NICHT gerettet (%s) — Laufverzeichnis bleibt stehen\n"
                         % (cell_id, type(_e).__name__))
        return True
    shutil.rmtree(c["run_dir"], ignore_errors=True)
    return True

def _cell_consoles(limit=10):

    aus = []
    try:
        eintraege = sorted((d for d in os.listdir(CONSOLE_ARCHIVE)
                            if os.path.isdir(os.path.join(CONSOLE_ARCHIVE, d))), reverse=True)
    except OSError:
        return {"ok": True, "consoles": []}, 200
    for d in eintraege[:max(1, int(limit or 10))]:
        p = os.path.join(CONSOLE_ARCHIVE, d)
        eintrag = {"id": d}
        for name, feld, n in (("vmm.out", "out_tail", 6000), ("vmm.err", "err_tail", 1500),
                              ("ENDE.txt", "ende", 800)):
            try:
                eintrag[feld] = _tail_file(os.path.join(p, name), n).decode("utf-8", "replace")
            except Exception:
                eintrag[feld] = ""
        aus.append(eintrag)
    return {"ok": True, "consoles": aus}, 200

def _cell_stop(cell_id):
    if _cell_cleanup(cell_id):
        sys.stderr.write("[cells] stopped %s\n" % cell_id)
        return {"ok": True, "cell_id": cell_id}, 200
    return {"ok": False, "error": "unknown cell"}, 404

def _stage_status(image_id, sha):
    p = _cas_path(image_id, sha)
    try:
        size = os.path.getsize(p)
        return {"ok": True, "present": True, "bytes": size}, 200
    except OSError:
        return {"ok": True, "present": False, "bytes": 0}, 200

def _stage_write(handler, image_id, sha, role):

    if not image_id or not sha or not re.match(r"^[A-Za-z0-9._-]{1,80}$", image_id) \
            or not re.match(r"^[0-9a-f]{64}$", sha):
        return {"ok": False, "error": "bad id/sha256"}, 400
    try:
        n = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return {"ok": False, "error": "bad content-length"}, 400
    if n < 0 or n > _STAGE_MAX:
        return {"ok": False, "error": "body too large"}, 413
    try:
        os.makedirs(IMAGES_DIR, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": "cas dir: %s" % e}, 500
    dst = _cas_path(image_id, sha)
    tmp = dst + ".tmp.%d.%s" % (os.getpid(), secrets.token_hex(4))
    h = hashlib.sha256()
    remaining = n
    try:
        with open(tmp, "wb") as f:
            while remaining > 0:
                chunk = handler.rfile.read(min(1 << 20, remaining))
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                remaining -= len(chunk)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return {"ok": False, "error": "write: %s" % e}, 500
    if remaining != 0 or h.hexdigest() != sha:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return {"ok": False, "error": "sha256 mismatch or short body"}, 400
    if role == "vmm":
        try:
            os.chmod(tmp, 0o755)
        except OSError:
            pass
    os.replace(tmp, dst)
    return {"ok": True, "present": True, "bytes": n, "id": image_id, "sha256": sha}, 200

PLEG_RELIST_S = float(os.environ.get("PN_NODE_PLEG_RELIST_S") or "10")
PLEG_RING_N = 512

_PLEG_LOCK = threading.Lock()
_PLEG_RING = collections.deque(maxlen=PLEG_RING_N)
_PLEG_SEQ = [0]
_PLEG_LETZTE = [None]
_PLEG_LEASE = {"cells_n": 0, "fremde_n": 0, "ts": 0.0}

def _pleg_ist():

    zellen = {}
    lebende_pids = set()
    with _CELLS_LOCK:
        items = list(_CELLS.items())
    for cell_id, c in items:
        try:
            rc = c["proc"].poll()
        except Exception:
            rc = -1
        pid = None
        try:
            pid = int(c["proc"].pid)
        except Exception:
            pass
        if rc is None and pid:
            lebende_pids.add(pid)
        zellen[cell_id] = {"state": "running" if rc is None else "exited",
                           "pid": pid, "rc": rc}
    fremde = set()
    try:
        meine = os.getuid()
        for eintrag in os.listdir("/proc"):
            if not eintrag.isdigit():
                continue
            p = "/proc/" + eintrag
            try:
                if os.stat(p).st_uid != meine:
                    continue
                with open(os.path.join(p, "comm")) as f:
                    if not f.read().strip().startswith("pn-vmm"):
                        continue
            except OSError:
                continue
            pid = int(eintrag)
            if pid in lebende_pids:
                continue
            if not _unsere_vm(pid):
                continue
            fremde.add(pid)
    except OSError:
        pass
    return {"zellen": zellen, "fremde": fremde}

def _pleg_diff(alt, neu):

    ev = []
    alt_z = (alt or {}).get("zellen") or {}
    neu_z = (neu or {}).get("zellen") or {}
    for cell_id in sorted(neu_z):
        z = neu_z[cell_id]
        a = alt_z.get(cell_id)
        if a is None:
            if z.get("state") == "running":
                ev.append({"kind": "started", "cell_id": cell_id, "pid": z.get("pid")})
            else:
                ev.append({"kind": "exited", "cell_id": cell_id, "pid": z.get("pid"),
                           "rc": z.get("rc")})
        elif a.get("state") != "running" and z.get("state") == "running":
            ev.append({"kind": "started", "cell_id": cell_id, "pid": z.get("pid")})
        elif a.get("state") == "running" and z.get("state") != "running":
            ev.append({"kind": "exited", "cell_id": cell_id, "pid": z.get("pid"),
                       "rc": z.get("rc")})
    for cell_id in sorted(alt_z):
        if cell_id not in neu_z:
            ev.append({"kind": "vanished", "cell_id": cell_id,
                       "pid": alt_z[cell_id].get("pid"),
                       "state_vorher": alt_z[cell_id].get("state")})
    alt_f = (alt or {}).get("fremde") or set()
    for pid in sorted((neu or {}).get("fremde") or set()):
        if pid not in alt_f:
            ev.append({"kind": "untracked_survivor", "pid": pid})
    return ev

def _pleg_emit(ev):

    with _PLEG_LOCK:
        _PLEG_SEQ[0] += 1
        e = dict(ev)
        e["seq"] = _PLEG_SEQ[0]
        e["ts"] = round(time.time(), 3)
        _PLEG_RING.append(e)
        return e["seq"]

def _pleg_events_since(since):

    with _PLEG_LOCK:
        seq = _PLEG_SEQ[0]
        ring = list(_PLEG_RING)
    aelteste = ring[0]["seq"] if ring else seq + 1
    gone = since > seq or since < aelteste - 1
    events = [] if gone else [e for e in ring if e["seq"] > since]
    return {"ok": True, "seq": seq, "since": since, "gone": gone, "events": events,
            "window": ([aelteste, seq] if ring else None), "relist_s": PLEG_RELIST_S}

def _pleg_relist():

    ist = _pleg_ist()
    with _PLEG_LOCK:
        alt = _PLEG_LETZTE[0]
    evs = _pleg_diff(alt, ist)
    for e in evs:
        _pleg_emit(e)
    with _PLEG_LOCK:
        _PLEG_LETZTE[0] = ist
        _PLEG_LEASE["cells_n"] = sum(1 for z in ist["zellen"].values()
                                     if z.get("state") == "running")
        _PLEG_LEASE["fremde_n"] = len(ist["fremde"])
        _PLEG_LEASE["ts"] = time.time()
    return len(evs)

def _pleg_schleife():
    while True:
        try:
            n = _pleg_relist()
            if n:
                sys.stderr.write("[pleg] %d Ereignis(se), seq=%d\n" % (n, _PLEG_SEQ[0]))
        except Exception:

            pass
        time.sleep(PLEG_RELIST_S)

def _pleg_starten():
    t = threading.Thread(target=_pleg_schleife, name="pleg-relist", daemon=True)
    t.start()
    return t

def _lease():

    try:
        load1 = round(os.getloadavg()[0], 3)
    except OSError:
        load1 = None
    with _PLEG_LOCK:
        seq = _PLEG_SEQ[0]
        cells_n = _PLEG_LEASE["cells_n"]
        fremde_n = _PLEG_LEASE["fremde_n"]
        relist_ts = _PLEG_LEASE["ts"]
    return {"ok": True, "seq": seq, "ts": round(time.time(), 3), "cells_n": cells_n,
            "untracked_n": fremde_n, "load1": load1,
            "relist_age_s": (round(time.time() - relist_ts, 1) if relist_ts else None)}

class Handler(BaseHTTPRequestHandler):
    server_version = "pn-node-agentd/" + AGENT_VERSION
    protocol_version = "HTTP/1.1"

    def _json(self, obj, code=200, ctype="application/json"):
        b = obj if isinstance(obj, bytes) else json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        try:
            self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _raw(self, data, extra=None):

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _deny(self, code):
        self._json({"ok": False, "error":
                    "unauthorized" if code == 401 else "agent token not configured"}, code)

    def _body_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n < 0 or n > MAX_BODY:
            return None
        raw = self.rfile.read(n) if n else b"{}"
        try:
            d = json.loads(raw or b"{}")
        except ValueError:
            return None
        return d if isinstance(d, dict) else None

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _job_route(self, path):

        rest = path[len("/jobs/"):]
        part = rest.split("/", 1)
        job_id = part[0]
        sub = part[1] if len(part) > 1 else ""
        if not _ID_RE.match(job_id or ""):
            return None, None
        return job_id, sub

    def do_GET(self):
        ok, code = _authed(self)
        if not ok:
            return self._deny(code)
        path, _, query = self.path.partition("?")
        q = urllib.parse.parse_qs(query)
        if path == "/health":
            return self._json(_health())

        if path == "/lease":
            return self._json(_lease())
        if path == "/events":
            try:
                since = int((q.get("since") or ["0"])[0])
            except ValueError:
                return self._json({"ok": False, "error": "since muss eine Zahl sein"}, 400)
            return self._json(_pleg_events_since(max(0, since)))

        if path == "/cells/stage":
            obj, code = _stage_status((q.get("id") or [""])[0], (q.get("sha256") or [""])[0])
            return self._json(obj, code)

        if path == "/cells" or path == "/cells/":
            obj, code = _cell_liste()
            return self._json(obj, code)
        if path == "/cells/consoles":
            try:
                _lim = int((q.get("limit") or ["10"])[0])
            except ValueError:
                _lim = 10
            obj, code = _cell_consoles(_lim)
            return self._json(obj, code)

        if path.startswith("/cells/") and path.endswith("/delta"):
            cell_id = path[len("/cells/"):-len("/delta")]
            if not cell_id or "/" in cell_id or not _ID_RE.match(cell_id):
                return self._json({"ok": False, "error": "bad cell id"}, 400)

            for _ort, _p in (("volume", _delta_pfad(cell_id)),
                             ("laufverzeichnis", os.path.join(CELLS_RUN_DIR, cell_id, "delta.img"))):
                try:
                    stt = os.stat(_p)
                except OSError:
                    continue
                if stt.st_size <= 0:
                    continue
                return self._json({"ok": True, "cell_id": cell_id, "present": True,
                                   "ort": _ort, "bytes": stt.st_size,
                                   "belegt": stt.st_blocks * 512,
                                   "mtime": int(stt.st_mtime)}, 200)
            return self._json({"ok": True, "cell_id": cell_id, "present": False}, 200)
        if path.startswith("/cells/"):
            cell_id = path[len("/cells/"):]
            if not cell_id or "/" in cell_id or not _ID_RE.match(cell_id):
                return self._json({"ok": False, "error": "bad cell id"}, 400)
            obj, code = _cell_status(cell_id)
            return self._json(obj, code)
        if path.startswith("/jobs/"):
            job_id, sub = self._job_route(path)
            if job_id is None:
                return self._json({"ok": False, "error": "bad job id"}, 400)

            if sub == "out" and "stream" in q:
                stream = (q.get("stream") or ["stdout"])[0]
                fname = "err" if stream == "stderr" else "out"
                try:
                    offset = int((q.get("offset") or ["0"])[0])
                except ValueError:
                    offset = 0
                fp = os.path.join(_job_dir(job_id), fname)
                meta = _meta_read(job_id)
                if meta is None and not os.path.exists(fp):
                    return self._json({"ok": False, "error": "unknown job"}, 404)
                data, size = _read_from(fp, offset)
                terminal = bool(meta) and meta.get("state") in _TERMINAL
                eof = "1" if (terminal and (offset + len(data)) >= size) else "0"
                return self._raw(data, {"X-Eof": eof})
            if sub in ("out", "err"):
                tail = DEFAULT_TAIL
                if "tail" in q:
                    try:
                        tail = max(1, min(int(q["tail"][0]), MAX_TAIL))
                    except ValueError:
                        pass
                fp = os.path.join(_job_dir(job_id), sub)
                if not os.path.exists(fp):
                    return self._json({"ok": False, "error": "unknown job"}, 404)
                return self._json(_tail_file(fp, tail), 200, "text/plain; charset=utf-8")
            if sub == "":
                meta = _meta_read(job_id)
                if meta is None:
                    return self._json({"ok": False, "error": "unknown job"}, 404)

                return self._json({"ok": True, "job": meta,
                                   "state": _CLIENT_STATE.get(meta.get("state"), "running"),
                                   "rc": meta.get("rc")})
        return self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        ok, code = _authed(self)
        if not ok:
            return self._deny(code)
        path, _, query = self.path.partition("?")
        q = urllib.parse.parse_qs(query)

        if path in ("/exec", "/cells") and not _node_active():
            return self._json({"ok": False, "error": "node draining",
                                "mode": _node_mode(), "heartbeat": _heartbeat_state()}, 503)
        if path == "/exec":
            body = self._body_json()
            if body is None:
                return self._json({"ok": False, "error": "bad json body"}, 400)
            obj, code = _exec_submit(body)
            return self._json(obj, code)

        if path == "/cells":
            body = self._body_json()
            if body is None:
                return self._json({"ok": False, "error": "bad json body"}, 400)
            obj, code = _cells_start(body)
            return self._json(obj, code)
        if path == "/cells/stage":
            obj, code = _stage_write(self, (q.get("id") or [""])[0],
                                     (q.get("sha256") or [""])[0], (q.get("role") or [""])[0])
            return self._json(obj, code)
        if path.startswith("/cells/") and path.endswith("/stop"):
            cell_id = path[len("/cells/"):-len("/stop")]
            if not _ID_RE.match(cell_id or ""):
                return self._json({"ok": False, "error": "bad cell id"}, 400)
            obj, code = _cell_stop(cell_id)
            return self._json(obj, code)
        if path.startswith("/jobs/") and (path.endswith("/kill") or path.endswith("/cancel")):
            job_id, sub = self._job_route(path)
            if job_id is None or sub not in ("kill", "cancel"):
                return self._json({"ok": False, "error": "bad job id"}, 400)
            obj, code = _job_kill(job_id)
            return self._json(obj, code)
        return self._json({"ok": False, "error": "not found"}, 404)

def _reap_waisen():

    if (os.environ.get("PN_NODE_REAP_ORPHANS") or "0").strip() not in ("1", "ja", "yes", "true"):
        return 0
    meine = os.getuid()
    pids = []
    try:
        for eintrag in os.listdir("/proc"):
            if not eintrag.isdigit():
                continue
            p = "/proc/" + eintrag
            try:
                if os.stat(p).st_uid != meine:
                    continue
                with open(os.path.join(p, "comm")) as f:
                    if not f.read().strip().startswith("pn-vmm"):
                        continue
            except OSError:
                continue
            if not _unsere_vm(int(eintrag)):
                continue
            pids.append(int(eintrag))
    except OSError:
        return 0

    gefuehrt = set()
    with _CELLS_LOCK:
        zellen = list(_CELLS.values())
    for c in zellen:
        try:
            if c["proc"].poll() is None:
                gefuehrt.add(int(c["proc"].pid))
        except Exception:
            pass
    if gefuehrt:
        pids = [p for p in pids if p not in gefuehrt]
        _leben(len(gefuehrt), "REAP: %d gefuehrte Zelle(n) bleiben unangetastet" % len(gefuehrt))
    if not pids:
        return 0
    _leben(len(pids), "REAP: %d verwaiste Zell-VM(s) gefunden -> beenden (%s)"
           % (len(pids), ",".join(str(x) for x in pids[:12])))
    for sig in (signal.SIGTERM, signal.SIGKILL):
        uebrig = []
        for pid in pids:
            try:
                os.kill(pid, sig)
                uebrig.append(pid)
            except OSError:
                pass
        if not uebrig:
            break
        time.sleep(6 if sig == signal.SIGTERM else 1)
        pids = [x for x in uebrig if os.path.isdir("/proc/%d" % x)]
        if not pids:
            break
    return len(pids)

class _FremderProzess:

    def __init__(self, pid, start=None):
        self.pid = int(pid)
        self._start = start
        self._rc = None

    def poll(self):
        if _proc_lebt(self.pid, self._start):
            return None
        if self._rc is None:
            self._rc = -1
        return self._rc

    def wait(self, timeout=None):
        ende = time.time() + (timeout if timeout else 0)
        while self.poll() is None:
            if timeout is not None and time.time() >= ende:
                raise subprocess.TimeoutExpired("pn-vmm", timeout)
            time.sleep(0.2)
        return self._rc

    def terminate(self):
        try:
            os.kill(self.pid, signal.SIGTERM)
        except OSError:
            pass

    def kill(self):
        try:
            os.kill(self.pid, signal.SIGKILL)
        except OSError:
            pass

def _wiederaufnahme():

    ergebnis = {"gefuehrt": 0, "bahnen_hielten": 0, "bahnen_gerettet": 0, "ohne_bahnen": 0,
                "tot": 0, "ohne_karte": 0, "aus_umgebung": 0, "unrettbar": 0, "zellen": []}
    if not READOPT_AN:
        ergebnis["aus"] = True
        return ergebnis
    rettungen = []
    gesehen = set()

    def aufnehmen(karte, run_dir):

        cell_id = str(karte.get("cell_id") or os.path.basename(run_dir.rstrip("/")))
        vm = karte.get("vmm") or {}
        shim = karte.get("shim") or {}
        shim_pid = shim.get("pid")
        shim_lebt = (_proc_lebt(shim_pid, shim.get("start"))
                     and "--lane-shim" in _proc_cmdline(shim_pid or 0))
        with _CELLS_LOCK:
            if isinstance(karte.get("cid"), int):
                _CID_USED.add(karte["cid"])
            _CELLS[cell_id] = {
                "proc": _FremderProzess(vm.get("pid"), vm.get("start")),
                "cid": karte.get("cid"), "run_dir": run_dir,
                "lanes": {l: (pf, None) for l, pf in (karte.get("lanes") or {}).items()},
                "lanes_public": karte.get("lanes") or {},
                "shims": [], "stop": threading.Event(),
                "vmm_err": karte.get("vmm_err") or os.path.join(run_dir, "vmm.err"),
                "vmm_out": karte.get("vmm_out") or os.path.join(run_dir, "vmm.out"),
                "shim_proc": None, "shim_pid": (shim_pid if shim_lebt else None),
                "shim_start": (shim.get("start") if shim_lebt else None),
                "adopt": karte.get("adopt") or {}, "mem_mb": karte.get("mem_mb"),
                "started": karte.get("started") or time.time(), "readopted": True}
        ergebnis["gefuehrt"] += 1
        if shim_lebt:
            ergebnis["bahnen_hielten"] += 1
            ergebnis["zellen"].append({"cell_id": cell_id, "zustand": "bahnen-hielten"})
            return

        neu = None
        try:
            lanes_cfg = {l: {"adopt": (karte.get("adopt") or {}).get(l), "sock": pf}
                         for l, pf in (karte.get("lanes") or {}).items()}
            neu = _shim_starten(cell_id, run_dir, lanes_cfg, karte.get("box_lane") or {},
                                karte.get("adopt_token") or "", modus="adoptieren")
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            sys.stderr.write("[cells] %s: Rettungs-Shim nicht startbar (%s)\n" % (cell_id, e))
        if neu is None:
            ergebnis["ohne_bahnen"] += 1
            ergebnis["zellen"].append({"cell_id": cell_id, "zustand": "ohne-bahnen"})
            return
        with _CELLS_LOCK:
            if cell_id in _CELLS:
                _CELLS[cell_id]["shim_proc"] = neu
                _CELLS[cell_id]["shim_pid"] = neu.pid
                _CELLS[cell_id]["shim_start"] = _proc_start(neu.pid)
        karte["shim"] = {"pid": neu.pid, "start": _proc_start(neu.pid)}
        _karte_schreiben(run_dir, karte)
        rettungen.append((cell_id, run_dir))

    try:
        eintraege = sorted(d for d in os.listdir(CELLS_RUN_DIR) if d.startswith("sc-"))
    except OSError:
        eintraege = []
    for d in eintraege:
        run_dir = os.path.join(CELLS_RUN_DIR, d)
        if not os.path.isdir(run_dir):
            continue
        karte = _karte_lesen(run_dir)
        if karte is None:
            continue
        gesehen.add(os.path.realpath(run_dir))
        vm = karte.get("vmm") or {}
        if not _proc_lebt(vm.get("pid"), vm.get("start")):
            ergebnis["tot"] += 1
            _delta_retten(str(karte.get("cell_id") or d), run_dir)
            continue
        aufnehmen(karte, run_dir)

    try:
        meine = os.getuid()
        for eintrag in os.listdir("/proc"):
            if not eintrag.isdigit():
                continue
            pfad = "/proc/" + eintrag
            try:
                if os.stat(pfad).st_uid != meine:
                    continue
                with io.open(os.path.join(pfad, "comm"), encoding="utf-8") as f:
                    if not f.read().strip().startswith("pn-vmm"):
                        continue
            except OSError:
                continue
            if not _unsere_vm(int(eintrag)):
                continue
            karte = _karte_aus_umgebung(int(eintrag))
            if not karte:

                ergebnis["unrettbar"] = ergebnis.get("unrettbar", 0) + 1
                sys.stderr.write("[cells] pid %s: eigene Zelle ohne Zellkarte, Umgebung "
                                 "verschlossen — nicht wieder anschliessbar (sie laeuft weiter "
                                 "und zaehlt als Waise)\n" % eintrag)
                continue
            run_dir = karte.get("run_dir") or ""
            if not run_dir or os.path.realpath(run_dir) in gesehen:
                continue
            gesehen.add(os.path.realpath(run_dir))
            ergebnis["ohne_karte"] += 1
            if not karte.get("box_lane"):

                sys.stderr.write("[cells] %s: keine gemerkte Box-Anschrift — Zelle wird gefuehrt, "
                                 "aber ihre Bahnen bleiben tot\n" % karte.get("cell_id"))
            try:
                os.makedirs(run_dir, exist_ok=True)
                _karte_schreiben(run_dir, karte)
            except OSError:
                pass
            ergebnis["aus_umgebung"] += 1
            aufnehmen(karte, run_dir)
    except OSError:
        pass

    if rettungen:
        gut_worte = ("adoptiert", "verbunden", "wieder-adoptiert")
        frist = time.time() + 25.0
        offen = list(rettungen)
        while offen and time.time() < frist:
            time.sleep(0.5)
            for eintrag in list(offen):
                z = _shim_zustand_lesen(eintrag[1])
                if z and any(w in gut_worte for w in (z.get("lanes") or {}).values()):
                    offen.remove(eintrag)
        for cell_id, run_dir in rettungen:
            bahnen = (_shim_zustand_lesen(run_dir) or {}).get("lanes") or {}
            gut = sorted(l for l, w in bahnen.items() if w in gut_worte)
            if gut:
                ergebnis["bahnen_gerettet"] += 1
                ergebnis["zellen"].append({"cell_id": cell_id, "zustand": "bahnen-gerettet",
                                           "bahnen": gut})
            else:
                ergebnis["ohne_bahnen"] += 1
                ergebnis["zellen"].append({"cell_id": cell_id, "zustand": "ohne-bahnen",
                                           "bahnen_zustand": bahnen})
    return ergebnis

def _delta_retten(cell_id, run_dir):

    try:
        alt = os.path.join(run_dir, "delta.img")
        if not (os.path.isfile(alt) and os.path.getsize(alt) > 0):
            return False
        os.makedirs(VOL_DIR, exist_ok=True)
        neu = _delta_pfad(cell_id)
        if os.path.isfile(neu):
            return False
        os.replace(alt, neu)
        _log("Zelle %s: Arbeitsstand aus dem Laufverzeichnis gerettet (%d MB)"
             % (cell_id, os.path.getsize(neu) // (1 << 20)))
        return True
    except OSError as e:
        _log("Zelle %s: Arbeitsstand nicht zu retten (%s) — er bleibt liegen" % (cell_id, e))
        return False

def _zellzahl():

    try:
        return len([d for d in os.listdir(CELLS_RUN_DIR) if d.startswith("sc-")])
    except OSError:
        return -1

def _leben(zellen, was):

    try:
        p = os.path.join(os.path.dirname(CELLS_RUN_DIR.rstrip("/")), "leben.log")
        with open(p, "a") as fh:
            fh.write("%s pid=%d zellverzeichnisse=%s %s\n"
                     % (time.strftime("%Y-%m-%dT%H:%M:%S"), os.getpid(), zellen, was))
    except Exception:
        pass

def main():
    os.makedirs(JOBS_DIR, exist_ok=True)
    for d in (IMAGES_DIR, CELLS_RUN_DIR):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
    removed = _sweep_jobs()
    try:
        _ab, _ae = _sweep_archive()
        if _ab or _ae:
            sys.stderr.write("[archiv] Start-Aufraeumen: %d Bild(er), %d Eintrag/Eintraege\n"
                             % (_ab, _ae))
    except Exception:
        pass
    _archive_sweeper()
    if not _server_token():

        sys.stderr.write("WARNUNG: keine Token-Datei %s — alle Requests werden 503\n"
                         % TOKEN_FILE)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    sys.stderr.write("pn-node-agentd v%s: port=%d jobs=%s runner=%s sweep=%d\n" % (
        AGENT_VERSION, PORT, JOBS_DIR,
        "systemd-run" if _systemd_run_usable() else "subprocess", removed))

    _leben(_zellzahl(), "START")

    try:
        _w = _wiederaufnahme()
        if _w.get("gefuehrt") or _w.get("tot") or _w.get("ohne_karte") or _w.get("unrettbar"):
            _bericht = ("WIEDERAUFNAHME: %d Zelle(n) weitergefuehrt (%d Bahnen hielten, %d gerettet, "
                        "%d ohne Bahn), %d beendet, %d aus der Umgebung rekonstruiert, "
                        "%d unrettbar (Zelle ohne Karte aus einer aelteren Fassung)"
                        % (_w.get("gefuehrt", 0), _w.get("bahnen_hielten", 0),
                           _w.get("bahnen_gerettet", 0), _w.get("ohne_bahnen", 0),
                           _w.get("tot", 0), _w.get("aus_umgebung", 0), _w.get("unrettbar", 0)))
            sys.stderr.write("[cells] %s\n" % _bericht)
            _leben(_zellzahl(), _bericht)
    except Exception:
        import traceback as _tb
        _leben(_zellzahl(), "WIEDERAUFNAHME gescheitert:\n" + _tb.format_exc())

    try:
        _reap_waisen()
    except Exception:
        pass

    try:
        _pleg_starten()
    except Exception:
        sys.stderr.write("[pleg] Relist-Thread nicht gestartet — /events bleibt leer, "
                         "/lease zeigt relist_age_s=null (Agent laeuft normal weiter)\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        _leben(_zellzahl(), "ENDE sauber (Strg-C)")
    except BaseException:

        import traceback as _tb
        _leben(_zellzahl(), "ENDE durch Ausnahme:\n" + _tb.format_exc())
        raise

if __name__ == "__main__":

    if len(sys.argv) > 2 and sys.argv[1] == "--lane-shim":
        sys.exit(_shim_main(sys.argv[2]))
    main()
