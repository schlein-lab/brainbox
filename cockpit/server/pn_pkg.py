#!/bin/python3

import contextlib
import gzip
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ARCH_MAP = {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "armhf"}
ARCH = ARCH_MAP.get(os.uname().machine, "amd64")
SUITE = os.environ.get("PN_PKG_SUITE", "noble")
MIRROR = os.environ.get(
    "PN_PKG_MIRROR",
    "http://ports.ubuntu.com/ubuntu-ports" if ARCH in ("arm64", "armhf")
    else "http://archive.ubuntu.com/ubuntu",
)
COMPONENTS = ("main", "universe")
SUITES = (SUITE, SUITE + "-updates")

ROOT = os.environ.get("PN_PKG_ROOT", "/")
CACHE = os.path.join(ROOT, "var/cache/pn-pkg")
STATE = os.path.join(ROOT, "var/lib/pn-pkg")
INSTALLED = os.path.join(STATE, "installed.json")
INDEX = os.path.join(CACHE, "index.tsv")
OFFSETS = os.path.join(CACHE, "offsets.json")
PROVIDES = os.path.join(CACHE, "provides.json")

ZSTD_BOOTSTRAP = (
    ("http://deb.debian.org/debian", "bookworm", "main"),
    ("http://archive.ubuntu.com/ubuntu", "focal", "main"),
    ("http://ports.ubuntu.com/ubuntu-ports", "focal", "main"),
)

BASE_PROVIDED = {
    "busybox", "busybox-static", "bash", "dash", "coreutils", "python3", "python3.12",
    "python3-minimal", "libc6", "libc-bin", "tmux", "base-files", "bsdutils", "sysvinit-utils",
    "login", "passwd", "debianutils", "findutils", "grep", "sed", "gzip", "tar", "util-linux",
    "mount", "hostname", "ncurses-bin", "ncurses-base",
}
NOISE_DEPS = {"debconf", "debconf-2.0", "dpkg", "install-info", "adduser", "ucf", "lsb-base"}

