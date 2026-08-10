#!/usr/bin/env python3

import os, sys, json, time

DEFAULT = json.dumps({"op": "sleep", "seconds": 1, "reason": "fakewarm default"})

def _script():
    p = os.environ.get("PN_FAKEWARM_SCRIPT")
    if p and os.path.exists(p):
        try:
            with open(p) as f:
                s = json.load(f)
            return [a if isinstance(a, str) else json.dumps(a) for a in s]
        except Exception:
            return []
    return []

def main():
    script = _script()
    wedge_at = os.environ.get("PN_FAKEWARM_WEDGE")
    crash_at = os.environ.get("PN_FAKEWARM_CRASH")
    wedge_at = int(wedge_at) if wedge_at not in (None, "") else None
    crash_at = int(crash_at) if crash_at not in (None, "") else None
    turn = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            sys.stdout.write(json.dumps({"ok": False, "error": "bad json"}) + "\n")
            sys.stdout.flush()
            continue
        if req.get("ping"):
            sys.stdout.write(json.dumps({"ok": True}) + "\n"); sys.stdout.flush(); continue
        if req.get("prime"):
            sys.stdout.write(json.dumps({"ok": True, "text": "primed"}) + "\n")
            sys.stdout.flush(); continue

        if crash_at is not None and turn == crash_at:
            os._exit(7)
        if wedge_at is not None and turn == wedge_at:
            while True:
                time.sleep(3600)
        action = script[turn] if turn < len(script) else (script[-1] if script else DEFAULT)
        turn += 1
        sys.stdout.write(json.dumps({"ok": True, "text": action}) + "\n")
        sys.stdout.flush()

if __name__ == "__main__":
    main()
