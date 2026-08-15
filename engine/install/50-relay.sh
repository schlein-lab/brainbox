#!/usr/bin/env bash
set -euo pipefail
REPO="$HOME/portioneer"
USERD="$HOME/.config/systemd/user"
BIN="$HOME/.local/bin"
. "$(dirname "$(readlink -f "$0")")/lib-service.sh"
ADAPTER_USER="${PN_ADAPTER_USER:-adapter}"
ADAPTER_UID="${PN_ADAPTER_UID:-4003}"
RELAY_DIR="$HOME/.local/share/portioneer/relay"
BROKER_GROUP="${PND_BROKER_GROUP:-pnbroker}"
BROKER_DIR="${PND_BROKER_DIR:-/run/portioneer}"
BROKER_SOCK="${PND_BROKER_SOCK:-$BROKER_DIR/pnd-broker.sock}"
PND_USER="$(id -un)"
log(){ printf '\033[36m[relay]\033[0m %s\n' "$*"; }

mkdir -p "$BIN" "$USERD" "$RELAY_DIR"
chmod 0700 "$RELAY_DIR" 2>/dev/null || true

if ! id "$ADAPTER_USER" >/dev/null 2>&1; then
  log "creating de-privileged relay broker system user $ADAPTER_USER (uid $ADAPTER_UID; needs root)"
  sudo useradd --system --uid "$ADAPTER_UID" --no-create-home --shell /usr/sbin/nologin \
    "$ADAPTER_USER" 2>/dev/null \
    || sudo useradd --system --no-create-home --shell /usr/sbin/nologin "$ADAPTER_USER" 2>/dev/null \
    || log "could not create $ADAPTER_USER (no root?) — drop-privilege/broker-assert will fail closed"
fi
if id "$ADAPTER_USER" >/dev/null 2>&1 && command -v setfacl >/dev/null 2>&1; then
  sudo setfacl -R -m "u:${ADAPTER_USER}:rwX" "$RELAY_DIR" 2>/dev/null \
    && sudo setfacl -R -d -m "u:${ADAPTER_USER}:rwX" "$RELAY_DIR" 2>/dev/null \
    && log "granted $ADAPTER_USER read+write access to $RELAY_DIR (ACL; system-unit path)" || true
  sudo setfacl -m "u:${ADAPTER_USER}:x" "$HOME" "$HOME/.local" "$HOME/.local/share" \
    "$HOME/.local/share/portioneer" 2>/dev/null || true
  QDB="$HOME/.local/share/portioneer/queue.db"
  [ -e "$QDB" ] && sudo setfacl -m "u:${ADAPTER_USER}:r" "$QDB" 2>/dev/null || true
fi

if ! getent group "$BROKER_GROUP" >/dev/null 2>&1; then
  log "creating broker group $BROKER_GROUP (needs root)"
  sudo groupadd --system "$BROKER_GROUP" 2>/dev/null \
    || log "could not create group $BROKER_GROUP (no root?) — broker socket stays uid-only (fail safe)"
fi
add_to_broker_group(){
  local u="$1"
  id "$u" >/dev/null 2>&1 || return 0
  if ! id -nG "$u" 2>/dev/null | tr ' ' '\n' | grep -qx "$BROKER_GROUP"; then
    sudo usermod -aG "$BROKER_GROUP" "$u" 2>/dev/null \
      && log "added $u to group $BROKER_GROUP" \
      || log "could not add $u to $BROKER_GROUP (no root?)"
  fi
}
if getent group "$BROKER_GROUP" >/dev/null 2>&1; then
  add_to_broker_group "$ADAPTER_USER"
  add_to_broker_group "$PND_USER"
  log "NOTE: a new login session is needed for $PND_USER to pick up $BROKER_GROUP; restart pnd from"
  log "      a fresh session (or reboot) so pnd's process holds the group and can chgrp its socket."
fi
svc_announce
case "$(svc_mgr)" in
pninit)
  PN_PORTIONEER_RUN="${PN_PORTIONEER_RUN:-/usr/local/bin/pn-portioneer-run}"
  if [ -x "$PN_PORTIONEER_RUN" ]; then
    svc_declare portioneer-run oneshot "$PN_PORTIONEER_RUN"
    if ! svc_dry_run; then
      svc_priv "$PN_PORTIONEER_RUN" || svc_warn "  $PN_PORTIONEER_RUN jetzt nicht ausfuehrbar (root noetig?) — greift spaetestens beim naechsten Boot"
    fi
  else
    svc_warn "$PN_PORTIONEER_RUN fehlt — $BROKER_DIR entsteht bei keinem Boot."
    svc_warn "  Der Boot-Schritt waere: pnctl add portioneer-run oneshot /usr/local/bin/pn-portioneer-run"
  fi
  ;;
