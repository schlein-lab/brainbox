#!/usr/bin/env bash
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR=${OUT_DIR:-/var/tmp/bbx}

TARGET=""; CREDS=""; IMAGE=""
SSH_HOST=""; SSH_USER="brainbox"; SSH_PORT=22; SSH_PASS_FILE=""
WS_SECONDS=${WS_SECONDS:-30}
JSON_OUT=""; PAGES=(); EXTRA=()

usage(){ sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --target)        TARGET="$2"; shift 2 ;;
    --creds)         CREDS="$2"; shift 2 ;;
    --image)         IMAGE="$2"; shift 2 ;;
    --ssh-host)      SSH_HOST="$2"; shift 2 ;;
    --ssh-user)      SSH_USER="$2"; shift 2 ;;
    --ssh-port)      SSH_PORT="$2"; shift 2 ;;
    --ssh-pass-file) SSH_PASS_FILE="$2"; shift 2 ;;
    --ws-seconds)    WS_SECONDS="$2"; shift 2 ;;
    --json)          JSON_OUT="$2"; shift 2 ;;
    --page)          PAGES+=(--page "$2"); shift 2 ;;
    --skip-session)  EXTRA+=(--skip-session); shift ;;
    --post-probe)    EXTRA+=(--post-probe); shift ;;
    --nested)        EXTRA+=(--nested); shift ;;
    -h|--help)       usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage 1 ;;
  esac
done

command -v python3 >/dev/null || { echo "python3 required"; exit 2; }

