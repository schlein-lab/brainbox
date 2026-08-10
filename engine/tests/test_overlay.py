#!/usr/bin/env python3

import os
import sys
import hashlib
import tempfile
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pnlib.overlay import keys, peer, topology, recovery, profiles
from pnlib.overlay.keys import generate_keypair, generate_preshared, WGKeypair
from pnlib.overlay.peer import PeerRegistry, PeerError
from pnlib.overlay.wizard import (OnboardingWizard, WizardConfig, ORDER,
                                  DISCOVER, ENROLL_PEER, EMIT_CONFIG, DONE)

def seeded_rand(seed: bytes):

    state = {"n": 0}

    def _rand(n: int) -> bytes:
        assert n == 32
        state["n"] += 1
        return hashlib.sha256(seed + state["n"].to_bytes(8, "big")).digest()
    return _rand

def parse_conf(text: str):

    iface = {}
    peers = []
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[Interface]":
            cur = iface
            continue
        if line == "[Peer]":
            cur = {}
            peers.append(cur)
            continue
        if "=" in line and cur is not None:
            k, _, v = line.partition("=")
            cur[k.strip()] = v.strip()
    return {"interface": iface, "peers": peers}

def build_registry():

    reg = PeerRegistry()
    keystore = {}
    spec = [
        ("hub",   "cloud-vm",   ["10.0.0.1/32"], "hub.example.com:51820", ("hub",)),
        ("node-a",   "pi",         ["10.0.0.2/32"], None,                     ()),
        ("metal", "bare-metal", ["10.0.0.3/32"], "metal.example.net:51821", ()),
        ("pi2",   "pi",         ["10.0.0.4/32"], None,                     ()),
    ]
    for i, (name, prof, ips, ep, tags) in enumerate(spec):
        kp = generate_keypair(seeded_rand(b"reg-" + name.encode()))
        keystore[name] = kp
        reg.add_peer(name, kp.public_b64, ips, endpoint=ep, profile=prof, tags=tags)
    return reg, keystore

def test_keypair_roundtrip_and_determinism():
    r1 = seeded_rand(b"same-seed")
    r2 = seeded_rand(b"same-seed")
    a = generate_keypair(r1)
    b = generate_keypair(r2)
    assert a.private == b.private and a.public == b.public, "deterministic under same seed"

    assert keys.derive_public(a.private) == a.public
    assert a.verify_consistent()

    assert keys.unb64(a.private_b64) == a.private
    assert keys.unb64(a.public_b64) == a.public

    c = generate_keypair(seeded_rand(b"other-seed"))
    assert c.private != a.private

def test_private_key_never_in_repr():
    kp = generate_keypair(seeded_rand(b"secret"))
    for s in (repr(kp), str(kp), "{}".format(kp)):
        assert kp.private_b64 not in s, "private key leaked into a string form"
        assert "redacted" in s
        assert kp.public_b64 in s
    psk = generate_preshared(seeded_rand(b"psk"))
    assert psk.b64 not in repr(psk) and "redacted" in repr(psk)

def test_bad_key_material_fails_closed():
    try:
        keys.derive_public(b"tooshort")
        assert False, "short private key should raise"
    except keys.OverlayKeyError:
        pass
    try:
        keys.unb64("not-valid-base64!!")
        assert False
    except keys.OverlayKeyError:
        pass

