
from __future__ import annotations
import sqlite3, os, time, json

from . import failklasse

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  cmd           TEXT NOT NULL,            -- JSON argv array
  cwd           TEXT NOT NULL,
  env           TEXT,                     -- JSON dict of env overrides
  profile       TEXT NOT NULL,            -- JSON ResourceProfile
  prio          INTEGER NOT NULL DEFAULT 100,
  mem_estimate  INTEGER NOT NULL DEFAULT 256,
  state         TEXT NOT NULL DEFAULT 'queued',
  scope_unit    TEXT,
  exit_code     INTEGER,
  log_path      TEXT,
  client_tag    TEXT,
  room          TEXT,                     -- linked phantom-room (commission / attach)
  source        TEXT,                     -- cli | http | portal | cron
  prog_done     INTEGER,                  -- progress: units completed
  prog_total    INTEGER,                  -- progress: total units (NULL = unknown)
  prog_msg      TEXT,                     -- progress: latest human message
  prog_at       REAL,                     -- progress/heartbeat timestamp
  attempts      INTEGER NOT NULL DEFAULT 0,
  submitted_at  REAL NOT NULL,
  started_at    REAL,
  finished_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_state_prio ON jobs(state, prio, id);
CREATE TABLE IF NOT EXISTS job_events (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,  -- the GLOBAL monotonic cursor = last_event_id
  job_id  INTEGER NOT NULL,
  ts      REAL NOT NULL,
  kind    TEXT NOT NULL,                  -- state|progress|partial|log|room-feed|approval-request|notify|steer|result|...
  topic   TEXT,                           -- bus topic: job/<id> | group/<gid> | user/<principal>
  data    TEXT                            -- JSON or plain text payload
);
CREATE INDEX IF NOT EXISTS idx_events_job ON job_events(job_id, id);
-- idx_events_topic is created in _migrate() AFTER it ADDs `topic` to a pre-existing
-- job_events; placing it in SCHEMA would fail on an old DB (CREATE TABLE IF NOT EXISTS
-- is a no-op there, so the column is absent until _migrate runs).
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

TERMINAL = ("done", "failed", "cancelled", "timeout", "rejected")

_V2_COLS = [
    ("room", "TEXT"), ("source", "TEXT"),
    ("prog_done", "INTEGER"), ("prog_total", "INTEGER"),
    ("prog_msg", "TEXT"), ("prog_at", "REAL"),
    ("attempts", "INTEGER NOT NULL DEFAULT 0"),
]

_V3_COLS = [
    ("principal", "TEXT"), ("deps", "TEXT"), ("group_id", "TEXT"),
    ("workspace", "TEXT"), ("reply_to", "TEXT"),
    ("needs_confirmation", "INTEGER NOT NULL DEFAULT 0"),
    ("isolation_tier", "TEXT"), ("parent_job", "INTEGER"),
    ("record_ok", "INTEGER NOT NULL DEFAULT 0"), ("task_type", "TEXT"),
]

_V4_COLS = [
    ("submitter_principal", "TEXT"), ("via_device", "TEXT"), ("via_method", "TEXT"),
]

_V5_COLS = [
    ("workspace_path", "TEXT"),
    ("record_commit", "TEXT"),
    ("result_uri", "TEXT"),
    ("result_hash", "TEXT"),
    ("replicated", "INTEGER NOT NULL DEFAULT 0"),
    ("replicated_at", "REAL"),
    ("retention_until", "REAL"),
]

_V6_COLS = [
    ("steer_seq", "INTEGER NOT NULL DEFAULT 0"),
    ("approval_nonce", "TEXT"),
    ("approval_state", "TEXT"),
    ("approval_at", "REAL"),
]

_V7_COLS = [
    ("approval_kind", "TEXT"),
    ("revise_count", "INTEGER NOT NULL DEFAULT 0"),
    ("revise_max", "INTEGER NOT NULL DEFAULT 3"),
    ("held_state", "TEXT"),
    ("held_exit_code", "INTEGER"),
]

_V8_COLS = [
    ("caps", "TEXT"),
]

_V10_COLS = [
    ("approved_by", "TEXT"),
]

_V11_COLS = [
    ("walltime_extra_s", "REAL NOT NULL DEFAULT 0"),
    ("soft_warned", "INTEGER NOT NULL DEFAULT 0"),
    ("oom_retries", "INTEGER NOT NULL DEFAULT 0"),
]

_V13_COLS = [
    ("node", "TEXT"),
]

_V15_COLS = [
    ("fail_class", "TEXT"),
    ("not_before", "REAL"),
]

_V16_COLS = [
    ("drain_requeues", "INTEGER NOT NULL DEFAULT 0"),
    ("node_assigned", "INTEGER NOT NULL DEFAULT 0"),
]

_V3_TABLES = """
CREATE TABLE IF NOT EXISTS principals (
  name  TEXT PRIMARY KEY,
  uid   INTEGER UNIQUE,
  kind  TEXT,                            -- user | agent | device | system
  note  TEXT
);
CREATE TABLE IF NOT EXISTS roles (
  name  TEXT PRIMARY KEY,
  caps  TEXT                             -- JSON list of capability strings
);
CREATE TABLE IF NOT EXISTS grants (
  principal TEXT,
  cap       TEXT
);
CREATE TABLE IF NOT EXISTS task_types (
  name               TEXT PRIMARY KEY,
  cmd_template       TEXT NOT NULL,      -- JSON argv array with {param} placeholders
  params_schema      TEXT,              -- JSON {name: type} (str|int)
  isolation_tier     TEXT,
  needs_confirmation INTEGER NOT NULL DEFAULT 0,
  klass              TEXT,
  approval           TEXT NOT NULL DEFAULT 'none', -- v7 gate: none|pre|post|checkpoint
  profile_template   TEXT                          -- STEP 7: JSON ResourceProfile patch (per-type override)
);
"""

_V4_TABLES = """
CREATE TABLE IF NOT EXISTS identities (
  method    TEXT NOT NULL,             -- peercred | ssh-key | web-session | telegram-id | email-from | device-channel
  selector  TEXT NOT NULL,             -- uid | ssh-fp | session-id | tg user-id | From: addr | device:slot
  principal TEXT NOT NULL,             -- the ONE stable human/agent this credential resolves to
  verified  INTEGER NOT NULL DEFAULT 0,-- 1 = peercred/crypto-verified; 0 = weak channel identity
  bound_at  REAL,
  last_seen REAL,
  PRIMARY KEY (method, selector)
);
"""

_V9_TABLES = """
CREATE TABLE IF NOT EXISTS brain_state (
  principal  TEXT NOT NULL,            -- which brain principal this hot-state belongs to (isolation)
  key        TEXT NOT NULL,            -- session:<p> | digest | intents | last_checkpoint | ...
  value      TEXT,                     -- JSON payload
  updated_at REAL,
  PRIMARY KEY (principal, key)
);
CREATE TABLE IF NOT EXISTS brain_timers (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT UNIQUE NOT NULL,    -- stable timer key (one row per name; idempotent install)
  principal   TEXT NOT NULL,           -- the acting-as principal this timer fires as (e.g. brain)
  interval_s  REAL NOT NULL,           -- recurring period (<=0 = one-shot, self-disables after fire)
  next_fire   REAL NOT NULL,           -- epoch: when now >= this, the brain fires action_json
  action_json TEXT NOT NULL,           -- a VALIDATED closed-world action (e.g. submit net.discover)
  enabled     INTEGER NOT NULL DEFAULT 1,
  created_at  REAL,
  last_fired  REAL
);
CREATE INDEX IF NOT EXISTS idx_brain_timer_due ON brain_timers(enabled, next_fire);
"""

_V12_TABLES = """
CREATE TABLE IF NOT EXISTS principal_policy (
  principal     TEXT PRIMARY KEY,
  prio_bias     INTEGER DEFAULT 0,       -- queue-rank bias applied at submit (NEGATIVE = sooner)
  quota_bytes   INTEGER,                 -- per-user byte quota  (STORE ONLY; enforced in strand #7)
  quota_inodes  INTEGER,                 -- per-user inode quota (STORE ONLY; enforced in strand #7)
  updated_at    REAL,
  updated_by    TEXT                     -- the admin principal that last wrote this policy (audit)
);
"""

