#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${REPO:-$HOME/brainarbeit-build/phantom-bugs}"
PND="$REPO/pn-atspid"
WORK="$(mktemp -d /tmp/bug1-proof.XXXXXX)"
export XDG_RUNTIME_DIR="$WORK/run"; mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
LOG="$WORK/log"; mkdir -p "$LOG"
PIDS=()
say() { printf '\n=== %s ===\n' "$*"; }
cleanup() {
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null; done
  sleep 0.3
  for p in "${PIDS[@]:-}"; do kill -9 "$p" 2>/dev/null; done
  [ -n "${KEEP:-}" ] || rm -rf "$WORK"
}
trap cleanup EXIT
say "WORK=$WORK"

DBUS_SOCK="$XDG_RUNTIME_DIR/dbus-session"
dbus-daemon --session --address="unix:path=$DBUS_SOCK" --nofork --print-pid >"$LOG/dbus.pid" 2>"$LOG/dbus.log" &
PIDS+=("$!"); sleep 0.6
export DBUS_SESSION_BUS_ADDRESS="unix:path=$DBUS_SOCK"

/usr/libexec/at-spi-bus-launcher --launch-immediately >"$LOG/atspi.log" 2>&1 &
PIDS+=("$!"); sleep 1.5
A11Y=$(dbus-send --session --print-reply --dest=org.a11y.Bus /org/a11y/bus \
        org.a11y.Bus.GetAddress 2>/dev/null | awk -F'"' '/string/{print $2}')
export AT_SPI_BUS_ADDRESS="$A11Y"
echo "a11y bus: ${A11Y:-<none>}"

COMP="$REPO/target/release/phantom"; [ -x "$COMP" ] || COMP="$REPO/target/debug/phantom"
export PHANTOM_HEADLESS=1
export WAYLAND_DISPLAY=wayland-pn
[ -n "${PHANTOM_KBD_LAYOUT:-}" ] && export PHANTOM_KBD_LAYOUT
"$COMP" --compositor "$WAYLAND_DISPLAY" >"$LOG/phantom.log" 2>&1 &
PIDS+=("$!"); sleep 1.5
export PHANTOM_CTL="$XDG_RUNTIME_DIR/phantom.ctl"
echo "phantom.ctl exists: $([ -S "$PHANTOM_CTL" ] && echo yes || echo no)"

export GTK_A11Y=atspi ACCESSIBILITY_ENABLED=1 GDK_BACKEND=wayland GTK_USE_PORTAL=0
python3 "$PND/coop-gtk-app.py" >"$LOG/coop.log" 2>&1 &
PIDS+=("$!"); sleep 3.0

ctl() { printf '%s\n' "$1" | socat - "UNIX-CONNECT:$PHANTOM_CTL" 2>/dev/null || \
        printf '%s\n' "$1" | nc -U "$PHANTOM_CTL" 2>/dev/null; }

SENT="${1:-/home/x/a_b.txt --flag=1 (q?) {[]} @e #c /resume-2}"

python3 - "$SENT" <<'PY'
import sys, os, time, socket
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

SENT = sys.argv[1]
CTL = os.environ["PHANTOM_CTL"]

def find_entry():
    Atspi.init()
    for i in range(Atspi.get_desktop_count()):
        d = Atspi.get_desktop(i)
        for j in range(d.get_child_count()):
            app = d.get_child_at_index(j)
            if not app or "coop" not in (app.get_name() or ""):
                continue
            stack = [app]
            while stack:
                n = stack.pop()
                try:
                    if n.get_role_name() == "text":
                        return n
                    for k in range(n.get_child_count()):
                        c = n.get_child_at_index(k)
                        if c: stack.append(c)
                except Exception:
                    pass
    return None

def entry_text(e):
    try:
        t = e.get_text_iface()
        return t.get_text(0, t.get_character_count())
    except Exception:
        try:
            return Atspi.Text.get_text(e, 0, Atspi.Text.get_character_count(e))
        except Exception:
            return "<unreadable>"

def ctl(line):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(CTL)
    s.sendall((line + "\n").encode())
    s.settimeout(2.0)
    out = b""
    try:
        while True:
            b = s.recv(4096)
            if not b: break
            out += b
    except Exception:
        pass
    s.close()
    return out.decode(errors="replace").strip()

e = find_entry()
if not e:
    print("FAIL: no Message entry found in coop a11y tree"); sys.exit(2)

# Clear, then grab keyboard focus on the GTK widget so forged wl_keyboard.key lands here.
try:
    Atspi.EditableText.set_text_contents(e, "")
except Exception as ex:
    print("warn: clear failed:", ex)
try:
    Atspi.Component.grab_focus(e)
except Exception as ex:
    print("warn: grab_focus failed:", ex)
time.sleep(0.4)
print("before:", repr(entry_text(e)))

# Type via the compositor forge — the BUG-1 keymap path.
reply = ctl(f"act @coop-gtk type {SENT}")
print("ctl reply:", reply)
time.sleep(0.6)

got = entry_text(e)
print("SENT   :", repr(SENT))
print("GOT    :", repr(got))
if got == SENT:
    print("RESULT : PASS — punctuation/path/slash-command typed verbatim")
    sys.exit(0)
else:
    # show first divergence
    for i,(a,b) in enumerate(zip(SENT, got)):
        if a!=b:
            print(f"DIVERGE at {i}: sent {a!r} got {b!r}"); break
    print("RESULT : FAIL — typed text does not match")
    sys.exit(1)
PY
RC=$?
echo "compositor log tail:"; tail -4 "$LOG/phantom.log" | sed 's/^/  /'
exit $RC
