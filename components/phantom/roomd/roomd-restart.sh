#!/bin/sh
pkill -9 -f "phantom-roomd.py" 2>/dev/null
sleep 1
cd "$HOME"
LOG="$HOME/.local/state/phantom-room/roomd.err"
setsid python3 "$HOME/phantom-roomd.py" >"$LOG" 2>&1 </dev/null &
sleep 1
echo "restarted pid=$(pgrep -f 'python3.*roomd' | head -1)"
head -20 "$LOG"
