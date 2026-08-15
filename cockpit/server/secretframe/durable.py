
from __future__ import annotations

import base64
import json
import os
import threading
import time

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    _HAVE_CRYPTO = True
except Exception:
    _HAVE_CRYPTO = False

try:

    from inject import inject_once
except Exception:
    inject_once = None

_HKDF_INFO = b"brainarbeit-vault:"

def _uid_safe(uid) -> str:

    s = "".join(c for c in str(uid) if c.isalnum() or c in "-_.@")
    return (s or "unknown")[:96]

class VaultError(Exception):
    pass

class DurableVault:

    def __init__(self, base_dir: str):
        if not _HAVE_CRYPTO:
            raise VaultError("cryptography (Fernet/HKDF) unavailable — durable vault disabled")
        self._root = os.path.join(base_dir, "secretvault")
        self._users = os.path.join(self._root, "users")
        self._master_path = os.path.join(self._root, "master.key")
        self._lock = threading.Lock()
        os.makedirs(self._users, exist_ok=True)
        self._master = self._load_or_create_master()

    def _load_or_create_master(self) -> bytes:
        try:
            with open(self._master_path, "rb") as f:
                raw = f.read().strip()
            if raw:
                return base64.urlsafe_b64decode(raw)
        except FileNotFoundError:
            pass
        except Exception as e:
            raise VaultError("master key unreadable: %s" % e)

        master = os.urandom(32)
        os.makedirs(self._root, exist_ok=True)
        tmp = "%s.tmp-%d" % (self._master_path, os.getpid())
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, base64.urlsafe_b64encode(master))
            finally:
                os.close(fd)
            os.replace(tmp, self._master_path)
        except FileExistsError:

            try:
                os.unlink(tmp)
            except OSError:
                pass
            with open(self._master_path, "rb") as f:
                return base64.urlsafe_b64decode(f.read().strip())
        try:
            os.chmod(self._master_path, 0o600)
        except OSError:
            pass
        return master

    def _fernet_for(self, uid) -> "Fernet":
        hk = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                  info=_HKDF_INFO + _uid_safe(uid).encode())
        return Fernet(base64.urlsafe_b64encode(hk.derive(self._master)))

    def _vault_path(self, uid) -> str:
        d = os.path.join(self._users, _uid_safe(uid))
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "vault.json")

    def _read_file(self, uid) -> dict:
        try:
            with open(self._vault_path(uid)) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception:
            return {}

    def _write_file(self, uid, data: dict) -> None:
        p = self._vault_path(uid)
        tmp = "%s.tmp-%d" % (p, os.getpid())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(data).encode())
        finally:
            os.close(fd)
        os.replace(tmp, p)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass

    def set(self, uid, name, value, kind=None) -> str:

        name = str(name).strip()
        if not name:
            raise VaultError("name required")
        if isinstance(value, str):
            value = value.encode("utf-8")
        if not isinstance(value, (bytes, bytearray)):
            raise VaultError("value must be str or bytes")
        ct = self._fernet_for(uid).encrypt(bytes(value)).decode("ascii")
        now = time.time()
        with self._lock:
            data = self._read_file(uid)
            prev = data.get(name) or {}
            data[name] = {"ct": ct,
                          "kind": kind if kind is not None else prev.get("kind"),
                          "created": prev.get("created", now), "updated": now}
            self._write_file(uid, data)
        return name

    def list_names(self, uid) -> list:

        data = self._read_file(uid)
        return sorted(({"name": n, "kind": e.get("kind"),
                        "created": e.get("created"), "updated": e.get("updated")}
                       for n, e in data.items()), key=lambda x: x["name"])

    def has(self, uid, name) -> bool:
        return str(name).strip() in self._read_file(uid)

    def delete(self, uid, name) -> bool:
        name = str(name).strip()
        with self._lock:
            data = self._read_file(uid)
            if name not in data:
                return False
            data.pop(name, None)
            self._write_file(uid, data)
        return True

    def fetch(self, uid, name) -> bytes:

        e = self._read_file(uid).get(str(name).strip())
        if not e:
            raise KeyError(name)
        try:
            return self._fernet_for(uid).decrypt(e["ct"].encode("ascii"))
        except InvalidToken:
            raise VaultError("ciphertext tampered or wrong key for %r" % name)

    def use(self, uid, name, consumer, *, require_mlock: bool = False):

        if inject_once is None:
            return consumer(memoryview(self.fetch(uid, name)))
        return inject_once(lambda: self.fetch(uid, name), consumer, require_mlock=require_mlock)
