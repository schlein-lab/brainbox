#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMG="${IMG:-/var/tmp/pn-nosystemd.img}"
KERNEL="${KERNEL:-/var/tmp/pn-vmlinuz}"
LOG="${LOG:-/var/tmp/pn-nosystemd.log}"
SECS="${SECS:-150}"; SSH_PORT="${SSH_PORT:-2224}"
[ -r "$IMG" ] || { echo "image not found: $IMG"; exit 1; }
[ -r "$KERNEL" ] || { echo "kernel not readable: $KERNEL"; exit 1; }
ACCEL="kvm:tcg"; [ -w /dev/kvm ] || ACCEL="tcg"
: > "$LOG"
echo "booting no-systemd rootfs as PID1 (accel=$ACCEL)"
qemu-system-x86_64 -machine accel=$ACCEL -cpu max -m 1024 -smp 2 -no-reboot -display none \
  -kernel "$KERNEL" -drive file="$IMG",format=raw,if=virtio \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22 -device virtio-net-pci,netdev=n0 \
  -append "root=/dev/vda ro console=ttyS0 panic=-1 init=/usr/lib/brainarbeit/pn-init pn.fullsystem pn.devmgr=mdev" \
  -serial file:"$LOG" 2>"${LOG%.log}.qemu.err" &
QPID=$!
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=3 -o PreferredAuthentications=password -o PubkeyAuthentication=no"
SSH_OK=0; OUT2=""
for i in $(seq 1 "$SECS"); do
  kill -0 "$QPID" 2>/dev/null || { echo "qemu exited early"; break; }
  OUT2="$(sshpass -p pntest ssh -p "$SSH_PORT" $SSH_OPTS brainarbeit@127.0.0.1 '
      echo PN_SSH_LOGIN_OK; echo "PID1=$(cat /proc/1/comm)"; id;
      echo "SYSTEMD_PROCS=$(ps -e -o comm= 2>/dev/null | grep -c "^systemd" || true)";
      echo "SYSTEMD_LIST=$(ps -e -o pid=,comm= 2>/dev/null | grep systemd || echo none)";
      ( ss -tln 2>/dev/null||busybox netstat -tln 2>/dev/null )|grep -q ":22 " && echo LIVE_SSHD_22;
      [ -S /run/user/1000/pnd.sock ] && echo LIVE_PND_SOCK; ip -br addr' 2>/dev/null)"
  if echo "$OUT2" | grep -q PN_SSH_LOGIN_OK; then
    SSH_OK=1; echo "==== SSH LOGIN SUCCEEDED ===="; echo "$OUT2"
    { echo "==== LIVE SSH-SESSION CHECKS ===="; echo "$OUT2"; } >> "$LOG"; break
  fi
  sleep 1
done
sleep 3; kill "$QPID" 2>/dev/null; wait "$QPID" 2>/dev/null
echo; echo "==== SERIAL LOG tail ===="; tail -n 45 "$LOG"
echo; echo "==== VERDICT ===="
PASS=0; FAIL=0; v(){ [ "$2" = ok ] && { echo "  PASS  $1"; PASS=$((PASS+1)); } || { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }; }
grep -qiE "pn-init.* PID1 up" "$LOG" && v "pn-init is PID1" ok || v "pn-init is PID1" no
echo "$OUT2" | grep -q "PID1=pn-init" && v "/proc/1/comm == pn-init" ok || v "/proc/1/comm == pn-init" no
[ "$SSH_OK" = 1 ] && v "SSH login over LAN (acceptance bar)" ok || v "SSH login over LAN" no
grep -qE "LIVE_SSHD_22" "$LOG" && v "sshd listening :22" ok || v "sshd listening :22" no
grep -qE "LIVE_PND_SOCK|PND_SOCK" "$LOG" && v "pn stack pnd.sock up (uid 1000)" ok || v "pn stack pnd.sock up" no
grep -q "L2 canary OK" "$LOG" && v "watchdog L2 canary (pnd dispatches)" ok || v "watchdog L2 canary" no
if echo "$OUT2" | grep -q "SYSTEMD_PROCS=0"; then v "ZERO systemd processes (we replaced it)" ok
else v "ZERO systemd processes" no; echo "    -> $(echo "$OUT2"|grep SYSTEMD_LIST)"; fi
echo "  ----"; echo "  $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ] && echo "  RESULT: GREEN (no-systemd stack under pn-init)" || echo "  RESULT: NOT FULLY GREEN"
exit 0
