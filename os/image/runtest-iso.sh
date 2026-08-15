#!/usr/bin/env bash
set -uo pipefail
OUT_DIR=${OUT_DIR:-/var/tmp/bbx}
ISO=${ISO:-$OUT_DIR/brainbox-installer-amd64.iso}
DISK=${DISK:-$OUT_DIR/iso-test-disk.raw}
DISK_GB=${DISK_GB:-12}
L1=${L1:-$OUT_DIR/iso-inst.log}
L2=${L2:-$OUT_DIR/iso-boot.log}
SSH_PORT=${SSH_PORT:-2225}; HTTP_PORT=${HTTP_PORT:-8085}; PORTAL_PORT=${PORTAL_PORT:-18085}
[ -r "$ISO" ] || { echo "ISO not found: $ISO"; exit 1; }
ACCEL="kvm:tcg"; [ -w /dev/kvm ] || ACCEL="tcg"

_belegt=""
for _p in "$SSH_PORT" "$HTTP_PORT" "$PORTAL_PORT"; do
  if ss -tln 2>/dev/null | grep -qE "127\.0\.0\.1:$_p |0\.0\.0\.0:$_p |\*:$_p "; then
    _belegt="$_belegt $_p"
  fi
done
if [ -n "$_belegt" ]; then
  echo "ABBRUCH: Port(s)$_belegt sind belegt — der Lauf wuerde als Fehlschlag enden, ohne die"
  echo "         Appliance je gestartet zu haben. Meist eine vergessene QEMU-Instanz:"
  echo "           pgrep -af '^qemu-system' ; kill <pid>"
  echo "         Oder andere Ports waehlen: SSH_PORT=… HTTP_PORT=… PORTAL_PORT=… $0"
  exit 3
fi

echo "### Phase 1: install from ISO onto a blank ${DISK_GB}G disk"
rm -f "$DISK"; truncate -s "${DISK_GB}G" "$DISK"; : > "$L1"
qemu-system-x86_64 -machine accel=$ACCEL -cpu max -m 2048 -smp 2 -no-reboot -display none \
  -drive file="$DISK",format=raw,if=virtio -cdrom "$ISO" -boot order=dc \
  -serial file:"$L1" 2>"${L1%.log}.qemu.err" &
Q=$!
INST_OK=0
for i in $(seq 1 300); do
  kill -0 "$Q" 2>/dev/null || break
  grep -q "INSTALLATION ABGESCHLOSSEN" "$L1" && { INST_OK=1; break; }
  grep -qE "brainbox: (no |dd )" "$L1" && break
  sleep 2
done
sleep 2; kill "$Q" 2>/dev/null; wait "$Q" 2>/dev/null
echo "--- installer serial tail ---"; tail -n 20 "$L1"
[ "$INST_OK" = 1 ] && echo "PHASE1: INSTALL OK" || { echo "PHASE1: INSTALL FAILED"; exit 1; }

echo "### Phase 1b: inject throwaway test key into the TEST DISK (never a release artifact)"
TESTKEY="$OUT_DIR/iso-test-key"
rm -f "$TESTKEY" "$TESTKEY.pub"; ssh-keygen -q -t ed25519 -N '' -f "$TESTKEY"
MNT_T="$(mktemp -d)"
LOOP="$(sudo losetup --show -fP "$DISK")"
sudo mount "${LOOP}p1" "$MNT_T"
sudo install -d -m 700 -o 1000 -g 1000 "$MNT_T/home/brainbox/.ssh"
sudo install -m 600 -o 1000 -g 1000 "$TESTKEY.pub" "$MNT_T/home/brainbox/.ssh/authorized_keys"
printf 'brainbox ALL=(ALL) NOPASSWD:ALL\n' | sudo tee "$MNT_T/etc/sudoers.d/zz-runtest" >/dev/null
sudo chmod 440 "$MNT_T/etc/sudoers.d/zz-runtest"
sudo umount "$MNT_T"; sudo losetup -d "$LOOP"; rmdir "$MNT_T"
echo "  test key injected (authorized_keys + zz-runtest sudoers, test disk only)"

echo "### Phase 2: boot the installed disk (no ISO)"
: > "$L2"
qemu-system-x86_64 -machine accel=$ACCEL -cpu max -m 2048 -smp 2 -no-reboot -display none \
  -drive file="$DISK",format=raw,if=virtio \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22,hostfwd=tcp:127.0.0.1:${HTTP_PORT}-:80,hostfwd=tcp:127.0.0.1:${PORTAL_PORT}-:8076 \
  -device virtio-net-pci,netdev=n0 -serial file:"$L2" 2>"${L2%.log}.qemu.err" &
