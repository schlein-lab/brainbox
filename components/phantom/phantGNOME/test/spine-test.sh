#!/usr/bin/env bash

set -u

REPO="${PHANTGNOME_REPO:-$HOME/phantGNOME}"
EXT="$REPO/extensions/phantom-ui@phantgnome"
fail=0
pass() { printf '  PASS  %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fail=1; }
info() { printf '  ..    %s\n' "$1"; }

echo "===== STATIC: extension.js syntax (gjs -m, ESM) ====="
if command -v gjs >/dev/null 2>&1; then
    gjs -m "$EXT/extension.js" >/tmp/phantom-gjs.out 2>&1
    if grep -q 'SyntaxError' /tmp/phantom-gjs.out; then
        bad "extension.js has a SyntaxError:"; grep 'SyntaxError' /tmp/phantom-gjs.out | sed 's/^/        /'
    elif grep -qE 'Failed to resolve imports|ImportError' /tmp/phantom-gjs.out; then
        pass "extension.js parses clean (only unresolved shell imports, as expected outside gnome-shell)"
    elif [ ! -s /tmp/phantom-gjs.out ]; then
        pass "extension.js parses and runs clean"
    else
        info "gjs produced unexpected output (no SyntaxError):"; sed 's/^/        /' /tmp/phantom-gjs.out
        pass "extension.js has no SyntaxError"
    fi
else
    info "gjs not found, skipping JS syntax check"
fi

echo "===== STATIC: JSON validity (python3 -m json.tool) ====="
for j in "$EXT/metadata.json" "$REPO/modes/phantom.json"; do
    if python3 -m json.tool "$j" >/dev/null 2>/tmp/phantom-json.err; then
        pass "valid JSON: $j"
    else
        bad "invalid JSON: $j"; sed 's/^/        /' /tmp/phantom-json.err
    fi
done

echo "===== STATIC: metadata.json fields ====="
md="$EXT/metadata.json"
uuid=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['uuid'])" "$md" 2>/dev/null)
[ "$uuid" = "phantom-ui@phantgnome" ] && pass "uuid = phantom-ui@phantgnome" || bad "uuid wrong: '$uuid'"
if python3 -c "import json,sys;sv=json.load(open(sys.argv[1]))['shell-version'];sys.exit(0 if '46' in sv else 1)" "$md" 2>/dev/null; then
    pass "shell-version includes 46"; else bad "shell-version missing 46"; fi
if python3 -c "import json,sys;sm=json.load(open(sys.argv[1])).get('session-modes',[]);sys.exit(0 if 'phantom' in sm and 'user' in sm else 1)" "$md" 2>/dev/null; then
    pass "session-modes == [phantom,user]"; else bad "session-modes missing phantom/user"; fi

echo "===== STATIC: modes/phantom.json keys ====="
pj="$REPO/modes/phantom.json"
chk_mode() {
    if python3 -c "import json,sys;d=json.load(open(sys.argv[1]));sys.exit(0 if ($2) else 1)" "$pj" 2>/dev/null; then
        pass "phantom.json: $1"; else bad "phantom.json: $1"; fi
}
chk_mode "parentMode == restrictive"          "d.get('parentMode')=='restrictive'"
chk_mode "isPrimary == true"                  "d.get('isPrimary') is True"
chk_mode "hasOverview == false"               "d.get('hasOverview') is False"
chk_mode "hasWorkspaces == false"             "d.get('hasWorkspaces') is False"
chk_mode "panel left/center/right all empty"  "d.get('panel',{}).get('left')==[] and d['panel'].get('center')==[] and d['panel'].get('right')==[]"
chk_mode "enabledExtensions has phantom-ui"   "'phantom-ui@phantgnome' in d.get('enabledExtensions',[])"

echo "===== STATIC: gschema compiles ====="
if command -v glib-compile-schemas >/dev/null 2>&1; then
    tmpd=$(mktemp -d)
    cp "$EXT/schemas/"*.gschema.xml "$tmpd/" 2>/dev/null
    if glib-compile-schemas --strict "$tmpd" 2>/tmp/phantom-schema.err; then
        pass "gschema compiles (--strict)"
    else
        bad "gschema failed to compile:"; sed 's/^/        /' /tmp/phantom-schema.err
    fi
    rm -rf "$tmpd"
else
    info "glib-compile-schemas not found, skipping"
fi