run_suite(){
  local args=(--target "$1" --creds "$2" --ws-seconds "$WS_SECONDS")
  [ -n "$JSON_OUT" ] && args+=(--json "$JSON_OUT")
  [ -n "$SSH_HOST" ] && args+=(--ssh-host "$SSH_HOST" --ssh-user "$SSH_USER" --ssh-port "$SSH_PORT")
  [ -n "$SSH_PASS_FILE" ] && args+=(--ssh-pass-file "$SSH_PASS_FILE")
  [ -n "${GUEST_KEY:-}" ] && [ -r "${GUEST_KEY:-}" ] && args+=(--ssh-key "$GUEST_KEY")
  [ ${#PAGES[@]} -gt 0 ] && args+=("${PAGES[@]}")
  [ ${#EXTRA[@]} -gt 0 ] && args+=("${EXTRA[@]}")
  PYTHONPATH="$HERE" python3 -m acceptance "${args[@]}"
}

if [ -z "$IMAGE" ]; then
  [ -n "$TARGET" ] || { echo "--target or --image required"; usage 1; }
  [ -n "$CREDS" ] || { echo "--creds FILE required (credentials never go on argv)"; usage 1; }
  [ -r "$CREDS" ] || { echo "creds file not readable: $CREDS"; exit 2; }
  run_suite "$TARGET" "$CREDS"
  exit $?
fi

[ -r "$IMAGE" ] || { echo "image not found: $IMAGE"; exit 2; }
command -v qemu-system-x86_64 >/dev/null || { echo "qemu-system-x86_64 required"; exit 2; }

SSH_FWD=${SSH_FWD:-2229}
HTTP_FWD=${HTTP_FWD:-8089}
PORTAL_FWD=${PORTAL_FWD:-18089}
BOOT_SECS=${BOOT_SECS:-300}
MEM=${MEM:-6144}
CPUS=${CPUS:-2}
LOG=${LOG:-$OUT_DIR/acceptance-boot.log}
mkdir -p "$OUT_DIR"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/bbx-acceptance.XXXXXX")"
chmod 700 "$WORK"
CREDS_FILE="$WORK/creds.json"

GUEST_KEY="$WORK/guestkey"
ssh-keygen -q -t ed25519 -N '' -C "brainbox-acceptance" -f "$GUEST_KEY" </dev/null >/dev/null 2>&1 \
  || echo "### note: ssh-keygen failed — in-guest checks will SKIP"
GUEST_PUB="$(cat "$GUEST_KEY.pub" 2>/dev/null || true)"

QPID=""
cleanup(){
  [ -n "$QPID" ] && kill "$QPID" 2>/dev/null
  [ -n "$QPID" ] && wait "$QPID" 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

fmt="raw"; case "$IMAGE" in *.qcow2) fmt="qcow2";; esac
OVERLAY="$WORK/overlay.qcow2"
if command -v qemu-img >/dev/null; then
  qemu-img create -f qcow2 -F "$fmt" -b "$(readlink -f "$IMAGE")" "$OVERLAY" >/dev/null 2>&1 \
    && DRIVE=(-drive file="$OVERLAY",format=qcow2,if=virtio) \
    || DRIVE=(-drive file="$IMAGE",format=$fmt,if=virtio)
else
  DRIVE=(-drive file="$IMAGE",format=$fmt,if=virtio)
fi

ACCEL="kvm:tcg"; [ -w /dev/kvm ] || ACCEL="tcg"
: > "$LOG"
echo "### booting image accel=$ACCEL img=$IMAGE (wizard :$HTTP_FWD, portal :$PORTAL_FWD)"
qemu-system-x86_64 \
  -machine accel=$ACCEL -cpu max -m "$MEM" -smp "$CPUS" -no-reboot -display none \
  "${DRIVE[@]}" \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:${SSH_FWD}-:22,hostfwd=tcp:127.0.0.1:${HTTP_FWD}-:80,hostfwd=tcp:127.0.0.1:${PORTAL_FWD}-:8076 \
  -device virtio-net-pci,netdev=n0 \
  -serial file:"$LOG" 2>"${LOG%.log}.qemu.err" &
QPID=$!

UP=0
for i in $(seq 1 "$BOOT_SECS"); do
  kill -0 "$QPID" 2>/dev/null || { echo "qemu exited early (see ${LOG%.log}.qemu.err)"; break; }
  if curl -fsS -m 3 "http://127.0.0.1:${HTTP_FWD}/" 2>/dev/null | grep -qi brainbox; then
    UP=1; echo "### wizard reachable after ${i}s"; break
  fi
  sleep 1
done
[ "$UP" = 1 ] || { echo "### FAIL: wizard never came up"; tail -n 20 "$LOG"; exit 1; }

CLAIM_CODE=""
for _try in 1 2 3 4 5 6 7 8; do
  CLAIM_CODE=$(grep -a "Passwort / pass" "$LOG" 2>/dev/null | tail -1 | sed "s/.*: //" | tr -d "\r ")
  [ -n "$CLAIM_CODE" ] && break
  sleep 3
done
[ -n "$CLAIM_CODE" ] && echo "### Setup-Code vom Schirm gelesen (${#CLAIM_CODE} Zeichen)" \
                     || echo "### WARN: kein Setup-Code im seriellen Log -- Besitznachweis wird fehlschlagen (Befund)"

echo "### driving the first-run wizard"
PYTHONPATH="$HERE" python3 -m acceptance.wizard \
  --wizard-url "http://127.0.0.1:${HTTP_FWD}" \
  --creds-out "$CREDS_FILE" \
  --owner "${OWNER_NAME:-tester}" \
  --ssh-key "$GUEST_PUB" \
  --claim-code "$CLAIM_CODE" \
  --hostname "${BOX_HOSTNAME:-brainbox}" || { echo "### FAIL: wizard did not complete"; exit 1; }

echo "### waiting for the portal to come up after setup"
PORTAL="https://127.0.0.1:${PORTAL_FWD}"
POK=0
for i in $(seq 1 180); do
  kill -0 "$QPID" 2>/dev/null || { echo "qemu exited"; break; }
  code=$(curl -sk -o /dev/null -m 3 -w "%{http_code}" "$PORTAL/" 2>/dev/null)
  case "$code" in 2??|3??|401|403) POK=1; echo "### portal up (http=$code) after ${i}s"; break ;; esac
  sleep 1
done
[ "$POK" = 1 ] || { echo "### FAIL: portal never came up after setup"; tail -n 20 "$LOG"; exit 1; }

SSH_HOST="127.0.0.1"; SSH_PORT="$SSH_FWD"
GUEST_SSH=(-i "$GUEST_KEY" -o IdentitiesOnly=yes -o BatchMode=yes
           -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
           -o ConnectTimeout=8)
IN_GUEST=0; IN_GUEST_WHY="kein Schluessel erzeugt"
if [ -r "$GUEST_KEY" ]; then
  for _i in $(seq 1 20); do
    if ssh "${GUEST_SSH[@]}" -p "$SSH_FWD" brainbox@127.0.0.1 true 2>/dev/null; then
      IN_GUEST=1; IN_GUEST_WHY=""; break
    fi
    sleep 2
  done
  [ "$IN_GUEST" = 1 ] || IN_GUEST_WHY="Anmeldung mit dem im Assistenten hinterlegten Schluessel schlug fehl"
fi
if [ "$IN_GUEST" = 1 ]; then
  echo "### in-guest: Anmeldung mit dem im Assistenten hinterlegten Schluessel OK"
else
  echo "### in-guest: NICHT erreichbar ($IN_GUEST_WHY) — die Tore darin werden UEBERSPRUNGEN, nicht bewertet"
fi

case " ${EXTRA[*]-} " in *--post-probe*) ;; *) EXTRA+=(--post-probe) ;; esac
case " ${EXTRA[*]-} " in *--nested*) ;; *) EXTRA+=(--nested) ;; esac

