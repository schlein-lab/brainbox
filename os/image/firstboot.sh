#!/bin/bash
set -u

[ -f /etc/brainbox/service.env ] && . /etc/brainbox/service.env 2>/dev/null || true
APP_USER="${BRAINBOX_USER:-${SERVICE_USER:-brainbox}}"
APP_HOME="${SERVICE_HOME:-$(getent passwd "$APP_USER" | cut -d: -f6)}"; APP_HOME="${APP_HOME:-/home/$APP_USER}"
FAIL=0
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
MARKER="/var/lib/brainbox/firstboot.done"
GUARD="/etc/brainbox/is-appliance"
WELCOME="/boot/firmware/brainbox-welcome.txt"; [ -d /boot/firmware ] || WELCOME="/boot/brainbox-welcome.txt"

log(){ echo "[firstboot] $*"; }
die(){ echo "[firstboot] ABORT: $*" >&2; exit 1; }

SCHRITTE=6
schritt(){
  mkdir -p /run/brainbox 2>/dev/null || true
  printf 'Schritt %s von %s: %s\n' "$1" "$SCHRITTE" "$2" > /run/brainbox/boot-status 2>/dev/null || true
  log "Schritt $1 von $SCHRITTE: $2"
}

[ -f "$GUARD" ] || die "no $GUARD — refusing (not an appliance image / could be the dev box)"
[ -f "$MARKER" ] && { log "already provisioned — nothing to do"; exit 0; }
[ "$(id -u)" = "0" ] || die "must run as root"
mkdir -p /var/lib/brainbox /etc/brainbox

if [ -e "$APP_HOME/.env" ] || [ -e "$APP_HOME/.config/brainbox/pak" ] \
   || [ -e "$APP_HOME/.claude.json" ] || [ -e "$APP_HOME/.llmpool" ] || [ -e "$APP_HOME/.claude" ]; then
  log "WARNING: build-box secrets present in image — running factory-clean defensively"
  TARGET_HOME="$APP_HOME" bash "$SELF_DIR/brainbox-factory-clean.sh" --apply || true
fi

schritt 1 "Festplatte wird auf die volle Groesse gebracht"
ROOTSRC="$(findmnt -no SOURCE / 2>/dev/null)"
case "$ROOTSRC" in
  /dev/*)
    RPART="$(basename "$ROOTSRC")"
    RDISK="$(lsblk -no PKNAME "$ROOTSRC" 2>/dev/null | head -1)"
    RNUM="$(cat "/sys/class/block/$RPART/partition" 2>/dev/null)"
    if [ -n "$RDISK" ] && [ -n "$RNUM" ]; then
      if command -v growpart >/dev/null 2>&1; then
        growpart "/dev/$RDISK" "$RNUM" >/dev/null 2>&1 || true
      elif command -v sfdisk >/dev/null 2>&1; then
        echo ", +" | sfdisk --no-reread -N "$RNUM" "/dev/$RDISK" >/dev/null 2>&1 || true
        partx -u "/dev/$RDISK" >/dev/null 2>&1 || true
      fi
      command -v resize2fs >/dev/null 2>&1 && resize2fs "$ROOTSRC" >/dev/null 2>&1 || true
      log "root grow attempted: disk=/dev/$RDISK part=$RNUM src=$ROOTSRC ($(df -h / | awk 'NR==2{print $2" total, "$4" free"}'))"
    fi
    ;;
esac

serial="$(sed -n 's/^Serial\s*:\s*0*//p' /proc/cpuinfo 2>/dev/null | tail -c7 | tr -d '\n')"
[ -n "$serial" ] || serial="$(head -c3 /dev/urandom | od -An -tx1 | tr -d ' \n')"
schritt 2 "Die Box bekommt ihren eigenen Namen"
NEWHOST="brainbox-${serial}"
log "hostname -> $NEWHOST"
printf '%s\n' "$NEWHOST" >/etc/hostname
hostname "$NEWHOST" 2>/dev/null || hostnamectl set-hostname "$NEWHOST" 2>/dev/null \
  || log "WARN: hostname not applied to the running kernel (brainbox-earlyboot applies it next boot)"
if grep -q "^127.0.1.1" /etc/hosts 2>/dev/null; then sed -i "s/^127.0.1.1.*/127.0.1.1\t$NEWHOST/" /etc/hosts
else printf '127.0.1.1\t%s\n' "$NEWHOST" >>/etc/hosts; fi

