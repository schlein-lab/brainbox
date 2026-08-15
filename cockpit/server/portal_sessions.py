
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import time

IDLE_RETENTION_S = 28 * 86400
DOWNLOAD_WINDOW_S = 14 * 86400
KEEP_GRACE_S = 72 * 3600
KEPT_EXTENSION_S = 28 * 86400

_SID_RE = re.compile(r"\A[a-z0-9]{6,16}\Z")

STATE_ACTIVE = "active"
STATE_EXPIRING = "expiring"
STATE_DOWNLOAD = "download_offered"
STATE_DELETED = "deleted"

def _now(now=None) -> float:
    return time.time() if now is None else float(now)

def _uid_tag(uid: str) -> str:

    head = re.sub(r"[^A-Za-z0-9]", "", str(uid))[:20] or "u"
    digest = hashlib.sha256(str(uid).encode("utf-8")).hexdigest()[:12]
    return "%s_%s" % (head, digest)

def new_sid(seed) -> str:

    return hashlib.sha256(str(seed).encode("utf-8")).hexdigest()[:12]

class SessionStore:

    def __init__(self, base_dir: str, uid: str, kind: str = "cockpit"):
        self.base = base_dir
        self.uid = uid
        self.kind = re.sub(r"[^a-z]", "", str(kind))[:12] or "cockpit"
        self.path = os.path.join(base_dir, "sessions.json")
        self.transcripts_dir = os.path.join(base_dir, "transcripts")
        self._lock_path = os.path.join(base_dir, "sessions.lock")

    @contextlib.contextmanager
    def _locked(self):

        os.makedirs(self.base, exist_ok=True)
        lf = open(self._lock_path, "w")
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
            finally:
                lf.close()

    def _load(self) -> list:
        try:
            with open(self.path) as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, sessions: list) -> None:
        os.makedirs(self.base, exist_ok=True)
        tmp = "%s.tmp.%d" % (self.path, os.getpid())
        with open(tmp, "w") as f:
            json.dump(sessions, f, indent=2)
        os.replace(tmp, self.path)

    def tmux_name(self, sid: str) -> str:
        if not _SID_RE.match(sid):
            raise ValueError(f"bad session id {sid!r}")
        return "%s-%s-%s" % (_uid_tag(self.uid), self.kind, sid)

    def transcript_path(self, sid: str) -> str:
        if not _SID_RE.match(sid):
            raise ValueError(f"bad session id {sid!r}")
        return os.path.join(self.transcripts_dir, "%s.log" % sid)

    def list(self) -> list:

        live = [s for s in self._load() if s.get("state") != STATE_DELETED]
        return sorted(live, key=lambda s: s.get("last_active", 0), reverse=True)

    def get(self, sid: str):

        for s in self._load():
            if s.get("id") == sid and s.get("state") != STATE_DELETED:
                return s
        return None

    def create(self, title: str = None, *, sid: str = None, now=None) -> dict:

        t = _now(now)
        with self._locked():
            sessions = self._load()
            _sid = sid
            if _sid is None:
                _sid = new_sid("%s|%s|%s|%d" % (self.uid, self.kind, title or "", len(sessions)))
            if not _SID_RE.match(_sid):
                raise ValueError(f"bad session id {_sid!r}")
            if any(s.get("id") == _sid for s in sessions):
                raise ValueError(f"session {_sid!r} already exists")
            s = {
                "id": _sid,
                "title": (title or "New session").strip()[:120],
                "kind": self.kind,
                "tmux": self.tmux_name(_sid),
                "created": t,
                "last_active": t,
                "kept_until": 0,
                "grace_started_at": 0,
                "download_offered_at": 0,
                "downloaded": False,
                "state": STATE_ACTIVE,
            }
            sessions.append(s)
            self._save(sessions)
        os.makedirs(self.transcripts_dir, exist_ok=True)
        return s

    def touch(self, sid: str, now=None) -> dict:

        t = _now(now)
        with self._locked():
            sessions = self._load()
            out = None
            for s in sessions:
                if s.get("id") == sid and s.get("state") != STATE_DELETED:
                    s["last_active"] = t
                    if s.get("state") in (STATE_EXPIRING, STATE_DOWNLOAD):
                        s["state"] = STATE_ACTIVE
                        s["grace_started_at"] = 0
                        s["download_offered_at"] = 0
                    out = s
                    break
            if out is not None:
                self._save(sessions)
        return out

    def rename(self, sid: str, title: str) -> dict:
        with self._locked():
            sessions = self._load()
            out = None
            for s in sessions:
                if s.get("id") == sid and s.get("state") != STATE_DELETED:
                    s["title"] = (title or "").strip()[:120] or s.get("title")
                    out = s
                    break
            if out is not None:
                self._save(sessions)
        return out

    def last_active(self):

        live = [s for s in self.list() if not s.get("archived")]
        return live[0] if live else None

    def mark_kept(self, sid: str, now=None) -> dict:

        t = _now(now)
        with self._locked():
            sessions = self._load()
            out = None
            for s in sessions:
                if s.get("id") == sid:
                    if s.get("state") == STATE_DELETED:
                        break
                    s["kept_until"] = t + KEPT_EXTENSION_S
                    s["state"] = STATE_ACTIVE
                    s["grace_started_at"] = 0
                    s["download_offered_at"] = 0
                    s["last_active"] = max(s.get("last_active", 0), t)
                    out = s
                    break
            if out is not None:
                self._save(sessions)
        return out

    def set_archived(self, sid: str, on: bool = True) -> dict:

        with self._locked():
            sessions = self._load()
            out = None
            for s in sessions:
                if s.get("id") == sid and s.get("state") != STATE_DELETED:
                    s["archived"] = bool(on)
                    out = s
                    break
            if out is not None:
                self._save(sessions)
        return out

    def delete(self, sid: str) -> dict:

        with self._locked():
            sessions = self._load()
            out = None
            for s in sessions:
                if s.get("id") == sid and s.get("state") != STATE_DELETED:
                    s["state"] = STATE_DELETED
                    self._purge_artifacts(s)
                    out = s
                    break
            if out is not None:
                self._save(sessions)
        return out

    def _purge_artifacts(self, s) -> None:

        sid = s.get("id")
        try:
            p = self.transcript_path(sid)
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
        tm = s.get("tmux")
        if tm:
            try:
                subprocess.run(["tmux", "kill-session", "-t", tm], capture_output=True, timeout=5)
            except Exception:
                pass

    def apply_scan(self, actions: dict, now=None) -> None:

        t = _now(now)
        with self._locked():
            sessions = self._load()
            by_id = {s.get("id"): s for s in sessions}
            changed = False
            for sid, act in actions.items():
                s = by_id.get(sid)
                if s is None or s.get("state") == STATE_DELETED:
                    continue

                if s.get("kept_until") and t < s["kept_until"]:
                    continue
                action = act.get("action")
                if action == "delete":

                    if s.get("state") != STATE_DOWNLOAD:
                        continue
                    s["state"] = STATE_DELETED
                    self._purge_artifacts(s)
                elif action in ("warn_email", "enter_grace"):
                    if s.get("state") == STATE_ACTIVE:
                        s["state"] = STATE_EXPIRING
                        s["grace_started_at"] = t
                elif action == "offer_download":
                    if s.get("state") == STATE_EXPIRING:
                        s["state"] = STATE_DOWNLOAD
                        s["download_offered_at"] = t
                elif action == "copy_to_client":
                    s["state"] = STATE_DELETED
                    self._purge_artifacts(s)
                else:
                    continue
                changed = True
            if changed:
                self._save(sessions)

