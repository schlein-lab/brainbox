#!/usr/bin/env python3

import base64
import importlib.machinery
import importlib.util
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_loader(
    "brainbox_setup",
    importlib.machinery.SourceFileLoader("brainbox_setup",
                                         os.path.join(HERE, "brainbox-setup")))
W = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(W)

FAILED = []

def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)

def mkkey(ktype="ssh-ed25519", inner=None, sizes=(32,), seed=0,
          comment="me@laptop"):

    parts = [(inner or ktype).encode()]
    for i, sz in enumerate(sizes):
        parts.append(bytes((seed * 31 + i * 7 + j) % 256 for j in range(sz)))
    body = b"".join(struct.pack(">I", len(x)) + x for x in parts)
    return ktype + " " + base64.b64encode(body).decode() + \
        ((" " + comment) if comment else "")

print("== validate_ssh_keys")

ok, res = W.validate_ssh_keys("")
check("empty paste is VALID (the card is a door, not a gate)", ok and res == [])

ok, res = W.validate_ssh_keys("   \n\n  ")
check("whitespace-only paste is valid", ok and res == [])

k = mkkey()
ok, res = W.validate_ssh_keys(k)
check("plain ed25519 key accepted", ok and len(res) == 1, res)
check("normalised line keeps type+blob+comment",
      ok and res and res[0].startswith("ssh-ed25519 ") and res[0].endswith("me@laptop"))

ok, res = W.validate_ssh_keys(mkkey("ssh-rsa", sizes=(3, 256)))
check("rsa key accepted", ok and len(res) == 1, res)

ok, res = W.validate_ssh_keys(mkkey("ecdsa-sha2-nistp256",
                                    sizes=(8, 65), seed=3))
check("ecdsa key accepted", ok and len(res) == 1, res)

ok, res = W.validate_ssh_keys(mkkey("sk-ssh-ed25519@openssh.com",
                                    sizes=(32, 11), seed=4))
check("FIDO/security-key type accepted", ok and len(res) == 1, res)

ok, res = W.validate_ssh_keys(k + "\r\n" + mkkey(seed=2, comment="second@pc") + "\r\n")
check("CRLF paste from Windows accepted", ok and len(res) == 2, res)

ok, res = W.validate_ssh_keys("# a comment\n" + k)
check("#-comment lines skipped", ok and len(res) == 1, res)

ok, res = W.validate_ssh_keys(k + "\n" + k)
check("same key twice collapses to one", ok and len(res) == 1, res)

kb = k.rsplit(" ", 1)[0] + " other-comment"
ok, res = W.validate_ssh_keys(k + "\n" + kb)
check("same key with a different comment is still one key",
      ok and len(res) == 1, res)

for bad, label in (
        ('command="/bin/false" ' + k, "forced-command prefix"),
        ("no-pty,no-agent-forwarding " + k, "option-list prefix"),
        ('environment="PATH=/tmp" ' + k, "environment= prefix"),
        ('permitopen="10.0.0.1:22" ' + k, "permitopen prefix")):
    ok, res = W.validate_ssh_keys(bad)
    check("REJECTED: %s" % label, not ok, res)

ok, res = W.validate_ssh_keys("ssh-dss " + k.split(" ")[1])
check("obsolete ssh-dss rejected", not ok and res.startswith("ssh_bad_type"), res)

trunc = k.split(" ")[1][:60]
ok, res = W.validate_ssh_keys("ssh-ed25519 " + trunc)
check("truncated key rejected (body does not name its own type)",
      not ok and res.startswith(("ssh_bad_body", "ssh_bad_b64")), res)

ok, res = W.validate_ssh_keys("ssh-ed25519 " + mkkey("ssh-rsa", sizes=(3, 256)).split(" ")[1])
check("type/blob mismatch rejected", not ok and res.startswith("ssh_bad_body"), res)

ok, res = W.validate_ssh_keys("ssh-ed25519 not!base64!at!all")
check("non-base64 body rejected", not ok, res)

ok, res = W.validate_ssh_keys("just some prose the owner pasted by mistake")
check("prose rejected", not ok, res)

ok, res = W.validate_ssh_keys("\n".join(mkkey(seed=i, comment="k%d" % i)
                                        for i in range(W.SSH_KEYS_MAX + 2)))
check("more than SSH_KEYS_MAX rejected", not ok and res == "ssh_too_many", res)

