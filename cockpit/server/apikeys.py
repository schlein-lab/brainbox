#!/usr/bin/env python3

import os
import json
import time
import hashlib
import secrets
import threading

KEY_PREFIX = "pak_"

SCOPE_CATALOG = [
    {"prefix": "/api/status",    "label": "Status / Health"},
    {"prefix": "/api/llm",       "label": "LLM chat (governed)"},
    {"prefix": "/v1",            "label": "LLM OpenAI-shim (/v1)"},
    {"prefix": "/api/stt",       "label": "Speech-to-Text"},
    {"prefix": "/api/tts",       "label": "Text-to-Speech"},
    {"prefix": "/api/voice",     "label": "Voice control"},
    {"prefix": "/api/transcript", "label": "Agent transcript (clean turns)"},
    {"prefix": "/api/read",      "label": "Read aloud (text|ref -> text+wav)"},
    {"prefix": "/api/client-actions", "label": "Client-action bus (box->client)"},
    {"prefix": "/api/displays",  "label": "Display targets (show / restore-idle)"},
    {"prefix": "/api/autonomy",  "label": "Per-session autonomy dial (L0-L5)"},
    {"prefix": "/api/session-cells", "label": "Session-cell roster + lifecycle feed"},
    {"prefix": "/api/queue",     "label": "Job queue (submit / own)"},
    {"prefix": "/api/jobs",      "label": "Jobs + artifacts (own)"},

    {"prefix": "/api/v1",              "label": "Public API v1 (full: submit + read + cancel)"},
    {"prefix": "POST /api/v1/jobs",    "label": "Public API v1 — submit jobs only"},
    {"prefix": "GET /api/v1",          "label": "Public API v1 — read only (jobs, status, results)"},
    {"prefix": "/api/sessions",  "label": "Session history"},
    {"prefix": "/api/funding",   "label": "Funding / quota"},
    {"prefix": "/screen/stream", "label": "Screen stream (view)"},
    {"prefix": "/ws/vnc",        "label": "VNC passthrough"},
    {"prefix": "/api/secret",    "label": "Secrets vault", "danger": True},
    {"prefix": "/api/admin",     "label": "Admin plane", "danger": True},
]

def valid_scope(s):

    if not isinstance(s, str):
        return False
    sp = s.split(" ", 1)
    if len(sp) == 2 and sp[0].isalpha() and sp[0].isupper():
        return sp[1].startswith("/")
    return s.startswith("/")

