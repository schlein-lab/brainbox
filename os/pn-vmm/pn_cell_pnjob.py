#!/usr/bin/env python3

import json, os, sys, urllib.request

BASE = os.environ.get("PN_COMPUTE_URL", "http://127.0.0.1:8089") + "/pncompute"

def _call(method, path, obj=None):
    data = json.dumps(obj).encode() if obj is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except Exception:
            return e.code, {"ok": False, "error": "HTTP %s" % e.code}
    except Exception as e:
        return 0, {"ok": False, "error": "Broker nicht erreichbar: %s" % e}

def main(argv):
    if not argv:
        print("usage: pnjob {submit|status|result|cancel|list} ...", file=sys.stderr)
        return 2
    cmd = argv[0]
    if cmd == "submit":
        mem = cpu = to = None
        rest = argv[1:]
        while rest and rest[0].startswith("--") and rest[0] != "--":
            k = rest.pop(0)
            v = rest.pop(0) if rest else None
            if k == "--mem":     mem = int(v)
            elif k == "--cpu":   cpu = int(v)
            elif k == "--timeout": to = int(v)
        if rest and rest[0] == "--":
            rest = rest[1:]
        if not rest:
            print("pnjob submit: leeres Kommando (argv nach --)", file=sys.stderr)
            return 2
        body = {"cmd": rest}
        if mem is not None: body["mem_mib"] = mem
        if cpu is not None: body["cpu_pct"] = cpu
        if to is not None:  body["timeout_s"] = to
        st, resp = _call("POST", "/submit", body)
    elif cmd in ("status", "result", "cancel"):
        if len(argv) < 2:
            print("pnjob %s: job_id fehlt" % cmd, file=sys.stderr)
            return 2
        jid = argv[1]
        if cmd == "cancel":
            st, resp = _call("POST", "/cancel", {"id": int(jid)})
        else:
            st, resp = _call("GET", "/%s?id=%s" % (cmd, jid))
    elif cmd == "list":
        st, resp = _call("GET", "/list")
    else:
        print("pnjob: unbekannter Befehl %r" % cmd, file=sys.stderr)
        return 2
    print(json.dumps(resp, ensure_ascii=False, indent=2))
    return 0 if resp.get("ok") else 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
