
from __future__ import annotations
import time
import threading
import secrets as _secrets

OPEN, OFFERED, DONE, EXPIRED = "open", "offered", "done", "expired"
LINK_TTL = 300
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

class _Link:
    __slots__ = ("link_id", "code", "principal", "new_pub", "offer", "created", "state")

    def __init__(self, link_id, code, principal, new_pub):
        self.link_id = link_id
        self.code = code
        self.principal = principal
        self.new_pub = new_pub
        self.offer = None
        self.created = time.time()
        self.state = OPEN

class LinkRelay:
    def __init__(self):
        self._links = {}
        self._lock = threading.RLock()

    def _code(self):
        return "-".join("".join(_secrets.choice(_CODE_ALPHABET) for _ in range(3)) for _ in range(2))

    def start(self, principal, new_pub):

        if not principal or not new_pub or not isinstance(new_pub, str):
            raise ValueError("principal and new_pub required")
        with self._lock:
            self._sweep_locked()

            code = self._code()
            tries = 0
            while any(l.code == code and l.state == OPEN for l in self._links.values()) and tries < 8:
                code = self._code(); tries += 1
            link_id = _secrets.token_urlsafe(18)
            self._links[link_id] = _Link(link_id, code, str(principal), new_pub)
            return link_id, code

    def resolve(self, principal, code):

        code = (code or "").strip().upper()
        with self._lock:
            self._sweep_locked()
            for l in self._links.values():
                if l.principal == principal and l.code == code and l.state == OPEN:
                    return {"link_id": l.link_id, "new_pub": l.new_pub}
            return None

    def offer(self, principal, link_id, sealed):

        if not isinstance(sealed, dict):
            return False
        with self._lock:
            self._sweep_locked()
            l = self._links.get(link_id)
            if not l or l.principal != principal or l.state != OPEN:
                return False
            l.offer = sealed
            l.state = OFFERED
            return True

    def get(self, principal, link_id):

        with self._lock:
            self._sweep_locked()
            l = self._links.get(link_id)
            if not l or l.principal != principal:
                return None
            out = {"new_pub": l.new_pub, "offer": l.offer, "state": l.state}
            if l.state == OFFERED and l.offer is not None:
                l.state = DONE
            return out

    def _sweep_locked(self):
        now = time.time()
        for lid in [k for k, l in self._links.items()
                    if now - l.created > LINK_TTL or l.state in (DONE, EXPIRED)
                    and now - l.created > 30]:
            self._links.pop(lid, None)
