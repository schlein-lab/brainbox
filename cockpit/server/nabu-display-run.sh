#!/bin/sh
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
: "${HOME:=$(getent passwd "$(id -u)" | cut -d: -f6)}"; export HOME
DIR="$(cd "$(dirname "$0")" && pwd)"
echo "nabu-display-run: Durchsage-Driver auf 127.0.0.1:8098" >&2
while true; do
  nice -n 5 python3 "$DIR/nabu_display.py"
  echo "nabu_display exited ($?); Neustart in 3s" >&2
  sleep 3
done
