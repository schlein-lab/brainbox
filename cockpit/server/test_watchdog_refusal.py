#!/usr/bin/env python3

import json
import os
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_passed = []
_failed = []

def check(cond, desc):
    (_passed if cond else _failed).append(desc)
    print(("  ok   " if cond else "  FAIL ") + desc)

TMP = tempfile.mkdtemp(prefix="wdrefusal-")
BUS = os.path.join(TMP, "session-bus.jsonl")

REFUSAL = ("API Error: Claude Code is unable to respond to this request, which appears to violate "
           "our Usage Policy (https://www.anthropic.com/legal/aup). Please double press esc ...")
RATE = "API Error: Request rejected (429) · This request would exceed your rate limit"
FLAGGED = "API Error: Sonnet 5's safeguards flagged this message for a policy reason"

SAID = []
fake_pc = types.ModuleType("portal_channels")
fake_pc.BUS_NAME = "session-bus.jsonl"
def _say(ctx, principal, body):
    SAID.append((principal, body.get("sid"), body.get("text"), body.get("origin")))
    return {"ok": True}, 200
fake_pc.session_say = _say
fake_pc.bus_append = lambda *a, **k: None
sys.modules["portal_channels"] = fake_pc

import pn_session_watchdog as wd

CTX = {"chan_ctx": lambda: {"data_dir": TMP}}

def bus_write(sid, role, text, ts=None):
    with open(BUS, "a") as f:
        f.write(json.dumps({"kind": "message", "sid": sid, "role": role,
                            "text": text, "ts": ts if ts is not None else time.time()}) + "\n")

print("== Erkennung ==")
check(wd._is_refusal(REFUSAL), "Usage-Policy-Ablehnung wird erkannt")
check(wd._is_refusal(FLAGGED), "safeguards-flagged wird erkannt")
check(not wd._is_refusal(RATE), "429 ist KEINE Ablehnung (Anstupsen waere hier falsch)")
check(not wd._is_refusal("Ich habe eben eine API Error: ... Meldung gesehen, die appears to violate"),
      "ein Agent, der ueber Ablehnungen SPRICHT, gilt nicht als abgelehnt")
check(not wd._is_refusal(""), "leerer Text ist keine Ablehnung")

print("\n== Bus-Tail -> letzte Assistenten-Zeile je Session ==")
bus_write("aaa", "assistant", "alles gut", ts=100)
bus_write("bbb", "user", REFUSAL, ts=101)
bus_write("aaa", "assistant", REFUSAL, ts=102)
m = wd._refusal_map(CTX)
check(m.get("aaa", (0, ""))[1] == REFUSAL, "juengste Assistenten-Zeile gewinnt")
check("bbb" not in m, "user-Zeilen werden ignoriert (nur der Assistent zaehlt)")

print("\n== Eskalationsleiter ==")
wd._REF.clear(); SAID.clear()
KEY = ("owner", "aaa")
now = 1000.0
wd._probe_refusal(CTX, None, KEY, now, m)
check(len(SAID) == 1 and SAID[0][2] == "Weiter.", "Anstupser 1 ist kurz und inhaltslos")
check(SAID[0][3] == "watchdog", "Zustellung ist als watchdog gekennzeichnet (nachvollziehbar)")

wd._probe_refusal(CTX, None, KEY, now + 5, m)
check(len(SAID) == 1, "entprellt: kein zweiter Anstupser innerhalb von REFUSAL_NUDGE_S")

wd._probe_refusal(CTX, None, KEY, now + wd.REFUSAL_NUDGE_S + 1, m)
check(len(SAID) == 2 and SAID[1][2] == "/compact",
      "Anstupser 2 verdichtet den Kontext WIRKLICH (/compact)")

wd._probe_refusal(CTX, None, KEY, now + 2 * wd.REFUSAL_NUDGE_S + 2, m)
check(len(SAID) == 3, "Anstupser 3 setzt nach der Verdichtung fort")

wd._probe_refusal(CTX, None, KEY, now + 3 * wd.REFUSAL_NUDGE_S + 3, m)
check(len(SAID) == 3, "nach REFUSAL_MAX_NUDGES wird NICHT weitergeklopft (Owner-Eskalation)")

print("\n== Erholung ==")
bus_write("aaa", "assistant", "So, weiter geht es.", ts=200)
m2 = wd._refusal_map(CTX)
wd._probe_refusal(CTX, None, KEY, now + 999, m2)
check(KEY not in wd._REF, "gesunder Turn setzt den Zaehler zurueck")
wd._probe_refusal(CTX, None, KEY, now + 1000, m)
check(len(SAID) == 4, "eine SPAETERE Ablehnung stupst wieder an (Budget war zurueckgesetzt)")

print("\n== Voice bleibt unangetastet ==")
SAID.clear(); wd._REF.clear()
bus_write("__voice__", "assistant", REFUSAL, ts=300)
wd._probe_refusal(CTX, None, ("owner", "__voice__"), now + 2000, wd._refusal_map(CTX))
check(not SAID, "Voice-Zelle wird nicht angestupst (da sitzt ein Mensch davor)")

print("\n=== test_watchdog_refusal: %d passed, %d failed ===" % (len(_passed), len(_failed)))
sys.exit(1 if _failed else 0)
