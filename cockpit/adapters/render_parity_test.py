#!/usr/bin/env python3

import os, sys, re

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..")))
from adapters import cvm_render as R
from adapters import telegram_approval as TG
from adapters import voice_approval as V

PASS = FAIL = 0
def check(c, label):
    global PASS, FAIL
    if c: PASS += 1; print(f"  PASS  {label}")
    else: FAIL += 1; print(f"  FAIL  {label}")

CVM = {
    "id": 77, "state": "staged", "approval_state": "pending", "needs_confirmation": True,
    "task_type": "device.bind",
    "approval_request": {
        "job_id": 77, "nonce": "NONCE-XYZ", "task_type": "device.bind",
        "summary": "Bind HP LaserJet 4500 (printer)",
        "action": "flash firmware v2.3 to hp-4500 over TFTP",
        "brick_warning": "irreversible; a failed flash can brick the device",
        "digest": "vendor=HP model=4500 fw_current=2.1 fw_target=2.3 backup=taken",
    },
}

def main():
    print("=== render parity — one CVM, three surfaces, identical decision verbs ===")

    check(R.is_awaiting(CVM), "cvm_render.is_awaiting True for the pending approval")
    check(R.nonce_of(CVM) == "NONCE-XYZ", "the single-use nonce is recoverable for the decision verbs")
    check("Bind HP LaserJet 4500" in R.title(CVM), "title from approval summary")
    act = R.action_line(CVM)
    check(act and act["text"].startswith("flash firmware") and act["brick"],
          "action_line carries the exact about-to-happen action + brick warning")

    txt = TG.message_text(CVM)
    check("flash firmware" in txt and "BRICK RISK" in txt and "hp-4500" in txt,
          "Telegram message shows the action + brick warning + target")
    kb = TG.inline_keyboard(CVM)
    cbs = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
    check(cbs == ["approve:NONCE-XYZ", "deny:NONCE-XYZ", "revise:NONCE-XYZ"],
          "Telegram inline buttons carry approve/deny/revise + the nonce")
    check(TG.callback_to_verb("approve:NONCE-XYZ") == {"verb": "approve", "nonce": "NONCE-XYZ"},
          "Telegram Approve tap -> approve verb with the nonce")
    check(TG.callback_to_verb("deny:NONCE-XYZ") == {"verb": "deny", "nonce": "NONCE-XYZ"},
          "Telegram Reject tap -> deny verb with the nonce")

    line = V.speak_line(CVM)
    check("Approval needed" in line and "Bind HP LaserJet 4500" in line and "Warning" in line,
          "voice reads the approval + brick warning aloud")
    check(V.utterance_to_verb("yes approve it", CVM) == {"verb": "approve", "nonce": "NONCE-XYZ"},
          "spoken 'approve' -> approve verb with the nonce")
    check(V.utterance_to_verb("no, don't do that", CVM) == {"verb": "deny", "nonce": "NONCE-XYZ"},
          "spoken 'no/deny' -> deny verb (checked before approve so 'don't' isn't 'do')")
    rv = V.utterance_to_verb("revise: use firmware 2.2 instead", CVM)
    check(rv["verb"] == "revise" and "2.2" in rv["input"]["feedback"],
          "spoken 'revise …' -> steer/revise verb carrying the spoken feedback")

    web_like = {"verb": "approve", "nonce": R.nonce_of(CVM)}
    tg_like = TG.callback_to_verb(f"approve:{R.nonce_of(CVM)}")
    voice_like = V.utterance_to_verb("approve", CVM)
    check(web_like == tg_like == voice_like,
          "web Approve == Telegram Approve == voice Approve  (identical verb + nonce -> one reality)")

    appjs = open(os.path.normpath(os.path.join(HERE, "..", "web", "app.js"))).read()
    for fn in ("title", "actionLine", "digest", "diff", "isAwaiting", "approvalSummary"):
        check(re.search(rf"\b{fn}\s*\(", appjs) is not None,
              f"web CVMRender exposes {fn}() (matches Python cvm_render)")

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
