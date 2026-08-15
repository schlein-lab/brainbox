#!/usr/bin/env python3

from __future__ import annotations

import copy
import json
import os

TRI = ("allow", "ask", "deny")

CLASS_PREFIX = "class:"
DEVICE_CLASSES = [
    {"key": "printer",   "label": "Drucker",                "kinds": ("printer",)},
    {"key": "display",   "label": "Bildschirme & Fernseher", "kinds": ("display", "tv", "cast", "nest-hub", "screen")},
    {"key": "speaker",   "label": "Lautsprecher & Sprachgeräte", "kinds": ("speaker", "voice")},
    {"key": "smarthome", "label": "Smarthome",              "kinds": ("hub", "sensor", "light", "switch",
                                                                      "thermostat", "climate", "cover")},
    {"key": "computer",  "label": "Rechner im Netz",        "kinds": ("host", "computer", "worker")},
    {"key": "input",     "label": "Tastatur & Maus",        "kinds": ("input-keyboard", "input-mouse",
                                                                      "input-trackball")},
]
_CLASS_KINDS = {c["key"]: set(c["kinds"]) for c in DEVICE_CLASSES}

_DEVICE_PROVIDER = None

def set_device_provider(fn):

    global _DEVICE_PROVIDER
    _DEVICE_PROVIDER = fn

_DISPLAY_PROVIDER = None

def set_display_provider(fn):

    global _DISPLAY_PROVIDER
    _DISPLAY_PROVIDER = fn

_OWN_SHARE_PROVIDER = None

def set_own_share_provider(fn):

    global _OWN_SHARE_PROVIDER
    _OWN_SHARE_PROVIDER = fn

def own_share_paths(principal, kind, sid):

    fn = _OWN_SHARE_PROVIDER
    if not callable(fn):
        return []
    try:
        return [str(p) for p in (fn(principal, kind, sid) or []) if p]
    except Exception:
        return []

def live_displays():
    fn = _DISPLAY_PROVIDER
    if not callable(fn):
        return []
    try:
        return list(fn() or [])
    except Exception:
        return []

def resolve_displays(entries):

    disps = live_displays()
    index = {}
    for d in disps:
        for alias in (d.get("id"), d.get("name"), d.get("label")):
            if alias:
                index[str(alias).strip().lower()] = d
    out, seen = [], set()

    def _emit(tok):
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)

    for e in (entries or []):
        s = str(e)
        if s == "*":
            _emit("*")
            continue
        d = index.get(s.strip().lower())
        if d:
            for alias in (d.get("id"), d.get("name"), d.get("label")):
                if alias:
                    _emit(str(alias))
        else:
            _emit(s)
    return out

def live_devices():

    fn = _DEVICE_PROVIDER
    if not callable(fn):
        return []
    try:
        return list(fn() or [])
    except Exception:
        return []

def class_of(kind):

    for key, kinds in _CLASS_KINDS.items():
        if kind in kinds:
            return key
    return None

def resolve_devices(entries):

    out, seen = [], set()
    for e in (entries or []):
        s = str(e)
        if not s.startswith(CLASS_PREFIX):
            if s not in seen:
                seen.add(s)
                out.append(s)
            continue
        want = _CLASS_KINDS.get(s[len(CLASS_PREFIX):])
        if not want:
            continue
        for d in live_devices():
            did, kind = str(d.get("id") or ""), str(d.get("kind") or "")
            if did and kind in want and did not in seen:
                seen.add(did)
                out.append(did)
    return out

VOICE_MODES = ("aus", "lautsprecher", "sprachsystem", "einhaengen")
_VOICE_RANK = {m: i for i, m in enumerate(VOICE_MODES)}

VOICE_LABELS = {
    "aus":          "Aus — die Sitzung kann nichts hörbar machen",
    "lautsprecher": "Durchsagen über einen Lautsprecher (Text zu Sprache)",
    "sprachsystem": "Durchsagen auch über das Sprachsystem — ohne die Sprachbedienung zu übernehmen",
    "einhaengen":   "Darf sich proaktiv als Sprachsitzung einhängen (übernimmt die Sprachbedienung)",
}

VOICE_HINTS = {
    "aus":          "Standard. Meldungen laufen weiter über Portal und Chat, nur eben nicht laut.",
    "lautsprecher": "Nutzt einen der unten ausgewählten Lautsprecher. Kein Sprachsystem nötig.",
    "sprachsystem": "Der Satellit sagt den Text an und geht danach zurück in den Ruhezustand. "
                    "Es entsteht KEIN Gespräch: die Sitzung kann darüber keine Antwort empfangen.",
    "einhaengen":   "Mächtig: was du danach sagst, geht an diese Sitzung. Nur, wenn sie wirklich "
                    "mit dir sprechen soll.",
}

_VOICE_CHANNEL_MIN = {"lautsprecher": "lautsprecher", "sprachsystem": "sprachsystem",
                      "einhaengen": "einhaengen"}

def migrate_voice_mode(v):

    key = str(v).strip().lower()
    return key if key in _VOICE_RANK else "aus"

def voice_allows(mode, channel):

    noetig = _VOICE_CHANNEL_MIN.get(str(channel).strip().lower())
    if noetig is None:
        return False
    return _VOICE_RANK.get(migrate_voice_mode(mode), 0) >= _VOICE_RANK[noetig]

def live_speakers():

    want = _CLASS_KINDS.get("speaker") or set()
    out = []
    for d in live_devices():
        if str(d.get("kind") or "") in want:
            out.append({"id": str(d.get("id") or ""),
                        "label": str(d.get("label") or d.get("name") or d.get("id") or ""),
                        "kind": str(d.get("kind") or "")})
    return [d for d in out if d["id"]]

PORTAL_VERB_LABELS = {
    "summon_lens":   "Ansicht auf dem Bildschirm umschalten",
    "browser_open":  "Webseite öffnen",
    "browser_click": "Im Browser klicken",
    "browser_type":  "Im Browser etwas eintippen",
    "queue_list":    "Warteschlange ansehen",
    "queue_add":     "Auftrag in die Warteschlange legen",
    "app_open":      "Programm öffnen",
    "client_input":  "Tastatur und Maus fernsteuern",
    "displays_show": "Etwas auf einer Anzeige zeigen",
    "hpc_submit": "Rechenauftrag an den externen Cluster schicken",
    "hpc_status": "Stand der externen Rechenaufträge abfragen",
    "hpc_fetch": "Eigene Ergebnis-/Ausgabedatei vom externen Cluster zurücklesen",
    "hpc_ctl": "Ein Kontroll-/Überwachungskommando auf dem Cluster-Login-Knoten ausführen",
    "hpc_slurmwatch": "Nachsehen, ob der Cluster-Melder läuft (Starten nur der Owner)",
}

