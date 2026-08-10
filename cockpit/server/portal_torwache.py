#!/usr/bin/env python3

import posixpath

OFFEN_GET = {
    "/login":            "die Anmeldeseite selbst — ohne sie kann sich niemand anmelden",
    "/portal-nav.js":    "Navigation, die auch die Anmeldeseite laedt",
    "/brainbox-ca.crt":  "das CA-Zertifikat holt man, BEVOR man dem Portal vertrauen kann",
    "/trust":            "die Anleitung dazu",
    "/reset":            "Kennwort vergessen — per Bauart vor der Anmeldung",
    "/msg/optout":       "Abmeldelink aus einer Nachricht; der Empfaenger hat kein Konto",
    "/api/auth/verify":  "Bestaetigungslink aus der E-Mail",
    "/pair":             "Geraete-Kopplung: das Geraet hat noch keine Sitzung",
    "/api/pair/info":    "dasselbe, lesende Seite",
    "/api/status":       "⚠️ VIP-Wache: /usr/local/bin/brainbox-portal-health fragt genau das. "
                         "Antwortet jedem 200 und reichert nur fuer Angemeldete an.",
    "/favicon.ico":      "Symbol der Anmeldeseite",
    "/favicon-32.png":   "Symbol der Anmeldeseite",
    "/favicon-512.png":  "Symbol der Anmeldeseite",

    "/api/passkey/available": "die Anmeldeseite fragt VOR der Anmeldung, ob Passkey angeboten wird",
}

OFFEN_GET_UNTER = {
    "/static/": "Stil und Schrift der Anmeldeseite",
    "/brand/":  "Logo der Anmeldeseite — NUR Bilder, der Typfilter sitzt in der Route",
    "/api/v1/": "oeffentliche Schnittstelle mit EIGENER Schluesselpruefung (problem+json)",
}

OFFEN_POST = {
    "/api/login":            "die Anmeldung selbst",
    "/api/logout":           "Abmelden muss auch mit abgelaufener Sitzung gehen",
    "/api/auth/forgot":      "Kennwort vergessen",
    "/api/auth/reset":       "Kennwort neu setzen — der Aufrufer hat definitionsgemaess keine Sitzung",
    "/api/register/request": "Konto beantragen",
    "/api/pair":             "einmalige Geraete-Kopplung; eigener, strengerer Riegel im Handler",
    "/api/companion/onbehalf":   "Off-LAN-Messenger-Tuer; eigener, strengerer Riegel im Handler: "
                             "nur 127.0.0.1/::1 + Shared-Secret + did->principal aus der Bindung.",
    "/api/identity/bootstrap": "der ERSTE Owner-Schluessel — zu dem Zeitpunkt gibt es keine "
                               "Anmeldung, gegen die man sich ausweisen koennte. Eigener Riegel: "
                               "nur 127.0.0.1/::1 und nur solange kein aktiver Owner existiert.",
    "/mcp":                  "MCP-Server, zustandslos, mit EIGENER Schluesselpruefung je Werkzeug",

    "/api/passkey/login/begin":  "Biometrie-Anmeldung, Schritt 1 — vor der Anmeldung",
    "/api/passkey/login/finish": "Biometrie-Anmeldung, Schritt 2 — SETZT die Sitzung erst",
}

OFFEN_POST_UNTER = {
    "/api/v1/": "oeffentliche Schnittstelle mit EIGENER Schluesselpruefung (problem+json)",
}

MASCHINE = ("/api/", "/ws/", "/live/", "/mcp")

def _sauber(pfad):

    if not pfad.startswith("/"):
        return False
    return posixpath.normpath(pfad) == pfad.rstrip("/") or posixpath.normpath(pfad) == pfad

def _tabellen(methode):
    m = (methode or "").upper()
    if m == "GET":
        return OFFEN_GET, OFFEN_GET_UNTER
    if m == "POST":
        return OFFEN_POST, OFFEN_POST_UNTER
    return {}, {}

def grund(methode, pfad):

    pfad = (pfad or "").split("?", 1)[0]
    if not _sauber(pfad):
        return None
    genau, unter = _tabellen(methode)
    if pfad in genau:
        return genau[pfad]
    for p, g in unter.items():
        if pfad == p.rstrip("/") or pfad.startswith(p):
            return g
    return None

def frei(methode, pfad):

    return grund(methode, pfad) is not None

def art(pfad):

    pfad = (pfad or "").split("?", 1)[0]
    return "json" if pfad.startswith(MASCHINE) else "seite"

def alle():

    raus = []
    for m, (genau, unter) in (("GET", (OFFEN_GET, OFFEN_GET_UNTER)),
                              ("POST", (OFFEN_POST, OFFEN_POST_UNTER))):
        raus += [(m, p, g, "genau") for p, g in sorted(genau.items())]
        raus += [(m, p, g, "unter") for p, g in sorted(unter.items())]
    return raus

if __name__ == "__main__":
    print("Tueren ohne Anmeldung — %d Stueck\n" % len(alle()))
    for m, p, g, art_ in alle():
        print("%-4s %-28s %s\n     %s" % (m, p + ("*" if art_ == "unter" else ""), "", g))
