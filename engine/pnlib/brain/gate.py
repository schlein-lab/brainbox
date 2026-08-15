
from __future__ import annotations
import json, time, hashlib

ALLOWED_OPS = ("submit", "message", "propose", "steer", "sleep")

FORBIDDEN_OPS = ("shell", "raw", "exec", "eval", "system", "spawn", "approve", "deny",
                 "egress-approve", "bind", "flash")

DEFAULT_TASK_TYPE_ALLOWLIST = (
    "echo.test", "sleep.test", "review.post", "net.discover", "summary.notify",
)

_COMMON_FIELDS = {"op": (str, True), "reason": (str, False)}
_OP_FIELDS = {

    "submit": {
        "task_type": (str, True),
        "params": (dict, False),
        "tag": (str, False),
        "reply_to": (str, False),
        "group_id": (str, False),
    },

    "submit-dag": {
        "nodes": (list, True),
        "group_id": (str, False),
        "tag": (str, False),
    },

    "message": {
        "to": (str, True),
        "text": (str, True),
    },

    "propose": {
        "kind": (str, True),
        "summary": (str, True),
        "task_type": (str, False),
        "params": (dict, False),
        "detail": (dict, False),
    },

    "steer": {
        "job_id": (int, True),
        "input": (str, True),
    },

    "sleep": {
        "seconds": (int, False),
    },
}

ALLOWED_OPS = ALLOWED_OPS + ("submit-dag",)

class ValidationError(Exception):
    pass

def validate_action(action, *, task_type_allowlist=DEFAULT_TASK_TYPE_ALLOWLIST):

    if not isinstance(action, dict):
        raise ValidationError(f"action must be a JSON object, got {type(action).__name__}")
    op = action.get("op")
    if not isinstance(op, str):
        raise ValidationError("action missing a string `op`")
    if op in FORBIDDEN_OPS:
        raise ValidationError(f"forbidden op {op!r}: the brain has no such capability "
                              f"(closed-world: no shell/raw/exec/self-approve)")
    if op not in ALLOWED_OPS:
        raise ValidationError(f"unknown op {op!r}: not in the closed enum {sorted(set(ALLOWED_OPS))}")

    spec = dict(_COMMON_FIELDS)
    spec.update(_OP_FIELDS[op])

    extra = set(action) - set(spec)
    if extra:
        raise ValidationError(f"op {op!r}: unknown field(s) {sorted(extra)} "
                              f"(closed-world additionalProperties:false)")

    for fname, (ftyp, required) in spec.items():
        if fname not in action:
            if required:
                raise ValidationError(f"op {op!r}: missing required field {fname!r}")
            continue
        val = action[fname]
        if ftyp is int and isinstance(val, bool):
            raise ValidationError(f"op {op!r}: field {fname!r} must be an int, not a bool")
        if not isinstance(val, ftyp):
            raise ValidationError(f"op {op!r}: field {fname!r} must be {ftyp.__name__}, "
                                  f"got {type(val).__name__}")

    tt = action.get("task_type")
    if op in ("submit",) and tt not in task_type_allowlist:
        raise ValidationError(f"submit task_type {tt!r} not in the brain's closed allowlist "
                              f"{sorted(task_type_allowlist)}")
    if op == "propose" and tt is not None and tt not in task_type_allowlist:
        raise ValidationError(f"propose task_type {tt!r} not in the brain's closed allowlist "
                              f"{sorted(task_type_allowlist)}")
    if op == "submit-dag":
        nodes = action["nodes"]
        if not nodes:
            raise ValidationError("submit-dag: `nodes` must be non-empty")
        for i, node in enumerate(nodes):
            if not isinstance(node, dict):
                raise ValidationError(f"submit-dag node {i} must be an object")
            ntt = node.get("task_type")
            if ntt not in task_type_allowlist:
                raise ValidationError(f"submit-dag node {i}: task_type {ntt!r} not in the "
                                      f"closed allowlist {sorted(task_type_allowlist)}")
            for d in (node.get("deps") or []):
                if not isinstance(d, int) or isinstance(d, bool) or d < 0 or d >= i:
                    raise ValidationError(f"submit-dag node {i}: local dep {d!r} must reference an "
                                          f"EARLIER node (0..{i-1}) — forward-only / acyclic")
    return dict(action)

def parse_action(text, *, task_type_allowlist=DEFAULT_TASK_TYPE_ALLOWLIST):

    raw = _extract_json_object(text)
    if raw is None:
        raise ValidationError("reasoning face emitted no parseable JSON object")
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError) as e:
        raise ValidationError(f"reasoning face emitted invalid JSON: {e}")
    return validate_action(obj, task_type_allowlist=task_type_allowlist)

