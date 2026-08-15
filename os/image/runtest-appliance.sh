#!/usr/bin/env bash
set -uo pipefail
IMG=${IMG:-/var/tmp/bbx/brainbox-appliance-amd64.raw}
LOG=${LOG:-/var/tmp/bbx/boot.log}
SSHOUT=${SSHOUT:-/var/tmp/bbx/sshout.txt}
SECS=${SECS:-260}
SSH_PORT=${SSH_PORT:-2223}
HTTP_PORT=${HTTP_PORT:-8080}
PORTAL_PORT=${PORTAL_PORT:-18076}
PW=${PW:-brainbox}
[ -r "$IMG" ] || { echo "image not found: $IMG"; exit 1; }
ACCEL="kvm:tcg"; [ -w /dev/kvm ] || ACCEL="tcg"
: > "$LOG"; : > "$SSHOUT"
echo "booting SELF-CONTAINED disk accel=$ACCEL img=$IMG (timeout ${SECS}s)"
qemu-system-x86_64 \
  -machine accel=$ACCEL -cpu max -m 2048 -smp 2 -no-reboot -display none \
  -drive file="$IMG",format=raw,if=virtio \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:${SSH_PORT}-:22,hostfwd=tcp:127.0.0.1:${HTTP_PORT}-:80,hostfwd=tcp:127.0.0.1:${PORTAL_PORT}-:8076 \
  -device virtio-net-pci,netdev=n0 \
  -serial file:"$LOG" 2>"${LOG%.log}.qemu.err" &
QPID=$!

SSH_OK=0; HTTP_OK=0; PORTAL_HTTP=0
for i in $(seq 1 "$SECS"); do
  kill -0 "$QPID" 2>/dev/null || { echo "qemu exited early (see ${LOG%.log}.qemu.err)"; break; }
  if [ "$HTTP_OK" = 0 ] && curl -fsS -m 3 "http://127.0.0.1:${HTTP_PORT}/" 2>/dev/null | grep -qi brainbox; then
    HTTP_OK=1; echo "==== HTTP setup page reachable (:$HTTP_PORT) ===="
  fi
  if [ "$PORTAL_HTTP" = 0 ]; then
    code=$(curl -sk -o /dev/null -m 3 -w "%{http_code}" "https://127.0.0.1:${PORTAL_PORT}/" 2>/dev/null)
    case "$code" in
      2??|3??|401|403) PORTAL_HTTP=1; echo "==== PORTAL https reachable (:$PORTAL_PORT->8076) http=$code ====" ;;
    esac
  fi
  if [ "$SSH_OK" = 0 ] && command -v sshpass >/dev/null; then
    OUT="$(sshpass -p "$PW" ssh -p "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
           -o ConnectTimeout=3 -o PreferredAuthentications=password -o PubkeyAuthentication=no \
           brainbox@127.0.0.1 'echo BBX_SSH_OK; echo "PID1=$(cat /proc/1/comm)"; \
             echo "SYSTEMD_PROCS=$(ps -e -o comm= | grep -c "^systemd" || echo 0)"; \
             ip -br addr | tr "\n" "|"; echo; \
             (ss -tln 2>/dev/null||busybox netstat -tln 2>/dev/null)|grep -q ":80 " && echo WEB80_LISTEN; \
             P=$(python3 -c "import json;print(json.load(open(\"/home/brainbox/.config/brainbox-portal/config.json\"))[\"port\"])" 2>/dev/null); echo "PORTAL_PORT=$P"; \
             (ss -tln 2>/dev/null||busybox netstat -tln 2>/dev/null)|grep -q ":$P " && echo PORTAL_LISTEN; \
             [ -S /run/user/1000/pnd.sock ] && echo PND_SOCK_LIVE; \
             command -v claude >/dev/null && echo CLAUDE_CLI_PRESENT; \
             [ -x ~/brainarbeit/os/pn-vmm/target/release/pn-vmm ] && echo PNVMM_PRESENT; \
             [ -x ~/brainarbeit/components/phantom/target/release/phantom ] && echo PHANTOM_PRESENT; \
             echo "HOSTNAME=$(hostname 2>/dev/null)"; \
             for b in tmux ffmpeg espeak-ng mke2fs debugfs; do command -v $b >/dev/null && echo "BIN_OK_$b"; done; \
             [ -r /dev/kvm ] && [ -w /dev/kvm ] && echo KVM_RW_AS_SERVICE_USER; \
             cat /etc/brainbox/caps.env 2>/dev/null; \
             python3 -c "import cryptography,segno,requests" 2>/dev/null && echo PYDEPS_OK; \
             pgrep -f "engine/tools/pnd" >/dev/null && echo PND_PROC; \
             pgrep -f "engine/tools/pn-llmd" >/dev/null && echo LLMD_PROC' 2>/dev/null)"
    if echo "$OUT" | grep -q BBX_SSH_OK; then
      SSH_OK=1; echo "==== SSH LOGIN OK ===="; echo "$OUT"; echo "$OUT" > "$SSHOUT"
      [ "$HTTP_OK" = 1 ] && [ "$PORTAL_HTTP" = 1 ] && break
    fi
  fi
  sleep 1
