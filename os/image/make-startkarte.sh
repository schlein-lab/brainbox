#!/usr/bin/env bash
set -euo pipefail
ZIEL="${1:?ZIELVERZEICHNIS fehlt}"
ART="${2:?pi|iso fehlt}"
KONTO="${3:-}"
[ -d "$ZIEL" ] || { echo "make-startkarte: $ZIEL ist kein Verzeichnis"; exit 1; }

case "$ART" in
  pi)
    KONTO="${KONTO:-ubuntu}"
    S2_DE="Karte in den Raspberry Pi stecken und den Strom anschliessen."
    S2_EN="Put the card into the Raspberry Pi and connect power."
    S3_DE="Netzwerkkabel einstecken. (WLAN geht auch -- das richten Sie gleich im Assistenten ein.)"
    S3_EN="Plug in the network cable. (Wi-Fi can be set up in the wizard later.)"
    ;;
  iso)
    KONTO="${KONTO:-brainbox}"
    S2_DE="Eine neue virtuelle Maschine anlegen: mindestens 4 GB RAM, 40 GB leere Festplatte,"$'\n'"    dieses ISO als CD/DVD einhaengen und starten."
    S2_EN="Create a new VM: at least 4 GB RAM, a 40 GB empty disk, attach this ISO as the"$'\n'"    CD/DVD drive and start it."
    S3_DE="Der Installer laeuft von allein durch (vier Schritte, keine Eingabe). Danach das ISO"$'\n'"    entfernen -- die Box startet dann von ihrer Festplatte."
    S3_EN="The installer runs on its own (four steps, no input). Then remove the ISO -- the box"$'\n'"    boots from its disk."
    ;;
  *) echo "make-startkarte: unbekannte Art '$ART' (pi|iso)"; exit 1;;
esac

TXT="$(cat <<EOF
==============================================================
     B R A I N B O X       So geht es los / How to start
==============================================================

DEUTSCH
-------

 1. An dieser Datei muessen Sie NICHTS tun. Sie ist nur die Anleitung.

 2. $S2_DE

 3. $S3_DE

 4. Zwei bis drei Minuten warten. Die Box richtet sich selbst ein.
    Haengt ein Monitor daran, laeuft dort eine Anzeige mit -- solange
    sich das Zeichen dreht, arbeitet sie.

 5. An Handy oder Laptop IM GLEICHEN NETZ den Browser oeffnen:

              http://brainbox.local/

    Dort fuehrt Sie ein Assistent durch alles Weitere.
    Es ist kein Befehl und keine Kommandozeile noetig.

 Wenn http://brainbox.local/ nicht geht:
    - Noch eine Minute warten und die Seite neu laden.
    - Einige Windows-Rechner koennen mit ".local"-Namen nichts anfangen.
      Dann im Router (Fritzbox, Speedport, ...) nachsehen, welche
      IP-Adresse das neue Geraet bekommen hat, und diese oeffnen:
      zum Beispiel http://192.168.1.42/
    - Haengt ein Monitor an der Box, steht die Adresse dort gross
      auf dem Bildschirm, zusammen mit einem QR-Code zum Abfotografieren.

 Ohne Monitor, und Sie moechten gleich per SSH herankommen:
    Legen Sie VOR dem ersten Start eine Datei mit dem Namen
        brainbox-authorized_keys
    direkt neben diese Karte und schreiben Sie Ihren oeffentlichen
    SSH-Schluessel hinein (eine Zeile je Schluessel). Sie wird beim
    ersten Start uebernommen und danach geloescht.

    Anmelden dann GENAU so -- das Konto heisst auf diesem Abbild
    "$KONTO", nicht "root" und nicht wie Ihr eigener Benutzername:

        ssh $KONTO@brainbox.local

    Eine Passwort-Anmeldung ueber SSH gibt es bewusst nicht, und
    "root" kann sich gar nicht anmelden. Verwaltungsrechte holen Sie
    sich danach mit "sudo" und dem Konsolen-Passwort, das der
    Assistent setzt.


