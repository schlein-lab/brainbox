
from __future__ import annotations

VERSION = "0.1.0-connect"

CONTRACT_VERBS = frozenset(
    {"submit", "subscribe", "cvm", "replay", "approve", "deny", "steer", "cancel", "job", "ping"}
)

def user_topic(principal: str) -> str:
    return f"user/{principal}"
