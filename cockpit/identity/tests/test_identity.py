

from __future__ import annotations

import os
import tempfile
import unittest

from identity import (
    AuthError,
    EnrollmentManager,
    EnrollRequest,
    FederationPolicy,
    IdentityConfig,
    KeyRecord,
    KeyRegistry,
    NonceCache,
    RequestVerifier,
    SigningKey,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REVOKED,
    args_digest_hex,
    b64url_decode,
    build_enroll_request_envelope,
    build_signed_request,
    build_signing_string,
    errors,
    jcs_canonical_bytes,
    key_id_from_pubkey,
)
import json

class Clock:
    def __init__(self, now_ms: int = 1_700_000_000_000):
        self.now = now_ms

    def __call__(self) -> int:
        return self.now

def fresh_stack(clock=None, config=None):

    reg = KeyRegistry(path=None)
    cfg = config or IdentityConfig.default()
    enroll = EnrollmentManager(reg, cfg)
    verifier = RequestVerifier(reg, cfg, clock=clock or Clock())
    return reg, enroll, verifier

def enroll_and_approve(enroll, verifier, owner, dev, principal, role="member",
                       ts=None):

    _env, req = build_enroll_request_envelope(
        dev, principal=principal, device_label=f"{principal}-device", role=role, ts=ts
    )
    rec = enroll.request(req)
    approve_env = build_signed_request(
        owner, principal="owner", verb="enroll.approve",
        args={"key_id": rec.key_id}, ts=ts,
    )
    enroll.approve(
        verifier.verify(approve_env), rec.key_id,
        approve_squatting_principal=True,
    )
    return rec

def bootstrapped(clock=None, config=None):

    if clock is None:
        clock = lambda: int(__import__("time").time() * 1000)
    reg, enroll, verifier = fresh_stack(clock=clock, config=config)
    owner = SigningKey.generate()
    enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
    return reg, enroll, verifier, owner

class TestCanonical(unittest.TestCase):
    def test_jcs_sorts_keys_and_is_minimal(self):
        self.assertEqual(jcs_canonical_bytes({"b": 1, "a": 2}), b'{"a":2,"b":1}')
        self.assertEqual(jcs_canonical_bytes({}), b"{}")
        self.assertEqual(jcs_canonical_bytes([1, 2, 3]), b"[1,2,3]")

    def test_jcs_number_formatting(self):
        self.assertEqual(jcs_canonical_bytes(1), b"1")
        self.assertEqual(jcs_canonical_bytes(1.0), b"1")
        self.assertEqual(jcs_canonical_bytes(-0.0), b"0")
        self.assertEqual(jcs_canonical_bytes(1.5), b"1.5")

    def test_jcs_string_escaping(self):
        self.assertEqual(jcs_canonical_bytes("a\nb"), b'"a\\nb"')
        self.assertEqual(jcs_canonical_bytes('quote"'), b'"quote\\""')

    def test_jcs_bool_and_null(self):
        self.assertEqual(jcs_canonical_bytes(True), b"true")
        self.assertEqual(jcs_canonical_bytes(False), b"false")
        self.assertEqual(jcs_canonical_bytes(None), b"null")

    def test_jcs_deterministic_regardless_of_insertion_order(self):
        a = {"z": {"y": 1, "x": 2}, "a": [3, {"m": 1, "k": 2}]}
        b = {"a": [3, {"k": 2, "m": 1}], "z": {"x": 2, "y": 1}}
        self.assertEqual(jcs_canonical_bytes(a), jcs_canonical_bytes(b))

    def test_args_digest_hex_stable(self):
        d1 = args_digest_hex({"text": "hi", "n": 1})
        d2 = args_digest_hex({"n": 1, "text": "hi"})
        self.assertEqual(d1, d2)
        self.assertEqual(len(d1), 64)
        self.assertEqual(d1, d1.lower())

    def test_key_id_shape(self):
        k = SigningKey.generate()
        kid = k.key_id()
        self.assertEqual(len(kid), 22)
        self.assertEqual(kid, key_id_from_pubkey(k.raw_public()))

        self.assertEqual(kid, k.key_id())

    def test_key_id_matches_manual_computation(self):
        import base64
        import hashlib
        k = SigningKey.generate()
        raw = k.raw_public()
        expect = base64.urlsafe_b64encode(
            hashlib.sha256(raw).digest()[:16]
        ).rstrip(b"=").decode()
        self.assertEqual(k.key_id(), expect)

    def test_signing_string_has_no_trailing_newline(self):
        s = build_signing_string(
            type="req", id="x", ts=1, nonce="n", principal="owner",
            key_id="k", funding="member-subsidized", verb="conversation.say",
            args={},
        )
        self.assertFalse(s.endswith(b"\n"))
        self.assertEqual(s.count(b"\n"), 9)

    def test_signing_string_rejects_embedded_newline(self):
        with self.assertRaises(ValueError):
            build_signing_string(
                type="req", id="x", ts=1, nonce="n", principal="ow\nner",
                key_id="k", funding="f", verb="v", args={},
            )

class TestBootstrap(unittest.TestCase):
    def test_bootstrap_creates_active_owner(self):
        reg, enroll, verifier = fresh_stack()
        owner = SigningKey.generate()
        rec = enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        self.assertEqual(rec.role, "owner")
        self.assertEqual(rec.principal, "owner")
        self.assertTrue(rec.active)
        self.assertTrue(reg.any_active_owner())

    def test_bootstrap_refuses_second_owner(self):
        reg, enroll, verifier, owner = bootstrapped()
        with self.assertRaises(AuthError) as cm:
            enroll.bootstrap_owner(SigningKey.generate().pubkey_b64url(), "x")
        self.assertEqual(cm.exception.code, errors.ERR_ENROLL_CONFLICT)

