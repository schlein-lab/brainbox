#!/usr/bin/env python3

import sys
import threading
import time

ARTEN = ("cache", "cursor", "snapshot", "singleton", "konfig")
NEUSTART_ARTEN = ("verfaellt", "rekonstruiert", "persistiert")

_REG = {}
_REG_LOCK = threading.Lock()

def register(name, art, halter, ref=None, ttl_s=None, beschreibung="",
             neustart="verfaellt", schreiber=""):

    if art not in ARTEN:
        raise ValueError("portal_zustand: unbekannte art %r (erlaubt: %s)"
                         % (art, "|".join(ARTEN)))
    if neustart not in NEUSTART_ARTEN:
        raise ValueError("portal_zustand: unbekannte neustart-Semantik %r (erlaubt: %s)"
                         % (neustart, "|".join(NEUSTART_ARTEN)))
    eintrag = {"art": art, "halter": str(halter), "ref": ref,
               "ttl_s": ttl_s, "beschreibung": str(beschreibung or ""),
               "neustart": neustart, "schreiber": str(schreiber or ""),
               "seit": time.time()}
    with _REG_LOCK:
        _REG[str(name)] = eintrag

def _schaetze(ref):

    out = {"typ": None, "len": None, "bytes_flach": None}
    if ref is None:
        out["hinweis"] = "nicht referenzierbar (instanzgebunden); Groesse unbekannt"
        return out
    try:
        obj = ref() if callable(ref) else ref
    except Exception as e:
        out["fehler"] = "Getter schlug fehl: %r" % (e,)
        return out
    if obj is None:
        out["hinweis"] = "noch nicht initialisiert"
        return out
    out["typ"] = type(obj).__name__
    try:
        out["len"] = len(obj)
    except TypeError:
        pass
    except Exception as e:
        out["fehler"] = "len() schlug fehl: %r" % (e,)
    try:
        out["bytes_flach"] = sys.getsizeof(obj)
    except Exception as e:
        out.setdefault("fehler", "getsizeof() schlug fehl: %r" % (e,))
    return out

def alle():

    with _REG_LOCK:
        eintraege = sorted(_REG.items())
    now = time.time()
    out = []
    for name, e in eintraege:
        row = {"name": name, "art": e["art"], "halter": e["halter"],
               "ttl_s": e["ttl_s"], "neustart": e["neustart"],
               "beschreibung": e["beschreibung"], "schreiber": e["schreiber"],
               "registriert_vor_s": round(now - e["seit"], 1)}
        row.update(_schaetze(e["ref"]))
        out.append(row)
    return out

def uebersicht():

    stores = alle()
    arten = {}
    for s in stores:
        arten[s["art"]] = arten.get(s["art"], 0) + 1
    return {"ok": True, "anzahl": len(stores), "arten": arten,
            "hinweis": ("Groessen sind FLACH geschaetzt (len + sys.getsizeof des Containers, "
                        "keine tiefe Traversierung); nur Module, die dieser Prozess importiert "
                        "hat, sind angemeldet. Vertrag: docs/portal-zustand.md"),
            "stores": stores}
