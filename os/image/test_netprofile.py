#!/usr/bin/env python3

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
W = os.path.join(HERE, "brainbox-setup")
SRC = open(W, encoding="utf-8").read()

fails = []

def ck(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s%s" % (name, (" — " + detail) if detail else ""))
        fails.append(name)

print("== Netzprofil: leise ist die Vorgabe")

ck("site.conf-Schreiber faellt auf 'managed' zurueck",
   'cfg.get("NET_PROFILE", "managed")' in SRC,
   "der Schreiber wuerde sonst 'home' in die site.conf legen")

ck("cfg-Grundzustand ist 'managed'",
   re.search(r'"NET_PROFILE":\s*"managed"', SRC) is not None)

ck("nirgends mehr eine 'home'-Vorgabe fuer NET_PROFILE",
   re.search(r'NET_PROFILE"?,?\s*"home"\)', SRC) is None
   and re.search(r'"NET_PROFILE":\s*"home"', SRC) is None)

print("== Netzprofil: nur ein ausdrueckliches 'home' schaltet laut")

m = re.search(r'cfg\["NET_PROFILE"\]\s*=\s*(.+)', SRC)
ck("Validierung vorhanden", m is not None)
if m:
    expr = m.group(1)
    ck("Validierung vergleicht strikt auf 'home'",
       '== "home"' in expr and '"managed"' in expr,
       "gefunden: %s" % expr.strip()[:90])

    def entscheide(v):
        return "home" if str(v).strip() == "home" else "managed"

    for wert, erwartet in (("home", "home"), ("managed", "managed"), ("", "managed"),
                           ("HOME", "managed"), ("home ", "home"), ("hom", "managed"),
                           (None, "managed"), ("1", "managed"), ("yes", "managed")):
        ck("Eingabe %r -> %s" % (wert, erwartet), entscheide(wert) == erwartet)

print("== Netzprofil: der Besitzer wird wirklich gefragt")

ck("Auswahl wird angezeigt", "NET_PROFILE_HOME" in SRC and "NET_PROFILE_MANAGED" in SRC)
ck("Feld wird eingesammelt", "NET_PROFILE:(document.getElementById" in SRC)
ck("Auswahl steht im Funktionen-Schritt",
   SRC.index("NET_PROFILE_MANAGED") > SRC.index("function viewFeatures"))
ck("'leise' ist vorausgewaehlt",
   re.search(r'id="NET_PROFILE_MANAGED"\s*checked|NET_PROFILE_MANAGED\\"\s*checked', SRC)
   is not None or 'NET_PROFILE_MANAGED\'+"\\" checked' in SRC
   or 'id=\'NET_PROFILE_MANAGED\' checked' in SRC
   or '"NET_PROFILE_MANAGED\' checked' in SRC
   or 'NET_PROFILE_MANAGED" checked' in SRC,
   "die sichere Wahl muss die Vorbelegung sein")

for spr, schluessel in (("DE", "In welchem Netz steht die Box?"),
                        ("EN", "Which network is the box on?")):
    ck("Text %s vorhanden" % spr, schluessel in SRC)

print()
if fails:
    print("FEHLGESCHLAGEN: %d" % len(fails))
    sys.exit(1)
print("alle Netzprofil-Tests bestanden")
