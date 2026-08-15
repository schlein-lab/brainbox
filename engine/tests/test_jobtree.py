#!/usr/bin/env python3

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pnlib.jobtree import tree as jt_tree
from pnlib.jobtree import cascade as jt_cascade
from pnlib.jobtree import durable as jt_durable

PASS = 0
FAIL = 0

def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   - {msg}")
    else:
        FAIL += 1
        print(f"  FAIL - {msg}")

_DDL = """
CREATE TABLE jobs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  cmd           TEXT,
  state         TEXT NOT NULL DEFAULT 'queued',
  parent_job    INTEGER,
  scope_unit    TEXT,
  attempts      INTEGER NOT NULL DEFAULT 0,
  idempotent    INTEGER NOT NULL DEFAULT 0,
  resumable     INTEGER NOT NULL DEFAULT 0,
  checkpoint    TEXT,
  resume_from   TEXT,
  stop_reason   TEXT,
  started_at    REAL,
  finished_at   REAL,
  submitted_at  REAL
);
"""

def fresh_db():
    fd, path = tempfile.mkstemp(prefix="jobtree_test_", suffix=".db")
    os.close(fd)
    cx = sqlite3.connect(path)
    cx.row_factory = sqlite3.Row
    cx.executescript(_DDL)
    cx.commit()
    return cx, path

def add(cx, jid, state, parent=None, scope=None, **kw):
    cols = ["id", "state", "parent_job", "scope_unit"]
    vals = [jid, state, parent, scope]
    for k, v in kw.items():
        cols.append(k)
        vals.append(v)
    q = f"INSERT INTO jobs({','.join(cols)}) VALUES({','.join('?' * len(vals))})"
    cx.execute(q, vals)
    cx.commit()

def state_of(cx, jid):
    return cx.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()["state"]

def row_of(cx, jid):
    return cx.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()

def test_tree_shape():
    print("[tree] build_tree — parent->children shape, descendants, ancestors")
    cx, path = fresh_db()
    try:

        add(cx, 1, "running")
        add(cx, 2, "running", parent=1)
        add(cx, 3, "queued", parent=1)
        add(cx, 4, "done", parent=2)
        add(cx, 5, "running", parent=2)
        add(cx, 7, "blocked", parent=5)
        add(cx, 6, "running")
        add(cx, 8, "running", parent=6)

        t = jt_tree.build_tree(cx)
        check(sorted(n.id for n in t.roots) == [1, 6], "roots are the parent-less jobs (1,6)")
        check(t.children_ids(2) == [4, 5], "direct children of 2 are [4,5] in id order")
        check(t.subtree_ids(2) == [2, 4, 5, 7], "subtree of 2 is depth-first [2,4,5,7]")
        check(sorted(t.descendants(1)) == [2, 3, 4, 5, 7], "all descendants of root 1")
        check(t.descendants(8) == [], "leaf 8 has no descendants")
        check(t.ancestors(7) == [5, 2, 1], "ancestors of 7 up to root are [5,2,1]")
        check(t.root_of(7) == 1, "root_of(7) is 1")
    finally:
        cx.close()
        os.unlink(path)

def test_tree_cycle_safe():
    print("[tree] corrupt self/loop parent does not hang the walk")
    cx, path = fresh_db()
    try:

        add(cx, 10, "running", parent=12)
        add(cx, 11, "running", parent=10)
        add(cx, 12, "running", parent=11)
        add(cx, 20, "running", parent=20)
        t = jt_tree.build_tree(cx)

        sub = t.subtree_ids(10)
        check(set(sub) <= {10, 11, 12} and sub[0] == 10, "cyclic subtree walk is finite")
        check(t.subtree_ids(20) == [20], "self-parent job is its own finite subtree")
    finally:
        cx.close()
        os.unlink(path)

def test_cascade_multilevel_and_scope():
    print("[cascade] kill a mid node -> whole subtree stopped; siblings/parent/other root untouched")
    cx, path = fresh_db()
    try:

        add(cx, 1, "running")
        add(cx, 2, "running", parent=1)
        add(cx, 3, "queued", parent=1)
        add(cx, 4, "done", parent=2)
        add(cx, 5, "running", parent=2)
        add(cx, 7, "blocked", parent=5)
        add(cx, 6, "running")
        add(cx, 8, "running", parent=6)

        res = jt_cascade.cascade_stop(cx, 2, reason="operator killed goal-2", by="admin")

        check(res["stopped"] == [2, 5, 7], "stopped = depth-first active subtree [2,5,7]")
        check(res["already_terminal"] == [4], "terminal descendant 4 recorded already_terminal")

        check(state_of(cx, 2) == "cancelled", "node 2 cancelled")
        check(state_of(cx, 5) == "cancelled", "grandchild 5 cancelled")
        check(state_of(cx, 7) == "cancelled", "great-grandchild 7 cancelled (multi-level reach)")

        check(state_of(cx, 4) == "done", "terminal descendant 4 left as done (not rewritten)")

        check(row_of(cx, 7)["stop_reason"] == "operator killed goal-2", "reason stamped on stopped row")
        n_audit = cx.execute("SELECT COUNT(*) c FROM jobtree_stops").fetchone()["c"]
        check(n_audit == 3, "audit log has one row per stopped node (3)")

        check(state_of(cx, 3) == "queued", "sibling subtree (3) untouched")
        check(state_of(cx, 1) == "running", "PARENT 1 untouched (cascade only goes down)")
        check(state_of(cx, 6) == "running", "other root 6 untouched")
        check(state_of(cx, 8) == "running", "other root's child 8 untouched")
    finally:
        cx.close()
        os.unlink(path)

