
from __future__ import annotations
import os

def _b(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")

class Config:

    PND_SOCK = os.environ.get("PN_SOCK") or os.path.join(
        os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "pnd.sock")

    RELAY_DB = os.environ.get("RELAY_DB") or os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "portioneer", "relay", "relay.db")

    HOST = os.environ.get("GW_HOST", "127.0.0.1")
    PORT = int(os.environ.get("GW_PORT", "8810"))
    CERT = os.environ.get("GW_TLS_CERT")
    KEY = os.environ.get("GW_TLS_KEY")

    REQUIRE_2FA = _b("GW_REQUIRE_2FA", True)
    ALLOW_NO_2FA = _b("GW_ALLOW_NO_2FA", False)

    BROKER_UID = int(os.environ.get("GW_BROKER_UID", "4004"))
    ID_METHOD = "device-channel"

    DEV_SECRET = "brainarbeit-gateway-dev-secret"
    SECRET = os.environ.get("GW_SECRET")

    ALLOW_RAW_CMD = False

    MAX_ATTACH_BYTES = int(os.environ.get("GW_MAX_ATTACH_BYTES", str(64 * 1024 * 1024)))
    DROPZONE = os.environ.get("GW_DROPZONE") or os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "portioneer", "gateway-intake")

    WEBHOOK_ALLOW_HOSTS = [h for h in os.environ.get("GW_WEBHOOK_HOSTS", "").split(",") if h.strip()]

    SCREEN_WS = os.environ.get("GW_SCREEN_WS", "ws://127.0.0.1:8800/ws/screen")

    WEBRTC_SIGNAL = os.environ.get("GW_WEBRTC_SIGNAL")

    VERSION = "1.0.0"
    API_PREFIX = "/v1"

_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")

def _is_loopback(cfg) -> bool:
    return cfg.HOST in _LOOPBACK_HOSTS

def _secret_is_unsafe(cfg) -> bool:

    s = cfg.SECRET
    return (not s) or (not s.strip()) or (s == cfg.DEV_SECRET)

def assert_safe_to_start(cfg=Config):

    if not cfg.REQUIRE_2FA and not cfg.ALLOW_NO_2FA:
        raise SystemExit("REFUSING TO START: GW_REQUIRE_2FA is off but GW_ALLOW_NO_2FA is not set. "
                         "Mandatory 2FA is the operator requirement (portioneer PR #14).")
    if not _is_loopback(cfg) and not (cfg.CERT and cfg.KEY):
        raise SystemExit(f"REFUSING TO START: binding {cfg.HOST} off-loopback requires TLS "
                         "(GW_TLS_CERT + GW_TLS_KEY), or terminate TLS at a trusted edge and bind loopback.")

    if not _is_loopback(cfg) and _secret_is_unsafe(cfg):
        raise SystemExit(f"REFUSING TO START: binding {cfg.HOST} off-loopback requires an explicit "
                         "high-entropy GW_SECRET (the publicly-known dev default would let an attacker "
                         "forge media tickets + webhook signing secrets and bypass mandatory 2FA). "
                         "Set GW_SECRET to a long random value (e.g. `python3 -c \"import secrets;"
                         "print(secrets.token_urlsafe(48))\"`).")

    if _secret_is_unsafe(cfg):
        import sys
        print("WARNING: GW_SECRET is unset or the publicly-known dev default — OK for loopback "
              "dev/test only. Set a high-entropy GW_SECRET before any off-loopback exposure.",
              file=sys.stderr)
