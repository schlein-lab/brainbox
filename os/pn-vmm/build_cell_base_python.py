#!/usr/bin/env python3

import os, sys, shutil, subprocess, glob
import platform, sysconfig

BB      = "/usr/bin/busybox"
PYBIN   = "/usr/bin/python3.12"
STDLIB  = "/usr/lib/python3.12"
CRYPTO  = "/usr/lib/python3/dist-packages/cryptography"
REPO    = os.environ.get("PN_REPO", os.path.expanduser("~/brainarbeit"))

MULTIARCH = sysconfig.get_config_var("MULTIARCH") or "%s-linux-gnu" % platform.machine()

def _finde_lader():

    kandidaten = ["/lib64/ld-linux-x86-64.so.2",
                  "/lib/ld-linux-aarch64.so.1",
                  "/lib/ld-linux-armhf.so.3"]
    kandidaten += sorted(glob.glob("/lib*/ld-linux*.so*"))
    kandidaten += sorted(glob.glob("/lib/%s/ld-linux*.so*" % MULTIARCH))
    for p in kandidaten:
        if os.path.exists(p):
            return p
    sys.exit("build_cell_base_python: kein dynamischer Lader gefunden (Architektur %s). "
             "Ohne ihn startet in der Zelle kein einziges Programm." % platform.machine())

REQ_LIBS = ["/lib/%s/%s" % (MULTIARCH, _n) for _n in (
    "libm.so.6", "libz.so.1", "libexpat.so.1", "libc.so.6",
    "libssl.so.3", "libcrypto.so.3", "libgcc_s.so.1",
)]
LD_LINUX = _finde_lader()

EXTRA_LIB_NAMES = [
    "libffi.so.8", "libsqlite3.so.0", "libbz2.so.1.0", "liblzma.so.5",
    "libreadline.so.8", "libtinfo.so.6", "libncursesw.so.6", "libmpdec.so.4",
    "libmpdec.so.3", "libuuid.so.1", "libcrypt.so.1", "libpanelw.so.6",
]
LIB_SEARCH = ["/lib/%s" % MULTIARCH, "/usr/lib/%s" % MULTIARCH, "/lib", "/usr/lib"]

BASE_DIR = "kernel/_pybase"
BASE_IMG = "kernel/base-python.img"
IMG_SIZE = "256M"

ROOT_INIT = r'''#!/bin/busybox sh
export PATH=/bin:/sbin
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
busybox mkdir -p /proc /sys /dev /tmp
busybox mount -t proc none /proc 2>/dev/null
busybox mount -t devtmpfs none /dev 2>/dev/null
busybox mount -t tmpfs none /tmp 2>/dev/null
busybox --install -s /bin 2>/dev/null
export PYTHONHOME=/usr
export PYTHONPATH=/site:/opt/pn
export LD_LIBRARY_PATH=/lib:/lib64
export CRYPTOGRAPHY_OPENSSL_NO_LEGACY=1
export BRAINBOX_CELL_KEYS=/tmp/cell-keys.json
# entropy: credit a FRESH host-provided seed (per-boot, delivered in the per-cell delta at /seed) so the
# CRNG initialises with REAL host entropy and getrandom() cannot block. Fresh-per-cold-boot => two cells
# never share RNG state (clone-safe). Wiped after use. GA path: continuous virtio-rng + newer kernel.
if [ -f /seed ]; then
  /bin/python3 -c 'import os,fcntl,struct
d=open("/seed","rb").read()
if d:
 fd=os.open("/dev/random",os.O_WRONLY); fcntl.ioctl(fd,0x40085203,struct.pack("ii",len(d)*8,len(d))+d)
print("PN_CELL_CRNG_SEEDED bytes="+str(len(d)))' 2>/dev/null
  busybox rm -f /seed
fi
echo "PN_CELL_PYBASE_ALIVE uid=$(busybox id -u) py=$(/bin/python3 -c 'import sys;print(sys.version.split()[0])' 2>&1)"
if busybox grep -q pn_keygen=1 /proc/cmdline 2>/dev/null; then
  echo "PN_CELL_KEYGEN_BEGIN"
  /bin/python3 /opt/pn/tools/pn-cell-sealed-run keygen 2>&1
  echo "PN_CELL_KEYGEN_END keystore=$([ -f /tmp/cell-keys.json ] && echo present-in-cell || echo MISSING)"
fi
if busybox grep -q pn_seat=1 /proc/cmdline 2>/dev/null && [ -x /bin/vsock-seat ]; then
  /bin/vsock-seat
  echo "PN_CELL_SEAT_ENDED_FALLBACK_SERIAL"
fi
exec busybox sh
'''

def find_lib(name):
    for d in LIB_SEARCH:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None

