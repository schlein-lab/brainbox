#!/usr/bin/env python3

import os, sys, json

DEFAULT = json.dumps({"op": "sleep", "seconds": 1, "reason": "fake-llm default (no script)"})

def _state_path():
    return os.environ.get("PN_FAKELLM_STATE", "/tmp/pn_fakellm_state")

def _script():
    p = os.environ.get("PN_FAKELLM_SCRIPT")
    if p and os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            return []
    return []

def next_action():

    script = _script()
    if not script:
        return DEFAULT
    sp = _state_path()
    try:
        with open(sp) as f:
            i = int(f.read().strip() or "0")
    except Exception:
        i = 0
    action = script[i] if i < len(script) else script[-1]
    try:
        with open(sp, "w") as f:
            f.write(str(min(i + 1, len(script))))
    except Exception:
        pass
    return action if isinstance(action, str) else json.dumps(action)

class Scripted:

    def __init__(self, actions):
        self._actions = [a if isinstance(a, str) else json.dumps(a) for a in actions]
        self._i = 0

    def __call__(self, prompt, *, session=None):
        if self._i < len(self._actions):
            a = self._actions[self._i]
            self._i += 1
            return a
        return DEFAULT

if __name__ == "__main__":

    sys.stdout.write(next_action())
