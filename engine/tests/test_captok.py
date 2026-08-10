#!/usr/bin/env python3

import os
import sys
import json
import random
import textwrap
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from relaylib import crypto
from pnlib import rootkey
from pnlib import captok
from pnlib.captok import mint, attenuate, seal, verify, try_verify, Caveat, CapTokError
from pnlib.captok.model import CapToken, EXP_DIM
from pnlib.captok.attenuate import _attenuate_unchecked

UNIVERSE = tuple("abcdefgh")

def _owner():
    priv, pub = rootkey.generate_owner_keypair_offbox()
    return priv, pub

def _mint(pub, priv, *, scope=("a", "b", "c"), audience="box-1", depth=3,
          exp=None, extra_caveats=()):
    cavs = [Caveat.scope("cap", scope)] + list(extra_caveats)
    return mint(owner_priv=priv, owner_pub=pub, agent="agent-root",
                audience=audience, max_redelegation_depth=depth, exp=exp, caveats=cavs)

def test_mint_verify_roundtrip():
    priv, pub = _owner()
    tok = _mint(pub, priv, scope=("a", "b", "c"), audience="box-1", depth=2)
    g = verify(tok, owner_pubkey=pub, audience="box-1", now=0)
    assert g.scopes["cap"] == frozenset({"a", "b", "c"})
    assert g.audience == "box-1"
    assert g.max_depth == 2 and g.depth == 0
    assert g.delegation.agents == ("agent-root",)
    assert g.root_pubkey_id == rootkey.owner_fingerprint(pubkey=pub)

    g2 = verify(CapToken.from_json(tok.to_json()), owner_pubkey=pub, audience="box-1", now=0)
    assert g2.scopes["cap"] == g.scopes["cap"]

def test_wrong_owner_key_rejected():
    priv, pub = _owner()
    _, other_pub = _owner()
    tok = _mint(pub, priv)
    ok, err = try_verify(tok, owner_pubkey=other_pub, audience="box-1", now=0)
    assert not ok and isinstance(err, CapTokError)

def test_attenuation_narrows_by_intersection_and_min():
    priv, pub = _owner()
    tok = _mint(pub, priv, scope=("a", "b", "c", "d"), depth=5,
                extra_caveats=[Caveat.num_leq("cpu_ms", 1000)])
    t1 = attenuate(tok, agent="a1", caveats=[Caveat.scope("cap", ("a", "b", "c")),
                                             Caveat.num_leq("cpu_ms", 500)])
    t2 = attenuate(t1, agent="a2", caveats=[Caveat.scope("cap", ("a", "b")),
                                            Caveat.num_leq("cpu_ms", 200),
                                            Caveat.member("paths", ("/tmp",))])
    g = verify(t2, owner_pubkey=pub, audience="box-1", now=0)
    assert g.scopes["cap"] == frozenset({"a", "b"})
    assert g.num_caps["cpu_ms"] == 200
    assert g.members["paths"] == frozenset({"/tmp"})
    assert g.delegation.agents == ("agent-root", "a1", "a2")
    assert g.depth == 2

def test_property_widening_always_rejected():

    rng = random.Random(1337)
    for _ in range(300):
        priv, pub = _owner()
        k = rng.randint(2, len(UNIVERSE))
        parent = tuple(sorted(rng.sample(UNIVERSE, k)))
        cap = rng.randint(100, 10_000)
        tok = _mint(pub, priv, scope=parent, depth=4,
                    extra_caveats=[Caveat.num_leq("cpu_ms", cap)])

        sub = tuple(sorted(rng.sample(parent, rng.randint(1, len(parent)))))
        lower = rng.randint(1, cap)
        good = attenuate(tok, agent="narrower",
                         caveats=[Caveat.scope("cap", sub), Caveat.num_leq("cpu_ms", lower)])
        g = verify(good, owner_pubkey=pub, audience="box-1", now=0)
        assert g.scopes["cap"] == frozenset(sub)
        assert g.num_caps["cpu_ms"] == lower

        outside = [c for c in UNIVERSE if c not in parent]
        if outside:
            wide_scope = tuple(sorted(set(sub) | {rng.choice(outside)}))
            forged = _attenuate_unchecked(tok, agent="attacker",
                                          caveats=[Caveat.scope("cap", wide_scope)])
            ok, err = try_verify(forged, owner_pubkey=pub, audience="box-1", now=0)
            assert not ok and isinstance(err, CapTokError), "widened scope must be rejected"

            try:
                attenuate(tok, agent="honest", caveats=[Caveat.scope("cap", wide_scope)])
                assert False, "attenuate() must refuse to widen scope"
            except CapTokError:
                pass

        forged_num = _attenuate_unchecked(tok, agent="attacker",
                                          caveats=[Caveat.num_leq("cpu_ms", cap + rng.randint(1, 50))])
        ok, err = try_verify(forged_num, owner_pubkey=pub, audience="box-1", now=0)
        assert not ok and isinstance(err, CapTokError), "raised numeric bound must be rejected"

