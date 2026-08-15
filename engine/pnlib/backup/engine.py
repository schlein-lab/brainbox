
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import os
import stat
import tarfile
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from pnlib import secrets as _secrets
from pnlib.backup.policy import Copy

ARCHIVE_MAGIC = "PNBKAR01"
NOBACKUP_MARKER = ".nobackup"

class Target(ABC):

    id: str = "target"

    media: str = "disk"

    offbox: bool = False

    @abstractmethod
    def put(self, content_id: str, blob: bytes) -> None: ...

    @abstractmethod
    def get(self, content_id: str) -> bytes: ...

    @abstractmethod
    def exists(self, content_id: str) -> bool: ...

    @abstractmethod
    def delete(self, content_id: str) -> None: ...

    @abstractmethod
    def list_ids(self) -> List[str]: ...

    def as_copy(self) -> Copy:
        return Copy(target_id=self.id, media=self.media, offbox=self.offbox)

class MemoryTarget(Target):

    def __init__(self, id: str = "mem", media: str = "memory", offbox: bool = False):
        self.id = id
        self.media = media
        self.offbox = offbox
        self._store: Dict[str, bytes] = {}

    def put(self, content_id: str, blob: bytes) -> None:
        self._store[content_id] = bytes(blob)

    def get(self, content_id: str) -> bytes:
        return self._store[content_id]

    def exists(self, content_id: str) -> bool:
        return content_id in self._store

    def delete(self, content_id: str) -> None:
        self._store.pop(content_id, None)

    def list_ids(self) -> List[str]:
        return list(self._store.keys())

    def corrupt(self, content_id: str, replacement: bytes = b"\x00corrupted") -> None:
        self._store[content_id] = replacement

class LocalDirTarget(Target):

    def __init__(self, root: str, id: str, media: str = "disk", offbox: bool = False):
        self.root = root
        self.id = id
        self.media = media
        self.offbox = offbox
        os.makedirs(root, exist_ok=True)

    def _path(self, content_id: str) -> str:
        return os.path.join(self.root, content_id)

    def put(self, content_id: str, blob: bytes) -> None:
        p = self._path(content_id)
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)

    def get(self, content_id: str) -> bytes:
        with open(self._path(content_id), "rb") as f:
            return f.read()

    def exists(self, content_id: str) -> bool:
        return os.path.exists(self._path(content_id))

    def delete(self, content_id: str) -> None:
        try:
            os.unlink(self._path(content_id))
        except FileNotFoundError:
            pass

    def list_ids(self) -> List[str]:
        return [n for n in os.listdir(self.root) if not n.endswith(".tmp")]

@dataclass
class FileEntry:
    path: str
    size: int
    sha256: str
    mode: int

    def to_dict(self) -> dict:
        return {"path": self.path, "size": self.size, "sha256": self.sha256, "mode": self.mode}

    @staticmethod
    def from_dict(d: dict) -> "FileEntry":
        return FileEntry(d["path"], d["size"], d["sha256"], d["mode"])

@dataclass
class Manifest:
    manifest_id: str
    created_at: str
    content_id: str
    cipher_digest: str
    plaintext_size: int
    cipher_size: int
    backend: str
    seal_meta: dict
    source_roots: List[str]
    files: List[FileEntry] = field(default_factory=list)
    copies: List[dict] = field(default_factory=list)
    gfs_label: Optional[str] = None
    version: int = 1

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "manifest_id": self.manifest_id,
            "created_at": self.created_at,
            "content_id": self.content_id,
            "cipher_digest": self.cipher_digest,
            "plaintext_size": self.plaintext_size,
            "cipher_size": self.cipher_size,
            "backend": self.backend,
            "seal_meta": self.seal_meta,
            "source_roots": list(self.source_roots),
            "files": [f.to_dict() for f in self.files],
            "copies": list(self.copies),
            "gfs_label": self.gfs_label,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def digest(self) -> str:

        return hashlib.sha256(self.to_json().encode()).hexdigest()

    @staticmethod
    def from_dict(d: dict) -> "Manifest":
        return Manifest(
            manifest_id=d["manifest_id"], created_at=d["created_at"],
            content_id=d["content_id"], cipher_digest=d["cipher_digest"],
            plaintext_size=d["plaintext_size"], cipher_size=d["cipher_size"],
            backend=d["backend"], seal_meta=d.get("seal_meta", {}),
            source_roots=d.get("source_roots", []),
            files=[FileEntry.from_dict(x) for x in d.get("files", [])],
            copies=d.get("copies", []), gfs_label=d.get("gfs_label"),
            version=d.get("version", 1),
        )

    @staticmethod
    def from_json(s: str) -> "Manifest":
        return Manifest.from_dict(json.loads(s))

