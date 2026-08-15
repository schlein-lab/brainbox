
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, Optional

from ..waist import BrainRequest, BrainReply, StreamEvent

@dataclass(frozen=True)
class Capabilities:

    name: str
    models: tuple = ()
    default_model: Optional[str] = None
    streaming: bool = False
    tools: bool = False
    vision: bool = False
    system_prompt: bool = False
    max_context: Optional[int] = None
    routing_kinds: tuple = ("loose",)
    byo_kinds: tuple = ()
    notes: str = ""

    def supports_model(self, model: Optional[str]) -> bool:

        if model is None or not self.models:
            return True
        return model in self.models

    def to_dict(self) -> dict:
        return {
            "name": self.name, "models": list(self.models),
            "default_model": self.default_model, "streaming": self.streaming,
            "tools": self.tools, "vision": self.vision, "system_prompt": self.system_prompt,
            "max_context": self.max_context, "routing_kinds": list(self.routing_kinds),
            "byo_kinds": list(self.byo_kinds), "notes": self.notes,
        }

class Provider(ABC):

    @property
    @abstractmethod
    def capabilities(self) -> Capabilities:
        ...

    @abstractmethod
    def generate(self, req: BrainRequest) -> BrainReply:

        ...

    def stream(self, req: BrainRequest) -> Iterator[StreamEvent]:

        reply = self.generate(req)
        if reply.ok:
            yield StreamEvent("message", text=reply.text or "",
                              data={"model": reply.model} if reply.model else {})
            if reply.usage:
                yield StreamEvent("usage", data=reply.usage)
            yield StreamEvent("done", data={})
        else:
            yield StreamEvent("error", data={"message": reply.error or "",
                                             "auth": bool(reply.auth)})

    def health(self) -> dict:

        return {"ok": True, "provider": self.capabilities.name, "health": "unknown"}
