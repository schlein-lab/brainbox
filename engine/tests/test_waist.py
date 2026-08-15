#!/usr/bin/env python3

import os
import sys
import json
import tempfile
import importlib.util
import importlib.machinery

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP_SECRETS = tempfile.mkdtemp(prefix="pn_waist_secrets_")
os.environ.setdefault("PN_SECRETS_DIR", _TMP_SECRETS)
os.environ.setdefault("PN_LLM_CMD", "/bin/echo")

from pnlib.llmpool import Pool
from pnlib.brain import (BrainRequest, BrainReply, StreamEvent,
                         ProviderRegistry)
from pnlib.brain.providers import base as pbase
from pnlib.brain.providers.claude_cli import (
    ClaudeCliProvider, classify_result, AUTH_MARKERS, parse_stream_json_events)

def _load_pnllmd():
    path = os.path.join(ROOT, "tools", "pn-llmd")
    spec = importlib.util.spec_from_loader(
        "pn_llmd_oracle", importlib.machinery.SourceFileLoader("pn_llmd_oracle", path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

ORACLE = _load_pnllmd()

_P = _F = 0

def check(cond, msg):
    global _P, _F
    if cond:
        _P += 1; print(f"  PASS  {msg}")
    else:
        _F += 1; print(f"  FAIL  {msg}")

def _echo_pool(word="MOCKANSWER", size=2):

    return Pool(size, "sonnet", f"/bin/echo {word}", env={})

def _mock_provider(word="MOCKANSWER"):
    return ClaudeCliProvider(pool=_echo_pool(word))

def _battery():
    return [

        {"ok": True, "text": "hello world", "raw": "hello world\n", "session": 7, "routing": "loose"},

        {"ok": True, "text": "token sk-ant-AAAAAAAAAAAAAAAAAAAA end",
         "raw": "token sk-ant-AAAAAAAAAAAAAAAAAAAA end", "session": 4, "routing": "loose"},

        *[{"ok": False, "error": "backend rc=1: boom", "raw": f"... {m} ...",
           "session": 3, "routing": "dedicated"} for m in AUTH_MARKERS],

        {"ok": True, "text": "Invalid API key detected", "raw": "Invalid API key detected",
         "session": 1, "routing": "loose"},

        {"ok": False, "error": "backend rc=2: kaboom", "raw": "kaboom", "session": 9, "routing": "loose"},

        {"ok": False, "error": "leaked ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 here",
         "raw": "leaked ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 here", "session": 5, "routing": "loose"},

        {"ok": False, "error": "timeout after 300s", "session": 2, "routing": "loose"},
    ]

def test_auth_markers_verbatim_parity():
    check(tuple(AUTH_MARKERS) == tuple(ORACLE.AUTH_MARKERS),
          f"AUTH_MARKERS verbatim == live pn-llmd ({len(AUTH_MARKERS)} markers)")

def test_classify_byte_identical_to_pnllmd():
    for i, raw in enumerate(_battery()):
        mine = classify_result(dict(raw))
        oracle = ORACLE._classify(dict(raw))
        check(json.dumps(mine) == json.dumps(oracle),
              f"classify_result byte-identical to pn-llmd._classify [case {i}] -> {json.dumps(mine)[:70]}")

def test_generate_success_shape():
    p = _mock_provider("MOCKANSWER")
    reply = p.generate(BrainRequest(prompt="ping-42"))
    check(isinstance(reply, BrainReply), "generate returns a BrainReply")
    check(reply.ok is True, "mock generate ok=True")
    check("MOCKANSWER" in (reply.text or "") and "ping-42" in (reply.text or ""),
          f"reply.text carries backend output ({reply.text!r})")
    check(reply.provider == "claude_cli", "reply.provider == claude_cli")
    check(reply.routing == "loose" and isinstance(reply.session, int),
          f"reply carries llmpool session/routing (session={reply.session}, routing={reply.routing})")
    d = reply.to_llmpool_dict()
    check(list(d.keys()) == ["ok", "text", "session", "routing"],
          f"to_llmpool_dict success key-order == v_ask ({list(d.keys())})")
    nd = reply.to_dict()
    check(nd["ok"] and nd["provider"] == "claude_cli" and "text" in nd,
          "to_dict normalized view has ok/provider/text")

def test_generate_dedicated_routing():
    p = _mock_provider()
    reply = p.generate(BrainRequest(prompt="x", kind="dedicated"))
    check(reply.routing == "dedicated", "dedicated routing hint threads through Pool.ask")
    reply2 = p.generate(BrainRequest(prompt="x", kind="bogus"))
    check(reply2.routing == "loose", "unknown kind coerced to loose (v_ask parity)")

def test_generate_empty_prompt_matches_vask():
    p = _mock_provider()
    d = p.generate(BrainRequest(prompt="")).to_llmpool_dict()
    check(d == {"ok": False, "error": "empty prompt"},
          f"empty prompt == v_ask short-circuit ({d})")

def test_generate_auth_detection_e2e():

    p = ClaudeCliProvider(pool=_echo_pool("Please run /login"))
    reply = p.generate(BrainRequest(prompt="hi"))
    check(reply.ok is False and reply.auth is True, "auth marker in backend output -> ok=False auth=True")
    check("LLM auth down" in (reply.error or ""), f"canonical auth error surfaced ({reply.error!r})")

def test_generate_redaction_e2e():
    p = ClaudeCliProvider(pool=_echo_pool("sk-ant-AAAAAAAAAAAAAAAAAAAA"))
    reply = p.generate(BrainRequest(prompt="hi"))
    check("sk-ant-AAAAAAAAAAAAAAAAAAAA" not in (reply.text or ""),
          f"secret-shaped output redacted before it leaves the provider ({reply.text!r})")
    check("redacted" in (reply.text or ""), "redaction placeholder present")

def test_capabilities_descriptor():
    caps = _mock_provider().capabilities
    check(caps.name == "claude_cli", "capability name == claude_cli")
    check(caps.routing_kinds == ("loose", "dedicated"), "advertises loose+dedicated routing")
    check(set(caps.byo_kinds) == {"max-token", "api-key", "codex"}, "advertises the 3 BYO brain kinds")
    check(caps.supports_model("opus") and caps.supports_model(None),
          "empty models tuple => accepts any model + default")
    check(isinstance(caps.to_dict(), dict) and caps.to_dict()["name"] == "claude_cli",
          "capabilities.to_dict() serializes")

def _strip_session(d):
    d = dict(d); d.pop("session", None); return d

def test_selector_single_provider_identity():
    prov = _mock_provider()
    reg = ProviderRegistry(); reg.register(prov)
    check(reg.select(None) is prov, "select(None) with one provider -> that provider")
    check(reg.select({}) is prov, "select({}) empty config -> that provider")
    check(reg.select({"provider": "nope"}) is prov, "unknown selection fails closed to the one provider")

    a = reg.generate(BrainRequest(prompt="same-input"), config=None).to_llmpool_dict()
    b = prov.generate(BrainRequest(prompt="same-input")).to_llmpool_dict()
    check(_strip_session(a) == _strip_session(b),
          f"registry.generate(None) == provider.generate (identity) [{_strip_session(a)}]")
    check(isinstance(a.get("session"), int) and isinstance(b.get("session"), int),
          "both carry an int session handle")

class _DummyProvider(pbase.Provider):
    @property
    def capabilities(self):
        return pbase.Capabilities(name="dummy", notes="test-only")

    def generate(self, req):
        return BrainReply(ok=True, text="DUMMY", provider="dummy")

def test_selector_multi_provider_defaults_to_first():
    prov = _mock_provider()
    dummy = _DummyProvider()
    reg = ProviderRegistry(); reg.register(prov); reg.register(dummy)
    check(reg.select(None) is prov, "no config with multiple providers -> FIRST (claude_cli), identity preserved")
    check(reg.select({"provider": "dummy"}) is dummy, "explicit config selects the named provider")
    check(reg.select({"order": ["dummy", "claude_cli"]}) is dummy, "order preference selects dummy")
    check(reg.names() == ["claude_cli", "dummy"], "registration order preserved")

def test_brainrequest_helpers():
    r = BrainRequest(prompt="p", kind="weird")
    check(r.kind == "loose", "BrainRequest coerces unknown kind -> loose")
    r2 = BrainRequest(prompt="p")
    r3 = r2.with_defaults("opus", 120)
    check(r3.model == "opus" and r3.timeout == 120 and r2.model is None,
          "with_defaults fills defaults non-mutatingly")

def test_default_stream_wraps_generate():
    p = _mock_provider("MOCKANSWER")
    evs = list(p.stream(BrainRequest(prompt="hi")))
    kinds = [e.kind for e in evs]
    check("message" in kinds and "done" in kinds, f"non-streaming provider degrades to message+done ({kinds})")
    msg = next(e for e in evs if e.kind == "message")
    check("MOCKANSWER" in (msg.text or ""), "streamed terminal message carries the text")

def test_stream_json_parser_normalizes():
    ndjson = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "model": "sonnet", "session_id": "s1"}),
        json.dumps({"type": "assistant", "message": {
            "content": [{"type": "text", "text": "Hello"}],
            "usage": {"input_tokens": 10, "output_tokens": 3}}}),
        json.dumps({"type": "rate_limit_event", "rate_limit": {
            "resource": "five_hour", "remaining": 42, "limit": 1000, "resets_in_seconds": 900}}),
        json.dumps({"type": "result", "subtype": "success", "result": "Hello there",
                    "usage": {"input_tokens": 10, "output_tokens": 5}}),
        "GARBAGE NOT JSON",
    ])
    evs = parse_stream_json_events(ndjson)
    kinds = [e.kind for e in evs]
    check(kinds[0] == "message_start", f"first event message_start ({kinds})")
    check("text_delta" in kinds, "assistant text -> text_delta")
    check("usage" in kinds, "usage line normalized")
    check("message" in kinds and "done" in kinds, "result -> message + done")
    check("raw" in kinds, "non-JSON line preserved as raw (nothing dropped)")
    rl = next(e for e in evs if e.kind == "rate_limit")
    check(rl.data["window"] == "5h" and rl.data["remaining"] == 42 and rl.data["reset_s"] == 900,
          f"rate_limit normalized (window/remaining/reset) -> {rl.data}")