CATALOG = [
    {"group": "dateien", "label": "Dateizugriff außerhalb der Sitzung", "items": [
        {"key": "fs_read",  "label": "Lesepfade (nur lesen)",  "kind": "paths",
         "hint": "Jeder Ordner wird der Sitzung einzeln bereitgestellt, z. B. /data/projekte"},
        {"key": "fs_write", "label": "Schreibpfade (lesen+schreiben)", "kind": "paths",
         "hint": "Vorsicht: die Sitzung kann diese Dateien ändern/löschen"},
    ]},

    {"group": "ausfuehrung", "label": "Ausführung", "items": [
        {"key": "exec_in_cell", "label": "Befehle in der Sitzung ausführen", "kind": "tri",
         "hint": "Läuft in der eigenen virtuellen Maschine der Sitzung — nie auf der Box selbst"},
    ]},
    {"group": "netz", "label": "Netz & Suche", "items": [
        {"key": "websearch",  "label": "Websuche", "kind": "tri"},
        {"key": "webfetch",   "label": "Webseiten abrufen", "kind": "tri"},
        {"key": "net_hosts",  "label": "Erlaubte Hosts/Domains", "kind": "list",
         "hint": "Leer = alle (bei Erlauben); sonst nur diese, z. B. *.wikipedia.org"},
        {"key": "net_general","label": "Allgemeines Internet (alles)", "kind": "tri",
         "hint": "Voller Internetzugang der Sitzung — Standard: aus"},
        {"key": "net_deny",   "label": "Gesperrte Hosts/Domains (schlaegt jeden Grant)", "kind": "list",
         "hint": "Greift vor allen Freigaben und auch bei allgemeinem Internet. Suffixregel wie bei den erlaubten Hosts."},
        {"key": "net_internal","label": "Internes Netz / LAN & loopback (Voll-Entsandbox)", "kind": "tri",
         "hint": "Hebt die LAN/loopback-Sperre auf — die Sitzung darf auch Box, Router und andere Geräte "
                 "im Heimnetz erreichen. NUR wenn der Admin es will (alles prinzipiell erreichbar). Standard: aus"},
        {"key": "net_serve",  "label": "Eingehende Verbindungen (Server/VNC anbieten)", "kind": "tri"},
    ]},
    {"group": "anzeigen", "label": "Anzeigen & Bildschirme", "items": [
        {"key": "displays",   "label": "Erlaubte Anzeigen", "kind": "displays",
         "hint": "Auf welche benannten Anzeigen darf die Sitzung etwas zeigen (Fernseher, Kiosk, lokal …)"},
        {"key": "vnc",        "label": "Bildschirm der Sitzung freigeben", "kind": "tri",
         "hint": "Live-Blick in die laufende Sitzung"},
    ]},
    {"group": "modell", "label": "Modell (LLM)", "items": [
        {"key": "llm",        "label": "Modellzugang", "kind": "tri",
         "hint": "Über den Broker der Box (Abo, kein Schlüssel in der Sitzung)"},
        {"key": "llm_max_turns", "label": "Max. Runden pro Anfrage", "kind": "int", "default": 25},
    ]},
    {"group": "portal", "label": "Aktionen im Portal", "items": [
        {"key": "portal_state",  "label": "Zustand der Box lesen", "kind": "tri",
         "hint": "Welche Sitzungen, Geräte und Aufträge es gerade gibt — nur lesen, nichts ändern"},
        {"key": "portal_verbs",  "label": "Erlaubte Aktionen", "kind": "list",
         "hint": "Jede Aktion einzeln freigeben — leer heißt: keine davon",
         "labels": PORTAL_VERB_LABELS,
         "options": [{"key": k, "label": v} for k, v in sorted(PORTAL_VERB_LABELS.items())]},
        {"key": "portal_displays_show", "label": "Etwas auf einer Anzeige zeigen", "kind": "tri"},
    ]},
    {"group": "effekte", "label": "Ausgehende Aktionen (unumkehrbar)", "items": [
        {"key": "fx_email",   "label": "E-Mail senden", "kind": "tri"},
        {"key": "fx_publish", "label": "Veröffentlichen/Posten", "kind": "tri"},

        {"key": "fx_pay",     "label": "Zahlen/Bestellen", "kind": "tri"},
        {"key": "fx_msg",     "label": "Nachrichten senden (Telegram/Teams …)", "kind": "tri"},
    ]},
    {"group": "geraete", "label": "Geräte", "items": [
        {"key": "devices", "label": "Verbundene Geräte", "kind": "devices",
         "hint": "Welche Geräte die Sitzung nutzen darf (Drucker, Lautsprecher, Fernseher, Smarthome …). "
                 "Die Liste sind die Geräte, die diese Box wirklich gefunden hat — ist sie leer, wurde "
                 "noch keines gefunden.",
         "classes": [{"key": c["key"], "label": c["label"], "token": CLASS_PREFIX + c["key"],
                      "kinds": list(c["kinds"])} for c in DEVICE_CLASSES]},
        {"key": "device_connect", "label": "Neue Geräte verbinden/koppeln", "kind": "tri",
         "hint": "Discovery + Pairing NEUER Geräte (Gerät-Empowerment) — mächtig; Standard: aus"},
        {"key": "dev_mic",     "label": "Mikrofon", "kind": "tri", "hint": "Datenschutz-sensibel"},
        {"key": "dev_camera",  "label": "Kamera", "kind": "tri", "hint": "Datenschutz-sensibel"},
    ]},

    {"group": "sprache", "label": "Sprachausgabe & Durchsagen", "items": [
        {"key": "voice_mode", "label": "Darf die Sitzung hörbar etwas mitteilen?", "kind": "enum",
         "choices": list(VOICE_MODES), "default": "aus",
         "labels": dict(VOICE_LABELS),
         "options": [{"key": m, "label": VOICE_LABELS[m], "hint": VOICE_HINTS[m]} for m in VOICE_MODES],
         "hint": "Eine Durchsage wird gesprochen, danach ist Ruhe — die Sitzung kann darüber KEINE "
                 "Antwort empfangen. Einhängen ist etwas anderes: die Sitzung übernimmt die "
                 "Sprachbedienung, und was du sagst, geht dann an sie."},
        {"key": "voice_target", "label": "Über welche Lautsprecher?", "kind": "speakers",
         "hint": "Die Liste sind die Lautsprecher und Sprachgeräte, die diese Box wirklich gefunden "
                 "hat (Sonos, Alexa, Sprachsatellit, Systemlautsprecher …). Nichts ausgewählt = "
                 "der Systemlautsprecher der Box. Greift ab der Stufe mit dem Lautsprecher."},
    ]},
    {"group": "geheimnisse", "label": "Geheimnisse & Umgebung", "items": [
        {"key": "secrets",    "label": "Benannte Geheimnisse (JIT, einzeln)", "kind": "list",
         "hint": "Nie der ganze Tresor; jedes Geheimnis einzeln benennen"},
    ]},

    {"group": "externes_rechnen", "label": "Externes Rechnen", "items": [
        {"key": "hpc_submit", "label": "Rechenaufträge an einen externen Rechen-Cluster schicken",
         "kind": "tri",
         "hint": "Große Berechnungen laufen nicht auf der Box, sondern auf einem Rechen-Cluster "
                 "(z. B. an einer Universität). Die Box schickt den Auftrag hin und fragt den Stand ab."},
    ]},

    {"group": "lokales_rechnen", "label": "Rechenauftrag an die Box (außerhalb der Zelle)", "items": [
        {"key": "compute_offload", "label": "Große Berechnungen an den Box-Governor geben", "kind": "tri",
         "hint": "Ein schweres Werkzeug (z. B. ein Genom-Assembler, der zig GB braucht) läuft nicht in der "
                 "kleinen Zelle (dort OOM), sondern als eingehegter, netz-isolierter, gedeckelter Job auf "
                 "der Box. Nur mit Obergrenzen unten. Standard: aus."},
        {"key": "compute_mem_max_mib", "label": "Max. Arbeitsspeicher je Job (MiB)", "kind": "int", "default": 0},
        {"key": "compute_cpu_max_pct", "label": "Max. CPU je Job (% eines Kerns, 100=1 Kern)", "kind": "int", "default": 0},
        {"key": "compute_timeout_max_s", "label": "Max. Laufzeit je Job (Sekunden)", "kind": "int", "default": 0},
        {"key": "compute_max_concurrent", "label": "Max. gleichzeitige Jobs dieser Sitzung", "kind": "int", "default": 0},
    ]},
    {"group": "autonomie", "label": "Bestätigung & Grenzen", "items": [
        {"key": "orchestrate", "label": "Darf eigene Sub-Sessions starten (Orchestrator)", "kind": "tri",
         "hint": "Diese Sitzung darf selbst weitere, vollwertige Sessions als eigene microVM-Zellen "
                 "spawnen und steuern (Dauer-/Orchestrierungsaufgaben). Aus = kann keine Sessions starten. "
                 "Jede Kind-Session belegt RAM der Box — es passen nur ein paar Zellen, der Rest wartet."},
        {"key": "phantom", "label": "Darf Programme steuern & Bildschirm lesen (phantom)", "kind": "tri",
         "hint": "Diese Sitzung erhält phantom-Kräfte über governte, protokollierte MCP-Werkzeuge: einen "
                 "echten Bildschirm SEMANTISCH lesen und Programme bedienen (Formulare ausfüllen, Knöpfe "
                 "drücken). JEDER Aufruf ist eine Aktion durch die Queue, deny-by-default und auditiert — "
                 "kein Ausbruch aus der Zelle. Aus = keine solchen Kräfte."},
        {"key": "autonomy",   "label": "Bestätigungsstufe (2FA)", "kind": "enum",
         "choices": ["streng", "standard", "frei"], "default": "standard",
         "hint": "Wie viel du per Handy-Code (2FA) bestätigst: „Streng“ = jede Änderung, "
                 "„Standard“ = Löschen/Senden, „Frei“ = nur Aktionen nach außen. Ändert NICHT, "
                 "wie der Agent in seiner Zelle arbeitet — nur wie viel du bestätigst."},
        {"key": "mem_mb",     "label": "Arbeitsspeicher (MB)", "kind": "int", "default": 1536},
        {"key": "token_budget", "label": "Token-Budget (0 = unbegrenzt)", "kind": "int", "default": 0},
    ]},
]

