#!/bin/bash
set -u
PNK=/home/ubuntu/brainarbeit/os/pn-vmm/kernel
BROOT=/home/ubuntu/kbuild
KVER=6.1
SRC=linux-$KVER
mark(){ echo "@@@ $* @@@ ($(date +%H:%M:%S))"; }

mkdir -p "$BROOT" "$PNK"; cd "$BROOT" || exit 2

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
  "https://raw.githubusercontent.com/firecracker-microvm/firecracker/main/resources/guest_configs/microvm-kernel-ci-aarch64-6.1.config" \
  "https://raw.githubusercontent.com/firecracker-microvm/firecracker/firecracker-v1.7/resources/guest_configs/microvm-kernel-ci-aarch64-6.1.config" ; do
  if curl -fsSL "$url" -o .config ; then mark "FC_CONFIG_OK $url"; CFG_OK=1; break; fi
done
if [ "$CFG_OK" = 0 ]; then
  mark "FC_CONFIG_MISSING -> defconfig + kvm_guest.config"
  make ARCH=arm64 defconfig >/dev/null 2>&1
  make ARCH=arm64 kvm_guest.config >/dev/null 2>&1 || true
fi

mark "FORCE OPTIONS"
for opt in VIRTIO VIRTIO_MMIO VIRTIO_MMIO_CMDLINE_DEVICES VIRTIO_BLK VSOCKETS VIRTIO_VSOCKETS \
           HW_RANDOM HW_RANDOM_VIRTIO RANDOM_TRUST_CPU OVERLAY_FS EXT4_FS DEVTMPFS DEVTMPFS_MOUNT \
           SERIAL_8250 SERIAL_8250_CONSOLE SERIAL_OF_PLATFORM SERIAL_EARLYCON \
           BLK_DEV_INITRD BINFMT_ELF BINFMT_SCRIPT PRINTK TTY BLK_DEV \
           SECCOMP SECCOMP_FILTER TMPFS TMPFS_POSIX_ACL TMPFS_XATTR \
           NET UNIX INET IPV6 PACKET NAMESPACES PID_NS UTS_NS IPC_NS USER_NS NET_NS \
           SYSVIPC POSIX_MQUEUE EPOLL EVENTFD SIGNALFD TIMERFD FUTEX INOTIFY_USER \
           PROC_FS PROC_SYSCTL SYSFS ; do
  scripts/config --enable "$opt"
done
for opt in RANDOMIZE_BASE WERROR MODULE_SIG DEBUG_INFO DEBUG_INFO_DWARF5 DEBUG_INFO_DWARF4 ; do
  scripts/config --disable "$opt"
done
scripts/config --enable DEBUG_INFO_NONE 2>/dev/null || true
make ARCH=arm64 olddefconfig >/dev/null 2>&1

mark "VERIFY CONFIG"
grep -E "CONFIG_(VIRTIO_MMIO|HW_RANDOM_VIRTIO|OVERLAY_FS|VIRTIO_VSOCKETS|VSOCKETS|VIRTIO_BLK|EXT4_FS|SERIAL_8250_CONSOLE|SERIAL_OF_PLATFORM|SECCOMP|UNIX|INET|RANDOMIZE_BASE)=" .config || true
echo "--- (RANDOMIZE_BASE not present above = KASLR off, good) ---"

mark "BUILD Image -j4 (nice/ionice)"
nice -n 10 ionice -c3 make ARCH=arm64 -j4 Image > build.out 2>&1; RC=$?
echo "--- last 30 lines of build.out ---"; tail -30 build.out
if [ $RC -eq 0 ] && [ -f arch/arm64/boot/Image ]; then
  cp arch/arm64/boot/Image "$PNK/vmlinux-rng.bin"
  cp arch/arm64/boot/Image "$PNK/vmlinux.bin"
  mark "BUILD_OK -> vmlinux-rng.bin + vmlinux.bin ($(stat -c%s arch/arm64/boot/Image) bytes)"
  file "$PNK/vmlinux-rng.bin"
else
  mark "BUILD_FAIL rc=$RC"
fi
mark "ALL_DONE"
