
from __future__ import annotations
import base64, hashlib, hmac, os, time

def media_enabled() -> bool:

    return os.environ.get("MEDIA_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")

def loopback_ice_servers() -> list:

    return []

def turn_credentials(secret: bytes, principal: str, ttl: int = 3600) -> tuple[str, str]:

    expiry = int(time.time()) + int(ttl)
    username = f"{expiry}:{principal}"
    digest = hmac.new(secret, username.encode(), hashlib.sha1).digest()
    return username, base64.b64encode(digest).decode()

def production_ice_servers(principal: str, *, reasons: list | None = None) -> list:

    reasons = reasons if reasons is not None else []
    if not media_enabled():
        reasons.append("MEDIA_ENABLED is not set (media is off-by-default)")
        return loopback_ice_servers()

    servers: list = []
    stun_url = os.environ.get("MEDIA_STUN_URL", "").strip()
    if stun_url:
        servers.append({"urls": [stun_url]})

    turn_url = os.environ.get("MEDIA_TURN_URL", "").strip()
    secret_file = os.environ.get("MEDIA_TURN_SECRET_FILE", "").strip()
    if not turn_url:
        reasons.append("MEDIA_TURN_URL is unset (no TURN relay configured) — NAT traversal "
                       "across the open internet will fail without it; a live deploy NEEDS a "
                       "real TURN server")
        return servers
    if not secret_file or not os.path.exists(secret_file):
        reasons.append("MEDIA_TURN_SECRET_FILE missing — refusing to advertise TURN without "
                       "a shared secret (fail-closed)")
        return servers
    try:
        secret = open(secret_file, "rb").read().strip()
    except OSError as e:
        reasons.append(f"could not read MEDIA_TURN_SECRET_FILE: {e}")
        return servers

    ttl = int(os.environ.get("MEDIA_TURN_TTL", "3600"))
    username, credential = turn_credentials(secret, principal, ttl)
    urls = [u.strip() for u in turn_url.split(",") if u.strip()]
    servers.append({"urls": urls, "username": username, "credential": credential})
    return servers

def is_loopback_only(ice_servers: list) -> bool:

    return not ice_servers
