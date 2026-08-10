
from __future__ import annotations
import os, json, time, shutil, subprocess, hashlib, socket, getpass

from . import RECORD_DIR, WORK_DIR, INDEX_DIR, TRASH_DIR, CAS_DIR, VERSION

REQUIRED_DIRS = ("inputs", "work", "artifacts", "log")
REQUIRED_FILES = ("MANIFEST.json", "README.md", "provenance.json")

WORK_TTL_S = 24 * 3600
WORK_TTL_PRESSURE_S = 3600
DATA_WARN_PCT = 15.0
DATA_HARD_PCT = 8.0
DATA_FLOOR_PCT = 3.0

def workspace_path(job_id: int) -> str:
    return os.path.join(WORK_DIR, str(job_id))

def index_path(job_id: int) -> str:
    return os.path.join(INDEX_DIR, str(job_id))

def mkspace(job_id: int) -> str:

    ws = workspace_path(job_id)

    os.makedirs(ws, exist_ok=True)
    os.chmod(ws, 0o700)
    for d in REQUIRED_DIRS:
        p = os.path.join(ws, d)
        os.makedirs(p, exist_ok=True)
        try:
            os.chmod(p, 0o700)
        except OSError:
            pass
    return ws

def steer_dir(ws: str) -> str:

    return os.path.join(ws, "work", "steer")

def write_steer(ws: str, seq: int, payload) -> str:

    d = steer_dir(ws)
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, f"{int(seq)}.json")
    body = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
    tmp = dest + ".tmp"
    with open(tmp, "w") as f:
        f.write(body)
    os.replace(tmp, dest)
    return dest

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def hash_tree(root: str) -> dict:

    out = {}
    if not os.path.isdir(root):
        return out
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root)
            try:
                out[rel] = {"sha256": _sha256_file(p), "bytes": os.path.getsize(p)}
            except OSError:
                continue
    return dict(sorted(out.items()))

CAS_LARGE_BYTES = 256 * 1024

def cas_put(path: str) -> str:

    digest = _sha256_file(path)
    shard = os.path.join(CAS_DIR, digest[:2], digest[2:4])
    os.makedirs(shard, exist_ok=True)
    dest = os.path.join(shard, digest)
    if not os.path.exists(dest):
        tmp = dest + ".tmp"
        shutil.copyfile(path, tmp)
        os.replace(tmp, dest)
    return "cas://" + digest

def build_provenance(job: dict, *, argv, exit_code, started_at, finished_at,
                     model=None, extra=None) -> dict:

    ws = job.get("workspace_path") or workspace_path(job["id"])
    inputs_h = hash_tree(os.path.join(ws, "inputs"))
    artifacts_h = hash_tree(os.path.join(ws, "artifacts"))
    prov = {
        "schema": "pn-provenance/1",
        "pn_version": VERSION,
        "job_id": job["id"],

        "who": {
            "principal": job.get("submitter_principal") or job.get("principal"),
            "via_method": job.get("via_method"),
            "via_device": job.get("via_device"),
            "os_user": getpass.getuser(),
        },

        "what": {
            "task_type": job.get("task_type"),
            "source": job.get("source"),
            "client_tag": job.get("client_tag"),
        },

        "when": {
            "submitted_at": job.get("submitted_at"),
            "started_at": started_at,
            "finished_at": finished_at,
        },

        "where": {
            "host": socket.gethostname(),
            "workspace": ws,
            "record_dir": RECORD_DIR,
        },

        "how": {
            "argv": argv,
            "model": model,
            "parent_job": job.get("parent_job"),
            "group_id": job.get("group_id"),
            "deps": json.loads(job["deps"]) if job.get("deps") else None,
            "isolation_tier": job.get("isolation_tier"),
        },

        "hashes": {"inputs": inputs_h, "artifacts": artifacts_h},

        "result": {
            "exit_code": exit_code,
            "state": "done" if exit_code == 0 else "failed",
        },
    }
    if extra:
        prov["extra"] = extra
    return prov

def default_rationale(job: dict, prov: dict) -> str:

    who = prov["who"]["principal"] or "unknown"
    what = job.get("task_type") or "(raw command)"
    return (f"# Record for job {job['id']}\n\n"
            f"- **task_type:** {what}\n"
            f"- **principal:** {who}\n"
            f"- **submitted:** {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(job.get('submitted_at') or time.time()))}\n\n"
            "## Rationale (auto-generated)\n\n"
            "No agent-authored rationale was provided for this job; this record was completed "
            "automatically. Hard provenance is in `provenance.json`; the manifest is `MANIFEST.json`.\n")

def _git(ws: str, *args, check=True) -> subprocess.CompletedProcess:
    env = dict(os.environ)

    env.setdefault("GIT_AUTHOR_NAME", "portioneer")
    env.setdefault("GIT_AUTHOR_EMAIL", "pnd@portioneer.local")
    env.setdefault("GIT_COMMITTER_NAME", "portioneer")
    env.setdefault("GIT_COMMITTER_EMAIL", "pnd@portioneer.local")
    return subprocess.run(["git", "-C", ws, *args], capture_output=True, text=True,
                          env=env, check=check, timeout=60)

def git_commit_record(ws: str, manifest: dict) -> str:

    if not os.path.isdir(os.path.join(ws, ".git")):
        _git(ws, "init", "-q")
    _git(ws, "add", "-A")
    msg = json.dumps(manifest, separators=(",", ":"))

    _git(ws, "commit", "-q", "--allow-empty", "-m", msg)
    oid = _git(ws, "rev-parse", "HEAD").stdout.strip()
    return oid

