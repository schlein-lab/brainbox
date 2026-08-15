#!/usr/bin/env bash
set -euo pipefail
REPO="${REPO:-$HOME/portioneer}"
SECRETS_DIR="${PN_SECRETS_DIR:-/var/lib/portioneer/secrets}"
LLMD_USER="${PN_LLMD_USER:-pn-llmd}"
USERD="${USERD:-$HOME/.config/systemd/user}"
log(){ printf '\033[35m[secrets]\033[0m %s\n' "$*"; }
. "$(dirname "$(readlink -f "$0")")/lib-service.sh"

if ! id "$LLMD_USER" >/dev/null 2>&1; then
  log "creating dedicated system user $LLMD_USER (needs root)"
  sudo useradd --system --no-create-home --shell /usr/sbin/nologin "$LLMD_USER" 2>/dev/null \
    || log "could not create $LLMD_USER (no root?) — falling back to current uid for dev"
fi

sudo mkdir -p "$SECRETS_DIR" 2>/dev/null || mkdir -p "$SECRETS_DIR"
if id "$LLMD_USER" >/dev/null 2>&1; then
  sudo chown "$LLMD_USER":"$LLMD_USER" "$SECRETS_DIR" 2>/dev/null || true
fi
chmod 0700 "$SECRETS_DIR" 2>/dev/null || sudo chmod 0700 "$SECRETS_DIR"
NB="$SECRETS_DIR/.nobackup"
[ -e "$NB" ] || { echo "portioneer secrets — excluded from all off-box backups" \
  | (sudo tee "$NB" >/dev/null 2>&1 || tee "$NB" >/dev/null); }
log "store ready: $SECRETS_DIR (0700, $LLMD_USER, .nobackup)"

svc_announce
case "$(svc_mgr)" in

systemd)
  DROPIN="$REPO/systemd/secrets-inaccessible.conf"
  for unit in pn-batch.slice brainbox-portal.service zyrkel.service reprofleet.service; do
    d="$USERD/${unit}.d"; mkdir -p "$d"
    cp "$DROPIN" "$d/secrets-inaccessible.conf" 2>/dev/null \
      && log "hardened $unit (InaccessiblePaths=$SECRETS_DIR)" || true
  done
  systemctl --user daemon-reload 2>/dev/null || true
  ;;

pninit|none)
  svc_skip "InaccessiblePaths-Haertung: pn-init hat keine Per-Dienst-Sandbox."
  log "  Wirksamer Schutz hier: $(ls -ld "$SECRETS_DIR" 2>/dev/null || echo "$SECRETS_DIR fehlt")"
  log "  UNGEDECKT bleibt: ein anderer Dienst, der ALS $LLMD_USER oder als root laeuft, kaeme heran."
  ;;
esac

log "done. pn-llmd is the ONLY writer (via setcred); never write brain.key by hand."
log "onboard a brain with:  pn-byobrain {max-token|api-key|codex}"