def _iter_files(root: str):

    root = os.path.abspath(root)
    if os.path.isfile(root):
        yield root, os.path.basename(root)
        return
    for dirpath, dirnames, filenames in os.walk(root):
        if NOBACKUP_MARKER in filenames:
            dirnames[:] = []
            continue
        dirnames.sort()
        for name in sorted(filenames):
            if name == NOBACKUP_MARKER:
                continue
            ab = os.path.join(dirpath, name)
            if not os.path.isfile(ab) or os.path.islink(ab):
                continue
            rel = os.path.relpath(ab, root)
            yield ab, rel.replace(os.sep, "/")

def build_archive(source_roots: Sequence[str]) -> tuple[bytes, List[FileEntry]]:

    entries: List[FileEntry] = []
    collected = []
    for root in source_roots:
        base = os.path.basename(os.path.abspath(root.rstrip("/"))) or "root"
        is_file = os.path.isfile(root)
        for ab, rel in _iter_files(root):
            arcname = base if is_file else f"{base}/{rel}"
            with open(ab, "rb") as f:
                data = f.read()
            mode = stat.S_IMODE(os.stat(ab).st_mode)
            collected.append((arcname, data, mode))

    collected.sort(key=lambda t: t[0])

    buf = io.BytesIO()

    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for arcname, data, mode in collected:
            ti = tarfile.TarInfo(name=arcname)
            ti.size = len(data)
            ti.mode = mode
            ti.mtime = 0
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            ti.type = tarfile.REGTYPE
            tar.addfile(ti, io.BytesIO(data))
            entries.append(FileEntry(path=arcname, size=len(data),
                                     sha256=hashlib.sha256(data).hexdigest(), mode=mode))
    return buf.getvalue(), entries

@dataclass
class BackupResult:
    manifest: Manifest
    targets_written: List[str]
    threetwoone: dict

def backup(source_roots: Sequence[str], targets: Sequence[Target],
           gfs_label: Optional[str] = None,
           now: Optional[_dt.datetime] = None) -> BackupResult:

    from pnlib.backup.policy import check_3_2_1

    if not targets:
        raise ValueError("backup requires at least one target")

    plaintext, files = build_archive(source_roots)
    content_id = hashlib.sha256(plaintext).hexdigest()

    sealed, seal_meta = _secrets.seal(plaintext)
    cipher_digest = hashlib.sha256(sealed).hexdigest()

    written: List[str] = []
    for t in targets:
        t.put(content_id, sealed)
        written.append(t.id)

    now = now or _dt.datetime.now(_dt.timezone.utc)
    manifest = Manifest(
        manifest_id=uuid.uuid4().hex,
        created_at=now.astimezone(_dt.timezone.utc).isoformat(),
        content_id=content_id,
        cipher_digest=cipher_digest,
        plaintext_size=len(plaintext),
        cipher_size=len(sealed),
        backend=seal_meta.get("backend", "unknown"),
        seal_meta=seal_meta,
        source_roots=[os.path.abspath(r) for r in source_roots],
        files=files,
        copies=[t.as_copy().__dict__ for t in targets],
        gfs_label=gfs_label,
    )

    report = check_3_2_1([t.as_copy() for t in targets])
    return BackupResult(manifest=manifest, targets_written=written,
                        threetwoone=report.to_dict())
