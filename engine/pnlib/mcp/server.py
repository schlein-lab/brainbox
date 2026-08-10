
from __future__ import annotations

import threading
import time as _time
from dataclasses import dataclass
from typing import Callable, Optional

from pnlib import authz
from pnlib.origin import Origin
from pnlib.mcp.auth import LeaseVerifier, VerifiedCall, MCPAuthError
from pnlib.mcp.poison import ToolDescriptor, ToolPinRegistry, ToolPoisonError

class MCPServerError(Exception):
    pass

class MCPAdmissionError(Exception):
    pass

class AdmissionController:

    def __init__(self, capacity: int, refill_per_sec: float = 0.0, *,
                 clock: Optional[Callable[[], float]] = None) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = int(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self._tokens = float(capacity)
        self._last: Optional[float] = None
        self._lock = threading.Lock()

        self._clock: Callable[[], float] = clock if clock is not None else _time.monotonic

    def try_admit(self) -> bool:

        t = float(self._clock())
        with self._lock:
            if self._last is None:
                self._last = t
            elapsed = max(0.0, t - self._last)
            self._last = t
            if self.refill_per_sec > 0:
                self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_sec)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    @property
    def available(self) -> float:
        return self._tokens

@dataclass
class _Tool:
    descriptor: ToolDescriptor
    handler: Callable[[dict, VerifiedCall], object]

@dataclass(frozen=True)
class CallResult:

    tool_name: str
    result: object
    verified: VerifiedCall
    provenance: Origin

class MCPServer:

    def __init__(self, *, audience: str, lease_verifier: LeaseVerifier,
                 pins: ToolPinRegistry, admission: AdmissionController,
                 recorder: Optional[Callable[[dict], None]] = None,
                 component: str = "pn-mcpd") -> None:
        if not isinstance(audience, str) or not audience:
            raise MCPServerError("server audience must be a non-empty string")
        self.audience = audience
        self.lease_verifier = lease_verifier
        self.pins = pins
        self.admission = admission
        self.recorder = recorder
        self.component = component
        self._tools: dict[str, _Tool] = {}
        self.executions = 0
        self.admission_rejections = 0

    def register_tool(self, descriptor: ToolDescriptor,
                      handler: Callable[[dict, VerifiedCall], object]) -> None:

        if not isinstance(descriptor, ToolDescriptor):
            raise MCPServerError("register_tool expects a ToolDescriptor")
        self.pins.approve(descriptor)
        self._tools[descriptor.name] = _Tool(descriptor, handler)

    def _set_live_descriptor(self, name: str, descriptor: ToolDescriptor) -> None:

        if name not in self._tools:
            raise MCPServerError(f"no such tool {name!r}")
        self._tools[name] = _Tool(descriptor, self._tools[name].handler)

    def call(self, tool_name: str, args: dict, *, lease, holder_agent_id: str,
             now: Optional[float] = None) -> CallResult:

        if not self.admission.try_admit():
            self.admission_rejections += 1
            raise MCPAdmissionError(
                f"admission limit reached — call to {tool_name!r} shed (server protecting itself)")

        tool = self._tools.get(tool_name)
        if tool is None:
            raise MCPServerError(f"no such tool {tool_name!r}")

        self.pins.check(tool.descriptor)

        verified = self.lease_verifier.verify(
            lease, holder_agent_id=holder_agent_id,
            server_audience=self.audience, tool_name=tool_name, now=now)

        task_type = "mcp.tool." + tool_name
        caps = {"task_type:" + task_type}
        decision = authz.decide(caps=caps, task_type=task_type,
                                principal="agent:" + holder_agent_id)
        if not decision.allowed:
            raise MCPAuthError(f"authz denied: {decision.reason}")

        result = tool.handler(dict(args or {}), verified)
        self.executions += 1

        provenance = Origin.agent(self.component, agent_id=holder_agent_id)
        self._record("mcp.tool.called", tool_name=tool_name, holder=holder_agent_id,
                     audience=self.audience, scope=sorted(verified.scope))
        return CallResult(tool_name=tool_name, result=result, verified=verified,
                          provenance=provenance)

    def _record(self, event: str, **fields) -> None:
        if self.recorder is None:
            return
        payload = {"event": event}
        payload.update(fields)
        try:
            self.recorder(payload)
        except Exception:
            pass

__all__ = ["MCPServer", "MCPServerError", "MCPAdmissionError",
           "AdmissionController", "CallResult"]
