#!/usr/bin/env python3

import os
import sys
import json
import stat
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from durable import DurableVault, VaultError

PASS = FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")

base = tempfile.mkdtemp()
v = DurableVault(base)

v.set("alice", "gmail_app_password", "hunter2 pw", kind="gmail_app_password")
check(v.fetch("alice", "gmail_app_password") == b"hunter2 pw", "round-trip str value")
v.set("alice", "raw", b"\x00\x01\xfe", kind="blob")
check(v.fetch("alice", "raw") == b"\x00\x01\xfe", "round-trip bytes value")

names = v.list_names("alice")
check({n["name"] for n in names} == {"gmail_app_password", "raw"}, "list_names returns names")
check(all("ct" not in n and "value" not in n for n in names), "list_names omits ciphertext/plaintext")
check(names[0]["kind"] in ("gmail_app_password", "blob"), "list_names carries kind metadata")

raw = open(os.path.join(base, "secretvault", "users", "alice", "vault.json"), "rb").read()
check(b"hunter2 pw" not in raw, "plaintext NOT present on disk")
check(b'"ct"' in raw, "ciphertext token present on disk")

v.set("bob", "gmail_app_password", "bob-secret")
check(v.fetch("alice", "gmail_app_password") == b"hunter2 pw", "alice reads her own value")
check(v.fetch("bob", "gmail_app_password") == b"bob-secret", "bob reads his own value")

bob_ct = json.load(open(os.path.join(base, "secretvault", "users", "bob", "vault.json")))["gmail_app_password"]["ct"]
try:
    v._fernet_for("alice").decrypt(bob_ct.encode())
    check(False, "alice's key CANNOT decrypt bob's ciphertext")
except Exception:
    check(True, "alice's key CANNOT decrypt bob's ciphertext")

bp = os.path.join(base, "secretvault", "users", "bob", "vault.json")
d = json.load(open(bp)); d["gmail_app_password"]["ct"] = d["gmail_app_password"]["ct"][:-4] + "AAAA"
open(bp, "w").write(json.dumps(d))
try:
    v.fetch("bob", "gmail_app_password"); check(False, "tampered ciphertext -> VaultError")
except VaultError:
    check(True, "tampered ciphertext -> VaultError")
except Exception:
    check(True, "tampered ciphertext -> raises (detected)")

before = [n for n in v.list_names("alice") if n["name"] == "raw"][0]
v.set("alice", "raw", b"new")
after = [n for n in v.list_names("alice") if n["name"] == "raw"][0]
check(after["created"] == before["created"] and after["updated"] >= before["updated"],
      "update keeps created, bumps updated")

check(v.delete("alice", "raw") is True, "delete existing -> True")
check(v.has("alice", "raw") is False, "deleted name gone")
check(v.delete("alice", "nope") is False, "delete missing -> False")
try:
    v.fetch("alice", "raw"); check(False, "fetch deleted -> KeyError")
except KeyError:
    check(True, "fetch deleted -> KeyError")

v2 = DurableVault(base)
check(v2.fetch("alice", "gmail_app_password") == b"hunter2 pw", "new instance decrypts (master reload)")

mk = os.path.join(base, "secretvault", "master.key")
check(stat.S_IMODE(os.stat(mk).st_mode) == 0o600, "master.key is 0600")
check(stat.S_IMODE(os.stat(os.path.join(base, "secretvault", "users", "alice", "vault.json")).st_mode) == 0o600,
      "vault.json is 0600")

seen = {}
v.use("alice", "gmail_app_password", lambda mv: seen.update(v=bytes(mv)))
check(seen.get("v") == b"hunter2 pw", "use() delivers plaintext via inject_once path")

print(f"\n=== test_durable: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
