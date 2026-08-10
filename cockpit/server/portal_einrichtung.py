

import io
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

FRIST_SUCHE = int(os.environ.get("EINRICHTUNG_SUCHFRIST", "8"))
FRIST_PROBE = int(os.environ.get("EINRICHTUNG_PROBEFRIST", "6"))
PN_INIT_CONF = os.environ.get("EINRICHTUNG_PNINIT", "/etc/pn-init.conf")
SCHREIBER = "/usr/local/sbin/pn-einrichtung-schreiben"

BEREICHE = [
    {
        "id": "homeassistant",
        "titel": "Smarthome",
        "untertitel": "Home Assistant",
        "warum": "Damit die Box Lampen, Rollläden und Lautsprecher bedienen und Ansagen machen kann.",
        "leser": {"pn_init": "pn-nabud", "wozu": "Durchsagen über Home Assistant ausspricht"},
        "suche": {"mdns": "_home-assistant._tcp", "schema": "http"},
        "felder": [
            {"k": "HA_URL", "label": "Adresse", "art": "gefunden"},
            {"k": "HA_TOKEN", "label": "Zugangsschlüssel", "art": "geheim",
             "anleitung": "In Home Assistant unten links auf deinen Namen klicken → Reiter "
                          "„Sicherheit“ → ganz unten „Langlebige Zugangs-Tokens“ → „Token "
                          "erstellen“. Den angezeigten Text hier einfügen.",
             "hilfe_url": "/profile/security"},
            {"k": "NABU_ENTITY", "label": "Sprachgerät", "art": "auswahl",
             "hinweis": "Über dieses Gerät spricht die Box."},
        ],
        "ziel": {"env": "~/.config/nabu-display.env"},
    },
    {
        "id": "nas",
        "titel": "Netzlaufwerk",
        "untertitel": "Dateiserver im Heimnetz (SMB)",
        "warum": "Damit die Box an deine Dateien kommt und Sicherungen dort ablegen kann.",
        "leser": {"pn_init": "pn-mnt-clouddata", "wozu": "das Netzlaufwerk beim Start einhängt"},

        "suche": {"mdns": "_smb._tcp", "schema": "smb", "ohne_sich_selbst": True},
        "felder": [
            {"k": "NAS_HOST", "label": "Dateiserver", "art": "gefunden"},
            {"k": "NAS_USER", "label": "Benutzername", "art": "eingabe",
             "hinweis": "Das Konto, mit dem du dich sonst am Dateiserver anmeldest."},
            {"k": "NAS_PASS", "label": "Kennwort", "art": "geheim",
             "anleitung": "Das Kennwort dieses Kontos auf dem Dateiserver. Es wird nur auf der "
                          "Box abgelegt (nur für root lesbar) und nie wieder angezeigt."},
            {"k": "NAS_SHARE", "label": "Freigabe", "art": "auswahl",
             "hinweis": "Die Box liest die Liste der Freigaben selbst aus."},
        ],
        "ziel": {"privilegiert": "nas"},
    },
    {
        "id": "telegram",
        "titel": "Benachrichtigungen",
        "untertitel": "Telegram",
        "warum": "Damit die Box dir Bescheid sagen kann und du ihr von unterwegs schreiben kannst.",
        "leser": {"pn_init": "zyrkel", "wozu": "Telegram mit der Box verbindet"},
        "suche": None,
        "felder": [
            {"k": "TELEGRAM_BOT_TOKEN", "label": "Bot-Schlüssel", "art": "geheim",
             "anleitung": "In Telegram @BotFather anschreiben → „/newbot“ → Namen vergeben. "
                          "BotFather antwortet mit einer Zeile wie „123456:AA…“ — die hier "
                          "einfügen.",
             "hilfe_url": "https://t.me/BotFather"},
            {"k": "TELEGRAM_CHAT_ID", "label": "Dein Chat", "art": "auswahl",
             "hinweis": "Schreib deinem Bot in Telegram irgendetwas — dann steht er hier."},
        ],
        "ziel": {"env": "~/zyrkel/.env", "json": ["~/zyrkel/config.json"]},
    },
    {
        "id": "fernzugang",
        "titel": "Fernzugang",
        "untertitel": "Erreichbarkeit von unterwegs",
        "warum": "Damit du von außen an die Box kommst, ohne einen Port im Router zu öffnen.",
        "leser": {"pn_init": "pn-relayd", "wozu": "die Verbindung nach außen hält"},
        "suche": None,
        "felder": [
            {"k": "RELAY_URL", "label": "Vermittlung", "art": "eingabe",
             "hinweis": "Ergibt sich aus deinem Konto — die Vorgabe passt im Normalfall."},
        ],
        "ziel": {"privilegiert": "relay"},
    },
    {
        "id": "ausfallsicherheit",
        "titel": "Ausfallsicherheit",
        "untertitel": "Zweite Box übernimmt",
        "warum": "Damit eine zweite Box unter derselben Adresse einspringt, wenn diese ausfällt.",
        "leser": {"pn_init": "keepalived", "wozu": "die gemeinsame Adresse übernimmt"},
        "suche": None,
        "felder": [
            {"k": "HA_VIP", "label": "Gemeinsame Adresse", "art": "eingabe",
             "hinweis": "Eine noch freie Adresse im Heimnetz. Die Box prüft, ob sie frei ist."},
            {"k": "VRRP_AUTH_PASS", "label": "Gemeinsames Kennwort", "art": "geheim",
             "anleitung": "Frei wählbar — beide Boxen brauchen dasselbe. Es schützt die Übernahme "
                          "der Adresse davor, dass ein fremdes Gerät sie an sich reißt."},
        ],
        "ziel": {"privilegiert": "hochverfuegbarkeit"},
    },
    {
        "id": "medien",
        "titel": "Video und Ton nach außen",
        "untertitel": "WebRTC-Vermittlung",
        "warum": "Damit Bild und Ton auch dann durchkommen, wenn beide Seiten hinter einem Router "
                 "sitzen.",
        "leser": {"pn_init": "mediad", "wozu": "Bild- und Tonverbindungen vermittelt"},
        "suche": None,
        "felder": [
            {"k": "MEDIA_TURN_URL", "label": "Vermittlungsserver", "art": "eingabe"},
            {"k": "MEDIA_STUN_URL", "label": "Adressauskunft", "art": "eingabe"},
        ],
        "ziel": {"env": "~/.config/brainbox/medien.env"},
    },
    {
        "id": "schnittstelle",
        "titel": "Programmierschnittstelle",
        "untertitel": "Gateway nach außen",
        "warum": "Nur nötig, wenn du eigene Programme an die Box anschließen willst.",
        "leser": {"pn_init": "gateway", "wozu": "Anfragen von außen beantwortet"},
        "suche": None,
        "felder": [
            {"k": "GW_HOST", "label": "Adresse, auf der gelauscht wird", "art": "eingabe"},
            {"k": "GW_SECRET", "label": "Signierschlüssel", "art": "geheim",
             "anleitung": "Frei wählbar, mindestens 32 Zeichen. Er unterschreibt die kurzlebigen "
                          "Eintrittskarten für Medien und Rückrufe."},
        ],
        "ziel": {"env": "~/.config/brainbox/schnittstelle.env"},
    },
]
NACH_ID = {b["id"]: b for b in BEREICHE}

