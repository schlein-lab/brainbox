
from __future__ import annotations
import os, json

from . import DATA_DIR

CONFIG_PATH = os.environ.get("PN_RECORD_CONFIG", os.path.join(DATA_DIR, "record", "config.json"))

DEFAULTS = {
    "retention": {
        "work_ttl_s": 24 * 3600,
        "work_ttl_pressure_s": 3600,
        "data_warn_pct": 15.0,
        "data_hard_pct": 8.0,
    },
    "replication": {},
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
    ret = cfg["retention"]
    if os.environ.get("PN_WORK_TTL_S"):
        ret["work_ttl_s"] = int(os.environ["PN_WORK_TTL_S"])
    if os.environ.get("PN_WORK_TTL_PRESSURE_S"):
        ret["work_ttl_pressure_s"] = int(os.environ["PN_WORK_TTL_PRESSURE_S"])
    if os.environ.get("PN_DATA_WARN_PCT"):
        ret["data_warn_pct"] = float(os.environ["PN_DATA_WARN_PCT"])
    if os.environ.get("PN_DATA_HARD_PCT"):
        ret["data_hard_pct"] = float(os.environ["PN_DATA_HARD_PCT"])
    if os.environ.get("PN_DATA_FLOOR_PCT"):
        ret["data_floor_pct"] = float(os.environ["PN_DATA_FLOOR_PCT"])
    return cfg

def save(cfg: dict, path: str | None = None) -> str:
    path = path or CONFIG_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return path

def apply_retention(record_module, cfg: dict | None = None) -> dict:

    cfg = cfg if cfg is not None else load()
    ret = cfg.get("retention") or {}
    record_module.WORK_TTL_S = int(ret.get("work_ttl_s", record_module.WORK_TTL_S))
    record_module.WORK_TTL_PRESSURE_S = int(ret.get("work_ttl_pressure_s",
                                                     record_module.WORK_TTL_PRESSURE_S))
    record_module.DATA_WARN_PCT = float(ret.get("data_warn_pct", record_module.DATA_WARN_PCT))
    record_module.DATA_HARD_PCT = float(ret.get("data_hard_pct", record_module.DATA_HARD_PCT))
    record_module.DATA_FLOOR_PCT = float(ret.get("data_floor_pct", record_module.DATA_FLOOR_PCT))
    return ret