def test_registry_renders_valid_conf_per_peer_no_leak():
    reg, keystore = build_registry()
    d = tempfile.mkdtemp(prefix="pn-overlay-mesh-")
    try:
        configs, paths = topology.generate_mesh(reg, keystore, d)
        assert set(configs) == {"hub", "node-a", "metal", "pi2"}
        assert len(paths) == 4
        all_privs = {n: kp.private_b64 for n, kp in keystore.items()}
        for name, text in configs.items():
            p = parse_conf(text)

            assert p["interface"].get("PrivateKey") == keystore[name].private_b64
            prof = profiles.get_profile(reg.get(name).profile)
            assert p["interface"].get("ListenPort") == str(prof.listen_port)
            assert p["interface"].get("MTU") == str(prof.mtu)

            peer_pubs = {pp["PublicKey"] for pp in p["peers"]}
            assert peer_pubs == {keystore[o].public_b64 for o in keystore if o != name}

            for other, priv in all_privs.items():
                if other != name:
                    assert priv not in text, f"{other} private leaked into {name} conf"

            for sec in p["peers"]:

                nb = next(o for o in reg.peers() if o.public_key_b64 == sec["PublicKey"])
                assert sec.get("AllowedIPs") == ", ".join(nb.allowed_ips)
                nb_prof = profiles.get_profile(nb.profile)
                if nb.endpoint and nb_prof.advertises_endpoint:
                    assert sec.get("Endpoint") == nb.endpoint
                else:
                    assert "Endpoint" not in sec, f"unreachable {nb.name} must not advertise endpoint"
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_conf_file_perms_and_content_owner_priv_only_on_privkey_line():
    reg, keystore = build_registry()
    d = tempfile.mkdtemp(prefix="pn-overlay-perm-")
    try:
        configs, paths = topology.generate_mesh(reg, keystore, d)
        for path in paths:
            mode = os.stat(path).st_mode & 0o777
            assert mode == 0o600, f"{path} must be 0600, got {oct(mode)}"
        for name, text in configs.items():
            priv = keystore[name].private_b64
            for line in text.splitlines():
                s = line.strip()
                if priv in s:
                    assert s.startswith("PrivateKey"), "own priv must only be on PrivateKey line"
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_pi_profile_emits_keepalive_metal_does_not():
    reg, keystore = build_registry()
    d = tempfile.mkdtemp(prefix="pn-overlay-ka-")
    try:
        configs, _ = topology.generate_mesh(reg, keystore, d)
        pi = parse_conf(configs["node-a"])
        for sec in pi["peers"]:
            assert sec.get("PersistentKeepalive") == "25", "pi (behind NAT) keeps pinholes open"
        metal = parse_conf(configs["metal"])
        for sec in metal["peers"]:
            assert "PersistentKeepalive" not in sec, "bare-metal has no NAT to keep alive"
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_hub_and_spoke_shape():
    reg, keystore = build_registry()
    d = tempfile.mkdtemp(prefix="pn-overlay-hs-")
    try:
        configs, _ = topology.generate_hub_and_spoke(reg, keystore, "hub", d)
        hub = parse_conf(configs["hub"])
        hub_peer_pubs = {s["PublicKey"] for s in hub["peers"]}
        assert hub_peer_pubs == {keystore[o].public_b64 for o in ("node-a", "metal", "pi2")}
        for spoke in ("node-a", "metal", "pi2"):
            sc = parse_conf(configs[spoke])
            assert len(sc["peers"]) == 1
            assert sc["peers"][0]["PublicKey"] == keystore["hub"].public_b64
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_registry_rejects_dup_name_and_dup_pubkey():
    reg, keystore = build_registry()
    kp = keystore["hub"]
    try:
        reg.add_peer("hub", kp.public_b64, ["10.0.0.9/32"])
        assert False, "dup name must raise"
    except PeerError:
        pass
    fresh = generate_keypair(seeded_rand(b"fresh"))
    try:
        reg.add_peer("newname", keystore["node-a"].public_b64, ["10.0.0.9/32"])
        assert False, "dup pubkey must raise"
    except PeerError:
        pass

def _wizard_config(out_dir):
    return WizardConfig(
        box_name="edge-box",
        profile="pi",
        allowed_ips=["10.9.0.5/32"],
        endpoint=None,
        tags=["tenant:acme"],
        known_peers=[{
            "name": "cloud-hub",
            "public_key_b64": generate_keypair(seeded_rand(b"cloud-hub")).public_b64,
            "allowed_ips": ["10.9.0.1/32"],
            "endpoint": "hub.example.com:51820",
            "profile": "cloud-vm",
        }],
        out_dir=out_dir,
    )

