
from __future__ import annotations
import json, time

def submit_text(text: str, *, task_type="intake.message", reply_topic=None) -> dict:

    params = {"text": text}
    if reply_topic:
        params["reply_to"] = reply_topic
    return {"verb": "submit", "task_type": task_type, "params": params}

def submit_attach(text: str, *, attachments=None, paths=None, task_type="intake.attach",
                  reply_topic=None) -> dict:

    params = {"text": text}
    if attachments:
        params["attachments"] = attachments
    if paths:
        params["paths"] = list(paths)
    if reply_topic:
        params["reply_to"] = reply_topic
    return {"verb": "submit", "task_type": task_type, "params": params}

def subscribe(principal: str, *, after_id=None, extra_topics=None) -> dict:

    topics = [f"user/{principal}"]
    if extra_topics:
        topics += list(extra_topics)
    req = {"verb": "subscribe", "topics": topics}
    if after_id is not None:
        req["after_id"] = after_id
    return req

def approve(nonce: str) -> dict:

    return {"verb": "approve", "nonce": nonce}

def reject(nonce: str) -> dict:
    return {"verb": "deny", "nonce": nonce}

def revise(job_id: int, feedback: str) -> dict:

    return {"verb": "steer", "id": int(job_id), "input": feedback}

def cvm_request(job_id: int) -> dict:
    return {"verb": "cvm", "id": int(job_id)}

def replay(principal: str, after_id: int) -> dict:
    return {"verb": "replay", "topics": [f"user/{principal}"], "after_id": int(after_id)}

class Reality:

    def __init__(self):
        self.jobs: dict[int, dict] = {}
        self.last_event_id: int = 0
        self.authorized_topics: list[str] = []
        self.health: dict = {}

    def apply(self, frame: dict) -> dict | None:

        t = frame.get("type") or frame.get("kind")
        eid = frame.get("event_id") or frame.get("id_event") or frame.get("seq")
        if isinstance(eid, int) and eid > self.last_event_id:
            self.last_event_id = eid

        if t == "subscribed":
            self.authorized_topics = frame.get("topics", [])
            return None
        if t == "health":
            self.health = frame.get("payload") or {k: v for k, v in frame.items()
                                                   if k not in ("type", "kind", "event_id")}
            return None
        if t == "revoked":

            self.health["revoked"] = True
            return None

        jid = frame.get("job_id") or frame.get("id")
        if jid is None:
            return None
        cvm = self.jobs.setdefault(int(jid), {"id": int(jid)})

        if t == "state":
            cvm["state"] = frame.get("state")

            if cvm.get("approval_state") == "pending" and \
                    frame.get("state") not in ("staged", "awaiting_approval"):
                cvm["approval_state"] = "resolved"
        elif t == "progress":
            cvm["progress"] = frame.get("progress") or {
                "done": frame.get("done"), "total": frame.get("total"), "msg": frame.get("msg")}
        elif t == "partial":
            cvm["partial"] = frame.get("partial", frame.get("text"))
        elif t == "approval-request":
            ar = frame.get("approval_request") or frame.get("payload") or {
                k: frame.get(k) for k in
                ("nonce", "task_type", "summary", "action", "brick_warning", "digest", "diff")}
            ar.setdefault("job_id", int(jid))
            cvm["approval_request"] = ar
            cvm["approval_state"] = "pending"
            cvm.setdefault("state", "awaiting_approval")
            cvm["task_type"] = ar.get("task_type") or cvm.get("task_type")
        elif t == "notify":
            cvm["notify"] = frame.get("text") or frame.get("msg") or frame.get("payload")
        elif t == "log":
            cvm.setdefault("log", []).append(frame.get("line") or frame.get("msg"))
        cvm["last_event_id"] = self.last_event_id
        return cvm

    def pending_approvals(self) -> list[dict]:

        return [c for c in self.jobs.values() if is_awaiting(c)]

def title(cvm: dict) -> str:
    ar = cvm.get("approval_request") or {}
    return ar.get("summary") or cvm.get("task_type") or f"job #{cvm.get('id')}"

def action_line(cvm: dict):

    ar = cvm.get("approval_request") or {}
    if ar.get("action"):
        return {"text": ar["action"], "brick": ar.get("brick_warning")}
    tt = cvm.get("task_type")
    if tt and tt != "(raw)":
        return {"text": tt, "brick": None}
    return None

def digest(cvm: dict):
    ar = cvm.get("approval_request") or {}
    if ar.get("digest"):
        return ar["digest"]
    if ar.get("preview"):
        return ar["preview"]
    p = cvm.get("partial")
    if p is not None:
        return p if isinstance(p, str) else json.dumps(p, indent=2)
    return None

def diff(cvm: dict):
    ar = cvm.get("approval_request") or {}
    return ar.get("diff")

def is_awaiting(cvm: dict) -> bool:
    return cvm.get("approval_state") == "pending" and \
        cvm.get("state") in ("staged", "awaiting_approval")

def nonce_of(cvm: dict):
    ar = cvm.get("approval_request") or {}
    return ar.get("nonce") or cvm.get("nonce")

def approval_summary(cvm: dict) -> str:

    act = action_line(cvm)
    suffix = ""
    if act:
        suffix = " — " + act["text"] + (" [BRICK RISK]" if act.get("brick") else "")
    return f"Approval needed: {title(cvm)}{suffix}"
