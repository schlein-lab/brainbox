
from __future__ import annotations
import os, time, shlex, shutil, subprocess, threading, itertools

class Session:

    _ids = itertools.count(1)

    def __init__(self, model: str, cmd_tmpl: str, env: dict | None = None):
        self.id = next(self._ids)
        self.model = model
        self.cmd_tmpl = cmd_tmpl
        self.env = env
        self.created = time.time()
        self.last_used = 0.0
        self.served = 0
        self.busy = False
        self.dedicated = False
        self.alive = True
        self._lock = threading.Lock()
        self._last_peek = ""

    def run(self, prompt: str, timeout: int) -> dict:

        argv = shlex.split(self.cmd_tmpl.format(model=self.model)) + [prompt]
        env = dict(os.environ)
        if self.env:
            env.update(self.env)

        if argv and not os.path.isabs(argv[0]):
            _search = (env.get("PATH", "") or os.defpath) + os.pathsep + os.path.expanduser("~/.local/bin")
            _found = shutil.which(argv[0], path=_search)
            if _found:
                argv[0] = _found
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                               stdin=subprocess.DEVNULL, env=env)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"timeout after {timeout}s"}
        except FileNotFoundError:
            return {"ok": False, "error": f"backend not found: {argv[0]}"}
        out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
        self._last_peek = out[-2000:]
        self.last_used = time.time()
        self.served += 1
        if r.returncode != 0:
            return {"ok": False, "error": f"backend rc={r.returncode}: {out.strip()[:200]}",
                    "raw": out}
        return {"ok": True, "text": r.stdout, "raw": out}

    def peek(self) -> str:
        return self._last_peek

    def kill(self):
        self.alive = False

class Pool:

    def __init__(self, size: int, model: str, cmd_tmpl: str, env: dict | None = None,
                 spawn_fn=None):
        self.size = max(1, size)
        self.model = model
        self.cmd_tmpl = cmd_tmpl
        self.env = env
        self._spawn_fn = spawn_fn or (lambda m: Session(m, cmd_tmpl, env))
        self._sessions: list[Session] = []
        self._sem = threading.BoundedSemaphore(self.size)
        self._lock = threading.Lock()
        self.stats = {"spawned": 0, "served": 0, "errors": 0, "auth_errors": 0,
                      "waiting": 0, "active": 0, "dedicated_active": 0}

    def spawn(self) -> Session:
        s = self._spawn_fn(self.model)
        with self._lock:
            self._sessions.append(s)
            self.stats["spawned"] += 1
        return s

    def _acquire_session(self, dedicated: bool) -> Session:

        with self._lock:
            alive = [s for s in self._sessions if s.alive]
            free = [s for s in alive if not s.busy and not s.dedicated]
            if free:

                s = max(free, key=lambda x: x.served) if not dedicated else free[0]
            elif len(alive) < self.size:
                s = self._spawn_inline()
            else:

                s = self._spawn_inline()
            s.busy = True
            s.dedicated = dedicated
            return s

    def _spawn_inline(self) -> Session:
        s = self._spawn_fn(self.model)
        self._sessions.append(s)
        self.stats["spawned"] += 1
        return s

    def _release(self, s: Session, dedicated: bool):
        with self._lock:
            s.busy = False
            s.dedicated = False

            alive = [x for x in self._sessions if x.alive]
            if len(alive) > self.size:

                extras = sorted([x for x in alive if not x.busy],
                                key=lambda x: x.last_used)[: len(alive) - self.size]
                for x in extras:
                    x.kill()
            self._sessions = [x for x in self._sessions if x.alive]

    def ask(self, prompt: str, timeout: int, kind: str = "loose") -> dict:

        dedicated = (kind == "dedicated")
        with self._lock:
            self.stats["waiting"] += 1
        self._sem.acquire()
        with self._lock:
            self.stats["waiting"] -= 1
            self.stats["active"] += 1
            if dedicated:
                self.stats["dedicated_active"] += 1
        s = self._acquire_session(dedicated)
        try:
            res = s.run(prompt, timeout)
            res["session"] = s.id
            res["routing"] = kind
            return res
        finally:
            self._release(s, dedicated)
            self._sem.release()
            with self._lock:
                self.stats["active"] -= 1
                self.stats["served"] += 1
                if dedicated:
                    self.stats["dedicated_active"] -= 1
                if not res.get("ok"):
                    self.stats["errors"] += 1
                    if res.get("auth"):
                        self.stats["auth_errors"] += 1

    def peek(self, session_id: int | None = None) -> dict:
        with self._lock:
            ss = [s for s in self._sessions if s.alive]
        if session_id is not None:
            for s in ss:
                if s.id == session_id:
                    return {"ok": True, "session": s.id, "tail": s.peek(),
                            "busy": s.busy, "served": s.served}
            return {"ok": False, "error": f"no session {session_id}"}
        return {"ok": True, "sessions": [
            {"id": s.id, "busy": s.busy, "dedicated": s.dedicated, "served": s.served,
             "age": round(time.time() - s.created, 1)} for s in ss]}

    def kill(self, session_id: int | None = None) -> dict:
        with self._lock:
            ss = list(self._sessions)
        n = 0
        for s in ss:
            if session_id is None or s.id == session_id:
                if s.alive:
                    s.kill(); n += 1
        with self._lock:
            self._sessions = [x for x in self._sessions if x.alive]
        return {"ok": True, "killed": n}

    def info(self) -> dict:
        with self._lock:
            alive = [s for s in self._sessions if s.alive]
            return {"size": self.size, "alive": len(alive),
                    "busy": sum(1 for s in alive if s.busy), **self.stats}