HARD_DENY_VERBS = ("vpn_connect", "vpn_down", "vpn_infra", "pn_apply", "shutdown", "reboot",
                   "terminal_run", "terminal_read")
HARD_RAILS = {"vault_all": "deny"}

AUTONOMY_CHOICES = ("streng", "standard", "frei")

def migrate_autonomy(v):

    key = str(v).strip().lower()
    return {"l0": "streng", "l1": "streng", "l2": "standard", "l3": "standard",
            "l4": "frei", "l5": "frei",
            "streng": "streng", "standard": "standard", "frei": "frei"}.get(key, "streng")

HPC_VERBS = ("hpc_submit", "hpc_status", "hpc_fetch", "hpc_ctl",

             "hpc_slurmwatch")
HPC_VERBS = HPC_VERBS

ORCHESTRATE_VERBS = ("session_spawn", "session_status", "session_tell",

                     "session_transcript", "session_watch",

                     "session_broadcast", "session_stop",

                     "session_resize",

                     "session_restart",

                     "schedule", "schedule_list", "schedule_cancel",
                     "store_status", "store_onboard")

_PRESET_BASE = {
    "fs_read": [], "fs_write": [], "exec_in_cell": "allow",
    "websearch": "deny", "webfetch": "deny", "net_hosts": [],
    "net_general": "deny", "net_internal": "deny", "net_serve": "deny", "net_deny": [],
    "displays": [], "vnc": "deny",
    "llm": "allow", "llm_max_turns": 25, "portal_state": "deny", "portal_verbs": [],
    "portal_displays_show": "deny", "fx_email": "deny", "fx_publish": "deny", "fx_pay": "deny",
    "fx_msg": "deny", "devices": [], "device_connect": "deny", "dev_mic": "deny", "dev_camera": "deny",
    "secrets": [], "hpc_submit": "deny", "orchestrate": "deny", "phantom": "deny", "autonomy": "streng", "mem_mb": 1024, "token_budget": 0,

    "voice_mode": "aus", "voice_target": [],

    "compute_offload": "deny", "compute_mem_max_mib": 0, "compute_cpu_max_pct": 0,
    "compute_timeout_max_s": 0, "compute_max_concurrent": 0,
}

