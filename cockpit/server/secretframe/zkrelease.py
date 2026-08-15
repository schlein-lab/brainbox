
from __future__ import annotations
import os
import time
import threading
import secrets as _secrets

import zkchannel
try:
    from inject import SecretRegion
except Exception:
    SecretRegion = None

PENDING, FULFILLED, CONSUMED, DENIED, EXPIRED = "pending", "fulfilled", "consumed", "denied", "expired"
REQUEST_TTL = 120
HOLD_TTL = 90
MAX_SECRET = 64 * 1024

class _Req:
    __slots__ = ("req_id", "principal", "name", "priv", "box_pub", "created",
                 "state", "region", "fulfilled_at", "reason")

    def __init__(self, req_id, principal, name, priv, box_pub):
        self.req_id = req_id
        self.principal = principal
        self.name = name
        self.priv = priv
        self.box_pub = box_pub
        self.created = time.time()
        self.state = PENDING
        self.region = None
        self.fulfilled_at = 0.0
        self.reason = ""

class ReleaseRegistry:

    def __init__(self):
        self._reqs = {}
        self._lock = threading.RLock()

    def request(self, principal, name, ttl=REQUEST_TTL):
        if not principal or not name:
            raise ValueError("principal and name required")
        with self._lock:
            self._sweep_locked()
            priv, box_pub = zkchannel.gen_ephemeral()
            rid = _secrets.token_urlsafe(18)
            self._reqs[rid] = _Req(rid, str(principal), str(name), priv, box_pub)
            return rid, box_pub

    def status(self, principal, req_id):
        with self._lock:
            self._sweep_locked()
            r = self._reqs.get(req_id)
            if not r or r.principal != principal:
                return None
            return r.state

    def consume(self, principal, req_id, consumer):

        with self._lock:
            self._sweep_locked()
            r = self._reqs.get(req_id)
            if not r or r.principal != principal or r.state != FULFILLED or r.region is None:
                return None
            r.state = CONSUMED
            region = r.region
            r.region = None
        try:
            return region.use(consumer)
        finally:
            try:
                region.close()
            except Exception:
                pass
            with self._lock:
                self._reqs.pop(req_id, None)

    def pending(self, principal):
        with self._lock:
            self._sweep_locked()
            return [{"req_id": r.req_id, "name": r.name, "box_pub": r.box_pub, "created": int(r.created)}
                    for r in self._reqs.values()
                    if r.principal == principal and r.state == PENDING]

    def fulfill(self, principal, req_id, sealed):

        with self._lock:
            self._sweep_locked()
            r = self._reqs.get(req_id)
            if not r or r.principal != principal or r.state != PENDING:
                return False
            priv = r.priv
        try:
            plaintext = zkchannel.open_sealed(priv, sealed)
        except Exception:
            return False
        try:
            if SecretRegion is None:
                raise RuntimeError("SecretRegion unavailable")
            region = SecretRegion(min(max(len(plaintext), 1), MAX_SECRET))
            region.load(plaintext)
        finally:

            try:
                pb = bytearray(plaintext)
                for i in range(len(pb)):
                    pb[i] = 0
            except Exception:
                pass
        with self._lock:
            r = self._reqs.get(req_id)
            if not r or r.state != PENDING:
                try:
                    region.close()
                except Exception:
                    pass
                return False
            r.region = region
            r.state = FULFILLED
            r.fulfilled_at = time.time()
            r.priv = None
        return True

    def deny(self, principal, req_id, reason=""):
        with self._lock:
            r = self._reqs.get(req_id)
            if not r or r.principal != principal or r.state != PENDING:
                return False
            r.state = DENIED
            r.reason = str(reason)[:120]
            r.priv = None
            return True

    def _sweep_locked(self):
        now = time.time()
        drop = []
        for rid, r in self._reqs.items():
            if r.state == PENDING and now - r.created > REQUEST_TTL:
                r.state = EXPIRED; r.priv = None; drop.append(rid)
            elif r.state == FULFILLED and now - r.fulfilled_at > HOLD_TTL:
                if r.region is not None:
                    try:
                        r.region.close()
                    except Exception:
                        pass
                    r.region = None
                r.state = EXPIRED; drop.append(rid)
            elif r.state in (CONSUMED, DENIED, EXPIRED) and now - r.created > REQUEST_TTL:
                drop.append(rid)
        for rid in drop:
            self._reqs.pop(rid, None)
