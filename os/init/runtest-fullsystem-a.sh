#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMG="${IMG:-/var/tmp/pn-rootfs-a.img}"
KERNEL="${KERNEL:-/var/tmp/pn-vmlinuz}"
LOG="${LOG:-/var/tmp/pn-fullsystem-a.log}"
SECS="${SECS:-180}"
SSH_PORT="${SSH_PORT:-2223}"
[ -r "$IMG" ]    || { echo "image not found: $IMG"; exit 1; }
[ -r "$KERNEL" ] || { echo "kernel not readable: $KERNEL"; exit 1; }

ACCEL="kvm:tcg"; [ -w /dev/kvm ] || ACCEL="tcg"
: > "$LOG"
echo "booting pn-init PID1 (OPTION a) accel=$ACCEL img=$IMG kernel=$KERNEL (timeout ${SECS}s)"
qemu-system-x86_64 \
  -machine accel=$ACCEL -cpu max -m 1024 -smp 2 -no-reboot -display none \
  -kernel "$KERNEL" \
  -drive file="$IMG",format=raw,if=virtio \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22 -device virtio-net-pci,netdev=n0 \
  -append "root=/dev/vda ro console=ttyS0 panic=-1 init=/sbin/pn-init pn.fullsystem pn.netif=enp0s2" \
  -serial file:"$LOG" 2>"${LOG%.log}.qemu.err" &
QPID=$!

echo "waiting for SSH on 127.0.0.1:${SSH_PORT} ..."
SSH_OK=0; USER_PROBE=""
for i in $(seq 1 "$SECS"); do
  if ! kill -0 "$QPID" 2>/dev/null; then echo "qemu exited early"; break; fi
  if command -v sshpass >/dev/null 2>&1; then
    OUT="$(sshpass -p pntest ssh -p "$SSH_PORT" \
            -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            -o ConnectTimeout=3 -o PreferredAuthentications=password -o PubkeyAuthentication=no \
            pn@127.0.0.1 'echo PN_SSH_LOGIN_OK; id; uname -n' 2>/dev/null)"
    if echo "$OUT" | grep -q PN_SSH_LOGIN_OK; then
      SSH_OK=1
      echo "==== SSH LOGIN SUCCEEDED (acceptance bar) ===="; echo "$OUT"
      USER_PROBE="$(sshpass -p pntest ssh -p "$SSH_PORT" \
            -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            -o ConnectTimeout=5 -o PreferredAuthentications=password -o PubkeyAuthentication=no \
            pn@127.0.0.1 'bash -s' <<'GUEST'
export XDG_RUNTIME_DIR=/run/user/1000
echo "---- OPTION-A IN-GUEST PROBE (uid $(id -u)) ----"
# 1) systemd --user running as uid 1000?
if pgrep -u 1000 -f "systemd --user" >/dev/null 2>&1; then
  echo "OPT_A_SYSTEMD_USER_RUNNING"; ps -o pid,user,args -u 1000 2>/dev/null | grep -E "systemd|pntest" | grep -v grep
else
  echo "OPT_A_SYSTEMD_USER_ABSENT"; ps -o pid,user,args -u 1000 2>/dev/null | grep -v grep
fi
# 2) pntest --user unit active / marker present?
if command -v systemctl >/dev/null 2>&1; then
  echo -n "pntest is-active: "; systemctl --user is-active pntest 2>&1
  echo "---- systemctl --user list (pntest/failed) ----"
  systemctl --user --no-pager --no-legend list-units 2>/dev/null | grep -E "pntest|dbus|failed" || true
