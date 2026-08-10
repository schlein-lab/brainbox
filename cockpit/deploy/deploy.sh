#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COCKPIT="$(cd "$HERE/.." && pwd)"

if [ "$(id -u)" = "0" ]; then
  echo "!! Do NOT run deploy.sh as root. pn-portal must run as the human's own uid (SO_PEERCRED)." >&2
  exit 1
fi

DEST="$HOME/brainarbeit/cockpit"
if [ "$COCKPIT" != "$DEST" ]; then
  echo ">> staging cockpit/ to $DEST"
  mkdir -p "$DEST"
  cp -a "$COCKPIT/server" "$COCKPIT/web" "$COCKPIT/adapters" "$COCKPIT/native" "$DEST/"
fi

USERV="$HOME/.config/systemd/user"
echo ">> install the pn-portal systemd --user unit"
mkdir -p "$USERV"
cp "$HERE/pn-portal.service" "$USERV/pn-portal.service"

echo ">> pre-flight: is a live pnd socket present?"
SOCK="${PN_SOCK:-${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/pnd.sock}"
if [ ! -S "$SOCK" ]; then
  echo "   WARNING: no pnd socket at $SOCK — the portal will start but serve nothing until pnd is up."
  echo "            Bring up the engine (pnd) first; the portal restarts and reconnects automatically."
fi

echo ">> enable + start pn-portal (as your user)"
systemctl --user daemon-reload
systemctl --user enable --now pn-portal.service
sleep 1
systemctl --user --no-pager --lines=0 status pn-portal.service || true

PORT="${PN_PORTAL_PORT:-8800}"; HOST="${PN_PORTAL_HOST:-127.0.0.1}"
echo
echo "live: the cockpit serves the one SPA at http://$HOST:$PORT/"
echo "      native window:  native/pn-cockpit --url http://127.0.0.1:$PORT/?shell=native"
echo "      logs:           journalctl --user -u pn-portal -f"
echo
echo "LAN access: front pn-portal with a TLS reverse proxy (wss:// works automatically), or set"
echo "            PN_PORTAL_HOST=0.0.0.0 + PN_PORTAL_CERT/PN_PORTAL_KEY in $DEST/.env and re-run."
