#!/usr/bin/env python3

import os, subprocess, shutil, sys

BB = "/usr/bin/busybox"
BASE_DIR = "kernel/_base_luks"
BASE_IMG = "kernel/base_luks.img"
DELTA_IMG = "kernel/delta_luks.img"
KEYFILE = "kernel/delta_luks.key"
MAPPER = "cell-delta-luks-build"
DELTA_CONTAINER_MB = 64

PW = os.environ.get("PN_SUDO_PW", "")

ROOT_INIT = """#!/bin/busybox sh
export PATH=/bin:/sbin
busybox mkdir -p /proc /sys /dev
busybox mount -t proc none /proc 2>/dev/null
busybox mount -t devtmpfs none /dev 2>/dev/null
busybox --install -s /bin 2>/dev/null
echo "PN_CELL_ROOT_PID1_ALIVE uid=$(busybox id -u) root=$(busybox grep ' / ' /proc/mounts | busybox awk '{print $3}')"
exec busybox sh
"""

def sudo(cmd):

    if not PW:
        sys.exit("PN_SUDO_PW not set in environment; refusing to run sudo step")
    p = subprocess.run(["sudo", "-S", "-p", ""] + cmd, input=PW + "\n",
                       text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    sys.stdout.write(p.stdout)
    return p.returncode

def build_base():
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
    subprocess.run(["truncate", "-s", "64M", BASE_IMG], check=True)
    subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q", "-d", BASE_DIR, BASE_IMG], check=True)
    print("built %s (RO CAS base)" % BASE_IMG)

def build_luks_delta():

    with open(KEYFILE, "wb") as f:
        f.write(os.urandom(64))
    os.chmod(KEYFILE, 0o600)

    if os.path.exists(DELTA_IMG):
        os.remove(DELTA_IMG)
    subprocess.run(["truncate", "-s", "%dM" % DELTA_CONTAINER_MB, DELTA_IMG], check=True)

    sudo(["cryptsetup", "close", MAPPER])

    rc = sudo(["cryptsetup", "luksFormat", "--type", "luks2", "--batch-mode",
               "--pbkdf", "pbkdf2", "--pbkdf-force-iterations", "1000",
               "--key-file", KEYFILE, DELTA_IMG])
    if rc != 0:
        sys.exit("luksFormat failed rc=%d" % rc)
    print("luksFormat OK on %s" % DELTA_IMG)

    rc = sudo(["cryptsetup", "open", "--key-file", KEYFILE, DELTA_IMG, MAPPER])
    if rc != 0:
        sys.exit("cryptsetup open failed rc=%d" % rc)
    try:
        rc = sudo(["mke2fs", "-t", "ext4", "-F", "-q", "/dev/mapper/" + MAPPER])
        if rc != 0:
            sys.exit("mke2fs on mapper failed rc=%d" % rc)
        print("ext4 created inside LUKS mapper")
    finally:
        sudo(["cryptsetup", "close", MAPPER])
    print("built %s (LUKS2 encrypted RW delta) + %s (keyfile)" % (DELTA_IMG, KEYFILE))

def main():
    build_base()
    build_luks_delta()

    subprocess.run(["ls", "-la", BASE_IMG, DELTA_IMG, KEYFILE])

if __name__ == "__main__":
    main()