SLOT_TENTHS = 10
LOOSE_PER_SLOT = 4

def lease_tenths(weight: int, kind: str) -> int:

    w = max(0, int(weight or 0))
    if w == 0:
        return 0
    if kind == "dedicated":
        return w * SLOT_TENTHS

    return -(-w * SLOT_TENTHS // LOOSE_PER_SLOT)

class LeaseTable:

    LEASE_TTL_S = 120.0

    def __init__(self, pool_slots: int, lease_ttl_s: float | None = None):
        self.pool_slots = max(1, int(pool_slots))
        self.lease_ttl_s = float(lease_ttl_s) if lease_ttl_s else self.LEASE_TTL_S
        self._leases: dict = {}
        self._lock = threading.Lock()

    def _leased_tenths(self) -> int:
        return sum(l["tenths"] for l in self._leases.values())

    def lease(self, job_id, weight: int, kind: str = "loose") -> dict:

        want = lease_tenths(weight, kind)
        cap = self.pool_slots * SLOT_TENTHS
        with self._lock:
            if job_id in self._leases:
                self._leases[job_id]["at"] = time.time()
                return {"ok": True, "already": True, **self._snapshot_locked()}
            if want == 0:
                return {"ok": True, "noop": True, **self._snapshot_locked()}
            if self._leased_tenths() + want > cap:
                return {"ok": False, "blocked": True,
                        "error": "llm pool saturated", **self._snapshot_locked()}
            self._leases[job_id] = {"tenths": want, "weight": int(weight),
                                    "kind": kind, "at": time.time()}
            return {"ok": True, "leased": True, **self._snapshot_locked()}

    def release(self, job_id) -> dict:

        with self._lock:
            had = self._leases.pop(job_id, None)
            return {"ok": True, "released": bool(had), **self._snapshot_locked()}

    def sweep_expired(self, now: float | None = None) -> dict:

        now = time.time() if now is None else now
        with self._lock:
            expired = [jid for jid, l in self._leases.items()
                       if now - l.get("at", now) > self.lease_ttl_s]
            for jid in expired:
                self._leases.pop(jid, None)
            return {"expired": expired, **self._snapshot_locked()}

    def _snapshot_locked(self) -> dict:
        cap = self.pool_slots * SLOT_TENTHS
        leased = self._leased_tenths()
        return {"llm_pool": self.pool_slots,
                "llm_in_use": round(leased / SLOT_TENTHS, 3),
                "llm_free": round((cap - leased) / SLOT_TENTHS, 3),
                "leases": len(self._leases)}

    def headroom(self) -> dict:

        with self._lock:
            return self._snapshot_locked()

    def held(self) -> dict:
        with self._lock:
            return {jid: dict(l) for jid, l in self._leases.items()}