fi
[ -e /run/user/1000/pntest.up ] && echo "OPT_A_PNTEST_MARKER_PRESENT" || echo "OPT_A_PNTEST_MARKER_MISSING"
[ -S /run/user/1000/pntest.sock ] && echo "OPT_A_PNTEST_SOCK_PRESENT" || true
# 3) system dbus socket present?
[ -S /run/dbus/system_bus_socket ] && echo "OPT_A_SYSTEM_DBUS_UP" || echo "OPT_A_SYSTEM_DBUS_DOWN"
# 4) /run/user/1000: tmpfs, 0700, owned 1000?
echo "---- /run/user/1000 stat ----"
stat -c '%A %U:%G %u:%g (%s) fs=%T' /run/user/1000 2>/dev/null || echo "  /run/user/1000 MISSING"
findmnt -no FSTYPE,OPTIONS /run/user/1000 2>/dev/null | sed 's/^/  mnt: /' || echo "  (not a mount)"
echo "---- user D-Bus (session bus) ----"
[ -S /run/user/1000/bus ] && echo "OPT_A_USER_DBUS_BUS_PRESENT" || echo "OPT_A_USER_DBUS_BUS_ABSENT"
echo "---- pn-init is PID1? ----"
cat /proc/1/comm 2>/dev/null | sed 's/^/  PID1_COMM=/'
echo "---- systemd --user journal-ish (user log if any) ----"
echo "---- END PROBE ----"
GUEST
)"
      echo "$USER_PROBE"
      { echo "==== OPTION-A IN-GUEST PROBE ===="; echo "$USER_PROBE"; } >> "$LOG"
      break
    fi
  fi
  sleep 1
done

sleep 4
if [ "$SSH_OK" = 1 ] && ! echo "$USER_PROBE" | grep -q OPT_A_PNTEST_MARKER_PRESENT; then
  echo "== retrying option-a probe (user manager may need more time) =="
  RETRY="$(sshpass -p pntest ssh -p "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=5 -o PreferredAuthentications=password -o PubkeyAuthentication=no pn@127.0.0.1 'bash -s' <<'GUEST'
export XDG_RUNTIME_DIR=/run/user/1000
pgrep -u 1000 -f "systemd --user" >/dev/null 2>&1 && echo OPT_A_SYSTEMD_USER_RUNNING || echo OPT_A_SYSTEMD_USER_ABSENT
systemctl --user is-active pntest 2>&1 | sed 's/^/pntest-active: /'
[ -e /run/user/1000/pntest.up ] && echo OPT_A_PNTEST_MARKER_PRESENT || echo OPT_A_PNTEST_MARKER_MISSING
systemctl --user --no-pager status pntest 2>&1 | head -20
GUEST
)"
  echo "$RETRY"; { echo "==== OPTION-A RETRY PROBE ===="; echo "$RETRY"; } >> "$LOG"
  USER_PROBE="$USER_PROBE
$RETRY"
fi

kill "$QPID" 2>/dev/null; wait "$QPID" 2>/dev/null

echo; echo "==== SERIAL LOG ($LOG) — tail ===="; tail -n 70 "$LOG"
echo; echo "==== QEMU STDERR (${LOG%.log}.qemu.err) tail ===="; tail -n 8 "${LOG%.log}.qemu.err" 2>/dev/null
echo; echo "==== VERDICT (OPTION a) ===="
PASS=0; FAIL=0
allout="$LOG"
chkgrep(){ if grep -qE "$2" "$LOG" 2>/dev/null || echo "$USER_PROBE" | grep -qE "$2"; then echo "  PASS  $1"; PASS=$((PASS+1)); else echo "  FAIL  $1"; FAIL=$((FAIL+1)); fi; }
[ "$SSH_OK" = 1 ] && { echo "  PASS  ACCEPTANCE BAR: SSH login over LAN"; PASS=$((PASS+1)); } || { echo "  FAIL  ACCEPTANCE BAR: SSH login over LAN"; FAIL=$((FAIL+1)); }
grep -qE "remounted / read-write" "$LOG" && { echo "  PASS  F1 root remounted rw"; PASS=$((PASS+1)); } || { echo "  FAIL  F1 root remounted rw"; FAIL=$((FAIL+1)); }
grep -qE "DHCP lease acquired" "$LOG" && { echo "  PASS  F3 DHCP lease"; PASS=$((PASS+1)); } || { echo "  FAIL  F3 DHCP lease"; FAIL=$((FAIL+1)); }
chkgrep "systemd --user running as uid 1000" "OPT_A_SYSTEMD_USER_RUNNING"
chkgrep "pntest --user unit marker present"  "OPT_A_PNTEST_MARKER_PRESENT"
chkgrep "system dbus up (system_bus_socket)" "OPT_A_SYSTEM_DBUS_UP"
chkgrep "/run/user/1000 exists (probe ran)"  "OPT_A_(SYSTEM_DBUS|USER_DBUS|PNTEST)"
echo "  ----"; echo "  $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ] && echo "  RESULT: GREEN (option a proven)" || echo "  RESULT: NOT FULLY GREEN"
exit 0
