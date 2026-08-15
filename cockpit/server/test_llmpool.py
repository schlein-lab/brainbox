#!/usr/bin/env python3

import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import llmpool

PASS = FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {label}")
    else:
        FAIL += 1; print(f"  FAIL {label}")

s = "\n".join([
    json.dumps({"type": "system", "subtype": "init"}),
    json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "par"}]}}),
    json.dumps({"type": "rate_limit_event",
                "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour",
                                    "resetsAt": "2099-01-01T00:00:00Z"}}),
    json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "PONG final"}),
])
text, events, is_err = llmpool.parse_stream(s)
check(text == "PONG final", "parse_stream picks result field")
check(len(events) == 1 and not is_err, "parse_stream collects rate event, no error")
check(llmpool.parse_stream(json.dumps(
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}))[0] == "hi",
    "parse_stream assistant fallback")
check(llmpool.parse_stream("plain answer\nline2")[0] == "plain answer\nline2",
      "parse_stream plain-text fallback never blanks the answer")
check(llmpool.parse_stream(json.dumps(
    {"type": "result", "subtype": "error_max_turns", "is_error": True, "result": ""}))[2] is True,
    "parse_stream flags is_error")

rl_ev = [{"rate_limit_info": {"status": "rejected", "resetsAt": "2099-01-01T00:00:00Z"}}]
check(llmpool.looks_rate_limited(0, "", "x", rl_ev) is True, "rate-limit via rejected event")
check(llmpool.looks_rate_limited(1, "usage limit reached", "", []) is True, "rate-limit via stderr")
check(llmpool.looks_rate_limited(0, "", "ok", []) is False, "clean success not rate-limited")
check(llmpool.looks_rate_limited(1, "segfault", "", []) is False, "generic error not rate-limited")

check(abs(llmpool._parse_ts("2099-01-01T00:00:00Z") - 4070908800.0) < 90000, "parse_ts ISO8601")
check(llmpool._parse_ts(1893456000) == 1893456000.0, "parse_ts epoch")
check(llmpool._parse_ts("garbage") == 0.0, "parse_ts junk → 0")

d = tempfile.mkdtemp()
cfg, state = os.path.join(d, "c.json"), os.path.join(d, "s.json")
with open(cfg, "w") as f:
    json.dump({"max_concurrent": 6, "accounts": [
        {"id": "a", "home": "/tmp/a"}, {"id": "b", "home": "/tmp/b"}, {"id": "c", "home": "/tmp/c"}]}, f)
clock = [1_000_000.0]
p = llmpool.LLMPool(cfg, state, "/tmp/default")
p._now = lambda: clock[0]
check(p.multi() and p.enabled_count() == 3 and p.max_concurrent == 6, "3-account config loaded")
picks = [p.pick()["id"] for _ in range(3)]
check(sorted(picks) == ["a", "b", "c"], "first 3 picks spread across accounts")
for aid in picks:
    p.record(aid, ok=True)
p.record("a", ok=False, was_rate_limited=True,
         rate_events=[{"rate_limit_info": {"status": "rejected", "resetsAt": "2099-01-01T00:00:00Z"}}])
arow = [x for x in p.snapshot()["accounts"] if x["id"] == "a"][0]
check(arow["cooling"] and arow["rate_limited"] == 1, "account cooled after reject")
check("a" not in set(p.pick()["id"] for _ in range(6)), "cooling account avoided by pick")
check(p.pick(exclude={"b", "c"}) is not None, "pick with exclude never None")
p.clear_cooldown("a")
check(not [x for x in p.snapshot()["accounts"] if x["id"] == "a"][0]["cooling"], "clear_cooldown un-cools")
check(llmpool.LLMPool(cfg, state, "/tmp/default").snapshot()["accounts"], "state reloads from disk")

p2 = llmpool.LLMPool(cfg, state, "/tmp/default")
p2._now = lambda: 2_000_000.0

p2._state["a"] = {**llmpool._blank_state(),
                  "five_hour": {"status": "rejected", "resets_at": 1_999_000.0, "at": 1_998_000.0}}
p2._state["b"] = llmpool._blank_state()
p2._state["c"] = llmpool._blank_state()
check(p2._rank("a", p2._now()) == 0, "stale reject (window reset) → rank 0 (auto-recovers)")

p2._state["a"]["five_hour"] = {"status": "rejected", "resets_at": 2_001_000.0, "at": 1_999_000.0}
check(p2._rank("a", p2._now()) == 3, "active reject (window in future) → rank 3 (avoided)")

p2._state["a"]["five_hour"] = {"status": "rejected", "resets_at": 1_999_000.0, "at": 1_998_000.0}
picks2 = set(p2.pick()["id"] for _ in range(6))
check("a" in picks2, "recovered account 'a' rejoins rotation after window reset")

