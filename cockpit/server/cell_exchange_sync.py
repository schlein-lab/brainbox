#!/usr/bin/env python3
"""exchange-sync — in-cell two-way sync: ~/austausch <-> the session's media-share folder.

Runs INSIDE the session microVM. Talks to the in-cell portal proxy (PORTAL_URL, default
http://127.0.0.1:8089) -> vsock -> host portal broker -> /api/cellfs/* — i.e. the governed
files lane, gated by THIS session's fs_read/fs_write allowlist (the portal auto-grants the
session's own share folder, nothing else). No other host path is reachable from here.

Semantics (deliberately simple, owner 2026-07-22 "die mediaserver daten kommen nicht an"):
  * both directions, newest mtime wins (2 s epsilon), deletions are NEVER propagated
  * chunked transfer (4 MB windows) via read offset/length + write append, atomic .part+rename
  * dotfiles, *.pnpart/*.pnup/*.cellfs.tmp and files > 2 GB are skipped, depth <= 4

usage: exchange-sync <remote_dir> <local_dir> [interval_s]
"""
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request

BASE = os.environ.get("PORTAL_URL", "http://127.0.0.1:8089").rstrip("/")
SID = os.environ.get("PN_EXCHANGE_SID", "")

CHUNK = 128 * 1024
MAXF = 2 * 1024 * 1024 * 1024
EPS = 2.0
PIDFILE = "/tmp/exchange-sync.pid"

def call(path, data=None, raw=False, timeout=120):
    if SID:
        path += ("&" if "?" in path else "?") + "session=" + urllib.parse.quote(SID)
    req = urllib.request.Request(
        BASE + path,
        data=(json.dumps(data).encode() if data is not None else None),
        headers={"Content-Type": "application/json"},
        method=("POST" if data is not None else "GET"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
    if raw:
        return b
    d = json.loads(b or b"{}")
    if not d.get("ok"):
        raise RuntimeError(d.get("error") or "cellfs error")
    return d

def q(p):
    return urllib.parse.quote(p)

def r_ls(p):
    return call("/api/cellfs/ls?path=%s" % q(p)).get("entries") or []

def r_stat(p):
    try:
        return call("/api/cellfs/stat?path=%s" % q(p))
    except Exception:
        return None

def skip(name):
    return (name.startswith(".") or name.endswith(".pnpart") or name.endswith(".pnup")
            or name.endswith(".cellfs.tmp"))

def walk_remote(root, rel="", depth=0, out=None):

    out = out if out is not None else {}
    if depth > 4:
        return out
    for e in r_ls(os.path.join(root, rel) if rel else root):
        n = e.get("name") or ""
        if skip(n):
            continue
        rl = os.path.join(rel, n) if rel else n
        if e.get("dir"):
            walk_remote(root, rl, depth + 1, out)
        else:
            out[rl] = {"size": int(e.get("size") or 0),
                       "mtime": (int(e["mtime"]) if e.get("mtime") is not None else None)}
    return out

def walk_local(root):
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not skip(d)]
        for f in fns:
            if skip(f):
                continue
            out.append(os.path.relpath(os.path.join(dp, f), root))
    return out

def pull(remote, local, rel, meta=None):
    src = os.path.join(remote, rel)

    st = meta if (meta and meta.get("mtime") is not None) else r_stat(src)
    if not st or st.get("dir") or int(st.get("size") or 0) > MAXF:
        return False
    dst = os.path.join(local, rel)
    try:
        lst = os.stat(dst)
        if lst.st_mtime >= int(st.get("mtime") or 0) - EPS:
            return False
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    part = dst + ".pnpart"
    size = int(st.get("size") or 0)
    with open(part, "wb") as f:
        off = 0
        while off < size:
            b = call("/api/cellfs/read?path=%s&offset=%d&length=%d"
                     % (q(src), off, min(CHUNK, size - off)), raw=True)
            if not b:
                break
            f.write(b)
            off += len(b)
    os.replace(part, dst)
    mt = int(st.get("mtime") or time.time())
    os.utime(dst, (mt, mt))
    return True

def push(remote, local, rel, rindex=None):
    src = os.path.join(local, rel)
    try:
        lst = os.stat(src)
    except FileNotFoundError:
        return False
    if lst.st_size > MAXF:
        return False
    dst = os.path.join(remote, rel)

    if rindex is not None and rel in rindex and rindex[rel].get("mtime") is not None:
        st = rindex[rel]
    elif rindex is not None and rel not in rindex:
        st = None
    else:
        st = r_stat(dst)
    if st and not st.get("dir") and int(st.get("mtime") or 0) >= int(lst.st_mtime) - EPS:
        return False
    rd = os.path.dirname(dst)
    if rd and rd.rstrip("/") != remote.rstrip("/"):
        call("/api/cellfs/mkdir", {"path": rd})
    up = dst + ".pnup"
    with open(src, "rb") as f:
        first = True
        while True:
            b = f.read(CHUNK)
            if not b and not first:
                break
            call("/api/cellfs/write", {"path": up, "content_b64": base64.b64encode(b).decode(),
                                       "append": (not first), "mtime": int(lst.st_mtime)})
            first = False
            if len(b) < CHUNK:
                break
    call("/api/cellfs/rename", {"path": up, "dst": dst})
    return True

def once(remote, local):
    n = 0
    rindex = walk_remote(remote)
    for rel, meta in rindex.items():
        try:
            if pull(remote, local, rel, meta):
                n += 1
        except Exception as e:
            sys.stderr.write("pull %s: %r\n" % (rel, e))
    for rel in walk_local(local):
        try:
            if push(remote, local, rel, rindex):
                n += 1
        except Exception as e:
            sys.stderr.write("push %s: %r\n" % (rel, e))
    return n

def main():
    if len(sys.argv) < 3:
        sys.stderr.write(__doc__)
        return 2
    remote, local = sys.argv[1], sys.argv[2]
    interval = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
    try:
        if os.path.exists(PIDFILE):
            pid = int(open(PIDFILE).read().strip() or 0)
            if pid and os.path.exists("/proc/%d" % pid):
                return 0
        with open(PIDFILE, "w") as f:
            f.write(str(os.getpid()))
    except (OSError, ValueError):
        pass
    os.makedirs(local, exist_ok=True)
    sys.stderr.write("exchange-sync: %s <-> %s alle %.0fs\n" % (remote, local, interval))

    cur = interval
    while True:
        try:
            n = once(remote, local)
            if n:
                sys.stderr.write("exchange-sync: %d Datei(en) uebertragen\n" % n)
                cur = interval
            else:
                cur = min(cur * 1.5, interval * 6)
        except Exception as e:
            sys.stderr.write("exchange-sync: %r\n" % e)
            time.sleep(10)
        time.sleep(cur)

if __name__ == "__main__":
    sys.exit(main() or 0)
