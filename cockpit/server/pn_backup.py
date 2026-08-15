#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HOME = os.path.expanduser("~")
PORTAL_DATA = os.environ.get("PN_PORTAL_DATA", os.path.join(HOME, ".local/share/brainbox-portal"))
PORTAL_CFG = os.environ.get("PN_PORTAL_CFG", os.path.join(HOME, ".config/brainbox-portal"))
PORTIONEER_DIR = os.environ.get("PN_PORTIONEER_DIR", os.path.join(HOME, ".local/share/portioneer"))
RELAY_DIR = os.path.join(PORTIONEER_DIR, "relay")
SHARES_DIR = os.environ.get("PN_SHARES_DIR", "/data/shares")
ENV_FILE = os.path.join(HOME, ".env")
POOLHOME_DIR = os.path.join(HOME, ".pn-poolhome")
LLMPOOL_DIR = os.path.join(HOME, ".llmpool")
USERS_DIR = os.path.join(PORTAL_DATA, "users")
DEST_DEFAULT = os.environ.get("PN_BACKUP_DEST", os.path.join(HOME, ".brainbox-backups", "state"))
SCHEMA = 2
PREFIX = "brainbox-state-"

DATA_EXCLUDE_DIRS = {
    "session-cells",
    "venv",
    "piper",
    "announce",
    "users",
    "static",
}

PORTIONEER_INCLUDE_SUBDIRS = {"relay"}
PORTIONEER_EXCLUDE_IN_RELAY = ()
CFG_EXCLUDE_SUFFIX = (".bak", ".presw", "-cookies.txt")

SQLITE_DBS = [
    (PORTAL_DATA, "jobs.db"), (PORTAL_DATA, "messages.db"), (PORTAL_DATA, "users.db"),
    (PORTIONEER_DIR, "queue.db"), (PORTIONEER_DIR, "acct.db"),
    (RELAY_DIR, "relay.db"),
]

_ROOT_SUB = {PORTAL_DATA: "portal-data", PORTAL_CFG: "config",
             PORTIONEER_DIR: "portioneer", RELAY_DIR: "portioneer/relay"}

def _sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)

def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def _box_identity():

    ident = {"lan_ip": None, "ca_fpr": None, "host": None}
    try:
        cfg = json.load(open(os.path.join(PORTAL_CFG, "config.json")))
        ident["lan_ip"] = cfg.get("lan_ip")
    except Exception:
        pass
    try:
        ident["host"] = os.uname().nodename
    except Exception:
        pass
    ca = os.path.join(PORTAL_CFG, "cert.pem")
    if os.path.exists(ca):
        try:
            ident["ca_fpr"] = _sha256(ca)[:16]
        except Exception:
            pass
    return ident

def _copy_tree(src, dst, exclude_dirs=(), exclude_suffix=()):

    skip_db = {name for _root, name in SQLITE_DBS}
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        if rel == ".":
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for fn in files:
            if fn in skip_db and os.path.dirname(os.path.join(root, fn)) in (PORTAL_DATA, RELAY_DIR, PORTIONEER_DIR):
                continue
            if any(fn.endswith(sfx) or sfx in fn for sfx in exclude_suffix):
                continue
            s = os.path.join(root, fn)
            if os.path.islink(s) or not os.path.isfile(s):
                continue
            d = os.path.join(dst, os.path.relpath(s, src))
            os.makedirs(os.path.dirname(d), exist_ok=True)
            try:
                shutil.copy2(s, d)
            except OSError:
                pass

def _sqlite_backup(src_db, dst_db):

    if not os.path.exists(src_db):
        return False
    os.makedirs(os.path.dirname(dst_db), exist_ok=True)
    r = _sh(["sqlite3", src_db, ".backup '%s'" % dst_db])
    if r.returncode != 0 or not os.path.exists(dst_db):

        r2 = _sh(["sqlite3", src_db, "VACUUM INTO '%s'" % dst_db])
        if r2.returncode != 0 or not os.path.exists(dst_db):
            try:
                shutil.copy2(src_db, dst_db)
            except OSError:
                return False
    return True