def _pn_init_dienste():

    namen = set()
    try:
        with io.open(PN_INIT_CONF, encoding="utf-8", errors="replace") as f:
            for z in f:
                z = z.strip()
                if z and not z.startswith("#") and "|" in z:
                    namen.add(z.split("|", 1)[0].strip())
    except OSError:
        pass
    return namen

def _leser_da(bereich, dienste=None):

    leser = bereich.get("leser") or {}
    name = leser.get("pn_init")
    if not name:
        return True, ""
    if name in (dienste if dienste is not None else _pn_init_dienste()):
        return True, ""
    return False, ("Auf dieser Box gibt es keinen Dienst, der diese Werte liest — erwartet wäre "
                   "„%s“, der %s. Solange er fehlt, würde eine Eingabe hier nichts bewirken."
                   % (name, leser.get("wozu", "sie benutzt")))

def _entfluchten(s):

    return re.sub(r"\\(\d{3})", lambda m: chr(int(m.group(1))), s or "")

def _txt_lesen(felder):
    d = {}
    for roh in felder:
        for stueck in re.findall(r'"([^"]*)"', roh):
            if "=" in stueck:
                k, _, v = stueck.partition("=")
                d[k.strip()] = v.strip()
    return d

def mdns_suchen(dienst, frist=None):

    frist = frist or FRIST_SUCHE
    try:
        r = subprocess.run(["avahi-browse", "-rtpk", dienst],
                           capture_output=True, text=True, timeout=frist + 4)
    except (OSError, subprocess.SubprocessError):
        return []
    gefunden, gesehen = [], set()
    for z in r.stdout.splitlines():
        if not z.startswith("="):
            continue
        f = z.split(";")
        if len(f) < 9 or f[2] != "IPv4":
            continue
        adresse, port = f[7], f[8]
        if (adresse, port) in gesehen:
            continue
        gesehen.add((adresse, port))
        gefunden.append({"name": _entfluchten(f[3]), "host": f[6],
                         "adresse": adresse, "port": port, "txt": _txt_lesen(f[9:])})
    return gefunden

