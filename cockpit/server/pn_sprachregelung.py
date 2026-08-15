

import json
import os

STUFEN = ("still", "knapp", "normal")
VORGABE = "knapp"
_DATEI = os.path.join(os.path.expanduser("~/.local/share/brainbox-portal"), "agent_sprache.json")

def modus():

    v = str(os.environ.get("PN_AGENT_SPRACHE") or "").strip().lower()
    if v in STUFEN:
        return v
    try:
        with open(_DATEI, encoding="utf-8") as f:
            v = str((json.load(f) or {}).get("modus") or "").strip().lower()
        if v in STUFEN:
            return v
    except Exception:
        pass
    return VORGABE

def setzen(neu, pfad=None):

    neu = str(neu or "").strip().lower()
    if neu not in STUFEN:
        raise ValueError("unbekannte Stufe %r (erlaubt: %s)" % (neu, ", ".join(STUFEN)))
    p = pfad or _DATEI
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".neu"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"modus": neu}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    return neu

_KNAPP = """# Sprachregelung dieser Box: KNAPP

Erzaehle nicht, was du tust — TU es. Deine Werkzeug-Aufrufe sind der Bericht; sie werden gelesen.

- Sprich NUR, wenn sich der Zustand wirklich AENDERT: etwas ist fertig, etwas ist gescheitert, du
  hast entschieden, du bist blockiert. Ein Satz, keine Einleitung, kein Nachklang.
- NIE: „ich schaue jetzt …", „arbeite weiter", „unveraendert", „laeuft noch", „alles gut", „OK",
  Zusammenfassungen dessen, was oben schon steht, und Ankuendigungen dessen, was gleich folgt.
- Ein bestaetigendes „OK" ist ausdruecklich UNERWUENSCHT: es kostet einen vollen Zug und sagt
  niemandem etwas.
- Warten heisst warten — INNERHALB eines Werkzeug-Aufrufs (langer Schlaf/Poll in EINEM Aufruf),
  nicht in Zwischenrufen. Stille gilt hier nicht als Stillstand.

AUSGENOMMEN und weiterhin ausfuehrlich: dein ERGEBNIS (Bericht, Ergebnisdatei, Antwort auf eine
gestellte Frage), eine echte RUECKFRAGE und jede WARNUNG. Sparsam heisst leiser, nicht unehrlicher:
was schiefgeht, sagst du sofort und vollstaendig."""

_STILL = """# Sprachregelung dieser Box: STILL

Dieser Raum arbeitet stumm. Du benutzt Werkzeuge; du kommentierst sie nicht.

- Waehrend der Arbeit: KEINE Prosa. Kein Zwischenstand, keine Ankuendigung, keine Bestaetigung.
- Warten heisst warten — INNERHALB eines Werkzeug-Aufrufs. Stille gilt hier nicht als Stillstand.
- Gesprochen wird an genau zwei Stellen: in deinem ERGEBNIS (Bericht bzw. Ergebnisdatei), und wenn
  du BLOCKIERT bist und eine Entscheidung brauchst, die du nicht selbst treffen darfst.
- Eine WARNUNG ist nie stumm: was schiefgeht, sagst du sofort und vollstaendig."""

def brief(stufe=None):

    m = stufe or modus()
    if m == "still":
        return _STILL
    if m == "knapp":
        return _KNAPP
    return ""

def kanal_vorgabe(stufe=None):

    return "normal" if (stufe or modus()) == "normal" else "ambient"
