
from __future__ import annotations

import json
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from pnlib import ipc
from pnlib.brain.waist import BrainRequest
from pnlib.brain.router import BrainRouter, RouteConstraints, RouteDecision

_LIVE_SOCKET_NAMES = frozenset({"pn-llmd.sock", "pnd.sock", "zyrkel-signals.sock"})
_DEFAULT_SHADOW_BASENAME = "pn-llmd-shadow.sock"

def _runtime_dir() -> str:
    return os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"

def default_socket_path() -> str:

    return os.path.join(_runtime_dir(), _DEFAULT_SHADOW_BASENAME)

def _assert_safe_socket_path(path: str) -> None:
    base = os.path.basename(path)
    if base in _LIVE_SOCKET_NAMES:
        raise ValueError(f"refusing to bind a LIVE socket name ({base!r}); the shadow harness must "
                         f"use its own alt socket (e.g. {_DEFAULT_SHADOW_BASENAME})")

@dataclass
class ShadowConfig:

    enabled: bool = False
    sample_rate: float = 0.0
    socket_path: Optional[str] = None

    def resolved_socket_path(self) -> str:
        return self.socket_path or default_socket_path()

@dataclass
class ShadowObservation:
    sampled: bool
    request: dict = field(default_factory=dict)
    decision: Optional[RouteDecision] = None
    ts: float = 0.0

    def to_dict(self) -> dict:
        return {"sampled": self.sampled, "request": self.request,
                "decision": self.decision.to_dict() if self.decision is not None else None,
                "ts": self.ts}

def _build_request(obj: Any):

    if isinstance(obj, BrainRequest):
        return obj, RouteConstraints(), {"prompt": obj.prompt, "model": obj.model, "kind": obj.kind}
    if isinstance(obj, dict):
        req = BrainRequest(prompt=obj.get("prompt", ""), model=obj.get("model"),
                           kind=obj.get("kind", "loose"))
        constraints = RouteConstraints.from_obj(obj.get("constraints"))
        return req, constraints, dict(obj)
    raise TypeError(f"request must be BrainRequest|dict, got {type(obj)!r}")

class ShadowHarness:

    def __init__(self, router: BrainRouter, config: Optional[ShadowConfig] = None,
                 rng: Any = None):
        self.router = router
        self.config = config or ShadowConfig()
        self._rng = rng
        self._lock = threading.Lock()
        self._seen = 0
        self._sampled = 0
        self._by_provider: dict = {}
        self._last: Optional[ShadowObservation] = None

        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._bound_path: Optional[str] = None

    def should_sample(self) -> bool:

        c = self.config
        if not c.enabled or c.sample_rate <= 0.0:
            return False
        if c.sample_rate >= 1.0:
            return True
        r = self._rng.random() if self._rng is not None else __import__("random").random()
        return r < c.sample_rate

    def observe(self, request: Any, now: Optional[float] = None) -> Optional[ShadowObservation]:

        with self._lock:
            self._seen += 1
        if not self.should_sample():
            return None
        req, constraints, raw = _build_request(request)
        decision = self.router.route(req, constraints=constraints, now=now)
        obs = ShadowObservation(sampled=True, request=raw, decision=decision,
                                ts=time.time() if now is None else now)
        with self._lock:
            self._sampled += 1
            key = decision.name or f"({decision.reason})"
            self._by_provider[key] = self._by_provider.get(key, 0) + 1
            self._last = obs
        return obs

    def stats(self) -> dict:
        with self._lock:
            return {"seen": self._seen, "sampled": self._sampled,
                    "by_provider": dict(self._by_provider),
                    "socket": self._bound_path, "running": self._running}

    @property
    def last(self) -> Optional[ShadowObservation]:
        return self._last

    def start(self) -> str:

        if self._running:
            return self._bound_path or ""
        path = self.config.resolved_socket_path()
        _assert_safe_socket_path(path)
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        s.listen(8)
        s.settimeout(0.5)
        self._sock = s
        self._bound_path = path
        self._running = True
        self._thread = threading.Thread(target=self._serve, name="pn-brain-shadow", daemon=True)
        self._thread.start()
        return path

    def _serve(self) -> None:
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    self._handle_conn(conn)
                except Exception:
                    pass

    def _handle_conn(self, conn: socket.socket) -> None:
        buf = b""
        conn.settimeout(2.0)
        while self._running:
            try:
                chunk = conn.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                resp = self._handle_line(line)
                conn.sendall(json.dumps(resp).encode() + b"\n")

    def _handle_line(self, line: bytes) -> dict:
        try:
            obj = json.loads(line.decode())
        except Exception as e:
            return {"ok": False, "error": f"bad json: {e}"}
        if not isinstance(obj, dict):
            return {"ok": False, "error": "request must be a JSON object"}

        if obj.get("op") == "ping":
            return {"ok": True, "pong": True, "shadow": True}
        obs = self.observe(obj)
        if obs is None:
            return {"ok": True, "sampled": False, "shadow": True}
        return {"ok": True, "sampled": True, "shadow": True,
                "decision": obs.decision.to_dict()}

    def stop(self) -> None:

        self._running = False
        t = self._thread
        if t is not None:
            t.join(timeout=2.0)
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._bound_path:
            try:
                os.unlink(self._bound_path)
            except OSError:
                pass

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False

def shadow_query(socket_path: str, request: dict, timeout: float = 3.0) -> dict:

    try:
        r = ipc.send_request(request, timeout=timeout, path=socket_path)
    except json.JSONDecodeError:
        return {}
    return {} if (not r.get("ok") and r.get("error") == "empty") else r
