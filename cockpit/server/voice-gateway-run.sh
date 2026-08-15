#!/bin/sh
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
: "${HOME:=$(getent passwd "$(id -u)" | cut -d: -f6)}"; export HOME

ENVF="$HOME/homeassistant/shim-env"
GW="$HOME/brainarbeit/cockpit/server/voice-gateway.py"

[ -r "$ENVF" ] || { echo "voice-gateway-run: $ENVF fehlt/unlesbar — ohne Schluessel kein Start" >&2; exit 1; }
[ -r "$GW" ]   || { echo "voice-gateway-run: $GW fehlt" >&2; exit 1; }

. "$ENVF"
exec python3 "$GW" >> "$HOME/voice-gateway.log" 2>&1
