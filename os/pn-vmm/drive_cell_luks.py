#!/usr/bin/env python3

import subprocess, threading, time, sys, os, getpass

BIN = os.environ.get("BIN", "/tmp/pn-vmm-luks")
KERNEL = "kernel/vmlinux.bin"
INITRD = "kernel/initramfs-cell-luks.cpio"
BASE = "kernel/base_luks.img"
DELTA_LUKS = "kernel/delta_luks.img"
KEYFILE = "kernel/delta_luks.key"
MAPPER = "cell-delta-luks-run"
MAPPER_DEV = "/dev/mapper/" + MAPPER
PLAIN = "/dev/shm/pn-delta-plain.img"
USER = getpass.getuser()
PW = os.environ.get("PN_SUDO_PW", "")

def sudo(cmd, check=False, quiet=False):
    if not PW:
        sys.exit("PN_SUDO_PW not set; refusing sudo step")
    p = subprocess.run(["sudo", "-S", "-p", ""] + cmd, input=PW + "\n",
                       text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if not quiet:
        for ln in p.stdout.splitlines():
            if "unable to resolve host" in ln:
                continue
            print("   [sudo] " + ln)
    if check and p.returncode != 0:
        sys.exit("sudo %s failed rc=%d\n%s" % (cmd[0], p.returncode, p.stdout))
    return p.returncode, p.stdout

def unlock_to_tmpfs():
    sudo(["cryptsetup", "close", MAPPER], quiet=True)
    sudo(["cryptsetup", "open", "--key-file", KEYFILE, DELTA_LUKS, MAPPER], check=True)

    sudo(["dd", "if=" + MAPPER_DEV, "of=" + PLAIN, "bs=1M", "conv=fsync"], check=True)
    sudo(["chown", "%s:%s" % (USER, USER), PLAIN], check=True)
    sudo(["cryptsetup", "close", MAPPER], check=True)
    sz = os.path.getsize(PLAIN)
    print("   unlocked LUKS delta -> %s (%d bytes, %d sectors, tmpfs/RAM)" % (PLAIN, sz, sz // 512))

def seal_from_tmpfs():
    sudo(["cryptsetup", "open", "--key-file", KEYFILE, DELTA_LUKS, MAPPER], check=True)
    sudo(["dd", "if=" + PLAIN, "of=" + MAPPER_DEV, "bs=1M", "conv=fsync"], check=True)
    sudo(["cryptsetup", "close", MAPPER], check=True)
    print("   re-encrypted tmpfs plaintext BACK into %s" % DELTA_LUKS)

def wipe_tmpfs():
    sudo(["shred", "-u", PLAIN], quiet=True)
    print("   shredded tmpfs plaintext %s" % PLAIN)

def boot(tag):
    env = dict(os.environ)
    env["PN_VMM_BLK"] = "%s,%s" % (BASE, PLAIN)
    p = subprocess.Popen([BIN, KERNEL, INITRD], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, bufsize=0, env=env)
    lines = []
    ready = threading.Event()
    done = threading.Event()

    def reader():
        for raw in iter(p.stdout.readline, b""):
            s = raw.decode(errors="replace")
            sys.stdout.write("[%s] %s" % (tag, s))
            sys.stdout.flush()
            lines.append(s)
            if "PN_CELL_READY" in s or "PN_CELL_ROOT_PID1_ALIVE" in s:
                ready.set()
        done.set()

    threading.Thread(target=reader, daemon=True).start()
    if ready.wait(90):
        time.sleep(0.6)
        try:
            p.stdin.write(b"busybox reboot -f\n")
            p.stdin.flush()
        except BrokenPipeError:
            pass
    else:
        print("!! [%s] cell never became ready within 90s" % tag, file=sys.stderr)
    done.wait(30)
    try:
        rc = p.wait(timeout=15)
    except subprocess.TimeoutExpired:
        p.kill()
        rc = -9
    return "".join(lines), rc

def at_rest_proof():
    r = subprocess.run(["grep", "-a", "-c", "written-boot1", DELTA_LUKS],
                       stdout=subprocess.PIPE, text=True)
    raw_hits = r.stdout.strip()
    print("   grep -a -c written-boot1 %s  ->  %s   (0 == plaintext NOT on disk at rest)"
          % (DELTA_LUKS, raw_hits))

    sudo(["cryptsetup", "close", MAPPER], quiet=True)
    sudo(["cryptsetup", "open", "--key-file", KEYFILE, DELTA_LUKS, MAPPER], check=True)
    mnt = "/tmp/pn-luks-verify-mnt"
    sudo(["mkdir", "-p", mnt])
    sudo(["mount", "-o", "ro", MAPPER_DEV, mnt], check=True)
    _, out = sudo(["sh", "-c", "grep -a -c written-boot1 %s/upper/home/owner/PERSIST 2>&1" % mnt], quiet=True)
    dec_hits = "".join([l for l in out.splitlines() if "unable to resolve host" not in l]).strip()
    print("   grep behind-key (decrypted container) upper/.../PERSIST -> %s   (>=1 == guest data recoverable with key)"
          % dec_hits)
    sudo(["umount", mnt], quiet=True)
    sudo(["cryptsetup", "close", MAPPER], quiet=True)
    return raw_hits, dec_hits

def main():
    print("=== HOST-SIDE LUKS: unlock delta into tmpfs (RAM) ===")
    unlock_to_tmpfs()
    try:
        b1, rc1 = boot("boot1")
        time.sleep(0.5)
        b2, rc2 = boot("boot2")
        print("=== seal: re-encrypt tmpfs plaintext back into LUKS container ===")
        seal_from_tmpfs()
    finally:
        wipe_tmpfs()
        sudo(["cryptsetup", "close", MAPPER], quiet=True)

    raw_hits, dec_hits = at_rest_proof()

    def vdb_not_luks(b):
        try:
            seg = b.split("PN_LUKS_VDB_MAGIC=")[1].split(" (")[0]
            return "LUKS" not in seg
        except IndexError:
            return False

    print("\n==================== VERDICT (LUKS cell, host-side decrypt) ====================")
    checks = {
        "host-side LUKS mode announced (b1)":       "PN_LUKS_MODE=HOST_SIDE_DECRYPT" in b1,
        "guest saw DECRYPTED vdb (not LUKS magic)": vdb_not_luks(b1),
        "delta mounted rw (boot1)":                 "PN_MOUNT_DELTA_OK" in b1,
        "overlay root assembled (boot1)":           "PN_OVERLAY_OK" in b1,
        "host $HOME ABSENT (b1)":         "PN_CELL_HOSTHOME=ABSENT_GOOD" in b1,
        "delta write happened (boot1)":             "PN_CELL_PERSIST_WRITTEN" in b1,
        "switch_root -> cell PID1 (boot1)":         "PN_CELL_ROOT_PID1_ALIVE" in b1,
        "persistence: marker FOUND (boot2)":        "PN_CELL_PERSIST_FOUND=written-boot1" in b2,
        "overlay root assembled (boot2)":           "PN_OVERLAY_OK" in b2,
        "AT REST: plaintext NOT in container":      raw_hits == "0",
        "BEHIND KEY: plaintext recoverable":        dec_hits not in ("", "0"),
    }
    for k, v in checks.items():
        print("  [%s] %s" % ("PASS" if v else "FAIL", k))
    print("  exit codes:", rc1, rc2)
    print("  RESULT:", "LUKS-DELTA (encryption-at-rest + persistence) PASS"
          if all(checks.values()) else "INCOMPLETE")

if __name__ == "__main__":
    main()
