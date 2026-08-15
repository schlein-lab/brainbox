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

RUNTIME_ROOT = os.path.expanduser("~/.local/share/brainarbeit/runtimes/agents")

def _find_gemini_pkg():

    env = os.environ.get("GEMINI_PKG_DIR")
    if env and os.path.isdir(env):
        return os.path.realpath(env)
    cand = shutil.which("gemini")
    if cand:
        try:
            real = os.path.realpath(cand)
            d = real
            for _ in range(6):
                d = os.path.dirname(d)
                if os.path.basename(d) == "gemini-cli" and os.path.exists(os.path.join(d, "package.json")):
                    return d
        except OSError:
            pass
    for pat in ("/usr/lib/node_modules/@google/gemini-cli",
                os.path.expanduser("~/.npm-global/lib/node_modules/@google/gemini-cli"),
                os.path.expanduser("~/.local/lib/node_modules/@google/gemini-cli")):
        for g in glob.glob(pat):
            return g
    return None

def _find_opencode_bin():

    env = os.environ.get("OPENCODE_BIN")
    if env and os.path.exists(env):
        return os.path.realpath(env)
    cand = shutil.which("opencode") or os.path.expanduser("~/.opencode/bin/opencode")
    if cand and os.path.exists(cand):
        real = os.path.realpath(cand)

        if real.endswith(".js"):
            for g in glob.glob(os.path.join(os.path.dirname(os.path.dirname(real)),
                                            "..", "opencode-linux-*", "bin", "opencode")):
                return os.path.realpath(g)
        return real
    return None

def _stage_lib(tree):

    libdir = os.path.join(tree, "agents", "lib")
    os.makedirs(libdir, exist_ok=True)
    staged = []
    for name in ("libstdc++.so.6", "libgcc_s.so.1"):
        for base in ("/lib/x86_64-linux-gnu", "/usr/lib/x86_64-linux-gnu", "/usr/lib64", "/lib64"):
            src = os.path.join(base, name)
            if os.path.exists(src):
                shutil.copy(os.path.realpath(src), os.path.join(libdir, name))
                staged.append(name)
                break
    return staged