def test_stream_json_rate_limit_variants():

    line = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}]},
                       "usage": {"rate_limit": {"window": "seven_day", "tokens_remaining": 5,
                                                "retry_after": 3600}}})
    evs = parse_stream_json_events([line])
    rl = next(e for e in evs if e.kind == "rate_limit")
    check(rl.data["window"] == "7d" and rl.data["remaining"] == 5 and rl.data["reset_s"] == 3600,
          f"nested usage.rate_limit (7d) normalized -> {rl.data}")

def test_live_pong_roundtrip():
    if os.environ.get("PN_WAIST_LIVE") != "1":
        print("  SKIP  live PONG round-trip (set PN_WAIST_LIVE=1 with a Max session to run)")
        return
    p = ClaudeCliProvider()
    reply = p.generate(BrainRequest(prompt="Reply with exactly one word: PONG", timeout=60))
    check(reply.ok and "PONG" in (reply.text or "").upper(),
          f"live Max PONG round-trip through the waist ({reply.text!r})")

def _run_all():
    fns = [g for n, g in sorted(globals().items()) if n.startswith("test_") and callable(g)]
    for fn in fns:
        print(f"\n# {fn.__name__}")
        fn()
    print(f"\n=== {_P} passed, {_F} failed ===")
    return 0 if _F == 0 else 1

if __name__ == "__main__":
    sys.exit(_run_all())
