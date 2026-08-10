#!/usr/bin/env bash

set -u

REPO="${PHANTGNOME_REPO:-$HOME/phantGNOME}"
EXT="$REPO/extensions/phantom-ui@phantgnome"
fail=0
pass() { printf '  PASS  %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fail=1; }
info() { printf '  ..    %s\n' "$1"; }

PYCHK='
import sys,ast,json,os
s=sys.stdin.read()
try:
    raw=ast.literal_eval("("+s.split("(",1)[1])
    d=json.loads(raw[0])
except Exception as e:
    sys.stderr.write("parse-fail: %s :: %s\n"%(e,s[:200])); sys.exit(2)
expr=os.environ.get("EXPR","True")
print(json.dumps(d))
sys.exit(0 if eval(expr) else 1)
'

echo "===== STATIC: extension declares the FUSION verbs + carries the machinery ====="
grep -q 'name="CreateMirror"'  "$EXT/extension.js" && pass "IFACE: CreateMirror"  || bad "no CreateMirror verb"
grep -q 'name="DestroyMirror"' "$EXT/extension.js" && pass "IFACE: DestroyMirror" || bad "no DestroyMirror verb"
grep -q 'name="ListMirrors"'   "$EXT/extension.js" && pass "IFACE: ListMirrors"   || bad "no ListMirrors verb"
grep -q 'name="RunRecipe"'     "$EXT/extension.js" && pass "IFACE: RunRecipe"     || bad "no RunRecipe verb"
grep -q 'class FusionTile'     "$EXT/extension.js" && pass "FusionTile class present"  || bad "no FusionTile"
grep -q 'class FusionScene'    "$EXT/extension.js" && pass "FusionScene class present" || bad "no FusionScene"
grep -q 'new Clutter.Clone'    "$EXT/extension.js" && pass "MIRROR uses Clutter.Clone (clone-not-reparent, §2)" || bad "no Clutter.Clone"
grep -q 'injectClickAt'        "$EXT/extension.js" && pass "InputProxy routes via gated injectClickAt (§3)" || bad "no injectClickAt route"
grep -q 'destroyAll'           "$EXT/extension.js" && pass "FusionScene.destroyAll for disable() teardown" || bad "no destroyAll"
grep -q 'this._fusionScene.destroyAll' "$EXT/extension.js" && pass "disable() calls fusionScene.destroyAll (perfect-reverse)" || bad "disable() does not tear down fusion scene"
if grep -q 'window_group.*remove_child\|remove_child.*window_group' "$EXT/extension.js"; then
    bad "MERGE/reparent appears wired (should be RESERVED per §2)"
else
    pass "MERGE/reparent NOT wired (MIRROR-only default, §2 rule honored)"
fi

echo "===== LIVE-HEADLESS: CreateMirror / ListMirrors / DestroyMirror / RunRecipe ====="
HEADLESS_OK=0
gnome-shell --help-all 2>&1 | grep -qi 'headless' && HEADLESS_OK=1

SRC_LAUNCH=""
if command -v gnome-text-editor >/dev/null 2>&1; then
    SRC_LAUNCH="gnome-text-editor"; SRC_MODE="gnome-text-editor"
elif command -v foot >/dev/null 2>&1; then
    SRC_LAUNCH="foot sh -c sleep\\ 60"; SRC_MODE="foot"
elif command -v xterm >/dev/null 2>&1; then
    SRC_LAUNCH="xterm -e sh -c sleep\\ 60"; SRC_MODE="xterm"
fi

if [ "$HEADLESS_OK" != 1 ]; then
    info "gnome-shell --headless NOT supported — skipping live fusion phase"
elif [ -z "${XDG_RUNTIME_DIR:-}" ] || [ ! -d "${XDG_RUNTIME_DIR:-/nonexistent}" ]; then
    info "no usable XDG_RUNTIME_DIR — skipping live fusion phase"
else
    info "gnome-shell --headless supported; mirror source via ${SRC_MODE:-none}"

    INNER=$(mktemp /tmp/phantom-fusion-XXXXXX.sh)
    cat > "$INNER" <<INNEREOF
#!/usr/bin/env bash
set -u
LOG=/tmp/phantom-fusion-headless.log
: > "\$LOG"
export GNOME_SHELL_SESSION_MODE=phantom
export XDG_DATA_DIRS="\$HOME/.local/share:\${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"

gnome-shell --headless --virtual-monitor 1280x800 --wayland --no-x11 >>"\$LOG" 2>&1 &
SHELL_PID=\$!

cleanup() {
    [ -n "\${SRC_PID:-}" ]   && kill "\$SRC_PID"   2>/dev/null
    [ -n "\${SHELL_PID:-}" ] && kill "\$SHELL_PID" 2>/dev/null
    sleep 1
    [ -n "\${SHELL_PID:-}" ] && kill -KILL "\$SHELL_PID" 2>/dev/null
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

# Open ONE real app window to mirror.
if [ -n "$SRC_LAUNCH" ]; then
    ( $SRC_LAUNCH >>"\$LOG" 2>&1 & echo \$! >/tmp/phantom-fusion-src.pid ) || true
fi
sleep 1
SRC_PID=\$(cat /tmp/phantom-fusion-src.pid 2>/dev/null)

# Wait until >=1 window is listed (best effort — surfaceless EGL may never paint).
for i in 1 2 3 4 5 6 7 8; do
    n=\$(gdbus call --session --dest org.gnome.Phantom --object-path /org/gnome/Phantom \
            --method org.gnome.Phantom.ListWindows 2>/dev/null \
        | python3 -c "import sys,ast,json
try:
    raw=ast.literal_eval('('+sys.stdin.read().split('(',1)[1]); print(len(json.loads(raw[0])))
except Exception: print(0)" 2>/dev/null)
    [ "\${n:-0}" -ge 1 ] && break
    sleep 1
done

echo "DBUS_FOR_OUTER=\$DBUS_SESSION_BUS_ADDRESS" > /tmp/phantom-fusion.busaddr
echo "READY"
sleep 30
INNEREOF
    chmod +x "$INNER"

    rm -f /tmp/phantom-fusion.busaddr
    ( timeout --kill-after=5 110 dbus-run-session -- bash "$INNER" \
        > /tmp/phantom-fusion.outer 2>&1 ) &
    OUTER_PID=$!

    busaddr=""
    for i in $(seq 1 60); do
        if [ -f /tmp/phantom-fusion.busaddr ]; then
            busaddr=$(sed -n 's/^DBUS_FOR_OUTER=//p' /tmp/phantom-fusion.busaddr)
            [ -n "$busaddr" ] && break
        fi
        kill -0 "$OUTER_PID" 2>/dev/null || break
        sleep 1
    done

    grep -q 'RESULT=phantom-exported' /tmp/phantom-fusion.outer 2>/dev/null \
        && pass "headless phantom gnome-shell booted + exported org.gnome.Phantom (enable() clean)"

    if [ -z "$busaddr" ]; then
        bad "headless fusion phase did not reach a live bus"
        info "---- outer log ----"; sed 's/^/        /' /tmp/phantom-fusion.outer | tail -n 30
        info "---- shell log ----"; tail -n 25 /tmp/phantom-fusion-headless.log 2>/dev/null | sed 's/^/        /'
    else
        pass "headless private bus live ($busaddr)"
        export DBUS_SESSION_BUS_ADDRESS="$busaddr"
        D="--session --dest org.gnome.Phantom --object-path /org/gnome/Phantom"

        gdbus call $D --method org.gnome.Phantom.SetStage A >/dev/null 2>&1
        dsA=$(gdbus call $D --method org.gnome.Phantom.DebugState 2>/dev/null)
        EXPR="d['gate']=='A' and d['appsHeadless']==True and d['tileRouting']=='LLM_ONLY' and d['fusionCount']==0" \
            printf '%s' "$dsA" | EXPR="d['gate']=='A' and d['appsHeadless']==True and d['tileRouting']=='LLM_ONLY' and d['fusionCount']==0" python3 -c "$PYCHK" >/dev/null 2>&1 \
            && pass "[baseline] SetStage A still yields LLM-focal full-auto profile (gate A, appsHeadless, LLM_ONLY, no stale mirrors)" \
            || bad  "[baseline] SetStage A profile regressed"

        wins=$(gdbus call $D --method org.gnome.Phantom.ListWindows 2>/dev/null)
        SRCID=$(printf '%s' "$wins" | python3 -c "
import sys,ast,json
try:
    raw=ast.literal_eval('('+sys.stdin.read().split('(',1)[1]); ws=json.loads(raw[0])
    print(ws[0]['id'] if ws else '')
except Exception: print('')
" 2>/dev/null)

        if [ -n "$SRCID" ]; then
            info "mirror source window id = $SRCID"
            cm=$(gdbus call $D --method org.gnome.Phantom.CreateMirror "$SRCID" 0.4 false 2>&1)
            info "CreateMirror -> ${cm:0:300}"
            MID=$(printf '%s' "$cm" | python3 -c "
import sys,ast,json
try:
    raw=ast.literal_eval('('+sys.stdin.read().split('(',1)[1]); d=json.loads(raw[0])
    print(d.get('mirrorId','') if d.get('ok') else '')
except Exception: print('')
" 2>/dev/null)
            if [ -n "$MID" ]; then
                pass "CreateMirror($SRCID) -> mirror created ($MID)"
            else
                bad "CreateMirror did not return a mirrorId"
            fi

            lm=$(gdbus call $D --method org.gnome.Phantom.ListMirrors 2>/dev/null)
            EXPR="len(d)>=1 and any(str(m.get('sourceId'))=='$SRCID' for m in d)" \
                printf '%s' "$lm" | EXPR="len(d)>=1 and any(str(m.get('sourceId'))=='$SRCID' for m in d)" python3 -c "$PYCHK" >/dev/null 2>&1 \
                && pass "ListMirrors shows the mirror tile bound to source $SRCID" \
                || bad  "ListMirrors did not show the created mirror"

            ds=$(gdbus call $D --method org.gnome.Phantom.DebugState 2>/dev/null)
            EXPR="d['fusionCount']>=1" printf '%s' "$ds" | EXPR="d['fusionCount']>=1" python3 -c "$PYCHK" >/dev/null 2>&1 \
                && pass "DebugState surfaces fusionCount>=1 (mirror tile exists)" \
                || bad  "DebugState fusionCount did not reflect the mirror"

            rendered=$(printf '%s' "$lm" | python3 -c "
import sys,ast,json
try:
    raw=ast.literal_eval('('+sys.stdin.read().split('(',1)[1]); d=json.loads(raw[0])
    print('yes' if any(m.get('rendered') and m.get('sourceWidth',0)>0 for m in d) else 'no')
except Exception: print('no')
" 2>/dev/null)
            if [ "$rendered" = yes ]; then
                pass "clone RENDERS: rendered=true with non-empty source size (live texture mirrored)"
            else
                info "clone rendered=false (source likely did not paint under surfaceless EGL) — plumbing proven, render best-effort"
            fi

            gdbus call $D --method org.gnome.Phantom.DestroyMirror "$MID" >/dev/null 2>&1
            ds2=$(gdbus call $D --method org.gnome.Phantom.DebugState 2>/dev/null)
            EXPR="d['fusionCount']==0" printf '%s' "$ds2" | EXPR="d['fusionCount']==0" python3 -c "$PYCHK" >/dev/null 2>&1 \
                && pass "DestroyMirror($MID) removed the tile (fusionCount==0)" \
                || bad  "DestroyMirror did not remove the tile"
        else
            info "no app window materialized headless (surfaceless EGL) — CreateMirror live-create skipped"
            cm=$(gdbus call $D --method org.gnome.Phantom.CreateMirror 999999999 0.4 false 2>&1)
            EXPR="d.get('ok')==False" printf '%s' "$cm" | EXPR="d.get('ok')==False" python3 -c "$PYCHK" >/dev/null 2>&1 \
                && pass "CreateMirror(bad-id) fails closed (ok:false), no crash" \
                || bad  "CreateMirror(bad-id) did not fail closed"
            lm=$(gdbus call $D --method org.gnome.Phantom.ListMirrors 2>/dev/null)
            EXPR="len(d)==0" printf '%s' "$lm" | EXPR="len(d)==0" python3 -c "$PYCHK" >/dev/null 2>&1 \
                && pass "ListMirrors returns well-formed empty list (no phantom mirrors)" \
                || bad  "ListMirrors malformed when empty"
        fi

        rr=$(gdbus call $D --method org.gnome.Phantom.RunRecipe \
            '{"steps":[{"op":"read","app":"0","selector":"","capture":"x"}]}' 2>&1)
        info "RunRecipe(read) -> ${rr:0:220}"
        EXPR="'steps' in d and len(d['steps'])>=1 and d['steps'][0]['op']=='read'" \
            printf '%s' "$rr" | EXPR="'steps' in d and len(d['steps'])>=1 and d['steps'][0]['op']=='read'" python3 -c "$PYCHK" >/dev/null 2>&1 \
            && pass "RunRecipe runs a read step (recipe pipeline executes)" \
            || bad  "RunRecipe read step did not execute"

        gdbus call $D --method org.gnome.Phantom.SetStage C >/dev/null 2>&1
        rrc=$(gdbus call $D --method org.gnome.Phantom.RunRecipe \
            '{"steps":[{"op":"write","app":"0","selector":"0/0","text":"x"}]}' 2>&1)
        info "RunRecipe(write@C) -> ${rrc:0:220}"
        EXPR="d['steps'][0].get('executed')==False or 'refus' in str(d['steps'][0].get('gate','')).lower() or d['steps'][0].get('ok')==False" \
            printf '%s' "$rrc" | EXPR="d['steps'][0].get('executed')==False or 'refus' in str(d['steps'][0].get('gate','')).lower() or d['steps'][0].get('ok')==False" python3 -c "$PYCHK" >/dev/null 2>&1 \
            && pass "[gate] WRITE recipe step REFUSED in stage C (recipe sinks honor the gate)" \
            || bad  "[gate] WRITE recipe step was NOT gated in stage C"
        gdbus call $D --method org.gnome.Phantom.SetStage A >/dev/null 2>&1
    fi

    wait "$OUTER_PID" 2>/dev/null
    rm -f "$INNER"
fi

echo "====="
echo "FLAGGED for the operator's REAL SEAT (not provable over no-seat SSH):"
echo "  FLAG  InputProxy INPUT ROUTING: a click on a visible+mapped mirror tile"
echo "        maps via buffer_rect and re-injects (injectClickAt) into the real"
echo "        source — needs a real cursor/seat + a painted, mapped source window."
echo "  FLAG  clone live-texture repaint under a real GPU (surfaceless EGL here)."
echo "====="
if [ "$fail" = 0 ]; then echo "RESULT: checks PASS"; else echo "RESULT: FAILURES present"; fi
exit "$fail"