def _log(msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()

def _net_hint(err):

    proxy = os.environ.get("http_proxy") or ""
    return (
        "Kein Netzzugang aus der Zelle (%s).\n"
        "  Proxy laut Umgebung: %s\n"
        "  Diese Zelle darf nur ins Netz, wenn die Session das Recht 'Internet' hat.\n"
        "  Der Besitzer schaltet es im Portal unter Sessions -> Ausstattung -> Netz frei\n"
        "  (oder waehlt ein Preset mit Internet). Danach: pn-pkg update" % (err, proxy or "keiner")
    )

def _fetch(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "pn-pkg/1.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()

def cmd_update(_args):
    os.makedirs(CACHE, exist_ok=True)
    tmp = INDEX + ".tmp"
    provides = {}
    n = 0
    with open(tmp, "w", encoding="utf-8") as out:
        for suite in SUITES:
            for comp in COMPONENTS:
                url = "%s/dists/%s/%s/binary-%s/Packages.gz" % (MIRROR, suite, comp, ARCH)
                _log("… hole %s/%s" % (suite, comp))
                try:
                    raw = gzip.decompress(_fetch(url))
                except urllib.error.URLError as e:
                    _log(_net_hint(e))
                    return 2
                except Exception as e:
                    _log("Index %s/%s nicht verfuegbar (%s) — uebersprungen" % (suite, comp, e))
                    continue
                cur = {}
                for line in raw.decode("utf-8", "replace").splitlines():
                    if not line.strip():
                        if cur.get("Package"):
                            out.write("\t".join([
                                cur.get("Package", ""), cur.get("Version", ""),
                                cur.get("Filename", ""), cur.get("Size", "0"),
                                cur.get("Depends", "").replace("\t", " "),
                                cur.get("Description", "").replace("\t", " ")[:200],
                            ]) + "\n")
                            for pv in re.split(r"\s*,\s*", cur.get("Provides", "")):
                                pv = pv.split()[0].strip() if pv.strip() else ""
                                if pv:
                                    provides.setdefault(pv, cur["Package"])
                            n += 1
                        cur = {}
                        continue
                    if line[0] in " \t":
                        continue
                    k, _, v = line.partition(":")
                    if k in ("Package", "Version", "Filename", "Size", "Depends", "Provides",
                             "Description"):
                        cur[k] = v.strip()
    os.replace(tmp, INDEX)

    offs, pos = {}, 0
    with open(INDEX, "rb") as f:
        for line in f:
            name = line.split(b"\t", 1)[0].decode()
            offs[name] = pos
            pos += len(line)
    with open(OFFSETS, "w") as f:
        json.dump(offs, f)
    with open(PROVIDES, "w") as f:
        json.dump(provides, f)
    _log("Index bereit: %d Pakete (%s, %s)" % (n, SUITE, ARCH))
    return 0

def _need_index():
    if not (os.path.exists(INDEX) and os.path.exists(OFFSETS)):
        rc = cmd_update([])
        if rc:
            raise SystemExit(rc)

def _lookup(name):
    with open(OFFSETS) as f:
        offs = json.load(f)
    if name not in offs:
        with open(PROVIDES) as f:
            prov = json.load(f)
        real = prov.get(name)
        if not real or real not in offs:
            return None
        name = real
    with open(INDEX, "rb") as f:
        f.seek(offs[name])
        parts = f.readline().decode("utf-8", "replace").rstrip("\n").split("\t")
    keys = ("name", "version", "filename", "size", "depends", "description")
    return dict(zip(keys, parts + [""] * (len(keys) - len(parts))))

def _deps_of(rec):
    out = []
    for group in re.split(r"\s*,\s*", rec.get("depends") or ""):
        if not group.strip():
            continue
        first = re.split(r"\s*\|\s*", group.strip())[0]
        nm = first.split()[0].strip()
        if nm and nm not in BASE_PROVIDED and nm not in NOISE_DEPS:
            out.append(nm)
    return out

def _installed():
    try:
        with open(INSTALLED) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def _save_installed(db):
    os.makedirs(STATE, exist_ok=True)
    tmp = INSTALLED + ".tmp"
    with open(tmp, "w") as f:
        json.dump(db, f, indent=1)
    os.replace(tmp, INSTALLED)

def _ar_members(blob):

    if not blob.startswith(b"!<arch>\n"):
        raise ValueError("kein .deb (ar-Magic fehlt)")
    pos = 8
    while pos + 60 <= len(blob):
        hdr = blob[pos:pos + 60]
        name = hdr[0:16].decode("ascii", "replace").strip().rstrip("/")
        try:
            size = int(hdr[48:58].decode("ascii").strip())
        except ValueError:
            break
        data = blob[pos + 60:pos + 60 + size]
        yield name, data
        pos += 60 + size + (size % 2)

def _zstd_via_ctypes(data):

    import ctypes
    import ctypes.util
    lib = None
    cands = [os.path.join(ROOT, "usr/lib/pn-pkg/libzstd.so.1"), "libzstd.so.1"]
    found = ctypes.util.find_library("zstd")
    if found:
        cands.insert(1, found)
    for c in cands:
        try:
            lib = ctypes.CDLL(c)
            break
        except OSError:
            continue
    if lib is None:
        lib = ctypes.CDLL(_bootstrap_libzstd())

    class _In(ctypes.Structure):
        _fields_ = [("src", ctypes.c_void_p), ("size", ctypes.c_size_t), ("pos", ctypes.c_size_t)]

    class _Out(ctypes.Structure):
        _fields_ = [("dst", ctypes.c_void_p), ("size", ctypes.c_size_t), ("pos", ctypes.c_size_t)]

    lib.ZSTD_createDStream.restype = ctypes.c_void_p
    lib.ZSTD_initDStream.argtypes = [ctypes.c_void_p]
    lib.ZSTD_initDStream.restype = ctypes.c_size_t
    lib.ZSTD_decompressStream.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Out), ctypes.POINTER(_In)]
    lib.ZSTD_decompressStream.restype = ctypes.c_size_t
    lib.ZSTD_freeDStream.argtypes = [ctypes.c_void_p]
    lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
    lib.ZSTD_isError.restype = ctypes.c_uint
    lib.ZSTD_DStreamOutSize.restype = ctypes.c_size_t

    ds = lib.ZSTD_createDStream()
    if not ds:
        raise RuntimeError("libzstd: DStream nicht erzeugbar")
    try:
        lib.ZSTD_initDStream(ds)
        chunk = max(65536, int(lib.ZSTD_DStreamOutSize()))
        src = ctypes.create_string_buffer(data, len(data))
        dst = ctypes.create_string_buffer(chunk)
        inb = _In(ctypes.cast(src, ctypes.c_void_p), len(data), 0)
        out = io.BytesIO()
        while inb.pos < inb.size:
            ob = _Out(ctypes.cast(dst, ctypes.c_void_p), chunk, 0)
            rc = lib.ZSTD_decompressStream(ds, ctypes.byref(ob), ctypes.byref(inb))
            if lib.ZSTD_isError(rc):
                raise RuntimeError("libzstd-Fehler beim Entpacken")
            out.write(dst.raw[:ob.pos])
            if ob.pos == 0 and rc == 0:
                break
        return out.getvalue()
    finally:
        lib.ZSTD_freeDStream(ds)

