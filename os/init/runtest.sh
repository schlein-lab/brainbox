#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
KERNEL="${KERNEL:-/tmp/pn-vmlinuz}"
INITRD="${INITRD:-/tmp/pn-init.cpio.gz}"
LOG="${LOG:-/tmp/pn-boot.log}"
SECS="${SECS:-60}"
MODE="${MODE:-normal}"
STATE="${STATE:-/tmp/pn-state.img}"
SWAP="${SWAP:-/tmp/pn-swap.img}"
MEM="${MEM:-768}"
SMP="${SMP:-2}"
[ -r "$KERNEL" ] || { echo "kernel not readable: $KERNEL"; exit 1; }
[ -r "$INITRD" ] || { echo "initrd not found: $INITRD (run mkinitramfs.sh)"; exit 1; }

APPEND="console=ttyS0 panic=-1 init=/init"
EXTRA=(-no-reboot)
DISK=()

attach_swap() {
  dd if=/dev/zero of="$SWAP" bs=1M count="${1:-256}" status=none
  mkswap "$SWAP" >/dev/null 2>&1 || true
  DISK=(-drive file="$SWAP",format=raw,if=virtio)
  APPEND="$APPEND pn.swapdev=/dev/vda"
}

case "$MODE" in
  poison)    APPEND="$APPEND pn.poisonpnd=1" ;;
  churn)     APPEND="$APPEND pn.churn" ;;
  sigusr1)   APPEND="$APPEND pn.sig=usr1" ;;
  sigterm)   APPEND="$APPEND pn.sig=term" ;;
  cgdump)    APPEND="$APPEND pn.cgdump pn.fakeudevd" ;;
  crashloop) APPEND="$APPEND pn.poisonpnd=1 pn.crashmax=2"
             dd if=/dev/zero of="$STATE" bs=1M count=1 status=none
             DISK=(-drive file="$STATE",format=raw,if=virtio)
             EXTRA=()
             ;;
  caps)           attach_swap 256; APPEND="$APPEND pn.cgdump" ;;
  caps-overcommit)APPEND="$APPEND pn.crit_max=95 pn.cgdump" ;;
  smallbox)       MEM=120; APPEND="$APPEND pn.cgdump" ;;
  smallbox-strict)MEM=120; APPEND="$APPEND pn.require_caps pn.cgdump" ;;
  nomemctl)       APPEND="$APPEND pn.nomemctl pn.cgdump" ;;
  nomemtotal)     APPEND="$APPEND pn.nomemtotal pn.cgdump" ;;
  nocg)           APPEND="$APPEND pn.nocg pn.cgdump" ;;
  allow-uncapped) APPEND="$APPEND pn.nomemctl pn.allow_uncapped pn.cgdump" ;;
  miscstorm)      APPEND="$APPEND pn.miscstorm pn.misc_high=99 pn.cgdump pn.stormwatch" ;;
  critstorm)      APPEND="$APPEND pn.critstorm pn.crit_max=60 pn.cgdump pn.stormwatch" ;;
  bothstorm)      APPEND="$APPEND pn.bothstorm pn.misc_high=99 pn.batch_high=99 pn.cgdump pn.stormwatch" ;;
  cpustorm)       APPEND="$APPEND pn.cpustorm pn.schedcheck pn.petlog pn.cgdump pn.stormwatch" ;;
  critcpustorm)   SMP=1; APPEND="$APPEND pn.critcpustorm pn.schedcheck pn.petlog pn.cgdump" ;;
  miscstorm-swap) attach_swap 256; APPEND="$APPEND pn.miscstorm pn.cgdump pn.stormwatch" ;;
  bothstorm-swap) attach_swap 256; APPEND="$APPEND pn.bothstorm pn.cgdump pn.stormwatch" ;;
esac

: > "$LOG"
echo "booting kernel=$KERNEL mode=$MODE mem=${MEM} smp=${SMP} (timeout ${SECS}s)"
timeout "$SECS" qemu-system-x86_64 \
  -machine accel=kvm:tcg -cpu max -m "$MEM" -smp "$SMP" "${EXTRA[@]}" -display none \
  -device i6300esb,id=wd0 -action watchdog=reset \
  "${DISK[@]}" \
  -kernel "$KERNEL" -initrd "$INITRD" \
  -append "$APPEND" \
  -serial file:"$LOG" 2>"${LOG%.log}.qemu.err" || true
echo "=== serial log ($LOG) ==="
cat "$LOG"
