#!/usr/bin/env python3

import subprocess, threading, time, sys, os

BIN = os.environ.get("BIN", "./target/release/pn-vmm")
KERNEL = "kernel/vmlinux.bin"
INITRD = "kernel/initramfs-cell.cpio"
BLK = "kernel/base.img,kernel/delta.img"

def boot(tag):
    env = dict(os.environ); env["PN_VMM_BLK"] = BLK
    p = subprocess.Popen([BIN, KERNEL, INITRD], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, bufsize=0, env=env)
    lines = []; ready = threading.Event(); done = threading.Event()

    def reader():
        for raw in iter(p.stdout.readline, b""):
            s = raw.decode(errors="replace"); sys.stdout.write("[%s] %s" % (tag, s)); sys.stdout.flush(); lines.append(s)
            if "PN_CELL_READY" in s or "PN_CELL_ROOT_PID1_ALIVE" in s: ready.set()
        done.set()

    threading.Thread(target=reader, daemon=True).start()
    if ready.wait(90):
        time.sleep(0.6)
        try:
            p.stdin.write(b"busybox reboot -f\n"); p.stdin.flush()
        except BrokenPipeError:
            pass
    else:
        print("!! [%s] cell never became ready within 90s" % tag, file=sys.stderr)
    done.wait(30)
    try:
        rc = p.wait(timeout=15)
    except subprocess.TimeoutExpired:
        p.kill(); rc = -9
    return "".join(lines), rc

b1, rc1 = boot("boot1")
time.sleep(0.5)
b2, rc2 = boot("boot2")

print("\n==================== VERDICT (cell overlay root) ====================")
checks = {
    "overlay root assembled (boot1)":          "PN_OVERLAY_OK" in b1,
    "host $HOME ABSENT in cell (b1)": "PN_CELL_HOSTHOME=ABSENT_GOOD" in b1,
    "owner CAS data visible (b1)":              "PN_CELL_OWNER_DATA=owners-private-cell-data" in b1,
    "delta write happened (boot1)":            "PN_CELL_PERSIST_WRITTEN" in b1,
    "switch_root -> cell PID1 (boot1)":         "PN_CELL_ROOT_PID1_ALIVE" in b1,
    "persistence: marker FOUND (boot2)":        "PN_CELL_PERSIST_FOUND=written-boot1" in b2,
    "host home still ABSENT (boot2)":           "PN_CELL_HOSTHOME=ABSENT_GOOD" in b2,
}
for k, v in checks.items():
    print("  [%s] %s" % ("PASS" if v else "FAIL", k))
print("  exit codes:", rc1, rc2)
print("  RESULT:", "MILESTONE-2 (cell overlay root + persistence) PASS"
      if all(checks.values()) else "INCOMPLETE")
