#!/bin/sh
set -eu

DATA="${BRAINARBEIT_DATA:-/var/lib/brainarbeit}"
PND_SOCK="${PND_SOCK:-/run/pnd.sock}"
GRUBENV="${GRUBENV:-/boot/grub/grubenv}"
GRACE="${GREENBOOT_GRACE:-45}"
log(){ echo "[greenboot] $*"; }

i=0
while [ "$i" -lt "$GRACE" ]; do
    [ -S "$PND_SOCK" ] && break
    i=$((i+1)); sleep 1
done

fail=0
note(){ log "FAIL: $1"; fail=$((fail+1)); }

if command -v socat >/dev/null 2>&1 && [ -S "$PND_SOCK" ]; then
    if ! printf 'ping\n' | socat -t2 - "UNIX-CONNECT:$PND_SOCK" 2>/dev/null | grep -q '^pong'; then
        note "pnd did not answer ping"
    fi
elif [ -S "$PND_SOCK" ]; then
    log "note: socat absent — socket present, ping not verified (degraded check)"
else
    note "pnd socket $PND_SOCK absent"
fi

if mountpoint -q "$DATA"; then
    freepct="$(df -P "$DATA" 2>/dev/null | awk 'NR==2{gsub("%","",$5); print 100-$5}')"
    if [ -n "$freepct" ] && [ "$freepct" -lt 8 ]; then
        note "DATA free ${freepct}% below hard floor 8% (stop-admit)"
    fi
else
    note "DATA not mounted at $DATA"
fi

if [ -d "$DATA/record/index/.git" ]; then
    git -C "$DATA/record/index" rev-parse --git-dir >/dev/null 2>&1 || note "Record git index corrupt"
else
    log "note: Record index not yet initialised (pre-firstboot) — not counted against health"
fi

if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | grep -q ':22 ' || note "sshd not listening on :22"
fi

set_env(){ if command -v grub-editenv >/dev/null 2>&1; then grub-editenv "$GRUBENV" set "$1" 2>/dev/null || true; fi; }

if [ "$fail" -eq 0 ]; then
    log "HEALTHY — marking this slot good"
    set_env boot_attempts=0
    set_env boot_success=1
    if command -v grub-set-default >/dev/null 2>&1; then grub-set-default 0 2>/dev/null || true; fi
    exit 0
else
    log "UNHEALTHY ($fail check(s) failed) — NOT clearing boot_attempts; GRUB will revert on next reset"
    set_env boot_success=0
    install -d "$DATA/logs" 2>/dev/null || true
    echo "$(date -u +%FT%TZ) health.degraded reason=greenboot fails=$fail" >> "$DATA/logs/boot-health.log" 2>/dev/null || true
    exit 1
fi
