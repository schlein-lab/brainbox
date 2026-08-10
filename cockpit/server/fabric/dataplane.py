
import os
import re
import json
import threading
import http.cookiejar

DEFAULT_ROOT = os.environ.get(
    "BRAINARBEIT_NAS_ROOT",
    os.path.expanduser("~/.local/share/brainarbeit/nas"),
)

_SAFE = re.compile(r"[^A-Za-z0-9._-]")

def _slug(s):

    s = _SAFE.sub("_", str(s or "").strip()) or "_"
    return "_" if s in (".", "..") else s

class Store:

    def __init__(self, root, principal, app):
        self.principal = _slug(principal)
        self.app = _slug(app)
        self.base = os.path.join(root, self.principal, self.app)
        self.files_dir = os.path.join(self.base, "files")
        self.kv_dir = os.path.join(self.base, "kv")
        os.makedirs(self.files_dir, exist_ok=True)
        os.makedirs(self.kv_dir, exist_ok=True)
        self._lock = threading.RLock()
        self._cj = None

    @property
    def rel(self):

        return "%s/%s" % (self.principal, self.app)

    def path(self, rel=""):

        p = os.path.normpath(os.path.join(self.files_dir, rel.lstrip("/")))
        root = os.path.normpath(self.files_dir)
        if p != root and not p.startswith(root + os.sep):
            raise ValueError("path escapes store: %r" % rel)
        return p

    def open(self, rel, mode="rb"):
        p = self.path(rel)
        if any(m in mode for m in ("w", "a", "x", "+")):
            os.makedirs(os.path.dirname(p), exist_ok=True)
        return open(p, mode)

    def exists(self, rel):
        return os.path.exists(self.path(rel))

    def list_files(self, subdir=""):

        d = self.path(subdir)
        out = []
        try:
            for name in sorted(os.listdir(d)):
                p = os.path.join(d, name)
                try:
                    stt = os.stat(p)
                except OSError:
                    continue
                out.append({"name": name, "size": stt.st_size,
                            "mtime": int(stt.st_mtime), "dir": os.path.isdir(p)})
        except FileNotFoundError:
            pass
        return out

    def delete(self, rel):
        p = self.path(rel)
        try:
            os.remove(p)
            return True
        except OSError:
            return False

    def get_kv(self, key, default=None):
        f = os.path.join(self.kv_dir, _slug(key) + ".json")
        with self._lock:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (FileNotFoundError, ValueError):
                return default

    def put_kv(self, key, obj):
        f = os.path.join(self.kv_dir, _slug(key) + ".json")
        tmp = f + ".tmp"
        with self._lock:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False, indent=0)
            os.replace(tmp, f)

    def cookie_jar(self):

        with self._lock:
            if self._cj is None:
                path = self.path("cookies.txt")
                cj = http.cookiejar.MozillaCookieJar(path)
                try:
                    cj.load(ignore_discard=True, ignore_expires=True)
                except (FileNotFoundError, http.cookiejar.LoadError):
                    pass
                self._cj = cj
            return self._cj

    def save_cookies(self):

        with self._lock:
            if self._cj is not None:
                try:
                    self._cj.save(ignore_discard=True, ignore_expires=True)
                except OSError:
                    pass

_stores = {}
_stores_lock = threading.Lock()

def open_store(principal, app, root=None):
    root = root or DEFAULT_ROOT
    key = (root, _slug(principal), _slug(app))
    with _stores_lock:
        st = _stores.get(key)
        if st is None:
            st = Store(root, principal, app)
            _stores[key] = st
        return st

def principals(root=None):
    root = root or DEFAULT_ROOT
    try:
        return sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    except FileNotFoundError:
        return []
