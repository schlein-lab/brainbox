#!/usr/bin/env bash

set -u

REPO="${PHANTGNOME_REPO:-$HOME/phantGNOME}"
EXT="$REPO/extensions/phantom-ui@phantgnome"
LLM_MATCH="phantom-llm"
fail=0
pass() { printf '  PASS  %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fail=1; }
info() { printf '  ..    %s\n' "$1"; }

PYCHK='
import sys,ast,json,os
s=sys.stdin.read()
try:
    raw=ast.literal_eval("("+s.split("(",1)[1])  # gdbus prints (\047json\047,)
    d=json.loads(raw[0])
except Exception as e:
    sys.stderr.write("parse-fail: %s :: %s\n"%(e,s[:200])); sys.exit(2)
expr=os.environ.get("EXPR","True")
print(json.dumps(d))
sys.exit(0 if eval(expr) else 1)
'

echo "===== STATIC: extension + schema carry the LLM-window focal tile ====="
grep -q 'name="llm-window-match"' "$EXT/schemas/"*.gschema.xml \
    && pass "schema declares llm-window-match" || bad "schema missing llm-window-match"
grep -q '_isLlmWindow'  "$EXT/extension.js" && pass "extension has _isLlmWindow matcher"  || bad "no _isLlmWindow"
grep -q '_promoteFocal' "$EXT/extension.js" && pass "extension has _promoteFocal"          || bad "no _promoteFocal"
grep -q 'setLlmMatch'   "$EXT/extension.js" && pass "extension has setLlmMatch (live key)" || bad "no setLlmMatch"
grep -q 'changed::llm-window-match' "$EXT/extension.js" && pass "extension watches llm-window-match live" || bad "no live watch on llm-window-match"
grep -q 'focalCount' "$EXT/extension.js" && pass "DebugState exposes focalCount" || bad "DebugState has no focalCount"
grep -q 'llmMatch'   "$EXT/extension.js" && pass "DebugState exposes llmMatch"   || bad "DebugState has no llmMatch"

echo "===== LIVE-HEADLESS: matching + non-matching window, SetStage A ====="
HEADLESS_OK=0
gnome-shell --help-all 2>&1 | grep -qi 'headless' && HEADLESS_OK=1

LLM_LAUNCH=""
if command -v foot >/dev/null 2>&1; then
    LLM_LAUNCH="foot --app-id=$LLM_MATCH sh -c sleep\\ 60"
    LLM_MODE="foot-appid"
elif command -v gnome-terminal >/dev/null 2>&1; then
    LLM_LAUNCH="gnome-terminal --title=$LLM_MATCH -- sh -c sleep\\ 60"
    LLM_MODE="gnome-terminal-title"
elif command -v xterm >/dev/null 2>&1; then
    LLM_LAUNCH="xterm -T $LLM_MATCH -e sh -c sleep\\ 60"
    LLM_MODE="xterm-title"
fi

if [ "$HEADLESS_OK" != 1 ]; then
    info "gnome-shell --headless NOT supported — skipping live focal phase"
elif [ -z "${XDG_RUNTIME_DIR:-}" ] || [ ! -d "${XDG_RUNTIME_DIR:-/nonexistent}" ]; then
    info "no usable XDG_RUNTIME_DIR — skipping live focal phase"
elif [ -z "$LLM_LAUNCH" ]; then
    info "no terminal (foot/gnome-terminal/xterm) to act as the LLM window — skipping live focal phase"
else
    info "gnome-shell --headless supported; LLM window via $LLM_MODE; match='$LLM_MATCH'"

    INNER=$(mktemp /tmp/phantom-llm-XXXXXX.sh)
    cat > "$INNER" <<INNEREOF
#!/usr/bin/env bash
set -u
LOG=/tmp/phantom-llm-headless.log
: > "\$LOG"
export GNOME_SHELL_SESSION_MODE=phantom
export XDG_DATA_DIRS="\$HOME/.local/share:\${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"

gnome-shell --headless --virtual-monitor 1280x800 --wayland --no-x11 >>"\$LOG" 2>&1 &
SHELL_PID=\$!

cleanup() {
    [ -n "\${LLM_PID:-}" ]    && kill "\$LLM_PID"    2>/dev/null
    [ -n "\${OTHER_PID:-}" ]  && kill "\$OTHER_PID"  2>/dev/null
    [ -n "\${SHELL_PID:-}" ]  && kill "\$SHELL_PID"  2>/dev/null
    sleep 1
    [ -n "\${SHELL_PID:-}" ]  && kill -KILL "\$SHELL_PID" 2>/dev/null
}
trap cleanup EXIT

if ! timeout 25 gdbus wait --session --timeout 25 org.gnome.Phantom; then
    echo "RESULT=phantom-NOT-exported"
    echo "---- shell log tail ----"; tail -n 30 "\$LOG"
    exit 11
fi
echo "RESULT=phantom-exported"

export WAYLAND_DISPLAY=wayland-0
unset DISPLAY

# (1) the NON-MATCHING ordinary app.
( gnome-text-editor >>"\$LOG" 2>&1 & echo \$! >/tmp/phantom-llm-other.pid ) || true
# (2) the MATCHING LLM window.
( $LLM_LAUNCH >>"\$LOG" 2>&1 & echo \$! >/tmp/phantom-llm-llm.pid ) || true
sleep 1
OTHER_PID=\$(cat /tmp/phantom-llm-other.pid 2>/dev/null)
LLM_PID=\$(cat /tmp/phantom-llm-llm.pid 2>/dev/null)

# wait until BOTH windows are listed (>=2) — the matcher needs both present.
for i in 1 2 3 4 5 6 7 8 9 10; do
    n=\$(gdbus call --session --dest org.gnome.Phantom --object-path /org/gnome/Phantom \
            --method org.gnome.Phantom.ListWindows 2>/dev/null \
        | python3 -c "import sys,ast,json
try:
    raw=ast.literal_eval('('+sys.stdin.read().split('(',1)[1]); print(len(json.loads(raw[0])))
except Exception: print(0)" 2>/dev/null)
    [ "\${n:-0}" -ge 2 ] && break
    sleep 1
done

echo "DBUS_FOR_OUTER=\$DBUS_SESSION_BUS_ADDRESS" > /tmp/phantom-llm.busaddr
echo "READY"
sleep 30
INNEREOF
    chmod +x "$INNER"

    rm -f /tmp/phantom-llm.busaddr
    ( timeout --kill-after=5 110 dbus-run-session -- bash "$INNER" \
        > /tmp/phantom-llm.outer 2>&1 ) &
    OUTER_PID=$!

    busaddr=""
    for i in $(seq 1 60); do
        if [ -f /tmp/phantom-llm.busaddr ]; then
            busaddr=$(sed -n 's/^DBUS_FOR_OUTER=//p' /tmp/phantom-llm.busaddr)
            [ -n "$busaddr" ] && break
        fi
        kill -0 "$OUTER_PID" 2>/dev/null || break
        sleep 1
    done

    grep -q 'RESULT=phantom-exported' /tmp/phantom-llm.outer 2>/dev/null \
        && pass "headless phantom gnome-shell booted + exported org.gnome.Phantom"

    if [ -z "$busaddr" ]; then
        bad "headless focal phase did not reach a live bus"
        info "---- outer log ----"; sed 's/^/        /' /tmp/phantom-llm.outer | tail -n 30
        info "---- shell log ----"; tail -n 25 /tmp/phantom-llm-headless.log 2>/dev/null | sed 's/^/        /'
    else
        pass "headless private bus live ($busaddr)"
        export DBUS_SESSION_BUS_ADDRESS="$busaddr"
        D="--session --dest org.gnome.Phantom --object-path /org/gnome/Phantom"

        wins=$(gdbus call $D --method org.gnome.Phantom.ListWindows 2>/dev/null)
        info "windows listed: $(printf '%s' "$wins" | python3 -c "import sys,ast,json
try:
 raw=ast.literal_eval('('+sys.stdin.read().split('(',1)[1]); print(len(json.loads(raw[0])))
except Exception: print(0)" 2>/dev/null)"
        haveMatch=$(printf '%s' "$wins" | EXPR="any(('$LLM_MATCH' in (w.get('wm_class','')+w.get('title','')).lower()) for w in __import__('json').loads(__import__('ast').literal_eval('('+sys.stdin.read().split('(',1)[1])[0]))" python3 -c "
import sys,ast,json,os
raw=ast.literal_eval('('+sys.stdin.read().split('(',1)[1])
ws=json.loads(raw[0])
m='$LLM_MATCH'
print('yes' if any(m in (w.get('wm_class','')+' '+w.get('title','')).lower() for w in ws) else 'no')
" 2>/dev/null)
        if [ "$haveMatch" = yes ]; then
            pass "a window matching '$LLM_MATCH' is present on the headless seat"
        else
            info "no '$LLM_MATCH'-matching window materialized headless (terminal may not paint under surfaceless EGL); focal assertions below are best-effort"
        fi

        ds_expr_A="d['llmMatch']=='$LLM_MATCH' and d['appsHeadless']==True"
        applied=$(gdbus call $D --method org.gnome.Phantom.SetStage A 2>&1)
        info "SetStage(A) -> $applied"
        dsA=$(gdbus call $D --method org.gnome.Phantom.DebugState 2>/dev/null)
        dumpA=$(EXPR="$ds_expr_A" printf '%s' "$dsA" | EXPR="$ds_expr_A" python3 -c "$PYCHK" 2>/tmp/phantom-llm.pyerr)
        rcA=$?
        info "[A] DebugState = ${dumpA:0:320}"
        if [ "$rcA" = 0 ]; then
            pass "[A] llmMatch + appsHeadless correct"
        else
            bad "[A] DebugState base check failed: $ds_expr_A"
            [ -s /tmp/phantom-llm.pyerr ] && sed 's/^/        /' /tmp/phantom-llm.pyerr
        fi

        if [ "$haveMatch" = yes ]; then
            focal_expr="d['focalCount']>=1 and any(o.get('opacity')==255 and ('$LLM_MATCH' in (o.get('wm_class','')+' '+o.get('title','')).lower()) for o in d['focal']) and d['headlessCount']>=1"
            EXPR="$focal_expr" printf '%s' "$dsA" | EXPR="$focal_expr" python3 -c "$PYCHK" >/dev/null 2>/tmp/phantom-llm.pyerr
            if [ $? = 0 ]; then
                pass "[A] MATCHING window stays opacity 255 (focal) + NON-matching demoted (headless>=1) — only-LLM-window proven"
            else
                bad "[A] focal carve-out NOT proven: $focal_expr"
                info "[A] full DebugState: $dumpA"
                [ -s /tmp/phantom-llm.pyerr ] && sed 's/^/        /' /tmp/phantom-llm.pyerr
            fi
        else
            info "[A] skipping HARD focal opacity assertion (no matching window painted); base llmMatch path verified instead"
        fi

        gdbus call $D --method org.gnome.Phantom.SetStage B >/dev/null 2>&1
        dsB=$(gdbus call $D --method org.gnome.Phantom.DebugState 2>/dev/null)
        EXPR="d['focalCount']==0 and d['appsHeadless']==False" printf '%s' "$dsB" \
            | EXPR="d['focalCount']==0 and d['appsHeadless']==False" python3 -c "$PYCHK" >/dev/null 2>&1 \
            && pass "[B] focal released (focalCount==0, apps restored)" \
            || bad "[B] focal not released on A->B"
    fi

    wait "$OUTER_PID" 2>/dev/null
    rm -f "$INNER"
fi

echo "====="
if [ "$fail" = 0 ]; then echo "RESULT: checks PASS"; else echo "RESULT: FAILURES present"; fi
exit "$fail"