p3 = llmpool.LLMPool(os.path.join(d, "none.json"), os.path.join(d, "s3.json"), "/home/owner")
check(not p3.multi() and p3.enabled_count() == 1, "no config → single default account")
check(p3.pick()["home"] == "/home/owner", "default account uses portal HOME")

import datetime as _dt
created = _dt.datetime(2026, 6, 17, 21, 38, 24).timestamp()

nr = llmpool.next_monthly_renewal(created, _dt.datetime(2026, 7, 1).timestamp())
check(_dt.datetime.fromtimestamp(nr).strftime("%Y-%m-%d") == "2026-07-17", "next_monthly_renewal advances to next month")

c31 = _dt.datetime(2026, 1, 31, 12, 0, 0).timestamp()
nr2 = llmpool.next_monthly_renewal(c31, _dt.datetime(2026, 2, 5).timestamp())
check(_dt.datetime.fromtimestamp(nr2).month == 2, "next_monthly_renewal clamps short months")
check(llmpool.next_monthly_renewal(0, _dt.datetime(2026, 7, 1).timestamp()) == 0.0, "no sub date → 0")

h = tempfile.mkdtemp()
os.makedirs(os.path.join(h, ".claude"), exist_ok=True)
with open(os.path.join(h, ".claude.json"), "w") as f:
    json.dump({"oauthAccount": {"emailAddress": "x@y.z", "displayName": "X",
        "organizationRateLimitTier": "default_claude_max_20x",
        "subscriptionCreatedAt": "2026-06-17T21:38:24.996364Z"}}, f)
with open(os.path.join(h, ".claude", ".credentials.json"), "w") as f:
    json.dump({"claudeAiOauth": {"subscriptionType": "max", "expiresAt": 1783193874953}}, f)
usage = os.path.join(h, "usage.json")
with open(usage, "w") as f:
    json.dump({"x@y.z": {"five_hour_pct": 42.0, "seven_day_pct": 7.0,
                         "five_hour_resets_at": 1783182600, "ts": 1783100000}}, f)
info = llmpool.read_account_info(h, usage, now=_dt.datetime(2026, 7, 1).timestamp(), ttl=0)
check(info["email"] == "x@y.z" and info["subscription"] == "max", "read_account_info identity+subscription")
check(info["tier"] == "default_claude_max_20x", "read_account_info tier")
check(info["five_hour_pct"] == 42.0 and info["seven_day_pct"] == 7.0, "read_account_info usage% from usage.json")
check(info["next_renewal"] > 0 and info["token_expires"] > 0, "read_account_info renewal + token expiry")
check(llmpool.read_account_info("/nonexistent/home", now=1.0)["email"] == "", "read_account_info missing HOME → blanks")

cfg2 = os.path.join(h, "cfg.json"); st2 = os.path.join(h, "st.json")
with open(cfg2, "w") as f:
    json.dump({"accounts": [{"id": "acc", "home": h}]}, f)
now_near = _dt.datetime.fromtimestamp(info["next_renewal"]).timestamp() - 86400
pr = llmpool.LLMPool(cfg2, st2, "/tmp/def", usage_path=usage)
due = pr.renewals_due(days=2, now=now_near)
check(len(due) == 1 and due[0]["id"] == "acc", "renewals_due flags an account 1 day out")
pr.mark_alerted("acc", info["next_renewal"])
check(len(pr.renewals_due(days=2, now=now_near)) == 0, "mark_alerted dedups the same cycle")

d2 = tempfile.mkdtemp()
c2 = os.path.join(d2, "cfg.json"); s2c = os.path.join(d2, "st.json")
pw = llmpool.LLMPool(c2, s2c, "/home/owner")
check(pw.add_account("max-2", os.path.join(d2, "h2"))["ok"], "add_account ok")
ids = [a["id"] for a in pw.accounts]
check("primary" in ids and "max-2" in ids, "add_account keeps implicit primary + adds new")
check(pw.account_home("max-2") == os.path.join(d2, "h2"), "account_home resolves new account")
check(not pw.multi(), "new account disabled by default → still single-account")
check(pw.set_enabled("max-2", True)["ok"] and pw.multi(), "set_enabled activates → multi")
check(llmpool.LLMPool(c2, s2c, "/home/owner").multi(), "config persisted across reload")
check(pw.add_account("bad id!!")["ok"] is False, "add_account rejects bad id")
check(pw.remove_account("primary")["ok"] is False, "remove_account refuses primary")
check(pw.remove_account("max-2")["ok"] and pw.account_home("max-2") is None, "remove_account drops account")
check(pw.remove_account("ghost")["ok"] is False, "remove_account unknown → not ok")
check(not llmpool.LLMPool(c2, s2c, "/home/owner").multi(), "removal persisted across reload")

print()
if FAIL:
    print(f"FAILED {FAIL} (passed {PASS})"); sys.exit(1)
print(f"ALL {PASS} PASS")
