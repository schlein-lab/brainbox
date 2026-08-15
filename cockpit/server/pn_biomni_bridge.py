#!/usr/bin/env python3

import os
import sys
import signal

sys.path.insert(0, os.path.expanduser("~/.local/bin"))
import pn_cell_session as pcs

UID = os.environ.get("PN_BIOMNI_UID", "owner")
SID = os.environ.get("PN_BIOMNI_SID", "biomni")
MEM = os.environ.get("PN_BIOMNI_MEM_MB", "2048")
SESSION = "biomni-" + SID

BANNER = (
    "\r\n\033[1m🧪 Biomni runtime\033[0m — biomedizinischer Agent in isolierter Zelle\r\n"
    "   durables Runtime-Image · Data-Lake read-only · LLM keylos über den Max-Pool · kein Netz\r\n"
    "   Tippe deine Aufgabe und Enter.  'exit' beendet die Session.\r\n\r\n")

def _w(s):
    sys.stdout.write(s)
    sys.stdout.flush()

def main():
    mgr = pcs.get_manager()
    _w(BANNER)
    _w("… starte isolierte Biomni-Zelle (erster Start dauert ein paar Sekunden) …\r\n")
    try:
        cell = mgr.ensure(UID, SESSION, policy={"runtime": "biomni", "mem_mb": MEM})
    except Exception as e:
        _w("Zelle konnte nicht starten: %r\r\n" % e)
        return
    if not cell.alive():
        _w("Zelle nicht lebendig (Boot fehlgeschlagen). Bitte Session neu starten.\r\n")
        return
    _w("✓ Zelle bereit (cid=%s).\r\n\r\n" % cell.cid)

    def _cleanup(*_a):
        try:
            mgr.stop(UID, SESSION)
        except Exception:
            pass

    signal.signal(signal.SIGTERM, lambda *a: (_cleanup(), os._exit(0)))

    try:
        while True:
            _w("\033[1mbiomni›\033[0m ")
            line = sys.stdin.readline()
            if not line:
                break
            prompt = line.strip()
            if not prompt:
                continue
            if prompt in ("exit", "quit", ":q"):
                break
            _w("  … denke nach & rechne …\r\n")
            try:
                ans = cell.ask_biomni(prompt, timeout=600)
            except Exception as e:
                ans = "(Fehler: %r)" % e
            _w("\r\n" + (ans or "(leer)") + "\r\n\r\n")
    except KeyboardInterrupt:
        pass
    finally:
        _cleanup()
        _w("\r\nBiomni-Session beendet.\r\n")

if __name__ == "__main__":
    main()
