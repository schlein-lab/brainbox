
import os

_CONF = "/etc/brainbox/site.conf"
_RUN = "/run/brainbox/site.env"
_cache = None

def _parse(path):
    d = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                d[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return d

def _load(force=False):
    global _cache
    if _cache is None or force:
        m = {}
        m.update(_parse(_RUN))
        m.update(_parse(_CONF))
        _cache = m
    return _cache

def get(key, default=None):
    if key in os.environ:
        return os.environ[key]
    return _load().get(key, default)

def get_int(key, default=0):
    try:
        return int(get(key, default))
    except (TypeError, ValueError):
        return default

def get_bool(key, default=False):
    v = get(key, None)
    return default if v is None else str(v).lower() in ("1", "true", "yes", "on")

def all():
    return dict(_load())

def reload():
    _load(force=True)
