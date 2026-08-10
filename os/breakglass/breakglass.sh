#!/bin/bash
cd "$HOME" || exit 1
CFG="${PN_BG_CONFIG:-$HOME/.config/pn-breakglass}"
mkdir -p "$CFG" 2>/dev/null
cat <<'BANNER'
==============================================================
  BRAINBOX BREAK-GLASS  —  Reparatur-Konsole (portal-unabhaengig)
  Diese Sitzung laeuft AM Host, nicht in einer Zelle. Nur fuer
  Reparatur/Diagnose. Aktionen werden auditiert.
  Chat-Ansicht (Links kopieren, tippen): /chat auf demselben Port
  Reboot dieser Appliance IMMER via: sudo pn-shutdown --reboot --yes
==============================================================
BANNER

ARGS=()
if claude --help 2>/dev/null | grep -q -- '--session-id'; then
  SID="$(tr -d '[:space:]' < "$CFG/session-id" 2>/dev/null)"
  PROJ="$(printf '%s' "$PWD" | sed 's/[^A-Za-z0-9]/-/g')"
  if [ -z "$SID" ]; then
    SID="$(cat /proc/sys/kernel/random/uuid 2>/dev/null)"
    [ -n "$SID" ] && printf '%s\n' "$SID" > "$CFG/session-id"
  fi
  if [ -n "$SID" ]; then
    if [ -f "$HOME/.claude/projects/$PROJ/$SID.jsonl" ]; then
      ARGS=(--resume "$SID")
    else
      ARGS=(--session-id "$SID")
    fi
  fi
fi

claude "${ARGS[@]}" || echo "(claude nicht verfuegbar — reine Reparatur-Shell)"
echo; echo "claude beendet — Reparatur-Shell. tmux bleibt bestehen."
exec bash -l
