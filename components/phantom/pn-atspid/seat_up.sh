#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
WORK="${1:-/tmp/pn-seatproof}"
rm -rf "$WORK"
export XDG_RUNTIME_DIR="$WORK/run"; mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
LOG="$WORK/log"; mkdir -p "$LOG"
PIDS=()
cleanup() {
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null; done
  sleep 0.3
  for p in "${PIDS[@]:-}"; do kill -9 "$p" 2>/dev/null; done
}
trap cleanup EXIT

DBUS_SOCK="$XDG_RUNTIME_DIR/dbus-session"
dbus-daemon --session --address="unix:path=$DBUS_SOCK" --nofork >"$LOG/dbus.log" 2>&1 &
PIDS+=("$!")
sleep 0.6
export DBUS_SESSION_BUS_ADDRESS="unix:path=$DBUS_SOCK"

/usr/libexec/at-spi-bus-launcher --launch-immediately >"$LOG/atspi.log" 2>&1 &
PIDS+=("$!")
sleep 1.5
A11Y=$(dbus-send --session --print-reply --dest=org.a11y.Bus /org/a11y/bus \
        org.a11y.Bus.GetAddress 2>/dev/null | awk -F'"' '/string/{print $2}')
export AT_SPI_BUS_ADDRESS="$A11Y"

COMP="$REPO/target/release/phantom"
[ -x "$COMP" ] || COMP="$REPO/target/debug/phantom"
export PHANTOM_HEADLESS=1
export PHANTOM_SHOT="$WORK/screen.png"
export WAYLAND_DISPLAY=wayland-pn
"$COMP" --headless "$WAYLAND_DISPLAY" >"$LOG/phantom.log" 2>&1 &
PIDS+=("$!")
sleep 1.5
export PHANTOM_CTL="$XDG_RUNTIME_DIR/phantom.ctl"

export GTK_A11Y=atspi ACCESSIBILITY_ENABLED=1 GDK_BACKEND=wayland GTK_USE_PORTAL=0
python3 "$HERE/coop-gtk-app.py" >"$LOG/coop.log" 2>&1 &
PIDS+=("$!")
sleep 3.0

export PN_ATSPID_SOCK="$XDG_RUNTIME_DIR/pn-atspid.sock"
export PN_RECORD="$WORK/record.jsonl"
python3 "$HERE/pn-atspid.py" >"$LOG/atspid.out" 2>"$LOG/atspid.err" &
ATSPID_PID="$!"
PIDS+=("$ATSPID_PID")
sleep 1.5

echo "SEAT_READY sock=$PN_ATSPID_SOCK record=$PN_RECORD work=$WORK"
wait "$ATSPID_PID"
