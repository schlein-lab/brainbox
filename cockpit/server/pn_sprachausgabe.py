#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

MAX_LEN = 900
_ENV = os.path.expanduser("~/.config/nabu-display.env")

TAKT_S = 20
STUNDE_MAX = 12
_STAND = os.path.expanduser("~/.local/share/brainbox-portal/sprachausgabe-takt.json")

def _env():
    cfg = {}
    try:
        with open(_ENV, encoding="utf-8") as f:
            for zeile in f:
                zeile = zeile.strip()
                if zeile and not zeile.startswith("#") and "=" in zeile:
                    k, v = zeile.split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return cfg

def _ha(pfad, koerper=None, timeout=25):
    cfg = _env()
    url = str(cfg.get("HA_URL", "")).rstrip("/")
    tok = str(cfg.get("HA_TOKEN", ""))
    if not (url and tok):
        raise RuntimeError("nabu-display.env unvollständig (HA_URL/HA_TOKEN)")
    r = urllib.request.Request(
        url + pfad,
        data=json.dumps(koerper).encode() if koerper is not None else None,
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        roh = f.read().decode("utf-8", "replace")
    return json.loads(roh) if roh.strip() else {}

def lautsprecher():

    try:
        st = _ha("/api/states")
    except Exception:
        return []
    aus = []
    for s in st:
        eid = str(s.get("entity_id") or "")
        if not eid.startswith("media_player."):
            continue
        zustand = str(s.get("state") or "")
        aus.append({
            "id": eid,
            "label": str((s.get("attributes") or {}).get("friendly_name") or eid.split(".", 1)[-1]),
            "zustand": zustand,
            "erreichbar": zustand not in ("unavailable", "unknown", ""),
        })
    return sorted(aus, key=lambda d: d["label"].lower())

def sprachsystem():

    ent = str(_env().get("NABU_ENTITY", "")).strip()
    return ent or None

def _takt_pruefen(sid, jetzt=None):

    jetzt = jetzt if jetzt is not None else time.time()
    try:
        with open(_STAND, encoding="utf-8") as f:
            stand = json.load(f)
    except Exception:
        stand = {}
    meins = [t for t in (stand.get(sid) or []) if jetzt - t < 3600]
    if meins and jetzt - max(meins) < TAKT_S:
        return False, "zu schnell hintereinander (frühestens %d s nach der letzten Durchsage)" % TAKT_S
    if len(meins) >= STUNDE_MAX:
        return False, "Stundenkontingent erschöpft (%d Durchsagen)" % STUNDE_MAX
    meins.append(jetzt)
    stand[sid] = meins
    stand = {k: [t for t in v if jetzt - t < 3600] for k, v in stand.items()}
    stand = {k: v for k, v in stand.items() if v}
    try:
        os.makedirs(os.path.dirname(_STAND), exist_ok=True)
        tmp = _STAND + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stand, f)
        os.replace(tmp, _STAND)
    except OSError:
        pass
    return True, ""

def _saeubern(text):

    t = re.sub(r"\s+", " ", str(text or "")).strip()
    t = "".join(c for c in t if c.isprintable())
    return t[:MAX_LEN]

def policy_der_sitzung(uid, sid):

    uid = re.sub(r"[^A-Za-z0-9_.-]", "", str(uid or ""))[:64]
    sid = re.sub(r"[^A-Za-z0-9_-]", "", str(sid or ""))[:40]
    if not (uid and sid):
        return None
    try:
        import pn_session_policy as psp
    except ImportError:
        return None
    wurzel = os.path.expanduser("~/.local/share/brainbox-portal/session-policies")
    ordner = os.path.join(wurzel, uid)
    treffer = None
    try:
        for name in sorted(os.listdir(ordner)):
            if name.endswith("-%s.json" % sid):
                treffer = os.path.join(ordner, name)
                break
    except OSError:
        return None
    if not treffer:
        return None
    try:
        with open(treffer, encoding="utf-8") as f:
            roh = json.load(f)
    except (OSError, ValueError):
        return None
    pol = psp.validate(roh)
    boden = os.path.join(ordner, "user-floor.json")
    if os.path.exists(boden):
        try:
            with open(boden, encoding="utf-8") as f:
                pol = psp.apply_floor(pol, json.load(f))
        except (OSError, ValueError):
            pass

    return pol

def darf(policy, kanal):

    try:
        import pn_session_policy as psp
    except ImportError:
        return False
    caps = (policy or {}).get("caps") or {}
    return psp.voice_allows(caps.get("voice_mode"), kanal)