def _eigene_adressen():

    try:
        r = subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True, text=True, timeout=4)
        return set(re.findall(r"inet (\d+\.\d+\.\d+\.\d+)", r.stdout))
    except (OSError, subprocess.SubprocessError):
        return set()

def _vorschlaege(bereich):

    s = bereich.get("suche") or {}
    if not s.get("mdns"):
        return []
    eigene = _eigene_adressen() if s.get("ohne_sich_selbst") else set()
    raus = []
    for t in mdns_suchen(s["mdns"]):
        if t["adresse"] in eigene:
            continue
        txt = t.get("txt") or {}
        if s.get("schema") == "smb":

            url, zusatz = t["adresse"], t["host"].rstrip(".")
        else:
            url = (txt.get("internal_url") or txt.get("base_url") or
                   "%s://%s:%s" % (s.get("schema", "http"), t["adresse"], t["port"])).rstrip("/")
            zusatz = ("Version %s" % txt["version"]) if txt.get("version") else ""
        raus.append({"name": txt.get("location_name") or t["name"] or t["host"],
                     "url": url, "adresse": t["adresse"], "zusatz": zusatz})
    return raus

def _holen(url, marke=None, frist=None):
    frist = frist or FRIST_PROBE
    anfrage = urllib.request.Request(url, headers={"Accept": "application/json"})
    if marke:
        anfrage.add_header("Authorization", "Bearer " + marke)
    ktx = ssl.create_default_context()
    ktx.check_hostname = False
    ktx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(anfrage, timeout=frist, context=ktx) as a:
        return a.status, a.read(1 << 20)

def _url_pruefen(url, schemata=("http", "https"), beispiel="http://192.168.1.50:8123"):
    url = (url or "").strip().rstrip("/")
    muster = r"^(%s)://[A-Za-z0-9._\-\[\]:]+(:\d+)?$" % "|".join(schemata)
    if not re.match(muster, url):
        return None, "Die Adresse muss so aussehen: %s" % beispiel
    return url, None

def ha_pruefen(url, marke):

    url, fehler = _url_pruefen(url)
    if fehler:
        return {"ok": False, "grund": fehler}
    try:
        code, roh = _holen(url + "/api/", marke)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "grund": "Home Assistant ist erreichbar, lehnt den Schlüssel aber "
                                          "ab. Bitte einen neuen Langlebigen Zugangs-Token anlegen."}
        return {"ok": False, "grund": "Home Assistant antwortet mit Fehler %s." % e.code}
    except (urllib.error.URLError, socket.timeout, OSError):
        return {"ok": False, "grund": "Unter dieser Adresse antwortet nichts. Läuft Home Assistant, "
                                      "und ist die Box im selben Netz?"}
    if code != 200:
        return {"ok": False, "grund": "Unerwartete Antwort (%s)." % code}
    return {"ok": True, "grund": "Verbindung steht."}

def ha_sprachgeraete(url, marke):

    try:
        code, roh = _holen(url.rstrip("/") + "/api/states", marke)
        zustaende = json.loads(roh.decode("utf-8", "replace"))
    except Exception:
        return []
    raus = []
    for z in zustaende if isinstance(zustaende, list) else []:
        eid = str(z.get("entity_id") or "")
        if eid.startswith(("assist_satellite.", "media_player.")):
            name = ((z.get("attributes") or {}).get("friendly_name")) or eid
            raus.append({"id": eid, "name": name,
                         "art": "Sprachgerät" if eid.startswith("assist_satellite.")
                                else "Lautsprecher"})
    raus.sort(key=lambda x: (x["art"] != "Sprachgerät", x["name"].lower()))
    return raus

