#!/usr/bin/env python3

import subprocess, threading, socket, os, sys, time, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import pn_session_cells as sc

D = os.path.dirname(os.path.realpath(__file__)); os.chdir(D)
BIN = "./target/release/pn-vmm"; KERNEL = "kernel/vmlinux.bin"; INITRD = "kernel/initramfs-cell.cpio"
BASE = "kernel/base-python.img"; CID = 11; TIMEOUT = 70
KSTORE = "/var/lib/brainbox-cell/keys.json"
KEYGEN = "BRAINBOX_CELL_KEYS=%s /bin/python3 /opt/pn/tools/pn-cell-sealed-run keygen" % KSTORE
MARK = "__PN_RC__"

def ensure_vol(path):
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["truncate", "-s", "64M", path], check=True)
    stg = tempfile.mkdtemp(); os.makedirs(stg + "/upper"); os.makedirs(stg + "/work")
    open(stg + "/upper/seed", "wb").write(os.urandom(64))
    subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q", "-d", stg, path], check=True)

def boot(delta, cmd):
    sock = tempfile.gettempdir() + "/pn-sc.sock"
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
                dd = conn.recv(4096)
            except socket.timeout:
                break
            if not dd:
                break
            buf += dd
        time.sleep(0.4)
        conn.sendall(("%s; RC=$?; echo %s${RC}__\n" % (cmd, MARK)).encode())
        acc = b""; t0 = time.time()
        while MARK.encode() not in acc and time.time() - t0 < TIMEOUT:
            try:
                dd = conn.recv(4096)
            except socket.timeout:
                break
            if not dd:
                break
            acc += dd
        res["out"] = acc
        t = acc.decode(errors="replace")
        if MARK in t:
            try:
                res["rc"] = int(t.split(MARK)[1].split("__")[0])
            except (ValueError, IndexError):
                pass
        try:
            conn.sendall(b"sync; busybox sync\n"); time.sleep(1.0); conn.sendall(b"busybox reboot -f\n")
        except OSError:
            pass
        time.sleep(0.4); conn.close()

    threading.Thread(target=portal, daemon=True).start()
    env = dict(os.environ)
    env["PN_VMM_BLK"] = "%s,%s" % (BASE, delta); env["PN_VMM_VSOCK"] = str(CID); env["PN_VMM_VSOCK_SEAT"] = sock
    p = subprocess.Popen([BIN, KERNEL, INITRD], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        p.wait(timeout=TIMEOUT + 15)
    except subprocess.TimeoutExpired:
        p.kill()
    if os.path.exists(sock):
        os.unlink(sock)
    out = res["out"].decode(errors="replace")
    x = cid = None
    for line in out.splitlines():
        if line.strip().startswith("{") and "cell_x_pub" in line:
            try:
                j = json.loads(line.strip()); x, cid = j.get("cell_x_pub"), j.get("cell_id_pub")
            except Exception:
                pass
    return res["rc"], x, cid

def keygen_session(reg, principal, session):
    r = reg.route(principal, session)
    ensure_vol(r["keystore_vol"])
    for attempt in range(4):
        rc, x, cid = boot(r["keystore_vol"], KEYGEN)
        if rc == 0 and x:
            return x, cid
        print("  %s/%s keygen attempt %d rc=%s retry" % (principal, session, attempt + 1, rc))
    return None, None

BASEDIR = tempfile.mkdtemp(prefix="pn-sc-proof-")
reg = sc.SessionCellRegistry(BASEDIR, vol_dir="kernel/session-vols")

for f in ("sc",):
    pass

print("=== provision two session-cells for principal-a ===")
reg.provision("principal-a", "proj1", autonomy=sc.L1)
reg.provision("principal-a", "proj2", autonomy=sc.L3)
print("  cells:", [c["cell"] for c in reg.list_live("principal-a")])

print("=== boot each session-cell, keygen in-cell (distinct per-session keys) ===")
x1, id1 = keygen_session(reg, "principal-a", "proj1")
x2, id2 = keygen_session(reg, "principal-a", "proj2")
print("  proj1 cell_x_pub:", x1)
print("  proj2 cell_x_pub:", x2)

print("=== lifecycle: suspend proj1 -> evict -> re-provision ===")
reg.suspend("principal-a", "proj1")
s_susp = reg.get("principal-a", "proj1")["state"]
reg.evict("principal-a", "proj1", reason="idle")
s_evict = reg.get("principal-a", "proj1")["state"]
route_after_evict = reg.route("principal-a", "proj1")
reg.provision("principal-a", "proj1")
s_reprov = reg.get("principal-a", "proj1")["state"]
notes = [json.loads(l) for l in open(reg.notify_path)] if os.path.exists(reg.notify_path) else []

_live = reg.list_live("principal-a")
_distinct_cells = len({c["cell"] for c in _live}) >= 2
checks = {
    "two distinct session-cells":        _distinct_cells,
    "proj1 got in-cell keys":            bool(x1 and id1),
    "proj2 got in-cell keys":            bool(x2 and id2),
    "per-session keys are DISTINCT":     bool(x1 and x2 and x1 != x2 and id1 != id2),
    "keys 32-byte hex":                  bool(x1 and len(x1) == 64 and x2 and len(x2) == 64),
    "suspend -> suspended":              s_susp == sc.SUSPENDED,
    "evict -> evicted":                  s_evict == sc.EVICTED,
    "poller route refuses evicted":      route_after_evict is None,
    "re-provision -> warm":              s_reprov == sc.WARM,
    "client notified evicted+reprov":    any(n["event"] == "evicted" for n in notes) and any(n["event"] == "reprovisioned" for n in notes),
    "autonomy per session (L1 vs L3)":   reg.get_autonomy("principal-a", "proj2") == sc.L3,
}
print("\n===== VERDICT (sessions als Zellen, end-to-end on pn-vmm) =====")
for k, v in checks.items():
    print("  [%s] %s" % ("PASS" if v else "FAIL", k))
print("  RESULT:", "SESSION-CELLS PASS" if all(checks.values()) else "INCOMPLETE")

for c in reg.list_live():
    for vk in ("keystore_vol", "work_vol"):
        p = c.get(vk)
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
sys.exit(0 if all(checks.values()) else 1)
