#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${REPO:-$HOME/brainarbeit-build/phantom-bugs}"
WORK="$(mktemp -d /tmp/bug2-proof.XXXXXX)"
export XDG_RUNTIME_DIR="$WORK/run"; mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
LOG="$WORK/log"; mkdir -p "$LOG"
PIDS=()
say() { printf '\n=== %s ===\n' "$*"; }
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null; done; sleep 0.3
  for p in "${PIDS[@]:-}"; do kill -9 "$p" 2>/dev/null; done; [ -n "${KEEP:-}" ] || rm -rf "$WORK"; }
trap cleanup EXIT
say "WORK=$WORK"

DBUS_SOCK="$XDG_RUNTIME_DIR/dbus-session"
dbus-daemon --session --address="unix:path=$DBUS_SOCK" --nofork >"$LOG/dbus.log" 2>&1 &
PIDS+=("$!"); sleep 0.5
export DBUS_SESSION_BUS_ADDRESS="unix:path=$DBUS_SOCK"

COMP="$REPO/target/release/phantom"
export PHANTOM_HEADLESS=1 PHANTOM_NO_INPUT=1 PHANTOM_DEBUG=1
export PHANTOM_SHOT="$WORK/screen.png"
export WAYLAND_DISPLAY=wayland-bug2
"$COMP" --compositor "$WAYLAND_DISPLAY" >"$LOG/phantom.log" 2>&1 &
PIDS+=("$!"); sleep 1.5
export PHANTOM_CTL="$XDG_RUNTIME_DIR/phantom.ctl"

ctl() { printf '%s\n' "$1" | socat - "UNIX-CONNECT:$PHANTOM_CTL" 2>/dev/null; }

export GDK_BACKEND=wayland GTK_USE_PORTAL=0
python3 "$HERE/popup-app.py" >"$LOG/popup.log" 2>&1 &
PIDS+=("$!"); sleep 3.5

say "list + spielwiese on"
ctl "list"
ctl "spielwiese on"
sleep 1.0

say "forge click on MenuButton -> GTK opens an xdg_popup menu"
for i in 1 2 3; do ctl "act 0 click 60 40" ; sleep 0.8; done
POPUPS=$(grep -cE "xdg_wm_base.3|xdg_popup" "$LOG/phantom.log" 2>/dev/null || echo 0)
echo "xdg_popup surfaces registered in compositor: $POPUPS"

say "snapshot the composited screen while the popup is up"
for i in $(seq 1 10); do
  ctl "focus 0" >/dev/null
  sleep 1.0
  [ -f "$PHANTOM_SHOT" ] && break
done
ls -l "$PHANTOM_SHOT" 2>/dev/null | sed 's/^/  /'

python3 - "$PHANTOM_SHOT" <<'PY'
import sys
from PIL import Image
img = Image.open(sys.argv[1]).convert("RGB")
W,H = img.size
px = img.load()
# Sample a coarse grid; count pixels close to the main green (0x20,0xC0,0x40).
def near(c, t, tol=40): return all(abs(a-b)<=tol for a,b in zip(c,t))
GREEN=(0x20,0xC0,0x40)
tot=grn=0
step=20
for y in range(0,H,step):
    for x in range(0,W,step):
        tot+=1
        if near(px[x,y], GREEN): grn+=1
frac = grn/tot if tot else 0
print(f"screen {W}x{H}  green-fraction={frac:.3f}  ({grn}/{tot} sample pts)")
# A popup that BLANKED the main window would drop green to ~0 (desktop cream / popup grey).
# The fix keeps the main green dominant. Require a strong majority still green.
if frac >= 0.6:
    print("RESULT: PASS — main toplevel survives behind the popup (no blank/steal)")
    sys.exit(0)
else:
    print("RESULT: FAIL — main content lost; popup blanked/stole the foreground")
    sys.exit(1)
PY
RC=$?
say "compositor log (foreground/popup decisions)"
grep -iE "popup|compositing client|foreground|focus" "$LOG/phantom.log" | tail -8 | sed 's/^/  /'
echo "popup-app log tail:"; tail -4 "$LOG/popup.log" | sed 's/^/  /'
exit $RC
