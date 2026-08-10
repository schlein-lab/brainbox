#!/bin/sh
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
: "${HOME:=$(getent passwd "$(id -u)" | cut -d: -f6)}"; export HOME
DIR="$(cd "$(dirname "$0")" && pwd)"
: "${CASTD_PORT:=8096}"; export CASTD_PORT

child=""
term() { [ -n "$child" ] && kill "$child" 2>/dev/null; exit 0; }
trap term TERM INT

echo "tvdisplay-run: pn_castd multi-target Cast daemon on :$CASTD_PORT" >&2
while true; do
  nice -n 5 python3 "$DIR/pn_castd.py" &
  child=$!
  wait "$child"
  echo "pn_castd exited ($?); restarting in 3s" >&2
  child=""
  sleep 3
done
