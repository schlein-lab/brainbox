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

RUNTIME_ROOT = os.path.expanduser("~/.local/share/brainarbeit/runtimes/codex")

_TRIPLE_BY_MACHINE = {
    "x86_64": "x86_64-unknown-linux-musl",
    "aarch64": "aarch64-unknown-linux-musl",
    "arm64": "aarch64-unknown-linux-musl",
}

def _host_triple():
    return _TRIPLE_BY_MACHINE.get(os.uname().machine)

def _resolve_codex_pkg_root():

    cand = shutil.which("codex") or os.path.expanduser("~/.local/bin/codex")
    try:
        real = os.path.realpath(cand)
        root = os.path.dirname(os.path.dirname(real))
        if os.path.exists(os.path.join(root, "package.json")):
            return root
    except OSError:
        pass
    for g in glob.glob(os.path.expanduser("~/.npm-global/lib/node_modules/@openai/codex")):
        return g
    return None

def detect_vendor_tree(explicit=None):

    if explicit:
        if not os.path.isdir(explicit):
            sys.exit("--vendor is not a directory: %s" % explicit)
        return os.path.realpath(explicit)
    root = _resolve_codex_pkg_root()
    if not root:
        sys.exit("could not locate the @openai/codex npm package — pass --vendor <triple-dir>")

    pkgs = glob.glob(os.path.join(root, "node_modules", "@openai", "*", "vendor", "*", "codex-package.json"))
    pkgs += glob.glob(os.path.join(root, "vendor", "*", "codex-package.json"))
    triple = _host_triple()
    chosen = None
    for p in pkgs:
        d = os.path.dirname(p)
        if triple and os.path.basename(d) == triple:
            chosen = d
            break
        chosen = chosen or d
    if not chosen:
        sys.exit("no codex vendor tree (codex-package.json) found under %s — pass --vendor" % root)
    return os.path.realpath(chosen)

def host_smoke(tree):

    binp = os.path.join(tree, "codex", "bin", "codex")
    print("PN_SMOKE_BEGIN", flush=True)
    if not (os.path.exists(binp) and os.access(binp, os.X_OK)):
        print("staged codex binary missing/not-exec:", binp)
        print("PN_SMOKE_FAIL")
        return None
    try:
        r = subprocess.run([binp, "--version"], capture_output=True, text=True, timeout=60)
    except Exception as e:
        print("codex --version raised:", e)
        print("PN_SMOKE_FAIL")
        return None
    ver = (r.stdout or "").strip() or (r.stderr or "").strip()
    print("rc=%d version=%r" % (r.returncode, ver[:120]))
    ok = r.returncode == 0 and any(ch.isdigit() for ch in ver)
    print("PN_SMOKE_%s" % ("PASS" if ok else "FAIL"))
    return ver if ok else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vendor", default=os.environ.get("CODEX_VENDOR_TREE"),
                    help="codex <target-triple> vendor dir to stage (default: auto-detect from PATH)")
    ap.add_argument("--version", default=os.environ.get("CODEX_RT_VERSION", time.strftime("c1-%Y%m%d")))
    ap.add_argument("--no-flip", action="store_true",
                    help="build the versioned dir but do NOT flip `current` (promote later)")
    args = ap.parse_args()

    vendor = detect_vendor_tree(args.vendor)
    print("[bake] codex vendor tree:", vendor, flush=True)

    stg = tempfile.mkdtemp(prefix="codex-rt-")
    try:
        tree = os.path.join(stg, "tree")
        os.makedirs(tree)

        subprocess.run("cp -a %s %s/codex" % (subprocess.list2cmdline([vendor]), tree),
                       shell=True, check=True)

        ca_bundled = None
        for ca in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
                   "/etc/ssl/cert.pem"):
            if os.path.exists(ca):
                shutil.copy(ca, os.path.join(tree, "codex", "ca-certificates.crt"))
                ca_bundled = ca
                break
        print("[bake] CA bundle:", ca_bundled or "NONE FOUND (TLS will fail in-cell!)", flush=True)

        cli_ver = host_smoke(tree)
        if not cli_ver:
            print("=> not writing image; fix the codex install above and re-run.")
            sys.exit(2)

        du = subprocess.run(["du", "-sb", tree], capture_output=True, text=True)
        tree_bytes = int(du.stdout.split()[0])
        img_mb = max(512, int(math.ceil(tree_bytes / 1048576.0 * 1.35)) + 96)
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
            "artifact": "codex-runtime",
            "version": args.version,
            "sha256": h,
            "size_bytes": size,
            "img_mb": img_mb,
            "codex_cli_version": cli_ver,
            "source_triple": os.path.basename(vendor),
            "ca_bundle_source": ca_bundled,

            "guest_paths": {"CODEX_BIN": "/work/codex/bin/codex",
                            "CODEX_PATH_DIR": "/work/codex/codex-path",
                            "CODEX_CA": "/work/codex/ca-certificates.crt"},
            "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        json.dump(manifest, open(os.path.join(outdir, "manifest.json"), "w"), indent=1)

        if not args.no_flip:
            cur_tmp = os.path.join(RUNTIME_ROOT, "current.tmp")
            try:
                os.remove(cur_tmp)
            except OSError:
                pass
            os.symlink(args.version, cur_tmp)
            os.replace(cur_tmp, os.path.join(RUNTIME_ROOT, "current"))

        print("CODEX_RUNTIME_BUILT img=%s sha=%s size=%d version=%s cli=%r flipped=%s"
              % (img, h[:16], size, args.version, cli_ver, not args.no_flip))
    finally:
        shutil.rmtree(stg, ignore_errors=True)

if __name__ == "__main__":
    main()
