
from __future__ import annotations

import hashlib
import json
import time as _time
from dataclasses import dataclass
from typing import Callable, Optional

class ToolPoisonError(Exception):

    def __init__(self, message: str, *, tool_name: str, pinned: Optional[str] = None,
                 seen: Optional[str] = None, quarantined: bool = False):
        super().__init__(message)
        self.tool_name = tool_name
        self.pinned = pinned
        self.seen = seen
        self.quarantined = quarantined

@dataclass(frozen=True)
class ToolDescriptor:

    name: str
    description: str
    input_schema: dict

    def canonical_bytes(self) -> bytes:

        payload = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")

    def pin_hash(self) -> str:

        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()

@dataclass(frozen=True)
class Pin:
    tool_name: str
    pin_hash: str
    approved_at: float

class ToolPinRegistry:

    def __init__(self, *, recorder: Optional[Callable[[dict], None]] = None,
                 clock: Callable[[], float] = _time.time) -> None:
        self._pins: dict[str, Pin] = {}
        self._quarantined: dict[str, dict] = {}
        self._recorder = recorder
        self._clock = clock

    def _record(self, event: str, **fields) -> None:
        if self._recorder is None:
            return
        payload = {"event": event, "at": self._clock()}
        payload.update(fields)
        try:
            self._recorder(payload)
        except Exception:

            pass

    def approve(self, descriptor: ToolDescriptor) -> Pin:

        if not isinstance(descriptor, ToolDescriptor):
            raise ToolPoisonError("approve expects a ToolDescriptor", tool_name=str(descriptor))
        if descriptor.name in self._quarantined:
            raise ToolPoisonError(
                f"tool {descriptor.name!r} is quarantined; release it before re-approving",
                tool_name=descriptor.name, quarantined=True)
        pin = Pin(descriptor.name, descriptor.pin_hash(), self._clock())
        self._pins[descriptor.name] = pin
        self._record("tool.approved", tool_name=descriptor.name, pin=pin.pin_hash)
        return pin

    def check(self, descriptor: ToolDescriptor) -> Pin:

        if not isinstance(descriptor, ToolDescriptor):
            raise ToolPoisonError("check expects a ToolDescriptor", tool_name=str(descriptor))
        name = descriptor.name

        if name in self._quarantined:
            raise ToolPoisonError(
                f"tool {name!r} is QUARANTINED (a prior descriptor mutation was detected)",
                tool_name=name, quarantined=True,
                pinned=self._quarantined[name].get("pinned"),
                seen=self._quarantined[name].get("seen"))

        pin = self._pins.get(name)
        if pin is None:
            raise ToolPoisonError(f"tool {name!r} was never approved (no pin)", tool_name=name)

        seen = descriptor.pin_hash()
        if seen != pin.pin_hash:

            self._quarantine(name, reason="descriptor-hash-mismatch",
                             pinned=pin.pin_hash, seen=seen)
            raise ToolPoisonError(
                f"tool {name!r} description/schema changed after approval — BLOCKED "
                f"(pinned {pin.pin_hash}, saw {seen})",
                tool_name=name, pinned=pin.pin_hash, seen=seen, quarantined=True)
        return pin

    def _quarantine(self, name: str, *, reason: str, pinned=None, seen=None) -> None:
        self._quarantined[name] = {"reason": reason, "at": self._clock(),
                                   "pinned": pinned, "seen": seen}
        self._record("tool.quarantined", tool_name=name, reason=reason, pinned=pinned, seen=seen)

    def is_quarantined(self, name: str) -> bool:
        return name in self._quarantined

    @property
    def quarantined(self) -> frozenset:
        return frozenset(self._quarantined)

    def release(self, name: str) -> None:

        if name in self._quarantined:
            del self._quarantined[name]
            self._record("tool.released", tool_name=name)

    def is_pinned(self, name: str) -> bool:
        return name in self._pins

__all__ = ["ToolPoisonError", "ToolDescriptor", "Pin", "ToolPinRegistry"]
