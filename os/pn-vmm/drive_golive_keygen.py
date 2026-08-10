#!/usr/bin/env python3

import subprocess, threading, socket, os, sys, time, tempfile, json

D = os.path.dirname(os.path.realpath(__file__)); os.chdir(D)
BIN = "./target/release/pn-vmm"; KERNEL = "kernel/vmlinux.bin"; INITRD = "kernel/initramfs-cell.cpio"
BASE = "kernel/base-python.img"
PRINCIPAL = sys.argv[1] if len(sys.argv) > 1 else "win-thin"
DELTA = "kernel/cell-%s-keystore.img" % PRINCIPAL
CID = 7
MARK = "__PN_RC__"; TIMEOUT = 70

def ensure_delta():

    if os.path.exists(DELTA):
        return False
    subprocess.run(["truncate", "-s", "64M", DELTA], check=True)
    stg = tempfile.mkdtemp()
    os.makedirs(os.path.join(stg, "upper")); os.makedirs(os.path.join(stg, "work"))
    open(os.path.join(stg, "upper", "seed"), "wb").write(os.urandom(64))
    subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q", "-d", stg, DELTA], check=True)
    return True

def boot(cmd_in_cell):

    sock = os.path.join(tempfile.gettempdir(), "pn-golive-keygen.sock")
    if os.path.exists(sock):
        os.unlink(sock)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); srv.bind(sock); srv.listen(1); srv.settimeout(TIMEOUT)
    res = {"out": b"", "rc": None}

    def portal():
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            return
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
        full = "%s; RC=$?; echo %s${RC}__\n" % (cmd_in_cell, MARK)
        conn.sendall(full.encode())
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
            conn.sendall(b"sync; busybox sync\n")
            time.sleep(1.2)
            conn.sendall(b"busybox reboot -f\n")
        except OSError:
            pass
        time.sleep(0.5); conn.close()

    threading.Thread(target=portal, daemon=True).start()
    env = dict(os.environ)
    env["PN_VMM_BLK"] = "%s,%s" % (BASE, DELTA)
    env["PN_VMM_VSOCK"] = str(CID); env["PN_VMM_VSOCK_SEAT"] = sock
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
    if os.path.exists(sock):
        os.unlink(sock)
    return res["out"].decode(errors="replace"), res["rc"], "".join(serial)

def parse_pub(out):
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{") and "cell_x_pub" in line:
            try:
                j = json.loads(line)
                return j.get("cell_x_pub"), j.get("cell_id_pub")
            except Exception:
                pass
    return None, None

KSTORE = "/var/lib/brainbox-cell/keys.json"
KEYGEN = "BRAINBOX_CELL_KEYS=%s /bin/python3 /opt/pn/tools/pn-cell-sealed-run keygen" % KSTORE
PUBKEYS = "BRAINBOX_CELL_KEYS=%s /bin/python3 /opt/pn/tools/pn-cell-sealed-run pubkeys" % KSTORE

def boot_retry(cmd, want_pub=True, tries=4):

    for i in range(1, tries + 1):
        out, rc, ser = boot(cmd)
        x, cid = parse_pub(out)
        ok = (rc == 0) and ((x and cid) if want_pub else True)
        if ok or rc is not None:
            return out, rc, ser, x, cid
        print("  attempt %d: boot stall (rc=%s, seat_ready=%s), retrying" %
              (i, rc, "PN_SEAT_READY" in out))
    return out, rc, ser, x, cid

fresh = ensure_delta()
print("keystore delta: %s (%s)" % (DELTA, "freshly created" if fresh else "REUSED (persistent)"))

out1, rc1, ser1, x1, id1 = boot_retry(KEYGEN, want_pub=True)
print("BOOT1 keygen: rc=%s cell_x_pub=%s cell_id_pub=%s" % (rc1, x1, id1))

out2, rc2, ser2, x2, id2 = boot_retry(PUBKEYS, want_pub=True)
print("BOOT2 pubkeys: rc=%s cell_x_pub=%s cell_id_pub=%s" % (rc2, x2, id2))

checks = {
    "boot1 keygen rc=0":                 rc1 == 0,
    "boot1 emitted cell pubkeys":        bool(x1 and id1),
    "boot2 pubkeys rc=0":                rc2 == 0,
    "keys PERSISTED across reboot":      bool(x2 and id2 and x1 == x2 and id1 == id2),
    "keys are 32-byte hex":              bool(x1 and len(x1) == 64 and id1 and len(id1) == 64),
}
print("\n===== VERDICT (§2 in-cell keygen + persistence) =====")
for k, v in checks.items():
    print("  [%s] %s" % ("PASS" if v else "FAIL", k))
ok = all(checks.values())
print("  RESULT:", "GOLIVE KEYGEN PASS" if ok else "INCOMPLETE")
if ok:
    print("PN_GOLIVE_CELL_X_PUB=%s" % x1)
    print("PN_GOLIVE_CELL_ID_PUB=%s" % id1)
sys.exit(0 if ok else 1)