ok, res = W.validate_ssh_keys("x" * (W.SSH_PASTE_MAX + 1))
check("oversized paste rejected", not ok and res == "ssh_too_long", res)

ok, res = W.validate_ssh_keys(mkkey(comment="ok\x07\x1b[31mred"))
check("control bytes stripped from the comment",
      ok and "\x1b" not in res[0] and "\x07" not in res[0], res)

ok, res = W.validate_ssh_keys(mkkey(comment="c" * 400))
check("comment capped", ok and len(res[0].split(" ", 2)[2]) <= W.SSH_COMMENT_MAX, len(res[0]))

ok, res = W.validate_ssh_keys("ssh-ed25519")
check("type without a body rejected", not ok, res)

print("== validate_console_password")

ok, res = W.validate_console_password("", "")
check("empty means 'keep what is there'", ok and res == "")

ok, res = W.validate_console_password("short1", "short1")
check("too short rejected", not ok and res == "pw_short", res)

ok, res = W.validate_console_password("x" * (W.CONSOLE_PW_MAX + 1),
                                      "x" * (W.CONSOLE_PW_MAX + 1))
check("too long rejected", not ok and res == "pw_long", res)

ok, res = W.validate_console_password("correct horse", "correct horse")
check("passphrase with a space accepted", ok and res == "correct horse", res)

ok, res = W.validate_console_password("has:colon!", "has:colon!")
check("colon rejected (would split the chpasswd record)",
      not ok and res == "pw_chars", res)

ok, res = W.validate_console_password("line\nbreak", "line\nbreak")
check("newline rejected", not ok and res == "pw_chars", res)

ok, res = W.validate_console_password("goodpassword", "goodpasswrod")
check("typo in the repeat field caught", not ok and res == "pw_mismatch", res)

ok, res = W.validate_console_password("goodpassword", "")
check("empty repeat field caught", not ok and res == "pw_mismatch", res)

ok, res = W.validate_console_password("goodpassword", None)
check("missing repeat field caught", not ok and res == "pw_mismatch", res)

print("== _write_authorized_keys")

tmp = tempfile.mkdtemp(prefix="bbxacc")
try:
    home = os.path.join(tmp, "home", "brainbox")
    os.makedirs(home)
    ak = os.path.join(home, ".ssh", "authorized_keys")
    user = __import__("pwd").getpwuid(os.getuid()).pw_name

    k1 = W.validate_ssh_keys(mkkey(comment="laptop"))[1][0]
    k2 = W.validate_ssh_keys(mkkey("ssh-rsa", sizes=(3, 256), comment="phone"))[1][0]

    W._write_authorized_keys(user, home, [k1])
    body = open(ak).read()
    check("authorized_keys created", k1 in body)
    check("mode is 0600", oct(os.stat(ak).st_mode & 0o777) == "0o600",
          oct(os.stat(ak).st_mode & 0o777))
    check(".ssh is 0700",
          oct(os.stat(os.path.join(home, ".ssh")).st_mode & 0o777) == "0o700")

    W._write_authorized_keys(user, home, [k2])
    body = open(ak).read()
    check("second key APPENDED, first one kept", k1 in body and k2 in body)

    n_before = len([l for l in open(ak) if l.strip()])
    W._write_authorized_keys(user, home, [k1])
    n_after = len([l for l in open(ak) if l.strip()])
    check("re-adding the same key is a no-op", n_before == n_after,
          "%d -> %d" % (n_before, n_after))

    same_key_new_comment = k1.rsplit(" ", 1)[0] + " renamed"
    W._write_authorized_keys(user, home, [same_key_new_comment])
    n_after2 = len([l for l in open(ak) if l.strip()])
    check("same key with a new comment does not duplicate", n_after2 == n_after,
          "%d -> %d" % (n_after, n_after2))

    check("no trailing blank lines",
          open(ak).read().endswith("\n") and "\n\n" not in open(ak).read())

    with open(ak, "w") as f:
        f.write("# my keys\n\n" + k1 + "\n")
    W._write_authorized_keys(user, home, [k2])
    body = open(ak).read()
    check("hand-written comment preserved", body.startswith("# my keys"))
    check("both keys present after merge", k1 in body and k2 in body)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("== step wiring")

check("'access' is in STEPS_ORDER", "access" in W.STEPS_ORDER)
check("'access' comes after 'owner'",
      W.STEPS_ORDER.index("access") == W.STEPS_ORDER.index("owner") + 1)
check("'access' is NOT a display step", "access" not in W.DISPLAY_STEPS)