def _extract_json_object(text):

    if not isinstance(text, str):
        return None
    s = text.strip()

    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    depth, start, in_str, esc = 0, None, False, False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                return s[start:i + 1]
    return None

class ActingAsViolation(Exception):
    pass

def plan_dispose(action, *, acting_as, reply_to_default=None):

    op = action["op"]

    for forbidden in ("principal", "acting_as", "on_behalf_of", "submitter", "_method",
                      "_selector", "uid", "_peer_uid"):
        if forbidden in action:
            raise ActingAsViolation(
                f"op {op!r} attempted to name a principal via {forbidden!r}; the brain may act "
                f"ONLY as its bound acting-as principal {acting_as!r} (confused-deputy refused)")

    if op == "sleep":
        return {"kind": "sleep", "seconds": int(action.get("seconds") or 0)}

    if op == "submit":
        req = {"verb": "submit", "task_type": action["task_type"]}
        if action.get("params"):
            req["params"] = action["params"]
        if action.get("tag"):
            req["tag"] = action["tag"]
        if action.get("group_id"):
            req["group_id"] = action["group_id"]
        rt = action.get("reply_to") or reply_to_default
        if rt:
            req["reply_to"] = rt
        return {"kind": "pnd", "request": req}

    if op == "submit-dag":
        req = {"verb": "submit-dag", "nodes": action["nodes"]}
        if action.get("group_id"):
            req["group_id"] = action["group_id"]
        return {"kind": "pnd", "request": req}

    if op == "message":

        req = {"verb": "submit", "task_type": "summary.notify",
               "params": {"msg": action["text"]}, "reply_to": reply_to_default,
               "tag": "brain:message"}
        return {"kind": "pnd", "request": req}

    if op == "propose":

        tt = action.get("task_type") or "net.discover"
        req = {"verb": "submit", "task_type": tt,
               "params": action.get("params") or {},
               "approval": "pre", "needs_confirmation": True,
               "tag": f"brain:propose:{action['kind']}"}

        req["_propose"] = {"kind": action["kind"], "summary": action["summary"],
                           "detail": action.get("detail")}
        return {"kind": "pnd", "request": req}

    if op == "steer":
        return {"kind": "pnd", "request": {"verb": "steer", "id": action["job_id"],
                                           "input": action["input"]}}

    return {"kind": "noop", "reason": f"no dispose plan for op {op!r}"}

def _now(now=None):
    return time.time() if now is None else now

def install_timer(cx, name, interval_s, action_json, *, principal="brain",
                  enabled=1, first_fire=None, now=None):

    now = _now(now)
    nf = float(first_fire) if first_fire is not None else now
    payload = action_json if isinstance(action_json, str) else json.dumps(action_json)
    cx.execute(
        "INSERT INTO brain_timers(name,principal,interval_s,next_fire,action_json,enabled,"
        "created_at,last_fired) VALUES(?,?,?,?,?,?,?,NULL) "
        "ON CONFLICT(name) DO UPDATE SET interval_s=excluded.interval_s, "
        "action_json=excluded.action_json, enabled=excluded.enabled, principal=excluded.principal",
        (name, principal, float(interval_s), nf, payload, int(enabled), now))
    cx.commit()
    r = cx.execute("SELECT id FROM brain_timers WHERE name=?", (name,)).fetchone()
    return r["id"]

def due_timers(cx, now=None):

    now = _now(now)
    return [dict(r) for r in cx.execute(
        "SELECT * FROM brain_timers WHERE enabled=1 AND next_fire<=? ORDER BY next_fire ASC",
        (now,)).fetchall()]

def advance_timer(cx, timer_id, now=None):

    now = _now(now)
    r = cx.execute("SELECT interval_s FROM brain_timers WHERE id=?", (timer_id,)).fetchone()
    if not r:
        return
    interval = r["interval_s"] or 0
    if interval > 0:
        cx.execute("UPDATE brain_timers SET last_fired=?, next_fire=? WHERE id=?",
                   (now, now + interval, timer_id))
    else:
        cx.execute("UPDATE brain_timers SET last_fired=?, enabled=0 WHERE id=?", (now, timer_id))
    cx.commit()

