#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMG="${IMG:-/var/tmp/pn-rootfs-b.img}"
KERNEL="${KERNEL:-/var/tmp/pn-vmlinuz}"
LOG="${LOG:-/var/tmp/pn-fullsystem-b.log}"
SECS="${SECS:-210}"
SSH_PORT="${SSH_PORT:-2225}"
SSH_USER="${SSH_USER:-${SUDO_USER:-$USER}}"
SSH_PASS="${SSH_PASS:-pntest}"
[ -r "$IMG" ]    || { echo "image not found: $IMG (run build-fullsystem-rootfs-b.sh)"; exit 1; }
[ -r "$KERNEL" ] || { echo "kernel not readable: $KERNEL"; exit 1; }

ACCEL="kvm:tcg"; [ -w /dev/kvm ] || ACCEL="tcg"
: > "$LOG"
echo "booting pn-init PID1 (OPTION b, fully no-systemd) accel=$ACCEL img=$IMG (window ${SECS}s)"
qemu-system-x86_64 \
  -machine accel=$ACCEL -cpu max -m 1024 -smp 2 -no-reboot -display none \
  -kernel "$KERNEL" \
  -drive file="$IMG",format=raw,if=virtio \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22 -device virtio-net-pci,netdev=n0 \
  -append "root=/dev/vda ro console=ttyS0 panic=-1 init=/sbin/pn-init pn.fullsystem pn.netif=enp0s2" \
  -serial file:"$LOG" 2>"${LOG%.log}.qemu.err" &
