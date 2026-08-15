

from . import STOP_STATE, TERMINAL_STATES, now, table_columns
from .tree import build_tree

_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS jobtree_stops (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id      INTEGER NOT NULL,   -- the node that was stopped
  root_id     INTEGER NOT NULL,   -- the node the cascade started from
  from_state  TEXT,               -- state it held before the stop
  to_state    TEXT NOT NULL,      -- the terminal stop state it was driven to
  reason      TEXT,               -- human/why string
  by          TEXT,               -- principal / actor that issued the stop
  ts          REAL NOT NULL
)
"""

def _ensure_audit(cx):
    cx.execute(_AUDIT_DDL)

def cascade_stop(cx, job_id, reason, table: str = "jobs",
                 stop_state: str = STOP_STATE, by: str = None,
                 ts: float = None, commit: bool = True) -> dict:

    ts = now() if ts is None else ts
    _ensure_audit(cx)
    cols = table_columns(cx, table)
    have_stop_reason = "stop_reason" in cols
    have_finished_at = "finished_at" in cols

    tree = build_tree(cx, table)
    stopped, already = [], []

    for jid in tree.subtree_ids(job_id):
        node = tree.get(jid)
        if node is None:
            continue
        state = node.state
        if state in TERMINAL_STATES:
            already.append(jid)
            continue

        sets = ["state=?"]
        args = [stop_state]
        if have_stop_reason:
            sets.append("stop_reason=?")
            args.append(reason)
        if have_finished_at:
            sets.append("finished_at=?")
            args.append(ts)
        args.append(jid)
        cx.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id=?", args)
        cx.execute(
            "INSERT INTO jobtree_stops(job_id,root_id,from_state,to_state,reason,by,ts)"
            " VALUES(?,?,?,?,?,?,?)",
            (jid, job_id, state, stop_state, reason, by, ts),
        )
        stopped.append(jid)

    if commit:
        cx.commit()
    return {
        "root": job_id,
        "stopped": stopped,
        "already_terminal": already,
        "reason": reason,
    }
