
from __future__ import annotations
import sqlite3, os, time, json, fnmatch

ALLOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS egress_allow (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  host        TEXT NOT NULL,        -- exact host or a glob (e.g. *.amazonaws.com)
  port        INTEGER NOT NULL,     -- 0 = any port
  principal   TEXT NOT NULL,        -- the requester this entry is scoped to (* = any)
  task_type   TEXT NOT NULL,        -- the task_type this entry is scoped to (* = any)
  note        TEXT,
  approved_by TEXT,                 -- the human principal who approved this entry
  created_at  REAL NOT NULL,
  UNIQUE(host, port, principal, task_type)
);
CREATE INDEX IF NOT EXISTS idx_egress_lookup ON egress_allow(principal, task_type);

CREATE TABLE IF NOT EXISTS egress_pending (
  nonce       TEXT PRIMARY KEY,     -- the approval nonce (the brain cannot mint it)
  host        TEXT NOT NULL,
  port        INTEGER NOT NULL,
  principal   TEXT NOT NULL,
  task_type   TEXT NOT NULL,
  job_id      INTEGER,
  state       TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | denied
  created_at  REAL NOT NULL,
  resolved_at REAL
);

CREATE TABLE IF NOT EXISTS egress_log (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         REAL NOT NULL,
  host       TEXT, port INTEGER, principal TEXT, task_type TEXT, job_id INTEGER,
  decision   TEXT,                  -- allow | deny | propose
  reason     TEXT
);
"""

def connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    cx = sqlite3.connect(path, timeout=10, check_same_thread=False)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA busy_timeout=8000")
    cx.executescript(ALLOW_SCHEMA)
    cx.commit()
    return cx

def add_allow(cx, host, port, principal, task_type, note=None, approved_by=None):

    cx.execute(
        "INSERT OR IGNORE INTO egress_allow(host,port,principal,task_type,note,approved_by,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (host, int(port), principal, task_type, note, approved_by, time.time()))
    cx.commit()
    r = cx.execute("SELECT id FROM egress_allow WHERE host=? AND port=? AND principal=? "
                   "AND task_type=?", (host, int(port), principal, task_type)).fetchone()
    return r["id"] if r else None

def _match(entry_val, want, *, glob=False):

    if entry_val == "*":
        return True
    if glob:
        return fnmatch.fnmatch(want, entry_val)
    return entry_val == want

def is_allowed(cx, host, port, principal, task_type):

    rows = cx.execute(
        "SELECT host,port,principal,task_type FROM egress_allow").fetchall()
    for r in rows:
        if (_match(r["host"], host, glob=True)
                and (r["port"] == 0 or r["port"] == int(port))
                and _match(r["principal"], principal)
                and _match(r["task_type"], task_type)):
            return True
    return False

def log_decision(cx, host, port, principal, task_type, job_id, decision, reason=None):
    cx.execute("INSERT INTO egress_log(ts,host,port,principal,task_type,job_id,decision,reason)"
               " VALUES(?,?,?,?,?,?,?,?)",
               (time.time(), host, int(port), principal, task_type, job_id, decision, reason))
    cx.commit()

def propose(cx, host, port, principal, task_type, job_id, nonce):

    existing = cx.execute(
        "SELECT nonce FROM egress_pending WHERE host=? AND port=? AND principal=? AND task_type=? "
        "AND state='pending'", (host, int(port), principal, task_type)).fetchone()
    if existing:
        return existing["nonce"], False
    cx.execute(
        "INSERT INTO egress_pending(nonce,host,port,principal,task_type,job_id,state,created_at)"
        " VALUES(?,?,?,?,?,?, 'pending', ?)",
        (nonce, host, int(port), principal, task_type, job_id, time.time()))
    cx.commit()
    return nonce, True

def resolve_proposal(cx, nonce, decision, approved_by):

    row = cx.execute("SELECT * FROM egress_pending WHERE nonce=?", (nonce,)).fetchone()
    if not row:
        return {"ok": False, "error": "unknown or expired nonce"}
    if row["state"] != "pending":

        if (decision == "approve" and row["state"] == "approved") or \
           (decision == "deny" and row["state"] == "denied"):
            return {"ok": True, "decision": row["state"], "idempotent": True}
        return {"ok": False, "error": f"already {row['state']}; cannot {decision}"}
    if decision == "approve":
        aid = add_allow(cx, row["host"], row["port"], row["principal"], row["task_type"],
                        note=f"approved from egress proposal (job {row['job_id']})",
                        approved_by=approved_by)
        cx.execute("UPDATE egress_pending SET state='approved', resolved_at=? WHERE nonce=?",
                   (time.time(), nonce))
        cx.commit()
        return {"ok": True, "decision": "approved", "allow_id": aid}
    if decision in ("deny", "reject"):
        cx.execute("UPDATE egress_pending SET state='denied', resolved_at=? WHERE nonce=?",
                   (time.time(), nonce))
        cx.commit()
        return {"ok": True, "decision": "denied"}
    return {"ok": False, "error": f"unknown decision {decision}"}

DENY, ALLOW, PROPOSE = "deny", "allow", "propose"

class EgressBroker:

    def __init__(self, allow_db, upstream_fn=None, cred_for=None, propose_fn=None, nonce_fn=None):
        self.db = allow_db
        self._upstream = upstream_fn
        self._cred_for = cred_for
        self._propose = propose_fn
        import secrets as _s
        self._nonce = nonce_fn or (lambda: _s.token_urlsafe(18))

    def check_connect(self, host, port, principal, task_type, job_id=None):

        if is_allowed(self.db, host, port, principal, task_type):
            log_decision(self.db, host, port, principal, task_type, job_id, ALLOW)
            return (ALLOW, {"host": host, "port": port})

        nonce = self._nonce()
        nonce, created = propose(self.db, host, port, principal, task_type, job_id, nonce)
        if created and self._propose:
            try:
                self._propose(host, port, principal, task_type, job_id, nonce)
            except Exception:
                pass
        log_decision(self.db, host, port, principal, task_type, job_id, PROPOSE,
                     reason=f"new destination -> approval-request (nonce {nonce})")
        return (PROPOSE, {"nonce": nonce, "host": host, "port": port, "new": created})

    def connect(self, host, port, principal, task_type, job_id=None):

        decision, detail = self.check_connect(host, port, principal, task_type, job_id)
        if decision != ALLOW:
            return {"ok": False, "decision": decision, **detail,
                    "error": f"egress to {host}:{port} not allowed (proposed for approval)"
                    if decision == PROPOSE else f"egress to {host}:{port} denied"}
        if self._upstream is None:
            return {"ok": True, "decision": ALLOW, "conn": None, "host": host, "port": port}
        cred = self._cred_for(host, port, principal, task_type) if self._cred_for else None
        try:
            conn = self._upstream(host, port, cred)
        except Exception as e:
            return {"ok": False, "decision": ALLOW, "error": f"upstream failed: {e}"}
        return {"ok": True, "decision": ALLOW, "conn": conn, "host": host, "port": port}

def tcp_upstream(host, port, cred=None, *, timeout=15):

    import socket as _socket
    return _socket.create_connection((host, int(port)), timeout=timeout)

def splice(client_sock, upstream_sock, *, chunk=65536):

    import select
    socks = [client_sock, upstream_sock]
    counts = {id(client_sock): 0, id(upstream_sock): 0}
    try:
        while True:
            r, _, x = select.select(socks, [], socks, 60)
            if x:
                break
            if not r:
                break
            done = False
            for s in r:
                try:
                    data = s.recv(chunk)
                except OSError:
                    done = True
                    break
                if not data:
                    done = True
                    break
                other = upstream_sock if s is client_sock else client_sock
                try:
                    other.sendall(data)
                except OSError:
                    done = True
                    break
                counts[id(s)] += len(data)
            if done:
                break
    finally:
        for s in socks:
            try:
                s.shutdown(_SHUT_RDWR)
            except OSError:
                pass
    return (counts[id(client_sock)], counts[id(upstream_sock)])

import socket as _socket_mod
_SHUT_RDWR = _socket_mod.SHUT_RDWR

NETNS_PREFIX = "pn-egress"
BRIDGE = "pn-egbr0"

def netns_name(tier: str) -> str:
    return f"{NETNS_PREFIX}-{tier}"

def plan_netns(tier: str, *, proxy_sock="/run/portioneer/egress.sock",
               host_veth=None, ns_veth="veth0") -> dict:

    ns = netns_name(tier)
    hveth = host_veth or f"pnveth-{tier}"
    setup = [
        ["ip", "netns", "add", ns],
        ["ip", "link", "add", hveth, "type", "veth", "peer", "name", ns_veth],
        ["ip", "link", "set", ns_veth, "netns", ns],

        ["ip", "netns", "exec", ns, "ip", "link", "set", "lo", "up"],
        ["ip", "netns", "exec", ns, "ip", "link", "set", ns_veth, "up"],
    ]

    nft = (
        f"table inet {NETNS_PREFIX} {{\n"
        f"  chain output {{\n"
        f"    type filter hook output priority 0; policy drop;\n"
        f"    oifname \"lo\" accept;\n"
        f"    counter comment \"default-deny: no ambient WAN; egress only via proxy unix socket\"\n"
        f"  }}\n"
        f"}}\n"
    )
    mount = ["mount", "--bind", proxy_sock, f"/run/netns-{ns}/egress.sock"]
    teardown = [
        ["ip", "netns", "del", ns],
        ["ip", "link", "del", hveth],
    ]
    return {"tier": tier, "netns": ns, "setup": setup, "nft": nft, "mount": mount,
            "teardown": teardown,
            "summary": f"netns={ns} default-DENY (policy drop) + proxy-only egress via {proxy_sock} "
                       f"[PRIVILEGED: rootless --user cannot create a netns]"}
