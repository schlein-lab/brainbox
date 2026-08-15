
from __future__ import annotations
import re
from . import cvm_render as R

def speak_line(cvm: dict) -> str:

    parts = [R.approval_summary(cvm).replace("[BRICK RISK]", "")]
    act = R.action_line(cvm)
    if act and act.get("brick"):
        parts.append(f"Warning: {act['brick']}.")
    dg = R.digest(cvm)
    if dg:
        snip = str(dg)
        parts.append("The result is: " + (snip if len(snip) <= 240 else snip[:240] + ", truncated."))
    parts.append("Say approve, reject, or revise.")
    return " ".join(parts)

_APPROVE = re.compile(r"\b(approve|accept|yes|confirm|go ahead|do it|okay|ok)\b", re.I)
_DENY    = re.compile(r"\b(reject|deny|no|cancel|stop|abort|don'?t)\b", re.I)
_REVISE  = re.compile(r"\b(revise|change|instead|but |actually|wait)\b", re.I)

def utterance_to_verb(utterance: str, cvm: dict) -> dict:

    nonce = R.nonce_of(cvm)
    u = utterance or ""
    if _REVISE.search(u):
        feedback = re.sub(r"^\s*(revise|change|instead)\b[:,]?\s*", "", u, flags=re.I).strip()
        return {"verb": "revise", "nonce": nonce, "input": {"feedback": feedback or u}}
    if _DENY.search(u):
        return {"verb": "deny", "nonce": nonce}
    if _APPROVE.search(u):
        return {"verb": "approve", "nonce": nonce}
    return {"verb": None, "error": "undecided", "reprompt": "Sorry — approve, reject, or revise?"}

class VoiceApprovalAdapter:
    def __init__(self, verb_call, tts, stt):
        self.verb_call = verb_call
        self.tts = tts
        self.stt = stt

    def handle_approval(self, cvm: dict) -> dict:

        self.tts(speak_line(cvm))
        for _ in range(2):
            req = utterance_to_verb(self.stt(), cvm)
            if req.get("verb"):
                resp = self.verb_call(req)
                self.tts("Done." if resp.get("ok") else "That failed.")
                return resp
            self.tts(req.get("reprompt", "Approve, reject, or revise?"))
        self.tts("No decision heard; leaving it pending.")
        return {"ok": False, "error": "no decision"}
