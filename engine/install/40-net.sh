#!/usr/bin/env bash
set -euo pipefail

TARGET_UID="${TARGET_UID:-1000}"
REPO="${REPO:-$(getent passwd "$TARGET_UID" | cut -d: -f6)/portioneer}"
log(){ printf '\033[35m[net]\033[0m %s\n' "$*"; }
. "$(dirname "$(readlink -f "$0")")/lib-service.sh"
CONF="$PN_ETC/portioneer/net.conf"
UNIT="$PN_ETC/systemd/system/portioneer-net.service"

if [ "$(id -u)" -ne 0 ] && [ "$PN_ETC" = "/etc" ]; then echo "40-net.sh must run as root"; exit 1; fi

IFACE="$(ip route show default | awk '/default/{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"
[ -z "$IFACE" ] && { log "no default-route iface; skipping network shaping"; exit 0; }
log "egress iface: $IFACE  uid: $TARGET_UID"

mkdir -p "$(dirname "$CONF")"
if [ ! -f "$CONF" ]; then
  if svc_dry_run; then
    svc_dry "$CONF anlegen (ENABLE=1 IFACE=$IFACE UID=$TARGET_UID RATE_MBIT=1000 80/10/60)"
  else
  cat > "$CONF" <<EOF
# portioneer egress shaping. Re-run install or restart the 'portioneer-net' boot step after edits.
# RATE_MBIT = link ceiling estimate (relative protection; safe to set high)
# INTER_PCT = interactive/LAN guaranteed share; BATCH_PCT = batch guaranteed share
# BATCH_CEIL_PCT = batch borrow ceiling when the link is otherwise idle
ENABLE=1
IFACE=$IFACE
UID=$TARGET_UID
RATE_MBIT=1000
INTER_PCT=80
BATCH_PCT=10
BATCH_CEIL_PCT=60
EOF
  log "wrote $CONF"
  fi
else
  log "$CONF exists; keeping it"
fi

svc_announce

case "$(svc_mgr)" in

pninit)
  svc_declare portioneer-net oneshot "$REPO/tools/pn-net" apply
  if svc_dry_run; then
    svc_dry "$REPO/tools/pn-net apply   (mit 8-s-Totmannschalter)"
  else
    ( sleep 8; "$REPO/tools/pn-net" status >/dev/null 2>&1 || "$REPO/tools/pn-net" clear >/dev/null 2>&1 ) &
    DEADMAN=$!
    if "$REPO/tools/pn-net" apply; then
      kill "$DEADMAN" 2>/dev/null || true
      log "applied + als Boot-Schritt 'portioneer-net' deklariert. verify: $REPO/tools/pn-net status"
    else
      log "WARN: apply failed; dead-man switch will clear shaping shortly"
    fi
  fi
  ;;

systemd)
  mkdir -p "$(dirname "$UNIT")"
  sed "s|@REPO@|$REPO|g" "$REPO/systemd/portioneer-net.service" > "$UNIT"
  if svc_dry_run; then
    svc_dry "systemctl daemon-reload; enable portioneer-net.service; restart portioneer-net.service"
  else
    systemctl daemon-reload
    systemctl enable portioneer-net.service >/dev/null 2>&1 || true
    ( sleep 8; systemctl is-active --quiet portioneer-net || "$REPO/tools/pn-net" clear >/dev/null 2>&1 ) &
    DEADMAN=$!
    if systemctl restart portioneer-net.service; then
      kill "$DEADMAN" 2>/dev/null || true
      log "applied + enabled. verify: $REPO/tools/pn-net status"
    else
      log "WARN: apply failed; dead-man switch will clear shaping shortly"
    fi
  fi
  ;;

none)
  svc_skip "Kein Boot-Schritt fuer die Egress-Formung — sie ueberlebt keinen Neustart."
  svc_skip "  Einmalig anwenden: $REPO/tools/pn-net apply"
  ;;
esac