def main():
    here = os.path.dirname(os.path.realpath(__file__))
    os.chdir(here)
    if os.path.exists(BASE_DIR):
        shutil.rmtree(BASE_DIR)
    for d in ["bin", "sbin", "lib", "lib64", "usr/lib", "usr/lib/ssl", "site", "opt/pn/tools",
              "proc", "sys", "dev", "tmp", "root", "home/owner"]:
        os.makedirs(os.path.join(BASE_DIR, d), exist_ok=True)

    shutil.copy(BB, f"{BASE_DIR}/bin/busybox"); os.chmod(f"{BASE_DIR}/bin/busybox", 0o755)
    os.symlink("busybox", f"{BASE_DIR}/bin/sh")

    shutil.copy(PYBIN, f"{BASE_DIR}/bin/python3"); os.chmod(f"{BASE_DIR}/bin/python3", 0o755)
    shutil.copytree(STDLIB, f"{BASE_DIR}/usr/lib/python3.12", symlinks=True)

    shutil.copytree(CRYPTO, f"{BASE_DIR}/site/cryptography", symlinks=True)
    for so in glob.glob("/usr/lib/python3/dist-packages/_cffi_backend*.so"):
        shutil.copy(so, f"{BASE_DIR}/site/{os.path.basename(so)}")

    _ca = "/etc/ssl/certs/ca-certificates.crt"
    if os.path.exists(_ca):
        os.makedirs(f"{BASE_DIR}/etc/ssl/certs", exist_ok=True)
        shutil.copy(_ca, f"{BASE_DIR}/etc/ssl/certs/ca-certificates.crt")
        os.symlink("certs/ca-certificates.crt", f"{BASE_DIR}/etc/ssl/cert.pem")
        os.symlink("/etc/ssl/certs/ca-certificates.crt", f"{BASE_DIR}/usr/lib/ssl/cert.pem")
    else:
        print("WARN: kein Host-CA-Bundle — Zellen brauchen dann Boot-Staging fuer TLS-Verify")

    _bc = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "bcrypt")
    if os.path.isdir(_bc):
        shutil.copytree(_bc, f"{BASE_DIR}/site/bcrypt", symlinks=True)
    else:
        print("WARN: vendor/bcrypt fehlt — passphrase-SSH-Keys brauchen dann Boot-Staging")

    shutil.copytree(f"{REPO}/relaylib", f"{BASE_DIR}/opt/pn/relaylib", symlinks=True)
    shutil.copy(f"{REPO}/tools/pn-cell-sealed-run", f"{BASE_DIR}/opt/pn/tools/pn-cell-sealed-run")
    os.chmod(f"{BASE_DIR}/opt/pn/tools/pn-cell-sealed-run", 0o755)

    copied = []
    for lib in REQ_LIBS:
        shutil.copy(lib, f"{BASE_DIR}/lib/{os.path.basename(lib)}"); copied.append(os.path.basename(lib))

    _ld_ziel = os.path.join(BASE_DIR, LD_LINUX.lstrip("/"))
    os.makedirs(os.path.dirname(_ld_ziel), exist_ok=True)
    shutil.copy(LD_LINUX, _ld_ziel)
    for name in EXTRA_LIB_NAMES:
        p = find_lib(name)
        if p:
            shutil.copy(p, f"{BASE_DIR}/lib/{name}"); copied.append(name)

    with open(f"{BASE_DIR}/sbin/init", "w") as f:
        f.write(ROOT_INIT)
    os.chmod(f"{BASE_DIR}/sbin/init", 0o755)
    with open(f"{BASE_DIR}/home/owner/HELLO.txt", "w") as f:
        f.write("owners-private-cell-data-host-home-not-visible")

    if os.path.exists("kernel/vsock_seat"):
        shutil.copy("kernel/vsock_seat", f"{BASE_DIR}/bin/vsock-seat")
        os.chmod(f"{BASE_DIR}/bin/vsock-seat", 0o755)

    print("staged libs:", " ".join(sorted(copied)))
    sz = subprocess.run(["du", "-sh", BASE_DIR], capture_output=True, text=True).stdout.split()[0]
    print("staging tree size:", sz)

    base_abs = os.path.abspath(BASE_DIR)
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONHOME": f"{base_abs}/usr",
        "PYTHONPATH": f"{base_abs}/site:{base_abs}/opt/pn",
        "LD_LIBRARY_PATH": f"{base_abs}/lib:{base_abs}/lib64",
        "CRYPTOGRAPHY_OPENSSL_NO_LEGACY": "1",
        "BRAINBOX_CELL_KEYS": "/tmp/pn-smoke-keys.json",
    }
    try:
        os.remove("/tmp/pn-smoke-keys.json")
    except OSError:
        pass
    print("PN_SMOKE_BEGIN")
    r = subprocess.run([f"{base_abs}/bin/python3", f"{base_abs}/opt/pn/tools/pn-cell-sealed-run", "keygen"],
                       env=env, capture_output=True, text=True)
    print("rc=%d" % r.returncode)
    print("stdout:", r.stdout.strip()[:800])
    if r.stderr.strip():
        print("stderr:", r.stderr.strip()[:1500])
    ok = r.returncode == 0 and ("cell_x_pub" in r.stdout) and os.path.exists("/tmp/pn-smoke-keys.json")
    print("PN_SMOKE_%s" % ("PASS" if ok else "FAIL"))
    if not ok:
        print("=> not writing image; fix deps above and re-run.")
        sys.exit(2)

    subprocess.run(["truncate", "-s", IMG_SIZE, BASE_IMG], check=True)
    subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q", "-d", BASE_DIR, BASE_IMG], check=True)
    print("PN_IMAGE_BUILT %s (%s)" % (BASE_IMG, IMG_SIZE))

if __name__ == "__main__":
    main()
