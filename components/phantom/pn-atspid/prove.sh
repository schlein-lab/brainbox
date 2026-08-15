#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
WORK="$(mktemp -d /tmp/pn-atspid-prove.XXXXXX)"
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

say "WORK=$WORK  XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"

say "private session dbus"
DBUS_SOCK="$XDG_RUNTIME_DIR/dbus-session"
dbus-daemon --session --address="unix:path=$DBUS_SOCK" --nofork --print-pid >"$LOG/dbus.pid" 2>"$LOG/dbus.log" &
PIDS+=("$!")
sleep 0.6
export DBUS_SESSION_BUS_ADDRESS="unix:path=$DBUS_SOCK"
echo "DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS"

say "private a11y bus (at-spi-bus-launcher + registryd)"
/usr/libexec/at-spi-bus-launcher --launch-immediately >"$LOG/atspi.log" 2>&1 &
PIDS+=("$!")
sleep 1.5
A11Y=$(dbus-send --session --print-reply --dest=org.a11y.Bus /org/a11y/bus \
        org.a11y.Bus.GetAddress 2>/dev/null | awk -F'"' '/string/{print $2}')
echo "a11y bus address: ${A11Y:-<none>}"
export AT_SPI_BUS_ADDRESS="$A11Y"

say "phantom headless compositor"
COMP="$REPO/target/release/phantom"
[ -x "$COMP" ] || COMP="$REPO/target/debug/phantom"
export PHANTOM_HEADLESS=1
export PHANTOM_SHOT="$WORK/screen.png"
export WAYLAND_DISPLAY=wayland-pn
"$COMP" --headless "$WAYLAND_DISPLAY" >"$LOG/phantom.log" 2>&1 &
PIDS+=("$!")
sleep 1.5
export PHANTOM_CTL="$XDG_RUNTIME_DIR/phantom.ctl"
echo "phantom.ctl: $PHANTOM_CTL  (exists: $([ -S "$PHANTOM_CTL" ] && echo yes || echo no))"
echo "compositor log tail:"; tail -3 "$LOG/phantom.log" 2>/dev/null | sed 's/^/  /'

say "cooperative GTK app (coop-gtk)"
export GTK_A11Y=atspi
export ACCESSIBILITY_ENABLED=1
export GDK_BACKEND=wayland
export GTK_USE_PORTAL=0
python3 "$HERE/coop-gtk-app.py" >"$LOG/coop.log" 2>&1 &
PIDS+=("$!")
sleep 3.0
echo "coop-gtk log tail:"; tail -5 "$LOG/coop.log" 2>/dev/null | sed 's/^/  /'

say "pn-atspid daemon"
export PN_ATSPID_SOCK="$XDG_RUNTIME_DIR/pn-atspid.sock"
export PN_RECORD="$WORK/record.jsonl"
python3 "$HERE/pn-atspid.py" >"$LOG/atspid.out" 2>"$LOG/atspid.err" &
PIDS+=("$!")
sleep 1.0
echo "atspid err tail:"; tail -2 "$LOG/atspid.err" | sed 's/^/  /'

CLI="python3 $HERE/pn-atspi.py"

say "PROOF A: apps on the private a11y bus"
$CLI apps

say "PROOF B: read_tree(coop) — semantic object model"
$CLI read_tree app=coop text=1

say "PROOF C: invoke by name — toggle Mute (expect checked state to flip)"
echo "--- before ---"
$CLI read_tree app=coop | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(n["role"],"|",n["name"],"|",n.get("states")) for n in d.get("nodes",[]) if "Mute" in n["name"] or "toggle" in n["role"]]'
echo "--- invoke ---"
$CLI invoke app=coop role="toggle button" name=Mute action=click
echo "--- after ---"
$CLI read_tree app=coop | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(n["role"],"|",n["name"],"|",n.get("states")) for n in d.get("nodes",[]) if "Mute" in n["name"] or "toggle" in n["role"]]'

say "PROOF C2: invoke Increment (expect counter label to advance)"
$CLI read_tree app=coop | python3 -c 'import json,sys; d=json.load(sys.stdin); [print("label:",repr(n["name"])) for n in d.get("nodes",[]) if "count" in n["name"]]'
$CLI invoke app=coop role="push button" name=Increment action=click
$CLI invoke app=coop role="push button" name=Increment action=click
$CLI read_tree app=coop | python3 -c 'import json,sys; d=json.load(sys.stdin); [print("label:",repr(n["name"])) for n in d.get("nodes",[]) if "count" in n["name"]]'

say "PROOF D: LADDER — click_at refused because invoke exists (no force)"
$CLI click_at app=coop role="push button" name=Increment

say "PROOF E: insert_text into the Message entry (Tier 1), then read it back"
$CLI insert_text app=coop role="text" name=Message text="hello brainarbeit"
$CLI read_tree app=coop text=1 | python3 -c 'import json,sys; d=json.load(sys.stdin); [print("entry text:",repr(n.get("text"))) for n in d.get("nodes",[]) if n["role"]=="text"]'

say "PROOF E2: press_keys (Tier 2) types via the compositor (act <window> type)"
$CLI press_keys app=coop text="!!"
echo "(reply above shows act_pointer/uinput bridge; cmd 'act @coop-gtk type ...')"

say "PROOF E3: BUG-1 keymap ROUND-TRIP — type punctuation/path/slash-command via the"
echo "         compositor forge, then read it BACK from the entry (closes E2's blind spot)"
B1SENT='/usr/local/bin --flag=1 (q?) {[]} @h #t |p /resume-2'
AT_SPI_BUS_ADDRESS="$AT_SPI_BUS_ADDRESS" PHANTOM_CTL="$PHANTOM_CTL" \
  python3 - "$B1SENT" <<'PYE3'
import sys, os, time, socket
import gi; gi.require_version("Atspi","2.0"); from gi.repository import Atspi
SENT=sys.argv[1]; CTL=os.environ["PHANTOM_CTL"]; Atspi.init()
def find(role):
    for i in range(Atspi.get_desktop_count()):
        d=Atspi.get_desktop(i)
        for j in range(d.get_child_count()):
            a=d.get_child_at_index(j)
            if not a or "coop" not in (a.get_name() or ""): continue
            st=[a]
            while st:
                n=st.pop()
                try:
                    if n.get_role_name()==role: return n
                    for k in range(n.get_child_count()):
                        c=n.get_child_at_index(k)
                        if c: st.append(c)
                except Exception: pass
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
e=find("text"); assert e, "no entry"
Atspi.EditableText.set_text_contents(e,""); Atspi.Component.grab_focus(e); time.sleep(0.3)
print("ctl:", ctl(f"act @coop-gtk type {SENT}")); time.sleep(0.5)
got=Atspi.Text.get_text(e,0,Atspi.Text.get_character_count(e))
print("sent:",repr(SENT)); print("got :",repr(got))
print("BUG-1:", "PASS — typed verbatim" if got==SENT else "FAIL — mismatch")
PYE3

say "PROOF F: mcp tool surface"
$CLI mcp_schema

say "PROOF G: the Record stubs (no-done-without-record)"
echo "record sink ($PN_RECORD):"; cat "$PN_RECORD" 2>/dev/null | sed 's/^/  /'

say "DONE — logs in $LOG (set KEEP=1 to retain $WORK)"
