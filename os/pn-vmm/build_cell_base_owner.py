#!/usr/bin/env python3

import os, shutil, subprocess, glob
import platform, sysconfig

MULTIARCH = sysconfig.get_config_var("MULTIARCH") or "%s-linux-gnu" % platform.machine()

D = os.path.dirname(os.path.realpath(__file__)); os.chdir(D)
PYBASE = "kernel/_pybase"; OWNER = "kernel/_ownerbase"; IMG = "kernel/base-owner.img"; SIZE = "640M"
CLAUDE = os.path.realpath(os.path.expanduser("~/.local/bin/claude"))
CTX = os.path.expanduser("~/phantGNOME/docs/PHANTOM-LLM-CONTEXT.md")
BINS = ["/usr/bin/bash", "/usr/bin/tmux"]

def ldd_deps(path):
    try:
        out = subprocess.run(["ldd", path], capture_output=True, text=True).stdout
    except Exception:
        return []
    deps = []
    for line in out.splitlines():
        if "=>" in line:
            p = line.split("=>", 1)[1].strip().split(" ")[0]
            if p.startswith("/") and os.path.exists(p):
                deps.append(p)
    return deps

def main():
    assert os.path.isdir(PYBASE), "need kernel/_pybase -> run build_cell_base_python.py first"
    if os.path.exists(OWNER):
        shutil.rmtree(OWNER)
    shutil.copytree(PYBASE, OWNER, symlinks=True)

    for b in BINS:
        if os.path.exists(b):
            dst = f"{OWNER}/bin/{os.path.basename(b)}"; shutil.copy(b, dst); os.chmod(dst, 0o755)

    os.makedirs(f"{OWNER}/opt/claude", exist_ok=True)
    shutil.copy(CLAUDE, f"{OWNER}/opt/claude/claude"); os.chmod(f"{OWNER}/opt/claude/claude", 0o755)
    link = f"{OWNER}/bin/claude"
    if os.path.lexists(link):
        os.remove(link)
    os.symlink("/opt/claude/claude", link)

    os.makedirs(f"{OWNER}/opt/pn/ctx", exist_ok=True)
    if os.path.exists(CTX):
        shutil.copy(CTX, f"{OWNER}/opt/pn/ctx/PHANTOM-LLM-CONTEXT.md")

    os.makedirs(f"{OWNER}/etc", exist_ok=True)
    open(f"{OWNER}/etc/passwd", "w").write("root:x:0:0:root:/root:/bin/bash\n")
    open(f"{OWNER}/etc/group", "w").write("root:x:0:\n")

    GCONV = "/usr/lib/%s/gconv" % MULTIARCH
    if os.path.isdir(GCONV):
        shutil.copytree(GCONV, f"{OWNER}/lib/gconv", symlinks=True)
        ip = f"{OWNER}/sbin/init"; t = open(ip).read()
        if "GCONV_PATH" not in t:
            open(ip, "w").write(t.replace("export LC_ALL=C.UTF-8\n",
                                          "export LC_ALL=C.UTF-8\nexport GCONV_PATH=/lib/gconv\n"))

    LA = "/usr/lib/locale/locale-archive"
    if os.path.exists(LA):
        os.makedirs(f"{OWNER}/usr/lib/locale", exist_ok=True)
        shutil.copy(LA, f"{OWNER}/usr/lib/locale/locale-archive")

    seen = set(os.path.basename(x) for x in glob.glob(f"{OWNER}/lib/*.so*"))
    queue = [f"{OWNER}/opt/claude/claude", f"{OWNER}/bin/bash", f"{OWNER}/bin/tmux"] + glob.glob(f"{OWNER}/lib/*.so*")
    added = []
    while queue:
        t = queue.pop()
        for dep in ldd_deps(t):
            name = os.path.basename(dep)
            if name in seen:
                continue
            seen.add(name)
            dst = f"{OWNER}/lib/{name}"
            if not os.path.exists(dst):
                shutil.copy(dep, dst); added.append(name); queue.append(dst)
    print("closure added:", " ".join(sorted(added)) or "(none)")
    sz = subprocess.run(["du", "-sh", OWNER], capture_output=True, text=True).stdout.split()[0]
    print("owner staging size:", sz)

    subprocess.run(["truncate", "-s", SIZE, IMG], check=True)
    subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q", "-d", OWNER, IMG], check=True)
    print("PN_OWNER_IMAGE_BUILT", IMG, SIZE)

if __name__ == "__main__":
    main()
