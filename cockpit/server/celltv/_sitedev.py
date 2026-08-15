
import os
import sys

for _p in (os.environ.get("PNLIB_HOME"), os.path.expanduser("~/portioneer")):
    if _p and os.path.isdir(os.path.join(_p, "pnlib")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
        break

try:
    from pnlib.devices import addr, resolve
except Exception:
    import re as _re

    _IP = _re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

    def resolve(ref, default=None):
        ref = str(ref or "").strip()
        return ref if _IP.match(ref) else default

    def addr(role, default=None):
        return default
