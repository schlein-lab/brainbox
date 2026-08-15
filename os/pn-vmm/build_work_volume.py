#!/usr/bin/env python3

import os, sys, shutil, subprocess, tempfile

def _confined_realpath(p, root):
    rp = os.path.realpath(p)
    rroot = os.path.realpath(root)
    return rp if (rp == rroot or rp.startswith(rroot + os.sep)) else None

def seed(grant_src, img, size_mb="256", inodes="4096"):
    grant_src = os.path.abspath(grant_src)
    seeddir = tempfile.mkdtemp(prefix="pn-workseed-")
    ind = os.path.join(seeddir, "in"); os.makedirs(ind)
    os.makedirs(os.path.join(seeddir, "out"))
    os.makedirs(os.path.join(seeddir, "outbox"))
    copied, skipped = 0, 0
    for dirpath, dirnames, filenames in os.walk(grant_src):
        rel = os.path.relpath(dirpath, grant_src)
        dst_dir = ind if rel == "." else os.path.join(ind, rel)
        os.makedirs(dst_dir, exist_ok=True)
        for d in list(dirnames):
            if os.path.islink(os.path.join(dirpath, d)) and _confined_realpath(os.path.join(dirpath, d), grant_src) is None:
                dirnames.remove(d); skipped += 1
        for f in filenames:
            src = os.path.join(dirpath, f)
            real = _confined_realpath(src, grant_src)
            if real is None or not os.path.isfile(real):
                skipped += 1; continue
            with open(real, "rb") as rf, open(os.path.join(dst_dir, f), "wb") as wf:
                shutil.copyfileobj(rf, wf)
            copied += 1
    open(os.path.join(seeddir, "GRANT.txt"), "w").write(
        "pn-cell working volume /work\n"
        "- /work/in     : granted inputs (resolved content — read them)\n"
        "- /work/out    : write results here; the host harvests them\n"
        "- /work/outbox : propose effects here (brick 4)\n")
    subprocess.run(["truncate", "-s", "%sM" % size_mb, img], check=True)
    subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q", "-N", str(inodes),
                    "-O", "^casefold,^encrypt,^inline_data", "-d", seeddir, img], check=True)
    shutil.rmtree(seeddir)
    print("PN_WORKVOL_SEEDED %s size=%sM inodes=%s copied=%d skipped=%d" % (img, size_mb, inodes, copied, skipped))

def inspect(img, path):
    r = subprocess.run(["debugfs", "-R", "ls -l %s" % path, img], capture_output=True, text=True)
    print(r.stdout + r.stderr)

def harvest(img, destdir):
    os.makedirs(destdir, exist_ok=True)
    cp = img + ".harvest"
    subprocess.run(["cp", "--reflink=auto", img, cp], check=True)
    for sub in ("out", "outbox"):
        subprocess.run(["debugfs", "-R", "rdump /%s %s" % (sub, destdir), cp], capture_output=True, text=True)
    os.remove(cp)
    print("PN_WORKVOL_HARVESTED %s -> %s (userspace debugfs, no loop-mount)" % (img, destdir))

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "seed":
        seed(*sys.argv[2:])
    elif cmd == "inspect":
        inspect(sys.argv[2], sys.argv[3])
    elif cmd == "harvest":
        harvest(sys.argv[2], sys.argv[3])
    else:
        print("usage: seed|inspect|harvest", file=sys.stderr); sys.exit(2)