class TestEnrollmentFlow(unittest.TestCase):
    def test_enroll_request_lands_pending(self):
        reg, enroll, verifier, owner = bootstrapped()
        dev = SigningKey.generate()
        _env, req = build_enroll_request_envelope(
            dev, principal="member:ana", device_label="ana-laptop"
        )
        rec = enroll.request(req)
        self.assertEqual(rec.status, "pending")
        self.assertFalse(rec.approved)
        self.assertEqual(rec.principal, "member:ana")

    def test_enroll_request_bad_proof_of_possession_rejected(self):
        reg, enroll, verifier, owner = bootstrapped()
        dev = SigningKey.generate()
        _env, req = build_enroll_request_envelope(
            dev, principal="member:ana", device_label="ana-laptop"
        )
        req.sig = req.sig[:-4] + ("AAAA" if not req.sig.endswith("AAAA") else "BBBB")
        with self.assertRaises(AuthError) as cm:
            enroll.request(req)
        self.assertEqual(cm.exception.code, errors.ERR_BAD_SIG)

    def test_enroll_request_may_not_self_claim_owner(self):
        reg, enroll, verifier, owner = bootstrapped()
        dev = SigningKey.generate()
        _env, req = build_enroll_request_envelope(
            dev, principal="owner", device_label="attacker", role="owner"
        )
        with self.assertRaises(AuthError) as cm:
            enroll.request(req)
        self.assertEqual(cm.exception.code, errors.ERR_BAD_REQUEST)

    def test_duplicate_enroll_request_conflicts(self):
        reg, enroll, verifier, owner = bootstrapped()
        dev = SigningKey.generate()
        _e, req = build_enroll_request_envelope(dev, principal="member:ana",
                                                device_label="l")
        enroll.request(req)
        _e2, req2 = build_enroll_request_envelope(dev, principal="member:ana",
                                                  device_label="l")
        with self.assertRaises(AuthError) as cm:
            enroll.request(req2)
        self.assertEqual(cm.exception.code, errors.ERR_ENROLL_CONFLICT)

    def test_only_owner_can_approve(self):
        reg, enroll, verifier, owner = bootstrapped()

        ana = SigningKey.generate()
        bob = SigningKey.generate()
        ana_rec = enroll_and_approve(enroll, verifier, owner, ana, "member:ana")
        _e, bob_req = build_enroll_request_envelope(bob, principal="member:bob",
                                                    device_label="bob")
        bob_rec = enroll.request(bob_req)

        ana_env = build_signed_request(ana, principal="member:ana",
                                       verb="enroll.approve",
                                       args={"key_id": bob_rec.key_id})
        with self.assertRaises(AuthError) as cm:
            enroll.approve(verifier.verify(ana_env), bob_rec.key_id)
        self.assertEqual(cm.exception.code, errors.ERR_NOT_OWNER)

    def test_deny_drops_pending_key(self):
        reg, enroll, verifier, owner = bootstrapped()
        dev = SigningKey.generate()
        _e, req = build_enroll_request_envelope(dev, principal="member:ana",
                                                device_label="l")
        rec = enroll.request(req)
        deny_env = build_signed_request(owner, principal="owner",
                                        verb="enroll.deny",
                                        args={"key_id": rec.key_id})
        enroll.deny(verifier.verify(deny_env), rec.key_id)
        self.assertFalse(reg.has(rec.key_id))

    def test_enroll_list(self):
        reg, enroll, verifier, owner = bootstrapped()
        ana = SigningKey.generate()
        enroll_and_approve(enroll, verifier, owner, ana, "member:ana")
        listed = enroll.list()
        kids = {r["key_id"] for r in listed}
        self.assertIn(owner.key_id(), kids)
        self.assertIn(ana.key_id(), kids)
        only_ana = enroll.list("member:ana")
        self.assertEqual(len(only_ana), 1)
        self.assertEqual(only_ana[0]["principal"], "member:ana")

class TestVerifyHappyPath(unittest.TestCase):
    def test_valid_signature_accepted(self):
        clock = Clock()
        reg, enroll, verifier = fresh_stack(clock=clock)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        ana = SigningKey.generate()
        enroll_and_approve(enroll, verifier, owner, ana, "member:ana", ts=clock.now)
        env = build_signed_request(ana, principal="member:ana",
                                   verb="conversation.say",
                                   args={"text": "hello"}, ts=clock.now)
        vr = verifier.verify(env)
        self.assertEqual(vr.principal, "member:ana")
        self.assertEqual(vr.role, "member")
        self.assertEqual(vr.verb, "conversation.say")
        self.assertEqual(vr.key_id, ana.key_id())
        self.assertEqual(vr.args, {"text": "hello"})

    def test_full_enroll_approve_verify_lifecycle(self):
        clock = Clock()
        reg, enroll, verifier = fresh_stack(clock=clock)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        dev = SigningKey.generate()

        _e, req = build_enroll_request_envelope(dev, principal="member:ana",
                                                device_label="l", ts=clock.now)
        rec = enroll.request(req)
        pending_env = build_signed_request(dev, principal="member:ana",
                                           verb="conversation.say",
                                           args={}, ts=clock.now)
        with self.assertRaises(AuthError) as cm:
            verifier.verify(pending_env)
        self.assertEqual(cm.exception.code, errors.ERR_NOT_APPROVED)

        approve_env = build_signed_request(owner, principal="owner",
                                           verb="enroll.approve",
                                           args={"key_id": rec.key_id}, ts=clock.now)
        enroll.approve(verifier.verify(approve_env), rec.key_id)

        ok_env = build_signed_request(dev, principal="member:ana",
                                      verb="conversation.say",
                                      args={"text": "hi"}, ts=clock.now)
        vr = verifier.verify(ok_env)
        self.assertEqual(vr.principal, "member:ana")

