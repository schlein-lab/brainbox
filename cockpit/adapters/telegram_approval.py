
from __future__ import annotations
import json
from . import cvm_render as R

def message_text(cvm: dict) -> str:

    lines = ["*" + _md(R.title(cvm)) + "*"]
    act = R.action_line(cvm)
    if act:
        warn = " ⚠️ *BRICK RISK*" if act.get("brick") else ""
        lines.append("about to: `" + _code(act["text"]) + "`" + warn)
        if act.get("brick"):
            lines.append("_" + _md(act["brick"]) + "_")
    dg = R.digest(cvm)
    if dg:
        snip = dg if len(str(dg)) <= 800 else str(dg)[:800] + "…"
        lines.append("```\n" + str(snip) + "\n```")
    df = R.diff(cvm)
    if df:
        lines.append("```diff\n" + str(df)[:800] + "\n```")
    lines.append(f"_job #{cvm.get('id')} · {_md(cvm.get('task_type') or '(raw)')}_")
    return "\n".join(lines)

def inline_keyboard(cvm: dict) -> dict:

    nonce = R.nonce_of(cvm)
    return {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"approve:{nonce}"},
        {"text": "❌ Reject",  "callback_data": f"deny:{nonce}"},
        {"text": "✍️ Revise",  "callback_data": f"revise:{nonce}"},
    ]]}

def resolved_text(cvm: dict) -> str:

    st = cvm.get("approval_state")
    mark = "✅ Approved" if st == "approved" else "❌ Rejected" if st == "denied" else "⏳ pending"
    return f"*{_md(R.title(cvm))}*\n{mark} — cleared on all devices\n_job #{cvm.get('id')}_"

def _md(s) -> str:

    s = "" if s is None else str(s)
    for ch in r"_*[]()~`>#+-=|{}.!":
        s = s.replace(ch, "\\" + ch)
    return s

def _code(s) -> str:

    s = "" if s is None else str(s)
    return s.replace("\\", "\\\\").replace("`", "\\`")

def callback_to_verb(callback_data: str) -> dict:

    op, _, nonce = callback_data.partition(":")
    if op == "approve":
        return {"verb": "approve", "nonce": nonce}
    if op == "deny":
        return {"verb": "deny", "nonce": nonce}
    if op == "revise":

        return {"verb": "revise", "nonce": nonce, "_needs_reply": True}
    return {"verb": None, "error": f"unknown callback {callback_data!r}"}

class TelegramApprovalAdapter:
    def __init__(self, bus_subscribe, verb_call, send_message, edit_message):

        self.bus_subscribe = bus_subscribe
        self.verb_call = verb_call
        self.send_message = send_message
        self.edit_message = edit_message
        self.msg_for_job = {}

    def on_bus_event(self, principal_chat_id, event):

        kind = event.get("kind")
        jid = event.get("job_id")
        data = event.get("data")
        if isinstance(data, str):
            try: data = json.loads(data)
            except ValueError: data = {}
        if kind == "approval-request":
            cvm = {"id": jid, "state": "staged", "approval_state": "pending",
                   "task_type": data.get("task_type"), "approval_request": data}
            mid = self.send_message(principal_chat_id, message_text(cvm), inline_keyboard(cvm))
            self.msg_for_job[jid] = (principal_chat_id, mid)
        elif kind == "state" and jid in self.msg_for_job:
            decision = data.get("decision")
            if decision in ("approved", "denied"):
                cvm = {"id": jid, "approval_state": decision, "task_type": data.get("task_type")}
                chat, mid = self.msg_for_job[jid]
                self.edit_message(chat, mid, resolved_text(cvm))

    def on_callback(self, callback_data):
        req = callback_to_verb(callback_data)
        if req.get("_needs_reply"):
            return {"ok": True, "prompt": "Reply with your revision."}
        return self.verb_call(req)
