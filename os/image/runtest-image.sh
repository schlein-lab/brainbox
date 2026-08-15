#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${OUT:-$HERE/out}"
IMG="${IMG:-}"
if [ -z "$IMG" ]; then
  IMG="$(ls -1 "$OUT"/*.raw "$OUT"/brainarbeit "$OUT"/brainarbeit.img 2>/dev/null | head -1)"
fi
[ -n "$IMG" ] && [ -r "$IMG" ] || { echo "image artifact not found under $OUT (build it: ./build.sh)"; exit 1; }
SECS="${SECS:-240}"
SSH_PORT="${SSH_PORT:-2223}"
OVMF_CODE="${OVMF_CODE:-/usr/share/OVMF/OVMF_CODE_4M.fd}"
OVMF_VARS_SRC="${OVMF_VARS_SRC:-/usr/share/OVMF/OVMF_VARS_4M.fd}"
LOG="${LOG:-/var/tmp/pn-image.log}"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

cp "$IMG" "$WORK/disk.raw"
cp "$OVMF_VARS_SRC" "$WORK/OVMF_VARS.fd" 2>/dev/null || dd if=/dev/zero of="$WORK/OVMF_VARS.fd" bs=1M count=4 status=none

ACCEL="kvm:tcg"; [ -w /dev/kvm ] || ACCEL="tcg"
: > "$LOG"
echo "booting IMAGE artifact $IMG via UEFI/GRUB (accel=$ACCEL, timeout ${SECS}s)"
qemu-system-x86_64 \
  -machine q35,accel=$ACCEL -cpu max -m 2048 -smp 2 -no-reboot -display none \
  -drive if=pflash,format=raw,unit=0,readonly=on,file="$OVMF_CODE" \
  -drive if=pflash,format=raw,unit=1,file="$WORK/OVMF_VARS.fd" \
  -drive file="$WORK/disk.raw",format=raw,if=virtio \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22 -device virtio-net-pci,netdev=n0 \
  -serial file:"$LOG" 2>"${LOG%.log}.qemu.err" &
QPID=$!

echo "waiting for SSH on 127.0.0.1:${SSH_PORT} (image boots through GRUB+initrd, slower than -kernel)..."
SSH_OK=0; SYSD=""
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=3 -o PreferredAuthentications=password -o PubkeyAuthentication=no"
for i in $(seq 1 "$SECS"); do
  kill -0 "$QPID" 2>/dev/null || { echo "qemu exited early"; break; }
  if command -v sshpass >/dev/null 2>&1; then
    OUT2="$(sshpass -p "${PN_TEST_PW:-pntest}" ssh -p "$SSH_PORT" $SSH_OPTS pn@127.0.0.1 '
        echo PN_SSH_LOGIN_OK; echo "PID1=$(cat /proc/1/comm)"; id;
        echo "SYSTEMD_PROCS=$(ps -e -o comm= 2>/dev/null | grep -c -E "^systemd" || true)";
        echo "SYSTEMD_LIST=$(ps -e -o pid=,comm= 2>/dev/null | grep -E "systemd" || echo none)";
        ( ss -tln 2>/dev/null || busybox netstat -tln 2>/dev/null ) | grep -q ":22 " && echo LIVE_SSHD_22;
        ip -br addr 2>/dev/null' 2>/dev/null)"
    if echo "$OUT2" | grep -q PN_SSH_LOGIN_OK; then
      SSH_OK=1
      echo "==== SSH LOGIN SUCCEEDED ===="; echo "$OUT2"
      { echo "==== LIVE SSH-SESSION CHECKS ===="; echo "$OUT2"; } >> "$LOG"
      break
    fi
  fi
  sleep 1
done
sleep 3; kill "$QPID" 2>/dev/null; wait "$QPID" 2>/dev/null

echo; echo "==== SERIAL LOG ($LOG) — tail ===="; tail -n 50 "$LOG"
echo; echo "==== VERDICT ===="
PASS=0; FAIL=0
v(){ if [ "$2" = ok ]; then echo "  PASS  $1"; PASS=$((PASS+1)); else echo "  FAIL  $1"; FAIL=$((FAIL+1)); fi; }
grep -qiE "pn-init.*PID1 up|pn-init\] v2" "$LOG" && v "GRUB booted pn-init as PID1" ok || v "GRUB booted pn-init as PID1" no
[ "$SSH_OK" = 1 ] && v "SSH login over LAN (acceptance bar)" ok || v "SSH login over LAN (acceptance bar)" no
echo "$OUT2" 2>/dev/null | grep -q "PID1=pn-init" && v "/proc/1/comm == pn-init" ok || v "/proc/1/comm == pn-init" no
grep -qE "LIVE_SSHD_22" "$LOG" && v "sshd listening :22" ok || v "sshd listening :22" no
if echo "$OUT2" 2>/dev/null | grep -q "SYSTEMD_PROCS=0"; then v "ZERO systemd processes (we replaced it)" ok
else v "ZERO systemd processes (we replaced it)" no; echo "    -> $(echo "$OUT2" | grep SYSTEMD_LIST)"; fi
echo "  ----"; echo "  $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ] && echo "  RESULT: GREEN (image boots to full capability with NO systemd)" || echo "  RESULT: NOT FULLY GREEN"
exit 0