_PRESET_DEFS = {
    "minimal": dict(_PRESET_BASE),

    "kind": dict(_PRESET_BASE, **{
        "portal_verbs": ["ask_owner", "ask_owner_result", "queue_list"],

        "fx_email": "ask", "fx_publish": "ask", "fx_pay": "ask", "fx_msg": "ask",
        "net_hosts": ["crates.io", "static.crates.io", "index.crates.io",
                      "pypi.org", "files.pythonhosted.org",
                      "deb.debian.org", "archive.ubuntu.com", "security.ubuntu.com"],
        "autonomy": "streng", "mem_mb": 1536,
    }),
    "standard": dict(_PRESET_BASE, **{
        "websearch": "deny", "webfetch": "deny",
        "portal_state": "allow",
        "portal_verbs": ["summon_lens", "browser_open", "queue_list", "queue_add",
                         "ask_owner", "ask_owner_result"],
        "portal_displays_show": "ask",
        "displays": ["local"],
        "fx_email": "ask", "fx_msg": "ask",
        "devices": [CLASS_PREFIX + "printer", CLASS_PREFIX + "smarthome"],
        "autonomy": "standard", "mem_mb": 1536,
    }),
    "erweitert": dict(_PRESET_BASE, **{
        "websearch": "allow", "webfetch": "allow", "net_general": "allow",
        "portal_state": "allow",
        "portal_verbs": ["summon_lens", "browser_open", "browser_click", "browser_type",
                         "queue_list", "queue_add", "app_open", "client_input",
                         "ask_owner", "ask_owner_result"],
        "portal_displays_show": "allow",
        "displays": ["*"],
        "vnc": "allow",
        "fx_email": "ask", "fx_msg": "allow", "fx_publish": "allow",
        "devices": ["*"],
        "device_connect": "ask", "dev_mic": "ask",
        "autonomy": "standard", "mem_mb": 2048,
    }),

    "fernzugriff": dict(_PRESET_BASE, **{
        "websearch": "allow", "webfetch": "allow", "net_general": "allow",
        "portal_state": "allow", "portal_verbs": ["queue_list", "queue_add",
                                                  "ask_owner", "ask_owner_result"],
        "portal_displays_show": "deny", "displays": [], "devices": [], "device_connect": "deny",
        "fx_email": "ask", "fx_msg": "ask", "autonomy": "streng", "mem_mb": 1536,
    }),
    "voll": dict(_PRESET_BASE, **{
        "websearch": "allow", "webfetch": "allow", "net_general": "allow", "net_serve": "allow",
        "net_internal": "allow",
        "portal_state": "allow", "portal_verbs": ["*"], "portal_displays_show": "allow",
        "displays": ["*"], "vnc": "allow",
        "fx_email": "allow", "fx_publish": "allow", "fx_pay": "ask", "fx_msg": "allow",
        "devices": ["*"], "device_connect": "allow", "dev_mic": "allow", "dev_camera": "allow",
        "hpc_submit": "allow", "orchestrate": "allow",
        "autonomy": "frei", "mem_mb": 3072,
    }),
}

PRESET_META = {
    "minimal":     {"label": "Nur Assistenz",
                    "desc": "Darf denken und antworten, sonst nichts. Kein Internet."},
    "kind":        {"label": "Kind",
                    "desc": "Für Kinder-Konten: kein freies Internet, keine Geräte oder Anzeigen. "
                            "Arbeiten in der eigenen Sitzung mit Paket-Quellen (cargo/pip/apt); "
                            "alles Weitere fragt die Sitzung über die Freigabe-Lane bei den Eltern an."},
    "standard":    {"label": "Im Haus",
                    "desc": "Alltagsgeräte und lokale Anzeige, Aufträge in der Warteschlange. Kein Internet."},
    "erweitert":   {"label": "Im Haus, erweitert",
                    "desc": "Zusätzlich Bildschirm-Steuerung und alle freigegebenen Geräte. Mit Internet, ohne direkten LAN-Zugriff."},
    "fernzugriff": {"label": "Fernzugriff",
                    "desc": "Von außerhalb: mit Internet und eigenen Aufträgen, keine Geräte im Haus."},
    "voll":        {"label": "Voll",
                    "desc": "Alles erlaubt, inkl. Internet und Heim-LAN. Nur für Wegwerf-Sitzungen."},
}

PRESET_ALIASES = {
    "trusted":   "erweitert",
    "remote":    "fernzugriff",
    "full":      "voll",
    "doktorand": "fernzugriff",
}

def canonical_preset(name):

    if not isinstance(name, str):
        return None
    n = PRESET_ALIASES.get(name, name)
    return n if n in _PRESET_DEFS else None

class _PresetMap(dict):

    def __contains__(self, key):
        return canonical_preset(key) is not None

    def __getitem__(self, key):
        n = canonical_preset(key)
        if n is None:
            raise KeyError(key)
        return dict.__getitem__(self, n)

    def get(self, key, default=None):
        n = canonical_preset(key)
        return dict.__getitem__(self, n) if n is not None else default

PRESETS = _PresetMap(_PRESET_DEFS)

DEFAULT_PRESET = "standard"

ORIGIN_DEFAULT = {"lan": "standard", "offlan": "fernzugriff"}

def default_preset_for_origin(origin):

    return ORIGIN_DEFAULT.get(origin, DEFAULT_PRESET)

def new_policy(preset=DEFAULT_PRESET):
    name = canonical_preset(preset) or DEFAULT_PRESET
    return {"preset": name, "caps": copy.deepcopy(_PRESET_DEFS[name])}

