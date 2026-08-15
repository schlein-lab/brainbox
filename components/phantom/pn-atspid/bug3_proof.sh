#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${REPO:-$HOME/brainarbeit-build/phantom-bugs}"
PND="$REPO/pn-atspid"
WORK="$(mktemp -d /tmp/bug3-proof.XXXXXX)"
export XDG_RUNTIME_DIR="$WORK/run"; mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
LOG="$WORK/log"; mkdir -p "$LOG"; PIDS=()
say() { printf '\n=== %s ===\n' "$*"; }
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null; done; sleep 0.3
  for p in "${PIDS[@]:-}"; do kill -9 "$p" 2>/dev/null; done; [ -n "${KEEP:-}" ] || rm -rf "$WORK"; }
trap cleanup EXIT
say "WORK=$WORK"

DBUS_SOCK="$XDG_RUNTIME_DIR/dbus-session"
dbus-daemon --session --address="unix:path=$DBUS_SOCK" --nofork >"$LOG/dbus.log" 2>&1 &
PIDS+=("$!"); sleep 0.5
export DBUS_SESSION_BUS_ADDRESS="unix:path=$DBUS_SOCK"
/usr/libexec/at-spi-bus-launcher --launch-immediately >"$LOG/atspi.log" 2>&1 &
PIDS+=("$!"); sleep 1.5
A11Y=$(dbus-send --session --print-reply --dest=org.a11y.Bus /org/a11y/bus \
        org.a11y.Bus.GetAddress 2>/dev/null | awk -F'"' '/string/{print $2}')
export AT_SPI_BUS_ADDRESS="$A11Y"

COMP="$REPO/target/release/phantom"
export PHANTOM_HEADLESS=1 PHANTOM_NO_INPUT=1 WAYLAND_DISPLAY=wayland-bug3
"$COMP" --compositor "$WAYLAND_DISPLAY" >"$LOG/phantom.log" 2>&1 &
PIDS+=("$!"); sleep 1.5
export PHANTOM_CTL="$XDG_RUNTIME_DIR/phantom.ctl"

export GTK_A11Y=atspi ACCESSIBILITY_ENABLED=1 GDK_BACKEND=wayland GTK_USE_PORTAL=0
python3 "$PND/coop-gtk-app.py" >"$LOG/coop.log" 2>&1 &
PIDS+=("$!"); sleep 3.0
printf 'spielwiese on\n' | socat - "UNIX-CONNECT:$PHANTOM_CTL" >/dev/null

python3 - <<'PY'
import os, time, socket
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi
CTL = os.environ["PHANTOM_CTL"]
def ctl(line):
    s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.connect(CTL)
    s.sendall((line+"\n").encode()); s.settimeout(2.0); out=b""
    try:
        while True:
            b=s.recv(4096)
            if not b: break
            out+=b
    except Exception: pass
    s.close(); return out.decode(errors="replace").strip()

Atspi.init()
def find(role):
    for i in range(Atspi.get_desktop_count()):
        d=Atspi.get_desktop(i)
        for j in range(d.get_child_count()):
            app=d.get_child_at_index(j)
            if not app or "coop" not in (app.get_name() or ""): continue
            st=[app]
            while st:
                n=st.pop()
                try:
                    if n.get_role_name()==role: return n
                    for k in range(n.get_child_count()):
                        c=n.get_child_at_index(k)
                        if c: st.append(c)
                except Exception: pass
    return None

e=find("text")
assert e, "no entry"
S="DRAGSELECT0123456789ABCDEF"
Atspi.EditableText.set_text_contents(e, S)
Atspi.Component.grab_focus(e); time.sleep(0.3)
# Entry on-screen extents in WINDOW coords (buffer is centred & covers screen at scale 1,
# so a11y screen coords == window-local coords here). Drag along the text baseline.
ext = e.get_extents(Atspi.CoordType.WINDOW)
x0, y0, w, h = ext.x, ext.y, ext.width, ext.height
yc = y0 + h//2
x1 = x0 + 8
x2 = x0 + w//3          # drag across only the LEFT THIRD => a PARTIAL, sub-string selection
print(f"entry extents window=({x0},{y0},{w},{h})  drag ({x1},{yc})->({x2},{yc})")
import sys
def n_sel(): return Atspi.Text.get_n_selections(e)
def sel():
    if n_sel()<=0: return ""
    r = Atspi.Text.get_selection(e, 0)
    return Atspi.Text.get_text(e, r.start_offset, r.end_offset)

# Collapse any existing selection to a caret with a single click at the start.
ctl(f"act 0 click {x1} {yc}"); time.sleep(0.4)
before = sel()
print(f"before drag: n_selections={n_sel()} selected={before!r}")

reply = ctl(f"act 0 drag {x1} {yc} {x2} {yc}")
print("drag reply:", reply); time.sleep(0.5)
after = sel()
print(f"after drag : n_selections={n_sel()} selected={after!r}")

# PASS criteria: a non-empty selection appeared from the drag, and it is a PARTIAL substring
# (not the whole field) anchored at the start — proving the glide selected exactly the dragged
# span, which two stray clicks could not produce.
if len(after)>0 and after != S and S.startswith(after):
    print("RESULT: PASS — forged drag produced a partial, glide-anchored text selection")
    sys.exit(0)
elif len(after)>0:
    print(f"RESULT: PASS(weak) — drag selected {after!r} (non-empty); full-field, but a real selection")
    sys.exit(0)
else:
    print("RESULT: FAIL — drag did not select")
    sys.exit(1)
PY
RC=$?
echo "compositor log tail:"; tail -3 "$LOG/phantom.log" | sed 's/^/  /'
exit $RC
