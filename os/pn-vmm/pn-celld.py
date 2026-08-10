#!/usr/bin/env python3

import argparse, json, os, subprocess, sys, shutil, socket, threading, time, tempfile

PN_VMM_HOME = os.environ.get("PN_VMM_HOME", os.path.expanduser("~/brainarbeit/os/pn-vmm"))
STATE = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
                     "portioneer", "cells")
REG = os.path.join(STATE, "cells.json")

KERNEL = os.path.join(PN_VMM_HOME, "kernel", "vmlinux.bin")
BIN = os.path.join(PN_VMM_HOME, "target", "release", "pn-vmm")
BASE_IMG = os.path.join(PN_VMM_HOME, "kernel", "base.img")
INITRD_CELL = os.path.join(PN_VMM_HOME, "kernel", "initramfs-cell.cpio")
INITRD_CELL_DATA = os.path.join(PN_VMM_HOME, "kernel", "initramfs-cell-data.cpio")

STAGE_SH = os.path.join(PN_VMM_HOME, "pn-cell-stage.sh")
RUN_SH = os.path.join(PN_VMM_HOME, "pn-cell-run.sh")
LUKS_BUILD = os.path.join(PN_VMM_HOME, "build_cell_images_luks.py")

def _run(cmd, **kw):
    print("  +", " ".join(cmd))
    return subprocess.run(cmd, check=True, **kw)

def _load():
    if os.path.exists(REG):
        return json.load(open(REG))
    return {"cells": {}, "next_cid": 3}

def _save(reg):
    os.makedirs(STATE, exist_ok=True)
    tmp = REG + ".tmp"
    json.dump(reg, open(tmp, "w"), indent=2)
    os.replace(tmp, REG)

def _ensure_base():
    if not os.path.exists(BASE_IMG):
        print("[pn-celld] CAS base absent -- building it once (shared, read-only)")
        _run(["python3", "build_cell_images.py"], cwd=PN_VMM_HOME)

    if not os.path.exists(INITRD_CELL):
        _run(["python3", "build_initramfs_cell.py"], cwd=PN_VMM_HOME)

def _make_delta(path, size, encrypt, keyfile):

    if encrypt and os.path.exists(LUKS_BUILD):
        print("[pn-celld] building LUKS-encrypted delta via", os.path.basename(LUKS_BUILD))
        _run(["python3", LUKS_BUILD, "--delta-out", path, "--delta-size", size,
              "--keyfile", keyfile], cwd=PN_VMM_HOME)
        return "luks"
    if encrypt:

        sys.exit("[pn-celld] ABBRUCH: --encrypt angefordert, aber %s fehlt. Es wird KEIN "
                 "unverschluesselter Datentraeger angelegt. Entweder den LUKS-Bauer "
                 "bereitstellen oder ohne --encrypt anlegen." % os.path.basename(LUKS_BUILD))
    _run(["truncate", "-s", size, path])
    _run(["mke2fs", "-t", "ext4", "-F", "-q", path])
    return "plain"

def cmd_create(a):
    reg = _load()
    if a.cell in reg["cells"]:
        sys.exit("cell %r already exists" % a.cell)
    _ensure_base()
    cdir = os.path.join(STATE, a.cell)
    os.makedirs(cdir, exist_ok=True)
    cid = a.cid or reg["next_cid"]
    reg["next_cid"] = max(reg["next_cid"], cid + 1)

    delta = os.path.join(cdir, "delta.img")
    keyfile = os.path.join(cdir, "delta.key")
    dkind = _make_delta(delta, a.delta_size, a.encrypt, keyfile)

    data = None
    if a.client_data:

        data = os.path.join(cdir, "data.img")
        if os.path.exists(STAGE_SH):
            _run(["bash", STAGE_SH, a.client_data, data])
        else:
            _run(["mke2fs", "-t", "ext4", "-F", "-q", "-d", a.client_data, "-b", "1024", data,
                  str(max(8192, 2 * _dirkb(a.client_data)))])

    cell = {
        "tenant": a.tenant or a.cell, "cid": cid, "delta": delta, "delta_kind": dkind,
        "keyfile": keyfile if dkind == "luks" else None, "data": data,
        "mem": a.mem, "cpu": a.cpu, "io": a.io, "state": "stopped",
    }
    reg["cells"][a.cell] = cell
    _save(reg)
    print("[pn-celld] created cell %r: cid=%d delta=%s(%s) data=%s mem=%s cpu=%s"
          % (a.cell, cid, a.delta_size, dkind, "yes" if data else "no", a.mem, a.cpu))

def _dirkb(d):
    total = 0
    for root, _, files in os.walk(d):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total // 1024

def cmd_run(a):
    reg = _load()
    c = reg["cells"].get(a.cell) or sys.exit("no such cell %r" % a.cell)
    disks = [BASE_IMG, c["delta"]] + ([c["data"]] if c.get("data") else [])
    initrd = INITRD_CELL_DATA if c.get("data") else INITRD_CELL
    if not os.path.exists(initrd):
        _run(["python3", os.path.join(PN_VMM_HOME,
              "build_initramfs_cell_data.py" if c.get("data") else "build_initramfs_cell.py")],
             cwd=PN_VMM_HOME)
    env = dict(os.environ)
    env["PN_VMM_BLK"] = ",".join(disks)
    env["PN_VMM_VSOCK"] = str(c["cid"])
    env["PN_VMM_BIN"] = BIN

    if not a.no_quota and os.path.exists(RUN_SH):
        env["CELL_MEM_MAX"] = c["mem"]; env["CELL_CPU_QUOTA"] = c["cpu"]; env["CELL_IO_WEIGHT"] = str(c["io"])
        if a.parent:
            env["PN_CELL_PARENT"] = a.parent
        cmd = ["bash", RUN_SH, KERNEL, initrd]
        print("[pn-celld] cell %r under cgroup quota (mem=%s cpu=%s io=%s)" % (a.cell, c["mem"], c["cpu"], c["io"]))
    else:
        cmd = [BIN, KERNEL, initrd]
        print("[pn-celld] cell %r WITHOUT cgroup cage (%s)"
              % (a.cell, "--no-quota" if a.no_quota else "pn-cell-run.sh absent"))
    print("[pn-celld] cell %r: PN_VMM_BLK=%s PN_VMM_VSOCK=%d" % (a.cell, env["PN_VMM_BLK"], c["cid"]))
    os.execvpe(cmd[0], cmd, env)

