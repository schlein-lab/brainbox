#!/bin/sh
set -u

ARB=${TMPDIR:-/tmp}
LOG="$ARB/pninit-probeboot.log"
ERR="$ARB/pninit-probeboot.qemu.err"
IMG=${IMG:-/var/tmp/pn-rootfs.img}
KERNEL=${KERNEL:-/var/tmp/probe-vmlinuz}
SECS=${SECS:-100}

echo "Kennung : $(id -un)/$(id -u)"
echo "Arbeits : $ARB"
: > "$LOG" 2>/dev/null || { echo "⛔ kann $LOG nicht anlegen — der Lauf waere blind. Abbruch."; exit 1; }
: > "$ERR" 2>/dev/null || { echo "⛔ kann $ERR nicht anlegen. Abbruch."; exit 1; }
[ -r "$IMG" ]    || { echo "⛔ Abbild $IMG nicht lesbar. Abbruch."; exit 1; }
[ -r "$KERNEL" ] || { echo "⛔ Kernel $KERNEL nicht lesbar. Abbruch."; exit 1; }

ACCEL="kvm:tcg"; [ -w /dev/kvm ] || ACCEL="tcg"
echo "Beschleunigung: $ACCEL"

qemu-system-x86_64 \
  -machine accel=$ACCEL -cpu max -m 1024 -smp 2 -no-reboot -display none -snapshot \
  -kernel "$KERNEL" \
  -drive file="$IMG",format=raw,if=virtio \
  -netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
  -append "root=/dev/vda ro console=ttyS0 panic=-1 init=/sbin/pn-init pn.fullsystem pn.netif=enp0s2" \
  -serial file:"$LOG" 2>"$ERR" &
QPID=$!

I=0
while [ "$I" -lt "$SECS" ]; do
  kill -0 "$QPID" 2>/dev/null || break
  grep -q "user-session] up as uid=1000" "$LOG" 2>/dev/null && break
  I=$((I + 1)); sleep 1
done
sleep 3
kill "$QPID" 2>/dev/null; wait "$QPID" 2>/dev/null

echo
echo "==== stammt das Urteil aus DIESEM Lauf? ===="
ZEILEN=$(wc -l < "$LOG" 2>/dev/null || echo 0)
echo "  $LOG — $ZEILEN Zeilen"
if [ "$ZEILEN" -lt 5 ]; then
  echo "  ⛔ Das Protokoll ist praktisch leer — QEMU hat nichts geschrieben, das Urteil waere wertlos."
  echo "  qemu-Fehler:"; sed "s/^/    /" "$ERR" 2>/dev/null | head -8
  exit 1
fi
echo "  ✔ frisch geschrieben (die Datei wurde zu Beginn dieses Laufs geleert)"

echo
echo "==== was das neue PID 1 beim Hochfahren getan hat ===="
grep -E "pn-init\] (cgroup|full-system|F2|F3|F4|user-session)" "$LOG" | head -20 | sed "s/^/  /"

echo
echo "==== URTEIL ===="
P=0; F=0
pruef() {
  if grep -qE "$2" "$LOG"; then echo "  PASS  $1"; P=$((P + 1)); else echo "  FAIL  $1"; F=$((F + 1)); fi
}
pruef "PID 1 uebernommen"            "pn-init\]"
pruef "cgroup-Stufen angelegt"       "cgroup: tiers ready"
pruef "Deckel geschrieben"           "cgroup: caps written"
pruef "Nie-OOM-Invariante haelt"     "INVARIANT OK"
pruef "Durchsetzung bestaetigt"      "enforcement CONFIRMED active"
pruef "F1 root rw"                   "remounted / read-write|re-mounted .* r/w"
pruef "F1 Auslagerung an"            "swapon /swap.img"
pruef "F2 Geraete gefunden"          "F2 (udev|dev):"
pruef "F3 Netz/Adresse"              "DHCP lease acquired|F3 net:"
pruef "F4 Nutzersitzung uid 1000"    "user-session\] up as uid=1000"
echo "  ----"
echo "  $P bestanden, $F durchgefallen"
[ "$F" = 0 ] && echo "  ERGEBNIS: GRUEN — dieses PID 1 bootet." || echo "  ERGEBNIS: NICHT GRUEN"
rm -f "$LOG" "$ERR"
exit "$F"
