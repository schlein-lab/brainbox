#!/usr/bin/env python3

import subprocess, threading, time, sys, os

BIN = os.environ.get("BIN", "./target/release/pn-vmm")
KERNEL = "kernel/vmlinux.bin"
INITRD = "kernel/initramfs-vsock.cpio"

env = dict(os.environ); env["PN_VMM_VSOCK"] = "3"
p = subprocess.Popen([BIN, KERNEL, INITRD], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, bufsize=0, env=env)
lines = []; ready = threading.Event(); done = threading.Event()

def reader():
    for raw in iter(p.stdout.readline, b""):
        s = raw.decode(errors="replace"); sys.stdout.write(s); sys.stdout.flush(); lines.append(s)
        if "PN_VSOCK_TEST_DONE" in s: ready.set()
    done.set()

threading.Thread(target=reader, daemon=True).start()
if ready.wait(90):
    time.sleep(0.4)
    try:
        p.stdin.write(b"busybox reboot -f\n"); p.stdin.flush()
    except BrokenPipeError:
        pass
else:
    print("!! guest never reached PN_VSOCK_TEST_DONE within 90s", file=sys.stderr)
done.wait(30)
try:
    rc = p.wait(timeout=15)
except subprocess.TimeoutExpired:
    p.kill(); rc = -9

blob = "".join(lines)
print("\n==================== VERDICT (virtio-vsock) ====================")
checks = {
    "guest booted with vsock device":   "PN_VMM_VSOCK_INITRAMFS_ALIVE" in blob,
    "/dev/vsock present (transport up)": "PN_VSOCK_DEV_PRESENT" in blob,
    "client connected to host (CID2)":   "PN_VSOCK_CONNECTED" in blob,
    "host echo round-tripped (RW path)": "PN_VSOCK_ECHO=PING-FROM-GUEST" in blob,
    "clean guest exit":                  "clean guest exit" in blob or "KVM_EXIT_SHUTDOWN" in blob,
}
for k, v in checks.items():
    print("  [%s] %s" % ("PASS" if v else "FAIL", k))
print("  exit code:", rc)
print("  RESULT:", "STAGE 3 (virtio-vsock echo) PASS" if all(checks.values()) else "INCOMPLETE")
