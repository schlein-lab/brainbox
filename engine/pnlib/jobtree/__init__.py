

import time

TERMINAL_STATES = frozenset({
    "done", "failed", "cancelled", "timeout", "rejected",
})

ACTIVE_STATES = frozenset({
    "queued", "blocked", "running", "staged", "awaiting_approval",
})

STOP_STATE = "cancelled"

def is_terminal(state) -> bool:
    return state in TERMINAL_STATES

def now() -> float:
    return time.time()

def table_columns(cx, table: str) -> set:

    return {r[1] for r in cx.execute(f"PRAGMA table_info({table})").fetchall()}
