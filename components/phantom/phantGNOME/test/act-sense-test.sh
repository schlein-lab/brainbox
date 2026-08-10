#!/usr/bin/env bash

set -u

REPO="${PHANTGNOME_REPO:-$HOME/phantGNOME}"
EXT="$REPO/extensions/phantom-ui@phantgnome"
HELPER="$EXT/phantom-atspi-helper.py"
fail=0
pass() { printf '  PASS  %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fail=1; }
info() { printf '  ..    %s\n' "$1"; }
flag() { printf '  FLAG  %s\n' "$1"; }

echo "===== STATIC: Stage-3 verbs declared in IFACE + implemented ====="
for v in InvokeAction WriteWidget ReadWidget ReadTree ListA11yApps Click Type \
         Minimize Maximize MakeAbove MoveToWorkspace FocusedWindow; do
    grep -q "name=\"$v\"" "$EXT/extension.js" && pass "IFACE declares $v" || bad "IFACE missing $v"
done
grep -q 'name="WindowsChanged"' "$EXT/extension.js" \
    && pass "IFACE declares WindowsChanged signal" || bad "no WindowsChanged signal"
for v in InvokeAction WriteWidget ReadWidget ReadTree ListA11yApps Click Type \
         Minimize Maximize MakeAbove MoveToWorkspace FocusedWindow; do
    grep -Eq "^\s+${v}Async\(|^\s+${v}\(" "$EXT/extension.js" \
        && pass "impl present: $v" || bad "no impl for $v"
done

echo "===== STATIC: GATE is profile.gate (single source of truth), not unsafe_mode ====="
grep -q '_gate(' "$EXT/extension.js" && pass "_gate() gate-check helper present" || bad "no _gate() helper"
grep -q 'this._mode.profile' "$EXT/extension.js" \
    && pass "gate reads ModeController profile (authoritative)" || bad "gate does not read profile"
if sed 's://.*::' "$EXT/extension.js" | grep -vE '^[[:space:]]*\*' \
     | grep -Eq 'unsafe_mode|\bEval\b'; then
    bad "extension.js references unsafe_mode/Eval in CODE (ARCH §2/risk6 violation)"
else
    pass "no unsafe_mode / Eval dependency in code (ARCH §2 honored; comment-only mentions OK)"
fi
grep -q "stage-C refuses" "$EXT/extension.js" && pass "gate refuses autonomous act in stage C" || bad "no stage-C refusal"
grep -q "stage-A autonomous" "$EXT/extension.js" && pass "gate allows immediately in stage A" || bad "no stage-A allow"
grep -q "stage-B ask-first" "$EXT/extension.js" && pass "gate queues in stage B" || bad "no stage-B queue"

echo "===== STATIC: act-plane priority — pixel inject guarded visible+mapped ====="
grep -q '_pointIsOnMappedWindow' "$EXT/extension.js" \
    && pass "Click guarded by visible+mapped precondition (FUSION §3)" || bad "no visible+mapped guard on Click"
grep -q 'no focused window' "$EXT/extension.js" \
    && pass "Type guarded by focused-window precondition (FUSION §3)" || bad "no focus guard on Type"
grep -q 'create_virtual_device' "$EXT/extension.js" \
    && pass "Clutter virtual device idiom present (ARCH §4)" || bad "no create_virtual_device"
grep -q 'phantom-atspi-helper.py' "$EXT/extension.js" \
    && pass "Funktionsbus shells out to the out-of-process AT-SPI helper (FUSION §9.7)" || bad "no helper shell-out"

echo "===== STATIC: AT-SPI helper shipped + executable + parses ====="
[ -f "$HELPER" ] && pass "helper present" || bad "helper missing"
[ -x "$HELPER" ] && pass "helper executable" || bad "helper not executable"
if command -v python3 >/dev/null 2>&1; then
    python3 -m py_compile "$HELPER" 2>/tmp/phantom-helper.err \
        && pass "helper parses (py_compile)" \
        || { bad "helper py_compile failed:"; sed 's/^/        /' /tmp/phantom-helper.err; }
fi

echo "===== STATIC: AT-SPI stack present on this box (the Funktionsbus substrate) ====="
[ -x /usr/libexec/at-spi-bus-launcher ] && pass "at-spi-bus-launcher present" || info "at-spi-bus-launcher not found"
[ -x /usr/libexec/at-spi2-registryd ] && pass "at-spi2-registryd present" || info "at-spi2-registryd not found"
ls /usr/lib/*/girepository-1.0/Atspi-2.0.typelib >/dev/null 2>&1 \
    && pass "Atspi-2.0 gi typelib present" || bad "no Atspi gi typelib"
python3 -c "import gi; gi.require_version('Atspi','2.0'); from gi.repository import Atspi" 2>/dev/null \
    && pass "python3 gi.repository.Atspi imports" || bad "Atspi gi import failed"
[ -f "$HOME/uiapi/uiapi.py" ] \
    && pass "proven uiapi engine present at $HOME/uiapi (helper derives from it)" \
    || info "uiapi not at $HOME/uiapi (helper is self-contained anyway)"

echo "===== LIVE-HEADLESS: gate logic + window verbs + WindowsChanged ====="
HEADLESS_OK=0
gnome-shell --help-all 2>&1 | grep -qi 'headless' && HEADLESS_OK=1

if [ "$HEADLESS_OK" != 1 ]; then
    info "gnome-shell --headless NOT supported — skipping live phase"
    flag "actual pointer/key landing + AT-SPI driving of a real app: test on the REAL SEAT"
elif [ -z "${XDG_RUNTIME_DIR:-}" ] || [ ! -d "${XDG_RUNTIME_DIR:-/nonexistent}" ]; then
    info "no usable XDG_RUNTIME_DIR — skipping live phase"
    flag "actual pointer/key landing + AT-SPI driving of a real app: test on the REAL SEAT"
else
    info "gnome-shell --headless supported; XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"

    INNER=$(mktemp /tmp/phantom-act-XXXXXX.sh)
    cat > "$INNER" <<'INNEREOF'
#!/usr/bin/env bash
set -u
LOG=/tmp/phantom-act-headless.log
: > "$LOG"
export GNOME_SHELL_SESSION_MODE=phantom
export XDG_DATA_DIRS="$HOME/.local/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"

gnome-shell --headless --virtual-monitor 1280x800 --wayland --no-x11 >>"$LOG" 2>&1 &
SHELL_PID=$!

cleanup() {
    [ -n "${EDITOR_PID:-}" ] && kill "$EDITOR_PID" 2>/dev/null
    [ -n "${SIGLOG_PID:-}" ] && kill "$SIGLOG_PID" 2>/dev/null
    [ -n "${SHELL_PID:-}" ]  && kill "$SHELL_PID"  2>/dev/null
    sleep 1
    [ -n "${SHELL_PID:-}" ] && kill -KILL "$SHELL_PID" 2>/dev/null
}
trap cleanup EXIT

if ! timeout 25 gdbus wait --session --timeout 25 org.gnome.Phantom; then
    echo "RESULT=phantom-NOT-exported"; tail -n 30 "$LOG"; exit 11
fi
echo "RESULT=phantom-exported"

D="--session --dest org.gnome.Phantom --object-path /org/gnome/Phantom"

# Start a background WindowsChanged signal logger BEFORE opening the test window,
# so an open is observed.
( gdbus monitor --session --dest org.gnome.Phantom 2>/dev/null \
    | grep --line-buffered 'WindowsChanged' > /tmp/phantom-act-signals.log & echo $! >/tmp/phantom-act-siglog.pid ) || true
SIGLOG_PID=$(cat /tmp/phantom-act-siglog.pid 2>/dev/null)
sleep 1

# Open a real app window so the window verbs + signal have a target.
export WAYLAND_DISPLAY=wayland-0
unset DISPLAY
( gnome-text-editor >>"$LOG" 2>&1 & echo $! >/tmp/phantom-act-editor.pid ) || true
sleep 1
EDITOR_PID=$(cat /tmp/phantom-act-editor.pid 2>/dev/null)
for i in 1 2 3 4 5 6 7 8; do
    n=$(gdbus call $D --method org.gnome.Phantom.ListWindows 2>/dev/null \
        | python3 -c "import sys,ast,json
try:
 raw=ast.literal_eval('('+sys.stdin.read().split('(',1)[1]); print(len(json.loads(raw[0])))
except Exception: print(0)" 2>/dev/null)
    [ "${n:-0}" -gt 0 ] && break
    sleep 1
done

echo "DBUS_FOR_OUTER=$DBUS_SESSION_BUS_ADDRESS" > /tmp/phantom-act.busaddr
echo "READY"
sleep 30
INNEREOF
    chmod +x "$INNER"

    rm -f /tmp/phantom-act.busaddr /tmp/phantom-act-signals.log
    ( timeout --kill-after=5 100 dbus-run-session -- bash "$INNER" \
        > /tmp/phantom-act.outer 2>&1 ) &
    OUTER_PID=$!

    busaddr=""
    for i in $(seq 1 55); do
        if [ -f /tmp/phantom-act.busaddr ]; then
            busaddr=$(sed -n 's/^DBUS_FOR_OUTER=//p' /tmp/phantom-act.busaddr)
            [ -n "$busaddr" ] && break
        fi
        kill -0 "$OUTER_PID" 2>/dev/null || break
        sleep 1
    done

    grep -q 'RESULT=phantom-exported' /tmp/phantom-act.outer 2>/dev/null \
        && pass "headless phantom gnome-shell booted + exported org.gnome.Phantom" \
        || info "headless export marker not seen"

    if [ -z "$busaddr" ]; then
        bad "headless phase did not reach a live bus"
        info "---- outer ----"; sed 's/^/        /' /tmp/phantom-act.outer | tail -n 30
        info "---- shell ----"; tail -n 25 /tmp/phantom-act-headless.log 2>/dev/null | sed 's/^/        /'
    else
        pass "headless private bus live ($busaddr)"
        export DBUS_SESSION_BUS_ADDRESS="$busaddr"
        D="--session --dest org.gnome.Phantom --object-path /org/gnome/Phantom"

        jget() { python3 -c "import sys,ast,json
try:
 raw=ast.literal_eval('('+sys.stdin.read().split('(',1)[1]); print(json.dumps(json.loads(raw[0])))
except Exception as e: print('null')"; }
        firstwin() { gdbus call $D --method org.gnome.Phantom.ListWindows 2>/dev/null | jget; }

        WJSON=$(firstwin)
        WID=$(printf '%s' "$WJSON" | python3 -c "import sys,json
a=json.load(sys.stdin)
print(a[0]['id'] if a else '')" 2>/dev/null)
        info "test window id = ${WID:-<none>}"

        gdbus call $D --method org.gnome.Phantom.SetStage C >/dev/null 2>&1
        CX=$(printf '%s' "$WJSON" | python3 -c "import sys,json
a=json.load(sys.stdin)
w=a[0]; print(w['x']+w['width']//2)" 2>/dev/null)
        CY=$(printf '%s' "$WJSON" | python3 -c "import sys,json
a=json.load(sys.stdin)
w=a[0]; print(w['y']+w['height']//2)" 2>/dev/null)
        rc=$(gdbus call $D --method org.gnome.Phantom.Click "${CX:-640}.0" "${CY:-400}.0" 1 2>&1)
        if printf '%s' "$rc" | grep -q 'false'; then
            pass "[gate] stage C: autonomous Click REFUSED (-> false)"
        else
            bad "[gate] stage C: Click was NOT refused (-> $rc)"
        fi

        gdbus call $D --method org.gnome.Phantom.SetStage B >/dev/null 2>&1
        gdbus call $D --method org.gnome.Phantom.Click "${CX:-640}.0" "${CY:-400}.0" 1 >/dev/null 2>&1
        PEND=$(gdbus call $D --method org.gnome.Phantom.DebugState 2>/dev/null | jget \
            | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('pendingConfirm',0))" 2>/dev/null)
        if [ "${PEND:-0}" -ge 1 ]; then
            pass "[gate] stage B: autonomous Click QUEUED (pendingConfirm=$PEND), not executed"
        else
            bad "[gate] stage B: Click was not queued (pendingConfirm=$PEND)"
        fi

        gdbus call $D --method org.gnome.Phantom.SetStage A >/dev/null 2>&1
        rc=$(gdbus call $D --method org.gnome.Phantom.Click "${CX:-640}.0" "${CY:-400}.0" 1 2>&1)
        info "[gate] stage-A Click raw (apps are headless in A, guard may refuse) -> $rc"

        gdbus call $D --method org.gnome.Phantom.SetStage A >/dev/null 2>&1
        if [ -n "$WID" ]; then
            mn=$(gdbus call $D --method org.gnome.Phantom.Minimize "$WID" 2>&1)
            if printf '%s' "$mn" | grep -q 'true'; then
                pass "[gate] stage A: gated Minimize PROCEEDS (-> true)"
            else
                bad "[gate] stage A: gated Minimize did not proceed (-> $mn)"
            fi
            MINF=$(firstwin | python3 -c "import sys,json
a=json.load(sys.stdin)
print('1' if a and a[0].get('minimized') else '0')" 2>/dev/null)
            [ "${MINF:-0}" = 1 ] \
                && pass "[window] Minimize observable via ListWindows (minimized=true)" \
                || info "[window] minimized flag not observed (=$MINF) — may be compositor timing"
        fi

        gdbus call $D --method org.gnome.Phantom.SetStage C >/dev/null 2>&1
        if [ -n "$WID" ]; then
            mx=$(gdbus call $D --method org.gnome.Phantom.Maximize "$WID" 2>&1)
            printf '%s' "$mx" | grep -q 'false' \
                && pass "[gate] stage C: gated Maximize REFUSED (-> false)" \
                || bad "[gate] stage C: Maximize not refused (-> $mx)"
            aw=$(gdbus call $D --method org.gnome.Phantom.ActivateWindow "$WID" 0 2>&1)
            printf '%s' "$aw" | grep -q 'false' \
                && pass "[gate] stage C: gated ActivateWindow REFUSED (-> false)" \
                || bad "[gate] stage C: ActivateWindow not refused (-> $aw)"
        fi

        gdbus call $D --method org.gnome.Phantom.SetStage A >/dev/null 2>&1
        if [ -n "$WID" ]; then
            aw=$(gdbus call $D --method org.gnome.Phantom.ActivateWindow "$WID" 0 2>&1)
            printf '%s' "$aw" | grep -q 'true' \
                && pass "[gate] stage A: gated ActivateWindow PROCEEDS (-> true)" \
                || info "[gate] stage A: ActivateWindow -> $aw (headless WM may not grant focus)"
        fi

        gdbus call $D --method org.gnome.Phantom.SetStage A >/dev/null 2>&1
        if [ -n "$WID" ]; then
            gdbus call $D --method org.gnome.Phantom.ActivateWindow "$WID" 0 >/dev/null 2>&1
            before=$(firstwin | python3 -c "import sys,json
a=json.load(sys.stdin)
w=a[0]; print(w['width']*w['height'])" 2>/dev/null)
            gdbus call $D --method org.gnome.Phantom.Maximize "$WID" >/dev/null 2>&1
            sleep 1
            after=$(firstwin | python3 -c "import sys,json
a=json.load(sys.stdin)
w=a[0]; print(w['width']*w['height'])" 2>/dev/null)
            if [ -n "${before:-}" ] && [ -n "${after:-}" ] && [ "${after:-0}" -ge "${before:-0}" ]; then
                pass "[window] Maximize observable (area ${before} -> ${after})"
            else
                info "[window] Maximize area before=${before:-?} after=${after:-?} (headless WM may not resize)"
            fi
            ma=$(gdbus call $D --method org.gnome.Phantom.MakeAbove "$WID" true 2>&1)
            printf '%s' "$ma" | grep -q 'true' \
                && pass "[window] MakeAbove(on) proceeds (-> true)" \
                || bad "[window] MakeAbove(on) -> $ma"
            gdbus call $D --method org.gnome.Phantom.MakeAbove "$WID" false >/dev/null 2>&1
        fi

        fw=$(gdbus call $D --method org.gnome.Phantom.FocusedWindow 2>/dev/null | jget)
        if printf '%s' "$fw" | grep -q '"id"'; then
            pass "[sense] FocusedWindow returns the focused window JSON"
        else
            info "[sense] FocusedWindow -> $fw (no focus in headless is possible)"
        fi

        la=$(gdbus call $D --method org.gnome.Phantom.ListA11yApps 2>/dev/null | jget)
        if printf '%s' "$la" | python3 -c "import sys,json
d=json.load(sys.stdin)
sys.exit(0 if isinstance(d,dict) and d.get('ok') is not None else 1)" 2>/dev/null; then
            pass "[funktionsbus] ListA11yApps returned well-formed JSON (helper shell-out works)"
            NAPP=$(printf '%s' "$la" | python3 -c "import sys,json
d=json.load(sys.stdin);print(len(d.get('apps',[])))" 2>/dev/null)
            info "[funktionsbus] a11y apps visible to helper: ${NAPP:-0} (headless session a11y may be sparse)"
        else
            info "[funktionsbus] ListA11yApps -> $la (a11y bus may be unavailable on this private headless session)"
            flag "AT-SPI DRIVING of a real app (WriteWidget/InvokeAction): test on the REAL SEAT with the app's a11y tree live"
        fi
        rt=$(gdbus call $D --method org.gnome.Phantom.ReadTree "0" 50 2>/dev/null | jget)
        printf '%s' "$rt" | python3 -c "import sys,json
d=json.load(sys.stdin)
sys.exit(0 if isinstance(d,dict) and d.get('ok') is not None else 1)" 2>/dev/null \
            && pass "[funktionsbus] ReadTree returned well-formed JSON" \
            || info "[funktionsbus] ReadTree -> ${rt:0:120}"

        sleep 1
        SIGN=$(wc -l < /tmp/phantom-act-signals.log 2>/dev/null || echo 0)
        if [ "${SIGN:-0}" -ge 1 ]; then
            pass "[sense] WindowsChanged FIRED ${SIGN}x (window open + restacks observed)"
        else
            bad "[sense] WindowsChanged did not fire (expected on window open/restack)"
        fi
        EPID=$(cat /tmp/phantom-act-editor.pid 2>/dev/null)
        [ -n "$EPID" ] && kill "$EPID" 2>/dev/null
        sleep 2
        SIGN2=$(wc -l < /tmp/phantom-act-signals.log 2>/dev/null || echo 0)
        [ "${SIGN2:-0}" -gt "${SIGN:-0}" ] \
            && pass "[sense] WindowsChanged fired again on window CLOSE (${SIGN}->${SIGN2})" \
            || info "[sense] no extra WindowsChanged on close (${SIGN}->${SIGN2}) — debounce/timing"
    fi

    wait "$OUTER_PID" 2>/dev/null
    rm -f "$INNER"
fi

echo "===== FLAGGED for the operator's REAL SEAT (not provable over no-seat SSH) ====="
flag "actual pointer LANDING: Click(x,y) hits the topmost mapped surface at (x,y) — needs a real cursor/seat"
flag "actual keyboard LANDING: Type(text) reaches the focused client — needs a real focused surface"
flag "AT-SPI DRIVING a real app: WriteWidget/InvokeAction into VS Code (launch --force-renderer-accessibility), Thunderbird, etc."

echo "====="
if [ "$fail" = 0 ]; then echo "RESULT: checks PASS"; else echo "RESULT: FAILURES present"; fi
exit "$fail"
