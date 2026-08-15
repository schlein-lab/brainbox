
from __future__ import annotations
import os

STATE_DIR = os.path.join(os.environ.get("XDG_DATA_HOME",
                         os.path.expanduser("~/.local/share")), "portioneer")
RELAY_DIR = os.path.join(STATE_DIR, "relay")
RELAY_DB = os.path.join(RELAY_DIR, "relay.db")
KEYS_DIR = os.path.join(RELAY_DIR, "keys")

VERSION = "0.2.0-relay-hardened"

ID_METHOD = "device-channel"

DEFAULT_BROKER_UID = 4003

def relay_broker_uid() -> int:
    try:
        return int(os.environ.get("RELAY_BROKER_UID", DEFAULT_BROKER_UID))
    except ValueError:
        return DEFAULT_BROKER_UID

FORBIDDEN_BROKER_CAPS = ("task.raw", "task_type:*", "view:all")

def broker_principal_caps(uid: int, *, db_path: str | None = None):

    import sqlite3
    try:
        from pnlib import db, DB_PATH
        path = db_path or DB_PATH

        last = None
        for uri in (f"file:{path}?mode=ro", f"file:{path}?immutable=1"):
            try:
                cx = sqlite3.connect(uri, uri=True, timeout=5)
                cx.row_factory = sqlite3.Row
                try:
                    principal = db.principal_for_uid(cx, uid)
                    caps = db.caps_for(cx, principal) if principal else set()
                    return principal, set(caps)
                finally:
                    cx.close()
            except sqlite3.OperationalError as e:
                last = e
                continue
        if last is not None:
            raise last
        return None, set()
    except Exception:
        return None, set()

def broker_is_deprivileged(uid: int, *, reasons: list | None = None, db_path: str | None = None) -> bool:

    reasons = reasons if reasons is not None else []
    principal, caps = broker_principal_caps(uid, db_path=db_path)
    if principal is None:
        reasons.append(f"broker uid {uid} resolves to NO principal in the engine registry")
        return False
    if "act-as" not in caps:
        reasons.append(f"broker principal {principal!r} (uid {uid}) lacks the `act-as` broker cap")
    bad = [c for c in FORBIDDEN_BROKER_CAPS if c in caps]
    if bad:
        reasons.append(f"broker principal {principal!r} (uid {uid}) holds forbidden cap(s) {bad} — "
                       "a de-privileged relay broker must NOT hold task.raw/task_type:*/view:all")
    return "act-as" in caps and not bad

def enabled() -> bool:

    return os.environ.get("RELAY_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")

def wss_transport_available() -> bool:

    try:
        import websockets
        return True
    except Exception:
        return False

def is_production_ready(reasons: list | None = None) -> bool:

    reasons = reasons if reasons is not None else []
    ok = True
    if not enabled():
        reasons.append("RELAY_ENABLED is not set (master off-by-default gate)"); ok = False
    url = os.environ.get("RELAY_URL", "")
    if not url:
        reasons.append("RELAY_URL is unset (no rendezvous configured)"); ok = False
    elif url.startswith("mock://"):
        reasons.append("RELAY_URL is a mock:// rendezvous (tests only, not production)"); ok = False
    elif not url.startswith("wss://"):
        reasons.append("RELAY_URL must be wss:// for a production rendezvous"); ok = False
    if not wss_transport_available():
        reasons.append("the `websockets` dependency is not installed (production WSS transport "
                       "unavailable; pip install websockets)"); ok = False
    if os.environ.get("RELAY_ACK_BLAST_RADIUS", "0").strip() not in ("1", "true", "yes"):
        reasons.append("operator has not acknowledged the revocation/ceilings blast-radius "
                       "controls (set RELAY_ACK_BLAST_RADIUS=1 after reading the runbook)")
        ok = False
    return ok
