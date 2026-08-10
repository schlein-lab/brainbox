
from __future__ import annotations
import json, threading, time, secrets, socket, os

class MockPnd:

    def __init__(self):
        self._lock = threading.RLock()
        self._events: list[dict] = []
        self._jobs: dict[int, dict] = {}
        self._nonces: dict[str, dict] = {}
        self._subs: list[tuple] = []
        self._next_job = 1

        self.confirm_task_types = {"intake.attach", "send.email", "device.flash"}

        self._approval_authority = {"admin"}

    TERMINAL = frozenset({"done", "failed", "cancelled", "timeout", "rejected"})

    def grant_approval_authority(self, principal):

        self._approval_authority.add(principal)

    def count_inflight_via_device(self, principal, via_device):

        if not principal or not via_device:
            return 0
        with self._lock:
            return sum(1 for j in self._jobs.values()
                       if j.get("submitter_principal") == principal
                       and j.get("via_device") == via_device
                       and j.get("state") not in self.TERMINAL)

    def _emit(self, topic: str, kind: str, job_id=None, **payload):
        with self._lock:
            eid = len(self._events) + 1
            ev = {"event_id": eid, "topic": topic, "type": kind, "ts": time.time()}
            if job_id is not None:
                ev["job_id"] = job_id
            ev.update(payload)
            self._events.append(ev)
            for topics, cb in list(self._subs):
                if topic in topics:
                    try:
                        cb(ev)
                    except Exception:
                        pass
            return ev

    def handle(self, req: dict, *, principal: str, via_device=None, ceiling_caps=None,
               is_broker=False) -> dict:
        verb = req.get("verb")
        if verb == "ping":
            return {"ok": True, "pong": True}
        if verb == "submit":
            return self._submit(req, principal, via_device=via_device or req.get("via_device"))
        if verb in ("approve", "deny", "reject"):
            return self._decide(req, verb, principal, is_broker=is_broker)
        if verb == "steer":
            return self._steer(req, principal)
        if verb == "cvm":
            return self._cvm(req)
        if verb == "replay":
            return self._replay(req, principal)
        if verb == "job":
            j = self._jobs.get(int(req.get("id", 0)))
            return {"ok": bool(j), "job": j} if j else {"ok": False, "error": "no such job"}
        if verb == "subscribe":
            return {"ok": False, "error": "subscribe is a streaming verb (use stream_subscribe)"}
        return {"ok": False, "error": f"unknown verb {verb!r}"}

    def _submit(self, req, principal, via_device=None) -> dict:
        with self._lock:
            jid = self._next_job
            self._next_job += 1
            tt = req.get("task_type", "(raw)")
            job = {"id": jid, "principal": principal, "submitter_principal": principal,
                   "task_type": tt, "params": req.get("params", {}), "state": "queued",
                   "via_device": via_device or req.get("via_device"),
                   "source": req.get("source", "lan")}
            self._jobs[jid] = job
        topic = f"user/{principal}"
        self._emit(topic, "state", job_id=jid, state="queued")

        if tt in self.confirm_task_types:
            nonce = secrets.token_urlsafe(24)
            with self._lock:
                self._nonces[nonce] = {"job_id": jid, "state": "pending"}
                job["state"] = "awaiting_approval"
                job["nonce"] = nonce
            ar = {"job_id": jid, "nonce": nonce, "task_type": tt,
                  "summary": _summary_for(tt, job["params"]),
                  "digest": _digest_for(job["params"])}
            act = _action_for(tt)
            if act:
                ar["action"] = act["text"]
                if act.get("brick"):
                    ar["brick_warning"] = act["brick"]
            self._emit(topic, "state", job_id=jid, state="awaiting_approval")
            self._emit(topic, "approval-request", job_id=jid, approval_request=ar)
        else:

            self._emit(topic, "progress", job_id=jid, progress={"done": 1, "total": 1, "msg": "ok"})
            with self._lock:
                job["state"] = "done"
            self._emit(topic, "state", job_id=jid, state="done")
            self._emit(topic, "notify", job_id=jid, text=f"done: {tt}")
        return {"ok": True, "id": jid, "pos": 0, "state": self._jobs[jid]["state"]}

    def _decide(self, req, verb, principal, is_broker=False) -> dict:
        nonce = req.get("nonce")
        with self._lock:
            rec = self._nonces.get(nonce)
            if not rec:
                return {"ok": False, "error": "unknown or expired nonce"}
            jid = rec["job_id"]
            job = self._jobs.get(jid)
            if not job:
                return {"ok": False, "error": "job gone"}
            submitter = job.get("submitter_principal", job["principal"])
            authorized = principal in self._approval_authority
            if is_broker:

                if not authorized:
                    return {"ok": False, "error": "submitter cannot self-approve "
                            "(separation of duties); device lacks approval:resolve"}

                if submitter == principal:
                    return {"ok": False, "error": "a relayed device cannot self-approve its own "
                            "submission (separation of duties)"}
            else:

                if submitter != principal:
                    return {"ok": False, "error": "not your job"}

            if rec["state"] != "pending":
                return {"ok": True, "idempotent": True, "decision": rec["state"]}
            decision = "approved" if verb in ("approve",) else "denied"
            rec["state"] = decision

        topic = f"user/{submitter}"
        if verb == "approve":
            self._emit(topic, "state", job_id=jid, state="running")
            with self._lock:
                job["state"] = "done"
            self._emit(topic, "state", job_id=jid, state="done")
            self._emit(topic, "notify", job_id=jid, text=f"approved + executed: {job['task_type']}")
        else:
            with self._lock:
                job["state"] = "rejected"
            self._emit(topic, "state", job_id=jid, state="rejected")
            self._emit(topic, "notify", job_id=jid, text=f"rejected: {job['task_type']}")
        return {"ok": True, "decision": decision, "id": jid}

    def _steer(self, req, principal) -> dict:
        jid = int(req.get("id", 0))
        job = self._jobs.get(jid)
        if not job or job["principal"] != principal:
            return {"ok": False, "error": "no such job"}
        topic = f"user/{principal}"
        self._emit(topic, "steer", job_id=jid, input=req.get("input"))

        nonce = secrets.token_urlsafe(24)
        with self._lock:
            self._nonces[nonce] = {"job_id": jid, "state": "pending"}
            job["state"] = "awaiting_approval"
            job["nonce"] = nonce
        ar = {"job_id": jid, "nonce": nonce, "task_type": job["task_type"],
              "summary": _summary_for(job["task_type"], job["params"]) + " (revised)",
              "digest": f"revised per: {req.get('input')!r}"}
        self._emit(topic, "state", job_id=jid, state="awaiting_approval")
        self._emit(topic, "approval-request", job_id=jid, approval_request=ar)
        return {"ok": True, "id": jid, "restaged": True}

    def _cvm(self, req) -> dict:
        jid = int(req.get("id", 0))
        job = self._jobs.get(jid)
        if not job:
            return {"ok": False, "error": "no such job"}
        cvm = {"id": jid, "state": job["state"], "task_type": job["task_type"]}
        if job.get("nonce") and self._nonces.get(job["nonce"], {}).get("state") == "pending":
            cvm["approval_state"] = "pending"
            cvm["approval_request"] = {"job_id": jid, "nonce": job["nonce"],
                                       "task_type": job["task_type"]}
        return {"ok": True, "cvm": cvm}

    def _replay(self, req, principal) -> dict:
        topics = req.get("topics") or [f"user/{principal}"]

        allowed = [t for t in topics if t == f"user/{principal}"]
        after = int(req.get("after_id", 0))
        with self._lock:
            evs = [e for e in self._events if e["topic"] in allowed and e["event_id"] > after]
        return {"ok": True, "events": evs, "topics": allowed,
                "cursor": self._events[-1]["event_id"] if self._events else 0}

    def stream_subscribe(self, topics, principal, after_id, send, stop: threading.Event):
        allowed = [t for t in topics if t == f"user/{principal}"]
        send({"ok": True, "type": "subscribed", "topics": allowed,
              "cursor": len(self._events)})

        start = int(after_id) if after_id is not None else 0
        with self._lock:
            backlog = [e for e in self._events if e["topic"] in allowed and e["event_id"] > start]
        for e in backlog:
            send(e)

        q = []
        cv = threading.Condition()

        def cb(ev):
            with cv:
                q.append(ev)
                cv.notify()
        with self._lock:
            self._subs.append((allowed, cb))
        try:
            while not stop.is_set():
                with cv:
                    if not q:
                        cv.wait(timeout=0.5)
                    while q:
                        send(q.pop(0))
        finally:
            with self._lock:
                self._subs = [(t, c) for (t, c) in self._subs if c is not cb]

def _summary_for(tt, params):
    text = (params or {}).get("text") or ""
    if tt == "intake.attach":
        n = len((params or {}).get("attachments", [])) + len((params or {}).get("paths", []))
        return f"Process {n} item(s): {text[:60]}"
    return text[:80] or tt

def _digest_for(params):
    p = params or {}
    bits = []
    if p.get("text"):
        bits.append(p["text"][:200])
    for a in p.get("attachments", []):
        bits.append(f"file {a['name']} ({a['size']}B sha256={a['sha256'][:12]}…)")
    for path in p.get("paths", []):
        bits.append(f"path {path}")
    return "\n".join(bits) or None

def _action_for(tt):
    if tt == "send.email":
        return {"text": "send the drafted email", "brick": None}
    if tt == "device.flash":
        return {"text": "flash firmware to the device",
                "brick": "IRREVERSIBLE — a bad image can BRICK the device"}
    return None