def test_cascade_idempotent():
    print("[cascade] re-issuing the same stop is idempotent (stops nobody new)")
    cx, path = fresh_db()
    try:
        add(cx, 1, "running")
        add(cx, 2, "running", parent=1)
        add(cx, 3, "running", parent=2)
        first = jt_cascade.cascade_stop(cx, 1, reason="stop")
        check(first["stopped"] == [1, 2, 3], "first cascade stops the whole tree")
        second = jt_cascade.cascade_stop(cx, 1, reason="stop-again")
        check(second["stopped"] == [], "second cascade stops nobody (idempotent)")
        check(sorted(second["already_terminal"]) == [1, 2, 3], "all now already_terminal")
        n_audit = cx.execute("SELECT COUNT(*) c FROM jobtree_stops").fetchone()["c"]
        check(n_audit == 3, "no duplicate audit rows from the idempotent re-run")
    finally:
        cx.close()
        os.unlink(path)

def test_durable_reconcile():
    print("[durable] restart reconcile — survive / requeue / RESUME-from-checkpoint / fail")
    cx, path = fresh_db()
    try:

        add(cx, 1, "running", scope="scope-live",  idempotent=1)
        add(cx, 2, "running", scope="scope-gone-B", idempotent=1, attempts=0)
        add(cx, 3, "running", scope="scope-gone-C", resumable=1,
            checkpoint='{"rows_done":42}', attempts=2)
        add(cx, 4, "running", scope="scope-gone-D", idempotent=0)
        add(cx, 9, "done")

        summary = jt_durable.reconcile(cx, live_scopes={"scope-live"})

        check(summary["survived"] == [1], "row on a live scope survives (stays running)")
        check(state_of(cx, 1) == "running", "survivor 1 still running")

        check(summary["requeued"] == [2], "orphan w/o checkpoint is re-queued fresh")
        r2 = row_of(cx, 2)
        check(r2["state"] == "queued", "requeued orphan 2 back to queued")
        check(r2["attempts"] == 1, "fresh re-queue bumped attempts 0->1")
        check(r2["scope_unit"] is None, "requeued orphan cleared its dead scope")
        check(r2["checkpoint"] is None, "fresh re-queue has no checkpoint")

        check(summary["resumed"] == [3], "checkpointed orphan is RESUMED")
        r3 = row_of(cx, 3)
        check(r3["state"] == "queued", "resumed orphan 3 back to queued")
        check(r3["resume_from"] == '{"rows_done":42}', "resumes FROM its checkpoint (not scratch)")
        check(r3["checkpoint"] == '{"rows_done":42}', "checkpoint preserved for the worker")
        check(r3["attempts"] == 2, "resume is the SAME run continuing (attempts unchanged)")
        check(r3["scope_unit"] is None, "resumed orphan cleared its dead scope")

        check(summary["failed"] == [4], "non-resumable non-idempotent orphan is marked failed")
        check(state_of(cx, 4) == "failed", "orphan 4 -> failed")
        check(row_of(cx, 4)["stop_reason"], "failed orphan carries a reason")

        check(state_of(cx, 9) == "done", "terminal row 9 untouched by reconcile")
    finally:
        cx.close()
        os.unlink(path)

def test_durable_idempotent():
    print("[durable] reconcile is idempotent — a second pass changes nothing")
    cx, path = fresh_db()
    try:
        add(cx, 1, "running", scope="gone", idempotent=1)
        first = jt_durable.reconcile(cx, live_scopes=set())
        check(first["requeued"] == [1], "first pass re-queues the orphan")

        second = jt_durable.reconcile(cx, live_scopes=set())
        check(second == {"survived": [], "resumed": [], "requeued": [], "failed": []},
              "second pass is a no-op (no running rows left)")
        check(row_of(cx, 1)["attempts"] == 1, "attempts not double-bumped by the second pass")
    finally:
        cx.close()
        os.unlink(path)

def main():
    print("=== T4 supervised job-tree: tree / cascade-stop / durable-reconcile ===")
    test_tree_shape()
    test_tree_cycle_safe()
    test_cascade_multilevel_and_scope()
    test_cascade_idempotent()
    test_durable_reconcile()
    test_durable_idempotent()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
