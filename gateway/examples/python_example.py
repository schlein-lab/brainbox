#!/usr/bin/env python3

import os, sys, threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk", "python"))
from brainarbeit_client import Brainarbeit

bb = Brainarbeit(
    os.environ["BB_URL"],
    device_did=os.environ["BB_DID"],
    durable_token=os.environ["BB_TOKEN"],
    totp_secret=os.environ.get("BB_TOTP"),
)

resp = bb.submit(
    "send.email",
    params={"to": "kunde@example.com", "subject": "Angebot"},
    attachments=[("angebot.pdf", b"%PDF-1.4 ...")],
    needs_confirmation=True,
)
print("submitted:", resp)
job_id = resp.get("id")
nonce = resp.get("nonce")

def watch():
    for frame in bb.stream(topics=[f"user/{os.environ.get('BB_PRINCIPAL', 'me')}"]):
        if frame.get("type") == "event":
            ev = frame["event"]
            print(f"  [{ev['kind']}] {ev.get('data')}")
            if ev["kind"] == "approval-result":
                return
threading.Thread(target=watch, daemon=True).start()

print("pending:", bb.pending_approvals())
print("approve:", bb.approve(nonce))

print("result:", bb.result(job_id))