_V17_TABLES = """
CREATE TABLE IF NOT EXISTS principal_approval_prefs (
  principal   TEXT NOT NULL,
  task_type   TEXT NOT NULL,
  approval    TEXT NOT NULL,             -- none | pre | post | checkpoint (die vom Nutzer gewählte Reibung)
  updated_at  REAL,
  PRIMARY KEY (principal, task_type)
);
"""

def _migrate(cx):

    cols = {r["name"] for r in cx.execute("PRAGMA table_info(jobs)")}
    for name, typ in (_V2_COLS + _V3_COLS + _V4_COLS + _V5_COLS + _V6_COLS + _V7_COLS
                      + _V8_COLS + _V10_COLS + _V11_COLS + _V13_COLS + _V15_COLS + _V16_COLS):
        if name not in cols:
            cx.execute(f"ALTER TABLE jobs ADD COLUMN {name} {typ}")
    cx.executescript(_V3_TABLES)
    cx.executescript(_V4_TABLES)
    cx.executescript(_V9_TABLES)
    cx.executescript(_V12_TABLES)
    cx.executescript(_V17_TABLES)

    ttcols = {r["name"] for r in cx.execute("PRAGMA table_info(task_types)")}
    if "approval" not in ttcols:
        cx.execute("ALTER TABLE task_types ADD COLUMN approval TEXT NOT NULL DEFAULT 'none'")

    if "profile_template" not in ttcols:
        cx.execute("ALTER TABLE task_types ADD COLUMN profile_template TEXT")

    _pp = {r["name"] for r in cx.execute("PRAGMA table_info(principal_policy)")}
    for _col, _decl in (
            ("weight", "INTEGER DEFAULT 1"),
            ("qos_preset", "TEXT"),
            ("submit_enabled", "INTEGER DEFAULT 1"),
            ("submit_reason", "TEXT"),
            ("max_submit_jobs", "INTEGER"),
            ("max_sessions", "INTEGER"),
            ("priority_boost", "TEXT"),
            ("boost_expiry", "REAL"),
            ("exclusive_entitled", "INTEGER DEFAULT 0"),
            ("preempt_entitled", "INTEGER DEFAULT 0"),
            ("updated_reason", "TEXT")):
        if _col not in _pp:
            cx.execute("ALTER TABLE principal_policy ADD COLUMN %s %s" % (_col, _decl))

    ecols = {r["name"] for r in cx.execute("PRAGMA table_info(job_events)")}
    if "topic" not in ecols:
        cx.execute("ALTER TABLE job_events ADD COLUMN topic TEXT")

    cx.execute("CREATE INDEX IF NOT EXISTS idx_principal "
               "ON jobs(submitter_principal, id)")

    cx.execute("CREATE INDEX IF NOT EXISTS idx_record_gc "
               "ON jobs(record_ok, replicated, retention_until)")

    cx.execute("CREATE INDEX IF NOT EXISTS idx_events_topic ON job_events(topic, id)")

    cx.execute("CREATE INDEX IF NOT EXISTS idx_state_only ON jobs(state)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_group ON jobs(group_id, id)")

    cx.execute("UPDATE jobs SET submitter_principal=principal "
               "WHERE submitter_principal IS NULL AND principal IS NOT NULL")
    cx.execute("UPDATE jobs SET submitter_principal='admin' "
               "WHERE submitter_principal IS NULL")
    cx.commit()

def seed_v3(cx):

    principals = [
        ("admin", 1000, "user", "human operator (uid 1000)"),
        ("brain", 4001, "agent", "LLM core / planner"),
        ("lan-guest", 4002, "user", "untrusted LAN caller"),
        ("adapter", 4003, "system", "de-privileged channel daemon (portal/zyrkel/voiced)"),
    ]
    cx.executemany("INSERT OR IGNORE INTO principals(name,uid,kind,note) VALUES(?,?,?,?)",
                   principals)
    grants = [

        ("admin", "task.raw"), ("admin", "task_type:*"), ("admin", "view:all"),
        ("admin", "approval:resolve"),
        ("brain", "task_type:echo.test"), ("brain", "task_type:sleep.test"),

        ("brain", "task_type:review.post"), ("brain", "task_type:deploy.irreversible"),

        ("brain", "task_type:summary.notify"), ("brain", "task_type:net.discover"),
        ("lan-guest", "task_type:echo.test"),

        ("adapter", "act-as"),
        ("adapter", "task_type:echo.test"), ("adapter", "task_type:sleep.test"),

        ("adapter", "task_type:commission.build"),

        ("adapter", "task_type:commission.run"),

        ("adapter", "approval:resolve"),
    ]
    for principal, cap in grants:
        if not cx.execute("SELECT 1 FROM grants WHERE principal=? AND cap=?",
                          (principal, cap)).fetchone():
            cx.execute("INSERT INTO grants(principal,cap) VALUES(?,?)", (principal, cap))

    task_types = [
        ("echo.test", '["/bin/echo","{msg}"]', '{"msg":"str"}', None, 0, None, "none"),
        ("sleep.test", '["/bin/sleep","{s}"]', '{"s":"int"}', None, 0, None, "none"),

        ("commission.build",
         '["%s","_commission_goal","{goal}"]' % (
             os.environ.get("BBX_PORTAL_BIN")
             or os.path.expanduser("~/.local/bin/brainbox-portal")),
         '{"goal":"str"}', None, 0, "commission", "none"),

        ("commission.run",
         '["%s","_commission","{jid}"]' % (
             os.environ.get("BBX_PORTAL_BIN")
             or os.path.expanduser("~/.local/bin/brainbox-portal")),
         '{"jid":"str"}', None, 0, "commission", "none"),

        ("review.post", '["/bin/echo","{msg}"]', '{"msg":"str"}', None, 0, None, "post"),

        ("deploy.irreversible", '["/bin/echo","DEPLOY:{target}"]', '{"target":"str"}',
         None, 0, "deploy", "pre"),

        ("summary.notify", '["/bin/echo","{msg}"]', '{"msg":"str"}', None, 0, "notify", "none"),

        ("net.discover", '["/bin/echo","net.discover:{cidr}"]', '{"cidr":"str"}',
         None, 0, "admin", "pre"),
    ]
    cx.executemany(
        "INSERT OR IGNORE INTO task_types"
        "(name,cmd_template,params_schema,isolation_tier,needs_confirmation,klass,approval)"
        " VALUES(?,?,?,?,?,?,?)", task_types)

    cx.execute("UPDATE task_types SET approval='none', needs_confirmation=0 "
               "WHERE name='commission.build' AND approval='pre'")

    typed = [

        ("repro.room", '["/bin/echo","repro.room:{room}"]', '{"room":"str"}', None, 0, "repro.room",
         "none", '{"mem":512,"llm_weight":1,"llm_kind":"dedicated","prio":200,"idempotent":true}'),
        ("filler", '["/bin/echo","filler:{room}"]', '{"room":"str"}', None, 0, "filler",
         "none", '{"mem":512,"llm_weight":1,"llm_kind":"dedicated","prio":200,"idempotent":true}'),

        ("spreadsheet.calc", '["/bin/echo","spreadsheet.calc:{sheet}"]', '{"sheet":"str"}', None, 0,
         "spreadsheet.calc", "none",
         '{"mem":200,"llm_weight":6,"llm_kind":"loose","cpu_weight":40,"cpu_quota_pct":50,"prio":100}'),

        ("service.start", '["/bin/echo","service.start:{svc}"]', '{"svc":"str"}', None, 0,
         "tiny", "none", '{"prio":70,"mem":96}'),
    ]
    cx.executemany(
        "INSERT OR IGNORE INTO task_types"
        "(name,cmd_template,params_schema,isolation_tier,needs_confirmation,klass,approval,"
        "profile_template) VALUES(?,?,?,?,?,?,?,?)", typed)
    cx.commit()

def connect(path: str, durability: str | None = None) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cx = sqlite3.connect(path, timeout=10, check_same_thread=False)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA busy_timeout=8000")

    dur = (durability or os.environ.get("PN_DURABILITY", "full")).lower()
    cx.execute("PRAGMA synchronous=" + ("FULL" if dur == "full" else "NORMAL"))
    cx.executescript(SCHEMA)
    _migrate(cx)
    seed_v3(cx)
    cx.commit()
    return cx

