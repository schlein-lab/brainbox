#!/usr/bin/env bash
set -uo pipefail
log(){ printf '\033[36m[system]\033[0m %s\n' "$*"; }
SUDO(){ echo "${SUDO_PASSWORD:-}" | sudo -S "$@" 2>/dev/null; }
. "$(dirname "$(readlink -f "$0")")/lib-service.sh"

[ -n "${SUDO_PASSWORD:-}" ] || { log "no SUDO_PASSWORD in env — skipping privileged stage"; exit 0; }

svc_announce

if svc_dry_run; then
  svc_dry "/etc/sysctl.d/99-portioneer.conf schreiben (swappiness=10, admin_reserve=256M) + sysctl --system"
else
  SUDO tee /etc/sysctl.d/99-portioneer.conf >/dev/null <<'CONF'
# portioneer tuning
vm.swappiness = 10
vm.admin_reserve_kbytes = 262144
CONF
  SUDO sysctl --system >/dev/null 2>&1 && log "sysctl applied (swappiness=10, admin_reserve=256M)"
fi

case "$(svc_mgr)" in

pninit)
  if grep -qE '^[[:space:]]*sshd\|' "$PN_INIT_CONF" 2>/dev/null; then
    log "sshd-Schutz: bereits strukturell — 'sacred' in $PN_INIT_CONF (pn-critical.slice, respawn durch PID 1)"
  else
    svc_warn "sshd steht NICHT in $PN_INIT_CONF — die Reparatur-Tuer haengt an nichts."
    svc_warn "  Setzen mit:  pnctl add sshd sacred /usr/sbin/sshd -D -e"
  fi
  if [ -d /sys/fs/cgroup/pn.slice ]; then
    log "io/cpu/memory-Delegation: vorhanden (pn.slice-Baum von pn-cgtree)"
  else
    svc_warn "pn.slice fehlt — ohne delegierten Baum lehnt pnd governte Jobs mit rc126 ab."
    svc_warn "  Bauen mit:  sudo /usr/local/bin/pn-cgtree   (Boot-oneshot 'pn-cgtree' in $PN_INIT_CONF)"
  fi
  ;;

systemd)
  SSHU=$(systemctl list-unit-files --type=service 2>/dev/null | grep -oE '^ssh[d]?\.service' | head -1)
  SSHU=${SSHU:-ssh.service}
  if svc_dry_run; then
    svc_dry "Drop-in /etc/systemd/system/$SSHU.d/portioneer.conf (OOMScoreAdjust=-900) + restart $SSHU"
    svc_dry "Drop-in /etc/systemd/system/user@.service.d/10-portioneer-delegate.conf (Delegate=...)"
  else
    SUDO mkdir -p "/etc/systemd/system/$SSHU.d"
    SUDO tee "/etc/systemd/system/$SSHU.d/portioneer.conf" >/dev/null <<'CONF'
# portioneer: never let the OOM killer take sshd (remote access must survive overload).
[Service]
OOMScoreAdjust=-900
CONF
    log "sshd ($SSHU) OOMScoreAdjust=-900 drop-in written"
    SUDO mkdir -p /etc/systemd/system/user@.service.d
    SUDO tee /etc/systemd/system/user@.service.d/10-portioneer-delegate.conf >/dev/null <<'CONF'
# portioneer: delegate io (and cpuset) to the user manager so pnd can shape per-job IO.
[Service]
Delegate=cpu cpuset io memory pids
CONF
    SUDO systemctl daemon-reload 2>/dev/null
    log "io delegation staged (effective after reboot/relogin)"
    SUDO systemctl restart "$SSHU" 2>/dev/null && log "$SSHU restarted (existing sessions kept)"
  fi
  ;;

none)
  svc_skip "sshd-OOM-Schutz und io-Delegation: keine Dienstverwaltung erkannt, nichts gesetzt."
  ;;
esac

log "done."