def ansage(text, kanal="sprachsystem", policy=None, sid="?", ziel=None, quelle=None):

    t = _saeubern(text)
    if not t:
        return False, "nichts zu sagen"
    if policy is None:
        return False, "ohne Rechte-Dokument wird nicht gesprochen"
    if not darf(policy, kanal):
        stufe = ((policy.get("caps") or {}).get("voice_mode")) or "aus"
        return False, "nicht erlaubt: Stufe '%s' deckt '%s' nicht" % (stufe, kanal)
    ok, grund = _takt_pruefen(str(sid or "?"))
    if not ok:
        return False, grund

    if quelle:
        t = _saeubern("%s: %s" % (quelle, t))

    try:
        if kanal == "lautsprecher":
            ziele = [ziel] if ziel else list((policy.get("caps") or {}).get("voice_target") or [])
            ziele = [z for z in ziele if str(z).startswith("media_player.")]
            if not ziele:
                return False, ("kein Lautsprecher ausgewählt — in den Rechten der Sitzung unter "
                               "„Über welche Lautsprecher?“ einen auswählen")
            gesagt = []
            for z in ziele:
                _ha("/api/services/tts/speak", {
                    "entity_id": "tts.piper", "media_player_entity_id": z, "message": t})
                gesagt.append(z)
            return True, "gesagt über %s (%d Zeichen)" % (", ".join(gesagt), len(t))

        if kanal == "sprachsystem":
            ent = sprachsystem()
            if not ent:
                return False, "kein Sprachsatellit eingerichtet (NABU_ENTITY fehlt)"

            try:
                _ha("/api/services/assist_satellite/announce",
                    {"entity_id": ent, "message": t, "preannounce": True})
            except urllib.error.HTTPError as e:
                if e.code != 400:
                    raise
                _ha("/api/services/assist_satellite/announce", {"entity_id": ent, "message": t})
            return True, "angesagt (%d Zeichen)" % len(t)

        if kanal == "einhaengen":
            ent = sprachsystem()
            if not ent:
                return False, "kein Sprachsatellit eingerichtet (NABU_ENTITY fehlt)"

            _ha("/api/services/assist_satellite/start_conversation",
                {"entity_id": ent, "start_message": t})
            return True, "Gespräch eröffnet (%d Zeichen)" % len(t)

        return False, "unbekannter Kanal: %r" % (kanal,)
    except Exception as e:
        return False, "Sprachdienst antwortet nicht: %r" % (e,)

def _selftest():

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pn_session_policy as psp
    fehler = []

    def gleich(was, ist, soll):
        if ist != soll:
            fehler.append("%s: %r statt %r" % (was, ist, soll))

    aus = psp.validate({"preset": "minimal", "caps": {}})
    laut = psp.validate({"preset": "minimal", "caps": {"voice_mode": "lautsprecher"}})
    spr = psp.validate({"preset": "minimal", "caps": {"voice_mode": "sprachsystem"}})
    ein = psp.validate({"preset": "minimal", "caps": {"voice_mode": "einhaengen"}})

    gleich("aus darf nichts", darf(aus, "lautsprecher"), False)
    gleich("aus darf nicht ansagen", darf(aus, "sprachsystem"), False)
    gleich("lautsprecher darf lautsprecher", darf(laut, "lautsprecher"), True)
    gleich("lautsprecher darf NICHT sprachsystem", darf(laut, "sprachsystem"), False)
    gleich("sprachsystem darf beides", darf(spr, "lautsprecher") and darf(spr, "sprachsystem"), True)
    gleich("sprachsystem darf NICHT einhaengen", darf(spr, "einhaengen"), False)
    gleich("einhaengen darf alles", darf(ein, "einhaengen"), True)
    gleich("unbekannter Kanal", darf(ein, "telepathie"), False)

    ok, grund = ansage("hallo", "sprachsystem", policy=aus, sid="probe")
    gleich("ohne Recht wird nicht gesprochen", ok, False)
    ok, grund = ansage("hallo", "sprachsystem", policy=None, sid="probe")
    gleich("ohne Policy wird nicht gesprochen", ok, False)
    ok, grund = ansage("   ", "sprachsystem", policy=ein, sid="probe")
    gleich("leerer Text", ok, False)

    gleich("Saeuberung", _saeubern("a\n\n  b\tc"), "a b c")
    gleich("Deckel", len(_saeubern("x" * 5000)), MAX_LEN)

    global _STAND
    alt = _STAND
    _STAND = "/tmp/sprachtakt-selftest-%d.json" % os.getpid()
    try:
        e1 = _takt_pruefen("t", 1000.0)
        e2 = _takt_pruefen("t", 1001.0)
        e3 = _takt_pruefen("t", 1000.0 + TAKT_S + 1)
        gleich("erster Ruf geht", e1[0], True)
        gleich("zweiter zu schnell", e2[0], False)
        gleich("nach dem Takt wieder", e3[0], True)
    finally:
        try:
            os.unlink(_STAND)
        except OSError:
            pass
        _STAND = alt

    if fehler:
        print("FEHLER:")
        for f in fehler:
            print("  " + f)
        return 1
    print("Selbsttest gruen (%d Pruefungen)" % 16)
    return 0

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    if "--liste" in sys.argv:
        print("Sprachsatellit:", sprachsystem() or "(keiner)")
        for d in lautsprecher():
            print("  %-46s %-12s %s" % (d["id"], d["zustand"],
                                        "" if d["erreichbar"] else "(gerade nicht anwählbar)"))
        sys.exit(0)
    sys.exit("Aufruf: pn_sprachausgabe.py --selftest | --liste")
