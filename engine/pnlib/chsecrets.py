
from __future__ import annotations
import os, json

from . import DATA_DIR
from . import secrets as _secrets

BROKER_SECRETS_DIR = os.environ.get("PN_BROKER_SECRETS_DIR",
                                    os.path.join(DATA_DIR, "broker-secrets"))
NOBACKUP = ".nobackup"

_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")

def _safe_name(name: str) -> str:
    n = (name or "").strip()
    if not n or any(c not in _SAFE for c in n) or n.startswith(".") or "/" in n or ".." in n:
        raise ValueError(f"unsafe channel credential name {name!r}")
    return n

def ensure_dir() -> None:
    os.makedirs(BROKER_SECRETS_DIR, mode=0o700, exist_ok=True)
    try:
        os.chmod(BROKER_SECRETS_DIR, 0o700)
    except OSError:
        pass
    nb = os.path.join(BROKER_SECRETS_DIR, NOBACKUP)
    if not os.path.exists(nb):
        with open(nb, "w") as f:
            f.write("portioneer broker secrets — excluded from all off-box backups\n")
        try:
            os.chmod(nb, 0o600)
        except OSError:
            pass

def _paths(name: str):
    n = _safe_name(name)
    return (os.path.join(BROKER_SECRETS_DIR, n + ".cred"),
            os.path.join(BROKER_SECRETS_DIR, n + ".meta"))

def write_channel_cred(name: str, value: str) -> dict:

    n = _safe_name(name)
    if not value:
        raise ValueError("empty channel credential")
    ensure_dir()
    blob, meta = _secrets.seal(value.encode())
    meta = {"version": 1, "channel": n, **meta}
    cred_p, meta_p = _paths(n)
    _secrets._atomic_write(cred_p, blob, 0o600)
    _secrets._atomic_write(meta_p, (json.dumps(meta, indent=2) + "\n").encode(), 0o600)
    import hashlib
    fp = hashlib.sha256(value.encode()).hexdigest()[:12]
    return {"channel": n, "backend": meta.get("backend"), "binding": meta.get("binding"),
            "sealed_bytes": len(blob), "fingerprint": fp, "path": BROKER_SECRETS_DIR}

def read_channel_cred(name: str) -> str | None:

    try:
        cred_p, meta_p = _paths(name)
    except ValueError:
        return None
    if os.path.exists(cred_p):
        with open(cred_p, "rb") as f:
            blob = f.read()
        meta = {}
        if os.path.exists(meta_p):
            with open(meta_p) as f:
                meta = json.load(f)
        try:
            return _secrets.unseal(blob, meta).decode()
        except Exception:
            return None

    if os.environ.get("PN_ALLOW_ENV_CRED") != "1":
        return None
    env_key = "PN_" + _safe_name(name).upper().replace("-", "_").replace(".", "_") + "_CRED"
    return os.environ.get(env_key)

def status() -> dict:

    present = []
    try:
        for fn in sorted(os.listdir(BROKER_SECRETS_DIR)):
            if fn.endswith(".cred"):
                present.append(fn[:-5])
    except OSError:
        pass
    return {"dir": BROKER_SECRETS_DIR, "sealed_channels": present,
            "backend_available": _secrets.best_backend()}
