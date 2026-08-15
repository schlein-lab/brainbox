#!/usr/bin/env python3

import subprocess, threading, time, sys, os

BIN = os.environ.get("BIN", "./target/release/pn-vmm")
KERNEL = os.environ.get("KERNEL", "vmlinux-rng.bin")
INITRD = os.environ.get("INITRD", "initramfs-cell.cpio")
BASE = os.environ.get("BASE", "base-owner-session.img")
DELTA = os.environ.get("DELTA", "delta-netproof.img")
TAP = os.environ.get("TAP", "pntap0")
IDX = os.environ.get("IDX", "0")
VCPUS = os.environ.get("VCPUS", "1")
MEM_MB = os.environ.get("MEM_MB", "1536")
URL = os.environ.get("URL", "http://detectportal.firefox.com/success.txt")

GUEST_IP = "10.77.%s.2" % IDX
HOST_IP = "10.77.%s.1" % IDX

env = dict(os.environ)
env["PN_VMM_BLK"] = "%s,%s" % (BASE, DELTA)
env["PN_VMM_NET_TAP"] = TAP
env["PN_VMM_MEM_MB"] = MEM_MB
env["PN_VMM_VCPUS"] = VCPUS
env.pop("PN_VMM_VSOCK", None)

p = subprocess.Popen([BIN, KERNEL, INITRD], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, bufsize=0, env=env)
lines = []
shell = threading.Event()
net_done = threading.Event()
done = threading.Event()

def reader():
    for raw in iter(p.stdout.readline, b""):
        s = raw.decode(errors="replace")
        sys.stdout.write(s); sys.stdout.flush(); lines.append(s)

        if "job control turned off" in s: shell.set()
        if "PN_NET_TEST_DONE" in s: net_done.set()
    done.set()

threading.Thread(target=reader, daemon=True).start()

CMDS = [
    "busybox ip link show eth0 && echo PN_NET_ETH0_PRE''SENT",
    "busybox dmesg | busybox grep -i virtio_net",
    "busybox ip addr add %s/30 dev eth0" % GUEST_IP,
    "busybox ip link set eth0 up",
    "busybox ip route add default via %s" % HOST_IP,
    "busybox ip addr show eth0 && echo PN_NET_CFG''_OK",
    "busybox ping -c 2 -W 3 %s && echo PN_NET_PING''_OK" % HOST_IP,
    "echo 'nameserver 9.9.9.9' > /etc/resolv.conf",
    "busybox wget -T 20 -qO- %s && echo PN_NET_HTTP''_OK" % URL,
    "echo PN_NET_TEST_DO''NE",
]

def send_slow(b):
    for i in range(0, len(b), 16):
        p.stdin.write(b[i:i + 16]); p.stdin.flush()
        time.sleep(0.05)

if shell.wait(120):
    time.sleep(1.0)
    try:
        for c in CMDS:
            send_slow(c.encode() + b"\n")
            time.sleep(1.0)
    except BrokenPipeError:
        pass
else:
    print("!! guest never reached the serial shell within 120s", file=sys.stderr)

if net_done.wait(90):
    time.sleep(0.5)
    try:
        p.stdin.write(b"busybox reboot -f\n"); p.stdin.flush()
    except BrokenPipeError:
        pass
done.wait(30)
try:
    rc = p.wait(timeout=15)
except subprocess.TimeoutExpired:
    p.kill(); rc = -9

blob = "".join(lines)
print("\n==================== VERDICT (virtio-net, vcpus=%s) ====================" % VCPUS)
checks = {
    "cell booted (session ready)":       "PN_CELL_SESSION_READY" in blob,
    "virtio-net device registered":      "virtio-net eth0 @" in blob,
    "guest eth0 present (driver probe)": "PN_NET_ETH0_PRESENT" in blob,
    "eth0 addr/route configured":        "PN_NET_CFG_OK" in blob and GUEST_IP in blob,
    "ping host across tap":              "PN_NET_PING_OK" in blob and "0% packet loss" in blob,
    "HTTP through host NAT":             "PN_NET_HTTP_OK" in blob and "success" in blob,
    "clean guest exit":                  "clean guest exit" in blob or "KVM_EXIT_SHUTDOWN" in blob,
}
for k, v in checks.items():
    print("  [%s] %s" % ("PASS" if v else "FAIL", k))
print("  exit code:", rc)
print("  RESULT:", "virtio-net (tap+NAT) PASS" if all(checks.values()) else "INCOMPLETE")
