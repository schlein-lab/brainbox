
from __future__ import annotations
import os, shlex, subprocess

BATCH_SLICE = "pn-batch.slice"

def unit_name(job_id: int) -> str:
    return f"pn-job-{job_id}.service"

def dispatch(job_id: int, argv: list[str], cwd: str, env: dict, props: list[str],
             out_path: str, err_path: str, rc_path: str, track: str | None = None):

    unit = unit_name(job_id)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    q = shlex.quote

    inner = '"$@" >%s 2>%s; echo $? > %s' % (q(out_path), q(err_path), q(rc_path))
    cmd = [
        "systemd-run", "--user", "--wait", "--collect", "--quiet",
        f"--slice={BATCH_SLICE}", f"--unit={unit}",
        "-p", f"WorkingDirectory={cwd}",
    ]
    for k, v in (env or {}).items():
        cmd += ["--setenv", f"{k}={v}"]
    for p in props:
        cmd += ["-p", p]
    cmd += ["--", "/bin/bash", "-c", inner, "pn-job"] + list(argv)
    try:
        if os.path.exists(rc_path):
            os.unlink(rc_path)

        child_env = {k: v for k, v in os.environ.items()
                     if k not in ("NOTIFY_SOCKET", "WATCHDOG_PID", "WATCHDOG_USEC",
                                  "LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES")}
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True, env=child_env)
    except Exception as e:
        try:
            with open(err_path, "a") as lf:
                lf.write(f"=== pn dispatch FAILED: {e} ===\n")
        except Exception:
            pass
        return None

def read_rc(rc_path: str):
    try:
        with open(rc_path) as f:
            return int(f.read().strip())
    except Exception:
        return None

def unit_status(job_id: int) -> dict:
    unit = unit_name(job_id)
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", unit,
             "-p", "ActiveState", "-p", "SubState", "-p", "Result",
             "-p", "ExecMainStatus", "-p", "LoadState"],
            capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return {"active": False, "result": "unknown", "code": None, "loaded": False}
    d = {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        d[k] = v
    loaded = d.get("LoadState") == "loaded"
    active = d.get("ActiveState") in ("active", "activating", "deactivating")
    try:
        code = int(d.get("ExecMainStatus", ""))
    except Exception:
        code = None
    return {"active": active, "result": d.get("Result", ""), "code": code,
            "loaded": loaded, "substate": d.get("SubState", "")}

def stop_unit(job_id: int):
    try:
        subprocess.run(["systemctl", "--user", "stop", unit_name(job_id)],
                       capture_output=True, text=True, timeout=10)
    except Exception:
        pass

def job_cgroup_stats(job_id: int) -> dict:

    from . import meters

    out = {"mem_peak": None, "cpu_s": None, "oom_kill": None}
    cg = meters.cgroup_path(unit_name(job_id))
    if not cg:
        return out
    try:
        with open(os.path.join(cg, "memory.peak")) as f:
            v = f.read().strip()
        if v and v != "max":
            out["mem_peak"] = int(v) // (1024 * 1024)
    except Exception:
        pass

    try:
        with open(os.path.join(cg, "memory.events")) as f:
            for line in f:
                if line.startswith("oom_kill "):
                    out["oom_kill"] = int(line.split()[1])
                    break
    except Exception:
        pass
    try:
        with open(os.path.join(cg, "cpu.stat")) as f:
            for line in f:
                if line.startswith("usage_usec"):
                    out["cpu_s"] = round(int(line.split()[1]) / 1e6, 3)
                    break
    except Exception:
        pass
    return out

def live_units() -> set[str]:

    try:
        out = subprocess.run(
            ["systemctl", "--user", "list-units", "--all", "--type=service",
             "--no-legend", "--plain", "pn-job-*.service"],
            capture_output=True, text=True, timeout=8).stdout
    except Exception:
        return set()
    s = set()
    for line in out.splitlines():
        tok = line.split()
        if tok and tok[0].startswith("pn-job-"):
            s.add(tok[0])
    return s
