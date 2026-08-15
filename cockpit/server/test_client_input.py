#!/usr/bin/env python3

import json, os, ssl, sys, http.client, urllib.parse

cfg = json.load(open(os.path.expanduser("~/.config/brainbox-portal/config.json")))
PORT = int(cfg.get("port", 8077)); PIN = cfg.get("pin") or ""
CTX = ssl._create_unverified_context()

def req(method, path, headers=None, body=None):
    c = http.client.HTTPSConnection("127.0.0.1", PORT, timeout=15, context=CTX)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse(); data = r.read()
    h = {k.lower(): v for k, v in r.getheaders()}; c.close()
    return r.status, h, data

s, h, d = req("POST", "/api/login", {"Content-Type": "application/x-www-form-urlencoded"},
              urllib.parse.urlencode({"user": "owner", "password": PIN}))
cookie = h.get("set-cookie", "").split(";")[0]
assert s == 302 and cookie, "login failed status=%d" % s
H = {"Cookie": cookie, "Content-Type": "application/json"}

def exec_verb(args):
    s, _, d = req("POST", "/api/agent/exec", H, json.dumps({"verb": "client_input", "args": args}))
    return s, json.loads(d or b"{}")

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("  [PASS] " if ok else "  [FAIL] ") + name + (("  -- " + detail) if detail else ""))
    if not ok: fails += 1

s, _, d = req("GET", "/api/client-actions?since=0", H)
start = json.loads(d or b"{}").get("next", 0)

s, j = exec_verb({"verb": "copy"})
r = (j.get("result") or {})
check("copy -> ok + verb=copy", s == 200 and j.get("ok") and r.get("verb") == "copy", "result=%s" % r)

s, j = exec_verb({"verb": "einfügen", "text": "hallo aus der box"})
r = (j.get("result") or {}); acts = j.get("actions") or []
paste_ok = (r.get("verb") == "paste"
            and any(a.get("action") == "paste" and (a.get("args") or {}).get("text") == "hallo aus der box" for a in acts))
check("einfügen+text -> paste w/ text in actions", s == 200 and j.get("ok") and paste_ok, "actions=%s" % acts)

s, j = exec_verb({"verb": "taste", "keys": "ctrl+shift+s"})
r = (j.get("result") or {}); acts = j.get("actions") or []
press_ok = r.get("verb") == "press" and any(a.get("action") == "press" and (a.get("args") or {}).get("keys") == "ctrl+shift+s" for a in acts)
check("taste+keys -> press w/ chord", s == 200 and press_ok, "actions=%s" % acts)

s, j = exec_verb({"verb": "markieren", "what": "word"})
r = (j.get("result") or {})
check("markieren+what=word -> select", s == 200 and r.get("verb") == "select", "result=%s" % r)

s, j = exec_verb({"verb": "frobnicate"})
check("unknown verb -> ok:false", s == 200 and j.get("ok") is False, "j=%s" % j)

s, j = exec_verb({"verb": "press"})
check("press w/o keys -> ok:false", s == 200 and j.get("ok") is False, "j=%s" % j)

s, _, d = req("GET", "/api/client-actions?since=%d" % start, H)
bus = json.loads(d or b"{}"); verbs = [a.get("verb") for a in bus.get("actions", [])]
got = {"copy", "paste", "press", "select"}.issubset(set(verbs))
check("client-actions bus carries copy/paste/press/select", got, "verbs=%s" % verbs)

print("\nRESULT:", "FORDERUNG-4 PASS" if fails == 0 else "FAIL (%d)" % fails)
sys.exit(1 if fails else 0)