def connect_ro(path: str) -> sqlite3.Connection:

    cx = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=5,
                         check_same_thread=False)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA busy_timeout=4000")
    return cx

def get_meta(cx, key, default=None):

    r = cx.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return r["v"] if r else default

def set_meta(cx, key, value):

    cx.execute("INSERT INTO meta(k,v) VALUES(?,?) "
               "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, None if value is None else str(value)))
    cx.commit()

def submit(cx, cmd, cwd, env, profile_json, prio, mem, tag, room=None, source="cli",
           principal=None, task_type=None, submitter_principal=None,
           via_device=None, via_method=None, reply_to=None,
           deps=None, group_id=None, parent_job=None, caps=None, node=None) -> int:

    if submitter_principal is None:
        submitter_principal = principal

    dep_list = list(deps) if deps else []
    init_state = "blocked" if dep_list else "queued"
    cur = cx.execute(
        "INSERT INTO jobs(cmd,cwd,env,profile,prio,mem_estimate,client_tag,room,source,"
        "principal,task_type,submitter_principal,via_device,via_method,reply_to,"
        "deps,group_id,parent_job,caps,node,state,submitted_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (json.dumps(cmd), cwd, json.dumps(env or {}), profile_json, prio, mem, tag,
         room, source, principal, task_type, submitter_principal, via_device, via_method,
         reply_to, json.dumps(dep_list) if dep_list else None, group_id, parent_job,
         json.dumps(sorted(caps)) if caps is not None else None, node, init_state, time.time()),
    )
    cx.commit()
    return cur.lastrowid

def get(cx, job_id, principal=None, scope_all=False):

    if scope_all:
        r = cx.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    else:
        r = cx.execute("SELECT * FROM jobs WHERE id=? AND submitter_principal=?",
                       (job_id, principal)).fetchone()
    return dict(r) if r else None

def principal_for_uid(cx, uid):

    if uid is None:
        return None
    r = cx.execute("SELECT name FROM principals WHERE uid=?", (uid,)).fetchone()
    return r["name"] if r else None

def caps_for(cx, principal):

    if not principal:
        return set()
    return {r["cap"] for r in cx.execute(
        "SELECT cap FROM grants WHERE principal=?", (principal,)).fetchall()}

WILDCARD_CAPS = frozenset({"task.raw", "task_type:*", "view:all"})

def is_lan_only_principal(cx, principal):

    if principal == "admin":
        return True
    return bool(caps_for(cx, principal) & WILDCARD_CAPS)

def caps_ceiling(eff_caps, *ceilings):

    out = set(eff_caps)
    for c in ceilings:
        if c is None:
            continue
        out &= set(c)
    return out

def get_task_type(cx, name):

    if not name:
        return None
    r = cx.execute("SELECT * FROM task_types WHERE name=?", (name,)).fetchone()
    return dict(r) if r else None

APPROVAL_STRENGTH = {"none": 0, "post": 1, "checkpoint": 2, "pre": 3}

def get_approval_prefs(cx, principal):

    if not principal:
        return {}
    return {r["task_type"]: r["approval"] for r in cx.execute(
        "SELECT task_type, approval FROM principal_approval_prefs WHERE principal=?",
        (principal,)).fetchall()}

def set_approval_pref(cx, principal, task_type, approval):

    if not principal:
        return {"ok": False, "error": "kein Prinzipal"}
    if not task_type:
        return {"ok": False, "error": "kein task_type"}
    a = (approval or "").strip().lower()
    if a in ("", "default", "unset"):
        cx.execute("DELETE FROM principal_approval_prefs WHERE principal=? AND task_type=?",
                   (principal, task_type))
        cx.commit()
        return {"ok": True, "task_type": task_type, "approval": None}
    if a not in APPROVAL_STRENGTH:
        return {"ok": False, "error": "ungültiger Freigabe-Wert: %r" % (approval,)}
    cx.execute(
        "INSERT INTO principal_approval_prefs(principal,task_type,approval,updated_at) "
        "VALUES(?,?,?,?) ON CONFLICT(principal,task_type) DO UPDATE SET approval=excluded.approval, "
        "updated_at=excluded.updated_at",
        (principal, task_type, a, time.time()))
    cx.commit()
    return {"ok": True, "task_type": task_type, "approval": a}

def effective_approval(cx, principal, task_type, base):

    base = (base or "none")
    if not principal or not task_type:
        return base
    r = cx.execute(
        "SELECT approval FROM principal_approval_prefs WHERE principal=? AND task_type=?",
        (principal, task_type)).fetchone()
    if not r:
        return base
    pref = r["approval"]
    if APPROVAL_STRENGTH.get(pref, 0) >= APPROVAL_STRENGTH.get(base, 0):
        return pref
    return base

FILLER_SOURCE = "filler"

