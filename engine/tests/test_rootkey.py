#!/usr/bin/env python3

import os
import sys
import json
import tempfile
import contextlib

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pnlib import rootkey
from relaylib import crypto

@contextlib.contextmanager
def _clean_env():

    saved = {k: os.environ.get(k) for k in (rootkey.ENV_OWNER_PUBKEY, rootkey.ENV_CONFIG_PATH)}
    for k in saved:
        os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

@contextlib.contextmanager
def _config_file(contents: dict | None):

    d = tempfile.mkdtemp(prefix="rootkey_test_")
    p = os.path.join(d, "config.json")
    if contents is not None:
        with open(p, "w") as f:
            json.dump(contents, f)
    try:
        yield p
    finally:
        with contextlib.suppress(OSError):
            os.remove(p)
        with contextlib.suppress(OSError):
            os.rmdir(d)

def test_load_pinned_pubkey_from_config():
    with _clean_env():
        priv, pub = rootkey.generate_owner_keypair_offbox()
        with _config_file({"lan_ip": "x", "owner_pubkey": pub.hex()}) as p:
            loaded = rootkey.load_owner_pubkey(p)
            assert loaded == pub, "pinned pubkey must load back byte-identical"
            assert rootkey.is_pinned(p) is True

            assert rootkey.load_owner_pubkey(config={"owner_pubkey": pub.hex()}) == pub
            os.environ[rootkey.ENV_CONFIG_PATH] = p
            assert rootkey.load_owner_pubkey() == pub

def test_env_override_wins_over_config():
    with _clean_env():
        _, pub_cfg = rootkey.generate_owner_keypair_offbox()
        _, pub_env = rootkey.generate_owner_keypair_offbox()
        assert pub_cfg != pub_env
        with _config_file({"owner_pubkey": pub_cfg.hex()}) as p:
            os.environ[rootkey.ENV_OWNER_PUBKEY] = pub_env.hex()
            assert rootkey.load_owner_pubkey(p) == pub_env, "env pin must win over the config file"

def test_offbox_sign_verifies_onbox():
    with _clean_env():

        priv, pub = rootkey.generate_owner_keypair_offbox()
        with _config_file({"owner_pubkey": pub.hex()}) as p:
            msg = b"grant seat=alice caps=view:self exp=2026-08-01"
            sig = crypto.ed_sign(priv, msg)
            assert rootkey.verify(sig, msg, path=p) is True

            assert rootkey.require_owner_sig(sig, msg, path=p) == pub

            assert rootkey.verify(sig, msg, pubkey=pub) is True

def test_flip_one_byte_fails():
    with _clean_env():
        priv, pub = rootkey.generate_owner_keypair_offbox()
        with _config_file({"owner_pubkey": pub.hex()}) as p:
            msg = bytearray(b"the-message-that-was-authorised")
            sig = crypto.ed_sign(priv, bytes(msg))
            assert rootkey.verify(bytes(sig), bytes(msg), path=p) is True

            bad_msg = bytearray(msg); bad_msg[0] ^= 0x01
            assert rootkey.verify(bytes(sig), bytes(bad_msg), path=p) is False, "flipped msg must fail"

            bad_sig = bytearray(sig); bad_sig[0] ^= 0x01
            assert rootkey.verify(bytes(bad_sig), bytes(msg), path=p) is False, "flipped sig must fail"

            other_priv, _ = rootkey.generate_owner_keypair_offbox()
            forged = crypto.ed_sign(other_priv, bytes(msg))
            assert rootkey.verify(forged, bytes(msg), path=p) is False, "wrong-key sig must fail"

            try:
                rootkey.require_owner_sig(bytes(bad_sig), bytes(msg), path=p)
                assert False, "require_owner_sig must raise on a bad signature"
            except rootkey.OwnerKeyError:
                pass

def test_no_onbox_owner_signing():

    try:
        rootkey.sign_on_box(b"anything")
        assert False, "sign_on_box must raise SovereignKeyError"
    except rootkey.SovereignKeyError:
        pass

    for forbidden in ("load_owner_privkey", "load_owner_private_key", "owner_privkey",
                      "OWNER_PRIVKEY", "sign", "sign_as_owner", "owner_sign"):
        assert not hasattr(rootkey, forbidden), \
            f"rootkey must expose no owner-private-key API, found: {forbidden}"

    assert rootkey.crypto.ed_sign is crypto.ed_sign

    before = set(os.listdir("."))
    priv1, pub1 = rootkey.generate_owner_keypair_offbox()
    priv2, pub2 = rootkey.generate_owner_keypair_offbox()
    after = set(os.listdir("."))
    assert before == after, "generate_owner_keypair_offbox must not write any file"
    assert priv1 != priv2 and pub1 != pub2, "each off-box keypair must be fresh"
    assert len(pub1) == 32 and len(priv1) == 32

def test_unpinned_and_malformed_fail_closed():
    with _clean_env():

        with _config_file(None) as p:
            assert rootkey.is_pinned(p) is False
            try:
                rootkey.load_owner_pubkey(p)
                assert False, "missing pin must raise"
            except rootkey.OwnerKeyError:
                pass

            try:
                rootkey.verify(b"\x00" * 64, b"msg", path=p)
                assert False, "verify with no pinned key must raise OwnerKeyError"
            except rootkey.OwnerKeyError:
                pass

        with _config_file({"lan_ip": "x"}) as p:
            assert rootkey.is_pinned(p) is False

        for bad in ("nothex!!", "aabb", 12345, "", "  "):
            with _config_file({"owner_pubkey": bad}) as p:
                assert rootkey.is_pinned(p) is False, f"malformed pin {bad!r} must not count as pinned"
                try:
                    rootkey.load_owner_pubkey(p)
                    assert False, f"malformed pin {bad!r} must raise"
                except rootkey.OwnerKeyError:
                    pass

def test_domain_separation():
    with _clean_env():
        priv, pub = rootkey.generate_owner_keypair_offbox()
        with _config_file({"owner_pubkey": pub.hex()}) as p:
            body = b"autonomy-level=3"

            sig = crypto.ed_sign(priv, rootkey.domain_bind(rootkey.DOMAIN_POLICY, body))
            assert rootkey.verify(sig, body, domain=rootkey.DOMAIN_POLICY, path=p) is True

            assert rootkey.verify(sig, body, domain=rootkey.DOMAIN_MINT, path=p) is False
            assert rootkey.verify(sig, body, domain=rootkey.DOMAIN_STH, path=p) is False

            assert rootkey.verify(sig, body, path=p) is False

def test_pin_helper_and_fingerprint_roundtrip():
    with _clean_env():
        priv, pub = rootkey.generate_owner_keypair_offbox()
        with _config_file({"lan_ip": "keepme"}) as p:

            written = rootkey.pin_owner_pubkey(pub, p)
            assert written == p
            with open(p) as f:
                cfg = json.load(f)
            assert cfg["owner_pubkey"] == pub.hex()
            assert cfg["lan_ip"] == "keepme", "pin must merge, not clobber the rest of the config"
            assert rootkey.load_owner_pubkey(p) == pub

            fp1 = rootkey.owner_fingerprint(path=p)
            fp2 = rootkey.owner_fingerprint(pubkey=pub)
            assert fp1 == fp2 and fp1.startswith("owner:b2:")

            try:
                rootkey.pin_owner_pubkey(b"tooshort", p)
                assert False, "pin must reject a wrong-length key"
            except rootkey.OwnerKeyError:
                pass

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
