#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
[ -f "$HOME/.env" ] && { set -a; . "$HOME/.env"; set +a; }
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

. "$(pwd)/lib-service.sh"

echo "== portioneer install =="
svc_announce
if [ "$(svc_mgr)" = "none" ]; then
  echo "[install] Ohne Dienstverwaltung wird NICHTS dauerhaft gestartet. Die Stufen laufen trotzdem"
  echo "[install] durch (Verknuepfungen, Verzeichnisse, Rechte) und melden jeden ausgelassenen Schritt."
fi

stage(){ bash "$1" || { rc=$?; echo "[install] ABBRUCH in $1 (rc=$rc) — Anlage ist UNVOLLSTAENDIG"; exit "$rc"; }; }

bash 00-system.sh || echo "[install] system stage skipped/failed (non-fatal)"
stage 10-governor.sh
stage 20-daemon.sh
stage 30-llmd.sh
if [ -n "${SUDO_PASSWORD:-}" ]; then
  echo "${SUDO_PASSWORD}" | sudo -S env REPO="$(pwd)/.." TARGET_UID="$(id -u)" \
       PN_DRY_RUN="${PN_DRY_RUN:-0}" PN_INIT_CONF="${PN_INIT_CONF:-/etc/pn-init.conf}" \
       bash 40-net.sh \
    || echo "[install] network stage skipped/failed (non-fatal)"
else
  echo "[install] no SUDO_PASSWORD -> run 'sudo install/40-net.sh' to enable egress shaping"
fi
bash 50-relay.sh || echo "[install] relay stage skipped/failed (non-fatal)"

case "$(svc_mgr)" in
  pninit)  echo "== done. Try: pn status   |  Dienste: pnctl list   (relay: pn-relayd --status) ==" ;;
  systemd) echo "== done. Try: pn status   |  Dienste: systemctl --user status pnd pn-llmd   (relay: pn-relayd --status) ==" ;;
  *)       echo "== done (ohne Dienstverwaltung — nichts laeuft dauerhaft; siehe Meldungen oben) ==" ;;
esac