done

sleep 2
kill "$QPID" 2>/dev/null; wait "$QPID" 2>/dev/null
echo; echo "==== SERIAL TAIL ===="; tail -n 20 "$LOG"
echo; echo "==== VERDICT (in-guest checks from the live SSH session) ===="
P=0; F=0
sctl(){ if grep -q "$2" "$SSHOUT" 2>/dev/null; then echo "  PASS  $1"; P=$((P+1)); else echo "  FAIL  $1"; F=$((F+1)); fi; }
serl(){ if grep -qE "$2" "$LOG" 2>/dev/null; then echo "  PASS  $1"; P=$((P+1)); else echo "  FAIL  $1"; F=$((F+1)); fi; }
serl "GRUB/kernel booted (pn-init banner)" "\[pn-init\]"
serl "F1 root remounted rw"                 "remounted / read-write|remounted rw"
serl "firstboot completed before portal"    "firstboot \(oneshot\) completed"
serl "F3 network/DHCP"                        "DHCP lease acquired|udhcpc\] applied|F3 net"
sctl "PID1 = pn-init, zero systemd"          "PID1=pn-init"
[ "$SSH_OK" = 1 ] && { echo "  PASS  SSH login over LAN"; P=$((P+1)); } || { echo "  FAIL  SSH login over LAN"; F=$((F+1)); }
[ "$HTTP_OK" = 1 ] && { echo "  PASS  web setup wizard reachable (:80)"; P=$((P+1)); } || { echo "  FAIL  web setup wizard reachable (:80)"; F=$((F+1)); }
sctl "python deps importable"                "PYDEPS_OK"
sctl "pnd running (job engine)"              "PND_PROC"
sctl "pn-llmd running (brain daemon)"        "LLMD_PROC"
[ "$PORTAL_HTTP" = 1 ] && { echo "  PASS  portal HTTPS reachable (serving)"; P=$((P+1)); } || { echo "  FAIL  portal HTTPS reachable"; F=$((F+1)); }
sctl "claude CLI present (brain backend)"    "CLAUDE_CLI_PRESENT"
sctl "pn-vmm shipped (cells)"                "PNVMM_PRESENT"
sctl "phantom shipped (Screen/seat)"         "PHANTOM_PRESENT"
sctl "hostname resolved (not '(none)')"      "HOSTNAME=brainbox"
sctl "tmux installed (cockpit/session lane)" "BIN_OK_tmux"
sctl "ffmpeg installed (cast lanes)"         "BIN_OK_ffmpeg"
sctl "espeak-ng installed (voice TTS)"       "BIN_OK_espeak-ng"
sctl "e2fsprogs installed (cell deltas)"     "BIN_OK_mke2fs"
sctl "/dev/kvm r+w as the service user"      "KVM_RW_AS_SERVICE_USER"
sctl "caps-detect enabled cells"             "CELLS_ENABLED=1"
echo "  ----"; echo "  $P passed, $F failed"
[ "$F" = 0 ] && echo "  RESULT: GREEN" || echo "  RESULT: NOT FULLY GREEN"
exit 0
