#!/usr/bin/env python3

import base64
import json
import os
import socket
import subprocess
import tempfile
import threading
import time

PN_VMM_HOME = os.environ.get("PN_VMM_HOME", os.path.expanduser("~/brainarbeit/os/pn-vmm"))
BIN = os.path.join(PN_VMM_HOME, "target", "release", "pn-vmm")
KERNEL = os.environ.get("PN_VMM_CELL_KERNEL", os.path.join(PN_VMM_HOME, "kernel", "vmlinux-rng.bin"))
INITRD = os.path.join(PN_VMM_HOME, "kernel", "initramfs-cell.cpio")
BASE = os.path.join(PN_VMM_HOME, "kernel", "base-python.img")

RUNNER_IN_CELL = "/opt/pn/tools/pn-cell-sealed-run"
KEYS_IN_CELL = "/var/lib/brainbox-cell/keys.json"
TIMEOUT = 75
MARK = "__PN_RC__"

def _boot_once(keystore_vol, cmd_in_cell, cid=13, seat_timeout=28, cmd_timeout=95):

    sock = os.path.join(tempfile.gettempdir(), "pn-cellrun-%d.sock" % cid)
    if os.path.exists(sock):
        os.unlink(sock)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock); srv.listen(1); srv.settimeout(seat_timeout)
    res = {"out": b"", "rc": None, "ready": False}
    done = threading.Event()

    def portal():
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            done.set(); return
        conn.settimeout(seat_timeout); buf = b""
        while b"PN_SEAT_READY" not in buf:
            try:
                d = conn.recv(4096)
            except socket.timeout:
                break
            if not d:
                break
            buf += d
        if b"PN_SEAT_READY" not in buf:
            done.set(); return
        res["ready"] = True
        time.sleep(0.3)
        conn.sendall(("%s; RC=$?; echo %s${RC}__\n" % (cmd_in_cell, MARK)).encode())
        conn.settimeout(cmd_timeout); acc = b""; t0 = time.time()
        while MARK.encode() not in acc and time.time() - t0 < cmd_timeout:
            try:
                d = conn.recv(65536)
            except socket.timeout:
                break
            if not d:
                break
            acc += d
        res["out"] = acc
        t = acc.decode(errors="replace")
        if MARK in t:
            try:
                res["rc"] = int(t.split(MARK)[1].split("__")[0])
            except (ValueError, IndexError):
                pass
        try:
            conn.sendall(b"sync; busybox sync\n"); time.sleep(0.8); conn.sendall(b"busybox reboot -f\n")
        except OSError:
            pass
        time.sleep(0.3); conn.close(); done.set()

    threading.Thread(target=portal, daemon=True).start()
    env = dict(os.environ)
    env["PN_VMM_BLK"] = "%s,%s" % (BASE, keystore_vol)
    env["PN_VMM_VSOCK"] = str(cid); env["PN_VMM_VSOCK_SEAT"] = sock
    p = subprocess.Popen([BIN, KERNEL, INITRD], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    done.wait(timeout=seat_timeout + cmd_timeout + 5)
    try:
        p.kill()
    except Exception:
        pass
    try:
        p.wait(timeout=5)
    except Exception:
        pass
    if os.path.exists(sock):
        os.unlink(sock)
    return res["out"].decode(errors="replace"), res["rc"], res["ready"]

def _boot(keystore_vol, cmd_in_cell, cid=13, timeout=TIMEOUT, tries=8):

    for i in range(tries):
        out, rc, ready = _boot_once(keystore_vol, cmd_in_cell, cid=cid)
        if ready and MARK in out:
            return out, rc
    return out, rc

def stage_runner(keystore_vol, runner_src, cid=13):

    b64 = base64.b64encode(open(runner_src, "rb").read()).decode()
    cmd = ("busybox mkdir -p /opt/pn/tools /var/lib/brainbox-cell; echo %s | base64 -d > %s; chmod +x %s; "
           "busybox ls -l %s && echo PN_STAGED_OK" % (b64, RUNNER_IN_CELL, RUNNER_IN_CELL, RUNNER_IN_CELL))
    out, rc = _boot(keystore_vol, cmd, cid=cid)
    return "PN_STAGED_OK" in out and rc == 0

def run_sealed_in_cell(keystore_vol, sealed_env, dev_x_pub, autonomy=1, cid=13):

    env_b64 = base64.b64encode(json.dumps(sealed_env).encode()).decode()

    seed = os.urandom(32).hex()
    warm = "busybox dd if=/dev/vda of=/dev/null bs=64k count=200 2>/dev/null; "
    cmd = (warm + "echo %s | base64 -d | PN_RNGSEED=%s BRAINBOX_CELL_KEYS=%s /bin/python3 %s run "
           "--device-x-pub %s --autonomy %d > /tmp/pn-res.json 2>/tmp/pn-res.err; echo PN_RES_START; "
           "busybox cat /tmp/pn-res.json; echo; echo PN_RES_END; echo PN_ERR_START; "
           "busybox cat /tmp/pn-res.err; echo PN_ERR_END"
           % (env_b64, seed, KEYS_IN_CELL, RUNNER_IN_CELL, dev_x_pub, int(autonomy)))
    out, rc = _boot(keystore_vol, cmd, cid=cid)
    if "PN_RES_START" in out and "PN_RES_END" in out:
        blob = out.split("PN_RES_START", 1)[1].split("PN_RES_END", 1)[0].strip()
        if blob:
            try:
                return json.loads(blob)
            except Exception:
                pass
    if "PN_ERR_START" in out:
        import sys
        err = out.split("PN_ERR_START", 1)[1].split("PN_ERR_END", 1)[0].strip()
        if err:
            sys.stderr.write("[run_sealed_in_cell] in-cell stderr: %s\n" % err[:400])
    return None
