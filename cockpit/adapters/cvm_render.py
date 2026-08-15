
from __future__ import annotations
import json

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
    if ar.get("digest"): return ar["digest"]
    if ar.get("preview"): return ar["preview"]
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

def approval_summary(cvm: dict) -> str:

    act = action_line(cvm)
    suffix = ""
    if act:
        suffix = " — " + act["text"] + (" [BRICK RISK]" if act.get("brick") else "")
    return f"Approval needed: {title(cvm)}{suffix}"

def nonce_of(cvm: dict):
    ar = cvm.get("approval_request") or {}
    return ar.get("nonce") or cvm.get("nonce")
