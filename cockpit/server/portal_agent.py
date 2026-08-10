

READ = "read"
LOW = "low"
IRREVERSIBLE = "irreversible"

LENSES = ("chat", "browser", "notes", "screen", "queue", "attach")

TOOLS = [
    {
        "name": "state",
        "description": "Read the current state of THIS user's cell: active lens (if known), open "
                       "seat apps + focused app, the current Screen URL and recently seen links. "
                       "Call this FIRST to resolve 'this/current/it'.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "ceremony_class": READ,
    },
    {
        "name": "summon_lens",
        "description": "Switch the user's visible lens (the one full-screen view). Records a pending "
                       "client action so the browser actually switches; use it to SHOW a result.",
        "input_schema": {"type": "object", "properties": {
            "lens": {"type": "string", "enum": list(LENSES)}}, "required": ["lens"]},
        "ceremony_class": LOW,
    },
    {
        "name": "display_list",
        "description": "List the display targets you can drive: the user's own window ('local') and any "
                       "paired LAN screen (e.g. a Pi-kiosk). Call before showing on a named display.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "ceremony_class": READ,
    },
    {
        "name": "display_show",
        "description": "VOICE-FIRST 'zeig mir X': show a ref (a paper/image/URL/object/the latest reply) on "
                       "a display. display defaults to 'local' (the user's own window); name a kiosk id to "
                       "drive a paired LAN screen. ref = {kind:'url'|'file'|'object'|'session-reply', value}. "
                       "file/object refs are scoped to THIS user. Reversible.",
        "input_schema": {"type": "object", "properties": {
            "display": {"type": "string"},
            "ref": {"type": "object", "properties": {
                "kind": {"type": "string"}, "value": {"type": "string"}, "text": {"type": "string"}}}},
            "required": ["ref"]},
        "ceremony_class": LOW,
    },
    {
        "name": "display_restore",
        "description": "Return a display to its idle content (the Pi-kiosk goes back to its photo loop; the "
                       "local window clears the shown ref). display defaults to 'local'.",
        "input_schema": {"type": "object", "properties": {"display": {"type": "string"}}},
        "ceremony_class": LOW,
    },
    {
        "name": "hpc_status",
        "description": "Check HPC compute status on the HPC cluster. Without job_id it "
                       "lists the user's running/queued SLURM jobs; with a job_id it reports that job. "
                       "If the cluster VPN is not up (only the operator can establish it), it says so.",
        "input_schema": {"type": "object", "properties": {"job_id": {"type": "string"}}},
        "ceremony_class": LOW,
    },
    {
        "name": "hpc_submit",
        "description": "Submit a computation to the HPC cluster via SLURM: either a one-line "
                       "shell 'command' (sbatch --wrap) or a full batch 'script' with an optional 'name'. "
                       "Returns the SLURM job id. Remote compute only — nothing runs on the local box. "
                       "Requires the cluster VPN to be up (operator-established); otherwise it says so.",
        "input_schema": {"type": "object", "properties": {
            "command": {"type": "string"}, "script": {"type": "string"}, "name": {"type": "string"}}},
        "ceremony_class": LOW,
    },
    {
        "name": "hpc_fetch",
        "description": "Read ONE of your own files back from the HPC cluster (a SLURM job's .out/.err, "
                       "a result file on the shared filesystem) into this session, size-capped. Use this "
                       "to retrieve computed results — do NOT invent side-channels. Params: path (required), "
                       "max_kb (optional, default 256, max 1024). Runs on the login node, no compute.",
        "input_schema": {"type": "object", "properties": {
            "path": {"type": "string"}, "max_kb": {"type": "integer"}}, "required": ["path"]},
        "ceremony_class": LOW,
    },
    {
        "name": "hpc_ctl",
        "description": "Run ONE whitelisted control/monitoring command on the HPC LOGIN node and get its "
                       "stdout: squeue/sacct/scontrol/scancel (jobs), ps/kill/pgrep (own process hygiene), "
                       "ls/stat/du/find/cat/head/tail/wc/grep (tiny file + status checks). NEVER compute "
                       "(that goes through hpc_submit=SLURM). No shell chaining/redirection.",
        "input_schema": {"type": "object", "properties": {"command": {"type": "string"}},
                         "required": ["command"]},
        "ceremony_class": LOW,
    },
    {
        "name": "hpc_slurmwatch",
        "description": "Check — or, for the owner, (re)start — the cluster REPORTER (slurmwatch) on the "
                       "HPC login node. It pushes SLURM state to Telegram; while it is down, nobody "
                       "hears about failed or OOM-killed jobs. aktion: 'status' (default, read-only) or "
                       "'start' (owner only, idempotent — does nothing if it already runs). You cannot "
                       "pass a command: the one that runs is fixed in the portal.",
        "input_schema": {"type": "object", "properties": {
            "aktion": {"type": "string", "enum": ["status", "start"]}}},
        "ceremony_class": LOW,
    },
    {
        "name": "browser_open",
        "description": "Open a URL (or a search query) in this cell's Firefox (the Screen browser). "
                       "A bare host gets https://; free text becomes a web search. Cell-scoped.",
        "input_schema": {"type": "object", "properties": {
            "url": {"type": "string"}, "query": {"type": "string"}}},
        "ceremony_class": LOW,
    },

    {
        "name": "app_sense",
        "description": "Sense the focused seat app (title + accessible text, honest a11y_empty when "
                       "the app exposes no tree). Read-only.",
        "input_schema": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["text", "intent", "shot"]}}},
        "ceremony_class": READ,
    },
    {
        "name": "app_drive",
        "description": "Low-stakes app-drive on the focused seat app: click (needs x,y), scroll, type "
                       "(text), enter. Reversible. Blind clicks without coordinates are refused honestly.",
        "input_schema": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["click", "scroll", "type", "enter", "press"]},
            "x": {"type": "number"}, "y": {"type": "number"}, "n": {"type": "integer"},
            "text": {"type": "string"}, "btn": {"type": "string"}}, "required": ["action"]},
        "ceremony_class": LOW,
    },
    {
        "name": "client_input",
        "description": "Send a CLASSIC desktop input action to the user's OWN client window / focused "
                       "control (the app on THEIR PC), which the client executes via OS input. This is "
                       "NOT the box seat app — for the seat use app_drive. Use it for edit/clipboard/key "
                       "actions in whatever the user is looking at locally. verb ∈ copy|cut|paste|select|"
                       "select-all|undo|redo|delete|right-click|context-menu|press|type (German aliases "
                       "accepted: kopieren, ausschneiden, einfügen, markieren, alles-markieren, "
                       "rückgängig, wiederholen, löschen, rechtsklick, kontextmenü, taste, tippen, "
                       "schreiben). args: text (paste/type payload), keys (a chord for press, e.g. "
                       "'ctrl+shift+s' | 'f5' | 'escape'), what (all|line|word for select), x/y "
                       "(optional screen coords for right-click). Reversible.",
        "input_schema": {"type": "object", "properties": {
            "verb": {"type": "string"},
            "text": {"type": "string"}, "keys": {"type": "string"},
            "what": {"type": "string", "enum": ["all", "line", "word"]},
            "x": {"type": "number"}, "y": {"type": "number"}}, "required": ["verb"]},
        "ceremony_class": LOW,
    },
    {
        "name": "files_list",
        "description": "List the files in THIS user's fabric store for an app (default 'notes').",
        "input_schema": {"type": "object", "properties": {"app": {"type": "string"}}},
        "ceremony_class": READ,
    },
    {
        "name": "file_read",
        "description": "Read a file from THIS user's fabric store.",
        "input_schema": {"type": "object", "properties": {
            "app": {"type": "string"}, "name": {"type": "string"}}, "required": ["name"]},
        "ceremony_class": READ,
    },
    {
        "name": "file_write",
        "description": "Write/replace a file in THIS user's fabric store.",
        "input_schema": {"type": "object", "properties": {
            "app": {"type": "string"}, "name": {"type": "string"}, "body": {"type": "string"}},
            "required": ["name", "body"]},
        "ceremony_class": LOW,
    },
    {
        "name": "store_status",
        "description": "NUR mit Orchestrator-Recht: Zustand des Software-Regals - alle zentralen "
                       "Werkzeug-Kisten mit Version, Programmzahl, verifizierten Bedien-Rezepten "
                       "und Einarbeitungs-Status (running/done/error).",
        "input_schema": {"type": "object", "properties": {}},
        "ceremony_class": READ,
    },
    {
        "name": "store_onboard",
        "description": "NUR mit Orchestrator-Recht: beantrage die Einarbeitung einer Regal-Kiste. "
                       "Der HOST startet dafuer eine sichtbare Board-Session, in der ein Agent die "
                       "Programme erkundet und die Bedien-Kartei schreibt - du installierst nichts "
                       "selbst. Laeuft die Einarbeitung bereits, wird ehrlich abgelehnt.",
        "input_schema": {"type": "object", "properties": {
            "kit": {"type": "string"}}, "required": ["kit"]},
        "ceremony_class": LOW,
    },
    {
        "name": "session_spawn",
        "description": "NUR mit Orchestrator-Recht: starte eine eigene, vollwertige Sub-Session als "
                       "isolierte microVM-Zelle, die GENAU EINE Aufgabe erledigt. Gib die Aufgabe "
                       "vollstaendig und in sich abgeschlossen an ('task') — die Sub-Session sieht "
                       "deinen Chat NICHT. Es laufen nur wenige Zellen gleichzeitig (RAM-Budget der "
                       "Box); ist kein Platz, wartet die Aufgabe automatisch. Gibt tid + Zustand "
                       "zurueck. Ohne Orchestrator-Recht abgelehnt. "
                       "'model' waehlt die Stufe des KINDES: Vorgabe 'sonnet' — die traegt den "
                       "Grossteil der Arbeit (Dateien, Skripte, Abrufe, Pruefungen, "
                       "Warteschlangen, Zusammenfassungen, Wiederholungslaeufe). Nimm 'opus' NUR "
                       "dort, wo wirklich fachlich geurteilt wird: ein Paper bewerten, ein "
                       "Verdikt fassen, widerspruechliche Belege abwaegen. Mehr Modell als noetig "
                       "ist kein Vorteil — dasselbe Kontingent traegt dann ein Vielfaches weniger "
                       "Laeufe, und genau daran ist am 28.07. die Flotte stehengeblieben.",
        "input_schema": {"type": "object", "properties": {
            "task": {"type": "string"}, "title": {"type": "string"},
            "model": {"type": "string", "enum": ["sonnet", "opus", "haiku"]}},
            "required": ["task"]},
        "ceremony_class": LOW,
    },
    {
        "name": "session_status",
        "description": "NUR mit Orchestrator-Recht: Zustand + Ergebnisse deiner Sub-Sessions "
                       "(laufen/warten/fertig/fehler, je Aufgabe tid + sid + Ergebnis).",
        "input_schema": {"type": "object", "properties": {}},
        "ceremony_class": READ,
    },
    {
        "name": "session_transcript",
        "description": "NUR mit Orchestrator-Recht: lies das VOLLSTAENDIGE Live-Transkript einer "
                       "Sub-Session ('tid' aus session_status) — jeden Modell-Zug, jeden "
                       "Werkzeug-Aufruf samt Eingabe und Ergebnis, nicht nur Endtexte. Geht "
                       "jederzeit, auch waehrend das Kind mitten in der Arbeit ist. Ohne 'ab' "
                       "kommen die juengsten 'kb' Kilobyte (Vorgabe 60, max 200); das Feld "
                       "'weiter_ab' im Ergebnis ist der Byte-Stand zum Weiterblaettern (beim "
                       "naechsten Aufruf als 'ab' uebergeben). ERGEBNIS-SCHEMA: der Inhalt steht "
                       "in 'ereignisse' — eine Liste von {art, ts, ...} mit art='antwort' (Text "
                       "des Kindes), 'werkzeug' (+werkzeug, eingabe), 'ergebnis' (+text, fehler) "
                       "und 'nutzer'. Es gibt KEIN Feld 'transcript' oder 'text'. Dazu "
                       "'groesse'/'ab'/'weiter_ab' als Byte-Staende. DEIN PFLICHTWERKZEUG der "
                       "Aufsicht: urteile nie nur nach 'running' — lies, WAS das Kind wirklich "
                       "tut (Kreisdrehen und Werkzeug-Fehlerserien siehst du nur hier).",
        "input_schema": {"type": "object", "properties": {
            "tid": {"type": "string"}, "ab": {"type": "integer"}, "kb": {"type": "integer"}},
            "required": ["tid"]},
        "ceremony_class": READ,
    },
    {
        "name": "session_watch",
        "description": "NUR mit Orchestrator-Recht: Auto-Aufsicht schalten oder erfragen "
                       "(modus 'an'|'aus'|'status', Vorgabe AN). AN heisst: neue Aktivitaet "
                       "ALLER deiner Kinder (Antworten + Werkzeug-Aufrufe) wird dir automatisch "
                       "als [AUTO-AUFSICHT]-Bericht ins Gespraech eingespielt, gebuendelt etwa "
                       "einmal pro Minute. Lass es AN, solange Kinder laufen — abschalten ist "
                       "die begruendete Ausnahme, nicht der Normalfall.",
        "input_schema": {"type": "object", "properties": {
            "modus": {"type": "string", "enum": ["an", "aus", "status"]}}},
        "ceremony_class": READ,
    },
    {
        "name": "session_tell",
        "description": "NUR mit Orchestrator-Recht: schicke einer LAUFENDEN Sub-Session (per tid aus "
                       "session_status) live eine Folge-Anweisung.",
        "input_schema": {"type": "object", "properties": {
            "tid": {"type": "string"}, "text": {"type": "string"}}, "required": ["tid", "text"]},
        "ceremony_class": LOW,
    },
    {
        "name": "session_broadcast",
        "description": "NUR mit Orchestrator-Recht: schicke EINE Anweisung an ALLE gerade laufenden "
                       "Sub-Sessions auf einmal (Kurskorrektur fuer die ganze Gruppe). Der Bericht "
                       "sagt je Sub-Session, ob sie erreicht wurde, und sonst warum nicht.",
        "input_schema": {"type": "object", "properties": {
            "text": {"type": "string"}}, "required": ["text"]},
        "ceremony_class": LOW,
    },
    {
        "name": "session_stop",
        "description": "NUR mit Orchestrator-Recht: brich eine entgleiste Sub-Session ab ('tid' aus "
                       "session_status; '*' bricht ALLE laufenden ab). Die Zelle wird beendet und der "
                       "Auftrag als abgebrochen vermerkt - nicht als fertig. 'reason' angeben. "
                       "Ein WARTENDER Auftrag (Zustand pending, noch ohne Zelle) laesst sich ebenfalls "
                       "abbrechen, wenn du ihn ausdruecklich beim Namen nennst - '*' erreicht nur "
                       "laufende Kinder. "
                       "WICHTIG - 'erledigt': setze es auf true, wenn das Kind sein Ergebnis GELIEFERT "
                       "hat und du nur noch den Slot freigibst (z. B. Studie veroeffentlicht, Zelle "
                       "idlet). Dann wird der Auftrag als FERTIG verbucht und 'reason' zaehlt als "
                       "Ergebnis. Ohne den Schalter gilt er als gescheitert - und ein gescheitertes "
                       "Paper wandert zurueck in die Warteschlange, obwohl es laengst auf der Site steht.",
        "input_schema": {"type": "object", "properties": {
            "tid": {"type": "string"}, "reason": {"type": "string"},
            "erledigt": {"type": "boolean",
                         "description": "true = das Kind hat geliefert, dies ist nur eine "
                                        "Slot-Freigabe (Auftrag wird als fertig verbucht)"}},
         "required": ["tid"]},
        "ceremony_class": LOW,
    },
    {
        "name": "session_resize",
        "description": "NUR mit Orchestrator-Recht: mehr Platz geben, wenn eine Zelle an ihrer "
                       "Disk oder ihrem RAM erstickt. 'tid' waehlt das Ziel: eine tid aus "
                       "session_status = genau dieses Kind, '*' = alle laufenden Kinder UND alle "
                       "kuenftigen, 'new' = nur kuenftige, 'self' = die eigene Zelle. "
                       "'disk_gb' bis 5 entscheidest du allein; 'mem_mb' bis 2048 ueber der "
                       "Grundausstattung ebenfalls. Willst du mehr, wird automatisch eine "
                       "Freigabe beim Besitzer beantragt (2FA) — du bekommst eine Kennung 'aid' "
                       "zurueck, fragst sie mit ask_owner_result ab und rufst danach "
                       "session_resize erneut mit 'approval' auf. Volumes WACHSEN nur, sie "
                       "schrumpfen nie. Ein laufendes Kind wird dabei neu gestartet und macht mit "
                       "erhaltenem Arbeitsstand weiter (mit restart:false verschiebst du das auf "
                       "den naechsten Start). Gib immer einen 'reason' an.",
        "input_schema": {"type": "object", "properties": {
            "tid": {"type": "string"}, "disk_gb": {"type": "number"},
            "mem_mb": {"type": "integer"}, "reason": {"type": "string"},
            "approval": {"type": "string"}, "restart": {"type": "boolean"}}},
        "ceremony_class": LOW,
    },
    {
        "name": "session_restart",
        "description": "NUR mit Orchestrator-Recht: ein Kind neu starten ('tid' aus "
                       "session_status, '*' = alle laufenden). Noetig, weil eine geaenderte "
                       "Ausstattung (session_resize) erst beim Start der Zelle wirksam wird — "
                       "Volumes wachsen vor dem Boot. Auch fuer ein haengendes Kind. Der "
                       "Arbeitsstand der Zelle bleibt erhalten und der Agent macht dort weiter; "
                       "gibt es noch keinen, faengt die Aufgabe sauber neu an. Der Auftrag bleibt "
                       "OFFEN (anders als session_stop, das ihn als abgebrochen vermerkt) und der "
                       "Neustart verbraucht keinen Wiederaufnahme-Versuch. 'reason' angeben. "
                       "NEU: du kannst damit auch einen bereits GESCHEITERTEN Auftrag zurueckholen, "
                       "solange sein Arbeitsstand noch liegt - session_status nennt diese unter "
                       "'fortsetzbar'. Die alte Zelle faehrt aus ihrem Delta wieder hoch und der Agent "
                       "macht MIT SEINEM GEDAECHTNIS weiter; oft ist die Rechnung laengst fertig und es "
                       "fehlt nur das Einsammeln und Veroeffentlichen. Dafuer musst du den Auftrag "
                       "ausdruecklich beim Namen nennen - '*' erreicht weiterhin nur laufende Kinder, "
                       "damit ein Rundumschlag nicht die ganze Flotte mit Leichen fuellt.",
        "input_schema": {"type": "object", "properties": {
            "tid": {"type": "string"}, "reason": {"type": "string"}}, "required": ["tid"]},
        "ceremony_class": LOW,
    },
    {
        "name": "voice_say",
        "description": "Tell the human something OUT LOUD. channel='sprachsystem' (default) announces "
                       "through the voice satellite and returns to idle — it does NOT start a "
                       "conversation, so you cannot receive an answer this way. channel='lautsprecher' "
                       "speaks through a granted speaker (TTS). channel='einhaengen' takes over voice "
                       "control so the user's next words go to YOU — only with that right. Refused "
                       "unless the session's voice level covers the channel. Rate-limited. Use it when "
                       "something needs the person, not the screen.",
        "input_schema": {"type": "object", "properties": {
            "text": {"type": "string"},
            "channel": {"type": "string", "enum": ["sprachsystem", "lautsprecher", "einhaengen"]},
            "speaker": {"type": "string"}},
            "required": ["text"]},
        "ceremony_class": LOW,
    },
    {
        "name": "ask_owner",
        "description": "Stelle dem BESITZER eine Frage (Mensch als Tool): erscheint als Karte in "
                       "seiner Braucht-dich-Inbox im Cockpit. Optional 'options' (2-6 kurze "
                       "Auswahlknoepfe) und 'urgent' (NUR fuer echte Blocker — schlaegt als Alarm "
                       "durch jede Stummschaltung). Gibt eine Kennung (aid) zurueck; die Antwort "
                       "kommt als Besitzer-Nachricht in deine Session UND ist per ask_owner_result "
                       "abrufbar. Arbeite waehrenddessen weiter, wenn moeglich.",
        "input_schema": {"type": "object", "properties": {
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}},
            "kind": {"type": "string", "enum": ["question", "approval"],
                     "description": "approval = Freigabe-Anfrage: der Besitzer genehmigt nur mit "
                                    "2FA-Handy-Code (fuer folgenreiche Aktionen); Default question."},
            "urgent": {"type": "boolean"}}, "required": ["question"]},
        "ceremony_class": LOW,
    },
    {
        "name": "ask_owner_result",
        "description": "Stand einer mit ask_owner gestellten Frage abrufen (aid).",
        "input_schema": {"type": "object", "properties": {"aid": {"type": "string"}},
                         "required": ["aid"]},
        "ceremony_class": READ,
    },
    {
        "name": "queue_job",
        "description": "Enqueue governed work: a natural-language 'prompt' commission, or a 'cmd' task "
                       "run under the portioneer queue. Funding-gated.",
        "input_schema": {"type": "object", "properties": {
            "prompt": {"type": "string"}, "cmd": {"type": "string"},
            "mem": {"type": "integer"}, "class": {"type": "string"}}},
        "ceremony_class": LOW,
    },
    {
        "name": "enter_credential",
        "description": "Enter a stored credential into the focused login form. NAME the credential only, "
                       "NEVER the secret itself. Arms a confirmation ceremony; the value never touches "
                       "speech, logs or the ledger.",
        "input_schema": {"type": "object", "properties": {
            "name": {"type": "string"}}, "required": ["name"]},
        "ceremony_class": IRREVERSIBLE,
    },
    {
        "name": "irreversible",
        "description": "Any irreversible verb (send, delete, pay, commit, kill). NEVER executes inline: "
                       "arms the ceremony engine (read-back + nonce + hold). The user confirms out loud.",
        "input_schema": {"type": "object", "properties": {
            "verb": {"type": "string", "enum": ["send", "delete", "pay", "commit", "kill"]},
            "text": {"type": "string"}}, "required": ["verb"]},
        "ceremony_class": IRREVERSIBLE,
    },
]

