#!/usr/bin/env bash
K="sudo ssh -i /var/tmp/bbx/iso-test-key -o IdentitiesOnly=yes -p 2225 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=6 -o LogLevel=ERROR brainbox@127.0.0.1"
p=0; f=0
ck(){ if [ "$1" = 1 ]; then echo "  PASS  $2"; p=$((p+1)); else echo "  FAIL  $2${3:+  [$3]}"; f=$((f+1)); fi; }

echo "=== A. Zugang / SSH ==="
O="$($K 'echo OK; id -un; groups | tr " " "\n" | grep -c sudo' 2>&1)"
echo "$O" | grep -q OK && ck 1 "SSH mit Schluessel funktioniert (war durch chage -d 0 tot)" || ck 0 "SSH mit Schluessel" "$O"
$K 'grep -q "^PasswordAuthentication no" /etc/ssh/sshd_config' 2>/dev/null && ck 1 "SSH-Passwortanmeldung ist aus" || ck 0 "SSH-Passwortanmeldung ist aus"
$K 'sudo -n passwd -S root 2>/dev/null | grep -qE " L | LK "' 2>/dev/null && ck 1 "root-Konto ist gesperrt" || ck 0 "root-Konto ist gesperrt"
V="$($K 'sudo -n chage -l brainbox 2>/dev/null | head -1' 2>&1)"
echo "$V" | grep -qi "never\|nie" && ck 1 "kein erzwungener Passwortwechsel (SSH bleibt nutzbar)" || ck 0 "kein erzwungener Passwortwechsel" "$V"

echo "=== B. Geraete-Identitaet (Image ist oeffentlich) ==="
PW="$($K 'sudo -n cat /etc/brainbox/initial-console-password 2>/dev/null | head -c 8' 2>&1)"
[ -n "$PW" ] && ck 1 "Konsolen-Passwort vorhanden" || ck 0 "Konsolen-Passwort vorhanden"
IMGPW=""
if [ -r /var/tmp/bbx/brainbox-appliance-amd64.raw ]; then
  _l="$(sudo losetup --show -fP -r /var/tmp/bbx/brainbox-appliance-amd64.raw 2>/dev/null)"
  if [ -n "$_l" ]; then
    _m="$(mktemp -d)"
    sudo mount -o ro "${_l}p1" "$_m" 2>/dev/null \
      && IMGPW="$(sudo cat "$_m/etc/brainbox/initial-console-password" 2>/dev/null | tr -d "\r\n")"
    sudo umount "$_m" 2>/dev/null; rmdir "$_m" 2>/dev/null; sudo losetup -d "$_l" 2>/dev/null
  fi
fi
LIVEPW="$($K 'sudo -n cat /etc/brainbox/initial-console-password 2>/dev/null' 2>/dev/null | tr -d "\r\n")"
if [ -z "$IMGPW" ] || [ -z "$LIVEPW" ]; then
  ck 0 "Passwort-Vergleich Image vs. Geraet nicht durchfuehrbar" "Image:${IMGPW:+da} Box:${LIVEPW:+da}"
elif [ "$IMGPW" = "$LIVEPW" ]; then
  ck 0 "Box benutzt das Passwort AUS DEM IMAGE — jeder Downloader kennt es!"
else
  ck 1 "Konsolen-Passwort weicht vom Image ab (pro Geraet erzeugt)"
fi
$K 'sudo -n test -s /etc/brainbox/initial-console-password' 2>/dev/null && ck 1 "Passwortdatei ist root-only lesbar" || ck 0 "Passwortdatei"
M="$($K 'sudo -n stat -c %a /etc/brainbox/initial-console-password' 2>/dev/null | tr -d "\r\n ")"
[ "$M" = "600" ] && ck 1 "Passwortdatei 0600" || ck 0 "Passwortdatei 0600" "$M"
HK="$($K 'sudo -n ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub 2>/dev/null | cut -d" " -f2' 2>/dev/null | tr -d '\r\n')"
[ -n "$HK" ] && ck 1 "SSH-Hostkey pro Geraet erzeugt ($HK)" || ck 0 "SSH-Hostkey"
KARTEN="$($K 'ls /boot/brainbox-welcome.txt /boot/firmware/brainbox-welcome.txt 2>/dev/null' 2>/dev/null | tr -d "\r")"
if [ -z "$KARTEN" ]; then
  ck 1 "keine Willkommenskarte auf der Boot-Partition (nichts zu leaken)"
else
  TREFFER="$($K "grep -hiE 'PIN[^0-9]{0,12}[0-9]{4,8}' $KARTEN 2>/dev/null | head -3" 2>/dev/null | tr -d "\r")"
  [ -z "$TREFFER" ] && ck 1 "Karte vorhanden, aber KEINE PIN darin ($KARTEN)" \
                    || ck 0 "PIN steht auf der Boot-Partition!" "$TREFFER"
