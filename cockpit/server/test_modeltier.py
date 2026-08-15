#!/usr/bin/env python3

import io
import os
import re
import sys

os.chdir(os.environ.get("BRAINBOX_SERVER") or os.path.dirname(os.path.abspath(__file__)))
src = io.open("portal_metasessions.py", encoding="utf-8").read()
voice = io.open("portal_routes_voice.py", encoding="utf-8").read()
agent = io.open("portal_agent.py", encoding="utf-8").read()

ok = fail = 0

def ck(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print("  ok   %s" % name)
    else:
        fail += 1
        print("  FAIL %s" % name)

m = re.search(r"_META_MODELLSTUFEN = \(([^)]*)\)", src)
stufen = tuple(x.strip().strip("\"'") for x in m.group(1).split(",")) if m else ()
ck("erlaubte Stufen sind sonnet/opus/haiku", set(stufen) == {"sonnet", "opus", "haiku"})

m2 = re.search(r'_META_CHILD_MODEL = \(os\.environ\.get\("PN_META_CHILD_MODEL"\) or "([a-z]+)"\)', src)
ck("Vorgabe fuer Kinder ist sonnet", bool(m2) and m2.group(1) == "sonnet")

def stufe(w, vorgabe=None):
    w = str(w or "").strip().lower()
    return w if w in stufen else vorgabe

ck("gueltiger Wunsch wird uebernommen", stufe("opus") == "opus")
ck("Grossschreibung wird normalisiert", stufe("OPUS") == "opus")
ck("TIPPFEHLER faellt NICHT auf opus, sondern auf die Vorgabe", stufe("opuss", "sonnet") == "sonnet")
ck("erfundene Stufe wird nicht durchgereicht", stufe("gpt-4", "sonnet") == "sonnet")
ck("leerer Wunsch ergibt keine Stufe", stufe(None) is None)

ck("Aufgabe traegt model nur bei ausdruecklichem Wunsch", '_auf["model"] = _stufe' in src)

def _ausdruck(quelle, name):

    i = quelle.find(name + " = (")
    if i < 0:
        return ""
    j = quelle.index("(", i)
    tiefe = 0
    for k in range(j, len(quelle)):
        if quelle[k] == "(":
            tiefe += 1
        elif quelle[k] == ")":
            tiefe -= 1
            if tiefe == 0:
                return quelle[j:k + 1]
    return ""

_kind = _ausdruck(src, "_kind_modell")
ck("der Ausdruck fuer die Kind-Modellstufe ist auffindbar", bool(_kind))
_p_auf = _kind.find('.get("model")')
_p_kind = _kind.find("_META_CHILD_MODEL")
_p_tpl = _kind.find('tpl.get("model")')
ck("Rangfolge 1: was die Aufgabe verlangt, kommt zuerst",
   _p_auf >= 0 and (_p_kind < 0 or _p_auf < _p_kind) and (_p_tpl < 0 or _p_auf < _p_tpl))
ck("Rangfolge 2: dann die Kind-Vorgabe", 0 <= _p_kind < _p_tpl)
ck("Rangfolge 3: erst dann die Vorlage", _p_tpl > 0)
ck("die Aufgaben-Stufe geht durch die Pruefung, nicht roh weiter",
   "_meta_modellstufe(" in _kind[:_p_auf])

ck("kein .get() auf der Prompt-Variablen `task`", 'task.get("model")' not in _kind)
ck("Spawn setzt _kind_modell statt stur tpl", '"model": _kind_modell' in src)
ck("der Orchestrator selbst behaelt sein Modell",
   'return {"model": prov.get("model") or _orch_default_model()' in src)

ck("das Verb reicht model durch", 'args.get("model")' in voice)
ck("das Werkzeug begrenzt auf die drei Stufen", '"enum": ["sonnet", "opus", "haiku"]' in agent)
ck("Pseudo-Eintrag wieder entfernt", "session_spawn_model_hinweis" not in agent)
ck("der Brief erklaert das Prinzip", "DU BIST DIE QUALITAETSINSTANZ" in src)

print("\n%d ok, %d fehlgeschlagen" % (ok, fail))
sys.exit(1 if fail else 0)
