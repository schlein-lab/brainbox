#!/usr/bin/env python3

import os, subprocess, shutil

BB = "/usr/bin/busybox"
BASE_DIR = "kernel/_base"
BASE_IMG = "kernel/base.img"
DELTA_IMG = "kernel/delta.img"

ROOT_INIT = """#!/bin/busybox sh
export PATH=/bin:/sbin
busybox mkdir -p /proc /sys /dev
busybox mount -t proc none /proc 2>/dev/null
busybox mount -t devtmpfs none /dev 2>/dev/null
busybox --install -s /bin 2>/dev/null
echo "PN_CELL_ROOT_PID1_ALIVE uid=$(busybox id -u) root=$(busybox grep ' / ' /proc/mounts | busybox awk '{print $3}')"
# If a real portal is attached (pn_seat=1), hand this cell's shell to it OVER VSOCK; else keep serial.
if busybox grep -q pn_seat=1 /proc/cmdline 2>/dev/null && [ -x /bin/vsock-seat ]; then
  /bin/vsock-seat
  echo "PN_CELL_SEAT_ENDED_FALLBACK_SERIAL"
fi
exec busybox sh
"""

def main():
    if os.path.exists(BASE_DIR):
        shutil.rmtree(BASE_DIR)
    for d in ["bin", "sbin", "etc", "proc", "sys", "dev", "tmp", "root", "home/owner"]:
        os.makedirs(os.path.join(BASE_DIR, d), exist_ok=True)
    shutil.copy(BB, os.path.join(BASE_DIR, "bin/busybox"))
    os.chmod(os.path.join(BASE_DIR, "bin/busybox"), 0o755)
    os.symlink("busybox", os.path.join(BASE_DIR, "bin/sh"))
    with open(os.path.join(BASE_DIR, "sbin/init"), "w") as f:
        f.write(ROOT_INIT)
    os.chmod(os.path.join(BASE_DIR, "sbin/init"), 0o755)
    with open(os.path.join(BASE_DIR, "home/owner/HELLO.txt"), "w") as f:
        f.write("owners-private-cell-data-host-home-not-visible")

    seat = "kernel/vsock_seat"
    if os.path.exists(seat):
        shutil.copy(seat, os.path.join(BASE_DIR, "bin/vsock-seat"))
        os.chmod(os.path.join(BASE_DIR, "bin/vsock-seat"), 0o755)

    subprocess.run(["truncate", "-s", "64M", BASE_IMG], check=True)
    subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q", "-d", BASE_DIR, BASE_IMG], check=True)
    subprocess.run(["truncate", "-s", "32M", DELTA_IMG], check=True)
    subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q", DELTA_IMG], check=True)
    print("built %s (RO CAS base) + %s (RW delta)" % (BASE_IMG, DELTA_IMG))

if __name__ == "__main__":
    main()