def _hash(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

class KeyStore:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "api_keys.json")
        self._lock = threading.Lock()
        self._keys = self._load()
        self._rebuild_index()
        self._mtime = self._file_mtime()
        self._hits = {}

    def _rebuild_index(self):
        self._by_hash = {r["hash"]: kid for kid, r in self._keys.items() if not r.get("revoked")}

    def _file_mtime(self):
        try:
            return os.stat(self.path).st_mtime
        except OSError:
            return 0

    def _maybe_reload(self):

        m = self._file_mtime()
        if m and m != self._mtime:
            with self._lock:
                m2 = self._file_mtime()
                if m2 and m2 != self._mtime:
                    self._keys = self._load()
                    self._rebuild_index()
                    self._mtime = m2

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
            os.write(fd, json.dumps(self._keys).encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, self.path)
        self._mtime = self._file_mtime()

    def create(self, uid, label="", scopes=None, role=None, ttl_days=0, rate_per_min=0):

        raw = KEY_PREFIX + secrets.token_urlsafe(32)
        kid = secrets.token_hex(4)
        now = int(time.time())
        try:
            ttl_days = int(ttl_days or 0); rate_per_min = int(rate_per_min or 0)
        except (TypeError, ValueError):
            ttl_days = rate_per_min = 0
        rec = {
            "hash": _hash(raw), "uid": str(uid), "label": (label or "")[:80],
            "scopes": [s for s in (scopes or []) if valid_scope(s)],
            "role": role or "", "created": now, "last_used": 0, "revoked": False,
            "expires_at": (now + ttl_days * 86400) if ttl_days > 0 else 0,
            "rate_per_min": max(0, rate_per_min),
        }
        with self._lock:
            self._keys[kid] = rec
            self._by_hash[rec["hash"]] = kid
            self._save()
        return kid, raw

    def resolve(self, raw):

        if not raw or not raw.startswith(KEY_PREFIX):
            return None
        self._maybe_reload()
        kid = self._by_hash.get(_hash(raw))
        if not kid:
            return None
        rec = self._keys.get(kid)
        if not rec or rec.get("revoked"):
            return None
        exp = rec.get("expires_at") or 0
        if exp and int(time.time()) > exp:
            return None
        rec["last_used"] = int(time.time())
        return {"id": kid, "uid": rec["uid"], "scopes": rec.get("scopes") or [],
                "role": rec.get("role") or "", "rate_per_min": rec.get("rate_per_min") or 0}

    def rate_ok(self, kid):

        rec = self._keys.get(kid)
        if not rec:
            return False
        rpm = rec.get("rate_per_min") or 0
        if rpm <= 0:
            return True
        now = time.time(); cutoff = now - 60.0
        hits = self._hits.setdefault(kid, [])
        while hits and hits[0] < cutoff:
            hits.pop(0)
        if len(hits) >= rpm:
            return False
        hits.append(now)
        return True

    @staticmethod
    def scope_ok(entry, path, method=None):

        scopes = (entry or {}).get("scopes") or []
        if not scopes:
            return True
        m = (method or "").upper()
        for s in scopes:
            s_method, s_path = "", s
            sp = s.split(" ", 1)
            if len(sp) == 2 and sp[0].isalpha() and sp[0].isupper():
                s_method, s_path = sp[0], sp[1]
            if s_method and m and s_method != m:
                continue
            if path == s_path or path.startswith(s_path.rstrip("/") + "/"):
                return True
        return False

    def list(self, uid=None):
        self._maybe_reload()
        out = []
        for kid, r in self._keys.items():
            if uid is not None and r.get("uid") != uid:
                continue
            out.append({"id": kid, "uid": r.get("uid"), "label": r.get("label", ""),
                        "scopes": r.get("scopes") or [], "created": r.get("created", 0),
                        "last_used": r.get("last_used", 0), "revoked": bool(r.get("revoked")),
                        "expires_at": r.get("expires_at", 0), "rate_per_min": r.get("rate_per_min", 0)})
        return sorted(out, key=lambda k: k["created"], reverse=True)

    def revoke(self, kid, owner_uid=None):

        with self._lock:
            rec = self._keys.get(kid)
            if not rec:
                return False
            if owner_uid is not None and rec.get("uid") != owner_uid:
                return False
            if not rec.get("revoked"):
                rec["revoked"] = True
                self._by_hash.pop(rec["hash"], None)
                self._save()
            return True

    def update(self, kid, scopes=None, label=None, rate_per_min=None, ttl_days=None, owner_uid=None):

        with self._lock:
            rec = self._keys.get(kid)
            if not rec or rec.get("revoked"):
                return None
            exp = rec.get("expires_at") or 0
            if exp and int(time.time()) > exp:
                return None
            if owner_uid is not None and rec.get("uid") != owner_uid:
                return None
            if scopes is not None:
                rec["scopes"] = [s for s in scopes if valid_scope(s)]
            if label is not None:
                rec["label"] = str(label)[:80]
            if rate_per_min is not None:
                try:
                    rec["rate_per_min"] = max(0, int(rate_per_min))
                except (TypeError, ValueError):
                    pass
            if ttl_days is not None:
                try:
                    td = int(ttl_days)
                    rec["expires_at"] = (int(time.time()) + td * 86400) if td > 0 else 0
                except (TypeError, ValueError):
                    pass
            self._save()
            return {"id": kid, "uid": rec.get("uid"), "label": rec.get("label", ""),
                    "scopes": rec.get("scopes") or [], "created": rec.get("created", 0),
                    "last_used": rec.get("last_used", 0), "revoked": bool(rec.get("revoked")),
                    "expires_at": rec.get("expires_at", 0), "rate_per_min": rec.get("rate_per_min", 0)}