schritt 3 "Eigene Schluessel werden erzeugt (dauert am laengsten)"
rm -f /etc/machine-id /var/lib/dbus/machine-id
systemd-machine-id-setup >/dev/null 2>&1 || dbus-uuidgen --ensure 2>/dev/null || true
[ -s /etc/machine-id ] || tr -d - </proc/sys/kernel/random/uuid >/etc/machine-id 2>/dev/null || true
ln -sf /etc/machine-id /var/lib/dbus/machine-id 2>/dev/null || true
[ -s /etc/machine-id ] || { FAIL=1; log "ERROR: machine-id regeneration failed"; }
rm -f /etc/ssh/ssh_host_*; ssh-keygen -A >/dev/null 2>&1 || true
ls /etc/ssh/ssh_host_*_key >/dev/null 2>&1 || { FAIL=1; log "ERROR: SSH host-key regeneration failed"; }

schritt 4 "Zugangsdaten werden fuer genau dieses Geraet gewuerfelt"
NEWPW="$(head -c 4096 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 20)"
if [ ${#NEWPW} -ge 20 ] && printf '%s:%s\n' "$APP_USER" "$NEWPW" | chpasswd 2>/dev/null; then
  ( umask 077; printf '%s\n' "$NEWPW" > /etc/brainbox/initial-console-password )
  chown root:root /etc/brainbox/initial-console-password 2>/dev/null || true
  chmod 0600 /etc/brainbox/initial-console-password 2>/dev/null || true
  chage -d -1 "$APP_USER" 2>/dev/null || true
  chage -M 99999 "$APP_USER" 2>/dev/null || true
  log "per-Geraet Konsolen-Passwort gesetzt (der im Image gebackene Wert gilt nicht mehr)"
else
  FAIL=1; log "ERROR: per-Geraet Konsolen-Passwort konnte nicht gesetzt werden"
fi

SETUP_CODE="$(printf '%06d' "$(( $(od -An -N4 -tu4 /dev/urandom | tr -d ' ') % 1000000 ))")"
if [ -n "$SETUP_CODE" ]; then
  ( umask 077; printf '%s\n' "$SETUP_CODE" > /etc/brainbox/setup-code )
  chown root:root /etc/brainbox/setup-code 2>/dev/null || true
  chmod 0600 /etc/brainbox/setup-code 2>/dev/null || true
  log "Setup-Code (6-stellig) fuer den Assistenten gewuerfelt"
else
  FAIL=1; log "ERROR: Setup-Code konnte nicht erzeugt werden"
fi

for BOOTDIR in /boot/firmware /boot; do
  PRESEED="$BOOTDIR/brainbox-authorized_keys"
  [ -r "$PRESEED" ] || continue
  AK="$APP_HOME/.ssh/authorized_keys"
  install -d -m 0700 -o "$APP_USER" -g "$APP_USER" "$APP_HOME/.ssh" 2>/dev/null || true
  touch "$AK" 2>/dev/null || true
  ADDED=0
  while IFS= read -r K; do
    case "$K" in ""|\#*) continue;; esac
    grep -qxF "$K" "$AK" 2>/dev/null || { printf '%s\n' "$K" >> "$AK"; ADDED=$((ADDED+1)); }
  done < "$PRESEED"
  chown "$APP_USER:$APP_USER" "$AK" 2>/dev/null || true
  chmod 0600 "$AK" 2>/dev/null || true
  shred -u "$PRESEED" 2>/dev/null || rm -f "$PRESEED"
  log "SSH-Vorbelegung von $BOOTDIR uebernommen ($ADDED neue Schluessel) und dort geloescht"
  break
done

if command -v avahi-daemon >/dev/null 2>&1; then
  log "mDNS name -> brainbox.local (system hostname $NEWHOST kept for SSH)"
  if [ -f /etc/avahi/avahi-daemon.conf ]; then
    if grep -qE '^[[:space:]]*#?[[:space:]]*host-name=' /etc/avahi/avahi-daemon.conf; then
      sed -i 's/^[[:space:]]*#\?[[:space:]]*host-name=.*/host-name=brainbox/' /etc/avahi/avahi-daemon.conf
    else
      sed -i '/^\[server\]/a host-name=brainbox' /etc/avahi/avahi-daemon.conf
    fi
  fi
  mkdir -p /etc/avahi/services
  systemctl enable --now avahi-daemon 2>/dev/null || true
fi

systemctl disable --now keepalived 2>/dev/null || true
rm -f /etc/keepalived/keepalived.conf 2>/dev/null || true

. /etc/brainbox/site.conf 2>/dev/null || true
if [ "${HA_ENABLED:-0}" = "1" ] && ! grep -q '^VRRP_AUTH_PASS=' /etc/brainbox/secrets.env 2>/dev/null; then
  vpass="$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  ( umask 077; printf 'VRRP_AUTH_PASS=%s\n' "$vpass" >> /etc/brainbox/secrets.env )
  chmod 600 /etc/brainbox/secrets.env 2>/dev/null || true
  log "minted per-box VRRP auth_pass"
fi

schritt 5 "Eigenes Zertifikat der Box wird ausgestellt"
runuser -u "$APP_USER" -- python3 "$APP_HOME/brainarbeit/cockpit/server/pn_certs.py" ensure >/dev/null 2>&1 || true
schritt 6 "Einrichtungs-Assistent wird vorbereitet"
runuser -u "$APP_USER" -- python3 "$APP_HOME/.local/bin/brainbox-portal" setup >/dev/null 2>&1 \
  || runuser -u "$APP_USER" -- python3 "$APP_HOME/brainarbeit/cockpit/server/brainbox-portal" setup >/dev/null 2>&1 || true

PORTAL_PORT="$(runuser -u "$APP_USER" -- python3 - <<'PY' 2>/dev/null
import json, os
try:
    print(int((json.load(open(os.path.expanduser("~/.config/brainbox-portal/config.json"))) or {}).get("port") or 8076))
except Exception:
    print(8076)
PY
)"; PORTAL_PORT="${PORTAL_PORT:-8076}"

if command -v avahi-daemon >/dev/null 2>&1; then
  cat >/etc/avahi/services/brainbox.service <<XML
<?xml version="1.0" standalone="no"?><!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">Brainbox on %h</name>
  <service><type>_http._tcp</type><port>80</port></service>
  <service><type>_https._tcp</type><port>$PORTAL_PORT</port></service>
</service-group>
XML
fi

[ -f /etc/brainbox/network.conf ] || printf 'MODE=dhcp\n' > /etc/brainbox/network.conf

FP="$(runuser -u "$APP_USER" -- python3 "$APP_HOME/brainarbeit/cockpit/server/pn_certs.py" info 2>/dev/null | sed -n 's/^fingerprint: //p')"
karte(){
  echo "==================== BRAINBOX ===================="
  echo " Einrichten : http://$NEWHOST.local/      (oder http://<IP>/)"
  echo " Portal     : https://$NEWHOST.local:$PORTAL_PORT/   (nach der Einrichtung)"
  echo " Zertifikat : https://$NEWHOST.local:$PORTAL_PORT/trust"
  echo " PIN        : $1"
  echo " CA (SHA-256): ${FP:-<siehe /trust>}"
  echo " Hostname   : $NEWHOST"
  echo " Konsole    : Benutzer $APP_USER -- das Erst-Passwort steht auf dem Bildschirm der"
  echo "              Box. Im Assistenten ein eigenes setzen -- dann verschwindet es dort."
  echo " SSH        : nur mit Schluessel. Entweder im Assistenten hinterlegen, ODER vor dem"
  echo "              ersten Start eine Datei 'brainbox-authorized_keys' mit dem oeffentlichen"
  echo "              Schluessel auf die BOOT-Partition der Karte legen -- sie wird beim"
  echo "              Start uebernommen und danach geloescht."
  echo "================================================="
}
karte "steht bewusst NICHT auf dieser Karte (Boot-Partition ist fuer jeden lesbar).
              Sie legen die PIN im Einrichtungs-Assistenten fest -- http://$NEWHOST.local/ --
              und er zeigt sie dort einmalig an. Verloren? An der Konsole als $APP_USER
              anmelden (Passwort steht am Bildschirm der Box) und neu setzen mit:
              brainbox-portal setup --pin <neue PIN>" | tee "$WELCOME" 2>/dev/null || true

if [ "$FAIL" = "0" ]; then
  date > "$MARKER"
  printf 'Dienste werden gestartet\n' > /run/brainbox/boot-status 2>/dev/null || true
  log "first-boot provisioning complete"
else
  log "first-boot provisioning INCOMPLETE (identity regen failed) — marker NOT written; retries next boot"
  exit 1
fi
