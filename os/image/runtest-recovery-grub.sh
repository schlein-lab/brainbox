#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
GEN="$HERE/mkosi.skeleton/etc/grub.d/40_brainarbeit_recovery"
[ -r "$GEN" ] || { echo "generator not found: $GEN"; exit 1; }

PASS=0; FAIL=0
v(){ if [ "$2" = ok ]; then echo "  PASS  $1"; PASS=$((PASS+1)); else echo "  FAIL  $1"; FAIL=$((FAIL+1)); fi; }

sh -n "$GEN" && v "generator is valid POSIX sh" ok || v "generator is valid POSIX sh" no

TMPD="$(mktemp -d)"; trap 'rm -rf "$TMPD"' EXIT
mkdir -p "$TMPD/boot"
: > "$TMPD/boot/vmlinuz-0.0.0-test"
: > "$TMPD/boot/initrd.img-0.0.0-test"
sed "s#/boot#$TMPD/boot#g" "$GEN" > "$TMPD/gen.sh"
CFG="$(sh "$TMPD/gen.sh")"
echo "---- generated recovery fragment ----"; echo "$CFG"; echo "-------------------------------------"

REC="$(printf '%s\n' "$CFG" | awk '/^menuentry/{n++} n==1{print} n==2{exit}')"

printf '%s' "$REC" | grep -q "recovery: pn-init minimal" \
    && v "recovery entry is titled 'pn-init minimal' (not 'stock systemd')" ok \
    || v "recovery entry is titled 'pn-init minimal' (not 'stock systemd')" no
printf '%s' "$REC" | grep -q "init=/usr/lib/brainarbeit/pn-init" \
    && v "recovery boots pn-init as PID1 (init= override present)" ok \
    || v "recovery boots pn-init as PID1 (init= override present)" no
printf '%s' "$REC" | grep -q "pn.recovery" \
    && v "recovery passes pn.recovery (minimal sacred-only bring-up)" ok \
    || v "recovery passes pn.recovery (minimal sacred-only bring-up)" no
printf '%s' "$REC" | grep -q 'root=LABEL=${rslot}' \
    && v "recovery selects slot from grubenv (\${rslot}, not hardcoded)" ok \
    || v "recovery selects slot from grubenv (\${rslot}, not hardcoded)" no
printf '%s' "$REC" | grep -q "prev_root_slot" \
    && v "recovery reads prev_root_slot grubenv var" ok \
    || v "recovery reads prev_root_slot grubenv var" no
printf '%s' "$REC" | grep -q "systemd" \
    && v "recovery contains NO 'systemd' (the appliance ships none)" no \
    || v "recovery contains NO 'systemd' (the appliance ships none)" ok
printf '%s' "$REC" | grep -q "rescue.target\|root=LABEL=root-a " \
    && v "recovery has no rescue.target / hardcoded root-a" no \
    || v "recovery has no rescue.target / hardcoded root-a" ok

echo "  ----"; echo "  $PASS passed, $FAIL failed"
if [ "$FAIL" = 0 ]; then
  echo "  RESULT: GREEN (recovery menuentry boots pn-init minimal, slot from grubenv, zero systemd)"
  echo "  NOTE: the live GRUB-boot proof (select entry -> pn-init PID1 + sacred sshd + 0 systemd)"
  echo "        is gated on the golden disk artifact + nested KVM — see runtest-image.sh / §9."
  exit 0
else
  echo "  RESULT: NOT GREEN"; exit 1
fi
