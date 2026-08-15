
import json
import re
import shlex
import subprocess
import threading
import time

_CTX = {}
_LOCK = threading.RLock()
_CACHE = {"ts": 0.0, "data": None}
TTL_S = 20.0

_SQ_FMT = "%i|%j|%T|%M|%P|%D|%C|%R|%V|%L"
_PS_CMD = ("ps -u \"$USER\" -o pid,etime,pcpu,pmem,args --no-headers 2>/dev/null "
           "| grep -v -E ' ps -u | grep -v | bash -lc| head -' | head -60")

def configure(**kw):
    _CTX.update({k: v for k, v in kw.items() if v is not None})

def _run(cmd, ctx, timeout=60):

    cfg = ctx.get("config") or {}
    lane = str(cfg.get("lane") or ("hpc" if (ctx.get("hpc_ssh") or _CTX.get("hpc_ssh")) else "ssh")).lower()
    if lane == "hpc":
        f = ctx.get("hpc_ssh") or _CTX.get("hpc_ssh")
        if not callable(f):
            return None, "", "keine HPC-Lane auf dieser Box (Profil auf lane:'ssh' stellen)"
        uid = (str(cfg.get("principal") or "").strip() or str(ctx.get("principal") or "").strip() or None)
        try:
            r, err = f(cmd, timeout=timeout, uid=uid)
        except TypeError:
            r, err = f(cmd, timeout=timeout)
        except Exception as e:
            return None, "", str(e)[:200]
        if err or not isinstance(r, dict):
            return None, "", str(err or "Lane-Fehler")[:300]
        return int(r.get("rc", 1)), str(r.get("out") or ""), None
    host = str(cfg.get("ssh_host") or "").strip()
    user = str(cfg.get("ssh_user") or "").strip()
    if not host:
        return None, "", "nicht konfiguriert: ssh_host fehlt (oder lane:'hpc' nutzen)"
    tgt = ("%s@%s" % (user, host)) if user else host
    try:
        pr = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", tgt,
                             "bash -lc %s" % shlex.quote(cmd)],
                            capture_output=True, text=True, timeout=timeout + 15)
        out = (pr.stdout or "").strip() or (pr.stderr or "").strip()[-1500:]
        return pr.returncode, out[-8000:], None
    except subprocess.TimeoutExpired:
        return None, "", "Zeitüberschreitung zum Cluster"
    except Exception as e:
        return None, "", str(e)[:200]

def _parse_jobs(out):
    jobs = []
    for ln in (out or "").splitlines():
        p = ln.strip().split("|")
        if len(p) < 10 or not p[0].strip():
            continue
        jobs.append({"id": p[0].strip(), "name": p[1].strip(), "state": p[2].strip(),
                     "time": p[3].strip(), "partition": p[4].strip(), "nodes": p[5].strip(),
                     "cpus": p[6].strip(), "where": p[7].strip(), "submitted": p[8].strip(),
                     "left": p[9].strip()})
    return jobs

def _parse_procs(out):
    procs = []
    for ln in (out or "").splitlines():
        p = ln.strip().split(None, 4)
        if len(p) < 5 or not p[0].isdigit():
            continue
        procs.append({"pid": p[0], "etime": p[1], "cpu": p[2], "mem": p[3], "cmd": p[4][:160]})
    return procs

def _activity(ctx, force=False):
    with _LOCK:
        now = time.time()
        if not force and _CACHE["data"] and now - _CACHE["ts"] < TTL_S:
            return _CACHE["data"]
        cfg = ctx.get("config") or {}
        label = str(cfg.get("cluster_label") or "Slurm-Cluster")
        cmd = ("squeue --me -h -o '%s' 2>/dev/null; echo '=====PS====='; %s" % (_SQ_FMT, _PS_CMD))
        rc, out, err = _run(cmd, ctx, timeout=60)
        if rc is None:
            d = {"ok": False, "error": err or "Cluster nicht erreichbar", "cluster": label, "ts": int(now)}
        else:
            part = out.split("=====PS=====")
            jobs = _parse_jobs(part[0])
            procs = _parse_procs(part[1] if len(part) > 1 else "")
            d = {"ok": True, "cluster": label, "ts": int(now), "jobs": jobs, "procs": procs,
                 "counts": {"slurm": len(jobs), "other": len(procs)}}
        _CACHE.update(ts=now, data=d)
        return d

def _job_detail(ctx, jid):
    if not re.match(r"^\d{1,12}(_\d{1,6})?$", str(jid or "")):
        return {"ok": False, "error": "ungültige Job-ID"}
    cmd = ("scontrol show job %s 2>&1; echo '=====SACCT====='; "
           "sacct -j %s -n -P --format=JobID,State,Elapsed,TotalCPU,MaxRSS,ReqMem,NNodes,NodeList,Start,End 2>/dev/null | head -8" % (jid, jid))
    rc, out, err = _run(cmd, ctx, timeout=45)
    if rc is None:
        return {"ok": False, "error": err or "Cluster nicht erreichbar"}
    part = out.split("=====SACCT=====")
    props = {}
    for m in re.finditer(r"(\w[\w:/\-]*)=(\S+)", part[0] or ""):
        props[m.group(1)] = m.group(2)
    acct = []
    for ln in (part[1] if len(part) > 1 else "").splitlines():
        c = ln.strip().split("|")
        if len(c) >= 10:
            acct.append({"step": c[0], "state": c[1], "elapsed": c[2], "cpu": c[3], "maxrss": c[4],
                         "reqmem": c[5], "nnodes": c[6], "nodes": c[7], "start": c[8], "end": c[9]})
    return {"ok": True, "id": str(jid), "raw": (part[0] or "").strip()[:6000], "props": props, "sacct": acct}

def _proc_detail(ctx, pid):
    if not str(pid or "").isdigit():
        return {"ok": False, "error": "ungültige PID"}
    cmd = "ps -p %s -o pid,ppid,user,stat,etime,pcpu,pmem,args 2>&1" % pid
    rc, out, err = _run(cmd, ctx, timeout=30)
    if rc is None:
        return {"ok": False, "error": err or "Cluster nicht erreichbar"}
    return {"ok": True, "pid": str(pid), "raw": out.strip()[:4000]}

def handle(verb, method, body, query, ctx):
    if verb == "activity":
        return _activity(ctx)
    if verb == "refresh":
        return _activity(ctx, force=True)
    if verb == "job":
        return _job_detail(ctx, (query.get("id") or body.get("id") or ""))
    if verb == "proc":
        return _proc_detail(ctx, (query.get("pid") or body.get("pid") or ""))
    if verb == "status":
        cfg = ctx.get("config") or {}
        return {"ok": True, "lane": cfg.get("lane") or ("hpc" if _CTX.get("hpc_ssh") else "ssh"),
                "cluster": cfg.get("cluster_label") or "Slurm-Cluster", "cached_s": max(0, int(time.time() - _CACHE["ts"]))}
    return {"ok": False, "error": "unbekanntes Verb: %s" % verb}