def set_state(cx, principal, key, value, now=None):

    now = _now(now)
    payload = value if isinstance(value, str) else json.dumps(value)
    cx.execute(
        "INSERT INTO brain_state(principal,key,value,updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(principal,key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (principal, key, payload, now))
    cx.commit()

def get_state(cx, principal, key, default=None):

    r = cx.execute("SELECT value FROM brain_state WHERE principal=? AND key=?",
                   (principal, key)).fetchone()
    if not r:
        return default
    try:
        return json.loads(r["value"])
    except (ValueError, TypeError):
        return r["value"]

def all_state(cx, principal):

    return {r["key"]: r["value"] for r in cx.execute(
        "SELECT key,value FROM brain_state WHERE principal=?", (principal,)).fetchall()}

ROTATE_MAX_AGE_S = 6 * 3600
ROTATE_MAX_TASKS = 50
ROTATE_STUCK_S = 20 * 60

def session_key(principal):

    return f"session:{principal}"

def new_session(cx, principal, *, digest=None, now=None):

    now = _now(now)
    sid = "sess-" + hashlib.sha256(f"{principal}:{now}".encode()).hexdigest()[:16]
    sess = {"session_id": sid, "principal": principal, "started_at": now,
            "tasks": 0, "last_progress_at": now, "digest": digest or "",
            "cache_prefix": sid}
    set_state(cx, principal, session_key(principal), sess, now=now)
    return sess

def get_session(cx, principal):

    return get_state(cx, principal, session_key(principal), default=None)

def bump_session(cx, principal, *, tasks_delta=0, progress=False, now=None):

    now = _now(now)
    sess = get_session(cx, principal)
    if not sess:
        sess = new_session(cx, principal, now=now)
    sess["tasks"] = int(sess.get("tasks", 0)) + int(tasks_delta)
    if progress:
        sess["last_progress_at"] = now
    set_state(cx, principal, session_key(principal), sess, now=now)
    return sess

def should_rotate(sess, *, now=None, max_age_s=ROTATE_MAX_AGE_S, max_tasks=ROTATE_MAX_TASKS,
                  stuck_s=ROTATE_STUCK_S, context_pressure=False):

    if not sess:
        return (True, "no-session")
    now = _now(now)
    if context_pressure:
        return (True, "context-pressure")
    if now - float(sess.get("started_at", now)) >= max_age_s:
        return (True, "age")
    if int(sess.get("tasks", 0)) >= max_tasks:
        return (True, "tasks")
    if now - float(sess.get("last_progress_at", now)) >= stuck_s:
        return (True, "stuck")
    return (False, None)

def compact_digest(sess, recent_jobs, intents, *, max_jobs=20):

    principal = (sess or {}).get("principal", "?")
    lines = [f"# brain digest for principal {principal}",
             f"session {sess.get('session_id', '?') if sess else '?'} "
             f"compacted_at {int(time.time())}"]
    if intents:
        lines.append("## standing intents")
        for it in intents:
            lines.append(f"- {it}")
    lines.append("## recent jobs (truth lives in the queue + Record)")
    for j in (recent_jobs or [])[:max_jobs]:
        lines.append(f"- job {j.get('id')} {j.get('task_type') or j.get('client_tag') or '?'} "
                     f"-> {j.get('state')}")
    digest = "\n".join(lines)
    return digest

def rotation_checkpoint(cx, principal, sess, recent_jobs, intents, *, reason="age", now=None):

    now = _now(now)
    digest = compact_digest(sess, recent_jobs, intents)
    set_state(cx, principal, "digest", digest, now=now)
    set_state(cx, principal, "last_checkpoint",
              {"reason": reason, "prev_session": (sess or {}).get("session_id"),
               "at": now, "tasks": (sess or {}).get("tasks", 0)}, now=now)
    return digest

def resume_plan(cx, principal, *, inflight_states=("queued", "blocked", "running",
                                                   "staged", "awaiting_approval"), now=None):

    now = _now(now)
    digest = get_state(cx, principal, "digest", default="")
    intents = get_state(cx, principal, "intents", default=[])
    prev = get_session(cx, principal)
    qmarks = ",".join("?" for _ in inflight_states)
    inflight = [r["id"] for r in cx.execute(
        f"SELECT id FROM jobs WHERE submitter_principal=? AND state IN ({qmarks}) ORDER BY id",
        [principal, *inflight_states]).fetchall()]
    timers = [dict(r) for r in cx.execute(
        "SELECT * FROM brain_timers WHERE principal=? ORDER BY id", (principal,)).fetchall()]
    fresh = new_session(cx, principal, digest=digest, now=now)
    return {"principal": principal, "digest": digest, "intents": intents,
            "inflight": inflight, "timers": timers,
            "prev_session": (prev or {}).get("session_id"),
            "fresh_session": fresh}