ENGLISH
-------

 1. You do NOT have to do anything with this file. It is just the guide.

 2. $S2_EN

 3. $S3_EN

 4. Wait two to three minutes. The box sets itself up. If a monitor is
    attached it shows a progress display -- while the spinner turns,
    it is working.

 5. On a phone or laptop ON THE SAME NETWORK, open a browser:

              http://brainbox.local/

    A wizard takes you through everything else.
    No commands, no command line.

 If http://brainbox.local/ does not work:
    - Wait another minute and reload.
    - Some Windows machines cannot resolve ".local" names. Look up the
      new device's IP address in your router and open that instead,
      for example http://192.168.1.42/
    - If a monitor is attached, the address and a QR code are on screen.

 Headless, and you want SSH right away:
    Before the first boot, put a file named
        brainbox-authorized_keys
    next to this card and paste your public SSH key into it (one key
    per line). It is picked up on first boot and then deleted.

    Then log in EXACTLY like this -- on this image the account is
    called "$KONTO", not "root" and not your own user name:

        ssh $KONTO@brainbox.local

    SSH password login is deliberately not available, and "root"
    cannot log in at all. You gain admin rights afterwards with
    "sudo" and the console password set in the wizard.

--------------------------------------------------------------
 Brainbox -- brainarbeit.com    Lizenz: PolyForm Noncommercial
==============================================================
EOF
)"

printf '%s\n' "$TXT" | sed 's/$/\r/' > "$ZIEL/START-HIER.txt"
cp "$ZIEL/START-HIER.txt" "$ZIEL/START-HERE.txt"

cat > "$ZIEL/START-HIER.html" <<'HTML'
<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Brainbox — So geht es los / How to start</title>
<style>
 :root{color-scheme:light dark}
 body{font:16px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
      max-width:44rem;margin:0 auto;padding:2rem 1.2rem}
 h1{font-size:1.6rem;margin:0 0 .2rem}
 .sub{opacity:.7;margin:0 0 2rem}
 a.knopf{display:block;text-align:center;font-size:1.35rem;font-weight:600;
      padding:1.1rem;margin:1.6rem 0;border-radius:.7rem;
      background:#1a56db;color:#fff;text-decoration:none}
 a.knopf:hover{background:#1e429f}
 ol{padding-left:1.3rem} li{margin:.5rem 0}
 .hinweis{border-left:3px solid #999;padding:.2rem 0 .2rem 1rem;opacity:.85;margin:1.5rem 0}
 code{background:rgba(128,128,128,.18);padding:.1rem .35rem;border-radius:.25rem}
 hr{border:0;border-top:1px solid rgba(128,128,128,.35);margin:2.5rem 0}
</style></head><body>
<h1>Brainbox</h1>
<p class="sub">So geht es los &middot; How to start</p>

<a class="knopf" href="http://brainbox.local/">http://brainbox.local/ &nbsp;&rarr;&nbsp; Einrichtung starten</a>

<ol>
 <li>Die Box anschliessen und einschalten.</li>
 <li>Zwei bis drei Minuten warten &ndash; sie richtet sich selbst ein.</li>
 <li>Den blauen Knopf oben antippen. Er muss von einem Geraet
     <strong>im selben Netz</strong> geoeffnet werden.</li>
 <li>Der Assistent fuehrt durch alles Weitere. Kein Befehl, keine Kommandozeile.</li>
</ol>

<p class="hinweis">Klappt der Knopf nicht? Einige Windows-Rechner kennen keine
<code>.local</code>-Namen. Dann im Router nachsehen, welche IP-Adresse das neue
Geraet bekommen hat, und diese im Browser oeffnen. Haengt ein Monitor an der Box,
steht die Adresse dort samt QR-Code auf dem Bildschirm.</p>

<hr>

<ol>
 <li>Connect the box and switch it on.</li>
 <li>Wait two to three minutes while it sets itself up.</li>
 <li>Open the blue button above from a device <strong>on the same network</strong>.</li>
 <li>A wizard takes you through the rest. No commands, no command line.</li>
</ol>

<p class="hinweis">Button not working? Some Windows machines cannot resolve
<code>.local</code> names &ndash; look up the device's IP address in your router and
open that instead. If a monitor is attached, the address and a QR code are shown
on the box's screen.</p>

<p style="opacity:.6;margin-top:2.5rem">Brainbox &middot; brainarbeit.com &middot;
PolyForm Noncommercial</p>
</body></html>
HTML

echo "  Startkarte ($ART) abgelegt: $ZIEL/START-HIER.txt, START-HERE.txt, START-HIER.html"
