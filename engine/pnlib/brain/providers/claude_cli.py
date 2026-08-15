
from __future__ import annotations

import json
import os
from typing import Iterator, Optional

from pnlib.llmpool import Pool
from pnlib import redact as pnredact

from ..waist import BrainRequest, BrainReply, StreamEvent
from .base import Provider, Capabilities

DEFAULT_MODEL = os.environ.get("PN_LLM_MODEL", "sonnet")
DEFAULT_CMD = os.environ.get("PN_LLM_CMD", "claude -p --model {model}")
DEFAULT_TIMEOUT = int(os.environ.get("PN_LLM_TIMEOUT", "300"))
DEFAULT_POOL_SIZE = int(os.environ.get("PN_LLM_POOL", "2"))

AUTH_REASON_MARKERS = (
    ("org_disabled", ("disabled claude subscription access",
                      "oauth authentication is currently not allowed for this organization",
                      "oauth_not_allowed_for_organization",
                      "ask your admin to enable access")),
    ("logged_out",   ("not logged in", "please run /login", "please run `claude /login`")),
    ("expired",      ("oauth token has expired", "refresh token has expired")),
    ("invalid",      ("invalid api key",)),
    ("balance",      ("credit balance is too low",)),
)

AUTH_REASON_DE = {
    "org_disabled": ("Claude-Zugang abgelehnt: dieses Konto hat Claude Code deaktiviert "
                     "— bitte anderes Konto verbinden."),
    "logged_out":   "Claude-Zugang nicht verbunden: bitte Konto neu anmelden.",
    "expired":      "Claude-Zugang abgelaufen: bitte Konto neu anmelden.",
    "invalid":      ("Claude-Zugang ungültig: die hinterlegte Anmeldung wird abgelehnt "
                     "— bitte Konto neu verbinden."),
    "balance":      "Claude-Kontingent aufgebraucht: bitte Abo oder Guthaben prüfen.",
}

AUTH_OWNER_ONLY = ("org_disabled",)

AUTH_MARKERS = tuple(m for _r, ms in AUTH_REASON_MARKERS for m in ms)

def auth_reason_for(text):

    low = (text or "").lower()
    for reason, markers in AUTH_REASON_MARKERS:
        if any(m in low for m in markers):
            return reason
    return None

def classify_result(res: dict) -> dict:

    raw = (res.get("raw") or res.get("text") or res.get("error") or "")
    reason = auth_reason_for(raw)
    if reason:
        res = {"ok": False,
               "error": "LLM auth down — " + AUTH_REASON_DE[reason],
               "auth": True,
               "auth_reason": reason,
               "status_de": AUTH_REASON_DE[reason],
               "owner_action_required": reason in AUTH_OWNER_ONLY,
               "session": res.get("session"), "routing": res.get("routing")}
    if res.get("text"):
        res["text"] = pnredact.redact(res["text"])
    if res.get("error"):
        res["error"] = pnredact.redact(res["error"])
    res.pop("raw", None)
    return res

class ClaudeCliProvider(Provider):

    NAME = "claude_cli"

    def __init__(self, pool: Optional[Pool] = None, *, model: Optional[str] = None,
                 cmd_tmpl: Optional[str] = None, env: Optional[dict] = None,
                 pool_size: Optional[int] = None, timeout: Optional[int] = None):
        self.model = model or DEFAULT_MODEL
        self.cmd_tmpl = cmd_tmpl or DEFAULT_CMD
        self.timeout = int(timeout or DEFAULT_TIMEOUT)
        self._pool = pool or Pool(int(pool_size or DEFAULT_POOL_SIZE), self.model,
                                  self.cmd_tmpl, env=env or {})

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            name=self.NAME,
            models=(),
            default_model=self.model,
            streaming=False,
            tools=False,
            vision=False,
            system_prompt=False,
            max_context=None,
            routing_kinds=("loose", "dedicated"),
            byo_kinds=("max-token", "api-key", "codex"),
            notes="claude -p CLI + pn-llmd warm llmpool (the current live LLM path)",
        )

    def generate(self, req: BrainRequest) -> BrainReply:
        if not req.prompt:

            return BrainReply.from_llmpool({"ok": False, "error": "empty prompt"},
                                           provider=self.NAME, model=req.model or self.model)
        model = req.model or self.model
        timeout = int(req.timeout or self.timeout)
        kind = req.kind if req.kind in ("loose", "dedicated") else "loose"
        res = self._pool.ask(req.prompt, timeout, kind=kind)
        classified = classify_result(res)
        return BrainReply.from_llmpool(classified, provider=self.NAME, model=model)

    def health(self) -> dict:
        info = self._pool.info()
        return {"ok": True, "provider": self.NAME, "backend": self._pool.cmd_tmpl,
                "pool": info}

    @property
    def pool(self) -> Pool:
        return self._pool