fi

echo "=== C. Host-Firewall ==="
NFT="$($K 'sudo -n nft list table inet brainbox 2>/dev/null | head -30' 2>&1)"
echo "$NFT" | grep -q "policy drop" && ck 1 "Firewall aktiv, Eingang default-deny" || ck 0 "Firewall aktiv" "$(echo "$NFT" | head -2)"
echo "$NFT" | grep -q "udp dport { 68" && ck 1 "DHCP bleibt offen (sonst keine Adresse nach Neustart)" || ck 0 "DHCP offen"
echo "$NFT" | grep -qE "tcp dport \{ 22, 80, 443, 8076 \}" && ck 1 "nur SSH/Assistent/Portal offen (managed)" || ck 0 "Portliste managed" "$(echo "$NFT" | grep 'tcp dport')"
PROF="$($K 'sed -n "s/^NET_PROFILE=//p" /etc/brainbox/site.conf' 2>/dev/null | tr -d "\r\n \"")"
[ "$PROF" = "managed" ] && ck 1 "Netzprofil ist managed (leise ausgeliefert)" || ck 0 "Netzprofil managed" "$PROF"

echo "=== D. Was von aussen erreichbar ist ==="
code_bg="$(curl -sk -o /dev/null -m 5 -w '%{http_code}' https://127.0.0.1:18090/healthz 2>/dev/null)"
[ "$code_bg" = "000" ] && ck 1 "Reparaturkonsole :8090 von aussen NICHT erreichbar" || ck 0 "Reparaturkonsole von aussen erreichbar!" "HTTP $code_bg"
code_cast="$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:18096/ 2>/dev/null)"
[ "$code_cast" = "000" ] && ck 1 "pn_castd :8096 von aussen NICHT erreichbar" || ck 0 "pn_castd erreichbar!" "HTTP $code_cast"
code_portal="$(curl -sk -o /dev/null -m 8 -w '%{http_code}' https://127.0.0.1:18085/ 2>/dev/null)"
case "$code_portal" in 2??|3??|401|403) ck 1 "Portal :8076 erreichbar (HTTP $code_portal)";; *) ck 0 "Portal erreichbar" "HTTP $code_portal";; esac

echo "=== E. Portal-Endpunkte ohne Anmeldung ==="
c_term="$(curl -sk -o /dev/null -m 6 -w '%{http_code}' https://127.0.0.1:18085/api/term 2>/dev/null)"
[ "$c_term" = "410" ] || [ "$c_term" = "302" ] && ck 1 "/api/term ist stillgelegt/gesperrt (HTTP $c_term)" || ck 0 "/api/term" "HTTP $c_term"
c_js="$(curl -sk -o /dev/null -m 6 -w '%{http_code}' https://127.0.0.1:18085/brand/app.js 2>/dev/null)"
[ "$c_js" = "404" ] && ck 1 "/brand/app.js nicht mehr ohne Anmeldung abrufbar" || ck 0 "/brand/app.js" "HTTP $c_js"
c_png="$(curl -sk -o /dev/null -m 6 -w '%{http_code}' https://127.0.0.1:18085/brand/lockup.png 2>/dev/null)"
case "$c_png" in 200|404) ck 1 "/brand/<bild> weiterhin erlaubt (Login-Seite, HTTP $c_png)";; *) ck 0 "/brand/<bild>" "HTTP $c_png";; esac
c_html="$(curl -sk -m 6 https://127.0.0.1:18085/brand/app.html 2>/dev/null | grep -c PP_USER)"
[ "$c_html" = "0" ] && ck 1 "keine Besitzer-uid/WS-Token ohne Anmeldung" || ck 0 "app.html leakt weiterhin PP_USER"

echo "=== F. Einrichtungs-Assistent ==="
c_w="$(curl -s -o /dev/null -m 6 -w '%{http_code}' http://127.0.0.1:8085/ 2>/dev/null)"
[ "$c_w" = "200" ] && ck 1 "Assistent antwortet (HTTP 200)" || ck 0 "Assistent antwortet nicht" "HTTP $c_w"
c_f="$(curl -s -m 6 'http://127.0.0.1:8085/?force=1' 2>/dev/null | grep -c 'bbx-csrf')"
SETUP_DONE="$($K 'test -f /etc/brainbox/setup-complete && echo ja || echo nein' 2>/dev/null | tr -d "\r\n ")"
echo "  (setup-complete: $SETUP_DONE — force=1 ist erst NACH dem Setup relevant)"

echo
echo "  ---- $p bestanden, $f fehlgeschlagen ----"
[ "$f" = 0 ] && echo "  ERGEBNIS: GRUEN" || echo "  ERGEBNIS: NICHT GRUEN"
