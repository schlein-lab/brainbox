
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from pnlib.overlay.peer import Peer, PeerRegistry, PeerError
from pnlib.overlay.keys import WGKeypair
from pnlib.overlay import profiles as _profiles

def _write_private(path: str, text: str) -> None:

    try:
        os.unlink(path)
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if os.stat(path).st_mode & 0o077:
            raise TopologyError("cannot secure %r to 0600 (filesystem has no perms?)" % path)
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise

class TopologyError(Exception):
    pass

def _profile_for(peer: Peer) -> _profiles.DeployProfile:
    try:
        return _profiles.get_profile(peer.profile)
    except KeyError as e:
        raise TopologyError(str(e))

def _render_interface(peer: Peer, kp: WGKeypair) -> str:
    if kp.public_b64 != peer.public_key_b64:
        raise TopologyError(
            f"keystore keypair for {peer.name!r} does not match its registered public key")
    prof = _profile_for(peer)
    lines = ["[Interface]", f"# {peer.name} ({prof.name})",
             f"PrivateKey = {kp.private_b64}"]
    if peer.allowed_ips:
        lines.append(f"Address = {', '.join(peer.allowed_ips)}")
    lines.append(f"ListenPort = {prof.listen_port}")
    lines.append(f"MTU = {prof.mtu}")
    return "\n".join(lines)

def _render_peer(local: Peer, neighbour: Peer) -> str:

    local_prof = _profile_for(local)
    nb_prof = _profile_for(neighbour)
    lines = ["[Peer]", f"# {neighbour.name} ({nb_prof.name})",
             f"PublicKey = {neighbour.public_key_b64}"]
    if neighbour.psk_b64:
        lines.append(f"PresharedKey = {neighbour.psk_b64}")
    if neighbour.allowed_ips:
        lines.append(f"AllowedIPs = {', '.join(neighbour.allowed_ips)}")

    if neighbour.endpoint and nb_prof.advertises_endpoint:
        lines.append(f"Endpoint = {neighbour.endpoint}")

    if local_prof.persistent_keepalive > 0:
        lines.append(f"PersistentKeepalive = {local_prof.persistent_keepalive}")
    return "\n".join(lines)

def _neighbours(local: Peer, all_peers: List[Peer], hub_name: Optional[str]) -> List[Peer]:
    others = [p for p in all_peers if p.name != local.name]
    if hub_name is None:
        return others

    if local.name == hub_name:
        return others
    return [p for p in others if p.name == hub_name]

def _render_config(local: Peer, kp: WGKeypair, neighbours: List[Peer]) -> str:
    blocks = [_render_interface(local, kp)]
    for nb in neighbours:
        blocks.append(_render_peer(local, nb))
    return "\n\n".join(blocks) + "\n"

def assert_no_foreign_priv(config_text: str, keystore: Dict[str, WGKeypair],
                           owner_name: str) -> None:

    owner_priv = keystore[owner_name].private_b64
    for name, kp in keystore.items():
        if name == owner_name:
            continue
        if kp.private_b64 in config_text:
            raise TopologyError(f"foreign private key for {name!r} leaked into {owner_name!r} config")

    for line in config_text.splitlines():
        s = line.strip()
        if s.startswith("PublicKey") or s.startswith("PresharedKey") or s.startswith("AllowedIPs") \
                or s.startswith("Endpoint"):
            if owner_priv in s:
                raise TopologyError(f"owner private key leaked onto a peer line in {owner_name!r} config")

def _generate(registry: PeerRegistry, keystore: Dict[str, WGKeypair],
              out_dir: str, hub_name: Optional[str]) -> Tuple[Dict[str, str], List[str]]:
    all_peers = registry.peers()
    if hub_name is not None and hub_name not in registry:
        raise TopologyError(f"hub {hub_name!r} is not in the registry")
    os.makedirs(out_dir, exist_ok=True)
    configs: Dict[str, str] = {}
    paths: List[str] = []
    for local in all_peers:
        kp = keystore.get(local.name)
        if kp is None:
            continue
        neighbours = _neighbours(local, all_peers, hub_name)
        text = _render_config(local, kp, neighbours)
        assert_no_foreign_priv(text, keystore, local.name)
        configs[local.name] = text
        path = os.path.join(out_dir, f"{local.name}.conf")
        _write_private(path, text)
        paths.append(path)
    return configs, sorted(paths)

def generate_hub_and_spoke(registry: PeerRegistry, keystore: Dict[str, WGKeypair],
                           hub_name: str, out_dir: str) -> Tuple[Dict[str, str], List[str]]:
    return _generate(registry, keystore, out_dir, hub_name=hub_name)

def generate_mesh(registry: PeerRegistry, keystore: Dict[str, WGKeypair],
                  out_dir: str) -> Tuple[Dict[str, str], List[str]]:
    return _generate(registry, keystore, out_dir, hub_name=None)

__all__ = [
    "generate_hub_and_spoke", "generate_mesh", "assert_no_foreign_priv", "TopologyError",
]