class TestVerifyRejections(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.reg, self.enroll, self.verifier = fresh_stack(clock=self.clock)
        self.owner = SigningKey.generate()
        self.enroll.bootstrap_owner(self.owner.pubkey_b64url(), "console")
        self.ana = SigningKey.generate()
        enroll_and_approve(self.enroll, self.verifier, self.owner, self.ana,
                           "member:ana", ts=self.clock.now)

    def _good_env(self, **over):
        base = dict(principal="member:ana", verb="conversation.say",
                    args={"text": "hi"}, ts=self.clock.now)
        base.update(over)
        return build_signed_request(self.ana, **base)

    def test_bad_signature_rejected(self):
        env = self._good_env()

        env["sig"] = env["sig"][:-2] + ("aa" if not env["sig"].endswith("aa") else "bb")
        with self.assertRaises(AuthError) as cm:
            self.verifier.verify(env)
        self.assertEqual(cm.exception.code, errors.ERR_BAD_SIG)

    def test_tampered_args_after_signing_rejected(self):
        env = self._good_env()
        env["args"] = {"text": "TAMPERED"}
        with self.assertRaises(AuthError) as cm:
            self.verifier.verify(env)
        self.assertEqual(cm.exception.code, errors.ERR_BAD_SIG)

    def test_tampered_verb_after_signing_rejected(self):
        env = self._good_env()
        env["verb"] = "verb.delete"
        with self.assertRaises(AuthError) as cm:
            self.verifier.verify(env)
        self.assertEqual(cm.exception.code, errors.ERR_BAD_SIG)

    def test_tampered_funding_after_signing_rejected(self):
        env = self._good_env()
        env["funding"] = "byo:llm"
        with self.assertRaises(AuthError) as cm:
            self.verifier.verify(env)
        self.assertEqual(cm.exception.code, errors.ERR_BAD_SIG)

    def test_unknown_key_rejected(self):
        stranger = SigningKey.generate()
        env = build_signed_request(stranger, principal="member:ana",
                                   verb="conversation.say", args={},
                                   ts=self.clock.now)
        with self.assertRaises(AuthError) as cm:
            self.verifier.verify(env)
        self.assertEqual(cm.exception.code, errors.ERR_NOT_ENROLLED)

    def test_pending_key_rejected(self):
        dev = SigningKey.generate()
        _e, req = build_enroll_request_envelope(dev, principal="member:x",
                                                device_label="l", ts=self.clock.now)
        self.enroll.request(req)
        env = build_signed_request(dev, principal="member:x",
                                   verb="conversation.say", args={},
                                   ts=self.clock.now)
        with self.assertRaises(AuthError) as cm:
            self.verifier.verify(env)
        self.assertEqual(cm.exception.code, errors.ERR_NOT_APPROVED)

    def test_name_key_mismatch_rejected(self):

        env = build_signed_request(self.ana, principal="member:bob",
                                   verb="conversation.say", args={},
                                   ts=self.clock.now)
        with self.assertRaises(AuthError) as cm:
            self.verifier.verify(env)
        self.assertEqual(cm.exception.code, errors.ERR_PRINCIPAL_MISMATCH)

    def test_stale_timestamp_rejected(self):
        old_ts = self.clock.now - (IdentityConfig.default().skew_ms + 1000)
        env = self._good_env(ts=old_ts)
        with self.assertRaises(AuthError) as cm:
            self.verifier.verify(env)
        self.assertEqual(cm.exception.code, errors.ERR_STALE)

    def test_future_timestamp_rejected(self):
        future_ts = self.clock.now + (IdentityConfig.default().skew_ms + 1000)
        env = self._good_env(ts=future_ts)
        with self.assertRaises(AuthError) as cm:
            self.verifier.verify(env)
        self.assertEqual(cm.exception.code, errors.ERR_STALE)

    def test_timestamp_at_edge_of_window_accepted(self):
        edge_ts = self.clock.now - IdentityConfig.default().skew_ms
        env = self._good_env(ts=edge_ts)
        vr = self.verifier.verify(env)
        self.assertEqual(vr.principal, "member:ana")

    def test_replayed_nonce_rejected(self):
        env = self._good_env()
        self.verifier.verify(env)
        with self.assertRaises(AuthError) as cm:
            self.verifier.verify(env)
        self.assertEqual(cm.exception.code, errors.ERR_REPLAY)

    def test_same_nonce_different_principal_not_a_replay(self):

        bob = SigningKey.generate()
        enroll_and_approve(self.enroll, self.verifier, self.owner, bob,
                           "member:bob", ts=self.clock.now)
        nonce = "AAAAAAAAAAAAAAAAAAAAAA"
        e1 = build_signed_request(self.ana, principal="member:ana",
                                  verb="conversation.say", args={},
                                  ts=self.clock.now, nonce=nonce)
        e2 = build_signed_request(bob, principal="member:bob",
                                  verb="conversation.say", args={},
                                  ts=self.clock.now, nonce=nonce)
        self.verifier.verify(e1)
        self.verifier.verify(e2)

    def test_nonce_reusable_after_window_expiry(self):
        env = self._good_env()
        self.verifier.verify(env)

        self.clock.now += 2 * IdentityConfig.default().skew_ms + 1

        env2 = self._good_env(ts=self.clock.now)
        self.verifier.verify(env2)

    def test_contract_mismatch_rejected(self):
        env = build_signed_request(self.ana, principal="member:ana",
                                   verb="conversation.say", args={},
                                   ts=self.clock.now, contract="portal-contract/2")
        with self.assertRaises(AuthError) as cm:
            self.verifier.verify(env)
        self.assertEqual(cm.exception.code, errors.ERR_CONTRACT_MISMATCH)

    def test_missing_field_rejected(self):
        env = self._good_env()
        del env["nonce"]
        with self.assertRaises(AuthError) as cm:
            self.verifier.verify(env)
        self.assertEqual(cm.exception.code, errors.ERR_BAD_REQUEST)

    def test_non_req_type_rejected(self):
        env = self._good_env()
        env["type"] = "res"
        with self.assertRaises(AuthError) as cm:
            self.verifier.verify(env)

        self.assertIn(cm.exception.code, (errors.ERR_BAD_SIG, errors.ERR_BAD_REQUEST))

class TestVerifyOrder(unittest.TestCase):
    def test_contract_checked_before_enrollment(self):
        clock = Clock()
        reg, enroll, verifier = fresh_stack(clock=clock)
        stranger = SigningKey.generate()
        env = build_signed_request(stranger, principal="member:x",
                                   verb="conversation.say", args={},
                                   ts=clock.now, contract="portal-contract/2")
        with self.assertRaises(AuthError) as cm:
            verifier.verify(env)

        self.assertEqual(cm.exception.code, errors.ERR_CONTRACT_MISMATCH)

    def test_enrollment_checked_before_signature(self):
        clock = Clock()
        reg, enroll, verifier = fresh_stack(clock=clock)
        stranger = SigningKey.generate()
        env = build_signed_request(stranger, principal="member:x",
                                   verb="conversation.say", args={}, ts=clock.now)
        env["sig"] = "garbage"
        with self.assertRaises(AuthError) as cm:
            verifier.verify(env)

        self.assertEqual(cm.exception.code, errors.ERR_NOT_ENROLLED)

    def test_interlock_checked_before_signature(self):
        clock = Clock()
        reg, enroll, verifier = fresh_stack(clock=clock)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        ana = SigningKey.generate()
        enroll_and_approve(enroll, verifier, owner, ana, "member:ana", ts=clock.now)

        env = build_signed_request(ana, principal="member:bob",
                                   verb="conversation.say", args={}, ts=clock.now)
        env["sig"] = "garbage"
        with self.assertRaises(AuthError) as cm:
            verifier.verify(env)
        self.assertEqual(cm.exception.code, errors.ERR_PRINCIPAL_MISMATCH)

    def test_signature_checked_before_timestamp(self):
        clock = Clock()
        reg, enroll, verifier = fresh_stack(clock=clock)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        ana = SigningKey.generate()
        enroll_and_approve(enroll, verifier, owner, ana, "member:ana", ts=clock.now)

        stale = clock.now - 10 * IdentityConfig.default().skew_ms
        env = build_signed_request(ana, principal="member:ana",
                                   verb="conversation.say", args={}, ts=clock.now)
        env["sig"] = env["sig"][:-2] + ("aa" if not env["sig"].endswith("aa") else "bb")
        env["ts"] = stale
        with self.assertRaises(AuthError) as cm:
            verifier.verify(env)
        self.assertEqual(cm.exception.code, errors.ERR_BAD_SIG)

    def test_no_side_effect_on_failure_nonce_not_consumed(self):

        clock = Clock()
        reg, enroll, verifier = fresh_stack(clock=clock)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        ana = SigningKey.generate()
        enroll_and_approve(enroll, verifier, owner, ana, "member:ana", ts=clock.now)
        nonce = "BBBBBBBBBBBBBBBBBBBBBB"
        stale = clock.now - (IdentityConfig.default().skew_ms + 5000)
        stale_env = build_signed_request(ana, principal="member:ana",
                                         verb="conversation.say", args={},
                                         ts=stale, nonce=nonce)
        with self.assertRaises(AuthError) as cm:
            verifier.verify(stale_env)
        self.assertEqual(cm.exception.code, errors.ERR_STALE)

        ok_env = build_signed_request(ana, principal="member:ana",
                                      verb="conversation.say", args={},
                                      ts=clock.now, nonce=nonce)
        vr = verifier.verify(ok_env)
        self.assertEqual(vr.nonce, nonce)

class TestRevocation(unittest.TestCase):
    def test_revoke_then_reject(self):
        clock = Clock()
        reg, enroll, verifier = fresh_stack(clock=clock)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        ana = SigningKey.generate()
        rec = enroll_and_approve(enroll, verifier, owner, ana, "member:ana",
                                 ts=clock.now)

        ok = build_signed_request(ana, principal="member:ana",
                                  verb="conversation.say", args={}, ts=clock.now)
        verifier.verify(ok)

        rev_env = build_signed_request(owner, principal="owner",
                                       verb="enroll.revoke",
                                       args={"key_id": rec.key_id}, ts=clock.now)
        enroll.revoke(verifier.verify(rev_env), rec.key_id)

        after = build_signed_request(ana, principal="member:ana",
                                     verb="conversation.say", args={}, ts=clock.now)
        with self.assertRaises(AuthError) as cm:
            verifier.verify(after)
        self.assertEqual(cm.exception.code, errors.ERR_PRINCIPAL_MISMATCH)

    def test_member_can_revoke_own_key_but_not_others(self):
        clock = Clock()
        reg, enroll, verifier = fresh_stack(clock=clock)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        ana = SigningKey.generate()
        bob = SigningKey.generate()
        ana_rec = enroll_and_approve(enroll, verifier, owner, ana, "member:ana",
                                     ts=clock.now)
        bob_rec = enroll_and_approve(enroll, verifier, owner, bob, "member:bob",
                                     ts=clock.now)

        ana_env = build_signed_request(ana, principal="member:ana",
                                       verb="enroll.revoke",
                                       args={"key_id": bob_rec.key_id}, ts=clock.now)
        with self.assertRaises(AuthError) as cm:
            enroll.revoke(verifier.verify(ana_env), bob_rec.key_id)
        self.assertEqual(cm.exception.code, errors.ERR_NOT_OWNER)

        ana_env2 = build_signed_request(ana, principal="member:ana",
                                        verb="enroll.revoke",
                                        args={"key_id": ana_rec.key_id},
                                        ts=clock.now)
        enroll.revoke(verifier.verify(ana_env2), ana_rec.key_id)
        self.assertEqual(reg.get(ana_rec.key_id).status, "revoked")

    def test_revoke_is_idempotent(self):
        clock = Clock()
        reg, enroll, verifier = fresh_stack(clock=clock)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        ana = SigningKey.generate()
        rec = enroll_and_approve(enroll, verifier, owner, ana, "member:ana",
                                 ts=clock.now)
        for _ in range(2):
            rev = build_signed_request(owner, principal="owner",
                                       verb="enroll.revoke",
                                       args={"key_id": rec.key_id}, ts=clock.now)
            enroll.revoke(verifier.verify(rev), rec.key_id)
        self.assertEqual(reg.get(rec.key_id).status, "revoked")

class TestRotation(unittest.TestCase):
    def test_rotate_old_active_until_new_approved(self):
        clock = Clock()
        reg, enroll, verifier = fresh_stack(clock=clock)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        ana = SigningKey.generate()
        old_rec = enroll_and_approve(enroll, verifier, owner, ana, "member:ana",
                                     ts=clock.now)
        ana_new = SigningKey.generate()

        rot_env = build_signed_request(ana, principal="member:ana",
                                       verb="enroll.rotate",
                                       args={"old_key_id": old_rec.key_id,
                                             "new_pubkey": ana_new.pubkey_b64url()},
                                       ts=clock.now)
        new_rec = enroll.rotate(verifier.verify(rot_env), old_rec.key_id,
                                ana_new.pubkey_b64url())
        self.assertEqual(new_rec.status, "pending")
        self.assertEqual(new_rec.principal, "member:ana")
        self.assertEqual(new_rec.rotated_from, old_rec.key_id)
        self.assertEqual(reg.get(old_rec.key_id).rotated_to, new_rec.key_id)

        old_use = build_signed_request(ana, principal="member:ana",
                                       verb="conversation.say", args={}, ts=clock.now)
        verifier.verify(old_use)

        new_use = build_signed_request(ana_new, principal="member:ana",
                                       verb="conversation.say", args={}, ts=clock.now)
        with self.assertRaises(AuthError) as cm:
            verifier.verify(new_use)
        self.assertEqual(cm.exception.code, errors.ERR_NOT_APPROVED)

        approve = build_signed_request(owner, principal="owner",
                                       verb="enroll.approve",
                                       args={"key_id": new_rec.key_id}, ts=clock.now)
        enroll.approve(verifier.verify(approve), new_rec.key_id)
        new_use2 = build_signed_request(ana_new, principal="member:ana",
                                        verb="conversation.say", args={}, ts=clock.now)
        verifier.verify(new_use2)
        rev = build_signed_request(owner, principal="owner",
                                   verb="enroll.revoke",
                                   args={"key_id": old_rec.key_id}, ts=clock.now)
        enroll.revoke(verifier.verify(rev), old_rec.key_id)
        old_after = build_signed_request(ana, principal="member:ana",
                                         verb="conversation.say", args={}, ts=clock.now)
        with self.assertRaises(AuthError) as cm:
            verifier.verify(old_after)
        self.assertEqual(cm.exception.code, errors.ERR_PRINCIPAL_MISMATCH)

    def test_rotate_requires_active_old_key(self):
        clock = Clock()
        reg, enroll, verifier = fresh_stack(clock=clock)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        ana = SigningKey.generate()
        rec = enroll_and_approve(enroll, verifier, owner, ana, "member:ana",
                                 ts=clock.now)

        rev = build_signed_request(owner, principal="owner", verb="enroll.revoke",
                                   args={"key_id": rec.key_id}, ts=clock.now)
        enroll.revoke(verifier.verify(rev), rec.key_id)

        ana_new = SigningKey.generate()
        owner_rot = build_signed_request(owner, principal="owner",
                                         verb="enroll.rotate", ts=clock.now,
                                         args={"old_key_id": rec.key_id,
                                               "new_pubkey": ana_new.pubkey_b64url()})
        with self.assertRaises(AuthError) as cm:
            enroll.rotate(verifier.verify(owner_rot), rec.key_id,
                          ana_new.pubkey_b64url())
        self.assertEqual(cm.exception.code, errors.ERR_ENROLL_CONFLICT)

class TestFederationOwnerApprove(unittest.TestCase):

    def test_delegate_disabled_by_default(self):
        clock = Clock()
        cfg = IdentityConfig(federation_policy=FederationPolicy.OWNER_APPROVE)
        reg, enroll, verifier = fresh_stack(clock=clock, config=cfg)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        ana = SigningKey.generate()
        enroll_and_approve(enroll, verifier, owner, ana, "member:ana", ts=clock.now)

        newdev = SigningKey.generate()
        del_env = build_signed_request(ana, principal="member:ana",
                                       verb="enroll.delegate",
                                       args={"pubkey": newdev.pubkey_b64url(),
                                             "device_label": "phone",
                                             "principal": "member:ana"},
                                       ts=clock.now)
        with self.assertRaises(AuthError) as cm:
            enroll.delegate(verifier.verify(del_env), newdev.pubkey_b64url(),
                            "phone", "member:ana")
        self.assertEqual(cm.exception.code, errors.ERR_DELEGATE_DISABLED)

    def test_default_config_is_owner_approve(self):
        self.assertEqual(IdentityConfig.default().federation_policy,
                         FederationPolicy.OWNER_APPROVE)

class TestFederationChainOfTrust(unittest.TestCase):

    def test_delegate_adds_active_key(self):
        clock = Clock()
        cfg = IdentityConfig(federation_policy=FederationPolicy.CHAIN_OF_TRUST)
        reg, enroll, verifier = fresh_stack(clock=clock, config=cfg)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        ana = SigningKey.generate()
        enroll_and_approve(enroll, verifier, owner, ana, "member:ana", ts=clock.now)

        phone = SigningKey.generate()
        del_env = build_signed_request(ana, principal="member:ana",
                                       verb="enroll.delegate",
                                       args={"pubkey": phone.pubkey_b64url(),
                                             "device_label": "phone",
                                             "principal": "member:ana"},
                                       ts=clock.now)
        new_rec = enroll.delegate(verifier.verify(del_env), phone.pubkey_b64url(),
                                  "phone", "member:ana")
        self.assertEqual(new_rec.status, "approved")
        self.assertEqual(new_rec.enrolled_by, ana.key_id())

        use = build_signed_request(phone, principal="member:ana",
                                   verb="conversation.say", args={}, ts=clock.now)
        vr = verifier.verify(use)
        self.assertEqual(vr.principal, "member:ana")

    def test_delegate_cannot_mint_owner(self):
        cfg = IdentityConfig(federation_policy=FederationPolicy.CHAIN_OF_TRUST)
        clock = Clock()
        reg, enroll, verifier = fresh_stack(clock=clock, config=cfg)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        ana = SigningKey.generate()
        enroll_and_approve(enroll, verifier, owner, ana, "member:ana", ts=clock.now)
        rogue = SigningKey.generate()
        del_env = build_signed_request(ana, principal="member:ana",
                                       verb="enroll.delegate",
                                       args={"pubkey": rogue.pubkey_b64url(),
                                             "device_label": "x",
                                             "principal": "owner"},
                                       ts=clock.now)
        with self.assertRaises(AuthError) as cm:
            enroll.delegate(verifier.verify(del_env), rogue.pubkey_b64url(),
                            "x", "owner", role="owner")
        self.assertEqual(cm.exception.code, errors.ERR_BAD_REQUEST)

class TestMultiDevice(unittest.TestCase):
    def test_principal_can_hold_multiple_active_keys(self):
        clock = Clock()
        reg, enroll, verifier = fresh_stack(clock=clock)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        dev1 = SigningKey.generate()
        dev2 = SigningKey.generate()
        enroll_and_approve(enroll, verifier, owner, dev1, "member:ana", ts=clock.now)
        enroll_and_approve(enroll, verifier, owner, dev2, "member:ana", ts=clock.now)
        self.assertEqual(len(reg.active_keys_for("member:ana")), 2)
        for dev in (dev1, dev2):
            env = build_signed_request(dev, principal="member:ana",
                                       verb="conversation.say", args={}, ts=clock.now)
            self.assertEqual(verifier.verify(env).principal, "member:ana")

class TestRegistryPersistence(unittest.TestCase):
    def test_registry_round_trips_through_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "registry.json")
            reg = KeyRegistry(path)
            cfg = IdentityConfig.default()
            enroll = EnrollmentManager(reg, cfg)
            verifier = RequestVerifier(reg, cfg, clock=Clock())
            owner = SigningKey.generate()
            enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
            ana = SigningKey.generate()
            enroll_and_approve(enroll, verifier, owner, ana, "member:ana",
                               ts=Clock().now)
            self.assertTrue(os.path.exists(path))

            reg2 = KeyRegistry(path)
            self.assertTrue(reg2.any_active_owner())
            ana_rec = reg2.get(ana.key_id())
            self.assertIsNotNone(ana_rec)
            self.assertEqual(ana_rec.principal, "member:ana")
            self.assertTrue(ana_rec.active)

            v2 = RequestVerifier(reg2, IdentityConfig.default(), clock=Clock())
            env = build_signed_request(ana, principal="member:ana",
                                       verb="conversation.say", args={},
                                       ts=Clock().now)
            self.assertEqual(v2.verify(env).principal, "member:ana")

    def test_atomic_write_no_partial_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "registry.json")
            reg = KeyRegistry(path)
            enroll = EnrollmentManager(reg)
            owner = SigningKey.generate()
            enroll.bootstrap_owner(owner.pubkey_b64url(), "console")

            leftovers = [f for f in os.listdir(d) if f.startswith(".registry.")]
            self.assertEqual(leftovers, [])