def pruefe_homeassistant(w):
    url, marke = w.get("HA_URL", ""), w.get("HA_TOKEN", "")
    e = ha_pruefen(url, marke)
    if e["ok"]:
        e["auswahl"] = {"NABU_ENTITY": ha_sprachgeraete(url, marke)}
    return e

def _smb_freigaben(host, benutzer, kennwort):

    laufraum = os.environ.get("XDG_RUNTIME_DIR") or ("/run/user/%d" % os.getuid())
    if not os.path.isdir(laufraum):
        laufraum = None
    d = tempfile.mkdtemp(prefix=".einr-smb-", dir=laufraum)
    try:
        p = os.path.join(d, "zugang")
        fd = os.open(p, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("username=%s\npassword=%s\n" % (benutzer, kennwort))
        try:
            r = subprocess.run(["smbclient", "-L", "//%s" % host, "-A", p, "-g", "-m", "SMB3"],
                               capture_output=True, text=True, timeout=FRIST_PROBE + 6)
        except (OSError, subprocess.SubprocessError):
            return None, "Die Box konnte den Dateiserver nicht befragen (smbclient fehlt)."
    finally:
        shutil.rmtree(d, ignore_errors=True)

    text = (r.stdout or "") + (r.stderr or "")
    if "NT_STATUS_LOGON_FAILURE" in text or "NT_STATUS_ACCESS_DENIED" in text:
        return None, "Der Dateiserver ist erreichbar, weist Benutzer oder Kennwort aber ab."
    if "NT_STATUS_ACCOUNT_LOCKED_OUT" in text:
        return None, "Das Konto ist auf dem Dateiserver gesperrt."
    if r.returncode != 0 and not r.stdout.strip():
        return None, ("Unter diesem Namen antwortet kein Dateiserver. Ist er eingeschaltet und im "
                      "selben Netz?")
    freigaben = []
    for z in r.stdout.splitlines():
        f = z.split("|")

        if len(f) >= 2 and f[0] == "Disk" and not f[1].endswith("$"):
            freigaben.append({"id": f[1], "name": f[1],
                              "art": (f[2].strip() if len(f) > 2 and f[2].strip() else "Freigabe")})
    return freigaben, None

def pruefe_nas(w):
    host = (w.get("NAS_HOST") or "").strip().rstrip("/")
    if not re.match(r"^[A-Za-z0-9._\-]+$", host or ""):
        return {"ok": False, "grund": "Bitte einen Dateiserver aus der Liste wählen."}
    if not (w.get("NAS_USER") or "").strip():
        return {"ok": False, "grund": "Ohne Benutzernamen lässt sich der Zugang nicht prüfen."}
    freigaben, fehler = _smb_freigaben(host, w["NAS_USER"].strip(), w.get("NAS_PASS", ""))
    if fehler:
        return {"ok": False, "grund": fehler}
    if not freigaben:
        return {"ok": False, "grund": "Der Zugang stimmt, aber dieses Konto sieht keine einzige "
                                      "Freigabe."}
    return {"ok": True, "grund": "Zugang steht — %d Freigabe(n) gefunden." % len(freigaben),
            "auswahl": {"NAS_SHARE": freigaben}}

def nas_zusammenbauen(w):

    raus = {"NAS_ENABLED": "1",
            "NAS_URL": "//%s/%s" % (w.get("NAS_HOST", "").strip(), w.get("NAS_SHARE", "").strip()),
            "NAS_USER": w.get("NAS_USER", "").strip()}
    if w.get("NAS_PASS"):
        raus["NAS_PASS"] = w["NAS_PASS"]
    return raus

def nas_zerlegen(g):

    m = re.match(r"^//([^/]+)/(.+)$", g.get("NAS_URL", "") or "")
    d = dict(g)
    if m:
        d["NAS_HOST"], d["NAS_SHARE"] = m.group(1), m.group(2)
    return d

def _telegram(marke, methode, frist=None):
    url = "https://api.telegram.org/bot%s/%s" % (urllib.parse.quote(marke, safe=""), methode)
    code, roh = _holen(url, None, frist)
    return json.loads(roh.decode("utf-8", "replace"))

def telegram_chats(marke):

    try:
        antwort = _telegram(marke, "getUpdates?limit=20")
    except Exception:
        return []
    raus, gesehen = [], set()
    for u in (antwort.get("result") or []):
        n = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        c = n.get("chat") or {}
        if not c.get("id") or c["id"] in gesehen:
            continue
        gesehen.add(c["id"])
        name = (" ".join(x for x in (c.get("title"), c.get("first_name"), c.get("last_name")) if x)
                or c.get("username") or str(c["id"]))
        raus.append({"id": str(c["id"]), "name": name,
                     "art": "Gruppe" if c.get("type") in ("group", "supergroup") else "Direkt"})
    return raus

def pruefe_telegram(w):
    marke = (w.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not re.match(r"^\d{5,}:[A-Za-z0-9_\-]{20,}$", marke):
        return {"ok": False, "grund": "Das sieht nicht aus wie ein Bot-Schlüssel. BotFather "
                                      "antwortet mit etwas in der Form „123456:AA…“."}
    try:
        antwort = _telegram(marke, "getMe")
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):
            return {"ok": False, "grund": "Telegram kennt diesen Bot-Schlüssel nicht. Wurde er bei "
                                          "BotFather zurückgezogen?"}
        return {"ok": False, "grund": "Telegram antwortet mit Fehler %s." % e.code}
    except (urllib.error.URLError, socket.timeout, OSError, ValueError):
        return {"ok": False, "grund": "Telegram ist von der Box aus nicht erreichbar. Hat die Box "
                                      "Zugang ins Internet?"}
    name = ((antwort.get("result") or {}).get("username")) or "?"
    chats = telegram_chats(marke)
    e = {"ok": True, "grund": "Verbunden mit @%s." % name,
         "auswahl": {"TELEGRAM_CHAT_ID": chats}}
    if not chats:

        e["grund"] += (" Schreib @%s jetzt in Telegram eine Nachricht und drück noch einmal auf "
                       "„Prüfen“ — dann steht dein Chat hier." % name)
        e["nochmal"] = True
    return e

def pruefe_fernzugang(w):
    url, fehler = _url_pruefen(w.get("RELAY_URL", ""), ("wss", "ws"), "wss://relay.beispiel.de/")
    if fehler:
        return {"ok": False, "grund": fehler}
    probe = re.sub(r"^ws", "http", url)
    try:
        code, _ = _holen(probe)
    except urllib.error.HTTPError as e:

        if e.code in (426, 400, 401, 403):
            return {"ok": True, "grund": "Die Vermittlung antwortet und spricht Websocket."}
        return {"ok": False, "grund": "Die Vermittlung antwortet mit Fehler %s." % e.code}
    except (urllib.error.URLError, socket.timeout, OSError):
        return {"ok": False, "grund": "Die Vermittlung ist von der Box aus nicht erreichbar."}
    return {"ok": True, "grund": "Die Vermittlung antwortet (%s)." % code}

def fernzugang_zusammenbauen(w):
    return {"RELAY_ENABLED": "1", "RELAY_URL": (w.get("RELAY_URL") or "").strip()}

def pruefe_ausfallsicherheit(w):
    import ipaddress
    vip = (w.get("HA_VIP") or "").strip()
    try:
        ipaddress.IPv4Address(vip)
    except ValueError:
        return {"ok": False, "grund": "Bitte eine Adresse in der Form 192.168.1.240 angeben."}
    if not (w.get("VRRP_AUTH_PASS") or "").strip():
        return {"ok": False, "grund": "Ohne gemeinsames Kennwort könnte ein fremdes Gerät die "
                                      "Adresse an sich reißen."}
    if vip in _eigene_adressen():
        return {"ok": True, "grund": "Diese Box hält die gemeinsame Adresse bereits."}
    try:
        r = subprocess.run(["ping", "-n", "-c", "1", "-W", "1", vip],
                           capture_output=True, text=True, timeout=6)
        belegt = (r.returncode == 0)
    except (OSError, subprocess.SubprocessError):
        belegt = False
    if belegt:

        return {"ok": False, "grund": "Diese Adresse antwortet schon — sie ist vergeben. Bitte "
                                      "eine freie wählen."}
    return {"ok": True, "grund": "Die Adresse ist frei und kann übernommen werden."}

def ausfall_zusammenbauen(w):
    return {"HA_ENABLED": "1", "HA_VIP": (w.get("HA_VIP") or "").strip(),
            "VRRP_AUTH_PASS": w.get("VRRP_AUTH_PASS", "")}

def pruefe_nur_form(w):

    fehlt = [k for k, v in w.items() if not str(v or "").strip()]
    if fehlt:
        return {"ok": False, "grund": "Es fehlt noch: %s." % ", ".join(sorted(fehlt))}
    return {"ok": True, "grund": "Die Angaben sind vollständig. Ob sie stimmen, kann die Box erst "
                                 "sagen, wenn der zugehörige Dienst läuft."}

PRUEFER = {
    "homeassistant": pruefe_homeassistant,
    "nas": pruefe_nas,
    "telegram": pruefe_telegram,
    "fernzugang": pruefe_fernzugang,
    "ausfallsicherheit": pruefe_ausfallsicherheit,
}
ZUSAMMENBAUEN = {
    "nas": nas_zusammenbauen,
    "fernzugang": fernzugang_zusammenbauen,
    "ausfallsicherheit": ausfall_zusammenbauen,
}
ZERLEGEN = {"nas": nas_zerlegen}

def pruefen(bereich, werte):
    return PRUEFER.get(bereich["id"], pruefe_nur_form)(werte)

def _env_lesen(pfad):
    d = {}
    try:

        with io.open(os.path.expanduser(pfad), encoding="utf-8") as f:
            for z in f:
                m = re.match(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$", z)
                if m:
                    d[m.group(1)] = m.group(2).strip("'\"")
    except OSError:
        pass
    return d

def _env_schreiben(pfad, neu):

    p = os.path.expanduser(pfad)
    d = _env_lesen(p)
    d.update({k: v for k, v in neu.items() if v is not None})
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".einrichtung-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("# von der Einrichtung im Portal geschrieben — Werte hier nicht von Hand ändern\n")
            for k in sorted(d):
                f.write("%s=%s\n" % (k, d[k]))
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

JSON_SCHLUESSEL = {"TELEGRAM_CHAT_ID": ("allowed_chat_id", int)}

def _json_schreiben(pfad, neu):
    p = os.path.expanduser(pfad)
    try:
        with io.open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return
    aenderung = False
    for k, v in neu.items():
        eintrag = JSON_SCHLUESSEL.get(k)
        if not eintrag or v in (None, ""):
            continue
        name, art = eintrag
        try:
            d[name] = art(v)
        except (TypeError, ValueError):
            continue
        aenderung = True
    if not aenderung:
        return
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".einrichtung-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
            f.write("\n")
        shutil.copymode(p, tmp)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

def _privilegiert_schreiben(name, werte):

    if not os.path.exists(SCHREIBER):
        raise OSError("Der Helfer %s ist nicht installiert." % SCHREIBER)
    r = subprocess.run(["sudo", "-n", SCHREIBER, name],
                       input=json.dumps(werte), capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise OSError((r.stderr or "").strip() or "Der Helfer lehnte ab (%d)." % r.returncode)

def _privilegiert_stand(name):

    if not os.path.exists(SCHREIBER):
        return {}, []
    try:
        r = subprocess.run(["sudo", "-n", SCHREIBER, "--stand", name],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return {}, []
        a = json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}, []
    return a.get("offen") or {}, a.get("geheim_gesetzt") or []

def _ablegen(bereich, werte):
    ziel = bereich.get("ziel") or {}
    if ziel.get("privilegiert"):
        _privilegiert_schreiben(ziel["privilegiert"], werte)
    if ziel.get("env"):
        eigene = {k: v for k, v in werte.items() if k not in JSON_SCHLUESSEL}
        if eigene:
            _env_schreiben(ziel["env"], eigene)
    for p in ziel.get("json", []):
        _json_schreiben(p, werte)

GESETZT = "•"

def _gespeichert(bereich):
    ziel = bereich.get("ziel") or {}
    d = {}
    if ziel.get("env"):
        d.update(_env_lesen(ziel["env"]))
    if ziel.get("privilegiert"):
        offen, geheim = _privilegiert_stand(ziel["privilegiert"])
        d.update(offen)
        d.update({k: GESETZT for k in geheim})
    for p in ziel.get("json", []):
        try:
            with io.open(os.path.expanduser(p), encoding="utf-8") as f:
                roh = json.load(f)
            for k, (name, _art) in JSON_SCHLUESSEL.items():
                if roh.get(name):
                    d[k] = str(roh[name])
        except (OSError, ValueError):
            pass
    return ZERLEGEN.get(bereich["id"], lambda x: x)(d)

def _stand(bereich, dienste=None):

    vorhanden = _gespeichert(bereich)
    aktiv, grund = _leser_da(bereich, dienste)
    felder, fertig = [], True
    for f in bereich["felder"]:
        wert = vorhanden.get(f["k"], "")
        gesetzt = bool(wert)
        fertig = fertig and gesetzt
        e = {k: f[k] for k in ("k", "label", "art", "anleitung", "hinweis", "hilfe_url") if k in f}
        e["gesetzt"] = gesetzt
        if f["art"] != "geheim":
            e["wert"] = wert
        felder.append(e)
    return {"id": bereich["id"], "titel": bereich["titel"],
            "untertitel": bereich.get("untertitel", ""), "warum": bereich.get("warum", ""),
            "suchbar": bool((bereich.get("suche") or {}).get("mdns")),
            "aktiv": aktiv, "gesperrt_weil": grund,
            "fertig": bool(fertig and aktiv), "felder": felder}

def _leib(h):
    try:
        n = int(h.headers.get("Content-Length") or 0)
        return json.loads(h.rfile.read(n).decode("utf-8")) if n else {}
    except Exception:
        return {}

def _werte_zusammen(bereich, leib):

    erlaubt = {f["k"] for f in bereich["felder"]}
    neu = {k: ("" if v is None else str(v)).strip()
           for k, v in (leib.get("werte") or {}).items() if k in erlaubt}
    alt = _gespeichert(bereich)
    raus = {}
    for f in bereich["felder"]:
        k = f["k"]
        if neu.get(k):
            raus[k] = neu[k]
        elif alt.get(k) and alt[k] != GESETZT:
            raus[k] = alt[k]
        else:
            raus[k] = ""
    return raus

SEITE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "einrichtung.html")

def dispatch(h, method, path):

    ist_seite = (method == "GET" and path == "/einrichtung")
    if not (ist_seite or path == "/api/einrichtung" or path.startswith("/api/einrichtung/")):
        return False

    if not (h.authed() and h._is_admin()):
        if ist_seite:
            h.send_html("Die Einrichtung ist nur für Verwalter.", 403)
        else:
            h._sess_json({"ok": False, "error": "Nur Verwalter dürfen die Box einrichten."}, 403)
        return True

    if ist_seite:
        try:
            with io.open(SEITE, encoding="utf-8") as f:
                h.send_html(f.read(), 200)
        except OSError:
            h.send_html("einrichtung.html fehlt", 404)
        return True

    if method == "GET" and path == "/api/einrichtung":
        dienste = _pn_init_dienste()
        h._sess_json({"ok": True, "bereiche": [_stand(b, dienste) for b in BEREICHE]})
        return True

    leib = _leib(h) if method == "POST" else {}
    bereich = NACH_ID.get(str(leib.get("bereich") or ""))
    if method == "POST" and not bereich:
        h._sess_json({"ok": False, "error": "Unbekannter Bereich."}, 400)
        return True
    if method == "POST":

        aktiv, grund = _leser_da(bereich)
        if not aktiv:
            h._sess_json({"ok": False, "error": grund}, 409)
            return True

    if method == "POST" and path == "/api/einrichtung/suchen":
        h._sess_json({"ok": True, "gefunden": _vorschlaege(bereich)})
        return True

    if method == "POST" and path == "/api/einrichtung/pruefen":
        h._sess_json({"ok": True, "ergebnis": pruefen(bereich, _werte_zusammen(bereich, leib))})
        return True

    if method == "POST" and path == "/api/einrichtung/speichern":
        werte = _werte_zusammen(bereich, leib)

        e = pruefen(bereich, werte)
        if not e["ok"]:
            h._sess_json({"ok": False, "error": e["grund"]}, 400)
            return True
        try:
            _ablegen(bereich, ZUSAMMENBAUEN.get(bereich["id"], lambda x: x)(werte))
        except (OSError, subprocess.SubprocessError) as ex:
            h._sess_json({"ok": False, "error": "Konnte nicht sichern: %s" % ex}, 500)
            return True
        h._sess_json({"ok": True, "stand": _stand(bereich)})
        return True

    h._sess_json({"ok": False, "error": "Unbekannte Route."}, 404)
    return True
