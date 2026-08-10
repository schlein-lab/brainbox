

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
from typing import Callable, Optional

try:
    _libc = ctypes.CDLL("libc.so.6", use_errno=True)
except OSError:
    _libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)

_libc.mmap.restype = ctypes.c_void_p
_libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                       ctypes.c_int, ctypes.c_int, ctypes.c_long]
_libc.munmap.restype = ctypes.c_int
_libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
_libc.mlock.restype = ctypes.c_int
_libc.mlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
_libc.munlock.restype = ctypes.c_int
_libc.munlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]

PROT_READ = 0x1
PROT_WRITE = 0x2
MAP_PRIVATE = 0x02
MAP_ANONYMOUS = 0x20
MAP_FAILED = ctypes.c_void_p(-1).value

MFD_CLOEXEC = 0x0001
MFD_ALLOW_SEALING = 0x0002
_HAVE_MEMFD = hasattr(os, "memfd_create")

class InjectionError(Exception):
    pass

def _syscall_err(name: str) -> InjectionError:
    e = ctypes.get_errno()
    return InjectionError(f"{name} failed: [{e}] {os.strerror(e)}")

class SecretRegion:

    def __init__(self, size: int, *, require_mlock: bool = True,
                 prefer_memfd: bool = True):
        if size <= 0:
            raise ValueError("size must be > 0")
        self.size = size
        self.require_mlock = require_mlock
        self._addr: Optional[int] = None
        self._memfd: int = -1
        self._locked = False
        self._loaded = False
        self._spent = False
        self._closed = False
        self._backing = "mmap-anon"
        self._reserve(prefer_memfd)

    def _reserve(self, prefer_memfd: bool) -> None:
        fd = -1
        if prefer_memfd and _HAVE_MEMFD:
            try:
                fd = os.memfd_create("brainarbeit-secret",
                                     MFD_CLOEXEC | MFD_ALLOW_SEALING)
                os.ftruncate(fd, self.size)
                self._memfd = fd
                self._backing = "memfd"
            except OSError:
                fd = -1

        if self._memfd >= 0:
            addr = _libc.mmap(None, self.size, PROT_READ | PROT_WRITE,
                              MAP_PRIVATE, self._memfd, 0)
        else:
            addr = _libc.mmap(None, self.size, PROT_READ | PROT_WRITE,
                              MAP_PRIVATE | MAP_ANONYMOUS, -1, 0)

        if addr is None or addr == MAP_FAILED:
            self._cleanup_fd()
            raise _syscall_err("mmap")
        self._addr = addr

        if _libc.mlock(ctypes.c_void_p(self._addr), self.size) == 0:
            self._locked = True
        else:
            err = ctypes.get_errno()
            if self.require_mlock:
                self._teardown()
                raise InjectionError(
                    f"mlock failed ([{err}] {os.strerror(err)}); refusing to "
                    f"hold a secret in swappable memory (fail-closed, §8.3). "
                    f"Raise RLIMIT_MEMLOCK or grant the leaf CAP_IPC_LOCK."
                )

    def load(self, secret: bytes) -> None:

        if self._closed or self._spent:
            raise InjectionError("region is closed or already spent")
        if len(secret) > self.size:
            raise InjectionError("secret larger than region")
        ctypes.memmove(self._addr, secret, len(secret))
        self._used_len = len(secret)
        self._loaded = True

    def use(self, consumer: Callable[[memoryview], object]) -> object:

        if not self._loaded:
            raise InjectionError("nothing loaded")
        if self._spent:
            raise InjectionError("secret already used once (single-use)")
        self._spent = True
        buf = (ctypes.c_char * self._used_len).from_address(self._addr)
        view = memoryview(buf).toreadonly()
        try:
            return consumer(view)
        finally:

            try:
                view.release()
            except Exception:
                pass
            self._zeroize()

    def _zeroize(self) -> None:
        if self._addr is not None:
            ctypes.memset(self._addr, 0, self.size)

    def _cleanup_fd(self) -> None:
        if self._memfd >= 0:
            try:
                os.close(self._memfd)
            except OSError:
                pass
            self._memfd = -1

    def _teardown(self) -> None:
        if self._addr is not None:

            self._zeroize()
            if self._locked:
                _libc.munlock(ctypes.c_void_p(self._addr), self.size)
                self._locked = False
            _libc.munmap(ctypes.c_void_p(self._addr), self.size)
            self._addr = None
        self._cleanup_fd()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._teardown()

    def __enter__(self) -> "SecretRegion":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self):

        try:
            self.close()
        except Exception:
            pass

    def read_residue(self) -> bytes:

        if self._addr is None:
            raise InjectionError("region unmapped")
        buf = (ctypes.c_char * self.size).from_address(self._addr)
        return bytes(buf)

    @property
    def backing(self) -> str:
        return self._backing

    @property
    def locked(self) -> bool:
        return self._locked

def inject_once(secret_provider: Callable[[], bytes],
                consumer: Callable[[memoryview], object],
                *, require_mlock: bool = True) -> object:

    secret = secret_provider()
    region = SecretRegion(len(secret) or 1, require_mlock=require_mlock)
    try:
        region.load(secret)

        del secret
        return region.use(consumer)
    finally:
        region.close()

