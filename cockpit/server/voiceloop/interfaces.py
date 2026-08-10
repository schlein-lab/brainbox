

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Callable, Optional

@dataclass
class Transcript:

    text: str
    final: bool = True
    confidence: float = 1.0

class SttEngine(abc.ABC):

    @abc.abstractmethod
    def transcribe(self, audio_ref: str) -> Transcript:
        ...

class VoicedStt(SttEngine):

    def __init__(self, endpoint: str = "unix:///run/voiced.sock") -> None:
        self.endpoint = endpoint

    def transcribe(self, audio_ref: str) -> Transcript:

        raise NotImplementedError(
            "VoicedStt targets the box's voiced/whisper endpoint; wire in Phase 2 "
            "increment 2 (TODO(voiced-rpc)). Use FakeStt for the loop/tests."
        )

class FakeStt(SttEngine):

    def __init__(self, script: Optional[dict] = None) -> None:
        self.script = dict(script or {})
        self.calls: list[str] = []

    def feed(self, audio_ref: str, text: str, confidence: float = 1.0) -> None:
        self.script[audio_ref] = Transcript(text=text, final=True, confidence=confidence)

    def transcribe(self, audio_ref: str) -> Transcript:
        self.calls.append(audio_ref)
        t = self.script.get(audio_ref)
        if t is None:
            raise KeyError(f"FakeStt has no script for audio_ref={audio_ref!r}")
        return t

class TtsSink(abc.ABC):

    @abc.abstractmethod
    def speak_partial(self, token: str) -> None:
        ...

    def speak(self, text: str) -> None:

        self.speak_partial(text)

    def end_turn(self) -> None:

        pass

class VoicedTts(TtsSink):

    def __init__(self, endpoint: str = "unix:///run/voiced.sock") -> None:
        self.endpoint = endpoint

    def speak_partial(self, token: str) -> None:

        raise NotImplementedError("VoicedTts wires to piper via voiced (TODO(voiced-rpc)).")

class FakeTts(TtsSink):

    def __init__(self) -> None:
        self.tokens: list[str] = []
        self.turns: list[list[str]] = []

    def speak_partial(self, token: str) -> None:
        self.tokens.append(token)

    def end_turn(self) -> None:
        self.turns.append(list(self.tokens))
        self.tokens = []

    @property
    def spoken(self) -> str:
        return "".join(self.tokens)

EARCONS = ("working", "done", "confirm", "error")

class Earcon(abc.ABC):
    @abc.abstractmethod
    def emit(self, kind: str) -> None:
        ...

class FakeEarcon(Earcon):
    def __init__(self) -> None:
        self.seq: list[str] = []

    def emit(self, kind: str) -> None:
        if kind not in EARCONS:
            raise ValueError(f"unknown earcon {kind!r}; must be one of {EARCONS}")
        self.seq.append(kind)

@dataclass
class LensText:

    surface_id: str
    text: str

@dataclass
class LensTree:

    surface_id: str
    nodes: list = field(default_factory=list)

class Sense(abc.ABC):

    @abc.abstractmethod
    def read_terminal(self, surface: Optional[str] = None, tail: int = 0) -> LensText:
        ...

    @abc.abstractmethod
    def sense_text(self, target: Optional[str] = None) -> LensText:
        ...

    @abc.abstractmethod
    def sense_tree(self, target: Optional[str] = None) -> LensTree:
        ...

class FakeSense(Sense):

    def __init__(self) -> None:
        self.terminals: dict[str, str] = {}
        self.texts: dict[str, str] = {}
        self.trees: dict[str, list] = {}
        self.reads: list[tuple] = []

    def set_terminal(self, surface_id: str, text: str) -> None:
        self.terminals[surface_id] = text

    def set_text(self, target: str, text: str) -> None:
        self.texts[target] = text

    def set_tree(self, target: str, nodes: list) -> None:
        self.trees[target] = nodes

    def _default_surface(self, mapping: dict) -> str:
        if not mapping:
            raise KeyError("FakeSense has no surfaces configured")
        return next(iter(mapping))

    def read_terminal(self, surface: Optional[str] = None, tail: int = 0) -> LensText:
        sid = surface or self._default_surface(self.terminals)
        self.reads.append(("terminal", sid, tail))
        text = self.terminals.get(sid, "")
        if tail and tail > 0:
            text = "\n".join(text.splitlines()[-tail:])
        return LensText(surface_id=sid, text=text)

    def sense_text(self, target: Optional[str] = None) -> LensText:
        sid = target or self._default_surface(self.texts)
        self.reads.append(("text", sid, 0))
        return LensText(surface_id=sid, text=self.texts.get(sid, ""))

    def sense_tree(self, target: Optional[str] = None) -> LensTree:
        sid = target or self._default_surface(self.trees)
        self.reads.append(("tree", sid, 0))
        return LensTree(surface_id=sid, nodes=list(self.trees.get(sid, [])))

class LlmStream(abc.ABC):

    @abc.abstractmethod
    def stream(self, prompt: str, on_token: Callable[[str], None]) -> str:

        ...

class FakeLlmStream(LlmStream):
    def __init__(self, response_for: Optional[Callable[[str], str]] = None) -> None:
        self.response_for = response_for or (lambda p: "ok")

    def stream(self, prompt: str, on_token: Callable[[str], None]) -> str:
        text = self.response_for(prompt)
        for tok in text.split(" "):
            on_token(tok + " ")
        return text
