#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMG="${IMG:-/var/tmp/pn-rootfs.img}"
KERNEL="${KERNEL:-/boot/vmlinuz-$(uname -r)}"
LOG="${LOG:-/var/tmp/pn-fullsystem.log}"
SECS="${SECS:-150}"
SSH_PORT="${SSH_PORT:-2222}"
[ -r "$IMG" ]    || { echo "image not found: $IMG (run build-fullsystem-rootfs.sh)"; exit 1; }
[ -r "$KERNEL" ] || { echo "kernel not readable: $KERNEL"; exit 1; }

ACCEL="kvm:tcg"; [ -w /dev/kvm ] || ACCEL="tcg"
: > "$LOG"
echo "booting pn-init as PID1 (full-system) accel=$ACCEL img=$IMG (timeout ${SECS}s)"
qemu-system-x86_64 \
  -machine accel=$ACCEL -cpu max -m 1024 -smp 2 -no-reboot -display none \
  -kernel "$KERNEL" \
  -drive file="$IMG",format=raw,if=virtio \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22 -device virtio-net-pci,netdev=n0 \
  -append "root=/dev/vda ro console=ttyS0 panic=-1 init=/sbin/pn-init pn.fullsystem pn.netif=enp0s2" \
  -serial file:"$LOG" 2>"${LOG%.log}.qemu.err" &
QPID=$!

echo "waiting for SSH on 127.0.0.1:${SSH_PORT} ..."
SSH_OK=0
for i in $(seq 1 "$SECS"); do
  if ! kill -0 "$QPID" 2>/dev/null; then echo "qemu exited early"; break; fi
  if command -v sshpass >/dev/null 2>&1; then
    OUT="$(sshpass -p pntest ssh -p "$SSH_PORT" \
            -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            -o ConnectTimeout=3 -o PreferredAuthentications=password -o PubkeyAuthentication=no \
            pn@127.0.0.1 'echo PN_SSH_LOGIN_OK; id; uname -n; ip -br addr; \
              ( ss -tln 2>/dev/null || busybox netstat -tln 2>/dev/null ) | grep -q ":22 " && echo LIVE_SSHD_22; \
              [ -S /run/user/1000/pnd.sock ] && echo LIVE_PND_SOCK; ls -l /run/user/1000/pnd.sock 2>/dev/null' 2>/dev/null)"
    if echo "$OUT" | grep -q PN_SSH_LOGIN_OK; then
      SSH_OK=1
      echo "==== SSH LOGIN SUCCEEDED (acceptance bar) ===="
      echo "$OUT"
      { echo "==== LIVE SSH-SESSION CHECKS ===="; echo "$OUT"; } >> "$LOG"
      break
    fi
  fi
  sleep 1
done

sleep 3
kill "$QPID" 2>/dev/null; wait "$QPID" 2>/dev/null

echo
echo "==== SERIAL LOG ($LOG) — tail ===="
tail -n 60 "$LOG"
echo
echo "==== VERDICT ===="
PASS=0; FAIL=0
chk(){ if grep -q "$2" "$LOG" 2>/dev/null || [ "${3:-}" = "$2" ]; then echo "  PASS  $1"; PASS=$((PASS+1)); else echo "  FAIL  $1"; FAIL=$((FAIL+1)); fi; }
grep -q "remounted / read-write"              "$LOG" && { echo "  PASS  F1 root remounted rw"; PASS=$((PASS+1)); } || { echo "  FAIL  F1 root remounted rw"; FAIL=$((FAIL+1)); }
grep -q "fstab mounts done"                   "$LOG" && { echo "  PASS  F1 fstab processed";    PASS=$((PASS+1)); } || { echo "  FAIL  F1 fstab processed";    FAIL=$((FAIL+1)); }
grep -q "swapon /swap.img"                    "$LOG" && { echo "  PASS  F1 swap enabled";       PASS=$((PASS+1)); } || { echo "  FAIL  F1 swap enabled";       FAIL=$((FAIL+1)); }
grep -qE "F2 udev: coldplug triggered|F2 dev: .*(coldplug|mdev)" "$LOG" && { echo "  PASS  F2 udev coldplug";      PASS=$((PASS+1)); } || { echo "  FAIL  F2 udev coldplug";      FAIL=$((FAIL+1)); }
grep -q "DHCP lease acquired"                 "$LOG" && { echo "  PASS  F3 DHCP lease";         PASS=$((PASS+1)); } || { echo "  FAIL  F3 DHCP lease";         FAIL=$((FAIL+1)); }
grep -qE "SSHD_LISTENING_22|LIVE_SSHD_22|Server listening on .* port 22" "$LOG" && { echo "  PASS  sshd listening :22";    PASS=$((PASS+1)); } || { echo "  FAIL  sshd listening :22";    FAIL=$((FAIL+1)); }
[ "$SSH_OK" = 1 ]                                     && { echo "  PASS  SSH login over LAN (BAR)"; PASS=$((PASS+1)); } || { echo "  FAIL  SSH login over LAN (BAR)"; FAIL=$((FAIL+1)); }
grep -q "user-session] up as uid=1000"        "$LOG" && { echo "  PASS  F4 user session uid1000"; PASS=$((PASS+1)); } || { echo "  FAIL  F4 user session uid1000"; FAIL=$((FAIL+1)); }
grep -qE "PND_SOCK_PRESENT|LIVE_PND_SOCK"      "$LOG" && { echo "  PASS  pn stack pnd.sock up";  PASS=$((PASS+1)); } || { echo "  FAIL  pn stack pnd.sock up";  FAIL=$((FAIL+1)); }
grep -q "L2 canary OK"                         "$LOG" && { echo "  PASS  watchdog L2 canary (pnd dispatches)"; PASS=$((PASS+1)); } || { echo "  FAIL  watchdog L2 canary"; FAIL=$((FAIL+1)); }
echo "  ----"
echo "  $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ] && echo "  RESULT: GREEN" || echo "  RESULT: NOT FULLY GREEN"
exit 0