def test_widening_via_multi_hop_rejected():

    priv, pub = _owner()
    tok = _mint(pub, priv, scope=("a", "b", "c", "d"), depth=5)
    t1 = attenuate(tok, agent="a1", caveats=[Caveat.scope("cap", ("a", "b"))])
    forged = _attenuate_unchecked(t1, agent="attacker", caveats=[Caveat.scope("cap", ("a", "b", "c"))])
    ok, err = try_verify(forged, owner_pubkey=pub, audience="box-1", now=0)
    assert not ok and isinstance(err, CapTokError)

def _flip_hex(h, idx=0):

    ch = h[idx]
    rep = "0" if ch != "0" else "1"
    return h[:idx] + rep + h[idx + 1:]

def test_flip_one_byte_in_any_block_fails():
    priv, pub = _owner()
    tok = _mint(pub, priv, scope=("a", "b", "c"), depth=3)
    tok = attenuate(tok, agent="a1", caveats=[Caveat.scope("cap", ("a", "b"))])
    tok = attenuate(tok, agent="a2", caveats=[Caveat.scope("cap", ("a",))])
    base = tok.to_dict()

    for bi in range(len(base["blocks"])):

        d = json.loads(json.dumps(base))
        d["blocks"][bi]["sig"] = _flip_hex(d["blocks"][bi]["sig"])
        ok, err = try_verify(CapToken.from_dict(d), owner_pubkey=pub, audience="box-1", now=0)
        assert not ok and isinstance(err, CapTokError), f"sig flip on block {bi} must fail"

        d = json.loads(json.dumps(base))
        d["blocks"][bi]["next_pub"] = _flip_hex(d["blocks"][bi]["next_pub"])
        ok, err = try_verify(CapToken.from_dict(d), owner_pubkey=pub, audience="box-1", now=0)
        assert not ok and isinstance(err, CapTokError), f"next_pub flip on block {bi} must fail"

        d = json.loads(json.dumps(base))
        d["blocks"][bi]["body"]["agent"] = d["blocks"][bi]["body"]["agent"] + "X"
        ok, err = try_verify(CapToken.from_dict(d), owner_pubkey=pub, audience="box-1", now=0)
        assert not ok and isinstance(err, CapTokError), f"body tamper on block {bi} must fail"

def test_proof_tamper_fails():
    priv, pub = _owner()
    tok = _mint(pub, priv)
    d = tok.to_dict()
    d["proof"] = _flip_hex(d["proof"])
    ok, err = try_verify(CapToken.from_dict(d), owner_pubkey=pub, audience="box-1", now=0)
    assert not ok and isinstance(err, CapTokError)

def test_truncation_rejected():
    priv, pub = _owner()
    tok = _mint(pub, priv, scope=("a", "b", "c"), depth=3)
    narrowed = attenuate(tok, agent="a1", caveats=[Caveat.scope("cap", ("a",))])

    truncated = CapToken(root_pubkey_id=narrowed.root_pubkey_id,
                         blocks=narrowed.blocks[:-1], proof=narrowed.proof, sealed=False)
    ok, err = try_verify(truncated, owner_pubkey=pub, audience="box-1", now=0)
    assert not ok and isinstance(err, CapTokError), "truncation must fail the tail proof"

def test_expired_rejected():
    priv, pub = _owner()
    tok = _mint(pub, priv, exp=1_000)
    assert verify(tok, owner_pubkey=pub, audience="box-1", now=999).exp == 1_000
    ok, err = try_verify(tok, owner_pubkey=pub, audience="box-1", now=1_001)
    assert not ok and isinstance(err, CapTokError)

    earlier = attenuate(tok, agent="a1", caveats=[Caveat.time_leq(EXP_DIM, 500)])
    assert verify(earlier, owner_pubkey=pub, audience="box-1", now=400).exp == 500
    ok, err = try_verify(earlier, owner_pubkey=pub, audience="box-1", now=600)
    assert not ok
    forged_later = _attenuate_unchecked(tok, agent="attacker", caveats=[Caveat.time_leq(EXP_DIM, 5_000)])
    ok, err = try_verify(forged_later, owner_pubkey=pub, audience="box-1", now=2_000)
    assert not ok and isinstance(err, CapTokError), "a later expiry must be rejected as widening"

def test_wrong_audience_rejected():
    priv, pub = _owner()
    tok = _mint(pub, priv, audience="box-A")
    assert verify(tok, owner_pubkey=pub, audience="box-A", now=0).audience == "box-A"
    ok, err = try_verify(tok, owner_pubkey=pub, audience="box-B", now=0)
    assert not ok and isinstance(err, CapTokError)
    ok, err = try_verify(tok, owner_pubkey=pub, audience=None, now=0)
    assert not ok, "a token bound to an audience must not verify with no audience presented"

