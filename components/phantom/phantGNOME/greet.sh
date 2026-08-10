#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${PHANTOM_ZYRKEL_ENV:-$HOME/zyrkel/.env}"
CFG_FILE="${PHANTOM_ZYRKEL_CONFIG:-$HOME/zyrkel/config.json}"

die() { echo "greet.sh: $*" >&2; exit 1; }

[ -r "$ENV_FILE" ] || die "no readable token file at $ENV_FILE"
set +u
. "$ENV_FILE"
set -u
[ -n "${TELEGRAM_BOT_TOKEN:-}" ] || die "TELEGRAM_BOT_TOKEN missing/empty in $ENV_FILE"

[ -r "$CFG_FILE" ] || die "no readable config at $CFG_FILE"
CHAT_ID="${PHANTOM_GREET_CHAT_ID:-}"
if [ -z "$CHAT_ID" ]; then
    CHAT_ID="$(python3 - "$CFG_FILE" <<'PY'
import json,sys
try:
    d=json.load(open(sys.argv[1]))
except Exception as e:
    sys.stderr.write("config parse error: %s\n"%e); sys.exit(3)
# prefer allowed_chat_id; fall back to group_chat_id.
for k in ("allowed_chat_id","group_chat_id"):
    v=d.get(k)
    if v is not None and str(v).strip() not in ("","0"):
        print(str(v)); sys.exit(0)
sys.exit(4)
PY
)" || die "no usable chat id (allowed_chat_id/group_chat_id) in $CFG_FILE"
fi
[ -n "$CHAT_ID" ] || die "resolved chat id is empty"

THREAD_ID="${PHANTOM_GREET_THREAD_ID:-}"

STAGE="?"
if command -v gdbus >/dev/null 2>&1; then
    raw="$(timeout 5 gdbus call --session --dest org.gnome.Phantom \
            --object-path /org/gnome/Phantom \
            --method org.freedesktop.DBus.Properties.Get \
            org.gnome.Phantom Stage 2>/dev/null || true)"
    case "$raw" in
        *"'A'"*) STAGE="A (Voll-Auto)";;
        *"'B'"*) STAGE="B (onsite + Halb-Auto)";;
        *"'C'"*) STAGE="C (onsite-manuell)";;
        *) STAGE="unbekannt (phantom-Dienst nicht erreichbar)";;
    esac
fi
HOST="$(hostname 2>/dev/null || echo localhost)"
NOW="$(date '+%Y-%m-%d %H:%M' 2>/dev/null || echo '')"

read -r -d '' TEXT <<EOF || true
Guten Morgen — phantGNOME laeuft auf ${HOST}.
Stand ${NOW}: aktueller Modus ${STAGE}.
Voll-Auto = nur das LLM-Fenster ist sichtbar, alles andere headless.
Fuer Halb-Auto den HUD-Regler nutzen oder: gdbus SetStage B (bzw. C fuer manuell).
EOF

API="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"
args=( -sS --max-time 20
       --data-urlencode "chat_id=${CHAT_ID}"
       --data-urlencode "text=${TEXT}"
       --data-urlencode "disable_web_page_preview=true" )
[ -n "$THREAD_ID" ] && args+=( --data-urlencode "message_thread_id=${THREAD_ID}" )

resp="$(curl "${args[@]}" "$API" 2>/dev/null || true)"
if printf '%s' "$resp" | grep -q '"ok":true'; then
    echo "greet.sh: morning greeting sent to chat ${CHAT_ID}${THREAD_ID:+ topic ${THREAD_ID}}"
    exit 0
else
    safe="$(printf '%s' "$resp" | sed -E 's/[0-9]{8,10}:[A-Za-z0-9_-]{35}/TOK/g')"
    die "Telegram sendMessage did not return ok:true -> ${safe:-<no response>}"
fi