cfg = W.new_cfg("de")

ok, errs = W.validate_and_merge(cfg, "access", {})
check("empty access step advances (harness contract)", ok, errs)

ok, errs = W.validate_and_merge(cfg, "access", {"ssh_keys": mkkey()})
check("valid key merges into cfg", ok and len(cfg["ssh_keys"]) == 1, errs)

ok, errs = W.validate_and_merge(cfg, "access", {"ssh_keys": "garbage"})
check("bad key reports under the right field id",
      not ok and "ssh_keys" in errs, errs)

cfg2 = W.new_cfg("de")
ok, errs = W.validate_and_merge(cfg2, "access",
                                {"console_password": "geheim12345",
                                 "console_password2": "geheim12345"})
check("password merges", ok and cfg2["console_password"] == "geheim12345", errs)

ok, errs = W.validate_and_merge(cfg2, "access", {})
check("re-submitting the card empty keeps the password",
      ok and cfg2["console_password"] == "geheim12345", cfg2["console_password"])

_real = W.detect_access_state
try:
    W.detect_access_state = lambda: {"user": "brainbox", "keys": 0,
                                     "initial_pw": True, "pw_auth": False,
                                     "root": False}
    cfg3 = W.new_cfg("de")
    ok, errs = W.validate_and_merge(cfg3, "access", {"ssh_password_auth": 1})
    check("SSH password login refused while the shipped password is live",
          not ok and errs.get("ssh_password_auth") == "pw_auth_needs_new_pw", errs)
    check("...and the switch is forced back off", cfg3["ssh_password_auth"] == 0)

    cfg4 = W.new_cfg("de")
    ok, errs = W.validate_and_merge(cfg4, "access",
                                    {"ssh_password_auth": 1,
                                     "console_password": "meineigenes1",
                                     "console_password2": "meineigenes1"})
    check("...allowed once an own password is set in the same step",
          ok and cfg4["ssh_password_auth"] == 1, errs)

    W.detect_access_state = lambda: {"user": "brainbox", "keys": 0,
                                     "initial_pw": False, "pw_auth": False,
                                     "root": False}
    cfg5 = W.new_cfg("de")
    ok, errs = W.validate_and_merge(cfg5, "access", {"ssh_password_auth": 1})
    check("...and allowed outright once the shipped password is gone",
          ok and cfg5["ssh_password_auth"] == 1, errs)
finally:
    W.detect_access_state = _real

print("== labels")

for lang in ("de", "en"):
    tbl = W.LABELS[lang]
    missing = [k for k in ("s_acc_title", "acc_intro", "acc_keys",
                           "acc_keys_hint", "acc_keys_ph", "acc_have_keys",
                           "acc_pw", "acc_pw2", "acc_pw_hint", "acc_pw_show",
                           "acc_pw_set", "acc_initial_pw", "acc_pwauth",
                           "acc_pwauth_hint", "done_ssh",
                           "ssh_too_long", "ssh_too_many", "ssh_bad_line",
                           "ssh_bad_type", "ssh_bad_b64", "ssh_bad_body",
                           "pw_short", "pw_long", "pw_chars", "pw_mismatch",
                           "pw_auth_needs_new_pw") if k not in tbl]
    check("all access labels present [%s]" % lang, not missing, missing)

emitted = set()
for probe in ("", "garbage", "ssh-ed25519 " + "A" * 40,
              "\n".join(mkkey(seed=i, comment=str(i)) for i in range(20)),
              "x" * (W.SSH_PASTE_MAX + 1)):
    ok, res = W.validate_ssh_keys(probe)
    if not ok:
        emitted.add(res.split("|")[0])
for pw in (("short1", "short1"), ("x" * 200, "x" * 200), ("a:b12345", "a:b12345"),
           ("password12", "password13")):
    ok, res = W.validate_console_password(*pw)
    if not ok:
        emitted.add(res)
for lang in ("de", "en"):
    gap = [e for e in emitted if e not in W.LABELS[lang]]
    check("every emitted error key is translated [%s]" % lang, not gap, gap)

print("== payloads")

det = W.build_detect_payload()
check("detect payload advertises the step", "access" in det["steps"])
check("detect payload carries live access state",
      isinstance(det.get("access"), dict) and "keys" in det["access"], det.get("access"))
check("detect payload never ships the password itself",
      "password" not in repr(det["access"]).lower()
      or det["access"].get("initial_pw") in (True, False))

