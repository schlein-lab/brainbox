#!/usr/bin/env python3

from __future__ import annotations
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from relaylib import crypto
from pnlib.a2a.card import (
    make_card, sign_card, signature_ok, SignedCard, AgentCard, AgentCardError,
)
from pnlib.a2a.trust import A2ATrustStore, verify_card, TrustError
from pnlib.a2a.peer import LocalMockPeer, handoff, HandoffRefused, HandoffResult

PASS = FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")

def expect_raise(exc, fn, label):
    global PASS, FAIL
    try:
        fn()
    except exc:
        PASS += 1
        print(f"  ok   {label}")
    except Exception as e:
        FAIL += 1
        print(f"  FAIL {label} (raised {type(e).__name__}: {e})")
    else:
        FAIL += 1
        print(f"  FAIL {label} (did not raise)")

NOW = 10_000

issuer_priv, issuer_pub = crypto.gen_ed25519()
card = make_card(agent_id="agent:worker-7", name="worker-7",
                 capabilities=("summarize", "translate"), issuer_pub=issuer_pub,
                 audience="box:S", ttl=3600, now=NOW)
signed = sign_card(card, issuer_priv=issuer_priv, issuer_pub=issuer_pub)

print("== a well-formed signed card verifies against its trusted issuer ==")
check(signature_ok(signed), "the freshly-signed card has a valid signature")
truststore = A2ATrustStore()
truststore.add_issuer(issuer_pub)
verified = verify_card(signed, truststore, audience="box:S", now=NOW)
check(verified.agent_id == "agent:worker-7", "verify_card returns the card for a trusted issuer")

print("== tamper ONE byte -> verification fails ==")

bad_sig = bytearray(signed.sig)
bad_sig[0] ^= 0x01
tampered_sig = SignedCard(card=signed.card, issuer_pub=signed.issuer_pub, sig=bytes(bad_sig))
check(not signature_ok(tampered_sig), "a one-byte signature flip fails signature_ok")
expect_raise(TrustError,
             lambda: verify_card(tampered_sig, truststore, audience="box:S", now=NOW),
             "verify_card rejects a signature-tampered card")

mutated_card = AgentCard(
    agent_id=card.agent_id, name=card.name,
    capabilities=card.capabilities + ("exfiltrate",),
    issuer=card.issuer, audience=card.audience,
    not_after=card.not_after, issued_at=card.issued_at)
tampered_body = SignedCard(card=mutated_card, issuer_pub=signed.issuer_pub, sig=signed.sig)
check(not signature_ok(tampered_body), "a card-body mutation fails signature_ok (sig no longer binds)")
expect_raise(TrustError,
             lambda: verify_card(tampered_body, truststore, now=NOW),
             "verify_card rejects a body-tampered card")

print("== untrusted issuer -> handoff refused ==")

rogue_priv, rogue_pub = crypto.gen_ed25519()
rogue_card = make_card(agent_id="agent:rogue", name="rogue", capabilities=("anything",),
                       issuer_pub=rogue_pub, audience="box:S", ttl=3600, now=NOW)
rogue_signed = sign_card(rogue_card, issuer_priv=rogue_priv, issuer_pub=rogue_pub)
check(signature_ok(rogue_signed), "the rogue card is internally well-signed (valid signature)")
expect_raise(TrustError,
             lambda: verify_card(rogue_signed, truststore, now=NOW),
             "verify_card refuses a card from an UNTRUSTED issuer")

delivered = {"count": 0}

def rogue_handler(task):
    delivered["count"] += 1
    return "should-never-run"

rogue_peer = LocalMockPeer(rogue_signed, rogue_handler)
expect_raise(HandoffRefused,
             lambda: handoff(rogue_peer, {"do": "x"}, truststore=truststore, audience="box:S", now=NOW),
             "handoff to an untrusted peer is REFUSED")
check(delivered["count"] == 0, "the task was NOT delivered to the untrusted peer")

print("== round-trip to a local mock peer -> provenance=agent ==")
seen = {}

def worker_handler(task):
    seen["task"] = task
    return {"summary": f"done:{task.get('text')}"}

peer = LocalMockPeer(signed, worker_handler)
result = handoff(peer, {"text": "hello-peer"}, truststore=truststore, audience="box:S", now=NOW)
check(isinstance(result, HandoffResult), "handoff returns a HandoffResult")
check(result.response == {"summary": "done:hello-peer"}, "the peer handler ran and returned its result")
check(seen.get("task") == {"text": "hello-peer"}, "the task was delivered to the trusted peer")
check(result.provenance.kind.value == "agent", "the result provenance is AGENT")
check(result.peer_agent_id == "agent:worker-7", "provenance names the peer agent id")

print("== expired card / audience mismatch -> refused ==")
expect_raise(TrustError,
             lambda: verify_card(signed, truststore, audience="box:S", now=NOW + 10_000),
             "an expired card is refused")
expect_raise(TrustError,
             lambda: verify_card(signed, truststore, audience="box:OTHER", now=NOW),
             "a card presented at the wrong audience is refused")

print("== signed-card JSON round-trips and still verifies ==")
rt = SignedCard.from_json(signed.to_json())
check(signature_ok(rt), "a JSON-round-tripped signed card still verifies")

print(f"\n[test_a2a] PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