class TestEnrollClaimBinding(unittest.TestCase):

    def test_poc1_self_signed_unrelated_message_rejected(self):

        reg, enroll, verifier, owner = bootstrapped()
        attacker = SigningKey.generate()
        bogus_sig = attacker.sign_b64url(b"totally-unrelated-message")
        req = EnrollRequest(
            pubkey=attacker.pubkey_b64url(),
            device_label="attacker",
            principal="member:victim-name",
            role="member",
            envelope={},
            sig=bogus_sig,
        )
        with self.assertRaises(AuthError) as cm:
            enroll.request(req)
        self.assertEqual(cm.exception.code, errors.ERR_BAD_REQUEST)
        self.assertFalse(reg.has(attacker.key_id()))

    def test_poc3_mutating_principal_after_signing_rejected(self):

        reg, enroll, verifier, owner = bootstrapped()
        dev = SigningKey.generate()
        _env, req = build_enroll_request_envelope(
            dev, principal="member:ana", device_label="ana-laptop"
        )
        req.principal = "member:pinned-victim"
        with self.assertRaises(AuthError) as cm:
            enroll.request(req)
        self.assertEqual(cm.exception.code, errors.ERR_BAD_REQUEST)
        self.assertFalse(reg.has(dev.key_id()))

    def test_mutating_pubkey_claim_rejected(self):

        reg, enroll, verifier, owner = bootstrapped()
        dev = SigningKey.generate()
        other = SigningKey.generate()
        _env, req = build_enroll_request_envelope(
            dev, principal="member:ana", device_label="l"
        )
        req.pubkey = other.pubkey_b64url()
        with self.assertRaises(AuthError) as cm:
            enroll.request(req)
        self.assertEqual(cm.exception.code, errors.ERR_BAD_REQUEST)

    def test_mutating_role_claim_rejected(self):

        reg, enroll, verifier, owner = bootstrapped()
        dev = SigningKey.generate()
        _env, req = build_enroll_request_envelope(
            dev, principal="member:ana", device_label="l", role="guest"
        )
        req.role = "member"
        with self.assertRaises(AuthError) as cm:
            enroll.request(req)
        self.assertEqual(cm.exception.code, errors.ERR_BAD_REQUEST)

    def test_tampered_envelope_args_rejected(self):

        reg, enroll, verifier, owner = bootstrapped()
        dev = SigningKey.generate()
        _env, req = build_enroll_request_envelope(
            dev, principal="member:ana", device_label="l"
        )

        req.envelope["args"] = dict(req.envelope["args"], device_label="EVIL")
        with self.assertRaises(AuthError) as cm:
            enroll.request(req)

        self.assertEqual(cm.exception.code, errors.ERR_BAD_SIG)

    def test_wrong_verb_envelope_rejected(self):
        reg, enroll, verifier, owner = bootstrapped()
        dev = SigningKey.generate()
        _env, req = build_enroll_request_envelope(
            dev, principal="member:ana", device_label="l"
        )
        req.envelope["verb"] = "conversation.say"
        with self.assertRaises(AuthError) as cm:
            enroll.request(req)
        self.assertEqual(cm.exception.code, errors.ERR_BAD_REQUEST)

    def test_honest_enroll_request_still_lands_pending(self):

        reg, enroll, verifier, owner = bootstrapped()
        dev = SigningKey.generate()
        _env, req = build_enroll_request_envelope(
            dev, principal="member:ana", device_label="ana-laptop"
        )
        rec = enroll.request(req)
        self.assertEqual(rec.status, "pending")
        self.assertEqual(rec.principal, "member:ana")
        self.assertEqual(rec.role, "member")

