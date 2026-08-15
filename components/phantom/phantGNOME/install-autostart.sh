#!/usr/bin/env bash
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
AUTOSTART_DIR="$HOME/.config/autostart"
USER_UNIT_DIR="$HOME/.config/systemd/user"

mkdir -p "$AUTOSTART_DIR" "$USER_UNIT_DIR"

install -m644 "$SRC/autostart/phantom-llm-window.desktop" \
    "$AUTOSTART_DIR/phantom-llm-window.desktop"
chmod +x "$SRC/phantom-llm-window.sh"
echo "installed autostart -> $AUTOSTART_DIR/phantom-llm-window.desktop"
echo "--- read-back ---"; sed 's/^/  /' "$AUTOSTART_DIR/phantom-llm-window.desktop"

install -m644 "$SRC/systemd/phantom-greet.service" "$USER_UNIT_DIR/phantom-greet.service"
install -m644 "$SRC/systemd/phantom-greet.timer"   "$USER_UNIT_DIR/phantom-greet.timer"
chmod +x "$SRC/greet.sh"
echo "installed systemd user units -> $USER_UNIT_DIR/phantom-greet.{service,timer}"
echo "--- read-back service ---"; sed 's/^/  /' "$USER_UNIT_DIR/phantom-greet.service"
echo "--- read-back timer ---";   sed 's/^/  /' "$USER_UNIT_DIR/phantom-greet.timer"

echo
echo "DONE (autostart + greeting units installed, sudo-free)."
echo "The LLM window will mount on the NEXT phantom-session login."
echo
echo "The 07:00 greeting is NOT enabled. When you want it, the operator runs:"
echo "  systemctl --user daemon-reload"
echo "  systemctl --user enable --now phantom-greet.timer"
echo "  # test once (sends a real Telegram message): bash $SRC/greet.sh"