def cmd_run_job(a):

    reg = _load()
    c = reg["cells"].get(a.cell) or sys.exit("no such cell %r" % a.cell)
    disks = [BASE_IMG, c["delta"]] + ([c["data"]] if c.get("data") else [])
    initrd = INITRD_CELL_DATA if c.get("data") else INITRD_CELL
    sock = os.path.join(tempfile.gettempdir(), "pn-celljob-%s.sock" % a.cell)
    if os.path.exists(sock):
        os.unlink(sock)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock); srv.listen(1); srv.settimeout(a.timeout)
    result = {"out": b"", "rc": None}
    MARK = "__PN_RC__"

    def portal():
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            return
        conn.settimeout(a.timeout)
        buf = b""
        while b"PN_SEAT_READY" not in buf:
            try:
                d = conn.recv(4096)
            except socket.timeout:
                break
            if not d:
                break
            buf += d
        time.sleep(0.3)

        conn.sendall(("%s; echo %s$?__\n" % (a.cmd, MARK)).encode())
        acc = b""
        t0 = time.time()
        while MARK.encode() not in acc and time.time() - t0 < a.timeout:
            try:
                d = conn.recv(4096)
            except socket.timeout:
                break
            if not d:
                break
            acc += d
        text = acc.decode(errors="replace")
        if MARK in text:
            pre, _, post = text.partition(MARK)
            result["out"] = pre.encode()
            try:
                result["rc"] = int(post.split("__")[0])
            except ValueError:
                result["rc"] = None
        else:
            result["out"] = acc
        try:
            conn.sendall(b"busybox reboot -f\n")
        except OSError:
            pass
        time.sleep(0.5); conn.close()

    th = threading.Thread(target=portal, daemon=True); th.start()
    env = dict(os.environ)
    env["PN_VMM_BLK"] = ",".join(disks); env["PN_VMM_VSOCK"] = str(c["cid"])
    env["PN_VMM_VSOCK_SEAT"] = sock; env["PN_VMM_BIN"] = BIN
    p = subprocess.Popen([BIN, KERNEL, initrd], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, env=env)
    try:
        p.wait(timeout=a.timeout + 20)
    except subprocess.TimeoutExpired:
        p.kill()
    th.join(timeout=5)
    os.unlink(sock) if os.path.exists(sock) else None
    sys.stdout.buffer.write(result["out"])
    sys.stdout.flush()
    print("\n[pn-celld] cell %r job rc=%s" % (a.cell, result["rc"]), file=sys.stderr)
    sys.exit(result["rc"] if isinstance(result["rc"], int) else 1)

def cmd_list(a):
    reg = _load()
    if not reg["cells"]:
        print("(no cells)"); return
    for cid, c in reg["cells"].items():
        print("  %-16s tenant=%-12s cid=%d delta=%s data=%s mem=%s cpu=%s"
              % (cid, c["tenant"], c["cid"], c["delta_kind"], "yes" if c.get("data") else "no",
                 c["mem"], c["cpu"]))

def cmd_destroy(a):
    reg = _load()
    if a.cell not in reg["cells"]:
        sys.exit("no such cell %r" % a.cell)
    del reg["cells"][a.cell]
    _save(reg)
    shutil.rmtree(os.path.join(STATE, a.cell), ignore_errors=True)
    print("[pn-celld] destroyed cell %r" % a.cell)

def main():
    p = argparse.ArgumentParser(prog="pn-celld", description="Brainarbeit per-tenant cell manager")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create"); c.add_argument("cell")
    c.add_argument("--tenant"); c.add_argument("--cid", type=int)
    c.add_argument("--mem", default="512M"); c.add_argument("--cpu", default="50%")
    c.add_argument("--io", type=int, default=50); c.add_argument("--delta-size", default="64M")
    c.add_argument("--client-data"); c.add_argument("--encrypt", action="store_true")
    c.set_defaults(fn=cmd_create)
    r = sub.add_parser("run"); r.add_argument("cell")
    r.add_argument("--no-quota", action="store_true", help="bypass the cgroup cage (tests)")
    r.add_argument("--parent", help="PN_CELL_PARENT cgroup to nest the cell under")
    r.set_defaults(fn=cmd_run)
    j = sub.add_parser("run-job"); j.add_argument("cell"); j.add_argument("--cmd", required=True)
    j.add_argument("--timeout", type=int, default=60); j.set_defaults(fn=cmd_run_job)
    l = sub.add_parser("list"); l.set_defaults(fn=cmd_list)
    d = sub.add_parser("destroy"); d.add_argument("cell"); d.set_defaults(fn=cmd_destroy)
    a = p.parse_args()
    a.fn(a)

if __name__ == "__main__":
    main()