class TestOwnerPrincipalReserved(unittest.TestCase):
    def test_poc16_member_role_cannot_pin_owner_principal(self):

        reg, enroll, verifier, owner = bootstrapped()
        dev = SigningKey.generate()
        _env, req = build_enroll_request_envelope(
            dev, principal="owner", device_label="attacker", role="member"
        )
        with self.assertRaises(AuthError) as cm:
            enroll.request(req)
        self.assertEqual(cm.exception.code, errors.ERR_BAD_REQUEST)
        self.assertFalse(reg.has(dev.key_id()))

    def test_delegate_cannot_mint_owner_principal(self):
        clock = Clock()
        cfg = IdentityConfig(federation_policy=FederationPolicy.CHAIN_OF_TRUST)
        reg, enroll, verifier = fresh_stack(clock=clock, config=cfg)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        ana = SigningKey.generate()
        enroll_and_approve(enroll, verifier, owner, ana, "member:ana", ts=clock.now)
        newk = SigningKey.generate()
        del_env = build_signed_request(ana, principal="member:ana",
                                       verb="enroll.delegate", args={},
                                       ts=clock.now)
        with self.assertRaises(AuthError) as cm:
            enroll.delegate(verifier.verify(del_env), newk.pubkey_b64url(),
                            "x", principal="owner", role="member")
        self.assertEqual(cm.exception.code, errors.ERR_BAD_REQUEST)

