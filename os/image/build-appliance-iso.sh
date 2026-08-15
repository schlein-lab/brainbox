#!/usr/bin/env bash
set -euo pipefail
OUT_DIR=${OUT_DIR:-/var/tmp/bbx}
QCOW=${QCOW:-$OUT_DIR/brainbox-appliance-amd64.qcow2}
PAYLOAD_XZ=${PAYLOAD_XZ:-$OUT_DIR/brainbox-appliance-amd64.raw.xz}
ISO_OUT=${ISO_OUT:-$OUT_DIR/brainbox-installer-amd64.iso}
HERE="$(cd "$(dirname "$0")" && pwd)"
ASSETS=${ASSETS:-$HERE/iso-installer}
WORK=$OUT_DIR/iso-root
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
for t in qemu-img losetup grub-mkrescue xorriso; do command -v "$t" >/dev/null || { echo "missing: $t"; exit 1; }; done
[ -r "$QCOW" ] || { echo "qcow missing: $QCOW"; exit 1; }
RAW_SRC=${RAW_SRC:-$OUT_DIR/brainbox-appliance-amd64.raw}
if [ ! -r "$PAYLOAD_XZ" ] || { [ -r "$RAW_SRC" ] && [ "$RAW_SRC" -nt "$PAYLOAD_XZ" ]; }; then
  [ -r "$RAW_SRC" ] || { echo "payload source raw missing: $RAW_SRC (run build-appliance-disk.sh first)"; exit 1; }
  echo "==== [iso] 0. (re)compress payload from current raw ===="
  nice -n 19 ionice -c3 xz -T2 -3 -c "$RAW_SRC" > "$PAYLOAD_XZ.tmp" && mv "$PAYLOAD_XZ.tmp" "$PAYLOAD_XZ"
fi
[ -r "$PAYLOAD_XZ" ] || { echo "payload raw.xz missing: $PAYLOAD_XZ"; exit 1; }
[ -r "$ASSETS/scripts-brainbox" ] || { echo "missing $ASSETS/scripts-brainbox"; exit 1; }
[ -r "$ASSETS/hook-brainbox" ] || { echo "missing $ASSETS/hook-brainbox"; exit 1; }

say(){ echo "==== [iso] $* ===="; }
TMPRAW=$OUT_DIR/iso-chroot.raw
MNT="$(mktemp -d)"; LOOP=""
cleanup(){ set +e; umount -R "$MNT" 2>/dev/null; [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null; rmdir "$MNT" 2>/dev/null; rm -f "$TMPRAW"; }
trap cleanup EXIT

say "1. throwaway rw copy for the initramfs chroot"
rm -f "$TMPRAW"; qemu-img convert -O raw "$QCOW" "$TMPRAW"
LOOP="$(losetup --show -fP "$TMPRAW")"; PART="${LOOP}p1"; [ -e "$PART" ] || PART="${LOOP}1"
mount "$PART" "$MNT"
mount -t proc proc "$MNT/proc"; mount -t sysfs sysfs "$MNT/sys"
mount --bind /dev "$MNT/dev"; mount --bind /dev/pts "$MNT/dev/pts"
cp /etc/resolv.conf "$MNT/etc/resolv.conf" 2>/dev/null || true
KVER="$(ls "$MNT/lib/modules" | sort -V | tail -1)"
[ -n "$KVER" ] || { echo "no kernel modules in appliance"; exit 1; }
echo "  appliance kernel: $KVER"

say "2. build the installer initramfs (MODULES=most + installer boot script)"
chroot "$MNT" sh -c 'command -v xz >/dev/null || (apt-get update && apt-get install -y --no-install-recommends xz-utils)' >/tmp/iso-xz.log 2>&1 || echo "  (xz-utils install skipped/failed — check /tmp/iso-xz.log)"
chroot "$MNT" sh -c 'command -v growpart >/dev/null || apt-get install -y --no-install-recommends cloud-guest-utils' >>/tmp/iso-xz.log 2>&1 || true
install -m0755 "$ASSETS/scripts-brainbox" "$MNT/etc/initramfs-tools/scripts/brainbox"
install -m0755 "$ASSETS/hook-brainbox"    "$MNT/etc/initramfs-tools/hooks/zz-brainbox"
RAW_BYTES="$(xz --robot -l "$PAYLOAD_XZ" | awk '$1=="totals"{print $5}')"
case "$RAW_BYTES" in ''|*[!0-9]*) echo "cannot determine uncompressed size of $PAYLOAD_XZ"; exit 1;; esac
sed -i "s/^RAW_BYTES=.*/RAW_BYTES=$RAW_BYTES/" "$MNT/etc/initramfs-tools/scripts/brainbox"
grep -q "^RAW_BYTES=$RAW_BYTES\$" "$MNT/etc/initramfs-tools/scripts/brainbox" \
  || { echo "RAW_BYTES injection into installer script failed"; exit 1; }
