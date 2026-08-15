#!/usr/bin/env python3

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("PN_LLM_CMD", "/bin/echo")

from pnlib.brain.waist import BrainRequest, BrainReply, ProviderRegistry
from pnlib.brain.providers.base import Provider, Capabilities
from pnlib.brain.router import (
    BrainRouter, ProviderProfile, RouteConstraints, norm_residency)
from pnlib.brain import shadow as shadowmod

class StubProvider(Provider):
    def __init__(self, name, **caps):
        self._caps = Capabilities(name=name, **caps)
        self.calls = 0

    @property
    def capabilities(self):
        return self._caps

    def generate(self, req):
        self.calls += 1
        return BrainReply(ok=True, text=f"[{self._caps.name}] {req.prompt}",
                          provider=self._caps.name)

def _profile(name, **kw):
    residency = kw.pop("residency", "cloud")
    caps_kw = {k: kw.pop(k) for k in ("streaming", "tools", "vision", "system_prompt",
                                      "routing_kinds", "models") if k in kw}
    return ProviderProfile(provider=StubProvider(name, **caps_kw), residency=residency, **kw)

REQ = BrainRequest(prompt="hello")

def test_ranking_table_headroom_then_cost():

    A = _profile("A", headroom=0.9, cost=5.0, latency_ms=100.0)
    B = _profile("B", headroom=0.9, cost=2.0, latency_ms=50.0)
    C = _profile("C", headroom=0.5, cost=1.0, latency_ms=10.0)
    r = BrainRouter([A, B, C])

    d = r.route(REQ, constraints=RouteConstraints(residency="cloud"), now=1000.0)
    assert d.ok and d.reason == "selected", d.to_dict()
    assert d.name == "B", d.to_dict()
    assert d.chain == ["B", "A", "C"], d.chain

def test_ranking_cost_tie_falls_to_latency():

    X = _profile("X", headroom=0.7, cost=3.0, latency_ms=80.0)
    Y = _profile("Y", headroom=0.7, cost=3.0, latency_ms=20.0)
    Z = _profile("Z", headroom=0.7, cost=3.0, latency_ms=20.0)
    r = BrainRouter([X, Y, Z])
    d = r.route(REQ, constraints=RouteConstraints(residency="cloud"),
                now=1000.0)
    assert d.name == "Y", d.to_dict()
    assert d.chain == ["Y", "Z", "X"], d.chain

def test_capability_and_threshold_feasibility():

    T = _profile("T", headroom=0.9, cost=9.0, tools=True)
    N = _profile("N", headroom=0.9, cost=1.0, tools=False)
    r = BrainRouter([T, N])
    d = r.route(REQ, constraints=RouteConstraints(require_tools=True), now=1000.0)
    assert d.name == "T", d.to_dict()
    assert ("N", "tools") in d.rejected, d.rejected

    d2 = r.route(REQ, constraints=RouteConstraints(require_tools=True, max_cost=5.0), now=1000.0)
    assert not d2.ok and d2.reason == "refused:infeasible", d2.to_dict()
    assert d2.provider is None

def test_residency_airgap_prefers_local_never_cloud():
    local = _profile("local-llama", residency="airgap", headroom=0.3, cost=0.0, latency_ms=200.0)
    cloud = _profile("cloud-claude", residency="cloud", headroom=0.99, cost=8.0, latency_ms=15.0)
    r = BrainRouter([cloud, local])
    d = r.route(REQ, constraints=RouteConstraints(residency="airgap"), now=1000.0)

    assert d.ok and d.name == "local-llama", d.to_dict()
    assert "cloud-claude" not in d.chain, d.chain
    assert ("cloud-claude", "residency") in d.rejected, d.rejected

    assert cloud.provider.calls == 0

def test_residency_airgap_with_only_cloud_refuses():
    cloud = _profile("cloud-claude", residency="cloud", headroom=0.99, cost=1.0)
    r = BrainRouter([cloud])

    d = r.route(REQ, constraints=RouteConstraints(residency="airgap"), now=1000.0)
    assert not d.ok, d.to_dict()
    assert d.reason == "refused:residency", d.to_dict()
    assert d.provider is None and d.name == "", d.to_dict()

    reply = r.generate(REQ, constraints=RouteConstraints(residency="airgap"), now=1000.0)
    assert isinstance(reply, BrainReply) and not reply.ok
    assert reply.provider == "" and "refused" in (reply.error or ""), reply.to_dict()
    assert cloud.provider.calls == 0, "cloud provider MUST NOT be called under an airgap refuse"

def test_residency_local_allows_airgap_and_local_excludes_cloud():
    air = _profile("air", residency="airgap", headroom=0.5)
    lan = _profile("lan", residency="local", headroom=0.9)
    cloud = _profile("cloud", residency="cloud", headroom=0.99)
    r = BrainRouter([cloud, lan, air])
    d = r.route(REQ, constraints=RouteConstraints(residency="local"), now=1000.0)
    assert d.ok and d.name == "lan", d.to_dict()
    assert "cloud" not in d.chain and set(d.chain) == {"air", "lan"}, d.chain