def _eff_prio(r, now):

    return (r["prio"] - int((now - r["submitted_at"]) // 600), r["id"])

def position(cx, job_id) -> int:

    r = cx.execute("SELECT state,source FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not r or r["state"] != "queued":
        return 0
    is_filler = (r["source"] == FILLER_SOURCE)
    if is_filler:
        rows = cx.execute("SELECT id,prio,submitted_at FROM jobs "
                          "WHERE state='queued' AND source=?", (FILLER_SOURCE,)).fetchall()
    else:
        rows = cx.execute("SELECT id,prio,submitted_at FROM jobs "
                          "WHERE state='queued' AND source!=?", (FILLER_SOURCE,)).fetchall()
    now = time.time()
    me = next((x for x in rows if x["id"] == job_id), None)
    if me is None:
        return 0
    my_key = _eff_prio(me, now)

    ahead = sum(1 for x in rows if _eff_prio(x, now) < my_key)
    return ahead + 1

def next_queued(cx, exclude_filler: bool = False, skip=None):

    now = time.time()

    if exclude_filler:
        rows = cx.execute("SELECT * FROM jobs WHERE state='queued' AND source!=? "
                          "AND (not_before IS NULL OR not_before<=?)",
                          (FILLER_SOURCE, now)).fetchall()
    else:
        rows = cx.execute("SELECT * FROM jobs WHERE state='queued' "
                          "AND (not_before IS NULL OR not_before<=?)", (now,)).fetchall()
    if skip:
        rows = [r for r in rows if r["id"] not in skip]
    if not rows:
        return None
    return dict(min(rows, key=lambda r: _eff_prio(r, now)))

def next_queued_filler(cx, skip=None):

    rows = cx.execute("SELECT * FROM jobs WHERE state='queued' AND source=? "
                      "AND (not_before IS NULL OR not_before<=?)",
                      (FILLER_SOURCE, time.time())).fetchall()
    if skip:
        rows = [r for r in rows if r["id"] not in skip]
    if not rows:
        return None
    now = time.time()
    return dict(min(rows, key=lambda r: _eff_prio(r, now)))

def running_fillers(cx) -> list:

    return [dict(r) for r in cx.execute(
        "SELECT * FROM jobs WHERE state='running' AND source=?", (FILLER_SOURCE,)).fetchall()]

def has_queued_real(cx) -> bool:

    r = cx.execute("SELECT 1 FROM jobs WHERE state='queued' AND source!=? LIMIT 1",
                   (FILLER_SOURCE,)).fetchone()
    return r is not None

def running(cx):
    return [dict(r) for r in cx.execute("SELECT * FROM jobs WHERE state='running'").fetchall()]

def counts(cx):
    d = {row["state"]: row["c"] for row in cx.execute(
        "SELECT state, COUNT(*) c FROM jobs GROUP BY state")}
    return d

SUCCESS = "done"

DEP_FAIL_STATES = ("failed", "cancelled", "timeout", "rejected")

def _deps_of(job) -> list:

    raw = job.get("deps") if isinstance(job, dict) else job["deps"]
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [int(x) for x in v] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []

def dep_state(cx, dep_ids):

    if not dep_ids:
        return (True, False, [], {})
    qmarks = ",".join("?" for _ in dep_ids)
    rows = {r["id"]: r["state"] for r in cx.execute(
        f"SELECT id, state FROM jobs WHERE id IN ({qmarks})", list(dep_ids)).fetchall()}
    missing = [d for d in dep_ids if d not in rows]
    any_failed = bool(missing) or any(s in DEP_FAIL_STATES for s in rows.values())
    all_success = (not missing) and all(rows.get(d) == SUCCESS for d in dep_ids)
    return (all_success, any_failed, missing, rows)

def deps_satisfied(cx, job) -> bool:

    ok, _failed, _missing, _states = dep_state(cx, _deps_of(job))
    return ok

def deps_failed(cx, job):

    _ok, failed, missing, states = dep_state(cx, _deps_of(job))
    if not failed:
        return None
    bad = [d for d, s in states.items() if s in DEP_FAIL_STATES]
    return {"failed_deps": bad, "missing_deps": missing, "states": states}

def blocked_jobs(cx, limit=1000):

    return [dict(r) for r in cx.execute(
        "SELECT * FROM jobs WHERE state='blocked' ORDER BY id LIMIT ?", (limit,)).fetchall()]

def promote_blocked(cx, job_id) -> bool:

    cur = cx.execute("UPDATE jobs SET state='queued' WHERE id=? AND state='blocked'", (job_id,))
    cx.commit()
    return cur.rowcount > 0

def assign_node(cx, job_id, node, mem_estimate=None) -> bool:

    if mem_estimate is None:
        cur = cx.execute("UPDATE jobs SET node=?, node_assigned=1 WHERE id=? AND state='queued'",
                         (node, job_id))
    else:
        cur = cx.execute("UPDATE jobs SET node=?, node_assigned=1, mem_estimate=? "
                         "WHERE id=? AND state='queued'",
                         (node, int(mem_estimate), job_id))
    cx.commit()
    return cur.rowcount > 0

def set_job_prio(cx, job_id, new_prio) -> bool:

    p = max(0, min(300, int(new_prio)))
    cur = cx.execute("UPDATE jobs SET prio=? WHERE id=? AND state='queued'", (p, job_id))
    cx.commit()
    return cur.rowcount > 0

def cancel_blocked_dep_failed(cx, job_id, detail=None) -> bool:

    cur = cx.execute(
        "UPDATE jobs SET state='cancelled', finished_at=? "
        "WHERE id=? AND state IN ('blocked','queued','staged','awaiting_approval')",
        (time.time(), job_id))
    cx.commit()
    return cur.rowcount > 0

def dependents_of(cx, job_id):

    out = []

    like = f'%{job_id}%'
    for r in cx.execute("SELECT * FROM jobs WHERE deps LIKE ?", (like,)).fetchall():
        if job_id in _deps_of(dict(r)):
            out.append(dict(r))
    return out

def gate_dag(cx):

    promoted, cancelled = [], []

    while True:
        progressed = False
        candidates = [dict(r) for r in cx.execute(
            "SELECT * FROM jobs WHERE state IN ('blocked','queued','staged','awaiting_approval') "
            "AND deps IS NOT NULL ORDER BY id").fetchall()]
        for job in candidates:
            jid = job["id"]
            det = deps_failed(cx, job)
            if det and cancel_blocked_dep_failed(cx, jid, det):
                cancelled.append((jid, det))
                add_event(cx, jid, "note",
                          {"reason": "dep_failed", "failed_deps": det.get("failed_deps"),
                           "missing_deps": det.get("missing_deps")})
                progressed = True
        if not progressed:
            break

    for job in blocked_jobs(cx):
        if deps_satisfied(cx, job) and promote_blocked(cx, job["id"]):
            promoted.append(job["id"])
    return {"promoted": promoted, "cancelled": cancelled}

def group_owner(cx, group_id):

    if not group_id:
        return None
    r = cx.execute("SELECT submitter_principal FROM jobs WHERE group_id=? "
                   "ORDER BY id LIMIT 1", (group_id,)).fetchone()
    return r["submitter_principal"] if r else None

def group_members(cx, group_id, principal=None, scope_all=False):

    if not group_id:
        return []
    if scope_all:
        rows = cx.execute("SELECT * FROM jobs WHERE group_id=? ORDER BY id", (group_id,)).fetchall()
    else:
        rows = cx.execute("SELECT * FROM jobs WHERE group_id=? AND submitter_principal=? ORDER BY id",
                          (group_id, principal)).fetchall()
    return [dict(r) for r in rows]

def group_status(cx, group_id, principal=None, scope_all=False):

    members = group_members(cx, group_id, principal=principal, scope_all=scope_all)
    if not members:
        return None
    by_state = {}
    for j in members:
        by_state[j["state"]] = by_state.get(j["state"], 0) + 1
    states = {j["state"] for j in members}
    if states & set(DEP_FAIL_STATES):
        overall = "failed"
    elif "running" in states:
        overall = "running"
    elif states & {"blocked", "queued", "staged", "awaiting_approval"}:
        overall = "pending"
    else:
        overall = "done"
    total = len(members)
    done = by_state.get("done", 0)
    return {
        "group_id": group_id,
        "overall": overall,
        "total": total,
        "done": done,
        "counts": by_state,
        "members": [{"id": j["id"], "state": j["state"], "task_type": j.get("task_type"),
                     "deps": _deps_of(j), "parent_job": j.get("parent_job"),
                     "exit_code": j.get("exit_code")} for j in members],
    }

def handoff_refs(cx, dep_ids, principal=None, scope_all=False):

    refs = []
    for d in dep_ids or []:
        j = get(cx, d, principal=principal, scope_all=scope_all)
        if not j:
            continue
        refs.append({
            "job_id": j["id"],
            "state": j["state"],
            "verdict": "done" if j["state"] == SUCCESS else j["state"],
            "result_uri": j.get("result_uri"),
            "result_hash": j.get("result_hash"),
            "record_commit": j.get("record_commit"),
            "workspace_path": j.get("workspace_path"),
        })
    return refs

def result_view(cx, job_id, principal=None, scope_all=False):

    j = get(cx, job_id, principal=principal, scope_all=scope_all)
    if not j:
        return None
    verdict = "done" if j["state"] == SUCCESS else j["state"]

    conf = None
    ev = cx.execute("SELECT data FROM job_events WHERE job_id=? AND kind IN ('result','partial') "
                    "ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
    if ev and ev["data"]:
        try:
            conf = json.loads(ev["data"]).get("confidence")
        except (ValueError, TypeError, AttributeError):
            conf = None
    if conf is None and j["state"] == SUCCESS:
        conf = 1.0
    return {
        "job_id": j["id"],
        "verdict": verdict,
        "confidence": conf,
        "artifacts": {
            "result_uri": j.get("result_uri"),
            "result_hash": j.get("result_hash"),
            "record_commit": j.get("record_commit"),
            "workspace_path": j.get("workspace_path"),
        },
    }

EXTERNAL_SOURCES = ("session",)

def _running_track(row) -> str:

    from .profile import ResourceProfile
    from . import sched
    try:
        return sched.track_of(ResourceProfile.from_json(row["profile"]))
    except Exception:
        return "batch"

def reserved_mib(cx, track: str | None = None) -> int:

    if track is None:

        r = cx.execute(
            "SELECT COALESCE(SUM(mem_estimate),0) s FROM jobs WHERE state='running' "
            "AND NOT (source='session' AND scope_unit LIKE 'session:node=%')").fetchone()
        return r["s"] or 0
    want = {"batch", "filler"} if track == "batch" else {track}
    total = 0
    for r in cx.execute("SELECT mem_estimate, profile, source, scope_unit "
                        "FROM jobs WHERE state='running'").fetchall():

        if (r["source"] or "") == "session" and (r["scope_unit"] or "").startswith("session:node="):
            continue
        if _running_track(r) in want:
            total += r["mem_estimate"] or 0
    return total

def running_track_counts(cx) -> dict:

    out = {"total": 0, "batch": 0, "interactive": 0, "filler": 0, "external": 0, "node": 0}
    for r in cx.execute("SELECT source, profile, node FROM jobs WHERE state='running'").fetchall():
        if (r["source"] or "") in EXTERNAL_SOURCES:
            out["external"] += 1
            continue
        if r["node"]:
            out["node"] += 1
            continue
        t = _running_track(r)
        out["total"] += 1
        out[t] = out.get(t, 0) + 1
    return out

def reserved_llm(cx) -> float:

    from .profile import ResourceProfile
    from . import sched
    total = 0.0
    for r in cx.execute("SELECT profile FROM jobs WHERE state='running'").fetchall():
        try:
            total += sched.llm_demand_slots(ResourceProfile.from_json(r["profile"]))
        except Exception:
            continue
    return total

def reserved_cpu(cx, track: str | None = None) -> float:

    from .profile import ResourceProfile
    from . import sched
    want = None if track is None else ({"batch", "filler"} if track == "batch" else {track})
    total = 0.0
    for r in cx.execute("SELECT profile, node FROM jobs WHERE state='running'").fetchall():
        try:
            prof = ResourceProfile.from_json(r["profile"])
            if want is not None and sched.track_of(prof) not in want:
                continue
            total += local_cpu_width(r, prof)
        except Exception:
            continue
    return total

def local_cpu_width(row, prof) -> float:

    from . import sched
    try:
        if row["node"]:
            from .remotedispatch import PROXY_CPU_PCT
            return max(0.0, PROXY_CPU_PCT / 100.0)
    except (IndexError, KeyError, TypeError):
        pass
    return sched.cpu_width(prof)

def kern_buchungen(cx) -> list:

    out = []
    rows = cx.execute(
        "SELECT id,state,node,profile,started_at,submitted_at,prio,source,client_tag,env "
        "FROM jobs WHERE state IN ('queued','running')").fetchall()
    for r in rows:
        try:
            p = json.loads(r["profile"] or "{}")
        except (ValueError, TypeError):
            continue
        k = p.get("kerne")
        if not k:
            continue
        try:
            k = int(k)
        except (TypeError, ValueError):
            continue
        if k < 1:
            continue
        out.append({"id": r["id"], "state": r["state"], "node": r["node"], "kerne": k,
                    "dauer_s": p.get("dauer_s"), "started_at": r["started_at"],
                    "submitted_at": r["submitted_at"], "prio": r["prio"],
                    "source": r["source"], "client_tag": r["client_tag"], "env": r["env"]})
    return out

def mark_running(cx, job_id, scope_unit, log_path):
    cx.execute("UPDATE jobs SET state='running',scope_unit=?,log_path=?,started_at=?,prog_at=? WHERE id=?",
               (scope_unit, log_path, time.time(), time.time(), job_id))
    cx.commit()

def mark_terminal(cx, job_id, state, exit_code=None, fail_class=None):

    if fail_class is not None:
        cx.execute("UPDATE jobs SET state=?,exit_code=?,finished_at=?,fail_class=? WHERE id=?",
                   (state, exit_code, time.time(), fail_class, job_id))
    else:
        cx.execute("UPDATE jobs SET state=?,exit_code=?,finished_at=? WHERE id=?",
                   (state, exit_code, time.time(), job_id))
    cx.commit()

def update_progress(cx, job_id, done=None, total=None, msg=None):

    sets, vals = ["prog_at=?"], [time.time()]
    if done is not None:
        sets.append("prog_done=?"); vals.append(int(done))
    if total is not None:
        sets.append("prog_total=?"); vals.append(int(total))
    if msg is not None:
        sets.append("prog_msg=?"); vals.append(str(msg)[:500])
    vals.append(job_id)
    cx.execute(f"UPDATE jobs SET {','.join(sets)} WHERE id=?", vals)
    cx.commit()

def heartbeat(cx, job_id):
    cx.execute("UPDATE jobs SET prog_at=? WHERE id=?", (time.time(), job_id))
    cx.commit()

EVENT_KINDS = (
    "state", "progress", "partial", "log", "room-feed", "approval-request", "notify",
    "steer", "result", "replicated", "error", "gc", "note", "checkpoint",

    "approval-result",

    "group-status", "workorder",
)

def add_event(cx, job_id, kind, data=None, topic=None):

    if data is not None and not isinstance(data, str):
        data = json.dumps(data, separators=(",", ":"))
    if topic is None:
        topic = f"job/{job_id}"
    cur = cx.execute("INSERT INTO job_events(job_id,ts,kind,topic,data) VALUES(?,?,?,?,?)",
                     (job_id, time.time(), kind, topic, data))
    cx.commit()
    return cur.lastrowid

def add_typed_event(cx, job_id, kind, data=None, topics=None):

    if data is not None and not isinstance(data, str):
        data = json.dumps(data, separators=(",", ":"))
    owner = cx.execute("SELECT submitter_principal, group_id FROM jobs WHERE id=?",
                       (job_id,)).fetchone()
    tset = list(topics) if topics else [f"job/{job_id}"]
    if owner:
        if owner["submitter_principal"]:
            tset.append(f"user/{owner['submitter_principal']}")

        if owner["group_id"] and owner["submitter_principal"]:
            tset.append(group_topic(owner["submitter_principal"], owner["group_id"]))

    seen, uniq = set(), []
    for t in tset:
        if t not in seen:
            seen.add(t); uniq.append(t)
    ts = time.time()
    ids = []
    for t in uniq:
        cur = cx.execute("INSERT INTO job_events(job_id,ts,kind,topic,data) VALUES(?,?,?,?,?)",
                         (job_id, ts, kind, t, data))
        ids.append(cur.lastrowid)
    cx.commit()
    return ids

def publish_user_notify(cx, principal, kind, data, job_id=0):

    if data is not None and not isinstance(data, str):
        data = json.dumps(data, separators=(",", ":"))
    cur = cx.execute("INSERT INTO job_events(job_id,ts,kind,topic,data) VALUES(?,?,?,?,?)",
                     (job_id, time.time(), kind, f"user/{principal}", data))
    cx.commit()
    return cur.lastrowid

def events(cx, job_id, limit=200, principal=None, scope_all=False):

    if not scope_all:
        owner = cx.execute("SELECT submitter_principal FROM jobs WHERE id=?",
                           (job_id,)).fetchone()
        if not owner or owner["submitter_principal"] != principal:
            return []
    return [dict(r) for r in cx.execute(
        "SELECT id,ts,kind,topic,data FROM job_events WHERE job_id=? ORDER BY id DESC LIMIT ?",
        (job_id, limit)).fetchall()]

def group_topic(submitter_principal, group_id):

    return f"group/{submitter_principal}/{group_id}"

def _parse_topic(topic):

    if not topic or "/" not in topic:
        return (None, None)
    kind, _, rest = topic.partition("/")
    if kind == "group":
        owner, sep, gid = rest.partition("/")
        if not sep:

            return ("group", None)
        return ("group", (owner, gid))
    return (kind, rest)

def authorize_topics(cx, principal, topics, scope_all=False):

    if scope_all:
        return list(topics)
    out = []
    for t in topics:
        kind, key = _parse_topic(t)
        if kind == "user":
            if key == principal:
                out.append(t)
        elif kind == "job":
            try:
                jid = int(key)
            except (TypeError, ValueError):
                continue
            if get(cx, jid, principal=principal) is not None:
                out.append(t)
        elif kind == "group":
            if not key:
                continue
            owner, gid = key

            if owner != principal:
                continue
            r = cx.execute("SELECT 1 FROM jobs WHERE group_id=? AND submitter_principal=? LIMIT 1",
                           (gid, principal)).fetchone()
            if r:
                out.append(t)

    return out

def events_since(cx, topics, after_id=0, limit=500):

    if not topics:
        return []
    qmarks = ",".join("?" for _ in topics)
    sql = (f"SELECT id,job_id,ts,kind,topic,data FROM job_events "
           f"WHERE topic IN ({qmarks}) AND id > ? ORDER BY id ASC LIMIT ?")
    return [dict(r) for r in cx.execute(sql, [*topics, after_id, limit]).fetchall()]

def max_event_id(cx):

    r = cx.execute("SELECT COALESCE(MAX(id),0) m FROM job_events").fetchone()
    return r["m"] or 0

def cvm(cx, job_id, principal=None, scope_all=False):

    j = get(cx, job_id, principal=principal, scope_all=scope_all)
    if not j:
        return None

    recent = events(cx, job_id, limit=50, principal=principal, scope_all=scope_all)
    def _payload(kind):
        e = next((e for e in recent if e["kind"] == kind), None)
        if not e:
            return None
        d = e.get("data")
        if isinstance(d, str) and d[:1] in ("{", "["):
            try:
                return json.loads(d)
            except ValueError:
                return d
        return d
    return {
        "schema": "pn-cvm/1",
        "id": j["id"],
        "state": j["state"],
        "task_type": j.get("task_type") or "(raw)",
        "principal": j.get("submitter_principal"),
        "group_id": j.get("group_id"),
        "topic": f"job/{job_id}",

        "blocked": j["state"] == "blocked",
        "deps": _deps_of(j),
        "parent_job": j.get("parent_job"),
        "caps": (json.loads(j["caps"]) if j.get("caps") else None),
        "work_order": _payload("workorder"),
        "progress": {"done": j.get("prog_done"), "total": j.get("prog_total"),
                     "msg": j.get("prog_msg"), "at": j.get("prog_at")},
        "exit_code": j.get("exit_code"),
        "record_ok": bool(j.get("record_ok")),
        "replicated": bool(j.get("replicated")),
        "needs_confirmation": bool(j.get("needs_confirmation")),
        "approval_state": j.get("approval_state"),

        "approval_kind": j.get("approval_kind"),
        "awaiting_approval": j["state"] == "awaiting_approval",
        "held_state": j.get("held_state"),
        "revise": {"count": j.get("revise_count") or 0,
                   "max": j.get("revise_max") if j.get("revise_max") is not None else 3},
        "result": {"record_commit": j.get("record_commit"), "result_hash": j.get("result_hash"),
                   "result_uri": j.get("result_uri"), "workspace_path": j.get("workspace_path")},
        "approval_request": _payload("approval-request"),
        "approval_result": _payload("approval-result"),
        "partial": _payload("partial"),
        "submitted_at": j.get("submitted_at"),
        "started_at": j.get("started_at"),
        "finished_at": j.get("finished_at"),
        "last_event_id": max_event_id(cx),
    }

def enqueue_steer(cx, job_id):

    cx.execute("UPDATE jobs SET steer_seq = COALESCE(steer_seq,0) + 1 WHERE id=?", (job_id,))
    cx.commit()
    r = cx.execute("SELECT steer_seq FROM jobs WHERE id=?", (job_id,)).fetchone()
    return r["steer_seq"] if r else None

def stage_for_approval(cx, job_id, nonce, approval_kind="pre"):

    cx.execute("UPDATE jobs SET state='staged', approval_nonce=?, approval_state='pending', "
               "approval_kind=?, approval_at=? WHERE id=?",
               (nonce, approval_kind, time.time(), job_id))
    cx.commit()

def hold_for_approval(cx, job_id, nonce, held_state, held_exit_code, approval_kind="post"):

    cx.execute(
        "UPDATE jobs SET state='awaiting_approval', approval_nonce=?, approval_state='pending', "
        "approval_kind=?, held_state=?, held_exit_code=?, approval_at=? WHERE id=?",
        (nonce, approval_kind, held_state, held_exit_code, time.time(), job_id))
    cx.commit()

def resolve_approval(cx, nonce, decision, principal=None, scope_all=False, feedback=None):

    if not nonce:
        return {"ok": False, "error": "missing nonce"}
    row = cx.execute(
        "SELECT id, state, submitter_principal, approval_state, approval_kind, held_state, "
        "held_exit_code, revise_count, revise_max FROM jobs WHERE approval_nonce=?",
        (nonce,)).fetchone()
    if not row:
        return {"ok": False, "error": "unknown or expired nonce"}
    if not scope_all and row["submitter_principal"] != principal:

        return {"ok": False, "error": "unknown or expired nonce"}

    rid, cur_state, app_state = row["id"], row["state"], row["approval_state"]
    is_post = (cur_state == "awaiting_approval")

    if decision in ("deny", "reject"):
        decision = "reject" if is_post else "deny"

    if (not scope_all and decision in ("approve", "deny", "reject")
            and row["submitter_principal"] == principal):
        return {"ok": False, "id": rid, "state": cur_state,
                "error": "submitter cannot self-approve (separation of duties)"}

    applied_map = {"approve": "approved", "deny": "denied", "reject": "rejected"}
    want = applied_map.get(decision)
    if want and app_state == want:
        return {"ok": True, "id": rid, "state": cur_state, "decision": want,
                "idempotent": True, "gate": row["approval_kind"]}

    if app_state in ("approved", "denied", "rejected") and decision != "revise":
        return {"ok": False, "error": f"already {app_state}; cannot {decision}",
                "id": rid, "state": cur_state}

    if not is_post:
        if cur_state != "staged":
            return {"ok": False, "error": f"job is {cur_state}, not staged (cannot {decision})",
                    "id": rid, "state": cur_state}
        if decision not in ("approve", "deny"):
            return {"ok": False, "error": f"pre-dispatch gate accepts approve/deny only (got "
                    f"{decision})", "id": rid, "state": cur_state}
        if decision == "approve":
            cx.execute("UPDATE jobs SET state='queued', approval_state='approved', approval_at=?, "
                       "approved_by=? WHERE id=? AND approval_state='pending'",
                       (time.time(), principal, rid))
            new_state = "queued"
        else:
            cx.execute("UPDATE jobs SET state='cancelled', approval_state='denied', approval_at=?, "
                       "approved_by=?, finished_at=? WHERE id=? AND approval_state='pending'",
                       (time.time(), principal, time.time(), rid))
            new_state = "cancelled"
        cx.commit()
        return {"ok": True, "id": rid, "state": new_state, "decision": applied_map[decision],
                "idempotent": False, "gate": row["approval_kind"] or "pre"}

    if decision == "approve":

        cx.execute("UPDATE jobs SET approval_state='approved', approval_at=?, approved_by=? "
                   "WHERE id=? AND approval_state='pending'", (time.time(), principal, rid))
        cx.commit()
        return {"ok": True, "id": rid, "state": cur_state, "decision": "approved",
                "idempotent": False, "gate": row["approval_kind"] or "post",
                "finalize": True, "held_state": row["held_state"] or "done",
                "held_exit_code": row["held_exit_code"]}
    if decision == "reject":

        cx.execute("UPDATE jobs SET state='rejected', approval_state='rejected', approval_at=?, "
                   "approved_by=?, finished_at=? WHERE id=? AND approval_state='pending'",
                   (time.time(), principal, time.time(), rid))
        cx.commit()
        return {"ok": True, "id": rid, "state": "rejected", "decision": "rejected",
                "idempotent": False, "gate": row["approval_kind"] or "post"}
    if decision == "revise":
        rc, rmax = (row["revise_count"] or 0), (row["revise_max"] if row["revise_max"] is not None else 3)
        if rc >= rmax:
            return {"ok": False, "error": f"revise loop exhausted ({rc}/{rmax}); approve or reject",
                    "id": rid, "state": cur_state, "revise_count": rc, "revise_max": rmax}

        cx.execute(
            "UPDATE jobs SET state='queued', approval_state=NULL, approval_nonce=NULL, "
            "held_state=NULL, held_exit_code=NULL, revise_count=revise_count+1, "
            "started_at=NULL, finished_at=NULL, exit_code=NULL, record_ok=0, "

            "walltime_extra_s=0, soft_warned=0, approval_at=? "
            "WHERE id=? AND approval_state='pending'", (time.time(), rid))
        cx.commit()
        return {"ok": True, "id": rid, "state": "queued", "decision": "revise",
                "idempotent": False, "gate": row["approval_kind"] or "post",
                "revise_count": rc + 1, "revise_max": rmax}
    return {"ok": False, "error": f"unknown decision {decision}", "id": rid, "state": cur_state}

def submitter_for_nonce(cx, nonce):

    if not nonce:
        return None
    r = cx.execute("SELECT submitter_principal FROM jobs WHERE approval_nonce=?", (nonce,)).fetchone()
    return r["submitter_principal"] if r else None

def pending_approvals(cx, principal=None, scope_all=False, limit=100):

    where = ["state IN ('staged','awaiting_approval')", "approval_state='pending'"]
    vals = []
    if not scope_all:
        where.append("submitter_principal=?"); vals.append(principal)
    vals.append(limit)
    sql = ("SELECT id, state, task_type, approval_kind, approval_nonce, submitter_principal, "
           "revise_count, revise_max, held_state, held_exit_code, result_hash, record_commit, "
           "result_uri, workspace_path, submitted_at, approval_at FROM jobs WHERE "
           + " AND ".join(where) + " ORDER BY approval_at DESC, id DESC LIMIT ?")
    out = []
    for r in cx.execute(sql, vals).fetchall():
        j = dict(r)

        ev = cx.execute(
            "SELECT data FROM job_events WHERE job_id=? AND kind='approval-request' "
            "ORDER BY id DESC LIMIT 1", (j["id"],)).fetchone()
        req = None
        if ev and ev["data"]:
            try:
                req = json.loads(ev["data"])
            except (ValueError, TypeError):
                req = ev["data"]
        j["approval_request"] = req
        out.append(j)
    return out

def requeue(cx, job_id, reset_age=False, fail_class=failklasse.TRANSIENT, not_before=None):

    now = time.time()
    if not_before is None:
        r = cx.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        delay = failklasse.backoff_s((r["attempts"] if r else 0) or 0)
        not_before = (now + delay) if delay > 0 else None
    if reset_age:
        cx.execute("UPDATE jobs SET state='queued',scope_unit=NULL,started_at=NULL,"
                   "exit_code=NULL,attempts=attempts+1,walltime_extra_s=0,soft_warned=0,"
                   "fail_class=?,not_before=?,submitted_at=? WHERE id=?",
                   (fail_class, not_before, now, job_id))
    else:
        cx.execute("UPDATE jobs SET state='queued',scope_unit=NULL,started_at=NULL,"
                   "exit_code=NULL,attempts=attempts+1,walltime_extra_s=0,soft_warned=0,"
                   "fail_class=?,not_before=? WHERE id=?",
                   (fail_class, not_before, job_id))
    cx.commit()
    return not_before

def requeue_drain(cx, job_id, clear_node=False):

    now = time.time()
    r = cx.execute("SELECT attempts FROM jobs WHERE id=? AND state='running'",
                   (job_id,)).fetchone()
    if not r:
        return None
    delay = failklasse.backoff_s((r["attempts"] or 0))
    nb = (now + delay) if delay > 0 else None
    extra = "node=NULL, node_assigned=0, " if clear_node else ""
    cur = cx.execute(
        "UPDATE jobs SET state='queued', scope_unit=NULL, started_at=NULL, exit_code=NULL, "
        "attempts=attempts+1, drain_requeues=drain_requeues+1, walltime_extra_s=0, "
        "soft_warned=0, " + extra + "fail_class='transient', not_before=? "
        "WHERE id=? AND state='running'", (nb, job_id))
    cx.commit()
    if not cur.rowcount:
        return None
    return nb if nb is not None else now

def grant_extension(cx, job_id, seconds):

    r = cx.execute("SELECT state, walltime_extra_s FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not r or r["state"] != "running":
        return None
    total = (r["walltime_extra_s"] or 0.0) + float(seconds)
    cx.execute("UPDATE jobs SET walltime_extra_s=?, soft_warned=0 WHERE id=?", (total, job_id))
    cx.commit()
    return total

def mark_soft_warned(cx, job_id):

    cx.execute("UPDATE jobs SET soft_warned=1 WHERE id=?", (job_id,))
    cx.commit()

def requeue_oom_grown(cx, job_id, new_mem, new_profile_json):

    cx.execute("UPDATE jobs SET state='queued', scope_unit=NULL, started_at=NULL, exit_code=NULL, "
               "profile=?, mem_estimate=?, soft_warned=0, walltime_extra_s=0, "
               "fail_class='transient', not_before=NULL, "
               "oom_retries=oom_retries+1 WHERE id=?",
               (new_profile_json, int(new_mem), job_id))
    cx.commit()

def oldest_wait(cx) -> float:
    r = cx.execute("SELECT MIN(submitted_at) m FROM jobs WHERE state='queued'").fetchone()
    return (time.time() - r["m"]) if r and r["m"] else 0.0

def list_recent(cx, limit=50, principal=None, scope_all=False, before_id=None):

    lim = max(int(limit or 50), 1)

    if before_id is not None:
        try:
            b = int(before_id)
        except (TypeError, ValueError):
            return []
        if scope_all:
            rows = cx.execute("SELECT * FROM jobs WHERE id<? ORDER BY id DESC LIMIT ?",
                              (b, lim)).fetchall()
        else:
            rows = cx.execute("SELECT * FROM jobs WHERE submitter_principal=? AND id<? "
                              "ORDER BY id DESC LIMIT ?", (principal, b, lim)).fetchall()
        return [dict(r) for r in rows]
    if scope_all:
        act = cx.execute(
            "SELECT * FROM jobs WHERE state IN ('queued','running') "
            "ORDER BY id DESC LIMIT ?", (lim,)).fetchall()
        rest = cx.execute(
            "SELECT * FROM jobs WHERE state NOT IN ('queued','running') "
            "ORDER BY id DESC LIMIT ?", (max(lim - len(act), 0),)).fetchall()
    else:
        act = cx.execute(
            "SELECT * FROM jobs WHERE submitter_principal=? AND state IN ('queued','running') "
            "ORDER BY id DESC LIMIT ?", (principal, lim)).fetchall()
        rest = cx.execute(
            "SELECT * FROM jobs WHERE submitter_principal=? AND state NOT IN ('queued','running') "
            "ORDER BY id DESC LIMIT ?", (principal, max(lim - len(act), 0))).fetchall()
    return [dict(r) for r in act] + [dict(r) for r in rest]

_POLICY_FIELDS = ("prio_bias", "quota_bytes", "quota_inodes",
                  "weight", "qos_preset", "submit_enabled", "submit_reason",
                  "max_submit_jobs", "max_sessions", "priority_boost", "boost_expiry",
                  "exclusive_entitled", "preempt_entitled")

POLICY_PRESETS = {
    "guest-filler":    {"label": "Guest-Filler",         "weight": 1,  "max_submit_jobs": 5,
                        "max_sessions": 2,  "priority_boost": "none",     "exclusive_entitled": 0, "preempt_entitled": 0},
    "standard":        {"label": "Standard",             "weight": 4,  "max_submit_jobs": 20,
                        "max_sessions": 5,  "priority_boost": "none",     "exclusive_entitled": 0, "preempt_entitled": 0},
    "trusted-batch":   {"label": "Trusted-Batch (Power)","weight": 16, "max_submit_jobs": 50,
                        "max_sessions": 12, "priority_boost": "elevated", "exclusive_entitled": 0, "preempt_entitled": 1},
    "owner-exclusive": {"label": "Owner-Exclusive",      "weight": 64, "max_submit_jobs": None,
                        "max_sessions": None, "priority_boost": "high",   "exclusive_entitled": 1, "preempt_entitled": 1},
}

def get_policy(cx, principal):

    r = cx.execute("SELECT * FROM principal_policy WHERE principal=?", (principal,)).fetchone()
    if not r:
        return {"principal": principal, "prio_bias": 0, "quota_bytes": None,
                "quota_inodes": None, "weight": 1, "qos_preset": None,
                "submit_enabled": 1, "submit_reason": None, "max_submit_jobs": None,
                "max_sessions": None, "priority_boost": "none", "boost_expiry": None,
                "exclusive_entitled": 0, "preempt_entitled": 0,
                "updated_at": None, "updated_by": None, "updated_reason": None}
    d = dict(r)
    if d.get("weight") is None:
        d["weight"] = 1
    if d.get("submit_enabled") is None:
        d["submit_enabled"] = 1
    if not d.get("priority_boost"):
        d["priority_boost"] = "none"
    return d

def apply_preset(cx, principal, preset, updated_by=None, reason=None):

    p = POLICY_PRESETS.get(preset)
    if not p:
        return None
    f = {k: v for k, v in p.items() if k != "label"}
    f["qos_preset"] = preset
    return set_policy(cx, principal, updated_by=updated_by, reason=reason, **f)

def set_policy(cx, principal, updated_by=None, reason=None, **fields):

    cx.execute("INSERT OR IGNORE INTO principal_policy(principal) VALUES(?)", (principal,))
    sets, vals = [], []
    for k in _POLICY_FIELDS:
        if k in fields:
            sets.append(f"{k}=?"); vals.append(fields[k])
    sets.append("updated_at=?"); vals.append(time.time())
    sets.append("updated_by=?"); vals.append(updated_by)
    sets.append("updated_reason=?"); vals.append(reason)
    vals.append(principal)
    cx.execute(f"UPDATE principal_policy SET {','.join(sets)} WHERE principal=?", vals)
    cx.commit()
    return get_policy(cx, principal)

def nonterminal_for_principal(cx, principal):

    qmarks = ",".join("?" for _ in TERMINAL)
    rows = cx.execute(
        f"SELECT * FROM jobs WHERE submitter_principal=? AND state NOT IN ({qmarks}) "
        f"ORDER BY id DESC", (principal, *TERMINAL)).fetchall()
    return [dict(r) for r in rows]

def queued_ids(cx, exclude_source=None):

    if exclude_source:
        rows = cx.execute(
            "SELECT id FROM jobs WHERE state='queued' AND (source IS NULL OR source!=?) "
            "ORDER BY id DESC", (exclude_source,)).fetchall()
    else:
        rows = cx.execute("SELECT id FROM jobs WHERE state='queued' ORDER BY id DESC").fetchall()
    return [r["id"] for r in rows]

def jobs_for_principal(cx, principal, state=None, via_device=None, limit=50, before_id=None):

    where = ["submitter_principal=?"]
    vals = [principal]
    if state:
        where.append("state=?"); vals.append(state)
    if via_device:
        where.append("via_device=?"); vals.append(via_device)
    if before_id is not None:
        try:
            where.append("id<?"); vals.append(int(before_id))
        except (TypeError, ValueError):
            return []
    vals.append(limit)
    sql = ("SELECT * FROM jobs WHERE " + " AND ".join(where) +
           " ORDER BY id DESC LIMIT ?")
    return [dict(r) for r in cx.execute(sql, vals).fetchall()]

def count_inflight_via_device(cx, principal, via_device):

    if not principal or not via_device:
        return 0
    qmarks = ",".join("?" for _ in TERMINAL)
    row = cx.execute(
        f"SELECT COUNT(*) c FROM jobs WHERE submitter_principal=? AND via_device=? "
        f"AND state NOT IN ({qmarks})",
        (principal, via_device, *TERMINAL)).fetchone()
    return int(row["c"]) if row else 0

def outputs_for_principal(cx, principal, since=None, limit=200):

    cols = {r["name"] for r in cx.execute("PRAGMA table_info(jobs)")}
    extra = [c for c in ("result_uri", "result_hash", "retention_until") if c in cols]
    sel = "id,task_type,state,finished_at" + ("," + ",".join(extra) if extra else "")
    where = ["submitter_principal=?", "state IN ('done','failed','cancelled','timeout')"]
    vals = [principal]
    if since is not None:
        where.append("finished_at>=?"); vals.append(float(since))
    vals.append(limit)
    sql = (f"SELECT {sel} FROM jobs WHERE " + " AND ".join(where) +
           " ORDER BY id DESC LIMIT ?")
    return [dict(r) for r in cx.execute(sql, vals).fetchall()]

def resolve_identity(cx, method, selector):

    r = cx.execute(
        "SELECT principal, verified FROM identities WHERE method=? AND selector=?",
        (method, str(selector))).fetchone()
    if not r:
        return (None, None)

    try:
        cx.execute("UPDATE identities SET last_seen=? WHERE method=? AND selector=?",
                   (time.time(), method, str(selector)))
        cx.commit()
    except sqlite3.OperationalError:
        pass
    return (r["principal"], r["verified"])

def bind_identity(cx, method, selector, principal, verified=0):

    now = time.time()
    cx.execute(
        "INSERT INTO identities(method,selector,principal,verified,bound_at,last_seen) "
        "VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(method,selector) DO UPDATE SET principal=excluded.principal, "
        "verified=excluded.verified, last_seen=excluded.last_seen",
        (method, str(selector), principal, int(verified), now, now))
    cx.commit()

def ensure_principal(cx, name, uid=None, kind="user", note=None):

    cx.execute("INSERT OR IGNORE INTO principals(name,uid,kind,note) VALUES(?,?,?,?)",
               (name, uid, kind, note))
    cx.commit()

def grant_cap(cx, principal, cap):

    if not cx.execute("SELECT 1 FROM grants WHERE principal=? AND cap=?",
                      (principal, cap)).fetchone():
        cx.execute("INSERT INTO grants(principal,cap) VALUES(?,?)", (principal, cap))
        cx.commit()

def set_workspace(cx, job_id, workspace_path):

    cx.execute("UPDATE jobs SET workspace_path=? WHERE id=?", (workspace_path, job_id))
    cx.commit()

def set_record(cx, job_id, record_commit=None, result_hash=None):

    sets, vals = [], []
    if record_commit is not None:
        sets.append("record_commit=?"); vals.append(record_commit)
    if result_hash is not None:
        sets.append("result_hash=?"); vals.append(result_hash)
    if not sets:
        return
    vals.append(job_id)
    cx.execute(f"UPDATE jobs SET {','.join(sets)} WHERE id=?", vals)
    cx.commit()

def finalize(cx, job_id, state, exit_code, record_ok, record_commit=None,
             result_uri=None, result_hash=None, retention_until=None, fail_class=None):

    if state == "done" and not record_ok:
        raise ValueError("finalize: refusing to publish done with record_ok=0 "
                         "(no-done-without-record)")

    cx.execute(
        "UPDATE jobs SET state=?, exit_code=?, record_ok=?, record_commit=?, "
        "result_uri=?, result_hash=?, retention_until=?, finished_at=?, fail_class=? WHERE id=?",
        (state, exit_code, 1 if record_ok else 0, record_commit, result_uri, result_hash,
         retention_until, time.time(), None if state == "done" else fail_class, job_id))
    cx.commit()

def mark_replicated(cx, job_id, result_uri=None, result_hash=None):

    now = time.time()
    sets = ["replicated=1", "replicated_at=?"]
    vals = [now]
    if result_uri is not None:
        sets.append("result_uri=?"); vals.append(result_uri)
    if result_hash is not None:
        sets.append("result_hash=?"); vals.append(result_hash)
    vals.append(job_id)
    cx.execute(f"UPDATE jobs SET {','.join(sets)} WHERE id=?", vals)
    cx.commit()

def mark_workspace_gcd(cx, job_id):

    cx.execute("UPDATE jobs SET workspace_path=NULL WHERE id=?", (job_id,))
    cx.commit()

def replication_pending(cx, limit=200):

    return [dict(r) for r in cx.execute(
        "SELECT * FROM jobs WHERE record_ok=1 AND replicated=0 "
        "AND workspace_path IS NOT NULL AND state IN ('done','failed','timeout') "
        "ORDER BY id LIMIT ?", (limit,)).fetchall()]

def gc_candidates(cx, now=None, limit=500):

    now = now or time.time()
    return [dict(r) for r in cx.execute(
        "SELECT * FROM jobs WHERE record_ok=1 AND replicated=1 "
        "AND workspace_path IS NOT NULL AND retention_until IS NOT NULL "
        "AND retention_until < ? ORDER BY retention_until LIMIT ?", (now, limit)).fetchall()]

def disk_pressure_victims(cx, limit=50):

    return [dict(r) for r in cx.execute(
        "SELECT * FROM jobs WHERE replicated=1 AND workspace_path IS NOT NULL "
        "AND state IN ('done','failed','timeout') "
        "ORDER BY COALESCE(finished_at, id) ASC LIMIT ?", (limit,)).fetchall()]

def whereis(cx, job_id, principal=None, scope_all=False):

    j = get(cx, job_id, principal=principal, scope_all=scope_all)
    if not j:
        return None
    gone = j.get("workspace_path") is None
    replicated = bool(j.get("replicated"))
    if not gone:
        where = j.get("workspace_path")
        bytes_status = "local"
    elif replicated and j.get("result_uri"):
        where = j.get("result_uri")
        bytes_status = "off-box (replicated)"
    else:
        where = None
        bytes_status = "gc'd, replica-unverified"
    return {
        "id": j["id"],
        "status": j["state"],
        "what": j.get("task_type") or "(raw)",
        "when": j.get("finished_at") or j.get("submitted_at"),
        "who": j.get("submitter_principal"),
        "record_ok": bool(j.get("record_ok")),
        "record_commit": j.get("record_commit"),
        "replicated": replicated,
        "result_uri": j.get("result_uri"),
        "result_hash": j.get("result_hash"),
        "bytes": where,
        "bytes_status": bytes_status,
        "workspace_present": not gone,
    }