systemd)
  if [ -d /etc/tmpfiles.d ] || sudo test -d /etc/tmpfiles.d 2>/dev/null; then
    TMPFILE="/etc/tmpfiles.d/portioneer-broker.conf"
    log "installing SYSTEM tmpfiles.d entry $TMPFILE ($BROKER_DIR 0770 $PND_USER:$BROKER_GROUP; needs root)"
    printf 'd %s 0770 %s %s -\n' "$BROKER_DIR" "$PND_USER" "$BROKER_GROUP" \
      | sudo tee "$TMPFILE" >/dev/null 2>&1 \
      && sudo systemd-tmpfiles --create "$TMPFILE" 2>/dev/null \
      && log "broker dir $BROKER_DIR created (0770 $PND_USER:$BROKER_GROUP)" \
      || log "could not install tmpfiles.d entry (no root?) — create $BROKER_DIR manually (see docs §6.4)"
  fi
  ;;
none) svc_skip "$BROKER_DIR bekommt keinen Boot-Schritt — nach einem Neustart fehlt es." ;;
esac
case "$(svc_mgr)" in
pninit)
  PND_ENVFILE="${PND_ENVFILE:-$PN_ETC/brainbox/pnd.env}"
  svc_envfile_set "$PND_ENVFILE" PND_BROKER_SOCK  "$BROKER_SOCK"
  svc_envfile_set "$PND_ENVFILE" PND_BROKER_GROUP "$BROKER_GROUP"
  svc_envfile_set "$PND_ENVFILE" PND_BROKER_UIDS  "$ADAPTER_UID"
  log "  (Socket binden: pnctl restart pnd)"
  ;;
systemd)
  PND_DROPIN_DIR="$USERD/pnd.service.d"
  mkdir -p "$PND_DROPIN_DIR"
  cat > "$PND_DROPIN_DIR/broker-socket.conf" <<EOF
# Installed by install/50-relay.sh — gives pnd a SECOND, group-accessible broker socket so the
# de-privileged off-LAN broker (pn-relayd as uid $ADAPTER_UID) can reach it. Default socket is
# untouched (still uid-only 0600). peercred attestation + authz are IDENTICAL on both sockets.
[Service]
Environment=PND_BROKER_SOCK=$BROKER_SOCK
Environment=PND_BROKER_GROUP=$BROKER_GROUP
EOF
  systemctl --user daemon-reload 2>/dev/null || true
  log "pnd broker-socket drop-in: PND_BROKER_SOCK=$BROKER_SOCK PND_BROKER_GROUP=$BROKER_GROUP"
  log "  (restart pnd to bind the broker socket: systemctl --user restart pnd)"
  ;;
esac

chmod +x "$REPO/tools/pn-relayd" "$REPO/tools/pn-pair" "$REPO/tools/pn-rzserver" 2>/dev/null || true
ln -sf "$REPO/tools/pn-relayd"   "$BIN/pn-relayd"
ln -sf "$REPO/tools/pn-pair"     "$BIN/pn-pair"
ln -sf "$REPO/tools/pn-rzserver" "$BIN/pn-rzserver"
log "linked pn-relayd + pn-pair + pn-rzserver into $BIN"

case "$(svc_mgr)" in

pninit)
  svc_declare pn-relayd \
    "disabled user=$ADAPTER_UID env=RELAY_ENABLED=0 env=RELAY_BROKER_UID=$ADAPTER_UID env=PND_BROKER_SOCK=$BROKER_SOCK env=RELAY_PND_SOCK=$BROKER_SOCK env=XDG_DATA_HOME=$HOME/.local/share env=RELAY_REQUIRE_DEPRIVILEGED_BROKER=1" \
    /usr/bin/python3 "$REPO/tools/pn-relayd"
  ;;

systemd)
  cp "$REPO/systemd/pn-relayd.service" "$USERD/pn-relayd.service"
  systemctl --user daemon-reload
  if [ -f "$REPO/systemd/pn-relayd-system.service" ]; then
    sed "s|@OPERATOR_HOME@|$HOME|g" "$REPO/systemd/pn-relayd-system.service" \
      | sudo tee /etc/systemd/system/pn-relayd.service >/dev/null 2>&1 \
      && sudo systemctl daemon-reload 2>/dev/null \
      && log "installed SYSTEM pn-relayd.service (User=adapter -> broker socket; DISABLED by default)" \
      || log "could not install system pn-relayd unit (no root?) — user unit + drop-to-4003 remains"
  fi
  ;;

none)
  svc_skip "pn-relayd wird nicht als Dienst hinterlegt — keine Dienstverwaltung erkannt."
  ;;
esac
log "pn-relayd hinterlegt, ABGESCHALTET (siehe docs/relay-security.md zum Einschalten)"
log "status: pn-relayd --status   |   audit: pn-relayd --audit --verify"