QPID=$!
START_TS=$(date +%s)

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=3 -o PreferredAuthentications=password -o PubkeyAuthentication=no"
probe(){ sshpass -p "$SSH_PASS" ssh -p "$SSH_PORT" $SSH_OPTS "$SSH_USER"@127.0.0.1 '
    echo PN_SSH_LOGIN_OK; echo "PID1=$(cat /proc/1/comm)"; echo "WHOAMI=$(id -un) UID=$(id -u)";
    echo "SYSTEMD_PROCS=$(ps -e -o comm= 2>/dev/null | grep -c "^systemd" || true)";
    echo "SYSTEMD_LIST=$(ps -e -o pid=,comm= 2>/dev/null | grep systemd || echo none)";
    echo "PNSTACK_UID1000:"; ps -e -o uid=,pid=,comm=,args= 2>/dev/null | awk "\$1==1000{print}" | grep -E "pndstub|pnd|pn-llmd|phantom|python|zyrkel|sleep|agetty" | sed "s/^/  /" | head -20;
    ( ss -tln 2>/dev/null||busybox netstat -tln 2>/dev/null )|grep -q ":22 " && echo LIVE_SSHD_22;
    [ -S /run/user/1000/pnd.sock ] && echo LIVE_PND_SOCK; ls -l /run/user/1000/pnd.sock 2>/dev/null;
    [ -d /run/portioneer ] && echo "LIVE_PORTIONEER_DIR $(ls -ld /run/portioneer)";
    echo "UPTIME=$(cut -d. -f1 /proc/uptime)s"' 2>/dev/null; }

echo "waiting for SSH on 127.0.0.1:${SSH_PORT} (acceptance bar) ..."
SSH_OK=0; FIRST=""; FIRST_AT=0
for i in $(seq 1 "$SECS"); do
  kill -0 "$QPID" 2>/dev/null || { echo "!! qemu exited early at ${i}s (unexpected — a reset would show here)"; break; }
  OUT="$(probe)"
  if echo "$OUT" | grep -q PN_SSH_LOGIN_OK; then
    SSH_OK=1; FIRST="$OUT"; FIRST_AT=$i
    echo "==== SSH LOGIN SUCCEEDED at ${i}s (acceptance bar) ===="; echo "$OUT"
    { echo "==== LIVE SSH-SESSION CHECKS (first, ${i}s) ===="; echo "$OUT"; } >> "$LOG"
    break
  fi
  sleep 1
done

echo "holding the VM up for the full ${SECS}s window to measure stability ..."
LATE=""; ALIVE_AT_END=0
END=$(( START_TS + SECS ))
while [ "$(date +%s)" -lt "$END" ]; do
  if ! kill -0 "$QPID" 2>/dev/null; then echo "!! qemu EXITED before window end (likely a self-reset/panic)"; break; fi
  sleep 5
done
if kill -0 "$QPID" 2>/dev/null; then
  ALIVE_AT_END=1
  LATE="$(probe)"
  if echo "$LATE" | grep -q PN_SSH_LOGIN_OK; then
    echo "==== SSH STILL REACHABLE near window end (uptime proof) ===="; echo "$LATE"
    { echo "==== LIVE SSH-SESSION CHECKS (late) ===="; echo "$LATE"; } >> "$LOG"
  fi
fi

kill "$QPID" 2>/dev/null; wait "$QPID" 2>/dev/null

echo; echo "==== SERIAL LOG ($LOG) — tail ===="; tail -n 60 "$LOG"
echo; echo "==== STABILITY EVIDENCE (boot/reset markers in serial) ===="
echo "  'PID1 up' occurrences (expect exactly 1 for a single continuous boot):"
grep -c "PID1 up" "$LOG" | sed 's/^/    /'
RESET_RE="WATCHDOG: pnd unrecoverable|SUSPEND pet|software-reset fallback -> restart|CRASH-LOOP DETECTED|^\[pn-init\] reboot$|^\[pn-init\] poweroff$"
echo "  give-up / watchdog-suspend / software-reset-FIRE / reboot lines (expect NONE):"
grep -nE "$RESET_RE" "$LOG" | sed 's/^/    /' || echo "    (none — no self-reset fired)"
echo "  L2 canary lines:"; grep -n "L2 canary" "$LOG" | sed 's/^/    /' || true

echo; echo "==== VERDICT (OPTION b) ===="
PASS=0; FAIL=0
v(){ [ "$2" = ok ] && { echo "  PASS  $1"; PASS=$((PASS+1)); } || { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }; }
BOOTS="$(grep -c "PID1 up" "$LOG" 2>/dev/null || echo 0)"

[ "$SSH_OK" = 1 ] && v "ACCEPTANCE BAR: SSH login over LAN" ok || v "ACCEPTANCE BAR: SSH login over LAN" no
echo "$FIRST" | grep -q "PID1=pn-init" && v "PID1 == pn-init (/proc/1/comm)" ok || v "PID1 == pn-init" no
echo "$FIRST" | grep -q "SYSTEMD_PROCS=0" && v "ZERO systemd processes (fully no-systemd)" ok || { v "ZERO systemd processes" no; echo "    -> $(echo "$FIRST"|grep SYSTEMD_LIST)"; }
echo "$FIRST" | grep -qE "pndstub|pnd" && echo "$FIRST" | grep -q "UID=1000" && v "pn stack running as uid 1000" ok || v "pn stack running as uid 1000" no
grep -qE "LIVE_PND_SOCK|PND_SOCK_PRESENT" "$LOG" && v "pnd per-user socket present (watchdog happy)" ok || v "pnd socket present" no
grep -q "L2 canary OK" "$LOG" && v "watchdog L2 canary OK (pnd dispatches)" ok || v "watchdog L2 canary" no
NORESET=ok
grep -qE "$RESET_RE" "$LOG" && NORESET=no
[ "${BOOTS:-0}" = 1 ] || NORESET=no
[ "$ALIVE_AT_END" = 1 ] || NORESET=no
v "STABILITY: single continuous boot, no self-reset, alive >=3min (boots=$BOOTS alive_end=$ALIVE_AT_END)" "$NORESET"

echo "  ----"; echo "  $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ] && echo "  RESULT: GREEN (option b: fully no-systemd, STABLE)" || echo "  RESULT: NOT FULLY GREEN"
exit 0