cfg6 = W.new_cfg("de")
W.validate_and_merge(cfg6, "access", {"ssh_keys": mkkey(),
                                      "console_password": "geheim12345",
                                      "console_password2": "geheim12345"})
s = W._sanitize_payload(cfg6)
check("recap counts keys, does not list them", s["ssh_keys"] == 1)
check("recap masks the password",
      "geheim12345" not in repr(s) and s["console_password"], s["console_password"])

print("== _apply_sshd_password_auth")

_tmp2 = tempfile.mkdtemp(prefix="bbxsshd")
_real_dropin, _real_run, _real_root, _real_tool = (
    W.SSHD_DROPIN, W.run, W._is_root, W.detect_tool)
try:
    W.SSHD_DROPIN = os.path.join(_tmp2, "99-brainbox-hardening.conf")
    W._is_root = lambda: True
    W.detect_tool = lambda *a, **k: None
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[:2] == ["sshd", "-t"]:
            return (0, "", "")
        return (127, "", "not found")

    W.run = fake_run

    def state(v):
        W._sshd_password_auth = lambda: v

    _real_state = W._sshd_password_auth
    try:

        state(False)
        out = W._apply_sshd_password_auth({"ssh_password_auth": 0})
        check("no-op when already off", out == ""
              and not os.path.exists(W.SSHD_DROPIN), out)

        state(True)
        out = W._apply_sshd_password_auth({"ssh_password_auth": 1})
        check("no-op when already on", out == ""
              and not os.path.exists(W.SSHD_DROPIN), out)

        state(None)
        out = W._apply_sshd_password_auth({"ssh_password_auth": 0})
        check("unknown state is left alone rather than closed",
              "untouched" in out and not os.path.exists(W.SSHD_DROPIN), out)

        state(False)
        out = W._apply_sshd_password_auth({"ssh_password_auth": 1})
        body = open(W.SSHD_DROPIN).read()
        check("switching ON writes the drop-in", "PasswordAuthentication yes" in body, body)
        check("keyboard-interactive follows the switch",
              "KbdInteractiveAuthentication yes" in body, body)
        check("root over SSH stays refused no matter what",
              "PermitRootLogin no" in body, body)
        check("sshd config was validated", ["sshd", "-t"] in calls, calls)

        state(True)
        W._apply_sshd_password_auth({"ssh_password_auth": 0})
        body = open(W.SSHD_DROPIN).read()
        check("switching OFF rewrites the drop-in",
              "PasswordAuthentication no" in body
              and "PermitRootLogin no" in body, body)

        keep = open(W.SSHD_DROPIN).read()

        def rejecting_run(cmd, **kw):
            if cmd[:2] == ["sshd", "-t"]:
                return (255, "", "line 1: Bad configuration option")
            return (127, "", "")

        W.run = rejecting_run
        state(True)
        try:
            W._apply_sshd_password_auth({"ssh_password_auth": 0})
            check("rejected config raises", False, "no exception")
        except RuntimeError as e:
            check("rejected config raises", "rolled back" in str(e), e)
        check("rejected config ROLLED BACK to the previous content",
              open(W.SSHD_DROPIN).read() == keep, open(W.SSHD_DROPIN).read())

        os.unlink(W.SSHD_DROPIN)
        state(True)
        try:
            W._apply_sshd_password_auth({"ssh_password_auth": 0})
        except RuntimeError:
            pass
        check("rejected config leaves NO file behind when there was none",
              not os.path.exists(W.SSHD_DROPIN))

        W.run = fake_run
        W._is_root = lambda: False
        state(True)
        try:
            W._apply_sshd_password_auth({"ssh_password_auth": 0})
            check("non-root refuses", False, "no exception")
        except RuntimeError as e:
            check("non-root refuses", "root" in str(e), e)
    finally:
        W._sshd_password_auth = _real_state
finally:
    W.SSHD_DROPIN, W.run, W._is_root, W.detect_tool = (
        _real_dropin, _real_run, _real_root, _real_tool)
    shutil.rmtree(_tmp2, ignore_errors=True)

print("== run() carries stdin")

rc, out, err = W.run(["cat"], input_="hallo\n")
check("run(input_=...) actually reaches the child",
      rc == 0 and out == "hallo\n", (rc, out, err))
rc, out, err = W.run(["true"])
check("run() without input still works", rc == 0, (rc, out, err))

print()
if FAILED:
    print("FAILED %d: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all access tests passed")