def retention_scan(sessions: list, *, now: float, has_client: bool) -> dict:

    out = {}
    for s in sessions:
        if s.get("state") == STATE_DELETED:
            continue
        sid = s.get("id")
        last = s.get("last_active", 0)
        kept_until = s.get("kept_until", 0)
        state = s.get("state", STATE_ACTIVE)
        if kept_until and now < kept_until:
            continue
        if state == STATE_ACTIVE:
            if now - last < IDLE_RETENTION_S:
                continue
            if has_client:
                out[sid] = {"action": "copy_to_client",
                            "reason": "idle>=28d, client present -> hand off + delete"}
            else:
                out[sid] = {
                    "action": "enter_grace",
                    "reason": "idle>=28d, portal-only -> 72h Behalten grace",
                    "email": {
                        "template": "session_expiring",
                        "subject": "Ihre Portal-Session wird in 72h gelöscht",
                        "keep_deadline": now + KEEP_GRACE_S,
                        "session_id": sid,
                        "title": s.get("title"),
                    },
                }
        elif state == STATE_EXPIRING:
            started = s.get("grace_started_at", 0) or last
            if now >= started + KEEP_GRACE_S:
                out[sid] = {"action": "offer_download",
                            "reason": "72h grace elapsed -> 14d download window",
                            "download_deadline": now + DOWNLOAD_WINDOW_S}
        elif state == STATE_DOWNLOAD:
            offered = s.get("download_offered_at", 0) or last
            if now >= offered + DOWNLOAD_WINDOW_S:
                out[sid] = {"action": "delete", "reason": "14d download window elapsed -> delete"}
    return out

__all__ = [
    "SessionStore", "retention_scan", "new_sid",
    "IDLE_RETENTION_S", "DOWNLOAD_WINDOW_S", "KEEP_GRACE_S", "KEPT_EXTENSION_S",
    "STATE_ACTIVE", "STATE_EXPIRING", "STATE_DOWNLOAD", "STATE_DELETED",
]
