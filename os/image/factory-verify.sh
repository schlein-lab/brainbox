#!/bin/bash
set -u
ROOT="${1:-/}"
RC=0
hit(){ echo "LEAK: $1"; RC=1; }

for p in \
  "$ROOT"/home/*/.claude.json "$ROOT"/home/*/.claude.json.backup \
  "$ROOT"/home/*/.claude/.credentials.json \
  "$ROOT"/home/*/.llmpool "$ROOT"/home/*/.llmpool/*/.claude.json \
  "$ROOT"/home/*/.env "$ROOT"/home/*/.env.local \
  "$ROOT"/home/*/.git-credentials "$ROOT"/home/*/.netrc \
  "$ROOT"/home/*/.config/brainbox/pak \
  "$ROOT"/home/*/.config/gh/hosts.yml \
  "$ROOT"/home/*/.config/brainbox-portal/config.json \
  "$ROOT"/home/*/.config/brainbox-portal/llmpool.json \
  "$ROOT"/home/*/.config/brainbox-portal/ca/brainbox-ca.key \
  "$ROOT"/home/*/.local/share/brainbox-portal/secretvault/master.key \
  "$ROOT"/home/*/.local/share/brainbox-portal/api_keys.json \
  "$ROOT"/home/*/.local/share/brainbox-portal/users.db \
  "$ROOT"/home/*/.local/share/brainbox-portal/provenance.key \
  "$ROOT"/home/*/.local/share/brainbox-portal/devices.json \
  "$ROOT"/root/.ssh/id_* "$ROOT"/root/.git-credentials "$ROOT"/root/.netrc \
  "$ROOT"/etc/brainbox/secrets.env "$ROOT"/etc/keepalived/keepalived.conf \
  "$ROOT"/etc/wpa_supplicant/wpa_supplicant*.conf; do
  [ -e "$p" ] && hit "$p"
done

if [ "$ROOT" != "/" ]; then
  for p in "$ROOT"/etc/ssh/ssh_host_*_key; do [ -e "$p" ] && hit "$p"; done
fi

if [ "$RC" = 0 ]; then echo "factory-verify: clean"; else echo "factory-verify: LEAKS FOUND"; fi
exit $RC
