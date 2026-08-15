#!/usr/bin/env bash
set -u

APP_ID="${PHANTOM_LLM_APPID:-phantom-llm}"
CLAUDE_BIN="${PHANTOM_CLAUDE_BIN:-$HOME/.local/bin/claude}"
CTX_FILE="${PHANTOM_LLM_CONTEXT:-$HOME/phantGNOME/docs/PHANTOM-LLM-CONTEXT.md}"
LOG="${PHANTOM_LLM_LOG:-$HOME/.local/state/phantom-llm-window.log}"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

log() { printf '%s %s\n' "$(date -Is 2>/dev/null)" "$*" >>"$LOG" 2>/dev/null; }

if [ "${GNOME_SHELL_SESSION_MODE:-}" != "phantom" ]; then
    if ! (command -v gdbus >/dev/null 2>&1 && \
          timeout 5 gdbus wait --session --timeout 5 org.gnome.Phantom 2>/dev/null); then
        log "not in phantom session (mode='${GNOME_SHELL_SESSION_MODE:-?}', no org.gnome.Phantom) — skipping"
        exit 0
    fi
fi

LOCK="${PHANTOM_LLM_LOCK:-${XDG_RUNTIME_DIR:-/tmp}/phantom-llm-window.pid}"

if [ -f "$LOCK" ]; then
    oldpid="$(cat "$LOCK" 2>/dev/null || true)"
    if [ -n "$oldpid" ] && kill -0 "$oldpid" 2>/dev/null; then
        log "LLM window launcher already alive (pid $oldpid, lock $LOCK) — not relaunching"
        exit 0
    fi
    rm -f "$LOCK"
fi

if command -v pgrep >/dev/null 2>&1; then
    if pgrep -af -- "--app-id=$APP_ID|--title=$APP_ID|-T $APP_ID|-T[[:space:]]*$APP_ID" >/dev/null 2>&1; then
        log "a terminal with app-id/title '$APP_ID' is already running — not relaunching"
        exit 0
    fi
fi

if command -v gdbus >/dev/null 2>&1; then
    wins=$(timeout 5 gdbus call --session --dest org.gnome.Phantom \
              --object-path /org/gnome/Phantom \
              --method org.gnome.Phantom.ListWindows 2>/dev/null || true)
    if [ -n "$wins" ] && printf '%s' "$wins" | grep -qiF "$APP_ID"; then
        log "a window already matches '$APP_ID' per ListWindows — not relaunching"
        exit 0
    fi
fi

echo "$$" > "$LOCK" 2>/dev/null || true

if [ -x "$CLAUDE_BIN" ]; then
    RUN_DESC="claude ($CLAUDE_BIN)"
    INNER="export PHANTOM_LLM_WINDOW=1 PATH=\"$HOME/.local/bin:\$PATH\";
           echo '[phantGNOME] desktop: phantom help  ·  knowledge: kartei list  ·  secrets: phantom secret';
           '$CLAUDE_BIN' --append-system-prompt-file '$CTX_FILE' || true;
           echo; echo '[claude exited — focal shell kept alive; run claude to resume]';
           exec bash -i"
else
    RUN_DESC="interactive shell (claude not found at $CLAUDE_BIN)"
    INNER="export PHANTOM_LLM_WINDOW=1 PATH=\"$HOME/.local/bin:\$PATH\";
           if [ -r '$CTX_FILE' ]; then cat '$CTX_FILE'; echo; fi;
           echo '[phantGNOME] claude CLI not found - drive the desktop via gdbus org.gnome.Phantom';
           exec bash -i"
fi

COCKPIT_INNER="export PATH=\"\$HOME/.local/bin:\$PATH\";
  if command -v tmux >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1 && python3 \$HOME/.local/bin/phantom-room cockpit up >/dev/null 2>&1; then
    exec tmux attach -t cockpit;
  fi;
  $INNER"

FOOT_CONFIG_ARG=""; [ -r "${PHANTOM_FOOT_CONFIG:-$HOME/.config/foot/phantom.ini}" ] && FOOT_CONFIG_ARG="--config=${PHANTOM_FOOT_CONFIG:-$HOME/.config/foot/phantom.ini}"
launch() {
    if command -v foot >/dev/null 2>&1; then
        log "launching LLM window via foot --app-id=$APP_ID running $RUN_DESC"
        exec foot ${FOOT_CONFIG_ARG} --app-id="$APP_ID" --title="$APP_ID" \
             env PHANTOM_LLM_WINDOW=1 bash -lc "$COCKPIT_INNER"
    elif command -v gnome-terminal >/dev/null 2>&1; then
        log "launching LLM window via gnome-terminal --title=$APP_ID running $RUN_DESC"
        exec gnome-terminal --wait --title="$APP_ID" -- \
             env PHANTOM_LLM_WINDOW=1 bash -lc "$INNER"
    elif command -v xterm >/dev/null 2>&1; then
        log "launching LLM window via xterm -T $APP_ID running $RUN_DESC"
        exec xterm -T "$APP_ID" -e env PHANTOM_LLM_WINDOW=1 bash -lc "$INNER"
    else
        log "NO terminal (foot/gnome-terminal/xterm) found — cannot mount LLM window"
        exit 1
    fi
}

launch