def test_norm_residency_aliases():
    for s in ("air-gapped", "AIR_GAPPED", "offline", "on-box", "sovereign"):
        assert norm_residency(s) == "airgap", s
    for s in ("on-prem", "LAN", "private"):
        assert norm_residency(s) == "local", s
    for s in ("remote", "api", "internet"):
        assert norm_residency(s) == "cloud", s
    assert norm_residency(None) == "any"
    assert norm_residency("something-unknown") == "cloud"

def _real_claude_cli():

    from pnlib.brain.providers.claude_cli import ClaudeCliProvider
    from pnlib.llmpool import Pool
    pool = Pool(1, "sonnet", "/bin/echo", env={})
    return ClaudeCliProvider(pool=pool)

def test_identity_single_provider_is_claude_cli():
    prov = _real_claude_cli()
    r = BrainRouter([ProviderProfile(provider=prov, residency="cloud")])

    d = r.route(REQ, constraints=None, config=None)
    assert d.ok and d.reason == "identity", d.to_dict()
    assert d.name == "claude_cli", d.to_dict()
    assert d.provider is prov, "must return the SAME provider object (byte-identical)"

def test_identity_matches_registry_select_byte_for_byte():

    prov = _real_claude_cli()
    other = StubProvider("B", routing_kinds=("loose",))
    profiles = [ProviderProfile(provider=prov, residency="cloud"),
                ProviderProfile(provider=other, residency="cloud")]
    r = BrainRouter(profiles)

    ref = ProviderRegistry()
    ref.register(prov)
    ref.register(other)

    for cfg in (None, {}, {"provider": "B"}, {"order": ["B"]}):
        d = r.route(REQ, constraints=None, config=cfg)
        assert d.reason == "identity", (cfg, d.to_dict())
        assert d.provider is ref.select(cfg), (cfg, d.name)

    d = r.route(REQ, constraints=RouteConstraints(), config=None)
    assert d.reason == "identity" and d.provider is prov

def test_cooldown_falls_back_then_refuses():
    top = _profile("top", headroom=0.9, cost=1.0)
    nxt = _profile("nxt", headroom=0.5, cost=1.0)
    r = BrainRouter([top, nxt])
    c = RouteConstraints(residency="cloud")

    top.cooldown_until = 2000.0
    d = r.route(REQ, constraints=c, now=1000.0)
    assert d.ok and d.name == "nxt", d.to_dict()
    assert d.chain == ["top", "nxt"], d.chain

    d2 = r.route(REQ, constraints=c, now=2500.0)
    assert d2.name == "top", d2.to_dict()

    nxt.cooldown_until = 3000.0
    d3 = r.route(REQ, constraints=c, now=1000.0)
    assert not d3.ok and d3.reason == "refused:cooldown", d3.to_dict()
    assert d3.retry_after == 2000.0, d3.retry_after

def test_energy_inert_when_no_reading():

    light = _profile("light", headroom=0.8, cost=1.0, latency_ms=10.0, energy_weight=1.0)
    heavy = _profile("heavy", headroom=0.8, cost=1.0, latency_ms=10.0, energy_weight=50.0)
    r = BrainRouter([heavy, light])
    d = r.route(REQ, constraints=RouteConstraints(residency="cloud", energy_reading=None),
                now=1000.0)
    assert d.chain == ["heavy", "light"], ("inert -> name tiebreak", d.chain)

def test_energy_tier_engages_on_battery():

    light = _profile("light", headroom=0.8, cost=1.0, latency_ms=10.0, energy_weight=1.0)
    heavy = _profile("heavy", headroom=0.8, cost=1.0, latency_ms=10.0, energy_weight=50.0)
    r = BrainRouter([heavy, light])
    reading = {"on_battery": True, "battery_pct": 20}
    d = r.route(REQ, constraints=RouteConstraints(residency="cloud", energy_reading=reading),
                now=1000.0)
    assert d.name == "light", d.to_dict()
    assert d.chain == ["light", "heavy"], d.chain

def _shadow_router():
    a = _profile("A", headroom=0.9, cost=2.0)
    b = _profile("B", headroom=0.5, cost=1.0)
    return BrainRouter([a, b])

class _FixedRng:
    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value

def test_shadow_off_by_default():
    h = shadowmod.ShadowHarness(_shadow_router())
    assert h.observe({"prompt": "x", "constraints": {"residency": "cloud"}}) is None
    assert h.stats()["seen"] == 1 and h.stats()["sampled"] == 0

