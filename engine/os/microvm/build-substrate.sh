#!/bin/bash
set -euo pipefail
MVDIR="${MVDIR:-$HOME/brainarbeit-build/microvm}"
mkdir -p "$MVDIR"/{bin,kernel,rootfs,run}
cd "$MVDIR"

echo "== 1) KVM present? =="
[ -e /dev/kvm ] || echo "  WARNING: /dev/kvm absent — load it: sudo modprobe kvm kvm_intel nested=1"

echo "== 2) Firecracker + jailer (latest static release) =="
if [ ! -x bin/firecracker ]; then
  LATEST=$(curl -s https://api.github.com/repos/firecracker-microvm/firecracker/releases/latest \
           | grep -m1 '"tag_name"' | cut -d'"' -f4)
  ARCH=x86_64
  curl -sL "https://github.com/firecracker-microvm/firecracker/releases/download/${LATEST}/firecracker-${LATEST}-${ARCH}.tgz" -o fc.tgz
  tar xzf fc.tgz
  RD=$(find . -maxdepth 1 -type d -name 'release-*' | head -1)
  cp -f "$RD/firecracker-${LATEST}-${ARCH}" bin/firecracker
  cp -f "$RD/jailer-${LATEST}-${ARCH}"      bin/jailer
  chmod +x bin/firecracker bin/jailer
  rm -rf fc.tgz "$RD"
fi
bin/firecracker --version | head -1

echo "== 3) our OWN kernel -> uncompressed vmlinux ELF (needs root to read /boot) =="
if [ ! -s kernel/vmlinux-ours ]; then
  EX="/usr/src/linux-headers-$(uname -r)/scripts/extract-vmlinux"
  if [ -r "/boot/vmlinuz-$(uname -r)" ]; then
    "$EX" "/boot/vmlinuz-$(uname -r)" > kernel/vmlinux-ours
  else
    echo "  /boot kernel not readable — run this step as root: sudo $0"
  fi
fi
[ -s kernel/vmlinux-ours ] && echo "  vmlinux-ours: $(du -h kernel/vmlinux-ours | cut -f1)"

echo "== 4) from-scratch busybox rootfs (our userland; no mount needed: mkfs.ext4 -d) =="
rm -rf rootfs/root && mkdir -p rootfs/root/{bin,sbin,etc,proc,sys,dev,tmp,run}
cp "$(command -v busybox)" rootfs/root/bin/busybox
cat > rootfs/root/init <<'INIT'
#!/bin/busybox sh
BB=/bin/busybox
$BB mount -t proc proc /proc 2>/dev/null
$BB mount -t sysfs sys /sys 2>/dev/null
$BB mount -t devtmpfs dev /dev 2>/dev/null
for a in sh ls cat echo id uname free mount hostname sleep poweroff grep head tail ip sed awk; do $BB ln -sf /bin/busybox /bin/$a 2>/dev/null; done
export PATH=/bin:/sbin
$BB ip link set lo up 2>/dev/null
if $BB ip link show eth0 >/dev/null 2>&1; then $BB ip link set eth0 up 2>/dev/null; HASNET=yes; else HASNET=no; fi
CID=$($BB sed -n "s/.*CELL_ID=\([^ ]*\).*/\1/p" /proc/cmdline)
TEN=$($BB sed -n "s/.*TENANT=\([^ ]*\).*/\1/p" /proc/cmdline)
echo ""
echo "  BRAINARBEIT CELL  cell=${CID:-?} tenant=${TEN:-?} kernel=$($BB uname -r) net=$HASNET"
echo "  (its OWN kernel; host enforces cgroup+uid+VM+net isolation)"
while true; do $BB sleep 3600; done
INIT
chmod +x rootfs/root/init
ln -sf busybox rootfs/root/bin/sh
printf 'brainarbeit-cell\n' > rootfs/root/etc/hostname
rm -f rootfs/cell-rootfs.ext4
mkfs.ext4 -q -F -L cell -d rootfs/root rootfs/cell-rootfs.ext4 160M
echo "  cell-rootfs.ext4: $(du -h rootfs/cell-rootfs.ext4 | cut -f1)"

echo "== DONE. substrate in $MVDIR — prove it: sudo python3 os/microvm/demo-isolation.py [--net] =="
