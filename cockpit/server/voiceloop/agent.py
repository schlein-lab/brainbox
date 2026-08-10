

from __future__ import annotations

import enum
import itertools
import re
from dataclasses import dataclass, field
from typing import List, Optional

from gate import WakeGate, State
from interfaces import (
    SttEngine, TtsSink, Earcon, Sense, LlmStream,
)
from lane import RealtimeLaneJob, voice_lane_job

class Ceremony(enum.Enum):
    READ = "read"
    LOW_STAKES = "low-stakes"
    IRREVERSIBLE = "irreversible"

@dataclass
class Intent:

    verb: str
    ceremony: Ceremony
    args: dict = field(default_factory=dict)
    confidence: float = 1.0

_IRREVERSIBLE = [
    (r"\b(sende|senden|schick|verschick|mail\s+send|send\b)", "verb.mail_send"),
    (r"\b(lösch|loesch|delete|entfern(e|en)\b)", "verb.delete"),
    (r"\b(zahl|überweis|ueberweis|pay\b|bezahl)", "verb.pay"),
    (r"\b(committe|commit\b|einchecken)", "verb.commit"),
    (r"\b(kill|beende\s+(den\s+)?(prozess|service|dienst)|abschieß)", "verb.kill"),
    (r"\b(trage.*login|login.*eintr|credential|passwort\s+eintr)", "verb.credential_enter"),
]

_LOW_STAKES = [
    (r"\b(neuer\s+tab|new\s+tab)\b", "app.open"),
    (r"\b(öffne|oeffne|open\b|geh\s+zu|navigier)", "app.open"),
    (r"\b(scrolle|scroll\b|runter|hoch\s+scroll)", "app.key"),
    (r"\b(tippe|type\b|schreib\s+(rein|in))", "app.type"),
    (r"\b(klick|click\b|drück\s+(auf|den)|press\s+the)", "app.click"),
    (r"\b(zeig.*(screen|bildschirm)|screen\s+lens|zeig\s+mir\s+das\s+im\s+screen)", "screen.show"),
]

_READ = [

    (r"\b(ausgabe|output|terminal|was\s+steht.*terminal|zeig.*terminal|lies.*terminal)",
     "terminal.read"),
    (r"\b(zeig\b|lies\b|was\s+steht|wie\s+sieht|read\b|show\b|sense\b|status\b)",
     "app.sense"),
]

def _match(patterns, text):
    for pat, verb in patterns:
        if re.search(pat, text):
            return verb
    return None

class Dispatcher:

    def classify(self, utterance: str) -> Intent:
        text = (utterance or "").strip().lower()
        if not text:
            return Intent(verb="conversation.say", ceremony=Ceremony.READ,
                          args={"utterance": utterance}, confidence=0.0)

        v = _match(_IRREVERSIBLE, text)
        if v:
            return Intent(verb=v, ceremony=Ceremony.IRREVERSIBLE,
                          args=self._parse_args(v, text, utterance))

        v = _match(_LOW_STAKES, text)
        if v:
            return Intent(verb=v, ceremony=Ceremony.LOW_STAKES,
                          args=self._parse_args(v, text, utterance))

        v = _match(_READ, text)
        if v:
            return Intent(verb=v, ceremony=Ceremony.READ,
                          args=self._parse_args(v, text, utterance))

        return Intent(verb="conversation.say", ceremony=Ceremony.READ,
                      args={"utterance": utterance}, confidence=0.5)

    def _parse_args(self, verb: str, text: str, raw: str) -> dict:
        args: dict = {}
        if verb == "terminal.read":
            m = re.search(r"letzten?\s+(\d+)|last\s+(\d+)|tail\s+(\d+)", text)
            if m:
                args["tail"] = int(next(g for g in m.groups() if g))

        return args

@dataclass
class TurnResult:
    turn_id: str
    verb: str
    ceremony: Ceremony
    ok: bool
    spoken: str = ""
    result: Optional[dict] = None
    error: Optional[str] = None
    earcons: List[str] = field(default_factory=list)