def offload_large_artifacts(ws: str) -> dict:

    moved = {}
    adir = os.path.join(ws, "artifacts")
    if not os.path.isdir(adir):
        return moved
    for dirpath, _d, files in os.walk(adir):
        for name in sorted(files):
            if name.endswith(".cas"):
                continue
            p = os.path.join(dirpath, name)
            try:
                if os.path.getsize(p) < CAS_LARGE_BYTES:
                    continue
            except OSError:
                continue
            locator = cas_put(p)
            rel = os.path.relpath(p, ws)
            with open(p + ".cas", "w") as f:
                json.dump({"cas": locator, "bytes": os.path.getsize(p), "name": name}, f)
            os.unlink(p)
            moved[rel] = locator
    return moved

README_EXCERPT_BYTES = 1200
MAX_PREVIEW_FILES = 25

def review_preview(job: dict, *, max_files: int = MAX_PREVIEW_FILES) -> dict:

    ws = job.get("workspace_path") or workspace_path(job["id"])
    arts = hash_tree(os.path.join(ws, "artifacts"))
    artifacts = [{"path": rel, "bytes": meta["bytes"], "sha256": meta["sha256"][:16]}
                 for rel, meta in list(arts.items())[:max_files]]
    readme = ""
    try:
        with open(os.path.join(ws, "README.md")) as f:
            readme = f.read(README_EXCERPT_BYTES)
    except OSError:

        for cand in (os.path.join(ws, "work", "RATIONALE.md"),):
            try:
                with open(cand) as f:
                    readme = f.read(README_EXCERPT_BYTES)
                break
            except OSError:
                continue
    diff = None
    try:
        if os.path.isdir(os.path.join(ws, ".git")):
            r = _git(ws, "show", "--stat", "--oneline", "HEAD", check=False)
            if r.returncode == 0:
                diff = r.stdout[:4000]
    except Exception:
        diff = None
    return {"artifacts": artifacts, "artifact_count": len(arts),
            "readme_excerpt": readme, "diff": diff}

def write_record(job: dict, *, argv, exit_code, started_at, finished_at,
                 rationale: str | None = None, model=None, extra=None) -> dict:

    ws = job.get("workspace_path") or workspace_path(job["id"])

    for d in REQUIRED_DIRS:
        os.makedirs(os.path.join(ws, d), exist_ok=True)

    prov = build_provenance(job, argv=argv, exit_code=exit_code, started_at=started_at,
                            finished_at=finished_at, model=model, extra=extra)

    cas_map = offload_large_artifacts(ws)

    manifest = {
        "schema": "pn-manifest/1",
        "job_id": job["id"],
        "task_type": job.get("task_type"),
        "principal": prov["who"]["principal"],
        "exit_code": exit_code,
        "committed_at": finished_at,
        "cas": cas_map,
        "tree": hash_tree(ws),
    }
    with open(os.path.join(ws, "provenance.json"), "w") as f:
        json.dump(prov, f, indent=2, sort_keys=True)
    readme = rationale if rationale else default_rationale(job, prov)
    with open(os.path.join(ws, "README.md"), "w") as f:
        f.write(readme)
    with open(os.path.join(ws, "MANIFEST.json"), "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    oid = git_commit_record(ws, manifest)

    result_hash = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    idx = index_path(job["id"])
    os.makedirs(idx, exist_ok=True)
    for fn in REQUIRED_FILES:
        src = os.path.join(ws, fn)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(idx, fn))

    return {"commit": oid, "manifest": manifest, "result_hash": result_hash,
            "index_dir": idx, "provenance": prov}

def compute_record_ok(job: dict, *, work_success: bool, committed: bool, emitted: bool) -> bool:

    if not work_success:
        return False
    if not committed:
        return False
    if not emitted:
        return False
    return valid_workspace(job.get("workspace_path") or workspace_path(job["id"]))

def valid_workspace(ws: str) -> bool:

    if not os.path.isdir(ws):
        return False
    for d in REQUIRED_DIRS:
        if not os.path.isdir(os.path.join(ws, d)):
            return False
    for fn in REQUIRED_FILES:
        if not os.path.isfile(os.path.join(ws, fn)):
            return False
    try:
        r = _git(ws, "rev-parse", "--verify", "HEAD", check=False)
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False

def replicate(job: dict, target=None) -> dict:

    ws = job.get("workspace_path")
    if not ws or not os.path.isdir(ws):
        return {"ok": False, "error": "no local workspace to replicate"}
    from . import replication, recordcfg
    if isinstance(target, replication.ReplicationTarget):
        tgt = target
    elif isinstance(target, str):
        tgt = replication.LocalDirTarget(target)
    else:
        tgt = replication.from_config(recordcfg.load())
    if tgt is None:

        return {"ok": False, "error": "no replica target configured "
                "(set PN_REPLICA_DIR / PN_REPLICA_RSYNC / PN_REPLICA_GIT / PN_REPLICA_S3 "
                "or record config replication.target)"}
    return tgt.push(job, ws)

def dispose_workspace(job_id: int) -> bool:

    ws = workspace_path(job_id)
    if not os.path.isdir(ws):
        return True
    os.makedirs(TRASH_DIR, exist_ok=True)
    staged = os.path.join(TRASH_DIR, f"{job_id}-{int(time.time())}")
    try:
        os.rename(ws, staged)
    except OSError:

        shutil.rmtree(ws, ignore_errors=True)
        return not os.path.isdir(ws)
    shutil.rmtree(staged, ignore_errors=True)
    return not os.path.isdir(ws)

def work_ttl_for(data_free_pct: float) -> int:

    return WORK_TTL_PRESSURE_S if data_free_pct < DATA_WARN_PCT else WORK_TTL_S