def _bootstrap_libzstd():

    dest_dir = os.path.join(ROOT, "usr/lib/pn-pkg")
    dest = os.path.join(dest_dir, "libzstd.so.1")
    if os.path.exists(dest):
        return dest
    _log("… hole einmalig libzstd (macht zstd-komprimierte Pakete lesbar)")
    os.makedirs(dest_dir, exist_ok=True)
    last = None
    for base, dist, comp in ZSTD_BOOTSTRAP:
        try:
            url = "%s/dists/%s/%s/binary-%s/Packages.gz" % (base, dist, comp, ARCH)
            raw = gzip.decompress(_fetch(url, timeout=300)).decode("utf-8", "replace")
            fn = None
            for blk in raw.split("\n\n"):
                if re.search(r"^Package: libzstd1$", blk, re.M):
                    mm = re.search(r"^Filename: (.+)$", blk, re.M)
                    if mm:
                        fn = mm.group(1).strip()
                        break
            if not fn:
                last = "libzstd1 nicht im Index von %s/%s" % (dist, comp)
                continue
            blob = _fetch("%s/%s" % (base, fn), timeout=300)
            for name, data in _ar_members(blob):
                if not name.startswith("data.tar"):
                    continue
                if name.endswith(".zst"):
                    raise RuntimeError("Quelle %s liefert selbst zstd" % dist)
                payload = _decompress(name, data)
                with tarfile.open(fileobj=io.BytesIO(payload)) as tf:
                    for m in tf.getmembers():
                        if m.isfile() and os.path.basename(m.name).startswith("libzstd.so.1"):
                            with tf.extractfile(m) as fsrc, open(dest, "wb") as fdst:
                                shutil.copyfileobj(fsrc, fdst)
                            os.chmod(dest, 0o755)
                            _log("   libzstd aus %s" % dist)
                            return dest
            last = "libzstd.so.1 im Paket von %s nicht gefunden" % dist
        except Exception as e:
            last = "%s: %s" % (dist, e)
            continue
    raise RuntimeError("zstd-Bootstrap fehlgeschlagen (%s)" % last)

def _decompress(name, data):
    if name.endswith(".gz"):
        return gzip.decompress(data)
    if name.endswith(".xz"):
        import lzma
        return lzma.decompress(data)
    if name.endswith(".zst"):
        for tool in ("unzstd", "zstd"):
            try:
                p = subprocess.run([tool, "-d", "-c"], input=data, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, check=True)
                return p.stdout
            except (OSError, subprocess.CalledProcessError):
                continue
        return _zstd_via_ctypes(data)
    return data

