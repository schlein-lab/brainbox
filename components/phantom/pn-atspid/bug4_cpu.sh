#!/usr/bin/env bash
set -u
REPO="${REPO:-$HOME/brainarbeit-build/phantom-bugs}"
PND="$REPO/pn-atspid"
SECS="${SECS:-20}"
WITH_APP="${WITH_APP:-0}"
WORK="$(mktemp -d /tmp/bug4-cpu.XXXXXX)"
export XDG_RUNTIME_DIR="$WORK/run"; mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
LOG="$WORK/log"; mkdir -p "$LOG"
PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null; done; sleep 0.3
  for p in "${PIDS[@]:-}"; do kill -9 "$p" 2>/dev/null; done; rm -rf "$WORK"; }
trap cleanup EXIT

DBUS_SOCK="$XDG_RUNTIME_DIR/dbus-session"
dbus-daemon --session --address="unix:path=$DBUS_SOCK" --nofork >"$LOG/dbus.log" 2>&1 &
PIDS+=("$!"); sleep 0.5
export DBUS_SESSION_BUS_ADDRESS="unix:path=$DBUS_SOCK"

COMP="$REPO/target/release/phantom"
export PHANTOM_HEADLESS=1 PHANTOM_NO_INPUT=1 PHANTOM_PROFILE=1
export WAYLAND_DISPLAY=wayland-cpu
"$COMP" --compositor "$WAYLAND_DISPLAY" >"$LOG/phantom.log" 2>&1 &
CPID=$!; PIDS+=("$CPID"); sleep 1.5

if [ "$WITH_APP" = "1" ]; then
  /usr/libexec/at-spi-bus-launcher --launch-immediately >"$LOG/atspi.log" 2>&1 &
  PIDS+=("$!"); sleep 1.0
  GTK_A11Y=atspi GDK_BACKEND=wayland WAYLAND_DISPLAY=wayland-cpu \
    python3 "$PND/coop-gtk-app.py" >"$LOG/coop.log" 2>&1 &
  PIDS+=("$!"); sleep 3.0
  echo "hosted coop-gtk app: yes"
else
  echo "hosted app: none (bare idle)"
fi

CLK=$(getconf CLK_TCK)
read_ticks() { awk '{print $14+$15}' "/proc/$1/stat"; }
T0=$(read_ticks "$CPID"); E0=$(date +%s.%N)
sleep "$SECS"
T1=$(read_ticks "$CPID"); E1=$(date +%s.%N)
DT=$(awk -v a="$T0" -v b="$T1" 'BEGIN{print b-a}')
EL=$(awk -v a="$E0" -v b="$E1" 'BEGIN{print b-a}')
PCT=$(awk -v t="$DT" -v clk="$CLK" -v el="$EL" 'BEGIN{printf "%.2f", (t/clk)/el*100}')
echo "compositor pid=$CPID  ticks=$DT  CLK_TCK=$CLK  elapsed=${EL}s  => idle CPU = ${PCT}% of one core (over ${SECS}s)"
echo "profiler lines:"; grep PROFILE "$LOG/phantom.log" | tail -4 | sed 's/^/  /'