run_suite "$PORTAL" "$CREDS_FILE"
rc=$?

if [ "$IN_GUEST" != 1 ] && [ -r "$HERE/acceptance/queue_load.py" ]; then
  echo "### queue-load gate: UEBERSPRUNGEN — $IN_GUEST_WHY"
elif [ -r "$HERE/acceptance/queue_load.py" ]; then
  echo "### queue-load governance gate (in-guest: scratch pnd + scratch cgroup tier)"
  if scp "${GUEST_SSH[@]}" -P "$SSH_FWD" \
        "$HERE/acceptance/queue_load.py" "brainbox@127.0.0.1:/tmp/queue_load.py" 2>/dev/null \
     && ssh "${GUEST_SSH[@]}" -p "$SSH_FWD" brainbox@127.0.0.1 \
        'python3 /tmp/queue_load.py; q=$?; rm -f /tmp/queue_load.py; exit $q'; then
    echo "### queue-load gate: GREEN"
  else
    echo "### queue-load gate: RED — the queue did NOT prove it governs work under load"
    rc=1
  fi
else
  echo "### note: queue-load governance gate SKIPPED (no in-guest SSH or gate file absent)"
fi

if [ "$IN_GUEST" != 1 ] && [ -r "$HERE/acceptance/admit_gate.py" ]; then
  echo "### admission-plane gate: UEBERSPRUNGEN — $IN_GUEST_WHY"
elif [ -r "$HERE/acceptance/admit_gate.py" ]; then
  echo "### admission-plane gate (in-guest: scratch pn-llmd — exec/act admission + AIMD)"
  if scp "${GUEST_SSH[@]}" -P "$SSH_FWD" \
        "$HERE/acceptance/admit_gate.py" "brainbox@127.0.0.1:/tmp/admit_gate.py" 2>/dev/null \
     && ssh "${GUEST_SSH[@]}" -p "$SSH_FWD" brainbox@127.0.0.1 \
        'python3 /tmp/admit_gate.py; q=$?; rm -f /tmp/admit_gate.py; exit $q'; then
    echo "### admission-plane gate: GREEN"
  else
    echo "### admission-plane gate: RED — exec/act admission or the adaptive contingent is broken"
    rc=1
  fi
else
  echo "### note: admission-plane gate SKIPPED (no in-guest SSH or gate file absent)"
fi

echo "### serial tail ###"; tail -n 15 "$LOG"
exit $rc
