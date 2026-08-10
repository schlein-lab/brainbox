#!/usr/bin/env python3

import argparse
import glob
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.realpath(__file__))
LOCK = os.path.join(HERE, "biomni-runtime.lock")
ENTRY_SRC = os.path.join(HERE, "biomni_entry.py")

PY = "/usr/bin/python3.12"
PY_ABI = "cp312-x86_64-linux-gnu"
DEFAULT_VENV = "/tmp/biomni-venv"
RUNTIME_ROOT = os.path.expanduser("~/.local/share/brainarbeit/runtimes/biomni")

LIBS = ["libstdc++.so.6", "libgcc_s.so.1", "libgomp.so.1",
        "libgfortran.so.5", "libquadmath.so.0", "libz.so.1"]
LIBDIRS = ["/usr/lib/x86_64-linux-gnu", "/lib/x86_64-linux-gnu"]

SMOKE_IMPORTS = "import biomni.agent, numpy, pandas, langchain_anthropic"

def resolve_lib(name):
    for d in LIBDIRS:
        for hit in glob.glob(os.path.join(d, name + "*")):
            return os.path.realpath(hit)
    return None

def bootstrap_venv(dest):

    print("[bootstrap] creating venv from lock ->", dest, flush=True)
    subprocess.run([PY, "-m", "venv", dest], check=True)

    subprocess.run([os.path.join(dest, "bin", "pip"), "install",
                    "--no-cache-dir", "--no-deps", "-r", LOCK], check=True)
    return os.path.join(dest, "lib", "python3.12", "site-packages")

def find_site(venv):
    site = os.path.join(venv, "lib", "python3.12", "site-packages")
    if not os.path.isdir(site):
        sys.exit("no site-packages under %s" % venv)
    return site

def host_smoke(tree):

    site = os.path.join(tree, "biomni-site")
    libs = os.path.join(tree, "biomni-libs")
    env = {
        "PATH": "/usr/bin:/bin",
        "LD_LIBRARY_PATH": libs,
        "ANTHROPIC_API_KEY": "sk-DUMMY",
        "PYTHONNOUSERSITE": "1",
    }
    code = ("import sys; sys.path.insert(0, %r); %s; print('BIOMNI_IMPORT_OK')"
            % (site, SMOKE_IMPORTS))
    print("PN_SMOKE_BEGIN", flush=True)
    r = subprocess.run([PY, "-S", "-c", code], env=env, capture_output=True, text=True)
    print("rc=%d" % r.returncode)
    print("stdout:", r.stdout.strip()[:600])
    if r.stderr.strip():
        print("stderr:", r.stderr.strip()[-1500:])
    ok = r.returncode == 0 and "BIOMNI_IMPORT_OK" in r.stdout
    print("PN_SMOKE_%s" % ("PASS" if ok else "FAIL"))
    return ok

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venv", default=os.environ.get("BIOMNI_VENV", DEFAULT_VENV),
                    help="existing venv to bake from (default: proven /tmp/biomni-venv)")
    ap.add_argument("--bootstrap", action="store_true",
                    help="recreate the venv from biomni-runtime.lock instead (reproducible)")
    ap.add_argument("--version", default=os.environ.get("BIOMNI_RT_VERSION",
                    time.strftime("e1-%Y%m%d")))
    ap.add_argument("--no-flip", action="store_true",
                    help="build the versioned dir but do NOT flip `current` (promote later)")
    args = ap.parse_args()

    if not os.path.exists(LOCK):
        sys.exit("missing lock: %s" % LOCK)
    if not os.path.exists(ENTRY_SRC):
        sys.exit("missing entrypoint: %s" % ENTRY_SRC)

    stg = tempfile.mkdtemp(prefix="biomni-rt-")
    try:
        if args.bootstrap:
            site = bootstrap_venv(os.path.join(stg, "venv"))
        else:
            site = find_site(args.venv)
            print("[bake] source site-packages:", site, flush=True)

        tree = os.path.join(stg, "tree")
        os.makedirs(os.path.join(tree, "biomni-site"))
        os.makedirs(os.path.join(tree, "biomni-libs"))
        subprocess.run("cp -a %s/. %s/biomni-site/" % (site, tree), shell=True, check=True)
        for name in LIBS:
            p = resolve_lib(name)
            if not p:
                sys.exit("missing host lib: %s" % name)
            shutil.copy(p, os.path.join(tree, "biomni-libs", name))
        print("staged libs:", sorted(os.listdir(os.path.join(tree, "biomni-libs"))), flush=True)

        if not host_smoke(tree):
            print("=> not writing image; fix deps above and re-run.")
            sys.exit(2)

        du = subprocess.run(["du", "-sb", tree], capture_output=True, text=True)
        tree_bytes = int(du.stdout.split()[0])
        img_mb = max(512, int(math.ceil(tree_bytes / 1048576.0 * 1.6)) + 64)
        print("tree=%dMB -> img=%dMB" % (tree_bytes // 1048576, img_mb), flush=True)

        outdir = os.path.join(RUNTIME_ROOT, args.version)
        os.makedirs(outdir, exist_ok=True)
        img = os.path.join(outdir, "runtime.img")
        if os.path.exists(img):
            os.unlink(img)
        subprocess.run(["truncate", "-s", "%dM" % img_mb, img], check=True)
        subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q", "-d", tree, img], check=True)

        h = hashlib.sha256(open(img, "rb").read()).hexdigest()
        size = os.path.getsize(img)
        manifest = {
            "artifact": "biomni-runtime",
            "version": args.version,
            "sha256": h,
            "python_abi": PY_ABI,
            "size_bytes": size,
            "img_mb": img_mb,
            "lock_sha256": hashlib.sha256(open(LOCK, "rb").read()).hexdigest(),
            "source": "bootstrap-lock" if args.bootstrap else ("venv:" + args.venv),
            "guest_paths": {"PYTHONPATH": "/work/biomni-site",
                            "LD_LIBRARY_PATH": "/work/biomni-libs"},
            "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        json.dump(manifest, open(os.path.join(outdir, "manifest.json"), "w"), indent=1)
        shutil.copy(LOCK, os.path.join(outdir, "biomni-runtime.lock"))
        shutil.copy(ENTRY_SRC, os.path.join(outdir, "biomni_entry.py"))

        if not args.no_flip:
            cur_tmp = os.path.join(RUNTIME_ROOT, "current.tmp")
            try:
                os.remove(cur_tmp)
            except OSError:
                pass
            os.symlink(args.version, cur_tmp)
            os.replace(cur_tmp, os.path.join(RUNTIME_ROOT, "current"))

        print("BIOMNI_RUNTIME_BUILT img=%s sha=%s size=%d version=%s flipped=%s"
              % (img, h[:16], size, args.version, not args.no_flip))
    finally:
        shutil.rmtree(stg, ignore_errors=True)

if __name__ == "__main__":
    main()