class TestNameSquatting(unittest.TestCase):
    def test_poc7_second_key_claiming_active_principal_is_flagged(self):

        reg, enroll, verifier, owner = bootstrapped()
        ana = SigningKey.generate()
        enroll_and_approve(enroll, verifier, owner, ana, "member:ana")
        attacker = SigningKey.generate()
        _env, req = build_enroll_request_envelope(
            attacker, principal="member:ana", device_label="ana-phone(FAKE)"
        )
        rec = enroll.request(req)
        self.assertTrue(rec.claims_existing_principal)

        listed = {r["key_id"]: r for r in enroll.list("member:ana")}
        self.assertTrue(listed[attacker.key_id()]["claims_existing_principal"])

    def test_poc7_owner_cannot_approve_squatter_blind(self):
        reg, enroll, verifier, owner = bootstrapped()
        ana = SigningKey.generate()
        enroll_and_approve(enroll, verifier, owner, ana, "member:ana")
        attacker = SigningKey.generate()
        _env, req = build_enroll_request_envelope(
            attacker, principal="member:ana", device_label="ana-phone(FAKE)"
        )
        rec = enroll.request(req)

        approve_env = build_signed_request(owner, principal="owner",
                                           verb="enroll.approve",
                                           args={"key_id": rec.key_id})
        with self.assertRaises(AuthError) as cm:
            enroll.approve(verifier.verify(approve_env), rec.key_id)
        self.assertEqual(cm.exception.code, errors.ERR_ENROLL_CONFLICT)

        approve_env2 = build_signed_request(owner, principal="owner",
                                            verb="enroll.approve",
                                            args={"key_id": rec.key_id})
        out = enroll.approve(verifier.verify(approve_env2), rec.key_id,
                             approve_squatting_principal=True)
        self.assertTrue(out.active)

    def test_first_key_for_principal_not_flagged(self):
        reg, enroll, verifier, owner = bootstrapped()
        dev = SigningKey.generate()
        _env, req = build_enroll_request_envelope(
            dev, principal="member:new", device_label="l"
        )
        rec = enroll.request(req)
        self.assertFalse(rec.claims_existing_principal)

