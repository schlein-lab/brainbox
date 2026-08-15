#!/bin/bash
set -u
PNK="${PNK:-$(cd "$(dirname "$0")" && pwd)}"
BROOT="${BROOT:-$HOME/kbuild}"
KVER=6.1
SRC=linux-$KVER
mark(){ echo "@@@ $* @@@ ($(date +%H:%M:%S))"; }

mkdir -p "$BROOT"; cd "$BROOT" || exit 2

if [ ! -d "$SRC" ]; then
  mark "DOWNLOAD linux-$KVER"
  curl -fsSL "https://cdn.kernel.org/pub/linux/kernel/v6.x/$SRC.tar.xz" -o "$SRC.tar.xz" || { mark "DOWNLOAD_FAIL"; exit 3; }
  mark "EXTRACT"
  tar xf "$SRC.tar.xz" || { mark "EXTRACT_FAIL"; exit 4; }
fi
cd "$SRC" || exit 5

mark "BASE CONFIG"
CFG_OK=0
for url in \
  "https://raw.githubusercontent.com/firecracker-microvm/firecracker/main/resources/guest_configs/microvm-kernel-ci-x86_64-6.1.config" \
  "https://raw.githubusercontent.com/firecracker-microvm/firecracker/firecracker-v1.7/resources/guest_configs/microvm-kernel-ci-x86_64-6.1.config" ; do
  if curl -fsSL "$url" -o .config ; then mark "FC_CONFIG_OK $url"; CFG_OK=1; break; fi
done
if [ "$CFG_OK" = 0 ]; then
  mark "FC_CONFIG_MISSING -> defconfig + kvm_guest.config"
  make defconfig >/dev/null 2>&1
  make kvm_guest.config >/dev/null 2>&1 || true
fi

mark "FORCE OPTIONS"
for opt in VIRTIO VIRTIO_MMIO VIRTIO_MMIO_CMDLINE_DEVICES VIRTIO_BLK VSOCKETS VIRTIO_VSOCKETS \
           HW_RANDOM HW_RANDOM_VIRTIO RANDOM_TRUST_CPU OVERLAY_FS EXT4_FS DEVTMPFS \
           SERIAL_8250 SERIAL_8250_CONSOLE BLK_DEV_INITRD BINFMT_ELF BINFMT_SCRIPT PRINTK TTY BLK_DEV \
           X86_MPPARSE X86_LOCAL_APIC X86_IO_APIC ; do
  scripts/config --enable "$opt"
done
for opt in RANDOMIZE_BASE RANDOMIZE_MEMORY WERROR MODULE_SIG DEBUG_INFO ; do
  scripts/config --disable "$opt"
done
make olddefconfig >/dev/null 2>&1

mark "VERIFY CONFIG"
grep -E "CONFIG_(VIRTIO_MMIO_CMDLINE_DEVICES|HW_RANDOM_VIRTIO|RANDOM_TRUST_CPU|OVERLAY_FS|VIRTIO_VSOCKETS|VIRTIO_BLK|EXT4_FS|RANDOMIZE_BASE)=" .config || true
echo "--- (RANDOMIZE_BASE not present above = KASLR off, good) ---"

mark "BUILD vmlinux -j6"
make -j6 vmlinux > build.out 2>&1; RC=$?
echo "--- last 30 lines of build.out ---"; tail -30 build.out
if [ $RC -eq 0 ] && [ -f vmlinux ]; then
  cp vmlinux "$PNK/vmlinux-rng.bin"
  mark "BUILD_OK -> vmlinux-rng.bin ($(stat -c%s vmlinux) bytes)"
  file "$PNK/vmlinux-rng.bin"
else
  mark "BUILD_FAIL rc=$RC"
fi
mark "ALL_DONE"
