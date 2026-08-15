#!/usr/bin/env python3

import os

BUSYBOX = "/usr/bin/busybox"
OUT = os.environ.get("OUT", "kernel/initramfs-cell.cpio")

S_IFDIR = 0o040000
S_IFREG = 0o100000
S_IFLNK = 0o120000
S_IFCHR = 0o020000

INIT = b"""#!/bin/busybox sh
export PATH=/bin
busybox mkdir -p /proc /sys /dev /lower /delta /newroot
busybox mount -t proc none /proc
busybox mount -t sysfs none /sys
busybox mount -t devtmpfs none /dev 2>/dev/null
busybox --install -s /bin 2>/dev/null
echo ""
echo "PN_VMM_CELL_INITRAMFS_ALIVE"
busybox mount -o ro -t ext4 /dev/vda /lower && echo "PN_MOUNT_BASE_OK" || echo "PN_MOUNT_BASE_FAIL"
busybox mount -t ext4 /dev/vdb /delta && echo "PN_MOUNT_DELTA_OK" || echo "PN_MOUNT_DELTA_FAIL"
busybox mkdir -p /delta/upper /delta/work
if busybox mount -t overlay overlay -o lowerdir=/lower,upperdir=/delta/upper,workdir=/delta/work /newroot 2>/dev/null; then
  echo "PN_OVERLAY_OK"
else
  echo "PN_OVERLAY_UNAVAILABLE_FALLBACK_BIND"
  busybox mount --bind /lower /newroot
fi
echo "PN_CELL_LS_ROOT_BEGIN"; busybox ls -1 /newroot; echo "PN_CELL_LS_ROOT_END"
echo -n "PN_CELL_HOSTHOME="; leak=0; for d in /newroot/home/*; do [ "$d" = "/newroot/home/owner" ] && continue; [ -e "$d" ] && leak=1; done; if [ "$leak" = 1 ]; then echo "VISIBLE_BAD"; else echo "ABSENT_GOOD"; fi
echo -n "PN_CELL_OWNER_DATA="; busybox cat /newroot/home/owner/HELLO.txt 2>/dev/null; echo ""
if [ -f /newroot/home/owner/PERSIST ]; then
  echo -n "PN_CELL_PERSIST_FOUND="; busybox cat /newroot/home/owner/PERSIST; echo ""
else
  echo "written-boot1" > /newroot/home/owner/PERSIST 2>/dev/null && echo "PN_CELL_PERSIST_WRITTEN" || echo "PN_CELL_PERSIST_WRITE_FAIL"
fi
busybox sync
echo "PN_CELL_READY"
# Hand the cell its own root. switch_root MUST be PID 1 (busybox bb_show_usage()s otherwise), so we
# exec it (no fork). switch_root itself verifies /newroot is a separate mount; we just confirm the
# cell init is present+executable (busybox has no `mountpoint` applet on this build).
if [ -x /newroot/sbin/init ]; then
  echo "PN_SWITCHROOT_ATTEMPT"
  exec busybox switch_root /newroot /sbin/init
fi
echo "PN_SWITCHROOT_SKIP_NO_INIT"
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
        fields = [self.ino, mode, 0, 0, nlink, 0, len(data), 0, 0, rdevmajor, rdevminor, len(name_b), 0]
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
    for d in ["bin", "dev", "proc", "sys", "lower", "delta", "newroot", "tmp"]:
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
    print("wrote %s (%d bytes)" % (OUT, len(data)))

if __name__ == "__main__":
    main()