exercise_verbs() {
    local label="$1"
    local D="--session --dest org.gnome.Phantom --object-path /org/gnome/Phantom"

    local stage
    stage=$(gdbus call $D --method org.freedesktop.DBus.Properties.Get \
        org.gnome.Phantom Stage 2>&1)
    info "[$label] Stage = $stage"

    local wins
    wins=$(gdbus call $D --method org.gnome.Phantom.ListWindows 2>&1)
    info "[$label] ListWindows -> ${wins:0:200}"
    local first_id
    first_id=$(printf '%s' "$wins" | python3 -c "
import sys,json,ast
s=sys.stdin.read()
try:
    raw=ast.literal_eval('('+s.split('(',1)[1])  # gdbus prints ('json',)
    arr=json.loads(raw[0]); print(arr[0]['id'] if arr else '')
except Exception:
    print('')" 2>/dev/null)

    if [ -n "$first_id" ]; then
        local act
        act=$(gdbus call $D --method org.gnome.Phantom.ActivateWindow "$first_id" 0 2>&1)
        if printf '%s' "$act" | grep -q 'true\|false'; then
            pass "[$label] ActivateWindow($first_id,0) -> $act"
        else
            bad "[$label] ActivateWindow($first_id,0) -> $act"
        fi
    else
        info "[$label] ListWindows returned no windows; skipping ActivateWindow"
    fi

    local snapf="/tmp/phantom-snap-$$.bin"
    if gdbus call $D --method org.gnome.Phantom.Snapshot 2>/tmp/phantom-snap.err \
        | python3 -c "
import sys,re
s=sys.stdin.read()
try:
    hexes=re.findall(r'0x([0-9a-fA-F]{2})', s)
    data=bytes(int(h,16) for h in hexes)
    if not data:
        sys.stderr.write('parse: no byte tokens found\n'); sys.exit(3)
    open(sys.argv[1],'wb').write(data)
    print(len(data))
    sys.exit(0)
except Exception as e:
    sys.stderr.write('parse: %s\n'%e); sys.exit(3)
" "$snapf" >/tmp/phantom-snap.len 2>>/tmp/phantom-snap.err; then
        local n; n=$(cat /tmp/phantom-snap.len 2>/dev/null)
        local magic; magic=$(head -c8 "$snapf" 2>/dev/null | od -An -tx1 | tr -s ' ' | sed 's/^ //;s/ $//')
        if [ -s "$snapf" ] && [ "$magic" = "89 50 4e 47 0d 0a 1a 0a" ]; then
            pass "[$label] Snapshot -> $n bytes, valid PNG magic (89 50 4e 47 ...)"
        elif [ -s "$snapf" ]; then
            bad "[$label] Snapshot returned $n bytes but bad magic ($magic)"
        else
            bad "[$label] Snapshot returned empty"
        fi
    else
        bad "[$label] Snapshot call/parse failed: $(head -c200 /tmp/phantom-snap.err)"
    fi
    rm -f "$snapf"
}

echo "===== LIVE-HEADLESS: throwaway phantom gnome-shell on a private bus ====="
HEADLESS_OK=0
gnome-shell --help-all 2>&1 | grep -qi 'headless' && HEADLESS_OK=1

if [ "$HEADLESS_OK" != 1 ]; then
    info "gnome-shell --headless NOT supported on this build — skipping headless phase"
elif [ -z "${XDG_RUNTIME_DIR:-}" ] || [ ! -d "${XDG_RUNTIME_DIR:-/nonexistent}" ]; then
    info "no usable XDG_RUNTIME_DIR — cannot start a headless display server; skipping"
else
    info "gnome-shell --headless supported; XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"
    info "NOTE: over a no-seat SSH/tty, mutter cannot get DRM master and falls back"
    info "      to a SURFACELESS software renderer (libEGL 'Permission denied' on"
    info "      /dev/dri/* is EXPECTED and non-fatal). The shell + D-Bus + Snapshot"
    info "      path still run; only GPU-accelerated paint is unavailable."

    INNER=$(mktemp /tmp/phantom-headless-XXXXXX.sh)
    cat > "$INNER" <<'INNEREOF'
#!/usr/bin/env bash
set -u
LOG=/tmp/phantom-headless.log
: > "$LOG"
export GNOME_SHELL_SESSION_MODE=phantom
# Make ~/.local/share resolve the phantom mode + extension.
export XDG_DATA_DIRS="$HOME/.local/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"

# Launch the headless shell in the background on this private bus.
gnome-shell --headless --virtual-monitor 1280x800 --wayland --no-x11 \
    >>"$LOG" 2>&1 &
SHELL_PID=$!

cleanup() {
    # Terminate ONLY the processes we launched, by captured PID. No patterns.
    [ -n "${EDITOR_PID:-}" ] && kill "$EDITOR_PID" 2>/dev/null
    [ -n "${SHELL_PID:-}" ]  && kill "$SHELL_PID"  2>/dev/null
    # give them a moment, then hard-stop only those exact PIDs
    sleep 1
    [ -n "${SHELL_PID:-}" ] && kill -KILL "$SHELL_PID" 2>/dev/null
}
trap cleanup EXIT

# Wait (bounded) for org.gnome.Phantom to appear on this bus.
if timeout 25 gdbus wait --session --timeout 25 org.gnome.Phantom; then
    echo "RESULT=phantom-exported"
else
    echo "RESULT=phantom-NOT-exported"
    echo "---- shell log tail ----"
    tail -n 25 "$LOG"
    exit 11
fi

# Launch a test window on the shell's wayland display so ListWindows has a target.
export WAYLAND_DISPLAY=wayland-0
unset DISPLAY
( gnome-text-editor >>"$LOG" 2>&1 & echo $! >/tmp/phantom-editor.pid ) || true
sleep 1
EDITOR_PID=$(cat /tmp/phantom-editor.pid 2>/dev/null)

# Give the window time to map + register with the compositor.
for i in 1 2 3 4 5 6 7 8; do
    n=$(gdbus call --session --dest org.gnome.Phantom \
            --object-path /org/gnome/Phantom \
            --method org.gnome.Phantom.ListWindows 2>/dev/null \
        | python3 -c "import sys,ast,json
try:
    raw=ast.literal_eval('('+sys.stdin.read().split('(',1)[1]); print(len(json.loads(raw[0])))
except Exception: print(0)" 2>/dev/null)
    [ "${n:-0}" -gt 0 ] && break
    sleep 1
done

# Mark the boundary so the OUTER script knows the bus is live, then idle briefly
# while the OUTER script runs the verb exercises on the SAME bus address.
echo "DBUS_FOR_OUTER=$DBUS_SESSION_BUS_ADDRESS" > /tmp/phantom-headless.busaddr
echo "READY"
# Keep the session alive for the outer exerciser (bounded).
sleep 18
INNEREOF
    chmod +x "$INNER"

    rm -f /tmp/phantom-headless.busaddr
    ( timeout --kill-after=5 70 dbus-run-session -- bash "$INNER" \
        > /tmp/phantom-headless.outer 2>&1 ) &
    OUTER_PID=$!

    busaddr=""
    for i in $(seq 1 40); do
        if [ -f /tmp/phantom-headless.busaddr ]; then
            busaddr=$(sed -n 's/^DBUS_FOR_OUTER=//p' /tmp/phantom-headless.busaddr)
            [ -n "$busaddr" ] && break
        fi
        kill -0 "$OUTER_PID" 2>/dev/null || break
        sleep 1
    done

    if grep -q 'RESULT=phantom-exported' /tmp/phantom-headless.outer 2>/dev/null; then
        pass "headless phantom gnome-shell booted and exported org.gnome.Phantom"
    fi

    if [ -n "$busaddr" ]; then
        pass "headless private bus is live ($busaddr)"
        DBUS_SESSION_BUS_ADDRESS="$busaddr" exercise_verbs "headless"
    else
        bad "headless phase did not reach a live org.gnome.Phantom bus"
        info "---- outer log ----"; sed 's/^/        /' /tmp/phantom-headless.outer | tail -n 30
        info "---- shell log tail ----"; tail -n 25 /tmp/phantom-headless.log 2>/dev/null | sed 's/^/        /'
    fi

    wait "$OUTER_PID" 2>/dev/null
    rm -f "$INNER"
fi

echo "===== LIVE-ATTACH: org.gnome.Phantom already on the ambient session bus ====="
if command -v gdbus >/dev/null 2>&1 && [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ] \
   && gdbus call --session --dest org.gnome.Phantom \
        --object-path /org/gnome/Phantom \
        --method org.freedesktop.DBus.Introspectable.Introspect >/dev/null 2>&1; then
    pass "org.gnome.Phantom present on ambient bus"
    exercise_verbs "attach"
else
    info "no ambient org.gnome.Phantom (not inside a phantom GNOME session) — attach phase skipped"
fi

echo "====="
if [ "$fail" = 0 ]; then echo "RESULT: checks PASS"; else echo "RESULT: FAILURES present"; fi
exit "$fail"
