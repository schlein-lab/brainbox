#!/bin/bash
set -u

APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
HOME_DIR="${TARGET_HOME:-$HOME}"
PP_CFG="$HOME_DIR/.config/brainbox-portal"
PP_DATA="$HOME_DIR/.local/share/brainbox-portal"
KEEP_DATA_RE='^(piper|whisper.*|.*models?|.*\.onnx|voices?)$'
KEEP_CFG_RE='^(static)$'

n=0
act(){
  local p="$1"; [ -e "$p" ] || return 0
  n=$((n+1))
  if [ "$APPLY" = "1" ]; then
    if [ -f "$p" ]; then shred -u "$p" 2>/dev/null || rm -f "$p"; else rm -rf "$p"; fi
    echo "  removed  $p"
  else
    echo "  WOULD remove  $p"
  fi
}

SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
sudo_wipe(){
  local pat p
  for pat in "$@"; do
    while IFS= read -r p; do
      [ -n "$p" ] || continue
      n=$((n+1))
      if [ "$APPLY" = "1" ]; then
        if $SUDO test -f "$p"; then $SUDO shred -u "$p" 2>/dev/null || $SUDO rm -f "$p"; else $SUDO rm -rf "$p"; fi
        echo "  removed  $p"
      else
        echo "  WOULD remove  $p"
      fi
    done < <($SUDO sh -c "for x in $pat; do [ -e \"\$x\" ] && printf '%s\\n' \"\$x\"; done" 2>/dev/null)
  done
}

echo "== factory-clean ($([ "$APPLY" = 1 ] && echo APPLY || echo DRY-RUN)) target=$HOME_DIR =="

echo "-- build-box secrets & credentials --"
for f in "$HOME_DIR/.env" "$HOME_DIR/.git-credentials" "$HOME_DIR/.config/brainbox/pak" \
         "$HOME_DIR/Dokumente/token.txt" "/etc/brainbox/secrets.env" \
         "$HOME_DIR/.config/gh/hosts.yml" "$HOME_DIR/.netrc"; do act "$f"; done
for k in "$HOME_DIR"/.ssh/id_*; do act "$k"; done
act "$HOME_DIR/.ssh/known_hosts"

echo "-- LLM / Claude subscription auth (BYO: customer logs in fresh) + misc dev creds --"
for f in "$HOME_DIR/.claude.json" "$HOME_DIR/.claude.json.backup" "$HOME_DIR/.env.local" \
         "$HOME_DIR/zyrkel/.env" "$HOME_DIR/.npmrc" "$HOME_DIR/.docker/config.json"; do act "$f"; done
for d in "$HOME_DIR/.claude" "$HOME_DIR/.copilot" "$HOME_DIR/.aws" "$HOME_DIR/.config/gh" \
         "$HOME_DIR/.gnupg" "$HOME_DIR/.llmpool"; do act "$d"; done
if [ -f "$PP_CFG/llmpool.json" ]; then
  while IFS= read -r h; do
    [ -n "$h" ] && [ "$h" != "$HOME_DIR" ] && { act "$h/.claude.json"; act "$h/.claude"; }
  done < <(python3 -c 'import json,sys
try: print("\n".join(a.get("home","") for a in (json.load(open(sys.argv[1])).get("accounts") or [])))
except Exception: pass' "$PP_CFG/llmpool.json" 2>/dev/null)
fi
sudo_wipe "/etc/keepalived/keepalived.conf" "/etc/keepalived/.keepalived.conf.new"

echo "-- shell history (user + root: may hold typed passwords/tokens) --"
for h in "$HOME_DIR/.bash_history" "$HOME_DIR/.zsh_history" "$HOME_DIR/.python_history" \
         "$HOME_DIR/.local/share/fish/fish_history" "$HOME_DIR/.lesshst"; do act "$h"; done
sudo_wipe "/root/.bash_history" "/root/.zsh_history" "/root/.python_history" "/root/.lesshst"

echo "-- system WiFi credentials (dev's home PSK must NOT ship in the image) --"
sudo_wipe "/etc/wpa_supplicant/wpa_supplicant.conf" "/etc/wpa_supplicant/wpa_supplicant-*.conf" \
          "/boot/wpa_supplicant.conf" "/boot/firmware/wpa_supplicant.conf" \
          "/etc/NetworkManager/system-connections/*"

echo "-- root-owned secrets (dev may have used sudo/gh/ssh/git as root) --"
sudo_wipe "/root/.ssh/id_*" "/root/.ssh/authorized_keys" "/root/.ssh/known_hosts" \
          "/root/.netrc" "/root/.git-credentials" "/root/.config/gh/hosts.yml" "/root/.env"

echo "-- portal config + per-box crypto (CA/leaf regenerate fresh) --"
if [ -d "$PP_CFG" ]; then
  for e in "$PP_CFG"/* "$PP_CFG"/.[!.]*; do
    [ -e "$e" ] || continue
    b="$(basename "$e")"
    echo "$b" | grep -Eq "$KEEP_CFG_RE" && { echo "  keep     $e"; continue; }
    act "$e"
  done
fi

echo "-- portal runtime + personal state (registries, sessions, transcripts, DBs, logs) --"
if [ -d "$PP_DATA" ]; then
  for e in "$PP_DATA"/* "$PP_DATA"/.[!.]*; do
    [ -e "$e" ] || continue
    b="$(basename "$e")"
    echo "$b" | grep -Eiq "$KEEP_DATA_RE" && { echo "  keep     $e"; continue; }
    act "$e"
  done
fi

echo "-- firefox per-user profiles (saved logins/cookies) --"
for d in "$PP_DATA"/users/*/firefox "$HOME_DIR"/.mozilla; do act "$d"; done

echo "-- first-boot marker (sonst laeuft die Einrichtung 'einmal pro Geraet' NIE wieder) --"
sudo_wipe "/var/lib/brainbox/firstboot.done"

echo "-- git working-tree remote credentials (leave the repo, drop cached creds) --"
if [ -d "$HOME_DIR/brainarbeit/.git" ]; then
  if [ "$APPLY" = "1" ]; then
    git -C "$HOME_DIR/brainarbeit" remote set-url origin "https://github.com/schlein-lab/brainarbeit.git" 2>/dev/null || true
    rm -f "$HOME_DIR/brainarbeit/.git/config.lock" 2>/dev/null || true
  fi
  echo "  ($([ "$APPLY" = 1 ] && echo did || echo would) neutralize origin URL to https, no token)"
fi

echo "== $([ "$APPLY" = 1 ] && echo removed || echo would-remove) $n item(s). $([ "$APPLY" = 1 ] || echo 'Re-run with --apply to delete.') =="
