#!/usr/bin/env python3

import subprocess, threading, socket, os, time, tempfile, sys

D = os.environ.get("PN_VMM_HOME", os.path.dirname(os.path.realpath(__file__))); os.chdir(D)
BIN = os.environ.get("PN_VMM_BIN", "./target/release/pn-vmm")
KERNEL = "kernel/vmlinux.bin"; INITRD = "kernel/initramfs-cell.cpio"; BASE = "kernel/base-python.img"
DELTA = os.path.join(tempfile.gettempdir(), "delta-readopt.img")
CID = "13"
SEAT = os.path.join(tempfile.gettempdir(), "pn-readopt-seat.sock")
ADOPT = os.path.join(tempfile.gettempdir(), "pn-readopt-adopt.sock")
TOKEN = os.environ.get("PN_VMM_ADOPT_TOKEN", "readopt-test-token-0xC0FFEE")
MARK1 = "__PN_A_DONE__"; MARK2 = "__PN_B_DONE__"; TIMEOUT = 75

for s in (SEAT, ADOPT, DELTA):
    if os.path.exists(s):
        os.unlink(s)
subprocess.run(["truncate", "-s", "64M", DELTA], check=True)
stg = tempfile.mkdtemp(); os.makedirs(os.path.join(stg, "upper")); os.makedirs(os.path.join(stg, "work"))
open(os.path.join(stg, "upper", "seed"), "wb").write(os.urandom(64))
subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q", "-d", stg, DELTA], check=True)

srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); srv.bind(SEAT); srv.listen(1); srv.settimeout(TIMEOUT)
res = {"ready": False, "echo1": b"", "echo2": b"", "connectB": None, "rebooted_between": False}

def drain_until(conn, needle, tmax):
    acc = b""; t0 = time.time()
    while needle.encode() not in acc and time.time() - t0 < tmax:
        try:
            d = conn.recv(4096)
        except socket.timeout:
            break
        if not d:
            break
        acc += d
    return acc

def portal():
    try:
        connA, _ = srv.accept()
    except socket.timeout:
        print("!! phase A: no seat connect"); return
    connA.settimeout(TIMEOUT)
    buf = drain_until(connA, "PN_SEAT_READY", TIMEOUT)
    res["ready"] = b"PN_SEAT_READY" in buf
    time.sleep(0.4)
    connA.sendall(("echo PN_A_ECHO_OK uid=$(busybox id -u); echo %s\n" % MARK1).encode())
    res["echo1"] = drain_until(connA, MARK1, TIMEOUT)

    try:
        connA.sendall(b"export PN_MARK=PNMARK_SURVIVE\n")
        time.sleep(0.3)
    except OSError:
        pass
    try:
        connA.close()
    except OSError:
        pass

    t0 = time.time(); connB = None; acked = False
    while time.time() - t0 < 25 and not acked:
        try:
            c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); c.settimeout(4); c.connect(ADOPT)
        except OSError:
            time.sleep(0.3); continue
        try:
            c.sendall((TOKEN + "\n").encode())
            if b"PNADOPTOK" in drain_until(c, "PNADOPTOK", 4):
                connB = c; acked = True; break
        except OSError:
            pass
        try: c.close()
        except OSError: pass
        time.sleep(0.3)
    res["connectB"] = acked
    if not acked:
        print("!! phase B: adopt handshake failed"); return
    connB.settimeout(TIMEOUT)
    time.sleep(0.3)

    connB.sendall(("echo MARK=[$PN_MARK]; echo PN_B_ECHO_OK uid=$(busybox id -u); echo %s\n" % MARK2).encode())
    res["echo2"] = drain_until(connB, MARK2, TIMEOUT)
    res["rebooted_between"] = b"PNMARK_SURVIVE" not in res["echo2"]
    try:
        connB.sendall(b"busybox reboot -f\n"); time.sleep(0.5); connB.close()
    except OSError:
        pass

threading.Thread(target=portal, daemon=True).start()
env = dict(os.environ)
env["PN_VMM_BLK"] = "%s,%s" % (BASE, DELTA)
env["PN_VMM_VSOCK"] = CID
env["PN_VMM_VSOCK_SEAT"] = SEAT
env["PN_VMM_VSOCK_SEAT_ADOPT"] = ADOPT
p = subprocess.Popen([BIN, KERNEL, INITRD], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0, env=env)
serial = []
threading.Thread(target=lambda: [serial.append(r.decode(errors="replace")) for r in iter(p.stdout.readline, b"")], daemon=True).start()
try:
    p.wait(timeout=TIMEOUT + 30)
except subprocess.TimeoutExpired:
    p.kill()
for s in (SEAT, ADOPT):
    if os.path.exists(s):
        os.unlink(s)

e1 = res["echo1"].decode(errors="replace"); e2 = res["echo2"].decode(errors="replace")
seat_log = "".join(serial)
reconnected = "re-adopted" in seat_log
ok = (res["ready"] and "PN_A_ECHO_OK uid=0" in e1
      and res["connectB"] and "PN_B_ECHO_OK uid=0" in e2
      and not res["rebooted_between"])
print("READY=%s ECHO1=%r" % (res["ready"], e1.strip()[:120]))
print("CONNECT_B=%s ECHO2=%r" % (res["connectB"], e2.strip()[:160]))
print("REBOOTED_BETWEEN=%s (must be False)  SERIAL_SAW_RECONNECT=%s" % (res["rebooted_between"], reconnected))
print("READOPT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