def validate(policy):

    caps = dict(_PRESET_BASE)
    src = (policy or {}).get("caps") or {}
    for k, base in _PRESET_BASE.items():
        v = src.get(k, base)
        if isinstance(base, str):
            if k == "autonomy":
                caps[k] = migrate_autonomy(v)
            elif k == "voice_mode":
                caps[k] = migrate_voice_mode(v)
            else:
                caps[k] = v if v in TRI else base
        elif isinstance(base, int):
            try:
                caps[k] = max(0, min(int(v), 1 << 22))
            except (TypeError, ValueError):
                caps[k] = base
        elif isinstance(base, list):
            if k in ("fs_read", "fs_write"):
                out = []
                for row in (v if isinstance(v, list) else []):
                    if isinstance(row, dict) and isinstance(row.get("path"), str) and row["path"].startswith("/"):
                        out.append({"path": os.path.normpath(row["path"]),
                                    "mode": "rw" if row.get("mode") == "rw" else "ro"})
                    elif isinstance(row, str) and row.startswith("/"):
                        out.append({"path": os.path.normpath(row), "mode": "ro"})
                caps[k] = out[:32]
            else:
                caps[k] = [str(x)[:200] for x in (v if isinstance(v, list) else []) if str(x).strip()][:64]

    caps["portal_verbs"] = [x for x in caps["portal_verbs"] if x not in HARD_DENY_VERBS]
    raw = (policy or {}).get("preset")

    if raw == "doktorand" and "hpc_submit" not in src:
        caps["hpc_submit"] = "allow"
    return {"preset": canonical_preset(raw) or "custom", "caps": caps}

def apply_floor(policy, floor, strict_lists=False, keep=None):

    if not floor:
        return policy
    rank = {"deny": 2, "ask": 1, "allow": 0}
    behalten = set(str(p) for p in (keep or []) if p)
    caps = dict(policy["caps"]); f = floor.get("caps") or floor
    for k, v in f.items():
        cur = caps.get(k)
        if k == "voice_mode":

            caps[k] = min(migrate_voice_mode(cur), migrate_voice_mode(v),
                          key=lambda m: _VOICE_RANK[m])
        elif isinstance(cur, str) and v in TRI and cur in TRI:
            caps[k] = v if rank[v] > rank[cur] else cur
        elif isinstance(cur, int) and isinstance(v, int) and v > 0:
            caps[k] = min(cur, v)
        elif isinstance(cur, list) and isinstance(v, list) and (v or strict_lists):
            if "*" in [str(x) for x in v]:
                continue
            if k == "devices":
                cur, v = resolve_devices(cur), resolve_devices(v)
            names = [(x.get("path") if isinstance(x, dict) else x) for x in cur]

            eigen = behalten if k in ("fs_read", "fs_write") else set()
            if "*" in names:
                caps[k] = list(v)
            else:
                allowed = set(v)
                caps[k] = [x for x in cur
                           if (x.get("path") if isinstance(x, dict) else x) in allowed
                           or (x.get("path") if isinstance(x, dict) else x) in eigen]
    return {"preset": policy.get("preset", "custom"), "caps": caps}

def decide(policy, key, resource=None):

    caps = (policy or {}).get("caps") or {}
    v = caps.get(key)
    if isinstance(v, str):
        return v if v in TRI else "deny"
    if isinstance(v, list):
        if key == "devices":
            v = resolve_devices(v)
        elif key == "displays":
            v = resolve_displays(v)
        if resource is None:
            return "allow" if v else "deny"
        names = [x.get("path") if isinstance(x, dict) else x for x in v]
        if "*" in names:
            return "allow"
        if key in ("fs_read", "fs_write"):
            rp = os.path.normpath(str(resource))
            return "allow" if any(rp == n or rp.startswith(n.rstrip("/") + "/") for n in names if n) else "deny"
        return "allow" if str(resource) in names else "deny"
    return "deny"

def enforcement(policy):

    caps = (policy or {}).get("caps") or {}
    tools_deny = []
    if caps.get("websearch") != "allow":
        tools_deny.append("WebSearch")
    if caps.get("webfetch") != "allow":
        tools_deny.append("WebFetch")
    verbs = list(caps.get("portal_verbs") or [])
    if caps.get("portal_displays_show") == "allow" and "displays_show" not in verbs:
        verbs.append("displays_show")
    if caps.get("hpc_submit") == "allow":
        verbs.extend(v for v in HPC_VERBS if v not in verbs)
    if caps.get("orchestrate") == "allow":
        verbs.extend(v for v in ORCHESTRATE_VERBS if v not in verbs)

    verbs = [v for v in verbs if v not in HARD_DENY_VERBS]
    return {
        "disallowed_tools": tools_deny,

        "portal_enabled": (caps.get("portal_state") == "allow" or bool(verbs)
                           or bool(caps.get("fs_read")) or bool(caps.get("fs_write"))),
        "portal_verbs": verbs,
        "portal_state": caps.get("portal_state", "deny"),
        "displays": resolve_displays(caps.get("displays")),
        "devices": resolve_devices(caps.get("devices")),

        "secrets": [str(x) for x in (caps.get("secrets") or []) if str(x).strip()][:64],
        "hpc_submit": caps.get("hpc_submit", "deny"),
        "orchestrate": caps.get("orchestrate", "deny"),
        "phantom": caps.get("phantom", "deny"),

        "device_connect": caps.get("device_connect", "deny"),
        "dev_mic": caps.get("dev_mic", "deny"),
        "dev_camera": caps.get("dev_camera", "deny"),
        "fs_read": list(caps.get("fs_read") or []),
        "fs_write": list(caps.get("fs_write") or []),
        "net_hosts": list(caps.get("net_hosts") or []),
        "net_general": caps.get("net_general", "deny"),
        "net_internal": caps.get("net_internal", "deny"),

        "net_deny": list(caps.get("net_deny") or []),
        "mem_mb": int(caps.get("mem_mb") or 1536),

        "fx_email": caps.get("fx_email", "deny"),
        "fx_publish": caps.get("fx_publish", "deny"),
        "fx_pay": caps.get("fx_pay", "deny"),
        "fx_msg": caps.get("fx_msg", "deny"),
        "llm": caps.get("llm", "allow"),
        "autonomy": migrate_autonomy(caps.get("autonomy", "standard")),
        "token_budget": int(caps.get("token_budget") or 0),

        "compute_enabled": caps.get("compute_offload") == "allow",
        "compute_mem_max_mib": int(caps.get("compute_mem_max_mib") or 0),
        "compute_cpu_max_pct": int(caps.get("compute_cpu_max_pct") or 0),
        "compute_timeout_max_s": int(caps.get("compute_timeout_max_s") or 0),
        "compute_max_concurrent": int(caps.get("compute_max_concurrent") or 0),
    }

