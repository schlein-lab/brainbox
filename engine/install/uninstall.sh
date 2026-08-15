#!/usr/bin/env bash
set -uo pipefail
USERD="$HOME/.config/systemd/user"
log(){ printf '\033[33m[uninstall]\033[0m %s\n' "$*"; }
SUDO(){ echo "${SUDO_PASSWORD:-}" | sudo -S "$@" 2>/dev/null; }
[ -f "$HOME/.env" ] && { set -a; . "$HOME/.env"; set +a; }
. "$(dirname "$(readlink -f "$0")")/lib-service.sh"

svc_announce

case "$(svc_mgr)" in

pninit)
  for s in pn-relayd portioneer-net pn-llmd pnd; do
    svc_undeclare "$s"
  done
  pninit_has portioneer-run && log "portioneer-run bleibt (fremder Boot-Schritt; bewusst: sudo pnctl rm portioneer-run)"
  log "Tiers pn-critical/pn-batch/pn-misc bleiben (gehoeren pn-init, nicht portioneer)"
  log "  $PN_ETC/brainbox/pnd.env wird NICHT angefasst (Betreiberdatei, fremder Inhalt)"
  ;;

systemd)
  if svc_dry_run; then
    svc_dry "systemctl --user disable --now pnd.service pn-llmd.service"
  else
    systemctl --user disable --now pnd.service pn-llmd.service 2>/dev/null || true
  fi
  for svc in brainbox-portal zyrkel reprofleet; do
    f="$USERD/$svc.service.d/pn.conf"
    if [ -f "$f" ]; then
      if [ -f "$f.pn-bak" ]; then mv "$f.pn-bak" "$f"; else rm -f "$f"; rmdir "$USERD/$svc.service.d" 2>/dev/null || true; fi
    fi
  done
  rm -f "$USERD/pnd.service" "$USERD/pn-llmd.service" \
        "$USERD/pn-interactive.slice" "$USERD/pn-batch.slice"
  if svc_dry_run; then
    svc_dry "systemctl --user daemon-reload; restart brainbox-portal zyrkel reprofleet"
  else
    systemctl --user daemon-reload 2>/dev/null
    systemctl --user restart brainbox-portal.service zyrkel.service reprofleet.service 2>/dev/null || true
  fi
  ;;

none)
  svc_skip "Keine Dienstverwaltung erkannt — es gibt nichts abzumelden."
  ;;
esac

svc_dry_run || rm -f "$HOME/.local/bin/pn"

if [ -n "${SUDO_PASSWORD:-}" ] && ! svc_dry_run; then
  [ -x "$HOME/portioneer/tools/pn-net" ] && SUDO "$HOME/portioneer/tools/pn-net" clear 2>/dev/null || true
  if [ "$(svc_mgr)" = "systemd" ]; then
    SUDO systemctl disable --now portioneer-net.service 2>/dev/null || true
    SUDO rm -f /etc/systemd/system/portioneer-net.service
    SSHU=$(systemctl list-unit-files --type=service 2>/dev/null | grep -oE '^ssh[d]?\.service' | head -1); SSHU=${SSHU:-ssh.service}
    SUDO rm -f "/etc/systemd/system/$SSHU.d/portioneer.conf" \
              /etc/systemd/system/user@.service.d/10-portioneer-delegate.conf
    SUDO systemctl daemon-reload 2>/dev/null
    SUDO systemctl restart "$SSHU" 2>/dev/null || true
  fi
  SUDO rm -f /etc/sysctl.d/99-portioneer.conf
  log "system drop-ins + network shaping removed"
fi

[ "${1:-}" = "--purge" ] && ! svc_dry_run && { rm -rf "$HOME/.local/share/portioneer"; log "purged job db + logs"; }
log "done. (Dienste abgemeldet; Host zurueck auf Stock. crontab: retrofit_cron.py --revert, falls umhuellt)"
