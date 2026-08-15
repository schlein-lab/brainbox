#!/bin/sh
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
: "${HOME:=$(getent passwd "$(id -u)" | cut -d: -f6)}"; export HOME
DIR="$(cd "$(dirname "$0")" && pwd)"
echo "job-announcer-run: Job-fertig-Melder (pollt /api/queue -> Nabu-Durchsage)" >&2
while true; do
  nice -n 5 python3 "$DIR/job_announcer.py"
  echo "job_announcer exited ($?); Neustart in 5s" >&2
  sleep 5
done
