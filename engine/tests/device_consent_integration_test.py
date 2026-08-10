#!/usr/bin/env python3

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from pnlib import db
from pnlib import devmaint as M

PASS = FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")

def fresh_db():
    path = tempfile.mktemp(prefix="pn_devc_", suffix=".db")
    cx = db.connect(path)

    for name, uid, kind in (("alice", 7001, "user"), ("brain", 4001, "agent")):
        cx.execute("INSERT OR IGNORE INTO principals(name,uid,kind,note) VALUES(?,?,?,?)",
                   (name, uid, kind, f"test {name}"))
    cx.commit()
    return cx, path

def make_flash_gate(cx, owner):

    import secrets
    jid = db.submit(cx, ["/bin/true"], "/tmp", {}, "{}", 100, 64, "firmware-flash",
                    principal=owner, task_type=None, submitter_principal=owner)
    nonce = secrets.token_urlsafe(18)
    db.stage_for_approval(cx, jid, nonce, approval_kind="pre")
    return jid, nonce

def consent_verifier(cx, owner_principal, authorized=False):

    def verify(nonce):
        res = db.resolve_approval(cx, nonce, "approve", principal=owner_principal,
                                  scope_all=authorized)

        return bool(res.get("ok"))
    return verify

def test_brain_cannot_mint():
    print("[1] the brain CANNOT mint/approve the firmware consent nonce (real approval gate)")
    cx, path = fresh_db()
    jid, nonce = make_flash_gate(cx, owner="alice")

    res_brain = db.resolve_approval(cx, nonce, "approve", principal="brain")
    check(not res_brain.get("ok") and "unknown" in res_brain.get("error", ""),
          "brain approving alice's flash gate -> 'unknown or expired nonce' (no oracle; can't consent)")

    import secrets
    forged = secrets.token_urlsafe(18)
    res_forged = db.resolve_approval(cx, forged, "approve", principal="brain")
    check(not res_forged.get("ok"), "a brain-forged random nonce does not resolve (not minted by pnd)")

    fe = M.FlashEngine(consent_verifier(cx, "brain"), mode="green")
    bk = M.Backup("dev", b"img"); bk.verify()
    try:
        fe.flash(descriptor={"artifact_sha256": "x", "target_arch": "arm"},
                 emulation={"ok": True}, backup=bk, consent_nonce=nonce, brick_warning_ack=True)
        check(False, "FlashEngine wrote without human consent")
    except M.ConsentRequired:
        check(True, "FlashEngine REFUSES the write — the brain cannot produce a verifying consent")
    os.unlink(path)

def test_human_consent_then_flash():
    print("[2] a HUMAN OPERATOR approves -> the burned nonce lets the FlashEngine do its EMULATED write")
    cx, path = fresh_db()
    jid, nonce = make_flash_gate(cx, owner="alice")

    fe_self = M.FlashEngine(consent_verifier(cx, "alice", authorized=False), mode="green")
    bk0 = M.Backup("dev", b"firmware-old"); bk0.verify()
    try:
        fe_self.flash(descriptor={"artifact_sha256": "abc123", "target_arch": "arm"},
                      emulation={"ok": True}, backup=bk0, consent_nonce=nonce, brick_warning_ack=True)
        check(False, "FlashEngine wrote on an UNAUTHORIZED self-consent (separation of duties broken)")
    except M.ConsentRequired:
        check(True, "an unauthorized self-consent is REFUSED (separation of duties)")

    fe = M.FlashEngine(consent_verifier(cx, "operator", authorized=True), mode="green")
    bk = M.Backup("dev", b"firmware-old"); bk.verify()
    write = fe.flash(descriptor={"artifact_sha256": "abc123", "target_arch": "arm"},
                     emulation={"ok": True}, backup=bk, consent_nonce=nonce, brick_warning_ack=True)
    check(write["emulated_write"] and write["real_write"] is False,
          "after the human operator approves, the write proceeds — EMULATED (real_write=False)")

    row = cx.execute("SELECT state, approval_state, approved_by FROM jobs WHERE id=?",
                     (jid,)).fetchone()
    check(row["approval_state"] == "approved",
          "the approval-gate row records the human 'approved' decision (consent provenance)")
    check(row["approved_by"] == "operator",
          "approved_by records the consenting OPERATOR (separation-of-duties audit)")
    os.unlink(path)

def main():
    print("=== P9 consent integration: the firmware nonce IS the approval-gate nonce ===\n")
    test_brain_cannot_mint()
    test_human_consent_then_flash()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
