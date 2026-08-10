#!/usr/bin/env python3

def apikeys_scope_ok(scopes, path):

    if not scopes:
        return True
    for s in scopes:
        if path == s or path.startswith(s.rstrip("/") + "/"):
            return True
    return False

def decide(*, ms_owner, action, caller, path="/api/metasession/ms1/tasks"):

    is_admin = caller.get("kind") == "admin"

    if caller["kind"] in ("session", "admin"):
        principal = caller.get("uid", "owner")
    elif caller["kind"] == "key":
        principal = caller.get("uid", "owner")
    else:
        principal = "owner"
    authed = caller["kind"] in ("session", "admin")
    apikey_scoped = caller["kind"] == "key" and apikeys_scope_ok(caller.get("scopes"), path)
    meta_owner_ok = (ms_owner == principal) or is_admin
    return bool(meta_owner_ok and (authed or (apikey_scoped and action == "tasks")))

CASES = [

    ("owner session appends tasks",
     dict(ms_owner="alice", action="tasks", caller={"kind": "session", "uid": "alice"}), True),
    ("owner session pauses",
     dict(ms_owner="alice", action="pause", caller={"kind": "session", "uid": "alice"}), True),
    ("admin appends to foreign ms",
     dict(ms_owner="alice", action="tasks", caller={"kind": "admin"}), True),
    ("owner's own scoped key appends tasks (legit node)",
     dict(ms_owner="alice", action="tasks",
          caller={"kind": "key", "uid": "alice", "scopes": ["/api/metasession"]}), True),
    ("owner's own key, no scopes (full principal) appends tasks",
     dict(ms_owner="alice", action="tasks",
          caller={"kind": "key", "uid": "alice", "scopes": []}), True),

    ("FOREIGN key (wrong uid) tries to inject tasks",
     dict(ms_owner="alice", action="tasks",
          caller={"kind": "key", "uid": "mallory", "scopes": ["/api/metasession"]}), False),
    ("owner's key scoped ONLY to /api/tts (scope bypass attempt)",
     dict(ms_owner="alice", action="tasks",
          caller={"kind": "key", "uid": "alice", "scopes": ["/api/tts"]}), False),
    ("owner's key tries pause (keys may only drive tasks)",
     dict(ms_owner="alice", action="pause",
          caller={"kind": "key", "uid": "alice", "scopes": ["/api/metasession"]}), False),
    ("owner's key tries delete",
     dict(ms_owner="alice", action="delete",
          caller={"kind": "key", "uid": "alice", "scopes": ["/api/metasession"]}), False),
    ("foreign session (logged-in non-owner)",
     dict(ms_owner="alice", action="tasks", caller={"kind": "session", "uid": "bob"}), False),
    ("unauthenticated",
     dict(ms_owner="alice", action="tasks", caller={"kind": "none"}), False),
    ("foreign key scoped to metasession but wrong owner (the ORIGINAL exploit)",
     dict(ms_owner="alice", action="tasks",
          caller={"kind": "key", "uid": "eve", "scopes": ["/api/metasession", "/api/queue"]}), False),
]

if __name__ == "__main__":
    ok = 0
    for name, kw, expected in CASES:
        got = decide(**kw)
        verdict = "PASS" if got == expected else "FAIL"
        if got == expected:
            ok += 1
        print("  [%s] %-55s expect=%s got=%s" % (verdict, name, expected, got))
    print("\n%d/%d cases correct — %s" % (ok, len(CASES),
          "METASESSION_AUTHZ_PROVEN" if ok == len(CASES) else "NEEDS_LOOK"))