def test_shadow_sampling_gate():
    router = _shadow_router()

    h1 = shadowmod.ShadowHarness(router, shadowmod.ShadowConfig(enabled=True, sample_rate=1.0))
    obs = h1.observe({"prompt": "x", "constraints": {"residency": "cloud"}})
    assert obs is not None and obs.sampled
    assert obs.decision.name == "A", obs.to_dict()

    h0 = shadowmod.ShadowHarness(router, shadowmod.ShadowConfig(enabled=True, sample_rate=0.0))
    assert h0.observe({"prompt": "x"}) is None

    hm = shadowmod.ShadowHarness(router, shadowmod.ShadowConfig(enabled=True, sample_rate=0.5),
                                 rng=_FixedRng(0.4))
    assert hm.observe({"prompt": "x", "constraints": {"residency": "cloud"}}) is not None
    hm2 = shadowmod.ShadowHarness(router, shadowmod.ShadowConfig(enabled=True, sample_rate=0.5),
                                  rng=_FixedRng(0.9))
    assert hm2.observe({"prompt": "x"}) is None

def test_shadow_refuses_live_socket_name():
    h = shadowmod.ShadowHarness(_shadow_router(),
                                shadowmod.ShadowConfig(enabled=True, sample_rate=1.0,
                                                       socket_path="/run/user/1000/pn-llmd.sock"))
    try:
        h.start()
        assert False, "must refuse to bind a live socket name"
    except ValueError as e:
        assert "pn-llmd.sock" in str(e)

def test_shadow_default_path_is_distinct_from_live():
    p = shadowmod.default_socket_path()
    assert os.path.basename(p) == "pn-llmd-shadow.sock"
    assert os.path.basename(p) not in shadowmod._LIVE_SOCKET_NAMES

def test_shadow_alt_socket_roundtrip():

    router = _shadow_router()
    tmpdir = tempfile.mkdtemp(prefix="pn_shadow_")
    sock_path = os.path.join(tmpdir, "pn-llmd-shadow.sock")
    h = shadowmod.ShadowHarness(router, shadowmod.ShadowConfig(
        enabled=True, sample_rate=1.0, socket_path=sock_path))
    try:
        bound = h.start()
        assert bound == sock_path and os.path.exists(sock_path)

        pong = shadowmod.shadow_query(sock_path, {"op": "ping"})
        assert pong.get("pong") is True and pong.get("shadow") is True

        resp = shadowmod.shadow_query(
            sock_path, {"prompt": "hello", "constraints": {"residency": "cloud"}})
        assert resp["ok"] and resp["sampled"] is True, resp
        assert resp["decision"]["name"] == "A", resp

        ref = router.route(BrainRequest(prompt="hello"),
                           constraints=RouteConstraints(residency="cloud"))
        assert resp["decision"]["name"] == ref.name

        resp2 = shadowmod.shadow_query(
            sock_path, {"prompt": "secret", "constraints": {"residency": "airgap"}})
        assert resp2["decision"]["ok"] is False
        assert resp2["decision"]["reason"] == "refused:residency", resp2
    finally:
        h.stop()
        assert not os.path.exists(sock_path), "stop() must remove the alt socket"
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass

def test_shadow_default_runtime_socket_bind():

    default = shadowmod.default_socket_path()
    runtime = os.path.dirname(default)
    if not os.path.isdir(runtime) or not os.access(runtime, os.W_OK):
        return
    live = os.path.join(runtime, "pn-llmd.sock")
    live_before = os.path.exists(live)
    h = shadowmod.ShadowHarness(_shadow_router(),
                                shadowmod.ShadowConfig(enabled=True, sample_rate=1.0))
    try:
        bound = h.start()
        assert bound == default and os.path.exists(default)
        assert os.path.realpath(bound) != os.path.realpath(live)
        pong = shadowmod.shadow_query(default, {"op": "ping"})
        assert pong.get("pong") is True
    finally:
        h.stop()

    assert os.path.exists(live) == live_before, "the live pn-llmd.sock must be untouched"

_TESTS = [
    test_ranking_table_headroom_then_cost,
    test_ranking_cost_tie_falls_to_latency,
    test_capability_and_threshold_feasibility,
    test_residency_airgap_prefers_local_never_cloud,
    test_residency_airgap_with_only_cloud_refuses,
    test_residency_local_allows_airgap_and_local_excludes_cloud,
    test_norm_residency_aliases,
    test_identity_single_provider_is_claude_cli,
    test_identity_matches_registry_select_byte_for_byte,
    test_cooldown_falls_back_then_refuses,
    test_energy_inert_when_no_reading,
    test_energy_tier_engages_on_battery,
    test_shadow_off_by_default,
    test_shadow_sampling_gate,
    test_shadow_refuses_live_socket_name,
    test_shadow_default_path_is_distinct_from_live,
    test_shadow_alt_socket_roundtrip,
    test_shadow_default_runtime_socket_bind,
]

def main():
    p = f = 0
    for t in _TESTS:
        try:
            t()
            p += 1
            print(f"  PASS  {t.__name__}")
        except Exception as e:
            f += 1
            import traceback
            print(f"  FAIL  {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n=== {p} passed, {f} failed ===")
    sys.exit(1 if f else 0)

if __name__ == "__main__":
    main()
