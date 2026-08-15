#!/usr/bin/env python3

import io
import os
import re
import sys

_HIER = os.path.dirname(os.path.abspath(__file__))
src = io.open(os.path.join(_HIER, "portal_metasessions.py"), encoding="utf-8").read()

m = re.search(r"^(def _meta_sweep_orphans\(now\):\n(?:[ \t].*\n|[ \t]*\n)*)", src, re.M)
assert m, "Funktion nicht gefunden"
fn_src = m.group(1)
_rumpf = fn_src.split("\n", 1)[1]
assert not re.search(r"^def ", _rumpf, re.M), \
    "Ausschnitt enthaelt eine zweite Funktion auf oberster Ebene -- der Schnitt stimmt nicht"

ok = fail = 0

def ck(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print("  ok   " + name)
    else:
        fail += 1
        print("  FAIL " + name)

class Zelle:
    def __init__(self, lebt=True):
        self._l = lebt

    def alive(self):
        return self._l

def lauf(daten, jetzt=100000.0, boot=0.0, letzte=0.0, zellen=None, versuche=None):
    stops = []
    ns = {
        "_META_BOOT_TS": boot, "_META_ADOPT_GRACE_S": 180.0,
        "_META_SWEEP_GRACE_S": 600.0, "_META_SWEEP_EVERY_S": 60.0,
        "_META_SWEEP_MAX_PER_RUN": 2, "_meta_last_sweep": letzte,
        "_META_SWEEP_MAX_VERSUCHE": 3,
        "_meta_sweep_versuche": (versuche if versuche is not None else {}),
        "DEFAULT_PRINCIPAL": "owner",
        "_meta_load": lambda: daten,
        "_meta_cell": lambda o, s: (zellen or {}).get(s, Zelle(True)),
        "_meta_cell_stop": lambda o, s: stops.append(s),
    }
    exec(compile(fn_src, "<sweep>", "exec"), ns)
    ns["_meta_sweep_orphans"](jetzt)
    return stops, ns

T = 100000.0

d = {"orch": {"owner": "owner", "tasks": [
    {"tid": "t1", "sid": "w1", "state": "error", "ended": T - 40000}]}}
stops, _ = lauf(d)
ck("verwaiste Zelle eines terminalen Auftrags wird gestoppt", stops == ["w1"])

d = {"orch": {"owner": "owner", "tasks": [
    {"tid": "t1", "sid": "w1", "state": "error", "ended": T - 120}]}}
stops, _ = lauf(d)
ck("Karenzzeit: frisch beendete Auftraege bleiben unberuehrt", stops == [])

d = {"orch": {"owner": "owner", "tasks": [
    {"tid": "t1", "sid": "w1", "state": "error", "ended": T - 40000},
    {"tid": "t2", "state": "pending", "resume_sid": "w1"}]}}
stops, _ = lauf(d)
ck("resume_sid einer wartenden Aufgabe wird verschont", stops == [])

d = {"a": {"owner": "owner", "tasks": [{"tid": "t1", "sid": "w1", "state": "error", "ended": T - 40000}]},
     "b": {"owner": "owner", "tasks": [{"tid": "t9", "sid": "w1", "state": "running"}]}}
stops, _ = lauf(d)
ck("sid mit laufendem Auftrag anderswo wird verschont", stops == [])

d = {"orch": {"owner": "owner", "tasks": [{"tid": "t1", "sid": "sub", "state": "error", "ended": T - 40000}]},
     "sub": {"owner": "owner", "tasks": [{"tid": "x", "state": "running", "sid": "enkel"}]}}
stops, _ = lauf(d)
ck("Worker mit eigener offener Metasession-Arbeit wird verschont", stops == [])

d = {"orch": {"owner": "owner", "tasks": [
    {"tid": "t%d" % i, "sid": "w%d" % i, "state": "error", "ended": T - 40000} for i in range(5)]}}
stops, _ = lauf(d)
ck("hoechstens 2 Stops pro Lauf", len(stops) == 2)

d = {"orch": {"owner": "owner", "tasks": [
    {"tid": "t1", "sid": "w1", "state": "error", "ended": T - 40000}]}}
stops, _ = lauf(d, letzte=T - 30)
ck("Drossel: kein Lauf innerhalb von 60 s", stops == [])

stops, _ = lauf(d, boot=T - 60)
ck("Adoptions-Gnadenfrist: kurz nach Boot kein Sweep", stops == [])

d = {"orch": {"owner": "owner", "tasks": [
    {"tid": "t1", "sid": "w1", "state": "error", "ended": T - 40000}]}}
stops, _ = lauf(d, zellen={"w1": Zelle(False)})
ck("bereits tote Zelle loest keinen Stop aus", stops == [])

d = {"orch": {"owner": "owner", "tasks": [
    {"tid": "t1", "sid": "w1", "state": "done", "ended": T - 40000}]}}
stops, _ = lauf(d)
ck("done-Auftraege werden genauso eingesammelt", stops == ["w1"])

d = {"orch": {"owner": "owner", "tasks": [
    {"tid": "t1", "sid": "w1", "state": "error", "ended": T - 40000}]}}
v = {}
laeufe = []
for i in range(5):
    stops, _ = lauf(d, letzte=0.0, versuche=v)
    laeufe.append(len(stops))
ck("hoechstens drei Stop-Versuche, danach Ruhe", laeufe == [1, 1, 1, 0, 0])
ck("der Zaehler steht bei drei", v.get("w1") == 3)

v2 = {"w1": 2}
lauf(d, zellen={"w1": Zelle(False)}, versuche=v2)
ck("erfolgreicher Stop loescht den Zaehler", "w1" not in v2)

print("\n%d ok, %d fehlgeschlagen" % (ok, fail))
sys.exit(1 if fail else 0)
