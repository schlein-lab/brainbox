
import os
import re
import json

try:
    from pnlib import site as _site
except Exception:
    _site = None

_IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

def _cfg(key, default=None):

    if _site is not None:
        return _site.get(key, default)
    v = os.environ.get(key)
    if v is not None:
        return v
    for p in ("/etc/brainbox/site.conf", "/run/brainbox/site.env"):
        try:
            for line in open(p):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, val = line.partition("=")
                    if k.strip() == key:
                        return val.strip().strip('"').strip("'")
        except OSError:
            pass
    return default

def _devices_json_path():
    p = _cfg("DEVICES_JSON")
    if p:
        return os.path.expanduser(p)
    home = _cfg("SERVICE_HOME") or os.path.expanduser("~")
    return os.path.join(home, ".local", "share", "brainbox-portal", "devices.json")

def _load_roster():
    try:
        d = json.load(open(_devices_json_path()))
    except Exception:
        return []
    if isinstance(d, list):
        return [x for x in d if isinstance(x, dict)]
    if isinstance(d, dict):
        items = d.get("devices")
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
        return [v for v in d.values() if isinstance(v, dict)]
    return []

def _dev_addr(dev):
    tr = dev.get("transport") or {}
    return tr.get("addr") or dev.get("ip") or dev.get("host")

def _from_registry(ref):

    ref = str(ref)
    for d in _load_roster():
        if ref in (d.get("id"), d.get("name"), d.get("role")):
            a = _dev_addr(d)
            if a:
                return a
    return None

def resolve(ref, default=None):

    ref = str(ref or "").strip()
    if not ref:
        return default
    if _IP_RE.match(ref):
        return ref
    return _from_registry(ref) or default

def addr(role, default=None):

    key = "DEV_" + re.sub(r"[^A-Za-z0-9]+", "_", str(role)).upper().strip("_")
    binding = _cfg(key)
    if binding:
        return resolve(binding, default)
    return _from_registry(role) or default

if __name__ == "__main__":
    import sys
    for a in (sys.argv[1:] or ["tv", "cast-tv"]):
        print("%-14s -> %s" % (a, addr(a) or resolve(a) or "(unresolved)"))