def _extract_deb(blob, root=None, dry=False):

    root = root or ROOT
    written = []
    for name, data in _ar_members(blob):
        if not name.startswith("data.tar"):
            continue
        raw = _decompress(name, data)
        with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
            for m in tf.getmembers():
                rel = m.name.lstrip("./")
                if not rel or rel.startswith(".."):
                    continue
                if "usr/share/python3/runtime.d/" in rel:
                    _log("  ~ %s uebersprungen (dpkg-Hook ohne dpkg-Registrierung)" % rel)
                    continue
                dest = os.path.join(root, rel)
                if dry:
                    written.append("/" + rel)
                    continue
                try:
                    if m.isdir():
                        os.makedirs(dest, exist_ok=True)
                    elif m.issym():
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        if os.path.lexists(dest):
                            os.remove(dest)
                        os.symlink(m.linkname, dest)
                    elif m.islnk():
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        src = os.path.join(root, m.linkname.lstrip("./"))
                        if os.path.lexists(dest):
                            os.remove(dest)
                        if os.path.exists(src):
                            os.link(src, dest)
                    elif m.isfile():
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with tf.extractfile(m) as fsrc, open(dest, "wb") as fdst:
                            shutil.copyfileobj(fsrc, fdst)
                        os.chmod(dest, m.mode or 0o644)
                    else:
                        continue
                except OSError as e:
                    _log("  ! %s: %s" % (rel, e))
                    continue
                written.append("/" + rel)
        break
    return written

def _link_into_path(files):

    for p in files:
        if not (p.startswith("/usr/bin/") or p.startswith("/usr/local/bin/")
                or p.startswith("/usr/sbin/")):
            continue
        target = os.path.join(ROOT, p.lstrip("/"))
        link = os.path.join(ROOT, "bin", os.path.basename(p))
        try:
            if not os.path.isfile(target) or not os.access(target, os.X_OK):
                continue
            if os.path.lexists(link):
                continue
            os.symlink(p, link)
        except OSError:
            continue

def cmd_install(args):
    want = [a for a in args if not a.startswith("-")]
    if not want:
        _log("Nutzung: pn-pkg install PAKET…")
        return 1
    _need_index()
    db = _installed()
    todo, seen = [], set()

    def walk(nm, depth=0):
        if nm in seen or nm in BASE_PROVIDED or depth > 12:
            return
        seen.add(nm)
        if nm in db:
            return
        rec = _lookup(nm)
        if not rec:
            if depth == 0:
                _log("Unbekanntes Paket: %s (pn-pkg search %s)" % (nm, nm))
            return
        for d in _deps_of(rec):
            walk(d, depth + 1)
        todo.append(rec)

    for nm in want:
        walk(nm)
    if not todo:
        _log("Nichts zu tun (schon vorhanden).")
        return 0
    total = sum(int(r.get("size") or 0) for r in todo)
    _log("Installiere %d Paket(e), %.1f MB:" % (len(todo), total / 1e6))
    _log("  " + " ".join(r["name"] for r in todo))
    for rec in todo:
        url = "%s/%s" % (MIRROR, rec["filename"])
        _log("… %s %s" % (rec["name"], rec["version"]))
        try:
            blob = _fetch(url, timeout=300)
        except Exception as e:
            _log(_net_hint(e) if "proxy" in str(e).lower() or "urlopen" in str(e).lower()
                 else "Download fehlgeschlagen (%s): %s" % (rec["name"], e))
            return 2
        try:
            files = _extract_deb(blob)
        except Exception as e:
            _log("Entpacken fehlgeschlagen (%s): %s" % (rec["name"], e))
            return 3
        _link_into_path(files)
        db[rec["name"]] = {"version": rec["version"], "files": files,
                           "description": rec.get("description", "")}
        _save_installed(db)

    for cmd in (["ldconfig"], ["/sbin/ldconfig"]):
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            break
        except (OSError, subprocess.SubprocessError):
            continue
    _log("Fertig. Neue Programme liegen in /usr/bin bzw. /bin.")
    return 0