TOOL_BY_NAME = {t["name"]: t for t in TOOLS}

RETIRED_TOOLS = ("terminal_run", "terminal_read")

def ceremony_class(verb):

    t = TOOL_BY_NAME.get(verb)
    return t["ceremony_class"] if t else READ

def _tool_catalogue_text():
    lines = []
    for t in TOOLS:
        props = (t.get("input_schema") or {}).get("properties") or {}
        argnames = ",".join(props.keys()) or "—"
        lines.append("  - %s (%s) args: %s — %s" % (
            t["name"], t["ceremony_class"], argnames, t["description"]))
    return "\n".join(lines)

APP_MAP = (
    "PHANTOM-PORTAL — was du bedienst:\n"
    "brainbox-portal ist ein sprach-zentriertes, semantik-first LAN-Cockpit. Es zeigt IMMER genau EINE "
    "Ansicht (\"lens\") gross im Vordergrund — es gibt keinen Desktop mit Fenstern, sondern on-demand "
    "Lenses. Jeder Nutzer hat seine EIGENE Zelle (cell): eigener Bildschirm-Seat, eigenes Firefox, "
    "eigene Dateien. Du bedienst NUR die Zelle des aktuellen Nutzers.\n"
    "\n"
    "Lens-Katalog (was jede Ansicht ist):\n"
    "  - chat     : das Gespräch mit dir (Sprachsteuerung).\n"
    "  - browser  : der Firefox der Zelle, deterministisch gesteuert (Marionette), kein Blind-Klicken.\n"
    "  - notes    : Notizen/Dateien im NAS-Datenspeicher des Nutzers.\n"
    "  - screen   : der Live-Stream des Zell-Seats (was gerade gross gezeigt wird).\n"
    "  - queue    : die governte Warteschlange (Jobs/Pipelines).\n"
    "  - attach   : Anhaenge/hochgeladene Dateien.\n"
    "\n"
    "Ceremony-Regeln:\n"
    "  - read         : lesen/erkunden — jederzeit erlaubt, notfalls raten-dann-korrigieren.\n"
    "  - low          : reversible Aktion (Ansicht wechseln, URL oeffnen, tippen/scrollen) — direkt "
    "erlaubt.\n"
    "  - irreversible : senden/loeschen/bezahlen/committen/killen und Zugangsdaten-Eingabe — laufen IMMER "
    "ueber die Ceremony (Vorlesen + Bestaetigungszahl + Halten). Du fuehrst sie NIE selbst aus; du "
    "startest sie nur, der Nutzer bestaetigt laut.\n"
    "\n"
    "Werkzeuge — rufe sie ausschliesslich ueber den Befehl `portalctl` auf:\n"
    "  `portalctl state`                      -> aktueller Zustand als JSON\n"
    "  `portalctl <verb> '<json-args>'`       -> Aktion ausfuehren\n"
    "  Beispiele: portalctl browser_open '{\"url\":\"https://example.org\"}'\n"
    "             portalctl summon_lens '{\"lens\":\"screen\"}'\n"
    "Tool-Katalog (name (class) args — Zweck):\n"
    + _tool_catalogue_text() + "\n"
    "\n"
    "HARTE REGELN:\n"
    "  - Du handelst NUR an der Zelle des AKTUELLEN Nutzers. Erfinde nie eine fremde Zelle oder IDs.\n"
    "  - Loese \"das/dies/aktuell/es\" IMMER zuerst ueber `portalctl state` auf, bevor du handelst.\n"
    "  - Antworte KNAPP, natuerlich und vorlese-tauglich: kein Markdown, keine Code-Bloecke, kurze Saetze.\n"
    "  - Unumkehrbare Verben laufen ueber die Ceremony (portalctl irreversible / enter_credential) — nie "
    "ungefragt.\n"
    "  - Ziel-Wahl bei Eingaben: `app_drive` steuert die SEAT-App der Zelle (auf der Box); `client_input` "
    "steuert das EIGENE Fenster des Nutzers auf SEINEM Rechner (dort kopieren/ausschneiden/einfuegen/"
    "markieren/tippen/Taste/Rechtsklick). Waehle das richtige Ziel.\n"
    "  - Wenn du etwas tust, tu es WIRKLICH via portalctl und sag knapp das Ergebnis.\n"
    "  - Es gibt KEINE Host-Shell und kein Terminal-Werkzeug. Du kannst keine Shell-Befehle ausfuehren. "
    "Fragt jemand danach, sag genau das und nenne die Alternative: Arbeit laeuft in einer Session-Zelle "
    "(Reiter „Sessions“, dort hat jede Sitzung ihr eigenes Terminal), die Box selbst wird per SSH "
    "verwaltet. Erfinde dafuer nie ein Werkzeug und behaupte nie, etwas ausgefuehrt zu haben."
)