Q=$!
SSH_OK=0; HTTP_OK=0; PORTAL_OK=0; SSHOUT=""
for i in $(seq 1 300); do
  kill -0 "$Q" 2>/dev/null || { echo "qemu exited early"; break; }
  [ "$HTTP_OK" = 0 ] && curl -fsS -m 3 "http://127.0.0.1:${HTTP_PORT}/" 2>/dev/null | grep -qi brainbox && { HTTP_OK=1; echo "wizard :80 reachable"; }
  if [ "$PORTAL_OK" = 0 ]; then c=$(curl -sk -o /dev/null -m 3 -w "%{http_code}" "https://127.0.0.1:${PORTAL_PORT}/" 2>/dev/null); case "$c" in 2??|3??|401|403) PORTAL_OK=1; echo "portal :8076 reachable http=$c";; esac; fi
  if [ "$SSH_OK" = 0 ]; then
    O="$(ssh -i "$TESTKEY" -o IdentitiesOnly=yes -p "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=3 brainbox@127.0.0.1 'echo BBX_OK; echo PID1=$(cat /proc/1/comm); pgrep -f engine/tools/pnd >/dev/null && echo PND; pgrep -f engine/tools/pn-llmd >/dev/null && echo LLMD; command -v claude >/dev/null && echo CLAUDE; df -h / | tail -1' 2>/dev/null)"
    echo "$O" | grep -q BBX_OK && { SSH_OK=1; SSHOUT="$O"; echo "=== SSH OK ==="; echo "$O"; }
  fi
  [ "$SSH_OK" = 1 ] && [ "$HTTP_OK" = 1 ] && [ "$PORTAL_OK" = 1 ] && break
  sleep 2
done

BANNER_OK=0; EXTRA=""
grep -Eqi "B R A I N B O X|brainbox\.local|Einrichten" "$L2" && BANNER_OK=1
if [ "$SSH_OK" = 1 ]; then
  EXTRA="$(ssh -i "$TESTKEY" -o IdentitiesOnly=yes -p "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 brainbox@127.0.0.1 'sh -s' <<'REMOTE' 2>/dev/null
getent hosts brainbox.local >/dev/null 2>&1 && echo MDNS
command -v qrencode >/dev/null 2>&1 && echo QRENCODE
sudo -n sh -c '
  ip link add nb0 type dummy 2>/dev/null
  printf "MODE=static\nIFACE=nb0\nIP=10.77.0.5\nPREFIX=24\nGATEWAY=10.77.0.1\nDNS=1.1.1.1\n" >/tmp/nb.conf
  /usr/local/sbin/brainbox-netcfg /tmp/nb.conf >/dev/null 2>&1
  ip -o -4 addr show nb0 | grep -q 10.77.0.5 && echo STATICIP
  ip link del nb0 2>/dev/null; rm -f /tmp/nb.conf
'
REMOTE
)"
  echo "--- extended probe (banner/mdns/static) ---"; echo "$EXTRA"
fi

sleep 2; kill "$Q" 2>/dev/null; wait "$Q" 2>/dev/null
echo "--- boot serial tail ---"; tail -n 15 "$L2"
echo; echo "==== ISO VERDICT ===="
p=0; f=0
ck(){ [ "$1" = 1 ] && { echo "  PASS  $2"; p=$((p+1)); } || { echo "  FAIL  $2"; f=$((f+1)); }; }
ck "$INST_OK" "installer wrote appliance to disk from ISO"
ck "$SSH_OK"  "installed appliance boots + SSH over LAN"
echo "$SSHOUT" | grep -q "PID1=pn-init" && ck 1 "PID1=pn-init" || ck 0 "PID1=pn-init"
echo "$SSHOUT" | grep -q PND && ck 1 "pnd running" || ck 0 "pnd running"
echo "$SSHOUT" | grep -q LLMD && ck 1 "pn-llmd running" || ck 0 "pn-llmd running"
echo "$SSHOUT" | grep -q CLAUDE && ck 1 "claude present" || ck 0 "claude present"
ck "$HTTP_OK" "setup wizard :80"
ck "$PORTAL_OK" "portal :8076 serving"
ck "$BANNER_OK" "console banner shows setup URL (no IP hunting)"
echo "$EXTRA" | grep -q MDNS     && ck 1 "brainbox.local resolves (mDNS)"         || ck 0 "brainbox.local resolves (mDNS)"
echo "$EXTRA" | grep -q STATICIP && ck 1 "static IP applies via brainbox-netcfg"  || ck 0 "static IP applies via brainbox-netcfg"
echo "$EXTRA" | grep -q QRENCODE && ck 1 "qrencode present (console QR)"          || ck 0 "qrencode present (console QR)"
grep -qE ":8077|port 8077" "$L2" && ck 0 "no stale :8077 in boot log" || ck 1 "no stale :8077 in boot log"
echo "  ----"; echo "  $p passed, $f failed"
[ "$f" = 0 ] && echo "  RESULT: GREEN" || echo "  RESULT: NOT FULLY GREEN"
exit 0
