#!/usr/bin/env python3

import os, sys, json, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import stats

P = F = 0
def ck(c, m):
    global P, F
    print(("  ok   " if c else " FAIL ") + m)
    P += (1 if c else 0); F += (0 if c else 1)

now = time.time()
d = tempfile.mkdtemp(); path = os.path.join(d, "provenance.jsonl")
def line(ts, principal, verb, meta=None):
    return json.dumps({"ts": int(ts * 1000), "principal": principal, "verb": verb, "meta": meta or {}})
rows = [
    line(now, "owner", "llm.chat", {"reply_chars": 40, "prompt_chars": 40}),
    line(now - 100, "acme", "llm.chat", {"reply_chars": 8}),
    line(now - 2 * 86400, "bob", "llm.v1_openai", {"reply_chars": 400}),
    line(now - 10 * 86400, "owner", "llm.chat", {"reply_chars": 100}),
    line(now - 50, "owner", "mail.test", {}),
]
with open(path, "w") as f:
    f.write("\n".join(rows) + "\n")

r = stats.aggregate(path, now=now, days=14)
ck(r["ok"] and r["entries"] == 5, "reads all entries")
ck(r["today"]["llm_calls"] == 2, "today llm_calls = 2 (got %d)" % r["today"]["llm_calls"])
ck(r["today"]["events"] == 3, "today events = 3 (2 llm + 1 mail)")
ck(r["today"]["llm_chars"] == 88, "today llm_chars = 88 (80+8)")
ck(r["today"]["llm_tokens_est"] == 22, "today token est = chars//4 = 22")
ck(r["week"]["llm_calls"] == 3, "week llm_calls = 3 (incl bob 2d ago), got %d" % r["week"]["llm_calls"])
ck(r["total"]["llm_calls"] == 4, "total llm_calls = 4 (incl 10d-ago)")
tu = {u["user"]: u for u in r["total"]["top_users"]}
ck("owner" in tu and tu["owner"]["llm_calls"] == 2, "per-user: owner 2 llm calls total")
ck("acme" in tu, "per-user includes acme")
ck(len(r["series"]) == 14, "series has 14 days")
ck(r["series"][-1]["llm_calls"] == 2, "series last day (today) = 2 llm calls")
ck(any(v["verb"] == "llm.chat" for v in r["verbs"]), "verb histogram present")
ck(stats.aggregate(os.path.join(d, "nope.jsonl"))["entries"] == 0, "missing file → empty, no crash")

with open(path, "a") as f:
    f.write(line(now - 30, "carol", "llm.chat", {"reply_chars": 12}) + "\n")
r2 = stats.aggregate(path, now=now, days=14)
ck(r2["entries"] == 6 and r2["today"]["llm_calls"] == 3, "inkrementell: neue Zeile binnen Aufruf sichtbar")
ck(r2["today"]["llm_chars"] == 100, "inkrementell: chars der neuen Zeile gezaehlt (88+12)")
with open(path, "a") as f:
    f.write(line(now - 20, "dave", "mail.test"))
r3 = stats.aggregate(path, now=now, days=14)
ck(r3["entries"] == 7 and r3["today"]["events"] == 5, "tail ohne \\n wird ueberlagert, nicht verschluckt")
r3b = stats.aggregate(path, now=now, days=14)
ck(r3b == r3, "tail-Overlay ist idempotent (Offset blieb VOR der Zeile)")
with open(path, "w") as f:
    f.write(rows[0] + "\n")
r4 = stats.aggregate(path, now=now, days=14)
ck(r4["entries"] == 1 and r4["total"]["llm_calls"] == 1, "geschrumpfte Datei → Neuaufbau statt Reste")
bad = os.path.join(d, "kaputt.jsonl"); os.mkdir(bad)
rb = stats.aggregate(bad, now=now)
ck(rb.get("ok") is False and "error" in rb, "unlesbar → ok:False + error, KEINE Nullen")

print()
if F: print("FAILED %d" % F); sys.exit(1)
print("ALL %d PASS" % P)