def make_bundle(dest=DEST_DEFAULT, label="", include_shares=False, include_llm=False):

    ts = int(time.time())
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(ts))
    os.makedirs(dest, exist_ok=True)
    staging = tempfile.mkdtemp(prefix="pnbak-")
    try:
        root = os.path.join(staging, "bundle")

        _copy_tree(PORTAL_CFG, os.path.join(root, "config"), exclude_suffix=CFG_EXCLUDE_SUFFIX)

        _copy_tree(PORTAL_DATA, os.path.join(root, "portal-data"),
                   exclude_dirs=DATA_EXCLUDE_DIRS, exclude_suffix=(".lock",))

        if os.path.isdir(RELAY_DIR):
            _copy_tree(RELAY_DIR, os.path.join(root, "portioneer", "relay"),
                       exclude_suffix=("-wal", "-shm", ".lock"))

        db_ok = {}
        for base, name in SQLITE_DBS:
            sub = _ROOT_SUB.get(base, "portal-data")
            db_ok[name] = _sqlite_backup(os.path.join(base, name), os.path.join(root, sub, name))

        if os.path.isfile(ENV_FILE):
            os.makedirs(os.path.join(root, "home"), exist_ok=True)
            try:
                shutil.copy2(ENV_FILE, os.path.join(root, "home", ".env"))
            except OSError:
                pass

        if include_llm:
            for src, sub in ((POOLHOME_DIR, os.path.join("home", ".pn-poolhome")),
                             (LLMPOOL_DIR, os.path.join("home", ".llmpool")),
                             (USERS_DIR, os.path.join("portal-data", "users"))):
                if os.path.isdir(src):
                    _copy_tree(src, os.path.join(root, sub))

        if include_shares and os.path.isdir(SHARES_DIR):
            _copy_tree(SHARES_DIR, os.path.join(root, "shares"))

        files = []
        for r2, _d, fs in os.walk(root):
            for fn in fs:
                p = os.path.join(r2, fn)
                files.append({"path": os.path.relpath(p, root),
                              "size": os.path.getsize(p), "sha256": _sha256(p)})
        manifest = {"schema": SCHEMA, "kind": "brainbox-state", "created": ts, "stamp": stamp,
                    "label": label[:80], "identity": _box_identity(),
                    "includes_shares": bool(include_shares), "includes_llm": bool(include_llm),
                    "excluded_dirs": sorted(DATA_EXCLUDE_DIRS),
                    "sqlite_consistent": db_ok, "files": files,
                    "total_bytes": sum(f["size"] for f in files), "n_files": len(files)}
        with open(os.path.join(root, "MANIFEST.json"), "w") as f:
            json.dump(manifest, f, indent=1)

        out = os.path.join(dest, "%s%s.tar.zst" % (PREFIX, stamp))
        tar_tmp = out + ".building"
        r = _sh(["tar", "-C", staging, "-I", "zstd -12 -T0", "-cf", tar_tmp, "bundle"])
        if r.returncode != 0:
            _sh(["rm", "-f", tar_tmp])
            r = _sh(["tar", "-C", staging, "-I", "zstd", "-cf", tar_tmp, "bundle"])
        if r.returncode != 0 or not os.path.exists(tar_tmp):
            return {"ok": False, "error": "tar/zstd fehlgeschlagen: %s" % (r.stderr or "")[:300]}
        os.replace(tar_tmp, out)
        size = os.path.getsize(out)
        return {"ok": True, "path": out, "stamp": stamp, "created": ts, "bytes": size,
                "n_files": manifest["n_files"], "sqlite_consistent": db_ok,
                "identity": manifest["identity"]}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (e.__class__.__name__, e)}
    finally:
        shutil.rmtree(staging, ignore_errors=True)

def list_bundles(dest=DEST_DEFAULT):
    out = []
    try:
        for fn in sorted(os.listdir(dest), reverse=True):
            if fn.startswith(PREFIX) and fn.endswith(".tar.zst"):
                p = os.path.join(dest, fn)
                st = os.stat(p)
                out.append({"path": p, "name": fn, "bytes": st.st_size,
                            "created": int(st.st_mtime)})
    except OSError:
        pass
    return out

def _tar_extract(bundle, dest_dir):

    r = _sh(["tar", "-I", "zstd", "-xf", bundle, "-C", dest_dir])
    if r.returncode != 0:
        raise RuntimeError("tar-extract fehlgeschlagen: %s" % (r.stderr or "")[:200])

