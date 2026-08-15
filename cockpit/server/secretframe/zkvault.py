
from __future__ import annotations
import os, json, time, hashlib, threading, base64, re

MAX_BLOB_BYTES = 1 << 20
SCHEMA = 1
_SAFE = re.compile(r"[^A-Za-z0-9._@-]")

class VaultError(Exception):
    pass

class ConflictError(VaultError):
    pass

class TooBigError(VaultError):
    pass

def _uid_safe(uid) -> str:

    s = _SAFE.sub("", str(uid))
    s = s.strip(".")
    if not s:
        raise VaultError("empty/invalid principal")
    return s[:128]

class ZeroKnowledgeVault:

    def __init__(self, base_dir: str):
        self._root = os.path.join(base_dir, "zkvault")
        os.makedirs(self._root, exist_ok=True)
        self._lock = threading.RLock()

    def _dir(self, uid) -> str:
        return os.path.join(self._root, _uid_safe(uid))

    def _meta_path(self, uid) -> str:
        return os.path.join(self._dir(uid), "meta.json")

    def _blob_path(self, uid, version: int) -> str:
        return os.path.join(self._dir(uid), f"blob.{int(version)}.bin")

    def _read_meta(self, uid) -> dict | None:
        try:
            with open(self._meta_path(uid)) as f:
                m = json.load(f)
        except (FileNotFoundError, ValueError):
            return None
        if not isinstance(m, dict) or "version" not in m:
            return None
        return m

    def head(self, uid) -> dict | None:

        with self._lock:
            m = self._read_meta(uid)
            if not m:
                return None
            return {"version": int(m["version"]), "bytes": int(m.get("bytes", 0)),
                    "sha256": m.get("sha256", ""), "updated": m.get("updated", 0)}

    def get(self, uid) -> dict | None:

        with self._lock:
            m = self._read_meta(uid)
            if not m:
                return None
            v = int(m["version"])
            try:
                with open(self._blob_path(uid, v), "rb") as f:
                    raw = f.read()
            except FileNotFoundError:
                return None

            if m.get("sha256") and hashlib.sha256(raw).hexdigest() != m["sha256"]:
                raise VaultError("blob/meta integrity mismatch")
            return {"version": v, "blob_b64": base64.b64encode(raw).decode("ascii"),
                    "bytes": len(raw), "sha256": m.get("sha256", ""), "updated": m.get("updated", 0)}

    def put(self, uid, blob: bytes, base_version) -> dict:

        if not isinstance(blob, (bytes, bytearray)):
            raise VaultError("blob must be bytes")
        blob = bytes(blob)
        if len(blob) > MAX_BLOB_BYTES:
            raise TooBigError(f"blob {len(blob)}B exceeds {MAX_BLOB_BYTES}B")
        with self._lock:
            cur = self._read_meta(uid)
            cur_v = int(cur["version"]) if cur else 0
            want = 0 if base_version in (None, "") else int(base_version)
            if want != cur_v:
                raise ConflictError(f"base_version {want} != stored {cur_v}")
            new_v = cur_v + 1
            d = self._dir(uid)
            os.makedirs(d, exist_ok=True)

            bp = self._blob_path(uid, new_v)
            tmp = bp + ".tmp"
            with open(tmp, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, bp)

            meta = {"schema": SCHEMA, "version": new_v, "updated": int(time.time()),
                    "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
            self._write_meta_atomic(uid, meta)

            self._gc(uid, keep=new_v)
            return {"version": new_v}

    def delete(self, uid, base_version) -> dict:

        with self._lock:
            cur = self._read_meta(uid)
            if not cur:
                return {"ok": True, "version": 0}
            cur_v = int(cur["version"])
            want = None if base_version in (None, "") else int(base_version)
            if want is not None and want != cur_v:
                raise ConflictError(f"base_version {want} != stored {cur_v}")
            d = self._dir(uid)
            for fn in os.listdir(d):
                if fn == "meta.json" or fn.startswith("blob."):
                    try:
                        os.remove(os.path.join(d, fn))
                    except OSError:
                        pass
            return {"ok": True, "version": 0}

    def _write_meta_atomic(self, uid, meta: dict) -> None:
        p = self._meta_path(uid)
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(meta, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)

    def _gc(self, uid, keep: int) -> None:
        d = self._dir(uid)
        try:
            entries = os.listdir(d)
        except OSError:
            return
        for fn in entries:
            if fn.startswith("blob.") and fn.endswith(".bin"):
                try:
                    v = int(fn[len("blob."):-len(".bin")])
                except ValueError:
                    continue
                if v != keep:
                    try:
                        os.remove(os.path.join(d, fn))
                    except OSError:
                        pass