def host_smoke(tree):

    node = os.path.join(tree, "agents", "node", "bin", "node")
    gem = os.path.join(tree, "agents", "gemini", "gemini.js")
    oc = os.path.join(tree, "agents", "opencode", "opencode")
    lib = os.path.join(tree, "agents", "lib")
    env = dict(os.environ, LD_LIBRARY_PATH=lib)
    print("PN_SMOKE_BEGIN", flush=True)
    gv = ov = None
    try:
        r = subprocess.run([node, gem, "--version"], capture_output=True, text=True, timeout=90, env=env)
        gv = (r.stdout or r.stderr or "").strip().splitlines()[-1] if (r.stdout or r.stderr) else ""
        print("gemini rc=%d version=%r" % (r.returncode, gv[:120]))
        if r.returncode != 0 or not any(c.isdigit() for c in gv):
            gv = None
    except Exception as e:
        print("gemini smoke raised:", e)
    try:
        r = subprocess.run([oc, "--version"], capture_output=True, text=True, timeout=60, env=env)
        ov = (r.stdout or r.stderr or "").strip().splitlines()[-1] if (r.stdout or r.stderr) else ""
        print("opencode rc=%d version=%r" % (r.returncode, ov[:120]))
        if r.returncode != 0 or not any(c.isdigit() for c in ov):
            ov = None
    except Exception as e:
        print("opencode smoke raised:", e)
    print("PN_SMOKE_%s" % ("PASS" if (gv and ov) else "FAIL"))
    return gv, ov

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=os.environ.get("AGENTS_RT_VERSION", time.strftime("a1-%Y%m%d")))
    ap.add_argument("--no-flip", action="store_true")
    args = ap.parse_args()

    node_bin = shutil.which("node")
    gem_pkg = _find_gemini_pkg()
    oc_bin = _find_opencode_bin()
    if not node_bin:
        sys.exit("node fehlt auf dem Host (apt install nodejs / nvm)")
    if not gem_pkg:
        sys.exit("@google/gemini-cli nicht gefunden — `npm i -g @google/gemini-cli` oder GEMINI_PKG_DIR setzen")
    if not oc_bin:
        sys.exit("opencode nicht gefunden — `npm i -g opencode-ai` (oder Install-Script) oder OPENCODE_BIN setzen")
    print("[bake] node:", node_bin)
    print("[bake] gemini pkg:", gem_pkg)
    print("[bake] opencode bin:", oc_bin, flush=True)

    stg = tempfile.mkdtemp(prefix="agents-rt-")
    try:
        tree = os.path.join(stg, "tree")
        agents = os.path.join(tree, "agents")
        os.makedirs(agents)

        os.makedirs(os.path.join(agents, "node", "bin"))
        shutil.copy(os.path.realpath(node_bin), os.path.join(agents, "node", "bin", "node"))
        os.chmod(os.path.join(agents, "node", "bin", "node"), 0o755)

        gdst = os.path.join(agents, "gemini")
        os.makedirs(gdst)
        shutil.copytree(gem_pkg, os.path.join(gdst, "gemini-cli"), symlinks=True)

        try:
            pj = json.load(open(os.path.join(gem_pkg, "package.json")))
            b = pj.get("bin")
            entry = b if isinstance(b, str) else (list(b.values())[0] if isinstance(b, dict) and b else "dist/index.js")
        except Exception:
            entry = "dist/index.js"
        with open(os.path.join(gdst, "gemini.js"), "w") as f:
            f.write("import('./gemini-cli/%s');\n" % entry.lstrip("./"))

        os.makedirs(os.path.join(agents, "opencode"))
        shutil.copy(oc_bin, os.path.join(agents, "opencode", "opencode"))
        os.chmod(os.path.join(agents, "opencode", "opencode"), 0o755)

        ocdeps = os.path.join(agents, "oc-deps")
        os.makedirs(ocdeps)
        r = subprocess.run(["npm", "install", "--prefix", ocdeps, "--no-audit", "--no-fund",
                            "@ai-sdk/openai-compatible"], capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not os.path.isdir(os.path.join(ocdeps, "node_modules")):
            sys.exit("npm install @ai-sdk/openai-compatible (vendoring) fehlgeschlagen:\n" +
                     (r.stderr or r.stdout)[-500:])
        print("[bake] oc-deps vendored:", len(os.listdir(os.path.join(ocdeps, "node_modules"))), "Pakete")

        libs = _stage_lib(tree)
        print("[bake] libs staged:", libs or "NONE (node wird in der Zelle scheitern!)")
        ca_bundled = None
        for ca in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/cert.pem"):
            if os.path.exists(ca):
                shutil.copy(ca, os.path.join(agents, "ca-certificates.crt"))
                ca_bundled = ca
                break
        print("[bake] CA bundle:", ca_bundled or "NONE FOUND (TLS wird in der Zelle scheitern!)", flush=True)

        gv, ov = host_smoke(tree)
        if not (gv and ov):
            print("=> Image wird NICHT geschrieben; Staging oben reparieren und neu bauen.")
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
        manifest = {
            "artifact": "agents-runtime", "version": args.version, "sha256": h,
            "size_bytes": os.path.getsize(img), "img_mb": img_mb,
            "gemini_cli_version": gv, "opencode_version": ov,
            "libs": libs, "ca_bundle_source": ca_bundled,
            "guest_paths": {"NODE": "/work/agents/node/bin/node",
                            "GEMINI": "/work/agents/gemini/gemini.js",
                            "OPENCODE": "/work/agents/opencode/opencode",
                            "LIB": "/work/agents/lib",
                            "CA": "/work/agents/ca-certificates.crt"},
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

        print("AGENTS_RUNTIME_BUILT img=%s sha=%s version=%s gemini=%r opencode=%r flipped=%s"
              % (img, h[:16], args.version, gv, ov, not args.no_flip))
    finally:
        shutil.rmtree(stg, ignore_errors=True)

if __name__ == "__main__":
    main()
