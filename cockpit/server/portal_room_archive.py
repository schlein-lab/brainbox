

import sys

def archiviere(*_a, **_kw):

    import portal_archive
    b = portal_archive.selbstarchivierung()
    return {"gesehen": b["geprueft"], "archiviert": b["archiviert"], "mb_frei": 0,
            "hinweis": "portal_room_archive loescht nichts mehr — portal_archive hat uebernommen"}

def main():
    sys.stderr.write(
        "Archivieren heisst nicht loeschen.\n"
        "portal_room_archive ist stillgelegt (01.08.2026): es loeschte Zell-Deltas und nannte das\n"
        "Archivieren. Zustaendig ist jetzt portal_archive — es verschiebt, loescht nie und deckt\n"
        "Portal, Medienserver und Telegram zugleich ab:\n"
        "    python3 cockpit/server/portal_archive.py --automatik [--trocken]\n"
        "    python3 cockpit/server/portal_archive.py --bericht\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