class TestApproveRoleConfirmation(unittest.TestCase):
    def test_approve_rejects_role_mismatch(self):
        reg, enroll, verifier, owner = bootstrapped()
        dev = SigningKey.generate()
        _env, req = build_enroll_request_envelope(
            dev, principal="member:ana", device_label="l", role="member"
        )
        rec = enroll.request(req)
        approve_env = build_signed_request(owner, principal="owner",
                                           verb="enroll.approve",
                                           args={"key_id": rec.key_id})
        with self.assertRaises(AuthError) as cm:
            enroll.approve(verifier.verify(approve_env), rec.key_id,
                           expected_role="guest")
        self.assertEqual(cm.exception.code, errors.ERR_BAD_REQUEST)

    def test_approve_accepts_matching_role(self):
        reg, enroll, verifier, owner = bootstrapped()
        dev = SigningKey.generate()
        _env, req = build_enroll_request_envelope(
            dev, principal="member:ana", device_label="l", role="member"
        )
        rec = enroll.request(req)
        approve_env = build_signed_request(owner, principal="owner",
                                           verb="enroll.approve",
                                           args={"key_id": rec.key_id})
        out = enroll.approve(verifier.verify(approve_env), rec.key_id,
                             expected_role="member")
        self.assertTrue(out.active)

class TestRegistryIntegrity(unittest.TestCase):
    def _write_registry(self, path, records):
        payload = {
            "schema": "brainarbeit-identity-registry/1",
            "keys": records,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def _good_owner_record(self):
        owner = SigningKey.generate()
        kid = owner.key_id()
        return owner, {
            "key_id": kid, "principal": "owner", "role": "owner",
            "pubkey": owner.pubkey_b64url(), "device_label": "console",
            "status": "approved", "enrolled_at": 1, "approved_at": 1,
        }

    def test_poc8_injected_owner_record_with_bad_keyid_rejected(self):

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "registry.json")
            attacker = SigningKey.generate()
            self._write_registry(path, [{
                "key_id": "AAAAAAAAAAAAAAAAAAAAAA", "principal": "owner",
                "role": "owner", "pubkey": attacker.pubkey_b64url(),
                "device_label": "evil", "status": "approved",
                "enrolled_at": 1, "approved_at": 1,
            }])
            with self.assertRaises(ValueError):
                KeyRegistry(path)

    def test_poc9_invalid_role_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "registry.json")
            _owner, rec = self._good_owner_record()
            rec["role"] = "not-a-real-role"
            self._write_registry(path, [rec])
            with self.assertRaises(ValueError):
                KeyRegistry(path)

    def test_poc9_invalid_status_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "registry.json")
            _owner, rec = self._good_owner_record()
            rec["status"] = "superuser"
            self._write_registry(path, [rec])
            with self.assertRaises(ValueError):
                KeyRegistry(path)

    def test_unknown_field_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "registry.json")
            _owner, rec = self._good_owner_record()
            rec["backdoor"] = True
            self._write_registry(path, [rec])
            with self.assertRaises(ValueError):
                KeyRegistry(path)

    def test_empty_principal_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "registry.json")
            _owner, rec = self._good_owner_record()
            rec["principal"] = ""
            self._write_registry(path, [rec])
            with self.assertRaises(ValueError):
                KeyRegistry(path)

    def test_honest_record_loads(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "registry.json")
            _owner, rec = self._good_owner_record()
            self._write_registry(path, [rec])
            reg = KeyRegistry(path)
            self.assertTrue(reg.any_active_owner())

    def test_registry_file_is_owner_only_0600(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "registry.json")
            reg = KeyRegistry(path)
            enroll = EnrollmentManager(reg)
            owner = SigningKey.generate()
            enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
            mode = os.stat(path).st_mode & 0o777
            self.assertEqual(mode, 0o600, oct(mode))

