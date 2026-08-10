

from . import ACTIVE_STATES, now, table_columns

def _truthy(v) -> bool:

    if v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "t", "y")
    return bool(v)

def reconcile(cx, live_scopes, table: str = "jobs", ts: float = None,
              commit: bool = True) -> dict:

    ts = now() if ts is None else ts
    live = set(live_scopes or ())
    cols = table_columns(cx, table)
    have_checkpoint = "checkpoint" in cols
    have_resume_from = "resume_from" in cols
    have_resumable = "resumable" in cols
    have_idempotent = "idempotent" in cols
    have_attempts = "attempts" in cols
    have_started = "started_at" in cols
    have_finished = "finished_at" in cols
    have_stop_reason = "stop_reason" in cols

    out = {"survived": [], "resumed": [], "requeued": [], "failed": []}

    rows = cx.execute(f"SELECT * FROM {table} WHERE state='running'").fetchall()
    for row in rows:
        jid = row["id"]
        scope = row["scope_unit"] if "scope_unit" in row.keys() else None

        if scope is not None and scope in live:
            out["survived"].append(jid)
            continue

        checkpoint = row["checkpoint"] if have_checkpoint else None
        resumable = (_truthy(row["resumable"]) if have_resumable else False) \
            or (checkpoint is not None)
        idempotent = _truthy(row["idempotent"]) if have_idempotent else False

        if resumable and checkpoint is not None:

            sets = ["state='queued'", "scope_unit=NULL"]
            args = []
            if have_started:
                sets.append("started_at=NULL")
            if have_resume_from:
                sets.append("resume_from=?")
                args.append(checkpoint)

            args.append(jid)
            cx.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id=?", args)
            out["resumed"].append(jid)

        elif idempotent:

            sets = ["state='queued'", "scope_unit=NULL"]
            args = []
            if have_started:
                sets.append("started_at=NULL")
            if have_attempts:
                sets.append("attempts=COALESCE(attempts,0)+1")
            if have_checkpoint:
                sets.append("checkpoint=NULL")
            if have_resume_from:
                sets.append("resume_from=NULL")
            args.append(jid)
            cx.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id=?", args)
            out["requeued"].append(jid)

        else:

            sets = ["state='failed'"]
            args = []
            if have_finished:
                sets.append("finished_at=?")
                args.append(ts)
            if have_stop_reason:
                sets.append("stop_reason=?")
                args.append("crash-orphaned: non-resumable, non-idempotent")
            args.append(jid)
            cx.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id=?", args)
            out["failed"].append(jid)

    if commit:
        cx.commit()
    return out
