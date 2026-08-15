
from __future__ import annotations
import os, json

from . import DATA_DIR

CONFIG_PATH = os.environ.get("PN_EGRESS_CONFIG", os.path.join(DATA_DIR, "egress", "config.json"))

DEFAULTS = {
    "proxy": {"real_upstream": False, "connect_timeout_s": 15},
    "seed": [],
}

def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out

def load(path: str | None = None) -> dict:

    path = path or CONFIG_PATH
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        with open(path) as f:
            cfg = _deep_merge(cfg, json.load(f))
    except (OSError, ValueError):
        pass
    if os.environ.get("PN_EGRESS_REAL_UPSTREAM", "").strip().lower() in ("1", "true", "yes", "on"):
        cfg["proxy"]["real_upstream"] = True
    return cfg

def save(cfg: dict, path: str | None = None) -> str:
    path = path or CONFIG_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return path

def apply_seed(egress_module, cx, cfg: dict | None = None) -> int:

    cfg = cfg if cfg is not None else load()
    n = 0
    for e in (cfg.get("seed") or []):
        egress_module.add_allow(cx, e["host"], int(e.get("port", 0)),
                                e.get("principal", "*"), e.get("task_type", "*"),
                                note=e.get("note", "config seed"), approved_by="config-seed")
        n += 1
    return n
