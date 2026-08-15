
from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from pnlib.overlay.keys import WGKeypair, generate_keypair, keypair_from_private_b64
from pnlib.overlay.peer import PeerRegistry, PeerError
from pnlib.overlay import topology as _topology
from pnlib.overlay import profiles as _profiles

DISCOVER = "discover"
NAME_BOX = "name-box"
GENERATE_KEYS = "generate-keys"
ENROLL_PEER = "enroll-peer"
EMIT_CONFIG = "emit-config"
VERIFY = "verify"
DONE = "done"

ORDER = [DISCOVER, NAME_BOX, GENERATE_KEYS, ENROLL_PEER, EMIT_CONFIG, VERIFY, DONE]

class WizardError(Exception):
    pass

def default_discoverer() -> dict:

    import socket
    return {"hostname": socket.gethostname(), "profile": "cloud-vm"}

@dataclass
class WizardConfig:

    box_name: Optional[str] = None
    profile: Optional[str] = None
    allowed_ips: List[str] = field(default_factory=list)
    endpoint: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    known_peers: List[dict] = field(default_factory=list)
    out_dir: str = ""

class OnboardingWizard:
    def __init__(self, config: WizardConfig,
                 rand: Callable[[int], bytes] = os.urandom,
                 discoverer: Callable[[], dict] = default_discoverer):
        self.config = config
        self._rand = rand
        self._discoverer = discoverer
        self.state: str = DISCOVER
        self.discovery: dict = {}
        self.box_name: Optional[str] = config.box_name
        self.profile: Optional[str] = config.profile
        self.keypair: Optional[WGKeypair] = None
        self.registry = PeerRegistry()
        self.emitted: Dict[str, str] = {}
        self.emitted_paths: List[str] = []
        self.verified: bool = False

    def _do_discover(self) -> None:
        if not self.discovery:
            self.discovery = dict(self._discoverer())

    def _do_name_box(self) -> None:
        if not self.box_name:
            self.box_name = self.config.box_name or self.discovery.get("hostname")
        if not self.profile:
            self.profile = self.config.profile or self.discovery.get("profile") or "cloud-vm"
        if not self.box_name:
            raise WizardError("cannot determine a box name (no config, no discovery hostname)")

        _profiles.get_profile(self.profile)

    def _do_generate_keys(self) -> None:
        if self.keypair is None:
            self.keypair = generate_keypair(self._rand)

    def _do_enroll_peer(self) -> None:

        if self.box_name not in self.registry:
            self.registry.add_peer(
                name=self.box_name,
                public_key_b64=self.keypair.public_b64,
                allowed_ips=self.config.allowed_ips,
                endpoint=self.config.endpoint,
                profile=self.profile,
                tags=self.config.tags,
            )

        for pd in self.config.known_peers:
            name = pd["name"]
            if name in self.registry:
                continue
            self.registry.add_peer(
                name=name,
                public_key_b64=pd["public_key_b64"],
                allowed_ips=pd.get("allowed_ips", []),
                endpoint=pd.get("endpoint"),
                psk_b64=pd.get("psk_b64"),
                profile=pd.get("profile", "cloud-vm"),
                tags=pd.get("tags", ()),
            )

    def _do_emit_config(self) -> None:
        if not self.config.out_dir:
            raise WizardError("emit-config needs config.out_dir")
        keystore = {self.box_name: self.keypair}
        configs, paths = _topology.generate_mesh(self.registry, keystore, self.config.out_dir)
        self.emitted = configs
        self.emitted_paths = paths

    def _do_verify(self) -> None:
        if self.keypair is None or not self.keypair.verify_consistent():
            raise WizardError("keypair failed consistency check")
        own_path = os.path.join(self.config.out_dir, f"{self.box_name}.conf")
        if not os.path.exists(own_path):
            raise WizardError("own config was not emitted")
        with open(own_path, encoding="utf-8") as f:
            text = f.read()

        priv_line = next((ln.strip() for ln in text.splitlines()
                          if ln.strip().startswith("PrivateKey")), None)
        if priv_line is None:
            raise WizardError("own config has no PrivateKey line")
        emitted_priv = priv_line.split("=", 1)[1].strip()
        if emitted_priv != self.keypair.private_b64:
            raise WizardError("emitted private key does not match the box keypair")
        from pnlib.overlay.keys import unb64, derive_public, b64
        if b64(derive_public(unb64(emitted_priv))) != self.keypair.public_b64:
            raise WizardError("emitted private key does not derive the box public key")

        for line in text.splitlines():
            s = line.strip()
            if self.keypair.private_b64 in s and not s.startswith("PrivateKey"):
                raise WizardError("private key leaked onto a non-PrivateKey line")
        self.verified = True

    _HANDLERS = {
        DISCOVER: "_do_discover",
        NAME_BOX: "_do_name_box",
        GENERATE_KEYS: "_do_generate_keys",
        ENROLL_PEER: "_do_enroll_peer",
        EMIT_CONFIG: "_do_emit_config",
        VERIFY: "_do_verify",
    }

    def advance(self) -> str:

        if self.state == DONE:
            return DONE
        handler = getattr(self, self._HANDLERS[self.state])
        handler()
        self.state = ORDER[ORDER.index(self.state) + 1]
        return self.state

    def run(self, until: str = DONE) -> str:

        if until not in ORDER:
            raise WizardError(f"unknown target state {until!r}")
        target = ORDER.index(until)
        guard = 0
        while ORDER.index(self.state) < target:
            self.advance()
            guard += 1
            if guard > len(ORDER) + 1:
                raise WizardError("wizard failed to converge")
        return self.state

    def to_state_dict(self) -> dict:
        return {
            "state": self.state,
            "discovery": self.discovery,
            "box_name": self.box_name,
            "profile": self.profile,
            "private_key_b64": self.keypair.private_b64 if self.keypair else None,
            "registry": self.registry.to_dict(),
            "emitted_paths": self.emitted_paths,
            "verified": self.verified,
            "config": {
                "box_name": self.config.box_name,
                "profile": self.config.profile,
                "allowed_ips": self.config.allowed_ips,
                "endpoint": self.config.endpoint,
                "tags": self.config.tags,
                "known_peers": self.config.known_peers,
                "out_dir": self.config.out_dir,
            },
        }

    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        try:
            os.unlink(tmp)
        except OSError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.to_state_dict(), f, indent=2, sort_keys=True)
        if os.stat(tmp).st_mode & 0o077:
            os.unlink(tmp)
            raise RuntimeError("cannot secure %r to 0600 (filesystem has no perms?)" % path)
        os.replace(tmp, path)

    @classmethod
    def resume(cls, path: str, rand: Callable[[int], bytes] = os.urandom,
               discoverer: Callable[[], dict] = default_discoverer) -> "OnboardingWizard":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        cfg = WizardConfig(**d["config"])
        w = cls(cfg, rand=rand, discoverer=discoverer)
        w.state = d["state"]
        w.discovery = d.get("discovery", {})
        w.box_name = d.get("box_name")
        w.profile = d.get("profile")
        if d.get("private_key_b64"):
            w.keypair = keypair_from_private_b64(d["private_key_b64"])
        w.registry = PeerRegistry.from_dict(d.get("registry", {}))
        w.emitted_paths = d.get("emitted_paths", [])
        w.verified = d.get("verified", False)
        return w

__all__ = [
    "OnboardingWizard", "WizardConfig", "WizardError", "default_discoverer",
    "DISCOVER", "NAME_BOX", "GENERATE_KEYS", "ENROLL_PEER", "EMIT_CONFIG", "VERIFY", "DONE", "ORDER",
]
