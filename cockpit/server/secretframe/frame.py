

from __future__ import annotations

import enum
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

class FrameClass(enum.Enum):

    CONTROL = "control"
    TRANSCRIPT = "transcript"
    LEDGER = "ledger"
    ACCOUNTING = "accounting"
    SECRET = "secret"

NON_PERSISTABLE = frozenset({FrameClass.SECRET})

class SecretLeak(Exception):
    pass

@dataclass
class Frame:

    cls: FrameClass
    kind: str
    payload: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    seq: Optional[int] = None

    def _guard(self, sink_name: str) -> None:
        if self.cls in NON_PERSISTABLE:
            raise SecretLeak(
                f"refusing to serialize a {self.cls.value} frame to {sink_name} "
                f"(kind={self.kind!r}); SECRET frames are excluded from "
                f"transcripts / The Record / accounting (design §8.3, invariant §9.2)"
            )

    def to_json(self) -> str:

        self._guard("json")
        return json.dumps(
            {"cls": self.cls.value, "kind": self.kind, "payload": self.payload,
             "ts": self.ts, "seq": self.seq},
            sort_keys=True,
        )

    def to_transcript(self) -> str:

        self._guard("transcript")

        return str(self.payload.get("text", ""))

    def to_ledger(self) -> dict:

        self._guard("ledger")
        return {"seq": self.seq, "kind": self.kind, "ts": self.ts,
                "payload": self.payload}

    def is_secret(self) -> bool:
        return self.cls in NON_PERSISTABLE

    @classmethod
    def secret_inject(cls, target: Any, credential_name: str, seq: Optional[int] = None) -> "Frame":

        return cls(
            cls=FrameClass.SECRET,
            kind="secret.inject",
            payload={"target": target, "credential_name": credential_name},
            seq=seq,
        )

Sink = Callable[[Frame], None]

def secret_scrubbing_sink(inner: Sink, *, on_drop: Optional[Callable[[Frame], None]] = None) -> Sink:

    def _sink(frame: Frame) -> None:
        if frame.cls in NON_PERSISTABLE:
            if on_drop is not None:
                on_drop(frame)
            return
        inner(frame)

    return _sink

def scrub_stream(frames: Iterable[Frame]) -> Iterable[Frame]:

    for f in frames:
        if f.cls not in NON_PERSISTABLE:
            yield f