def _read_manifest(bundle):

    r = subprocess.run(["tar", "-I", "zstd", "-xOf", bundle, "bundle/MANIFEST.json"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    try:
        return json.loads(r.stdout.decode("utf-8"))
    except Exception:
        return None

def verify_bundle(bundle):
    try:
        man = _read_manifest(bundle)
        if not man:
            return {"ok": False, "error": "kein MANIFEST.json im Bundle"}

        staging = tempfile.mkdtemp(prefix="pnverify-")
        try:
            _tar_extract(bundle, staging)
            root = os.path.join(staging, "bundle")
            bad = []
            for f in man.get("files", []):
                p = os.path.join(root, f["path"])
                if not os.path.exists(p) or _sha256(p) != f["sha256"]:
                    bad.append(f["path"])
            return {"ok": not bad, "stamp": man.get("stamp"), "created": man.get("created"),
                    "identity": man.get("identity"), "n_files": man.get("n_files"),
                    "sqlite_consistent": man.get("sqlite_consistent"),
                    "mismatched": bad[:20], "n_mismatched": len(bad)}
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (e.__class__.__name__, e)}

def restore_bundle(bundle, apply=False):

    v = verify_bundle(bundle)
    if not v.get("ok"):
        return {"ok": False, "error": "Bundle-Verifikation fehlgeschlagen", "detail": v}
    targets = {"config": PORTAL_CFG, "portal-data": PORTAL_DATA,
               "portioneer": PORTIONEER_DIR, "home": HOME}
    plan = []
    staging = tempfile.mkdtemp(prefix="pnrestore-")
    try:
        _tar_extract(bundle, staging)
        root = os.path.join(staging, "bundle")
        for top, dstroot in targets.items():
            srcroot = os.path.join(root, top)
            if not os.path.isdir(srcroot):
                continue
            for r2, _d, fs in os.walk(srcroot):
                for fn in fs:
                    s = os.path.join(r2, fn)
                    rel = os.path.relpath(s, srcroot)
                    plan.append({"from": os.path.relpath(s, root),
                                 "to": os.path.join(dstroot, rel)})
        if not apply:
            return {"ok": True, "dry_run": True, "n_files": len(plan),
                    "targets": targets, "sample": plan[:15], "verify": v}

        safety = make_bundle(label="pre-restore-safety")
        applied = 0
        for item in plan:
            s = os.path.join(root, item["from"])
            d = item["to"]
            os.makedirs(os.path.dirname(d), exist_ok=True)
            try:
                shutil.copy2(s, d)
                applied += 1
            except OSError:
                pass
        return {"ok": True, "applied": applied, "n_files": len(plan),
                "safety_backup": safety.get("path"), "note":
                "Portal neu starten (pnctl restart phantom-portal), damit der Zustand geladen wird."}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (e.__class__.__name__, e)}
    finally:
        shutil.rmtree(staging, ignore_errors=True)

def prune(dest=DEST_DEFAULT, keep=14):
    b = list_bundles(dest)
    removed = []
    for old in b[keep:]:
        try:
            os.remove(old["path"]); removed.append(old["name"])
        except OSError:
            pass
    return {"ok": True, "kept": min(len(b), keep), "removed": removed}

def _main(argv):
    ap = argparse.ArgumentParser(prog="pn_backup")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_now = sub.add_parser("now"); p_now.add_argument("--dest", default=DEST_DEFAULT)
    p_now.add_argument("--label", default=""); p_now.add_argument("--shares", action="store_true")
    p_now.add_argument("--llm", action="store_true", help="LLM-OAuth-Pool-Credentials mitsichern")
    p_ls = sub.add_parser("list"); p_ls.add_argument("--dest", default=DEST_DEFAULT)
    p_vf = sub.add_parser("verify"); p_vf.add_argument("bundle")
    p_rs = sub.add_parser("restore"); p_rs.add_argument("bundle"); p_rs.add_argument("--apply", action="store_true")
    p_pr = sub.add_parser("prune"); p_pr.add_argument("--dest", default=DEST_DEFAULT); p_pr.add_argument("--keep", type=int, default=14)
    a = ap.parse_args(argv)
    if a.cmd == "now":
        res = make_bundle(a.dest, a.label, a.shares, a.llm)
    elif a.cmd == "list":
        res = {"ok": True, "bundles": list_bundles(a.dest)}
    elif a.cmd == "verify":
        res = verify_bundle(a.bundle)
    elif a.cmd == "restore":
        res = restore_bundle(a.bundle, a.apply)
    elif a.cmd == "prune":
        res = prune(a.dest, a.keep)
    else:
        res = {"ok": False, "error": "unknown"}
    print(json.dumps(res, indent=1, ensure_ascii=False))
    return 0 if res.get("ok") else 1

if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
