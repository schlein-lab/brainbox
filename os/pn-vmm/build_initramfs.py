#!/usr/bin/env python3

import os, sys

BUSYBOX = "/usr/bin/busybox"
OUT = os.environ.get("OUT", "kernel/initramfs.cpio")

S_IFDIR = 0o040000
S_IFREG = 0o100000
S_IFLNK = 0o120000
S_IFCHR = 0o020000

INIT = b"""#!/bin/busybox sh
export PATH=/bin
busybox mkdir -p /proc /sys /dev /root /tmp
busybox mount -t proc none /proc
busybox mount -t sysfs none /sys
busybox mount -t devtmpfs none /dev 2>/dev/null
busybox --install -s /bin 2>/dev/null
echo ""
echo "=================================================="
echo " PN-VMM STAGE 2B  --  userspace is ALIVE"
echo -n " kernel : "; busybox uname -r
echo -n " arch   : "; busybox uname -m
echo -n " id     : "; busybox id
echo -n " uptime : "; busybox cut -d' ' -f1 /proc/uptime
busybox grep MemTotal /proc/meminfo
echo "-- virtio-blk probe --"
if [ -b /dev/vda ]; then
  echo "PN_VMM_BLK_DEV_PRESENT=/dev/vda"
  echo -n "PN_VMM_BLK_CAP_SECTORS="; busybox cat /sys/block/vda/size 2>/dev/null
  echo -n "PN_VMM_BLK_READ="; busybox dd if=/dev/vda bs=48 count=1 2>/dev/null | busybox tr -d '\\000'; echo ""
  echo "PNVM-GUEST-WROTE-THIS" | busybox dd of=/dev/vda bs=512 seek=1 count=1 conv=notrunc 2>/dev/null
  busybox sync
  echo "PN_VMM_BLK_WROTE_SECTOR1"
else
  echo "PN_VMM_BLK_DEV_ABSENT"
fi
echo " -- interactive busybox shell on ttyS0 --"
echo "PN_VMM_SHELL_READY"
exec busybox sh
"""

class Cpio:
    def __init__(self):
        self.buf = bytearray()
        self.ino = 721

    def _pad4(self):
        while len(self.buf) % 4:
            self.buf += b"\x00"

    def add(self, name, mode, data=b"", rdevmajor=0, rdevminor=0, nlink=1):
        name_b = name.encode() + b"\x00"
        fields = [
            self.ino, mode, 0, 0, nlink, 0, len(data),
            0, 0, rdevmajor, rdevminor, len(name_b), 0,
        ]
        self.ino += 1
        hdr = b"070701" + b"".join(b"%08X" % (f & 0xFFFFFFFF) for f in fields)
        self.buf += hdr + name_b
        self._pad4()
        self.buf += data
        self._pad4()

    def finish(self):

        self.add("TRAILER!!!", 0, nlink=1)
        return bytes(self.buf)

def main():
    with open(BUSYBOX, "rb") as f:
        bb = f.read()

    c = Cpio()

    for d in ["bin", "dev", "proc", "sys", "root", "tmp"]:
        c.add(d, S_IFDIR | 0o755, nlink=2)

    c.add("bin/busybox", S_IFREG | 0o755, bb)
    c.add("init", S_IFREG | 0o755, INIT)

    c.add("bin/sh", S_IFLNK | 0o777, b"busybox")

    c.add("dev/console", S_IFCHR | 0o600, rdevmajor=5, rdevminor=1)
    c.add("dev/null", S_IFCHR | 0o666, rdevmajor=1, rdevminor=3)
    c.add("dev/tty", S_IFCHR | 0o666, rdevmajor=5, rdevminor=0)
    c.add("dev/ttyS0", S_IFCHR | 0o660, rdevmajor=4, rdevminor=64)
    data = c.finish()

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "wb") as f:
        f.write(data)
    print("wrote %s  (%d bytes, busybox %d bytes)" % (OUT, len(data), len(bb)))

if __name__ == "__main__":
    main()
