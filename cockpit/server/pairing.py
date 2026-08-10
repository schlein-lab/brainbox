#!/usr/bin/env python3

import os
import json
import time
import hashlib
import secrets
import threading

PAIR_MAX_ATTEMPTS = 5
PAIR_LOCKOUT_S = 900

def _hash(raw):
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()

class PairingStore:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "pairings.json")
        self._lock = threading.Lock()
        self._codes = self._load()

    def _load(self):
        try:
            with open(self.path) as fh:
                d = json.load(fh)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(self._codes).encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, self.path)

    def _find(self, code):
        h = _hash(code)
        for pid, rec in self._codes.items():
            if rec.get("hash") == h:
                return pid, rec
        return None, None

    def mint(self, uid, scopes=None, label="", ttl_s=600, minted_by="", key_ttl_days=0, rate_per_min=0):

        code = secrets.token_urlsafe(24)
        pid = secrets.token_hex(4)
        now = int(time.time())
        try:
            ttl_s = int(ttl_s or 600); key_ttl_days = int(key_ttl_days or 0); rate_per_min = int(rate_per_min or 0)
        except (TypeError, ValueError):
            ttl_s, key_ttl_days, rate_per_min = 600, 0, 0
        rec = {"hash": _hash(code), "uid": str(uid),
               "scopes": [s for s in (scopes or []) if isinstance(s, str) and s.startswith("/")],
               "label": (label or "")[:80], "minted_by": str(minted_by or ""),
               "created": now, "expires": now + max(30, ttl_s), "used": 0,
               "attempts": 0, "locked_until": 0,
               "key_ttl_days": max(0, key_ttl_days), "rate_per_min": max(0, rate_per_min)}
        with self._lock:
            self._codes[pid] = rec
            self._save()
        return pid, code

    def peek(self, code):

        with self._lock:
            pid, rec = self._find(code)
            if not rec:
                return None
            now = int(time.time())
            if rec.get("used") or now > rec.get("expires", 0):
                return None
            if rec.get("locked_until", 0) and now < rec["locked_until"]:
                return None
            return dict(rec, pid=pid)

    def redeem(self, code):

        with self._lock:
            pid, rec = self._find(code)
            now = int(time.time())
            if not rec:
                return None
            if rec.get("locked_until", 0) and now < rec["locked_until"]:
                return None
            if rec.get("used") or now > rec.get("expires", 0):
                return None
            rec["used"] = now
            self._save()
            return dict(rec, pid=pid)

    def fail(self, code):

        with self._lock:
            pid, rec = self._find(code)
            if not rec:
                return
            rec["attempts"] = int(rec.get("attempts", 0)) + 1
            if rec["attempts"] >= PAIR_MAX_ATTEMPTS:
                rec["locked_until"] = int(time.time()) + PAIR_LOCKOUT_S
            self._save()

    def pending(self, uid=None):

        now = int(time.time())
        out = []
        with self._lock:
            for pid, rec in self._codes.items():
                if rec.get("used") or now > rec.get("expires", 0):
                    continue
                if uid is not None and rec.get("uid") != uid:
                    continue
                out.append({"pid": pid, "uid": rec.get("uid"), "label": rec.get("label", ""),
                            "scopes": rec.get("scopes") or [], "created": rec.get("created", 0),
                            "expires": rec.get("expires", 0), "minted_by": rec.get("minted_by", ""),
                            "attempts": rec.get("attempts", 0)})
        return sorted(out, key=lambda r: r["created"], reverse=True)

    def cancel(self, pid, owner_uid=None):

        with self._lock:
            rec = self._codes.get(pid)
            if not rec:
                return False
            if owner_uid is not None and rec.get("uid") != owner_uid:
                return False
            self._codes.pop(pid, None)
            self._save()
            return True

    def gc(self):

        now = int(time.time())
        with self._lock:
            dead = [pid for pid, rec in self._codes.items()
                    if (rec.get("used") and now - int(rec.get("used") or 0) > 3600)
                    or now - int(rec.get("expires") or 0) > 3600]
            if dead:
                for pid in dead:
                    self._codes.pop(pid, None)
                self._save()
