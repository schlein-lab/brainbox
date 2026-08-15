
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Iterable, Set, Tuple

class PeerError(Exception):
    pass

def _norm_allowed_ips(cidrs: Iterable[str]) -> Tuple[str, ...]:
    out: List[str] = []
    for c in cidrs:
        try:
            net = ipaddress.ip_network(c, strict=False)
        except Exception as e:
            raise PeerError(f"invalid allowed-ip {c!r}: {type(e).__name__}")
        out.append(str(net))

    return tuple(sorted(set(out)))

def _norm_endpoint(endpoint: Optional[str]) -> Optional[str]:
    if endpoint is None:
        return None
    if ":" not in endpoint:
        raise PeerError(f"endpoint must be host:port, got {endpoint!r}")
    host, _, port = endpoint.rpartition(":")
    if not host:
        raise PeerError(f"endpoint missing host: {endpoint!r}")
    try:
        p = int(port)
    except ValueError:
        raise PeerError(f"endpoint port not an int: {endpoint!r}")
    if not (0 < p < 65536):
        raise PeerError(f"endpoint port out of range: {endpoint!r}")
    return f"{host}:{p}"

@dataclass(frozen=True)
class Peer:
    name: str
    public_key_b64: str
    allowed_ips: Tuple[str, ...]
    endpoint: Optional[str] = None
    psk_b64: Optional[str] = None
    profile: str = "cloud-vm"
    tags: Tuple[str, ...] = ()

    def has_tag(self, tag: str) -> bool:
        return tag in self.tags

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "public_key_b64": self.public_key_b64,
            "allowed_ips": list(self.allowed_ips),
            "endpoint": self.endpoint,
            "psk_b64": self.psk_b64,
            "profile": self.profile,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Peer":
        return cls(
            name=d["name"],
            public_key_b64=d["public_key_b64"],
            allowed_ips=_norm_allowed_ips(d.get("allowed_ips", ())),
            endpoint=_norm_endpoint(d.get("endpoint")),
            psk_b64=d.get("psk_b64"),
            profile=d.get("profile", "cloud-vm"),
            tags=tuple(d.get("tags", ())),
        )

class PeerRegistry:

    def __init__(self) -> None:
        self._peers: Dict[str, Peer] = {}
        self._revoked_pubkeys: Set[str] = set()

    def __contains__(self, name: str) -> bool:
        return name in self._peers

    def __len__(self) -> int:
        return len(self._peers)

    def get(self, name: str) -> Peer:
        try:
            return self._peers[name]
        except KeyError:
            raise PeerError(f"no such peer {name!r}")

    def peers(self) -> List[Peer]:

        return [self._peers[n] for n in sorted(self._peers)]

    def by_tag(self, tag: str) -> List[Peer]:
        return [p for p in self.peers() if p.has_tag(tag)]

    def is_authorized(self, public_key_b64: str) -> bool:

        if public_key_b64 in self._revoked_pubkeys:
            return False
        return any(p.public_key_b64 == public_key_b64 for p in self._peers.values())

    def revoked_pubkeys(self) -> Set[str]:
        return set(self._revoked_pubkeys)

    def add_peer(self, name: str, public_key_b64: str, allowed_ips: Iterable[str],
                 endpoint: Optional[str] = None, psk_b64: Optional[str] = None,
                 profile: str = "cloud-vm", tags: Iterable[str] = ()) -> Peer:
        if not name:
            raise PeerError("peer name must be non-empty")
        if name in self._peers:
            raise PeerError(f"duplicate peer name {name!r}")
        if public_key_b64 in self._revoked_pubkeys:
            raise PeerError(f"public key for {name!r} is revoked; use a fresh key")
        for p in self._peers.values():
            if p.public_key_b64 == public_key_b64:
                raise PeerError(f"public key already enrolled as {p.name!r}")
        peer = Peer(
            name=name,
            public_key_b64=public_key_b64,
            allowed_ips=_norm_allowed_ips(allowed_ips),
            endpoint=_norm_endpoint(endpoint),
            psk_b64=psk_b64,
            profile=profile,
            tags=tuple(tags),
        )
        self._peers[name] = peer
        return peer

    def remove_peer(self, name: str) -> None:
        peer = self.get(name)
        self._revoked_pubkeys.add(peer.public_key_b64)
        del self._peers[name]

    def rotate_peer(self, name: str, new_public_key_b64: str,
                    new_psk_b64: Optional[str] = None) -> Peer:

        old = self.get(name)
        if new_public_key_b64 in self._revoked_pubkeys:
            raise PeerError("new public key is revoked; generate a fresh key")
        for p in self._peers.values():
            if p.name != name and p.public_key_b64 == new_public_key_b64:
                raise PeerError(f"new public key collides with peer {p.name!r}")
        self._revoked_pubkeys.add(old.public_key_b64)
        new = replace(old, public_key_b64=new_public_key_b64,
                      psk_b64=new_psk_b64 if new_psk_b64 is not None else old.psk_b64)
        self._peers[name] = new
        return new

    def set_allowed_ips(self, name: str, allowed_ips: Iterable[str]) -> Peer:
        new = replace(self.get(name), allowed_ips=_norm_allowed_ips(allowed_ips))
        self._peers[name] = new
        return new

    def set_endpoint(self, name: str, endpoint: Optional[str]) -> Peer:
        new = replace(self.get(name), endpoint=_norm_endpoint(endpoint))
        self._peers[name] = new
        return new

    def to_dict(self) -> dict:
        return {
            "peers": [p.to_dict() for p in self.peers()],
            "revoked_pubkeys": sorted(self._revoked_pubkeys),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PeerRegistry":
        reg = cls()
        for pd in d.get("peers", []):
            p = Peer.from_dict(pd)
            reg._peers[p.name] = p
        reg._revoked_pubkeys = set(d.get("revoked_pubkeys", []))
        return reg

__all__ = ["Peer", "PeerRegistry", "PeerError"]