def build_state(ctx, uid):

    uid = str(uid or "owner")
    st = {"uid": uid, "lens": None, "apps": [], "focused": None,
          "screen_url": None, "links": []}
    g = ctx if isinstance(ctx, dict) else {}
    try:
        st["lens"] = g.get("active_lens")
    except Exception:
        pass
    apps = []
    try:
        enum = g.get("seat_enumerate")
        if enum:
            apps = enum(uid) or []
            st["apps"] = [{"title": a.get("title"), "app_id": a.get("app_id"),
                           "focused": bool(a.get("focused"))} for a in apps if isinstance(a, dict)][:12]
    except Exception:
        apps = []
    try:
        foc = g.get("seat_focused")
        f = foc(apps, uid) if foc else None
        if isinstance(f, dict):
            st["focused"] = {"title": f.get("title"), "app_id": f.get("app_id")}
    except Exception:
        pass

    try:
        ll = g.get("links_load")
        if ll:
            links = ll(uid) or []
            st["links"] = [x.get("url") for x in links[:8] if isinstance(x, dict) and x.get("url")]
            for x in links:
                if isinstance(x, dict) and x.get("source") == "opened":
                    st["screen_url"] = x.get("url")
                    break
    except Exception:
        pass
    return st

def state_line(st):

    st = st or {}
    try:
        apps = ",".join([(a.get("app_id") or a.get("title") or "?")
                         for a in (st.get("apps") or [])][:5]) or "-"
        foc = (st.get("focused") or {}).get("title") or "-"
        url = st.get("screen_url") or "-"
        return "uid=%s lens=%s apps=[%s] focus=%s url=%s" % (
            st.get("uid", "owner"), st.get("lens") or "?", apps, foc, url)
    except Exception:
        return "uid=%s" % st.get("uid", "owner")