class VoiceAgent:

    def __init__(
        self,
        *,
        gate: WakeGate,
        stt: SttEngine,
        tts: TtsSink,
        earcon: Earcon,
        sense: Sense,
        llm: Optional[LlmStream] = None,
        lane: Optional[RealtimeLaneJob] = None,
        principal: str = "owner",
    ) -> None:
        self.gate = gate
        self.stt = stt
        self.tts = tts
        self.earcon = earcon
        self.sense = sense
        self.llm = llm
        self.lane = lane or voice_lane_job(principal=principal)
        self.principal = principal
        self.dispatcher = Dispatcher()
        self._ids = itertools.count(1)
        self._earcon_seq: List[str] = []

        if self.gate.earcon_hook is None:
            self.gate.earcon_hook = self._emit_earcon

    def _emit_earcon(self, kind: str) -> None:
        self._earcon_seq.append(kind)
        self.earcon.emit(kind)

    def _new_turn_id(self) -> str:
        return f"turn-{next(self._ids)}"

    def bind_referent(self, ref, candidates: list):

        if len(candidates) == 1:
            return candidates[0]
        return None

    def handle_turn(self, audio_ref: str) -> TurnResult:

        self._earcon_seq = []

        self.gate.submit_audio(audio_ref)
        transcript = self.stt.transcribe(audio_ref)
        self.gate.begin_dispatch()

        intent = self.dispatcher.classify(transcript.text)
        turn_id = self._new_turn_id()
        try:
            result = self._execute(turn_id, intent)
        finally:
            self.tts.end_turn()
            self.gate.finish_turn()
        result.earcons = list(self._earcon_seq)
        return result

    def _execute(self, turn_id: str, intent: Intent) -> TurnResult:
        if intent.ceremony is Ceremony.READ:
            return self._execute_read(turn_id, intent)
        if intent.ceremony is Ceremony.LOW_STAKES:

            self.tts.speak_partial("das kann ich gleich, ")
            self.tts.speak_partial("aber die aktion ist noch nicht verdrahtet.")
            return TurnResult(turn_id=turn_id, verb=intent.verb,
                              ceremony=intent.ceremony, ok=False,
                              spoken=self._flush_spoken(),
                              error="ERR_NOT_IMPLEMENTED")

        self.earcon.emit("confirm")
        self._earcon_seq.append("confirm")
        self.tts.speak_partial("das ist eine unwiderrufliche aktion, ")
        self.tts.speak_partial("die braucht die bestätigungs-zeremonie.")
        return TurnResult(turn_id=turn_id, verb=intent.verb,
                          ceremony=intent.ceremony, ok=False,
                          spoken=self._flush_spoken(),
                          error="ERR_CEREMONY_REQUIRED")

    def _execute_read(self, turn_id: str, intent: Intent) -> TurnResult:

        self.tts.speak_partial("mach ich: ")

        verb = intent.verb
        if verb == "terminal.read":
            tail = int(intent.args.get("tail", 0) or 0)
            lens = self.sense.read_terminal(tail=tail)
            self._speak_lines(lens.text)
            self.earcon.emit("done")
            self._earcon_seq.append("done")
            return TurnResult(turn_id=turn_id, verb=verb, ceremony=Ceremony.READ,
                              ok=True, spoken=self._flush_spoken(),
                              result={"text": lens.text, "surface_id": lens.surface_id})

        if verb == "app.sense":
            lens = self.sense.sense_text()
            self._speak_lines(lens.text)
            self.earcon.emit("done")
            self._earcon_seq.append("done")
            return TurnResult(turn_id=turn_id, verb=verb, ceremony=Ceremony.READ,
                              ok=True, spoken=self._flush_spoken(),
                              result={"text": lens.text, "surface_id": lens.surface_id})

        if verb == "conversation.say":
            utter = intent.args.get("utterance", "")
            if self.llm is not None:
                self.llm.stream(utter, self.tts.speak_partial)
                self.earcon.emit("done")
                self._earcon_seq.append("done")
                return TurnResult(turn_id=turn_id, verb=verb, ceremony=Ceremony.READ,
                                  ok=True, spoken=self._flush_spoken())
            self.tts.speak_partial("(kein llm verdrahtet)")
            return TurnResult(turn_id=turn_id, verb=verb, ceremony=Ceremony.READ,
                              ok=False, spoken=self._flush_spoken(),
                              error="ERR_NOT_IMPLEMENTED")

        self.tts.speak_partial("(read-verb noch nicht verdrahtet)")
        return TurnResult(turn_id=turn_id, verb=verb, ceremony=Ceremony.READ,
                          ok=False, spoken=self._flush_spoken(),
                          error="ERR_NOT_IMPLEMENTED")

    def _speak_lines(self, text: str) -> None:
        if not text.strip():
            self.tts.speak_partial("nichts zu sehen.")
            return
        for line in text.splitlines():
            self.tts.speak_partial(line + " ")

    def _flush_spoken(self) -> str:

        return getattr(self.tts, "spoken", "")