_RL_WINDOW_HINTS = {"five_hour": "5h", "5h": "5h", "seven_day": "7d", "7d": "7d",
                    "week": "7d", "weekly": "7d", "hour": "5h"}

def _norm_rate_limit(rl: dict) -> dict:

    if not isinstance(rl, dict):
        return {"raw": rl}
    window = rl.get("window") or rl.get("period") or rl.get("bucket") or rl.get("resource")
    out = {
        "resource": rl.get("resource") or rl.get("name") or window,
        "remaining": rl.get("remaining", rl.get("tokens_remaining", rl.get("requests_remaining"))),
        "limit": rl.get("limit", rl.get("tokens_limit", rl.get("requests_limit"))),
        "reset_s": rl.get("reset_s", rl.get("resets_in_seconds",
                          rl.get("retry_after", rl.get("seconds_until_reset")))),
        "window": _RL_WINDOW_HINTS.get(str(window).lower(), window) if window else None,
        "raw": rl,
    }
    return out

def _extract_rate_limit(obj: dict):

    if obj.get("type") in ("rate_limit", "rate_limit_event"):
        return _norm_rate_limit(obj.get("rate_limit") or obj.get("event") or obj)
    for key in ("rate_limit", "rate_limit_event"):
        if key in obj:
            return _norm_rate_limit(obj[key])
    usage = obj.get("usage")
    if isinstance(usage, dict) and "rate_limit" in usage:
        return _norm_rate_limit(usage["rate_limit"])
    return None

def parse_stream_json_events(lines) -> list:

    if isinstance(lines, (str, bytes)):
        text = lines.decode() if isinstance(lines, bytes) else lines
        lines = text.splitlines()
    events: list = []
    for line in lines:
        s = (line.decode() if isinstance(line, bytes) else line).strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except Exception:
            events.append(StreamEvent("raw", data={"line": s}))
            continue
        if not isinstance(obj, dict):
            events.append(StreamEvent("raw", data={"line": s}))
            continue

        rl = _extract_rate_limit(obj)
        if rl is not None:
            events.append(StreamEvent("rate_limit", data=rl))

        typ = obj.get("type")
        if typ == "system":
            events.append(StreamEvent("message_start",
                                      data={k: obj.get(k) for k in ("subtype", "model", "session_id")
                                            if obj.get(k) is not None}))
        elif typ in ("assistant", "message", "content_block_delta", "text"):
            txt = _message_text(obj)
            if txt:
                events.append(StreamEvent("text_delta", text=txt))
            usage = _message_usage(obj)
            if usage:
                events.append(StreamEvent("usage", data=usage))
        elif typ == "result":
            txt = obj.get("result") or obj.get("text")
            if txt:
                events.append(StreamEvent("message", text=txt))
            usage = _message_usage(obj)
            if usage:
                events.append(StreamEvent("usage", data=usage))
            events.append(StreamEvent("done", data={"stop_reason": obj.get("subtype")}))
        elif rl is None:
            events.append(StreamEvent("raw", data={"line": s}))
    return events

def _message_text(obj: dict) -> str:

    if isinstance(obj.get("text"), str):
        return obj["text"]
    if isinstance(obj.get("delta"), dict) and isinstance(obj["delta"].get("text"), str):
        return obj["delta"]["text"]
    msg = obj.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(c.get("text", "") for c in content
                           if isinstance(c, dict) and c.get("type") in (None, "text"))
    return ""

def _message_usage(obj: dict) -> Optional[dict]:

    usage = obj.get("usage")
    msg = obj.get("message")
    if usage is None and isinstance(msg, dict):
        usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None
    return {"input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
            "raw": usage}

def make_provider(**kw) -> ClaudeCliProvider:

    return ClaudeCliProvider(**kw)