echo "  payload uncompressed: $RAW_BYTES bytes"
if grep -q '^MODULES=' "$MNT/etc/initramfs-tools/initramfs.conf"; then
  sed -i 's/^MODULES=.*/MODULES=most/' "$MNT/etc/initramfs-tools/initramfs.conf"
else echo 'MODULES=most' >> "$MNT/etc/initramfs-tools/initramfs.conf"; fi
printf '\nisofs\nsr_mod\ncdrom\nvirtio_blk\nvirtio_scsi\nahci\nnvme\nsd_mod\n' >> "$MNT/etc/initramfs-tools/modules"
chroot "$MNT" update-initramfs -c -k "$KVER" >/tmp/iso-initramfs.log 2>&1 \
  || chroot "$MNT" update-initramfs -u -k "$KVER" >>/tmp/iso-initramfs.log 2>&1 \
  || { echo "initramfs build FAILED"; tail -25 /tmp/iso-initramfs.log; exit 1; }
[ -f "$MNT/boot/initrd.img-$KVER" ] || { echo "no initrd produced"; ls "$MNT/boot"; exit 1; }

say "3. assemble ISO tree (kernel + installer initrd + payload + grub.cfg)"
rm -rf "$WORK"; mkdir -p "$WORK/live" "$WORK/boot/grub"
cp "$MNT/boot/vmlinuz-$KVER"    "$WORK/live/vmlinuz"
cp "$MNT/boot/initrd.img-$KVER" "$WORK/live/initrd.img"
umount -R "$MNT" 2>/dev/null || true; losetup -d "$LOOP" 2>/dev/null || true; LOOP=""; rm -f "$TMPRAW"
cp "$PAYLOAD_XZ" "$WORK/live/brainbox.raw.xz"
cat > "$WORK/boot/grub/grub.cfg" <<EOF
set timeout=8
set default=0
serial --unit=0 --speed=115200
terminal_input console serial
terminal_output console serial
menuentry "Brainbox installieren  /  Install Brainbox  (leere Platte)" {
    echo ''
    echo '  Brainbox wird geladen - bitte warten.'
    echo '  Loading Brainbox - please wait.'
    echo ''
    linux /live/vmlinuz boot=brainbox console=tty0 console=ttyS0,115200
    echo '  Installer wird geladen (64 MB). Der Bildschirm bleibt jetzt etwa'
    echo '  20 bis 60 Sekunden dunkel. Das ist NORMAL - nichts ist abgestuerzt.'
    echo '  The screen stays dark for 20-60 s while the installer loads.'
    echo '  This is normal, nothing has crashed.'
    initrd /live/initrd.img
}
menuentry "Von der Festplatte starten  /  Boot from hard disk (already installed)" {
    echo ''
    echo '  Die bereits installierte Brainbox wird gestartet.'
    echo '  Starting the Brainbox already installed on this machine.'
    echo ''
    insmod part_msdos
    insmod part_gpt
    insmod chain
    set root=(hd0)
    chainloader +1
    boot
    echo ''
    echo '  Das hat nicht geklappt (UEFI-Rechner starten nicht auf diesem Weg).'
    echo '  Bitte die Maschine ausschalten, das ISO / CD-Laufwerk entfernen und'
    echo '  neu starten - dann startet die Box von ihrer Festplatte.'
    echo '  Please power off, remove the ISO, and start again.'
    sleep --interruptible 3600
}
EOF

bash "$HERE/make-startkarte.sh" "$WORK" iso

say "4. grub-mkrescue -> hybrid ISO (BIOS + UEFI)"
grub-mkrescue --volid BRAINBOX -o "$ISO_OUT" "$WORK" >/tmp/iso-mkrescue.log 2>&1 \
  || { echo "grub-mkrescue FAILED"; tail -25 /tmp/iso-mkrescue.log; exit 1; }
trap - EXIT; rmdir "$MNT" 2>/dev/null || true
echo "ISO: $ISO_OUT ($(du -h "$ISO_OUT"|cut -f1))"
say "ISO BUILD DONE"
