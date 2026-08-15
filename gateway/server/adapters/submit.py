
from __future__ import annotations
import os, json, time, secrets, hashlib, unicodedata

def _is_control(ch: str) -> bool:

    if ch in "\x00\x7f" or ord(ch) < 0x20:
        return True
    return unicodedata.category(ch) in ("Cc", "Cf")

class SubmitError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code

def _safe_name(name: str) -> str:
    base = os.path.basename(name or "file")
    keep = "".join(c for c in base if c.isalnum() or c in ("-", "_", ".")) or "file"
    return keep[:128]

def stage_attachments(cfg, device_did: str, attachments: list[dict]) -> list[str]:

    import base64
    if not attachments:
        return []
    req_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)

    d = os.path.join(cfg.DROPZONE, _safe_name(device_did)[:32], req_id)
    os.makedirs(d, mode=0o700, exist_ok=True)
    total = 0
    refs = []
    for a in attachments:
        try:
            raw = base64.b64decode(a["content_b64"], validate=True)
        except Exception:
            raise SubmitError("attachment content_b64 is not valid base64")
        total += len(raw)
        if total > cfg.MAX_ATTACH_BYTES:
            raise SubmitError(f"attachments exceed {cfg.MAX_ATTACH_BYTES} bytes", code=413)
        p = os.path.join(d, _safe_name(a.get("filename")))
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, raw)
        finally:
            os.close(fd)
        refs.append(p)
    return refs

def build_submit_request(cfg, body: dict, attach_paths: list[str]) -> dict:

    if body.get("cmd"):
        raise SubmitError("raw 'cmd' is not permitted over the public API; submit a task_type "
                          "(no raw/HPC reach through the API tier)", code=403)
    task_type = body.get("task_type")
    if not task_type or not isinstance(task_type, str):
        raise SubmitError("a 'task_type' (string) is required")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise SubmitError("'params' must be an object")

    path_refs = list(attach_paths)
    for p in (body.get("path_refs") or []):
        if not isinstance(p, str):
            raise SubmitError("'path_refs' entries must be strings (box-side paths)")
        path_refs.append(p)
    if path_refs:
        params = {**params, "attachments": path_refs}

    reply_to = body.get("reply_to")
    if isinstance(reply_to, str):

        probe = "".join(ch for ch in reply_to if not (ch.isspace() or _is_control(ch)))
        _PREFIX = "webhook:"
        if probe[:len(_PREFIX)].casefold() == _PREFIX:
            from .webhooks import validate_url, canonicalize_url, WebhookError
            hook_url = probe[len(_PREFIX):]
            try:
                validate_url(cfg, hook_url)
                hook_url = canonicalize_url(hook_url)
            except WebhookError as e:
                raise SubmitError(f"reply_to webhook rejected: {e}", code=e.code)

            reply_to = _PREFIX + hook_url
            body = {**body, "reply_to": reply_to}

    req = {"verb": "submit", "task_type": task_type, "params": params,
           "class": body.get("class", "worker")}

    for k_api, k_pnd in (("tag", "tag"), ("room", "room"), ("priority", "prio"),
                         ("mem", "mem"), ("timeout", "timeout"), ("idempotent", "idempotent"),
                         ("reply_to", "reply_to"), ("group_id", "group_id"),
                         ("parent_job", "parent_job"), ("deps", "deps"),
                         ("needs_confirmation", "needs_confirmation"),
                         ("work_order", "work_order")):
        if k_api in body and body[k_api] is not None:
            req[k_pnd] = body[k_api]
    req["source"] = "api"
    return req

def submit(cfg, pnd, principal, body: dict) -> dict:

    attach = body.get("attachments") or []
    if not isinstance(attach, list):
        raise SubmitError("'attachments' must be a list")
    paths = stage_attachments(cfg, principal.device_did, attach)
    req = build_submit_request(cfg, body, paths)
    return pnd.call(req, device_did=principal.device_did)