class TestRevocationTerminal(unittest.TestCase):
    def test_poc14_revoked_key_cannot_re_enroll(self):
        reg, enroll, verifier, owner = bootstrapped()
        ana = SigningKey.generate()
        rec = enroll_and_approve(enroll, verifier, owner, ana, "member:ana")

        rev_env = build_signed_request(owner, principal="owner",
                                       verb="enroll.revoke",
                                       args={"key_id": rec.key_id})
        enroll.revoke(verifier.verify(rev_env), rec.key_id)
        self.assertEqual(reg.get(rec.key_id).status, "revoked")

        _env, req = build_enroll_request_envelope(
            ana, principal="member:ana", device_label="ana-again"
        )
        with self.assertRaises(AuthError) as cm:
            enroll.request(req)
        self.assertEqual(cm.exception.code, errors.ERR_ENROLL_CONFLICT)

        self.assertEqual(reg.get(rec.key_id).status, "revoked")

    def test_revoked_key_barred_even_for_new_principal(self):
        reg, enroll, verifier, owner = bootstrapped()
        ana = SigningKey.generate()
        rec = enroll_and_approve(enroll, verifier, owner, ana, "member:ana")
        rev_env = build_signed_request(owner, principal="owner",
                                       verb="enroll.revoke",
                                       args={"key_id": rec.key_id})
        enroll.revoke(verifier.verify(rev_env), rec.key_id)

        _env, req = build_enroll_request_envelope(
            ana, principal="member:brand-new", device_label="x"
        )
        with self.assertRaises(AuthError) as cm:
            enroll.request(req)
        self.assertEqual(cm.exception.code, errors.ERR_ENROLL_CONFLICT)

class TestNonceCacheBounds(unittest.TestCase):
    def test_per_principal_cap_evicts_oldest(self):
        clock = Clock()
        cache = NonceCache(skew_ms=120_000, clock=clock, max_per_principal=3,
                           max_total=1000)
        for i in range(3):
            self.assertTrue(cache.check_and_record("p", f"n{i}"))

        self.assertTrue(cache.check_and_record("p", "n3"))
        self.assertFalse(cache.seen("p", "n0"))
        self.assertTrue(cache.seen("p", "n3"))

        self.assertTrue(cache.check_and_record("p", "n0"))

    def test_global_cap_bounds_total(self):
        clock = Clock()
        cache = NonceCache(skew_ms=120_000, clock=clock,
                           max_per_principal=1000, max_total=5)
        for i in range(20):
            cache.check_and_record(f"principal-{i}", "n")
        self.assertLessEqual(cache._total, 5)

    def test_expiry_frees_entries(self):
        clock = Clock()
        cache = NonceCache(skew_ms=1000, clock=clock, max_per_principal=1000,
                           max_total=1000)
        cache.check_and_record("p", "n")
        self.assertTrue(cache.seen("p", "n"))
        clock.now += 2001

        self.assertTrue(cache.check_and_record("p", "n2"))
        self.assertFalse(cache.seen("p", "n"))

    def test_oversized_nonce_rejected_before_recording(self):
        clock = Clock()
        reg, enroll, verifier = fresh_stack(clock=clock)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        ana = SigningKey.generate()
        enroll_and_approve(enroll, verifier, owner, ana, "member:ana", ts=clock.now)
        env = build_signed_request(ana, principal="member:ana",
                                   verb="conversation.say", args={},
                                   ts=clock.now, nonce="A" * 500)
        with self.assertRaises(AuthError) as cm:
            verifier.verify(env)
        self.assertEqual(cm.exception.code, errors.ERR_BAD_REQUEST)

    def test_bad_charset_nonce_rejected(self):
        clock = Clock()
        reg, enroll, verifier = fresh_stack(clock=clock)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        ana = SigningKey.generate()
        enroll_and_approve(enroll, verifier, owner, ana, "member:ana", ts=clock.now)
        env = build_signed_request(ana, principal="member:ana",
                                   verb="conversation.say", args={},
                                   ts=clock.now, nonce="not a nonce!!")
        with self.assertRaises(AuthError) as cm:
            verifier.verify(env)
        self.assertEqual(cm.exception.code, errors.ERR_BAD_REQUEST)

class TestConcurrency(unittest.TestCase):
    def test_concurrent_same_nonce_only_one_wins(self):
        import threading

        clock = Clock()
        reg, enroll, verifier = fresh_stack(clock=clock)
        owner = SigningKey.generate()
        enroll.bootstrap_owner(owner.pubkey_b64url(), "console")
        ana = SigningKey.generate()
        enroll_and_approve(enroll, verifier, owner, ana, "member:ana", ts=clock.now)

        env = build_signed_request(ana, principal="member:ana",
                                   verb="conversation.say", args={"x": 1},
                                   ts=clock.now)
        results = {"ok": 0, "replay": 0, "other": 0}
        lock = threading.Lock()
        start = threading.Barrier(16)

        def worker():
            start.wait()
            try:
                verifier.verify(dict(env))
                with lock:
                    results["ok"] += 1
            except AuthError as e:
                with lock:
                    if e.code == errors.ERR_REPLAY:
                        results["replay"] += 1
                    else:
                        results["other"] += 1

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results["ok"], 1, results)
        self.assertEqual(results["replay"], 15, results)
        self.assertEqual(results["other"], 0, results)

    def test_nonce_cache_check_and_record_atomic(self):
        import threading

        clock = Clock()
        cache = NonceCache(skew_ms=120_000, clock=clock)
        wins = []
        lock = threading.Lock()
        start = threading.Barrier(32)

        def worker():
            start.wait()
            if cache.check_and_record("p", "same-nonce"):
                with lock:
                    wins.append(1)

        threads = [threading.Thread(target=worker) for _ in range(32)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(wins), 1)

class TestPubkeyValidation(unittest.TestCase):
    def test_short_pubkey_rejected(self):
        reg, enroll, verifier, owner = bootstrapped()
        dev = SigningKey.generate()
        _env, req = build_enroll_request_envelope(
            dev, principal="member:ana", device_label="l"
        )

        from identity import b64url_encode
        req.pubkey = b64url_encode(b"\x00" * 16)
        with self.assertRaises((AuthError, ValueError)):
            enroll.request(req)

if __name__ == "__main__":
    unittest.main(verbosity=2)
