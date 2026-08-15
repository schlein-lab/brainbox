

from __future__ import annotations

import os

__all__ = ["canon", "lane_key", "matches"]

_HOME_VARS = ("${HOME}/", "$HOME/")
_QUOTES = ('"', "'")

def canon(text, box_home=None):

    s = "" if text is None else str(text)
    home = os.path.expanduser("~") if box_home is None else str(box_home)
    home = home.rstrip("/")
    if home and home != "":
        s = s.replace(home + "/", "~/")
    for var in _HOME_VARS:
        s = s.replace(var, "~/")
    for q in _QUOTES:
        s = s.replace(q, "")
    return " ".join(s.split())

def lane_key(argv, box_home=None):

    toks = [t for t in (argv or [])
            if "=" not in str(t) and str(t) != "/usr/bin/env" and not str(t).endswith("/env")]
    return canon(" ".join(map(str, toks)), box_home) if toks else ""

def matches(key, cmdline, box_home=None):

    k = canon(key, box_home)
    if not k:
        return False
    return k in canon(cmdline, box_home)
