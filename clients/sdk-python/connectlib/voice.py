
from __future__ import annotations

class SttBackend:

    name = "none"

    def transcribe(self, audio: bytes) -> str:
        raise NotImplementedError

class TypedStt(SttBackend):

    name = "typed"

    def transcribe(self, audio) -> str:

        return audio if isinstance(audio, str) else audio.decode("utf-8", "replace")

class HttpStt(SttBackend):

    name = "box-http"

    def __init__(self, base_url: str, *, token: str | None = None, ca_file: str | None = None,
                 timeout_s: float = 60.0, content_type: str = "audio/webm"):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.ca_file = ca_file
        self.timeout_s = timeout_s
        self.content_type = content_type

    def transcribe(self, audio: bytes) -> str:
        import json as _json
        import ssl as _ssl
        import urllib.request as _rq
        if isinstance(audio, str):

            return audio
        ctx = _ssl.create_default_context(cafile=self.ca_file) if self.ca_file \
            else _ssl.create_default_context()
        req = _rq.Request(self.base_url + "/api/stt", data=audio, method="POST")
        req.add_header("Content-Type", self.content_type)
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        with _rq.urlopen(req, timeout=self.timeout_s, context=ctx) as r:
            res = _json.loads(r.read().decode("utf-8", "replace"))
        if res.get("error"):

            raise RuntimeError(res["error"])
        return (res.get("text") or "").strip()

class VoiceIntake:

    def __init__(self, stt: SttBackend | None = None):
        self.stt = stt or TypedStt()

    def to_text(self, audio_or_text) -> str:
        return self.stt.transcribe(audio_or_text)

def utterance_to_verb(utterance: str) -> dict | None:

    if not utterance:
        return None
    u = utterance.strip().lower()

    if any(p in u for p in ("don't", "do not", "reject", "deny", "no, ", "cancel", "stop")):
        return {"verb": "deny"}
    if u.startswith("revise") or u.startswith("change") or "revise" in u:

        for lead in ("revise", "change"):
            if u.startswith(lead):
                return {"verb": "steer", "feedback": utterance.strip()[len(lead):].strip(" :,-")}
        return {"verb": "steer", "feedback": utterance.strip()}
    if any(p in u for p in ("approve", "yes", "go ahead", "confirm", "do it", "accept", "okay", "ok")):
        return {"verb": "approve"}
    return None
