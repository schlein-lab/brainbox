
from __future__ import annotations
import json
import os

from . import dispatch as _facade

PROXY_MEM_MIB = 128

PROXY_CPU_PCT = 25

P_NODE = "X-PnNode"
P_ENDPOINT = "X-PnNodeEndpoint"
P_TOKENFILE = "X-PnNodeTokenFile"
P_TIMEOUT = "X-PnNodeTimeout"

def workers_path() -> str:

    return (os.environ.get("PN_WORKERS_JSON")
            or os.path.expanduser("~/.local/share/brainbox-portal/workers.json"))

def token_dir() -> str:
    return (os.environ.get("PN_NODE_TOKEN_DIR")
            or os.path.expanduser("~/.config/brainbox-workers"))

def load_workers() -> dict:

    try:
        with open(workers_path()) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}

def get_worker(node_id: str):

    if not node_id:
        return None
    return load_workers().get(str(node_id))

_KIND_ALIASES = {"process": ("process", "exec")}

def caps_cover(worker: dict, kind: str = "process"):

    caps = (worker or {}).get("caps") or {}
    kinds = caps.get("kinds")
    accepted = _KIND_ALIASES.get(kind, (kind,))
    if isinstance(kinds, list) and not any(a in kinds for a in accepted):
        return (f"worker '{worker.get('id')}' does not support kind '{kind}' "
                f"(caps.kinds={kinds})")
    return None

def ensure_token_file(worker: dict):

    tok = (worker or {}).get("token") or ""
    wid = (worker or {}).get("id") or ""
    if not wid:
        return None
    d = token_dir()
    path = os.path.join(d, f"{wid}.token")
    if not tok:
        return path if os.path.exists(path) else None
    try:
        cur = None
        try:
            with open(path) as f:
                cur = f.read().strip()
        except OSError:
            pass
        if cur != tok:
            os.makedirs(d, mode=0o700, exist_ok=True)
            tmp = path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, (tok + "\n").encode("utf-8"))
            finally:
                os.close(fd)
            os.replace(tmp, path)
        return path
    except OSError:
        return None

def node_props(node_id: str, timeout_s=None):

    w = get_worker(node_id)
    if w is None or not (w.get("endpoint") or "").startswith(("http://", "https://")):
        return None
    tf = ensure_token_file(w)
    if not tf:
        return None
    out = [f"{P_NODE}={node_id}", f"{P_ENDPOINT}={w['endpoint']}", f"{P_TOKENFILE}={tf}"]
    if timeout_s:
        try:
            out.append(f"{P_TIMEOUT}={int(timeout_s)}")
        except (TypeError, ValueError):
            pass
    return out

_DRAIN_SIGTERM_CODES = frozenset({-15, 143})
_DRAIN_CHANNEL_CODES = frozenset({125})

def _worker_draining(rec) -> bool:

    if not rec:
        return True
    if rec.get("state") != "online":
        return True
    h = (rec.get("facts") or {}).get("health") or {}
    if h.get("draining") is True:
        return True
    if h.get("node_active") is False:
        return True
    return str(h.get("mode") or "").strip().lower() == "off"

def drain_verdict(job, code, worker_rec, max_requeues=2):

    nix = {"requeue": False, "clear_node": False, "reason": ""}
    if not (job or {}).get("node"):
        return nix
    if (job.get("state") or "running") != "running":
        return nix
    try:
        c = int(code)
    except (TypeError, ValueError):
        return nix
    done = int(job.get("drain_requeues") or 0)
    if done >= max(0, int(max_requeues)):
        return dict(nix, reason="drain-requeue cap erreicht (%d/%d) -> ehrlich failed"
                    % (done, max_requeues))
    clear = bool(job.get("node_assigned"))
    if c in _DRAIN_CHANNEL_CODES:
        return {"requeue": True, "clear_node": clear,
                "reason": "Dispatch-Kanal riss ab (rc 125, Node '%s' unerreichbar)"
                          % job.get("node")}
    if c in _DRAIN_SIGTERM_CODES and _worker_draining(worker_rec):
        return {"requeue": True, "clear_node": clear,
                "reason": "SIGTERM waehrend Node '%s' draint/fort ist" % job.get("node")}
    return nix

def _remote_run_bin() -> str:
    override = os.environ.get("PN_REMOTE_RUN")
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tools", "pn-remote-run")

def dispatch(job_id, argv, cwd, env, props, out_path, err_path, rc_path):

    node = endpoint = tokenfile = timeout = None
    kept = []
    for p in props or []:
        k, _, v = str(p).partition("=")
        k = k.strip()
        if k == P_NODE:
            node = v.strip()
        elif k == P_ENDPOINT:
            endpoint = v.strip()
        elif k == P_TOKENFILE:
            tokenfile = v.strip()
        elif k == P_TIMEOUT:
            timeout = v.strip()
        else:
            kept.append(p)
    if not (node and endpoint and tokenfile):
        return None
    proxy = [_remote_run_bin(),
             "--endpoint", endpoint, "--token-file", tokenfile,
             "--job-id", str(job_id),
             "--out", out_path, "--err", err_path, "--rc", rc_path]
    if timeout:
        proxy += ["--timeout", timeout]

    if env:
        try:
            _ef = out_path + ".execenv"
            _fd = os.open(_ef, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.write(_fd, json.dumps(env).encode()); os.close(_fd)
            proxy += ["--exec-env-file", _ef]
        except OSError:
            pass
    if cwd and str(cwd) not in ("", "/"):
        proxy += ["--exec-cwd", str(cwd)]
    proxy += ["--"] + list(argv)

    kept = [p for p in kept if not str(p).startswith("X-PnCell")] + ["X-PnCell=1"]
    return _facade.dispatch(job_id, proxy, cwd, env, kept, out_path, err_path, rc_path)
