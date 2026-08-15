
from __future__ import annotations
import os
import json
import hashlib

from relaylib import crypto

class OwnerKeyError(Exception):
    pass

class SovereignKeyError(Exception):
    pass

OWNER_PUBKEY_CONFIG_KEY = "owner_pubkey"

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/brainbox-portal/config.json")
ENV_CONFIG_PATH = "PN_PORTAL_CONFIG"
ENV_OWNER_PUBKEY = "PN_OWNER_PUBKEY"

_PUBKEY_LEN = 32

DOMAIN_MINT = b"brainarbeit/owner/mint/1"
DOMAIN_STH = b"brainarbeit/owner/sth/1"
DOMAIN_POLICY = b"brainarbeit/owner/policy/1"

def config_path(path: str | None = None) -> str:

    return path or os.environ.get(ENV_CONFIG_PATH) or DEFAULT_CONFIG_PATH

def _parse_hex_pubkey(value, *, source: str) -> bytes:

    if not isinstance(value, str):
        raise OwnerKeyError(f"owner pubkey from {source} is not a hex string (got {type(value).__name__})")
    s = value.strip()
    if not s:
        raise OwnerKeyError(f"owner pubkey from {source} is empty")
    try:
        raw = bytes.fromhex(s)
    except ValueError:
        raise OwnerKeyError(f"owner pubkey from {source} is not valid hex")
    if len(raw) != _PUBKEY_LEN:
        raise OwnerKeyError(
            f"owner pubkey from {source} has the wrong length "
            f"(need {_PUBKEY_LEN} bytes, got {len(raw)})")
    return raw

def _read_config(path: str) -> dict:

    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}

def load_owner_pubkey(path: str | None = None, *, config: dict | None = None) -> bytes:

    env = os.environ.get(ENV_OWNER_PUBKEY)
    if env is not None:
        return _parse_hex_pubkey(env, source=f"env:{ENV_OWNER_PUBKEY}")

    if config is not None:
        if OWNER_PUBKEY_CONFIG_KEY not in config:
            raise OwnerKeyError(f"no '{OWNER_PUBKEY_CONFIG_KEY}' in the supplied config")
        return _parse_hex_pubkey(config.get(OWNER_PUBKEY_CONFIG_KEY),
                                 source="config(dict)")

    p = config_path(path)
    cfg = _read_config(p)
    if OWNER_PUBKEY_CONFIG_KEY not in cfg:
        raise OwnerKeyError(
            f"owner public key is not pinned: no '{OWNER_PUBKEY_CONFIG_KEY}' in {p} "
            f"(pin it out-of-band via the owner's tooling)")
    return _parse_hex_pubkey(cfg.get(OWNER_PUBKEY_CONFIG_KEY), source=p)

def is_pinned(path: str | None = None, *, config: dict | None = None) -> bool:

    try:
        load_owner_pubkey(path, config=config)
        return True
    except OwnerKeyError:
        return False

def domain_bind(domain: bytes, msg: bytes) -> bytes:

    if not isinstance(domain, (bytes, bytearray)):
        raise OwnerKeyError("domain must be bytes")
    if not isinstance(msg, (bytes, bytearray)):
        raise OwnerKeyError("msg must be bytes")
    d = bytes(domain)
    return len(d).to_bytes(2, "big") + d + bytes(msg)

def verify(sig: bytes, msg: bytes, *, pubkey: bytes | None = None,
           domain: bytes | None = None, path: str | None = None,
           config: dict | None = None) -> bool:

    key = pubkey if pubkey is not None else load_owner_pubkey(path, config=config)
    if not isinstance(key, (bytes, bytearray)) or len(key) != _PUBKEY_LEN:
        raise OwnerKeyError("owner pubkey has the wrong length for verification")
    if domain is not None:
        msg = domain_bind(domain, msg)
    if not isinstance(sig, (bytes, bytearray)):
        return False
    return crypto.ed_verify(bytes(key), bytes(sig), bytes(msg))

def require_owner_sig(sig: bytes, msg: bytes, **kw) -> bytes:

    pubkey = kw.get("pubkey")
    if pubkey is None:
        pubkey = load_owner_pubkey(kw.get("path"), config=kw.get("config"))
        kw = dict(kw)
        kw["pubkey"] = pubkey
    if not verify(sig, msg, **kw):
        raise OwnerKeyError("owner signature verification FAILED (unauthorised or tampered)")
    return bytes(pubkey)

def owner_fingerprint(pubkey: bytes | None = None, *, path: str | None = None,
                      config: dict | None = None) -> str:

    key = pubkey if pubkey is not None else load_owner_pubkey(path, config=config)
    return "owner:b2:" + hashlib.blake2s(bytes(key), digest_size=16).hexdigest()

def pin_owner_pubkey(pub_raw: bytes, path: str | None = None) -> str:

    if not isinstance(pub_raw, (bytes, bytearray)) or len(pub_raw) != _PUBKEY_LEN:
        raise OwnerKeyError(f"owner pubkey must be exactly {_PUBKEY_LEN} raw bytes")
    p = config_path(path)
    cfg = _read_config(p)
    cfg[OWNER_PUBKEY_CONFIG_KEY] = bytes(pub_raw).hex()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = p + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(fd, "w") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
    os.replace(tmp, p)
    return p

def generate_owner_keypair_offbox():

    return crypto.gen_ed25519()

def sign_on_box(*args, **kwargs):

    raise SovereignKeyError(
        "refusing to sign as the owner on the box: the owner private key is sovereign and lives "
        "only off-box (client-side / out-of-band). On-box owner signing is categorically forbidden.")
