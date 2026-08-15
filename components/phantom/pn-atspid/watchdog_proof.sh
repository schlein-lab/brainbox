#!/usr/bin/env bash
set -u
REPO="${REPO:-$HOME/brainarbeit-build/phantom-bugs}"
WORK="$(mktemp -d /tmp/wd-proof.XXXXXX)"
export XDG_RUNTIME_DIR="$WORK/run"; mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
LOG="$WORK/log"; mkdir -p "$LOG"; PIDS=()
say() { printf '\n=== %s ===\n' "$*"; }
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null; done; sleep 0.3
  for p in "${PIDS[@]:-}"; do kill -9 "$p" 2>/dev/null; done; [ -n "${KEEP:-}" ] || rm -rf "$WORK"; }
trap cleanup EXIT
say "WORK=$WORK"

COMP="$REPO/target/release/phantom"
PORT=8097
export PHANTOM_HEADLESS=1 PHANTOM_NO_INPUT=1
export PHANTOM_STREAM="127.0.0.1:$PORT"
export VFILE="$XDG_RUNTIME_DIR/phantom-watchdog.verdict"
export WAYLAND_DISPLAY=wayland-wd
"$COMP" --compositor "$WAYLAND_DISPLAY" >"$LOG/phantom.log" 2>&1 &
PIDS+=("$!"); sleep 2.0
echo "verdict file streamd watches:"; grep -m1 "watchdog verdict file" "$LOG/phantom.log" | sed 's/^/  /'
echo "streamd bind:"; grep -m1 "MJPEG on" "$LOG/phantom.log" | sed 's/^/  /'

state() { curl -s "http://127.0.0.1:$PORT/state"; }
mjpeg_state() {
  timeout 3 curl -s "http://127.0.0.1:$PORT/stream" | tr -d '\r' | grep -m1 -i "X-Phantom-State" || true
}

say "1) BEFORE any verdict — heuristic drives /state (watchdog=UNKNOWN)"
state; echo

say "2) pn-init writes STUCK to the verdict file (atomic rename)"
printf 'STUCK\n' > "$VFILE.tmp" && mv "$VFILE.tmp" "$VFILE"
sleep 1.0
echo "/state now:"; state; echo
echo "MJPEG header now:"; mjpeg_state

say "3) verify the override is authoritative: /state.state == STUCK AND watchdog == STUCK"
OUT="$(state)"; echo "$OUT"
echo "$OUT" | grep -q '"state":"STUCK"' && echo "  state flipped to STUCK: YES" || { echo "  state flipped to STUCK: NO"; FAIL=1; }
echo "$OUT" | grep -q '"watchdog":"STUCK"' && echo "  watchdog verdict == STUCK: YES" || { echo "  watchdog verdict == STUCK: NO"; FAIL=1; }

say "4) pn-init clears it (UNKNOWN) — heuristic resumes, watchdog back to UNKNOWN"
printf 'UNKNOWN\n' > "$VFILE"
sleep 1.0
OUT="$(state)"; echo "$OUT"
echo "$OUT" | grep -q '"watchdog":"UNKNOWN"' && echo "  override cleared: YES" || { echo "  override cleared: NO"; FAIL=1; }

say "compositor watchdog log"
grep -i "watchdog verdict" "$LOG/phantom.log" | sed 's/^/  /'

if [ -n "${FAIL:-}" ]; then echo; echo "RESULT: FAIL"; exit 1; else echo; echo "RESULT: PASS — STUCK verdict overrides the heuristic; UNKNOWN restores it"; fi