def test_wizard_runs_to_verify():
    d = tempfile.mkdtemp(prefix="pn-overlay-wiz-")
    try:
        w = OnboardingWizard(_wizard_config(d), rand=seeded_rand(b"box-seed"))
        assert w.state == DISCOVER
        w.run()
        assert w.state == DONE and w.verified
        assert os.path.exists(os.path.join(d, "edge-box.conf"))
        text = open(os.path.join(d, "edge-box.conf")).read()
        p = parse_conf(text)

        assert p["interface"]["PrivateKey"] == w.keypair.private_b64
        assert w.keypair.public_b64 not in text

        assert any(s.get("Endpoint") == "hub.example.com:51820" for s in p["peers"])
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_wizard_idempotent():
    d = tempfile.mkdtemp(prefix="pn-overlay-idem-")
    try:
        w = OnboardingWizard(_wizard_config(d), rand=seeded_rand(b"box-seed"))
        w.run()
        priv1 = w.keypair.private_b64
        cfg1 = open(os.path.join(d, "edge-box.conf")).read()

        for st in ORDER[:-1]:
            w.state = st
            w.advance()
        assert w.keypair.private_b64 == priv1, "re-run must NOT re-mint the key"
        assert len(w.registry) == 2, "re-enroll must not duplicate peers"
        w.state = EMIT_CONFIG
        w.advance()
        cfg2 = open(os.path.join(d, "edge-box.conf")).read()
        assert cfg1 == cfg2, "re-emit must be byte-identical"
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_wizard_resume_matches_straight_through():
    d1 = tempfile.mkdtemp(prefix="pn-overlay-straight-")
    d2 = tempfile.mkdtemp(prefix="pn-overlay-resume-")
    statef = os.path.join(d2, "wizard.state.json")
    try:

        w_full = OnboardingWizard(_wizard_config(d1), rand=seeded_rand(b"resume-seed"))
        w_full.run()
        full_cfg = open(os.path.join(d1, "edge-box.conf")).read()

        w = OnboardingWizard(_wizard_config(d2), rand=seeded_rand(b"resume-seed"))
        w.run(until=ENROLL_PEER)
        assert w.state == ENROLL_PEER
        w.save(statef)
        assert (os.stat(statef).st_mode & 0o777) == 0o600
        w2 = OnboardingWizard.resume(statef, rand=seeded_rand(b"UNUSED-different-seed"))
        w2.run()
        assert w2.state == DONE and w2.verified

        assert w2.keypair.private_b64 == w_full.keypair.private_b64
        resume_cfg = open(os.path.join(d2, "edge-box.conf")).read()
        assert resume_cfg == full_cfg, "resumed run must produce identical config"
    finally:
        shutil.rmtree(d1, ignore_errors=True)
        shutil.rmtree(d2, ignore_errors=True)

def test_recovery_rotates_key_old_no_longer_authorizes():
    reg, keystore = build_registry()
    d = tempfile.mkdtemp(prefix="pn-overlay-rec-")
    try:
        old_pub = reg.get("node-a").public_key_b64
        assert reg.is_authorized(old_pub)
        res = recovery.recover_peer(
            reg, keystore, "node-a",
            rand=seeded_rand(b"rekey-node-a"),
            rotate_psk=True,
            new_allowed_ips=["10.0.0.22/32"],
            clock=lambda: 1234.0)
        new_pub = res.new_keypair.public_b64

        assert not reg.is_authorized(old_pub), "old (compromised) key must NOT authorise"
        assert reg.is_authorized(new_pub)
        assert old_pub in reg.revoked_pubkeys()

        assert res.revocation.old_public_key_b64 == old_pub
        assert res.revocation.revoked_allowed_ips == ("10.0.0.2/32",)
        assert res.revocation.at == 1234.0

        configs, _ = topology.generate_mesh(reg, keystore, d)
        for name, text in configs.items():
            assert old_pub not in text, f"revoked key resurfaced in {name} config"

        node_a = parse_conf(configs["node-a"])
        assert node_a["interface"]["PrivateKey"] == res.new_keypair.private_b64
        assert node_a["interface"]["Address"] == "10.0.0.22/32"

        try:
            reg.add_peer("intruder", old_pub, ["10.0.0.99/32"])
            assert False, "revoked pubkey must not re-enrol"
        except PeerError:
            pass
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_three_profiles_distinct():
    ps = [profiles.PI, profiles.CLOUD_VM, profiles.BARE_METAL]
    params = [p.as_params() for p in ps]

    for i in range(len(params)):
        for j in range(i + 1, len(params)):
            assert params[i] != params[j], f"profiles {i},{j} not distinct"

    assert len({p.mtu for p in ps}) == 3
    assert len({p.persistent_keepalive for p in ps}) == 3

    assert profiles.PI.behind_nat and not profiles.PI.advertises_endpoint
    assert not profiles.CLOUD_VM.behind_nat and profiles.CLOUD_VM.advertises_endpoint
    assert not profiles.BARE_METAL.behind_nat and profiles.BARE_METAL.advertises_endpoint
    assert profiles.get_profile("pi") is profiles.PI

_TESTS = [
    test_keypair_roundtrip_and_determinism,
    test_private_key_never_in_repr,
    test_bad_key_material_fails_closed,
    test_registry_renders_valid_conf_per_peer_no_leak,
    test_conf_file_perms_and_content_owner_priv_only_on_privkey_line,
    test_pi_profile_emits_keepalive_metal_does_not,
    test_hub_and_spoke_shape,
    test_registry_rejects_dup_name_and_dup_pubkey,
    test_wizard_runs_to_verify,
    test_wizard_idempotent,
    test_wizard_resume_matches_straight_through,
    test_recovery_rotates_key_old_no_longer_authorizes,
    test_three_profiles_distinct,
]

def main() -> int:
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
    return 1 if f else 0

if __name__ == "__main__":
    sys.exit(main())
