#!/usr/bin/env bash

set -u

REPO="${PHANTGNOME_REPO:-$HOME/phantGNOME}"
EXT="$REPO/extensions/phantom-ui@phantgnome"
fail=0
pass() { printf '  PASS  %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fail=1; }
info() { printf '  ..    %s\n' "$1"; }

PYCHK='
import sys,ast,json
s=sys.stdin.read()
try:
    raw=ast.literal_eval("("+s.split("(",1)[1])  # gdbus prints (\047json\047,)
    d=json.loads(raw[0])
except Exception as e:
    sys.stderr.write("parse-fail: %s :: %s\n"%(e,s[:200])); sys.exit(2)
import os
expr=os.environ.get("EXPR","True")
print(json.dumps(d))
sys.exit(0 if eval(expr) else 1)
'

echo "===== STATIC: gschema has stage + default-stage ====="
if command -v glib-compile-schemas >/dev/null 2>&1; then
    tmpd=$(mktemp -d)
    cp "$EXT/schemas/"*.gschema.xml "$tmpd/" 2>/dev/null
    if glib-compile-schemas --strict "$tmpd" 2>/tmp/phantom-modeschema.err; then
        pass "gschema compiles (--strict)"
    else
        bad "gschema failed:"; sed 's/^/        /' /tmp/phantom-modeschema.err
    fi
    rm -rf "$tmpd"
    grep -q "name=\"default-stage\"" "$EXT/schemas/"*.gschema.xml \
        && pass "schema declares default-stage" \
        || bad "schema missing default-stage"
fi

echo "===== STATIC: extension.js declares SetStage + DebugState + ModeController ====="
grep -q 'name="SetStage"'   "$EXT/extension.js" && pass "IFACE has SetStage"   || bad "no SetStage in IFACE"
grep -q 'name="DebugState"' "$EXT/extension.js" && pass "IFACE has DebugState" || bad "no DebugState in IFACE"
grep -q 'class ModeController' "$EXT/extension.js" && pass "ModeController class present" || bad "no ModeController"
if sed 's://.*::' "$EXT/extension.js" \
     | grep -Eq '^[[:space:]]*[A-Za-z_][A-Za-z0-9_.]*(window_group|top_window_group)\.visible[[:space:]]*=[[:space:]]*(false|0)'; then
    bad "extension.js assigns window_group.visible=false in code (STRESS #2 violation!)"
else
    pass "no window_group.visible=false blanking in code (STRESS #2 respected)"
fi
grep -q 'Clutter.Clone' "$EXT/extension.js" && pass "keep-alive Clutter.Clone present (STRESS #3)" || bad "no keep-alive clone"
grep -q 'disable-extension-version-validation' "$EXT/extension.js" && pass "pins version-validation (FUSION §9.3)" || bad "no version-validation pin"
grep -q 'disabled-extensions' "$EXT/extension.js" && pass "watchdogs disabled-extensions (FUSION §9.4)" || bad "no disabled-extensions watchdog"
grep -q 'class Lifeboat' "$EXT/extension.js" && pass "secondary in-enable() Lifeboat affordance present (FUSION §9.1)" || bad "no secondary Lifeboat"
grep -q "extension-state-changed" "$EXT/extension.js" && pass "umbrella watchdogs extension-state-changed (FUSION §9.4 / MODES §5.9)" || bad "no extension-state-changed watchdog in umbrella"

echo "===== STATIC: INDEPENDENT lifeboat is a SEPARATE mode-extension (FUSION §9.2 / STRESS #4) ====="
LIFEBOAT="$REPO/extensions/phantom-lifeboat@phantgnome"
[ -f "$LIFEBOAT/extension.js" ]  && pass "phantom-lifeboat@phantgnome extension.js present (distinct object from umbrella)" || bad "no independent lifeboat extension"
[ -f "$LIFEBOAT/metadata.json" ] && pass "phantom-lifeboat metadata.json present" || bad "no lifeboat metadata.json"
if [ -f "$LIFEBOAT/metadata.json" ]; then
    grep -q '"phantom"' "$LIFEBOAT/metadata.json" && pass "lifeboat declares the phantom session-mode" || bad "lifeboat missing phantom session-mode"
    grep -q '"shell-version"' "$LIFEBOAT/metadata.json" && grep -q '"46"' "$LIFEBOAT/metadata.json" && pass "lifeboat shell-version in lockstep (46)" || bad "lifeboat shell-version not 46"
fi
grep -q 'extension-state-changed' "$LIFEBOAT/extension.js" 2>/dev/null && pass "lifeboat independently watchdogs extension-state-changed" || bad "lifeboat missing extension-state-changed watch"
grep -q 'enableExtension' "$LIFEBOAT/extension.js" 2>/dev/null && pass "lifeboat re-enables phantom-ui (way-back affordance)" || bad "lifeboat missing re-enable affordance"
if grep -q 'phantom-lifeboat@phantgnome' "$REPO/modes/phantom.json"; then
    pass "modes/phantom.json lists phantom-lifeboat in enabledExtensions (independent load)"
else
    bad "modes/phantom.json does NOT list the independent lifeboat"
fi

echo "===== STATIC: version-validation pinned at SYSTEM layer, not only in-extension (FUSION §9.3) ====="
OVERRIDE="$REPO/session/zz-phantom.gschema.override"
if [ -f "$OVERRIDE" ]; then
    pass "system GSettings override present (session/zz-phantom.gschema.override)"
    grep -q '^\[org.gnome.shell\]' "$OVERRIDE" && grep -q 'disable-extension-version-validation=true' "$OVERRIDE" \
        && pass "override pins org.gnome.shell disable-extension-version-validation=true" \
        || bad "override does not pin disable-extension-version-validation=true"
    if command -v glib-compile-schemas >/dev/null 2>&1 && [ -d /usr/share/glib-2.0/schemas ]; then
        tmpo=$(mktemp -d)
        cp /usr/share/glib-2.0/schemas/*.gschema.xml "$tmpo/" 2>/dev/null
        cp /usr/share/glib-2.0/schemas/*.gschema.override "$tmpo/" 2>/dev/null
        cp "$OVERRIDE" "$tmpo/"
        if glib-compile-schemas "$tmpo" 2>/tmp/phantom-override.err; then
            pass "override compiles in the full system schema set (install-system.sh path)"
            val=$(GSETTINGS_SCHEMA_DIR="$tmpo" gsettings get org.gnome.shell disable-extension-version-validation 2>/dev/null)
            [ "$val" = "true" ] \
                && pass "compiled override flips disable-extension-version-validation -> true" \
                || bad "override did not flip the key (got: ${val:-<none>})"
        else
            bad "override failed to compile:"; sed 's/^/        /' /tmp/phantom-override.err
        fi
        rm -rf "$tmpo"
    fi
else
    bad "no system GSettings override shipped (in-extension runtime pin can't cover pre-enable OUT_OF_DATE)"
fi
grep -q 'zz-phantom.gschema.override' "$REPO/install-system.sh" \
    && grep -q 'glib-compile-schemas /usr/share/glib-2.0/schemas' "$REPO/install-system.sh" \
    && pass "install-system.sh ships + compiles the override" \
    || bad "install-system.sh does not ship/compile the override"

if sed 's://.*::' "$EXT/extension.js" \
     | grep -Eq 'sessionMode\._sync[[:space:]]*\(|\.switchMode[[:space:]]*\('; then
    bad "extension.js calls sessionMode._sync/switchMode in code (MODES §5.1 violation!)"
else
    pass "never calls sessionMode._sync / switchMode (MODES §5.1)"
fi

echo "===== LIVE-HEADLESS: drive SetStage A/B/C and verify DebugState ====="
HEADLESS_OK=0
gnome-shell --help-all 2>&1 | grep -qi 'headless' && HEADLESS_OK=1

if [ "$HEADLESS_OK" != 1 ]; then
    info "gnome-shell --headless NOT supported — skipping live phase"
elif [ -z "${XDG_RUNTIME_DIR:-}" ] || [ ! -d "${XDG_RUNTIME_DIR:-/nonexistent}" ]; then
    info "no usable XDG_RUNTIME_DIR — skipping live phase"
else
    info "gnome-shell --headless supported; XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"

    INNER=$(mktemp /tmp/phantom-modes-XXXXXX.sh)
    cat > "$INNER" <<'INNEREOF'
#!/usr/bin/env bash
set -u
LOG=/tmp/phantom-modes-headless.log
: > "$LOG"
export GNOME_SHELL_SESSION_MODE=phantom
export XDG_DATA_DIRS="$HOME/.local/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"

gnome-shell --headless --virtual-monitor 1280x800 --wayland --no-x11 >>"$LOG" 2>&1 &
SHELL_PID=$!

cleanup() {
    [ -n "${EDITOR_PID:-}" ] && kill "$EDITOR_PID" 2>/dev/null
    [ -n "${SHELL_PID:-}" ]  && kill "$SHELL_PID"  2>/dev/null
    sleep 1
    [ -n "${SHELL_PID:-}" ] && kill -KILL "$SHELL_PID" 2>/dev/null
}
trap cleanup EXIT

if ! timeout 25 gdbus wait --session --timeout 25 org.gnome.Phantom; then
    echo "RESULT=phantom-NOT-exported"
    echo "---- shell log tail ----"; tail -n 30 "$LOG"
    exit 11
fi
echo "RESULT=phantom-exported"

# Open a real app window so headless demotion has a target.
export WAYLAND_DISPLAY=wayland-0
unset DISPLAY
( gnome-text-editor >>"$LOG" 2>&1 & echo $! >/tmp/phantom-modes-editor.pid ) || true
sleep 1
EDITOR_PID=$(cat /tmp/phantom-modes-editor.pid 2>/dev/null)
for i in 1 2 3 4 5 6 7 8; do
    n=$(gdbus call --session --dest org.gnome.Phantom --object-path /org/gnome/Phantom \
            --method org.gnome.Phantom.ListWindows 2>/dev/null \
        | python3 -c "import sys,ast,json
try:
    raw=ast.literal_eval('('+sys.stdin.read().split('(',1)[1]); print(len(json.loads(raw[0])))
except Exception: print(0)" 2>/dev/null)
    [ "${n:-0}" -gt 0 ] && break
    sleep 1
done

echo "DBUS_FOR_OUTER=$DBUS_SESSION_BUS_ADDRESS" > /tmp/phantom-modes.busaddr
echo "READY"
sleep 30
INNEREOF
    chmod +x "$INNER"

    rm -f /tmp/phantom-modes.busaddr
    ( timeout --kill-after=5 95 dbus-run-session -- bash "$INNER" \
        > /tmp/phantom-modes.outer 2>&1 ) &
    OUTER_PID=$!

    busaddr=""
    for i in $(seq 1 50); do
        if [ -f /tmp/phantom-modes.busaddr ]; then
            busaddr=$(sed -n 's/^DBUS_FOR_OUTER=//p' /tmp/phantom-modes.busaddr)
            [ -n "$busaddr" ] && break
        fi
        kill -0 "$OUTER_PID" 2>/dev/null || break
        sleep 1
    done

    if grep -q 'RESULT=phantom-exported' /tmp/phantom-modes.outer 2>/dev/null; then
        pass "headless phantom gnome-shell booted + exported org.gnome.Phantom"
    fi

    if [ -z "$busaddr" ]; then
        bad "headless phase did not reach a live bus"
        info "---- outer log ----"; sed 's/^/        /' /tmp/phantom-modes.outer | tail -n 30
        info "---- shell log ----"; tail -n 25 /tmp/phantom-modes-headless.log 2>/dev/null | sed 's/^/        /'
    else
        pass "headless private bus live ($busaddr)"
        export DBUS_SESSION_BUS_ADDRESS="$busaddr"
        D="--session --dest org.gnome.Phantom --object-path /org/gnome/Phantom"

        wins0=$(gdbus call $D --method org.gnome.Phantom.ListWindows 2>/dev/null)
        nwin0=$(printf '%s' "$wins0" | python3 -c "import sys,ast,json
try:
 raw=ast.literal_eval('('+sys.stdin.read().split('(',1)[1]); print(len(json.loads(raw[0])))
except Exception: print(0)" 2>/dev/null)
        info "windows before switching: ${nwin0:-0}"

        run_stage() {
            local stage="$1" expr="$2"
            local applied
            applied=$(gdbus call $D --method org.gnome.Phantom.SetStage "$stage" 2>&1)
            info "SetStage($stage) -> $applied"
            local ds
            ds=$(gdbus call $D --method org.gnome.Phantom.DebugState 2>/dev/null)
            local dump
            dump=$(EXPR="$expr" printf '%s' "$ds" | EXPR="$expr" python3 -c "$PYCHK" 2>/tmp/phantom-modes.pyerr)
            local rc=$?
            info "[$stage] DebugState = ${dump:0:240}"
            if [ "$rc" = 0 ]; then
                pass "[$stage] profile applied + verified: $expr"
            else
                bad "[$stage] DebugState did NOT satisfy: $expr"
                [ -s /tmp/phantom-modes.pyerr ] && sed 's/^/        /' /tmp/phantom-modes.pyerr
            fi
        }

        run_stage A "d['gate']=='A' and d['appsHeadless']==True and d['headlessCount']>=1 and d['keepAliveClones']>=1 and d['groups']['overlay']['reactive']==False and d['groups']['hud']['reactive']==True and d['tileRouting']=='LLM_ONLY'"

        winsA=$(gdbus call $D --method org.gnome.Phantom.ListWindows 2>/dev/null)
        nwinA=$(printf '%s' "$winsA" | python3 -c "import sys,ast,json
try:
 raw=ast.literal_eval('('+sys.stdin.read().split('(',1)[1]); print(len(json.loads(raw[0])))
except Exception: print(0)" 2>/dev/null)
        if [ "${nwinA:-0}" -ge "${nwin0:-0}" ] && [ "${nwin0:-0}" -ge 1 ]; then
            pass "[A] app window still MAPPED/listed while headless ($nwinA win) — STRESS #2 honored"
        else
            info "[A] window count A=$nwinA before=$nwin0 (no app to demote if 0)"
        fi

        run_stage B "d['gate']=='B' and d['appsHeadless']==False and d['headlessCount']==0 and d['groups']['overlay']['reactive']==True and d['groups']['overlay']['opacity']==255 and d['tileRouting']=='SHARED'"

        winsB=$(gdbus call $D --method org.gnome.Phantom.ListWindows 2>/dev/null)
        nwinB=$(printf '%s' "$winsB" | python3 -c "import sys,ast,json
try:
 raw=ast.literal_eval('('+sys.stdin.read().split('(',1)[1]); print(len(json.loads(raw[0])))
except Exception: print(0)" 2>/dev/null)
        [ "${nwinB:-0}" -ge "${nwin0:-0}" ] \
            && pass "[B] app window persisted across A->B ($nwinB win)" \
            || bad "[B] app window LOST across A->B (before=$nwin0 after=$nwinB)"

        run_stage C "d['gate']=='C' and d['appsHeadless']==False and d['groups']['overlay']['reactive']==False and d['groups']['overlay']['opacity']<=60 and d['tileRouting']=='HUMAN_ONLY'"

        st=$(gdbus call $D --method org.freedesktop.DBus.Properties.Get \
                org.gnome.Phantom Stage 2>/dev/null)
        printf '%s' "$st" | grep -q "'C'" \
            && pass "Stage property mirror = C after SetStage(C)" \
            || bad "Stage mirror not C: $st"
    fi

    wait "$OUTER_PID" 2>/dev/null
    rm -f "$INNER"
fi

echo "====="
if [ "$fail" = 0 ]; then echo "RESULT: checks PASS"; else echo "RESULT: FAILURES present"; fi
exit "$fail"