DEVICE_ROSTER = []

def device_roster():

    return list(DEVICE_ROSTER)

class PolicyStore:
    def __init__(self, base_dir):
        self.base = base_dir
        os.makedirs(self.base, exist_ok=True)

    def _p(self, principal, kind, sid, mkdir=True):

        d = os.path.join(self.base, str(principal).replace("/", "_"))
        if mkdir:
            os.makedirs(d, exist_ok=True)
        safe = "%s-%s.json" % (str(kind).replace("/", "_"), str(sid).replace("/", "_"))
        return os.path.join(d, safe)

    def _default_p(self):
        return os.path.join(self.base, "policy-default.json")

    def get_default(self):
        try:
            return validate(json.load(open(self._default_p())))
        except Exception:
            return new_policy(DEFAULT_PRESET)

    def set_default(self, policy):
        pol = validate(policy)
        tmp = self._default_p() + ".tmp"
        json.dump(pol, open(tmp, "w"), indent=1)
        os.replace(tmp, self._default_p())
        return pol

    def get(self, principal, kind, sid):
        try:
            return validate(json.load(open(self._p(principal, kind, sid, mkdir=False))))
        except Exception:
            return self.get_default()

    def set(self, principal, kind, sid, policy):
        pol = validate(policy)
        p = self._p(principal, kind, sid)
        tmp = p + ".tmp"
        json.dump(pol, open(tmp, "w"), indent=1)
        os.replace(tmp, p)
        return pol

    def _floor_p(self, principal):
        d = os.path.join(self.base, str(principal).replace("/", "_"))
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "user-floor.json")

    def get_user_floor(self, principal):
        try:
            f = json.load(open(self._floor_p(principal)))
            return f if isinstance(f, dict) and f.get("caps") else None
        except Exception:
            return None

    def set_user_floor(self, principal, floor):

        if not floor or not (isinstance(floor, dict) and floor.get("caps")):
            try:
                os.remove(self._floor_p(principal))
            except OSError:
                pass
            return None
        clean = {}
        src = floor["caps"]
        for k, base in _PRESET_BASE.items():
            if k not in src:
                continue
            v = src[k]
            if isinstance(base, str):
                if k == "autonomy":
                    clean[k] = migrate_autonomy(v)
                elif v in TRI:
                    clean[k] = v
            elif isinstance(base, int):
                try:
                    clean[k] = max(0, min(int(v), 1 << 22))
                except (TypeError, ValueError):
                    pass
            elif isinstance(base, list):
                clean[k] = [str(x.get("path") if isinstance(x, dict) else x)[:200]
                            for x in (v if isinstance(v, list) else []) if x][:64]
        if not clean:
            return None
        p = self._floor_p(principal)
        json.dump({"caps": clean}, open(p + ".tmp", "w"), indent=1)
        os.replace(p + ".tmp", p)
        return {"caps": clean}

    def _has_own(self, principal, kind, sid):

        try:
            return os.path.exists(self._p(principal, kind, sid))
        except Exception:
            return False

    def effective(self, principal, kind, sid, global_floor=None):

        uf = self.get_user_floor(principal)
        eigen = own_share_paths(principal, kind, sid)
        if uf and not self._has_own(principal, kind, sid):
            pol = validate({"preset": uf.get("preset") or DEFAULT_PRESET,
                            "caps": dict(uf.get("caps") or {})})
        else:
            pol = self.get(principal, kind, sid)
        if global_floor:
            pol = apply_floor(pol, global_floor, keep=eigen)
        if uf:

            pol = apply_floor(pol, uf, strict_lists=True, keep=eigen)
        return pol

