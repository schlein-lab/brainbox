#!/bin/sh
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
: "${HOME:=$(getent passwd "$(id -u)" | cut -d: -f6)}"; export HOME
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "pn-notify-run: Meldeweg (Bus -> Postfach je Nutzer)" >&2
while true; do
  nice -n 5 python3 "$DIR/pn-notify"
  echo "pn-notify beendet ($?); Neustart in 5s" >&2
  sleep 5
done
