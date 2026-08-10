
from __future__ import annotations
import os, json, time, shutil, stat, shlex, subprocess, sqlite3, pwd

from pnlib import STATE_DIR
from pnlib import profile as pnprofile

CELLS_ROOT = os.environ.get("PN_CELLS_DIR", os.path.join(STATE_DIR, "cells"))
REGISTRY_DB = os.environ.get("PN_CELLS_DB", os.path.join(STATE_DIR, "cells.db"))

SUBDIRS = ("secrets", "home", "work")

BATCH_SLICE = "pn-batch.slice"

VALID_TIERS = ("cell-vm", "cell-ns", "cell-sandbox")
VALID_STATES = ("provisioned", "suspended", "torndown")

_SCRUB = ("NOTIFY_SOCKET", "WATCHDOG_PID", "WATCHDOG_USEC",
          "LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES")

_REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS cells (
  principal     TEXT PRIMARY KEY,         -- the owner principal (one cell per principal)
  tier          TEXT NOT NULL,            -- cell-vm | cell-ns | cell-sandbox
  requested_tier TEXT NOT NULL,           -- what was asked for (before any degrade)
  degraded_from TEXT,                     -- non-NULL if we degraded loudly (e.g. cell-ns->cell-sandbox)
  state         TEXT NOT NULL,            -- provisioned | suspended | torndown
  cgroup        TEXT NOT NULL,            -- the child slice unit for this cell
  secrets_path  TEXT NOT NULL,            -- the cell's OWN sealed secrets dir
  home_path     TEXT NOT NULL,
  uid           INTEGER,                  -- POSIX uid the cell runs as (own uid => own-perm isolation)
  mem_max       INTEGER,                  -- MiB hard cap for the cell slice
  cpu_quota_pct INTEGER,
  pids_max      INTEGER,
  created_at    REAL NOT NULL,
  updated_at    REAL NOT NULL,
  note          TEXT
);
"""

def registry_connect(path: str | None = None) -> sqlite3.Connection:
    p = path or REGISTRY_DB
    os.makedirs(os.path.dirname(p), exist_ok=True)
    cx = sqlite3.connect(p)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA journal_mode=WAL")
    cx.executescript(_REGISTRY_SCHEMA)
    return cx

def _bin(name: str) -> bool:
    return bool(shutil.which(name))

def _arch() -> str:

    try:
        return os.uname().machine
    except Exception:
        return "unknown"

def _is_arm(machine: str | None = None) -> bool:
    m = (machine if machine is not None else _arch()) or ""
    return m.startswith(("aarch64", "arm"))

def _nested_virt() -> bool:

    if not os.path.exists("/dev/kvm"):
        return False
    if _is_arm():

        return os.access("/dev/kvm", os.R_OK | os.W_OK)
    for p in ("/sys/module/kvm_intel/parameters/nested",
              "/sys/module/kvm_amd/parameters/nested"):
        try:
            with open(p) as f:
                if f.read().strip() in ("Y", "1"):
                    return True
        except OSError:
            continue
    return False

def _kvm_usable() -> bool:

    if not os.path.exists("/dev/kvm"):
        return False
    return os.access("/dev/kvm", os.R_OK | os.W_OK)

def _pn_vmm_bin() -> str | None:

    cands = []
    env = os.environ.get("PN_VMM_BIN")
    if env:
        cands.append(env)
    cands.append(os.path.expanduser("~/pn-vmm-build/target/release/pn-vmm"))
    try:

        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cands.append(os.path.join(repo, "os", "pn-vmm", "target", "release", "pn-vmm"))
    except Exception:
        pass
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return shutil.which("pn-vmm") or None

def _hypervisor() -> str | None:

    cands = ["firecracker", "cloud-hypervisor"]
    cands.append("qemu-system-aarch64" if _is_arm() else "qemu-system-x86_64")
    cands.append("qemu-kvm")
    for h in cands:
        if _bin(h):
            return h
    return None

def _apparmor_userns_restricted() -> bool:

    try:
        with open("/proc/sys/kernel/apparmor_restrict_unprivileged_userns") as f:
            return f.read().strip() == "1"
    except OSError:
        return False

def _userns_clone_enabled() -> bool:
    try:
        with open("/proc/sys/kernel/unprivileged_userns_clone") as f:
            return f.read().strip() == "1"
    except OSError:

        return True

def _has_subid(user: str | None = None) -> bool:
    user = user or pwd.getpwuid(os.getuid()).pw_name
    try:
        with open("/etc/subuid") as f:
            return any(line.startswith(user + ":") for line in f)
    except OSError:
        return False

def host_capabilities() -> dict:

    bwrap = _bin("bwrap")
    newuidmap = _bin("newuidmap")
    aa_restrict = _apparmor_userns_restricted()
    userns_clone = _userns_clone_enabled()

    cell_ns_rootless = bool(bwrap and userns_clone and not aa_restrict)
    arch = _arch()
    nested = _nested_virt()
    hyper = _hypervisor()
    kvm_usable = _kvm_usable()
    pn_vmm = _pn_vmm_bin()

    if _is_arm(arch):
        cell_vm = bool(kvm_usable and pn_vmm)
    else:
        cell_vm = bool(nested and kvm_usable and hyper)
    if cell_vm:
        tier_color = "green"
    elif cell_ns_rootless:
        tier_color = "amber"
    else:
        tier_color = "red"
    return {
        "arch": arch,
        "bwrap": bwrap, "newuidmap": newuidmap,
        "userns_clone": userns_clone,
        "apparmor_userns_restricted": aa_restrict,
        "subid_mapped": _has_subid(),
        "cell_ns_rootless": cell_ns_rootless,
        "nested_virt": nested, "kvm_usable": kvm_usable, "hypervisor": hyper,
        "pn_vmm": pn_vmm,
        "cell_vm_available": cell_vm,

        "cell_sandbox_available": True,
        "tier_color": tier_color,
    }

class EnrollRefused(Exception):
    pass

def choose_tier(requested: str, caps: dict | None = None, *, high_blast: bool = False
                ) -> tuple[str, str | None]:

    caps = caps or host_capabilities()
    requested = (requested or "cell-ns").lower()
    if requested not in VALID_TIERS:
        raise ValueError(f"unknown tier {requested!r}; expected {VALID_TIERS}")

    if requested == "cell-vm":
        if caps["cell_vm_available"]:
            return "cell-vm", None

        if high_blast:
            raise EnrollRefused(
                "CELL-VM (private kernel) requested for a high-blast-radius role but the "
                f"host cannot provide nested-virt+hypervisor (nested_virt={caps['nested_virt']}, "
                f"kvm_usable={caps['kvm_usable']}, hypervisor={caps['hypervisor']}). "
                "Refusing to enroll into a shared kernel. Install a hypervisor "
                "(firecracker/cloud-hypervisor) and add the runner to the kvm group, or "
                "enroll this role on a GREEN host.")

        ns_tier, deg = choose_tier("cell-ns", caps)
        return ns_tier, "cell-vm"

    if requested == "cell-ns":
        if caps["cell_ns_rootless"]:
            return "cell-ns", None

        return "cell-sandbox", "cell-ns"

    return "cell-sandbox", None

def cell_secrets_dir(principal: str) -> str:
    return os.path.join(CELLS_ROOT, principal, "secrets")

def with_cell_secrets_env(principal: str, env: dict | None = None) -> dict:

    e = dict(env or {})
    e["PN_SECRETS_DIR"] = cell_secrets_dir(principal)
    return e

def seal_cell_cred(principal: str, value: str, kind: str) -> dict:

    import importlib
    saved = os.environ.get("PN_SECRETS_DIR")
    os.environ["PN_SECRETS_DIR"] = cell_secrets_dir(principal)
    try:
        from pnlib import secrets as pnsecrets
        importlib.reload(pnsecrets)
        return pnsecrets.write_cred(value, kind)
    finally:
        if saved is None:
            os.environ.pop("PN_SECRETS_DIR", None)
        else:
            os.environ["PN_SECRETS_DIR"] = saved

def _now() -> float:
    return time.time()

def _mkdir_0700(path: str) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)

def provision(principal: str, requested_tier: str = "cell-ns", *,
              high_blast: bool = False, mem_max: int = 256, cpu_quota_pct: int = 50,
              pids_max: int = 128, uid: int | None = None,
              cx: sqlite3.Connection | None = None, caps: dict | None = None,
              note: str = "") -> dict:

    if not principal or "/" in principal or principal.startswith("."):
        raise ValueError(f"bad principal name {principal!r}")
    caps = caps or host_capabilities()
    tier, degraded_from = choose_tier(requested_tier, caps, high_blast=high_blast)

    cell_dir = os.path.join(CELLS_ROOT, principal)
    _mkdir_0700(CELLS_ROOT)
    _mkdir_0700(cell_dir)
    for sd in SUBDIRS:
        _mkdir_0700(os.path.join(cell_dir, sd))

    secrets_path = os.path.join(cell_dir, "secrets")
    home_path = os.path.join(cell_dir, "home")
    cgroup = cell_slice_unit(principal)

    own = cx or registry_connect()
    try:
        own.execute(
            """INSERT INTO cells(principal,tier,requested_tier,degraded_from,state,cgroup,
                                 secrets_path,home_path,uid,mem_max,cpu_quota_pct,pids_max,
                                 created_at,updated_at,note)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(principal) DO UPDATE SET
                 tier=excluded.tier, requested_tier=excluded.requested_tier,
                 degraded_from=excluded.degraded_from, state=excluded.state,
                 cgroup=excluded.cgroup, secrets_path=excluded.secrets_path,
                 home_path=excluded.home_path, uid=excluded.uid, mem_max=excluded.mem_max,
                 cpu_quota_pct=excluded.cpu_quota_pct, pids_max=excluded.pids_max,
                 updated_at=excluded.updated_at, note=excluded.note""",
            (principal, tier, requested_tier, degraded_from, "provisioned", cgroup,
             secrets_path, home_path, uid, mem_max, cpu_quota_pct, pids_max,
             _now(), _now(), note))
        own.commit()
    finally:
        if cx is None:
            own.close()

    return {"principal": principal, "tier": tier, "requested_tier": requested_tier,
            "degraded_from": degraded_from, "state": "provisioned", "cgroup": cgroup,
            "secrets_path": secrets_path, "home_path": home_path, "uid": uid,
            "mem_max": mem_max, "cpu_quota_pct": cpu_quota_pct, "pids_max": pids_max,
            "tier_color": caps["tier_color"]}

def get(principal: str, cx: sqlite3.Connection | None = None) -> dict | None:
    own = cx or registry_connect()
    try:
        r = own.execute("SELECT * FROM cells WHERE principal=?", (principal,)).fetchone()
        return dict(r) if r else None
    finally:
        if cx is None:
            own.close()

def list_cells(cx: sqlite3.Connection | None = None) -> list[dict]:
    own = cx or registry_connect()
    try:
        return [dict(r) for r in own.execute(
            "SELECT * FROM cells ORDER BY created_at").fetchall()]
    finally:
        if cx is None:
            own.close()

def _set_state(principal: str, state: str, cx: sqlite3.Connection | None = None) -> None:
    if state not in VALID_STATES:
        raise ValueError(state)
    own = cx or registry_connect()
    try:
        own.execute("UPDATE cells SET state=?, updated_at=? WHERE principal=?",
                    (state, _now(), principal))
        own.commit()
    finally:
        if cx is None:
            own.close()

def suspend(principal: str, cx: sqlite3.Connection | None = None) -> dict:

    stop_cell_slice(principal)
    _set_state(principal, "suspended", cx)
    return {"principal": principal, "state": "suspended"}

def resume(principal: str, cx: sqlite3.Connection | None = None) -> dict:

    _set_state(principal, "provisioned", cx)
    return {"principal": principal, "state": "provisioned"}

def teardown(principal: str, *, wipe: bool = True,
             cx: sqlite3.Connection | None = None) -> dict:

    stop_cell_slice(principal)
    cell_dir = os.path.join(CELLS_ROOT, principal)
    if wipe and os.path.isdir(cell_dir):
        shutil.rmtree(cell_dir, ignore_errors=True)
    _set_state(principal, "torndown", cx)
    return {"principal": principal, "state": "torndown", "wiped": bool(wipe)}

def cell_slice_unit(principal: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in principal)
    return f"pn-cell-{safe}.slice"

def _cell_props(c: dict) -> list[str]:

    props = [
        f"MemoryMax={int(c['mem_max'])}M",
        f"MemoryHigh={int(c['mem_max'] * 0.9)}M",

        "MemorySwapMax=0",
        "OOMScoreAdjust=500",
        f"TasksMax={int(c['pids_max'])}",
        "NoNewPrivileges=yes",
        "RestrictSUIDSGID=yes",
        "ProtectSystem=strict",
        "ProtectKernelTunables=yes",
        "PrivateTmp=yes",
        f"ReadWritePaths={os.path.join(CELLS_ROOT, c['principal'])}",
    ]
    if c.get("cpu_quota_pct"):
        props.append(f"CPUQuota={int(c['cpu_quota_pct'])}%")
    return props

def stop_cell_slice(principal: str) -> None:
    for unit in (cell_slice_unit(principal),):
        try:
            subprocess.run(["systemctl", "--user", "stop", unit],
                           capture_output=True, text=True, timeout=10)
        except Exception:
            pass

def _bwrap_argv(c: dict, inner_argv: list[str]) -> list[str]:

    cell_dir = os.path.join(CELLS_ROOT, c["principal"])
    argv = [
        "bwrap",
        "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
        "--uid", "0", "--gid", "0",
        "--die-with-parent", "--new-session",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind-try", "/bin", "/bin",
        "--ro-bind-try", "/lib", "/lib",
        "--ro-bind-try", "/lib64", "/lib64",
        "--ro-bind-try", "/etc", "/etc",
        "--proc", "/proc", "--dev", "/dev",
        "--tmpfs", "/tmp",

        "--bind", os.path.join(cell_dir, "home"), "/root",
        "--bind", os.path.join(cell_dir, "secrets"), "/root/.pn-secrets",
        "--setenv", "HOME", "/root",
        "--setenv", "PN_SECRETS_DIR", "/root/.pn-secrets",
        "--setenv", "PN_CELL", c["principal"],
        "--chdir", "/root",
    ]
    return argv + ["--"] + inner_argv

def run_in_cell(principal: str, argv: list[str], *, cx: sqlite3.Connection | None = None,
                timeout: int | None = None, capture: bool = True,
                env: dict | None = None) -> dict:

    own = cx or registry_connect()
    try:
        c = get(principal, own)
        if not c:
            raise KeyError(f"no cell for principal {principal!r}; provision first")
        if c["state"] == "torndown":
            raise RuntimeError(f"cell for {principal!r} is torn down")
        if c["state"] == "suspended":
            resume(principal, own)
            c = get(principal, own)

        cell_dir = os.path.join(CELLS_ROOT, principal)
        run_env = with_cell_secrets_env(principal, env)
        run_env.setdefault("HOME", os.path.join(cell_dir, "home"))
        run_env.setdefault("PN_CELL", principal)

        tier = c["tier"]
        if tier == "cell-vm":

            raise RuntimeError(
                "CELL-VM run path needs a hypervisor (firecracker/cloud-hypervisor); not "
                "installed on this host. Provisioning would have refused a high-blast role.")

        if tier == "cell-ns":

            cmd = _bwrap_argv(c, list(argv))
            full_env = {**os.environ, **run_env}
            scrub = {k: v for k, v in full_env.items() if k not in _SCRUB}
            proc = subprocess.run(cmd, capture_output=capture, text=True,
                                  timeout=timeout, env=scrub)
            return {"principal": principal, "tier": tier, "rc": proc.returncode,
                    "stdout": (proc.stdout if capture else None),
                    "stderr": (proc.stderr if capture else None), "cmd": cmd}

        work = os.path.join(cell_dir, "work")
        os.makedirs(work, exist_ok=True)
        stamp = int(time.time() * 1000)
        out_p = os.path.join(work, f".run-{stamp}.out")
        err_p = os.path.join(work, f".run-{stamp}.err")
        rc_p = os.path.join(work, f".run-{stamp}.rc")
        unit = f"pn-cell-run-{cell_slice_unit(principal)[:-6]}-{stamp}.service"
        props = _cell_props(c)
        q = shlex.quote
        inner = '"$@" >%s 2>%s; echo $? > %s' % (q(out_p), q(err_p), q(rc_p))
        cmd = ["systemd-run", "--user", "--wait", "--collect", "--quiet",
               f"--slice={BATCH_SLICE}", f"--unit={unit}",
               "-p", f"WorkingDirectory={work}"]
        for k, v in run_env.items():
            cmd += ["--setenv", f"{k}={v}"]
        for p in props:
            cmd += ["-p", p]
        cmd += ["--", "/bin/bash", "-c", inner, "pn-cell"] + list(argv)
        scrub = {k: v for k, v in os.environ.items() if k not in _SCRUB}
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=scrub)
        finally:
            pass
        rc = None
        try:
            with open(rc_p) as f:
                rc = int(f.read().strip())
        except Exception:
            rc = 1
        out = err = None
        if capture:
            try:
                with open(out_p) as f:
                    out = f.read()
            except Exception:
                out = ""
            try:
                with open(err_p) as f:
                    err = f.read()
            except Exception:
                err = ""
        for p in (out_p, err_p, rc_p):
            try:
                os.unlink(p)
            except OSError:
                pass
        return {"principal": principal, "tier": tier, "rc": rc,
                "stdout": out, "stderr": err, "cmd": cmd}
    finally:
        if cx is None:
            own.close()