def test_depth_exceeded_rejected():
    priv, pub = _owner()
    tok = _mint(pub, priv, depth=1)
    t1 = attenuate(tok, agent="a1", caveats=[Caveat.scope("cap", ("a",))])
    assert verify(t1, owner_pubkey=pub, audience="box-1", now=0).depth == 1

    try:
        attenuate(t1, agent="a2", caveats=[Caveat.scope("cap", ("a",))])
        assert False, "attenuate() must refuse to exceed the depth ceiling"
    except CapTokError:
        pass

    forged = _attenuate_unchecked(t1, agent="a2", caveats=[Caveat.scope("cap", ("a",))])
    ok, err = try_verify(forged, owner_pubkey=pub, audience="box-1", now=0)
    assert not ok and isinstance(err, CapTokError)

def test_max_depth_can_only_lower():
    priv, pub = _owner()
    tok = _mint(pub, priv, depth=4)
    lowered = attenuate(tok, agent="a1", caveats=[Caveat.scope("cap", ("a",))],
                        max_redelegation_depth=1)
    assert verify(lowered, owner_pubkey=pub, audience="box-1", now=0).max_depth == 1

    forged = _attenuate_unchecked(lowered, agent="attacker",
                                  caveats=[Caveat.scope("cap", ("a",))],
                                  max_redelegation_depth=9)
    ok, err = try_verify(forged, owner_pubkey=pub, audience="box-1", now=0)
    assert not ok and isinstance(err, CapTokError)

def test_seal_verifies_and_blocks_attenuation():
    priv, pub = _owner()
    tok = seal(attenuate(_mint(pub, priv, scope=("a", "b")), agent="a1",
                         caveats=[Caveat.scope("cap", ("a",))]))
    g = verify(tok, owner_pubkey=pub, audience="box-1", now=0)
    assert g.scopes["cap"] == frozenset({"a"}) and tok.sealed
    try:
        attenuate(tok, agent="a2", caveats=[Caveat.scope("cap", ("a",))])
        assert False, "a sealed token must not be attenuable"
    except CapTokError:
        pass

    d = tok.to_dict()
    d["proof"] = _flip_hex(d["proof"])
    ok, err = try_verify(CapToken.from_dict(d), owner_pubkey=pub, audience="box-1", now=0)
    assert not ok and isinstance(err, CapTokError)

_OFFLINE_CHILD = textwrap.dedent(r"""
    import sys, socket, json
    # HARD-disable all networking BEFORE importing anything that could dial out.
    def _blocked(*a, **k):
        raise OSError("network is disabled in this offline verifier")
    socket.socket = _blocked
    socket.create_connection = _blocked
    socket.getaddrinfo = _blocked
    # prove the block is real: a connect attempt must fail
    try:
        socket.socket(); print("NETWORK-NOT-BLOCKED", file=sys.stderr); sys.exit(3)
    except OSError:
        pass

    ROOT = sys.argv[1]; sys.path.insert(0, ROOT)
    owner_pub = bytes.fromhex(sys.argv[2])
    audience = sys.argv[3]
    token_json = sys.stdin.read()

    from pnlib.captok import verify, CapTokError
    try:
        g = verify(token_json, owner_pubkey=owner_pub, audience=audience, now=0)
    except CapTokError as e:
        print("VERIFY-FAILED:" + repr(e)); sys.exit(2)
    print("OFFLINE-OK " + json.dumps(sorted(g.scopes.get("cap", []))))
""")

def _run_offline(tok, pub, audience):
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(_OFFLINE_CHILD)
        child = f.name
    try:
        env = {k: v for k, v in os.environ.items()
               if not k.lower().endswith("_proxy") and k.upper() not in ("HTTP_PROXY", "HTTPS_PROXY")}
        proc = subprocess.run(
            [sys.executable, child, ROOT, pub.hex(), audience],
            input=tok.to_json(), capture_output=True, text=True, timeout=60, env=env)
        return proc
    finally:
        os.unlink(child)

def test_offline_verify_in_subprocess_with_no_network():
    priv, pub = _owner()
    tok = attenuate(_mint(pub, priv, scope=("a", "b", "c"), audience="box-1"),
                    agent="a1", caveats=[Caveat.scope("cap", ("a", "b"))])
    proc = _run_offline(tok, pub, "box-1")
    assert proc.returncode == 0, f"offline verify failed: rc={proc.returncode}\nOUT:{proc.stdout}\nERR:{proc.stderr}"
    assert proc.stdout.startswith("OFFLINE-OK"), proc.stdout
    assert json.loads(proc.stdout.split(" ", 1)[1]) == ["a", "b"]

    d = tok.to_dict(); d["blocks"][0]["sig"] = _flip_hex(d["blocks"][0]["sig"])
    bad = CapToken.from_dict(d)
    proc2 = _run_offline(bad, pub, "box-1")
    assert proc2.returncode == 2 and proc2.stdout.startswith("VERIFY-FAILED"), \
        f"tampered token should fail offline: {proc2.stdout} {proc2.stderr}"

def _run_standalone() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"  FAIL  {t.__name__}: {e!r}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(_run_standalone())
