#!/usr/bin/env python3

import os

BUSYBOX = "/usr/bin/busybox"
CLIENT = os.environ.get("CLIENT", "kernel/vsock_client")
OUT = os.environ.get("OUT", "kernel/initramfs-vsock.cpio")

S_IFDIR = 0o040000
S_IFREG = 0o100000
S_IFLNK = 0o120000
S_IFCHR = 0o020000

INIT = b"""#!/bin/busybox sh
export PATH=/bin
busybox mkdir -p /proc /sys /dev
busybox mount -t proc none /proc
busybox mount -t sysfs none /sys
busybox mount -t devtmpfs none /dev 2>/dev/null
busybox --install -s /bin 2>/dev/null
echo ""
echo "PN_VMM_VSOCK_INITRAMFS_ALIVE"
echo "PN_VSOCK_DMESG_BEGIN"; busybox dmesg | busybox grep -i vsock; echo "PN_VSOCK_DMESG_END"
if [ -e /dev/vsock ]; then echo "PN_VSOCK_DEV_PRESENT"; else echo "PN_VSOCK_DEV_ABSENT"; fi
echo "PN_VSOCK_CLIENT_BEGIN"
/vsock_client
echo "PN_VSOCK_CLIENT_END"
echo "PN_VSOCK_TEST_DONE"
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
    with open(CLIENT, "rb") as f:
        client = f.read()
    c = Cpio()
    for d in ["bin", "dev", "proc", "sys", "tmp"]:
        c.add(d, S_IFDIR | 0o755, nlink=2)
    c.add("bin/busybox", S_IFREG | 0o755, bb)
    c.add("init", S_IFREG | 0o755, INIT)
    c.add("vsock_client", S_IFREG | 0o755, client)
    c.add("bin/sh", S_IFLNK | 0o777, b"busybox")
    c.add("dev/console", S_IFCHR | 0o600, rdevmajor=5, rdevminor=1)
    c.add("dev/null", S_IFCHR | 0o666, rdevmajor=1, rdevminor=3)
    c.add("dev/ttyS0", S_IFCHR | 0o660, rdevmajor=4, rdevminor=64)
    data = c.finish()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "wb") as f:
        f.write(data)
    print("wrote %s (%d bytes, client %d bytes)" % (OUT, len(data), len(client)))

if __name__ == "__main__":
    main()
