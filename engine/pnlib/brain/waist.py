
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

ROUTING_KINDS = ("loose", "dedicated")

def _norm_kind(kind: Any) -> str:

    return kind if kind in ROUTING_KINDS else "loose"

@dataclass
class BrainRequest:

    prompt: str
    model: Optional[str] = None
    kind: str = "loose"
    timeout: Optional[int] = None
    stream: bool = False
    system: Optional[str] = None
    tools: Optional[list] = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        self.kind = _norm_kind(self.kind)

    def with_defaults(self, model: str, timeout: int) -> "BrainRequest":

        return BrainRequest(
            prompt=self.prompt,
            model=self.model or model,
            kind=self.kind,
            timeout=int(self.timeout or timeout),
            stream=self.stream,
            system=self.system,
            tools=self.tools,
            metadata=dict(self.metadata),
        )

STREAM_KINDS = (
    "message_start", "text_delta", "message", "usage",
    "rate_limit", "error", "done", "raw",
)

@dataclass
class StreamEvent:
    kind: str
    text: Optional[str] = None
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d: dict = {"kind": self.kind}
        if self.text is not None:
            d["text"] = self.text
        if self.data:
            d["data"] = self.data
        return d

@dataclass
class BrainReply:

    ok: bool
    text: Optional[str] = None
    error: Optional[str] = None
    auth: bool = False
    provider: str = ""
    model: Optional[str] = None
    session: Optional[int] = None
    routing: Optional[str] = None
    usage: Optional[dict] = None
    events: Optional[list] = None
    _source: Optional[dict] = None

    @classmethod
    def from_llmpool(cls, res: dict, *, provider: str = "", model: Optional[str] = None,
                     usage: Optional[dict] = None,
                     events: Optional[list] = None) -> "BrainReply":

        return cls(
            ok=bool(res.get("ok")),
            text=res.get("text"),
            error=res.get("error"),
            auth=bool(res.get("auth", False)),
            provider=provider,
            model=model,
            session=res.get("session"),
            routing=res.get("routing"),
            usage=usage,
            events=events,
            _source=dict(res),
        )

    def to_llmpool_dict(self) -> dict:

        if self._source is not None:
            return dict(self._source)
        d: dict = {"ok": self.ok}
        if self.ok:
            if self.text is not None:
                d["text"] = self.text
        else:
            if self.error is not None:
                d["error"] = self.error
            if self.auth:
                d["auth"] = True
        if self.session is not None:
            d["session"] = self.session
        if self.routing is not None:
            d["routing"] = self.routing
        return d

    def to_dict(self) -> dict:

        d: dict = {"ok": self.ok, "provider": self.provider}
        for k in ("text", "error", "model", "session", "routing", "usage"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        if self.auth:
            d["auth"] = True
        if self.events:
            d["events"] = [e.to_dict() if isinstance(e, StreamEvent) else e
                           for e in self.events]
        return d

class ProviderRegistry:

    def __init__(self):
        self._providers: list = []
        self._by_name: dict = {}

    def register(self, provider) -> None:
        name = provider.capabilities.name
        if name in self._by_name:

            self._providers = [provider if p.capabilities.name == name else p
                               for p in self._providers]
        else:
            self._providers.append(provider)
        self._by_name[name] = provider

    def names(self) -> list:
        return [p.capabilities.name for p in self._providers]

    def get(self, name: str):
        return self._by_name.get(name)

    def select(self, config: Optional[dict] = None):

        if not self._providers:
            raise LookupError("no providers registered")
        first = self._providers[0]

        if len(self._providers) == 1 or not config:
            return first
        name = config.get("provider")
        if name and name in self._by_name:
            return self._by_name[name]
        for cand in (config.get("order") or []):
            if cand in self._by_name:
                return self._by_name[cand]
        return first

    def generate(self, req: BrainRequest, config: Optional[dict] = None) -> BrainReply:

        return self.select(config).generate(req)

    def stream(self, req: BrainRequest, config: Optional[dict] = None) -> Iterator[StreamEvent]:
        return self.select(config).stream(req)
