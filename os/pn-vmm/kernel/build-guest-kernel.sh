#!/bin/bash
set -u
PNK="$(cd "$(dirname "$0")" && pwd)"
BROOT="${BROOT:-$HOME/kbuild}"
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 4)}"
KVER=6.1
SRC="linux-$KVER"
mark(){ echo "@@@ $* @@@ ($(date +%H:%M:%S))"; }

TARGET="${1:-$(uname -m)}"
case "$TARGET" in
  x86_64|amd64)        ARCHNAME=x86_64;  KARCH=x86_64; FC=x86_64;  IMGREL=vmlinux ;;
  aarch64|arm64)       ARCHNAME=aarch64; KARCH=arm64;  FC=aarch64; IMGREL=arch/arm64/boot/Image ;;
  *) echo "unknown target arch: $TARGET (use x86_64 or aarch64)"; exit 2 ;;
esac

XC=""
if [ "$KARCH" = arm64 ] && [ "$(uname -m)" != "aarch64" ]; then
  XC="${CROSS_COMPILE:-aarch64-linux-gnu-}"
  command -v "${XC}gcc" >/dev/null 2>&1 || {
    echo "FATAL: cross toolchain ${XC}gcc not found (apt-get install gcc-aarch64-linux-gnu)"; exit 3; }
fi
MAKE=(make ARCH="$KARCH")
[ -n "$XC" ] && MAKE+=(CROSS_COMPILE="$XC")

mkdir -p "$BROOT" || exit 2; cd "$BROOT" || exit 2

if [ ! -d "$SRC" ]; then
  mark "DOWNLOAD linux-$KVER"
  curl -fsSL "https://cdn.kernel.org/pub/linux/kernel/v6.x/$SRC.tar.xz" -o "$SRC.tar.xz" \
    || { mark "DOWNLOAD_FAIL"; exit 4; }
  mark "EXTRACT"; tar xf "$SRC.tar.xz" || { mark "EXTRACT_FAIL"; exit 5; }
fi
cd "$SRC" || exit 6

mark "BASE CONFIG ($ARCHNAME)"
SNAP="$PNK/config-$ARCHNAME-$KVER"
if [ -f "$SNAP" ]; then
  cp "$SNAP" .config
  mark "USING SNAPSHOT $SNAP (exact reproduction)"
else
  CFG_OK=0
  for url in \
    "https://raw.githubusercontent.com/firecracker-microvm/firecracker/main/resources/guest_configs/microvm-kernel-ci-$FC-$KVER.config" \
    "https://raw.githubusercontent.com/firecracker-microvm/firecracker/firecracker-v1.7/resources/guest_configs/microvm-kernel-ci-$FC-$KVER.config" ; do
    if curl -fsSL "$url" -o .config ; then mark "FC_CONFIG_OK $url"; CFG_OK=1; break; fi
  done
  if [ "$CFG_OK" = 0 ]; then
    mark "FC_CONFIG_MISSING -> defconfig + kvm_guest.config"
    "${MAKE[@]}" defconfig      >/dev/null 2>&1
    "${MAKE[@]}" kvm_guest.config >/dev/null 2>&1 || true
  fi
fi

mark "FORCE OPTIONS"
COMMON="VIRTIO VIRTIO_MMIO VIRTIO_MMIO_CMDLINE_DEVICES VIRTIO_BLK VIRTIO_NET \
        VSOCKETS VIRTIO_VSOCKETS HW_RANDOM HW_RANDOM_VIRTIO RANDOM_TRUST_CPU \
        OVERLAY_FS EXT4_FS DEVTMPFS DEVTMPFS_MOUNT TMPFS TMPFS_POSIX_ACL TMPFS_XATTR \
        BLK_DEV_INITRD BINFMT_ELF BINFMT_SCRIPT PRINTK TTY BLK_DEV \
        SECCOMP SECCOMP_FILTER \
        NET UNIX INET IPV6 PACKET NAMESPACES PID_NS UTS_NS IPC_NS USER_NS NET_NS \
        SYSVIPC EPOLL EVENTFD SIGNALFD TIMERFD FUTEX INOTIFY_USER \
        PROC_FS PROC_SYSCTL SYSFS"
X86_ONLY="SERIAL_8250 SERIAL_8250_CONSOLE X86_MPPARSE X86_LOCAL_APIC X86_IO_APIC \
          HYPERVISOR_GUEST KVM_GUEST"
ARM_ONLY="SERIAL_8250 SERIAL_8250_CONSOLE SERIAL_OF_PLATFORM SERIAL_EARLYCON \
          OF ARM_GIC PCI"
if [ "$KARCH" = x86_64 ]; then ENABLE="$COMMON $X86_ONLY"; else ENABLE="$COMMON $ARM_ONLY"; fi
for opt in $ENABLE; do scripts/config --enable "$opt"; done
for opt in RANDOMIZE_BASE RANDOMIZE_MEMORY WERROR MODULE_SIG \
           DEBUG_INFO DEBUG_INFO_DWARF5 DEBUG_INFO_DWARF4 ; do
  scripts/config --disable "$opt"
done
scripts/config --enable DEBUG_INFO_NONE 2>/dev/null || true
"${MAKE[@]}" olddefconfig >/dev/null 2>&1

mark "VERIFY CONFIG"
grep -E "CONFIG_(VIRTIO_MMIO|VIRTIO_MMIO_CMDLINE_DEVICES|HW_RANDOM_VIRTIO|RANDOM_TRUST_CPU|OVERLAY_FS|VIRTIO_VSOCKETS|VIRTIO_BLK|EXT4_FS|SECCOMP_FILTER|RANDOMIZE_BASE)=" .config || true
echo "--- (RANDOMIZE_BASE absent above = KASLR off, as required) ---"

if [ "$KARCH" = x86_64 ]; then BUILDTGT=vmlinux; else BUILDTGT=Image; fi
mark "BUILD $BUILDTGT -j$JOBS"
nice -n 10 ionice -c3 "${MAKE[@]}" -j"$JOBS" "$BUILDTGT" > build.out 2>&1; RC=$?
echo "--- last 20 lines of build.out ---"; tail -20 build.out
if [ $RC -ne 0 ] || [ ! -f "$IMGREL" ]; then mark "BUILD_FAIL rc=$RC"; exit 7; fi

cp "$IMGREL" "$PNK/vmlinux-rng.bin"
if [ "$KARCH" = arm64 ]; then
  cp "$IMGREL" "$PNK/vmlinux.bin"
  mark "BUILD_OK -> vmlinux-rng.bin + vmlinux.bin ($(stat -c%s "$IMGREL") bytes)"
else
  mark "BUILD_OK -> vmlinux-rng.bin ($(stat -c%s "$IMGREL") bytes) (x86 vmlinux.bin left untouched)"
fi
file "$PNK/vmlinux-rng.bin"
mark "ALL_DONE"