def cmd_remove(args):
    db = _installed()
    rc = 0
    for nm in [a for a in args if not a.startswith("-")]:
        rec = db.pop(nm, None)
        if not rec:
            _log("Nicht installiert: %s" % nm)
            rc = 1
            continue
        for p in sorted(rec.get("files") or [], key=len, reverse=True):
            try:
                if os.path.islink(p) or os.path.isfile(p):
                    os.remove(p)
                elif os.path.isdir(p):
                    os.rmdir(p)
            except OSError:
                pass
        _log("Entfernt: %s" % nm)
    _save_installed(db)
    return rc

def cmd_list(_args):
    db = _installed()
    if not db:
        _log("Noch nichts per pn-pkg installiert.")
    for nm, rec in sorted(db.items()):
        print("%-28s %-22s %s" % (nm, rec.get("version", "?"), rec.get("description", "")[:60]))
    return 0

def cmd_search(args):
    _need_index()
    pat = (args[0] if args else "").lower()
    if not pat:
        _log("Nutzung: pn-pkg search MUSTER")
        return 1
    hits = 0
    with open(INDEX, encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 6:
                continue
            if pat in parts[0].lower() or pat in parts[5].lower():
                print("%-30s %s" % (parts[0], parts[5][:70]))
                hits += 1
                if hits >= 40:
                    print("… (gekuerzt)")
                    break
    if not hits:
        _log("Kein Treffer.")
    return 0

def cmd_show(args):
    _need_index()
    for nm in args:
        rec = _lookup(nm)
        if not rec:
            _log("Unbekannt: %s" % nm)
            continue
        print("Paket:        %s\nVersion:      %s\nGroesse:      %.1f MB\nBeschreibung: %s\n"
              "Haengt ab von: %s\n" % (rec["name"], rec["version"],
                                       int(rec.get("size") or 0) / 1e6,
                                       rec.get("description", ""), ", ".join(_deps_of(rec)) or "-"))
    return 0

def _pip_wheel_urls():

    urls = []
    try:
        meta = json.loads(_fetch("https://pypi.org/pypi/pip/json", timeout=120).decode("utf-8"))
        for f in meta.get("urls") or []:
            if f.get("packagetype") == "bdist_wheel" and str(f.get("filename", "")).endswith(
                    "-py3-none-any.whl"):
                urls.append(f["url"])
    except Exception:
        pass
    try:
        html = _fetch("https://pypi.org/simple/pip/", timeout=120).decode("utf-8", "replace")
        found = re.findall(r'href="([^"]+pip-[\d.]+-py3-none-any\.whl[^"]*)"', html)
        urls.extend(u.split("#")[0] for u in found[-3:])
    except Exception:
        pass
    return urls

def cmd_bootstrap_pip(_args):

    try:
        import pip
        _log("pip ist bereits vorhanden.")
        return 0
    except ImportError:
        pass
    cands = _pip_wheel_urls()
    if not cands:
        _log(_net_hint("PyPI nicht erreichbar"))
        return 2
    os.makedirs(CACHE, exist_ok=True)
    cache = os.path.join(CACHE, "pip")
    os.makedirs(cache, exist_ok=True)
    last = ""
    for url in cands:
        name = url.rsplit("/", 1)[-1].split("?")[0]
        _log("… lade %s" % name)
        if not (name.endswith(".whl") and name.count("-") >= 4):
            last = "unerwarteter Wheel-Name %r" % name[:60]
            continue
        try:
            data = _fetch(url, timeout=300)
        except Exception as e:
            last = str(e)
            continue
        if not data.startswith(b"PK"):
            head = data[:160].decode("utf-8", "replace").replace("\n", " ")
            last = "keine ZIP-Datei erhalten (Anfang: %s)" % head
            continue
        whl = os.path.join(CACHE, name)
        with open(whl, "wb") as f:
            f.write(data)
        env = dict(os.environ)
        env.setdefault("PIP_CACHE_DIR", cache)
        rc = subprocess.call([sys.executable, whl + "/pip", "install", "--no-index",
                              "--break-system-packages", whl], env=env)
        if rc == 0:
            _log("pip ist da: python3 -m pip install <paket>")
            return 0
        last = "pip-Installer endete mit rc=%d" % rc
    _log("pip-Bootstrap fehlgeschlagen: %s" % last)
    _log("Ausweg: pn-pkg install python3-pip   (Distributions-Paket)")
    return 2

SHIMS = {
    "sudo": """#!/bin/sh
# In der Zelle laeuft alles als root — 'sudo' ist deshalb ein Durchreicher, damit Anleitungen
# aus dem Netz unveraendert funktionieren. Bekannte Schalter werden geschluckt.
while [ $# -gt 0 ]; do
  case "$1" in
    -n|-H|-E|-k|-S) shift ;;
    -u) shift 2 ;;
    --) shift; break ;;
    -*) shift ;;
    *) break ;;
  esac
done
[ $# -eq 0 ] && exec /bin/sh
exec "$@"
""",
    "apt": """#!/bin/sh
exec /opt/pn/pn-pkg apt "$@"
""",
    "apt-get": """#!/bin/sh
exec /opt/pn/pn-pkg apt "$@"
""",
    "apt-cache": """#!/bin/sh
exec /opt/pn/pn-pkg apt "$@"
""",
    "pip3": """#!/bin/sh
# pip in der Zelle: beim ersten Aufruf einziehen (kein ensurepip im Basis-Image).
/bin/python3 -c 'import pip' 2>/dev/null || /opt/pn/pn-pkg bootstrap-pip || exit $?
PIP_CACHE_DIR=${PIP_CACHE_DIR:-/var/cache/pn-pkg/pip}; export PIP_CACHE_DIR
mkdir -p "$PIP_CACHE_DIR" 2>/dev/null
# PEP 668: die Zelle IST die Umgebung — es gibt keine Distributions-Paketverwaltung, mit der
# pip kollidieren koennte (pn-pkg fasst site-packages nicht an). Deshalb bei install/uninstall
# ausdruecklich erlauben, statt den Nutzer in venv-Ratschlaege zu schicken.
cmd="$1"
case "$cmd" in
  install)
    shift
    # --no-warn-script-location: wir verlinken gleich selbst, die Warnung waere dann falsch.
    # --root-user-action=ignore: in der Zelle laeuft alles als root, und der Rat "nimm ein
    # venv" fuehrt hier nirgendwo hin (siehe PEP-668-Anmerkung oben).
    # ⚠️ NUR bei install: `pip uninstall` kennt --no-warn-script-location nicht und bricht
    # damit in die Nutzungsmeldung ab (gemessen 12.08.2026).
    /bin/python3 -m pip install --break-system-packages --no-warn-script-location --root-user-action=ignore "$@"
    rc=$?
    # Der Zell-PATH ist nur /bin:/sbin, pip legt Konsolen-Skripte nach /usr/local/bin.
    # Ohne Verlinkung ist ein frisch installiertes Programm unter seinem NAMEN nicht
    # aufrufbar -- gemessen 12.08.2026: `cowsay` -> "sh: cowsay: not found".
    [ $rc -eq 0 ] && /opt/pn/pn-pkg relink
    exit $rc ;;
  uninstall)
    shift
    /bin/python3 -m pip uninstall --break-system-packages --root-user-action=ignore "$@"
    rc=$?
    # Nach dem Entfernen zeigt der Verweis in /bin ins Nichts -- relink raeumt ihn weg.
    [ $rc -eq 0 ] && /opt/pn/pn-pkg relink
    exit $rc ;;
  *) exec /bin/python3 -m pip "$@" ;;
esac
""",
    "pip": """#!/bin/sh
exec /bin/pip3 "$@"
""",
}

def cmd_apt(args):

    args = [a for a in args if a not in ("-y", "--yes", "-q", "-qq", "--no-install-recommends")]
    verb = args[0] if args else "help"
    rest = args[1:]
    if verb in ("install",):
        return cmd_install(rest)
    if verb in ("remove", "purge", "autoremove"):
        return cmd_remove(rest)
    if verb in ("update",):
        return cmd_update(rest)
    if verb in ("search",):
        return cmd_search(rest)
    if verb in ("show", "policy"):
        return cmd_show(rest)
    if verb in ("list",):
        return cmd_list(rest)
    if verb in ("upgrade", "dist-upgrade", "full-upgrade"):
        _log("Ein Voll-Upgrade gibt es hier nicht: die Zelle ist ein Basis-Image plus deine "
             "Nachinstallationen. Einzelne Pakete: pn-pkg install <name>.")
        return 1
    _log(__doc__)
    return 1

def cmd_install_shims(_args):
    os.makedirs("/opt/pn", exist_ok=True)
    self_path = os.path.abspath(__file__)
    if self_path != "/opt/pn/pn-pkg":
        shutil.copyfile(self_path, "/opt/pn/pn-pkg")
    os.chmod("/opt/pn/pn-pkg", 0o755)
    for name, body in SHIMS.items():
        p = os.path.join("/bin", name)
        with open(p, "w") as f:
            f.write(body)
        os.chmod(p, 0o755)
    for name in ("pn-pkg",):
        link = os.path.join("/bin", name)
        if not os.path.lexists(link):
            os.symlink("/opt/pn/pn-pkg", link)

    import glob as _glob
    for marker in _glob.glob("/usr/lib/python3*/EXTERNALLY-MANAGED") + \
            _glob.glob("/usr/lib/python3/dist-packages/EXTERNALLY-MANAGED") + \
            _glob.glob("/lib/python3*/EXTERNALLY-MANAGED"):
        with contextlib.suppress(OSError):
            os.rename(marker, marker + ".pn-disabled")
    cmd_relink([])
    _log("Shims installiert: sudo, apt, apt-get, apt-cache, pip, pip3, pn-pkg")
    return 0

def cmd_relink(_args):

    found = []
    for d in ("usr/bin", "usr/local/bin", "usr/sbin"):
        p = os.path.join(ROOT, d)
        if not os.path.isdir(p):
            continue
        for name in os.listdir(p):
            found.append("/" + d + "/" + name)
    binp = os.path.join(ROOT, "bin")
    before = set(os.listdir(binp)) if os.path.isdir(binp) else set()

    tot = []
    for name in (sorted(before)):
        link = os.path.join(binp, name)
        if not os.path.islink(link):
            continue
        ziel = os.readlink(link)
        if not ziel.startswith(("/usr/bin/", "/usr/local/bin/", "/usr/sbin/")):
            continue
        if os.path.exists(os.path.join(ROOT, ziel.lstrip("/"))):
            continue
        with contextlib.suppress(OSError):
            os.remove(link)
            tot.append(name)
    if tot:
        _log("tote Verweise entfernt: %s" % " ".join(tot[:20]))
        before -= set(tot)

    _link_into_path(found)
    after = set(os.listdir(os.path.join(ROOT, "bin"))) if os.path.isdir(
        os.path.join(ROOT, "bin")) else set()
    if after - before:
        _log("verlinkt nach /bin: %s" % " ".join(sorted(after - before)[:20]))
    return 0

COMMANDS = {
    "update": cmd_update, "install": cmd_install, "remove": cmd_remove, "list": cmd_list,
    "search": cmd_search, "show": cmd_show, "bootstrap-pip": cmd_bootstrap_pip,
    "apt": cmd_apt, "--install-shims": cmd_install_shims, "relink": cmd_relink,
}

def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    fn = COMMANDS.get(argv[1])
    if not fn:
        _log("Unbekannter Befehl: %s" % argv[1])
        print(__doc__)
        return 1
    try:
        return fn(argv[2:])
    except KeyboardInterrupt:
        return 130
    except urllib.error.URLError as e:
        _log(_net_hint(e))
        return 2

if __name__ == "__main__":
    sys.exit(main(sys.argv))
