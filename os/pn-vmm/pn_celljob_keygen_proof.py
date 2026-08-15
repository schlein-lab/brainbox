#!/usr/bin/env python3

import subprocess, threading, socket, os, sys, time, tempfile

D = os.path.dirname(os.path.realpath(__file__)); os.chdir(D)
BIN = "./target/release/pn-vmm"; KERNEL = "kernel/vmlinux.bin"; INITRD = "kernel/initramfs-cell.cpio"
BASE = "kernel/base-python.img"; DELTA = "kernel/delta-proof.img"
CID = 5
SOCK = os.path.join(tempfile.gettempdir(), "pn-celljob-proof.sock")
MARK = "__PN_RC__"; TIMEOUT = 60

subprocess.run(["truncate", "-s", "64M", DELTA], check=True)
stg = tempfile.mkdtemp()
os.makedirs(os.path.join(stg, "upper")); os.makedirs(os.path.join(stg, "work"))
open(os.path.join(stg, "upper", "seed"), "wb").write(os.urandom(64))
subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q", "-d", stg, DELTA], check=True)

if os.path.exists(SOCK):
    os.unlink(SOCK)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); srv.bind(SOCK); srv.listen(1); srv.settimeout(TIMEOUT)
res = {"out": b"", "rc": None}

def portal():
    try:
        conn, _ = srv.accept()
    except socket.timeout:
        print("!! VMM never connected to the seat socket"); return
    conn.settimeout(TIMEOUT); buf = b""
    while b"PN_SEAT_READY" not in buf:
        try:
            d = conn.recv(4096)
        except socket.timeout:
            break
        if not d:
            break
        buf += d
    time.sleep(0.4)
    cmd = ("/bin/python3 /opt/pn/tools/pn-cell-sealed-run keygen; RC=$?; "
           "busybox ls -l /tmp/cell-keys.json; echo %s${RC}__\n" % MARK)
    conn.sendall(cmd.encode())
    acc = b""; t0 = time.time()
    while MARK.encode() not in acc and time.time() - t0 < TIMEOUT:
        try:
            d = conn.recv(4096)
        except socket.timeout:
            break
        if not d:
            break
        acc += d
    res["out"] = acc
    text = acc.decode(errors="replace")
    if MARK in text:
        try:
            res["rc"] = int(text.split(MARK)[1].split("__")[0])
        except (ValueError, IndexError):
            pass
    try:
        conn.sendall(b"busybox reboot -f\n")
    except OSError:
        pass
    time.sleep(0.5); conn.close()

threading.Thread(target=portal, daemon=True).start()
env = dict(os.environ)
env["PN_VMM_BLK"] = "%s,%s" % (BASE, DELTA); env["PN_VMM_VSOCK"] = str(CID); env["PN_VMM_VSOCK_SEAT"] = SOCK
p = subprocess.Popen([BIN, KERNEL, INITRD], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0, env=env)
serial = []

def sr():
    for raw in iter(p.stdout.readline, b""):
        serial.append(raw.decode(errors="replace"))

threading.Thread(target=sr, daemon=True).start()
try:
    p.wait(timeout=TIMEOUT + 20)
except subprocess.TimeoutExpired:
    p.kill()
if os.path.exists(SOCK):
    os.unlink(SOCK)

out = res["out"].decode(errors="replace"); sblob = "".join(serial)
print("===== stdout captured OVER VSOCK from inside the cell =====")
print(out.strip()[:900])
print("===== VERDICT (run-in-cell over vsock: keygen) =====")
checks = {
    "VMM bridged the vsock seat":          "PN_SEAT_READY" in out or "BRIDGE" in sblob,
    "cell seeded w/ host entropy":         "PN_CELL_CRNG_SEEDED" in sblob,
    "keygen emitted pubkeys over vsock":   ("cell_x_pub" in out and "cell_id_pub" in out),
    "keygen rc=0":                         res["rc"] == 0,
    "keystore present in-cell":            "cell-keys.json" in out,
}
for k, v in checks.items():
    print("  [%s] %s" % ("PASS" if v else "FAIL", k))
print("  rc =", res["rc"])
print("  RESULT:", "RUN-IN-CELL OVER VSOCK (keygen) PASS" if all(checks.values()) else "INCOMPLETE")
