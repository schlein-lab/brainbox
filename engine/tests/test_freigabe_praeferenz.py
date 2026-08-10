#!/usr/bin/env python3

import os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from pnlib import db

_fail = [0]
def check(cond, msg):
    print(("  ok  " if cond else "FAIL  ") + msg)
    if not cond:
        _fail[0] += 1

def main():
    p = tempfile.mktemp(suffix=".db")
    cx = db.connect(p)

    cb = db.get_task_type(cx, "commission.build")
    check(cb is not None and cb["approval"] == "none",
          "commission.build Default = 'none' (Housekeeping, keine Freigabe)")
    check(cb is not None and int(cb["needs_confirmation"]) == 0,
          "commission.build needs_confirmation = 0 (läuft sofort)")
    cr = db.get_task_type(cx, "commission.run")
    check(cr is not None and cr["approval"] == "none",
          "commission.run (K5-B Portal-Bahn) existiert, Default 'none'")
    di = db.get_task_type(cx, "deploy.irreversible")
    check(di is not None and di["approval"] == "pre",
          "deploy.irreversible bleibt ECHTER PRE-Gate (unverändert)")

    tt = {r["name"] for r in cx.execute("PRAGMA table_info(principal_approval_prefs)")}
    check({"principal", "task_type", "approval"} <= tt, "principal_approval_prefs-Tabelle vorhanden")

    check(db.get_approval_prefs(cx, "web:owner") == {}, "frisch: keine Präferenzen")
    r = db.set_approval_pref(cx, "web:owner", "commission.build", "pre")
    check(r.get("ok") and r.get("approval") == "pre", "setzt commission.build -> pre")
    check(db.get_approval_prefs(cx, "web:owner") == {"commission.build": "pre"},
          "get liest die gesetzte Präferenz zurück")

    check(db.get_approval_prefs(cx, "web:kind") == {}, "Präferenz ist self-scoped (fremder Nutzer leer)")

    bad = db.set_approval_pref(cx, "web:owner", "commission.build", "vielleicht")
    check(not bad.get("ok"), "ungültiger Wert fail-closed abgelehnt")
    check(db.get_approval_prefs(cx, "web:owner") == {"commission.build": "pre"},
          "abgelehntes set ließ die alte Präferenz unangetastet")

    cl = db.set_approval_pref(cx, "web:owner", "commission.build", "")
    check(cl.get("ok") and cl.get("approval") is None, "leerer Wert löscht den Override")
    check(db.get_approval_prefs(cx, "web:owner") == {}, "nach dem Löschen keine Präferenz mehr")

    check(db.effective_approval(cx, "web:owner", "commission.build", "none") == "none",
          "ohne Präferenz: Default 'none'")

    db.set_approval_pref(cx, "web:owner", "commission.build", "pre")
    check(db.effective_approval(cx, "web:owner", "commission.build", "none") == "pre",
          "Präferenz 'pre' auf Housekeeping (Default none) GREIFT -> pre")

    db.set_approval_pref(cx, "web:owner", "commission.build", "none")
    check(db.effective_approval(cx, "web:owner", "commission.build", "none") == "none",
          "Präferenz 'none' auf Default none -> none (voll togglebar)")

    db.set_approval_pref(cx, "web:owner", "deploy.irreversible", "none")
    check(db.effective_approval(cx, "web:owner", "deploy.irreversible", "pre") == "pre",
          "Präferenz 'none' kann echten PRE-Gate NICHT schwächen -> bleibt pre")

    db.set_approval_pref(cx, "web:owner", "deploy.irreversible", "pre")
    check(db.effective_approval(cx, "web:owner", "deploy.irreversible", "pre") == "pre",
          "Präferenz 'pre' auf pre-Default -> pre")

    cx.close()
    try:
        os.unlink(p)
    except OSError:
        pass
    print(("\nALLE GRÜN" if _fail[0] == 0 else "\n%d FEHLGESCHLAGEN" % _fail[0]))
    sys.exit(1 if _fail[0] else 0)

if __name__ == "__main__":
    main()