def _selftest():
    ok = True
    def ck(name, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print("  [%s] %s" % ("PASS" if cond else "FAIL", name))

    p = new_policy("standard")
    ck("standard preset loads (sealed: search+net off)", p["caps"]["websearch"] == "deny" and p["caps"]["net_general"] == "deny")
    ck("minimal denies search", new_policy("minimal")["caps"]["websearch"] == "deny")

    dirty = {"preset": "standard", "caps": {"websearch": "yolo", "portal_verbs": ["browser_open", "vpn_connect"],
             "fs_read": [{"path": "/data/x", "mode": "rw"}, {"path": "rel/bad"}, "/etc"], "mem_mb": "abc"}}
    v = validate(dirty)
    ck("bad tri clamped", v["caps"]["websearch"] == "deny")
    ck("hard-rail verb stripped", "vpn_connect" not in v["caps"]["portal_verbs"])
    ck("rel path dropped, abs kept", len(v["caps"]["fs_read"]) == 2)
    ck("bad int -> default", v["caps"]["mem_mb"] == 1024)

    pol = validate({"caps": dict(_PRESET_BASE, displays=["local", "kiosk-display-a"],
                                 fs_read=[{"path": "/data/cloud", "mode": "ro"}])})
    ck("display allowed", decide(pol, "displays", "kiosk-display-a") == "allow")
    ck("display denied", decide(pol, "displays", "kiosk-tv-1") == "deny")
    ck("path prefix allowed", decide(pol, "fs_read", "/data/cloud/sub/file.txt") == "allow")
    ck("path outside denied", decide(pol, "fs_read", "/data/cloudX") == "deny")
    ck("path traversal denied", decide(pol, "fs_read", "/data/cloud/../secret") == "deny")

    base = new_policy("trusted")
    floored = apply_floor(base, {"caps": {"net_general": "deny", "mem_mb": 1024, "fx_email": "deny"}})
    ck("floor denies net", floored["caps"]["net_general"] == "deny")
    ck("floor caps mem", floored["caps"]["mem_mb"] == 1024)
    loose = apply_floor(new_policy("minimal"), {"caps": {"websearch": "allow"}})
    ck("floor cannot loosen", loose["caps"]["websearch"] == "deny")

    e = enforcement(new_policy("minimal"))
    ck("minimal disallows WebSearch tool", "WebSearch" in e["disallowed_tools"])
    ck("minimal: portal off", not e["portal_enabled"])
    e2 = enforcement(new_policy("standard"))
    ck("standard: portal on + verbs", e2["portal_enabled"] and "browser_open" in e2["portal_verbs"])

    import tempfile
    st = PolicyStore(tempfile.mkdtemp())
    st.set("owner", "voice", "s1", {"preset": "trusted", "caps": PRESETS["trusted"]})
    got = st.get("owner", "voice", "s1")
    ck("store roundtrip", got["caps"]["autonomy"] == "standard")
    ck("unknown session -> default", st.get("owner", "voice", "nope")["preset"] == DEFAULT_PRESET)

    rem = new_policy("fernzugriff")
    ck("fernzugriff: no local devices", rem["caps"]["devices"] == [] and rem["caps"]["displays"] == [])
    ck("fernzugriff: brain + web on", rem["caps"]["llm"] == "allow" and rem["caps"]["websearch"] == "allow")

    ck("shipped device seed is empty", DEVICE_ROSTER == [] and device_roster() == [])
    for _name, _caps in _PRESET_DEFS.items():
        bad = [d for d in _caps["devices"] if not (d == "*" or str(d).startswith(CLASS_PREFIX))]
        ck("preset %s names no device instance (%r)" % (_name, bad), not bad)
        badd = [d for d in _caps["displays"] if d not in ("*", "local")]
        ck("preset %s names no display instance (%r)" % (_name, badd), not badd)

    ck("no provider -> class grants nothing (fail-closed)",
       decide(new_policy("standard"), "devices", "printer-7") == "deny")
    set_device_provider(lambda: [{"id": "printer-7", "kind": "printer"},
                                 {"id": "tv-42", "kind": "tv"},
                                 {"id": "ha-1", "kind": "hub"}])
    try:
        std = new_policy("standard")
        ck("class:printer matches a live printer", decide(std, "devices", "printer-7") == "allow")
        ck("class:smarthome matches a live hub", decide(std, "devices", "ha-1") == "allow")
        ck("standard does NOT grant the TV", decide(std, "devices", "tv-42") == "deny")
        ck("unknown device denied", decide(std, "devices", "nope") == "deny")
        ck("enforcement expands classes to real ids",
           sorted(enforcement(std)["devices"]) == ["ha-1", "printer-7"])
        ck("erweitert wildcard reaches every device",
           decide(new_policy("erweitert"), "devices", "tv-42") == "allow")
    finally:
        set_device_provider(None)
    ck("provider cleared -> class grants nothing again",
       enforcement(new_policy("standard"))["devices"] == [])

    ck("alias: trusted -> erweitert", canonical_preset("trusted") == "erweitert")
    ck("alias: remote -> fernzugriff", canonical_preset("remote") == "fernzugriff")
    ck("alias: full -> voll", canonical_preset("full") == "voll")
    ck("alias lookup still works", PRESETS["trusted"] is PRESETS["erweitert"])
    ck("old key still 'in' PRESETS", "trusted" in PRESETS and "remote" in PRESETS)
    ck("dropdown lists only current keys",
       sorted(PRESETS.keys()) == ["erweitert", "fernzugriff", "kind", "minimal", "standard", "voll"])
    ck("unknown preset is not a preset", canonical_preset("nonsense") is None)
    old = validate({"preset": "trusted", "caps": dict(_PRESET_DEFS["erweitert"])})
    ck("stored 'trusted' migrates on read", old["preset"] == "erweitert")
    ck("migrated policy keeps its grants", old["caps"]["vnc"] == "allow" and old["caps"]["autonomy"] == "standard")
    ck("legacy L5 autonomy migrates to frei", migrate_autonomy("L5") == "frei" and migrate_autonomy("L0") == "streng")
    ck("garbage autonomy fails safe to streng", migrate_autonomy("???") == "streng" and migrate_autonomy(None) == "streng")

    dok = validate({"preset": "doktorand", "caps": {"portal_verbs": list(HPC_VERBS), "llm": "allow"}})
    ck("doktorand migrates to fernzugriff", dok["preset"] == "fernzugriff")
    ck("doktorand keeps external compute", dok["caps"]["hpc_submit"] == "allow")
    ck("hpc_submit puts the HPC verbs on the bus",
       "hpc_submit" in enforcement(dok)["portal_verbs"])
    ck("no hpc_submit -> no HPC verbs",
       "hpc_submit" not in enforcement(new_policy("standard"))["portal_verbs"])
    ck("doktorand is gone as a preset", "doktorand" not in list(PRESETS.keys()))

    ck("exec_host_terminal removed from the catalog",
       not any(it["key"] == "exec_host_terminal" for g in CATALOG for it in g["items"]))
    ck("exec_host_terminal removed from presets", "exec_host_terminal" not in _PRESET_BASE)
    ck("terminal verbs hard-denied",
       "terminal_run" in HARD_DENY_VERBS and "terminal_read" in HARD_DENY_VERBS)
    revived = validate({"preset": "voll", "caps": {"portal_verbs": ["terminal_run", "terminal_read",
                                                                   "queue_add"]}})
    ck("old policy cannot re-enable the host shell",
       revived["caps"]["portal_verbs"] == ["queue_add"])
    ck("enforcement strips them even unvalidated",
       "terminal_run" not in enforcement({"caps": {"portal_verbs": ["terminal_run"]}})["portal_verbs"])

    permissive = apply_floor(validate({"caps": dict(_PRESET_BASE, devices=["dev-a"])}),
                             {"caps": {"devices": ["*"]}})
    ck("'*' floor does not strip the list", permissive["caps"]["devices"] == ["dev-a"])

    ck("LAN default = standard", default_preset_for_origin("lan") == "standard")
    ck("off-LAN default = fernzugriff", default_preset_for_origin("offlan") == "fernzugriff")
    ck("no dead origins", set(ORIGIN_DEFAULT) == {"lan", "offlan"})

    ck("every preset has a German label",
       all(PRESET_META.get(k, {}).get("label") for k in PRESETS.keys()))

    st.set("bob", "voice", "default", {"preset": "trusted", "caps": PRESETS["trusted"]})
    st.set_user_floor("bob", {"caps": {"devices": [], "portal_displays_show": "deny",
                                       "device_connect": "deny", "autonomy": "L2"}})
    eff = st.effective("bob", "voice", "default")
    ck("user-floor strips devices", eff["caps"]["devices"] == [])
    ck("user-floor denies displays_show", eff["caps"]["portal_displays_show"] == "deny")
    ck("user-floor cannot loosen (still trusted web)", eff["caps"]["websearch"] == "allow")
    st.set_user_floor("bob", None)
    ck("clearing floor restores", st.effective("bob", "voice", "default")["caps"]["portal_displays_show"] == "allow")

    st.set("carol", "voice", "default", {"preset": "full", "caps": PRESETS["full"]})
    st.set_user_floor("carol", {"caps": {"portal_verbs": list(HPC_VERBS), "displays": [], "devices": []}})
    effc = st.effective("carol", "voice", "default")
    ck("wildcard verbs capped to floor", effc["caps"]["portal_verbs"] == list(HPC_VERBS))
    ck("wildcard displays -> none", effc["caps"]["displays"] == [] and effc["caps"]["devices"] == [])

    ck("net_internal deny by default", new_policy("standard")["caps"]["net_internal"] == "deny")
    ck("full = ent-sandbox (net_internal allow)", PRESETS["full"]["net_internal"] == "allow")
    ck("enforcement carries net_internal", enforcement(new_policy("full"))["net_internal"] == "allow")
    ck("enforcement net_internal deny for standard", enforcement(new_policy("standard"))["net_internal"] == "deny")

    kid = new_policy("kind")
    ck("kind: kein freies Netz/Suche",
       kid["caps"]["net_general"] == "deny" and kid["caps"]["net_internal"] == "deny"
       and kid["caps"]["websearch"] == "deny" and kid["caps"]["webfetch"] == "deny")
    ck("kind: keine Geraete/Anzeigen/Geheimnisse",
       kid["caps"]["devices"] == [] and kid["caps"]["displays"] == [] and kid["caps"]["secrets"] == []
       and kid["caps"]["device_connect"] == "deny" and kid["caps"]["dev_mic"] == "deny"
       and kid["caps"]["dev_camera"] == "deny")

    ck("kind: die vier Torfaelle sind ELTERN-Freigaben (ask)",
       kid["caps"]["fx_email"] == "ask" and kid["caps"]["fx_msg"] == "ask"
       and kid["caps"]["fx_publish"] == "ask" and kid["caps"]["fx_pay"] == "ask")
    ck("kind: Eltern-Lane offen (ask_owner)",
       "ask_owner" in kid["caps"]["portal_verbs"] and "ask_owner_result" in kid["caps"]["portal_verbs"])
    ck("kind: strengste Autonomie", kid["caps"]["autonomy"] == "streng")
    ck("kind: kein Orchestrieren/HPC/phantom",
       kid["caps"]["orchestrate"] == "deny" and kid["caps"]["hpc_submit"] == "deny"
       and kid["caps"]["phantom"] == "deny")
    ek = enforcement(kid)
    ck("kind: Paket-Registries als net_hosts geflattet",
       "crates.io" in ek["net_hosts"] and "pypi.org" in ek["net_hosts"]
       and "files.pythonhosted.org" in ek["net_hosts"] and "deb.debian.org" in ek["net_hosts"])
    ck("kind: enforcement haelt Netz+Suche zu",
       ek["net_general"] == "deny" and ek["net_internal"] == "deny"
       and "WebSearch" in ek["disallowed_tools"] and "WebFetch" in ek["disallowed_tools"])
    ck("kind: Broker-Lane an, ask_owner drin",
       ek["portal_enabled"] and "ask_owner" in ek["portal_verbs"] and "ask_owner_result" in ek["portal_verbs"])
    ck("kind: Preset validiert unter eigenem Namen",
       validate({"preset": "kind", "caps": dict(_PRESET_DEFS["kind"])})["preset"] == "kind")
    ck("kind hat deutschen Anzeigenamen", PRESET_META.get("kind", {}).get("label") == "Kind")

    net_defaults = {
        "minimal":     ("deny",  "deny"),
        "kind":        ("deny",  "deny"),
        "standard":    ("deny",  "deny"),
        "erweitert":   ("allow", "deny"),
        "fernzugriff": ("allow", "deny"),
        "voll":        ("allow", "allow"),
    }
    for _pn, (_g, _i) in net_defaults.items():
        _c = new_policy(_pn)["caps"]
        ck("net default %s: net_general=%s"  % (_pn, _g), _c["net_general"]  == _g)
        ck("net default %s: net_internal=%s" % (_pn, _i), _c["net_internal"] == _i)
        _e = enforcement(new_policy(_pn))
        ck("enforcement mirrors %s net_general"  % _pn, _e["net_general"]  == _g)
        ck("enforcement mirrors %s net_internal" % _pn, _e["net_internal"] == _i)

    ck("fernzugriff has internet egress by default", new_policy("fernzugriff")["caps"]["net_general"] == "allow")
    ck("fernzugriff still has no LAN",               new_policy("fernzugriff")["caps"]["net_internal"] == "deny")

    caged = apply_floor(new_policy("voll"), {"caps": {"net_general": "deny"}})
    ck("floor denies net_general even on voll (tighten wins)", caged["caps"]["net_general"] == "deny")
    lan_off = apply_floor(new_policy("voll"), {"caps": {"net_internal": "deny"}})
    ck("floor strips LAN from voll", lan_off["caps"]["net_internal"] == "deny")
    ck("floor leaves untouched knobs alone (voll keeps internet)", lan_off["caps"]["net_general"] == "allow")

    stored_old = validate({"preset": "fernzugriff",
                           "caps": dict(_PRESET_BASE, websearch="allow", webfetch="allow")})
    ck("stored fernzugriff keeps its old net_general=deny (no retro-grant)",
       stored_old["caps"]["net_general"] == "deny")

    print("\nSELFTEST:", "ALL GREEN" if ok else "FAILURES")
    return 0 if ok else 1

if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("pn_session_policy — import me; --selftest to verify.")
