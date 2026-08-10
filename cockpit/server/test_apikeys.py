#!/usr/bin/env python3

import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import apikeys

PASS = FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok   {label}")
    else:
        FAIL += 1; print(f"  FAIL {label}")

d = tempfile.mkdtemp()
ks = apikeys.KeyStore(d)

kid, key = ks.create("smarthome", label="pi", scopes=["/api/llm", "/api/queue"])
check(isinstance(kid, str) and len(kid) == 8, "key_id is 8 hex chars")
check(key.startswith("pak_") and len(key) > 40, "full key has pak_ prefix + high entropy")

ent = ks.resolve(key)
check(ent is not None and ent["uid"] == "smarthome", "resolve(key) -> principal smarthome")
check(ent["scopes"] == ["/api/llm", "/api/queue"], "resolve carries scopes")
check(ks.resolve("pak_totally-bogus") is None, "unknown key -> None")
check(ks.resolve("smarthome") is None, "non-prefixed junk -> None")
check(ks.resolve("") is None and ks.resolve(None) is None, "empty/None -> None")

raw_file = open(os.path.join(d, "api_keys.json")).read()
check(key not in raw_file, "raw key is NOT in api_keys.json (stored hashed)")
check(apikeys._hash(key) in raw_file, "sha256(key) IS in the file")

check(apikeys.KeyStore.scope_ok(ent, "/api/llm/chat"), "scoped key allowed under /api/llm")
check(apikeys.KeyStore.scope_ok(ent, "/api/llm"), "scoped key allowed at exact scope")
check(apikeys.KeyStore.scope_ok(ent, "/api/queue/9/cancel"), "scoped key allowed deep under /api/queue")
check(not apikeys.KeyStore.scope_ok(ent, "/api/admin/overview"), "scoped key DENIED on /api/admin")
check(not apikeys.KeyStore.scope_ok(ent, "/api/queuexyz"), "scope match respects path boundary (no /api/queuexyz)")
check(not apikeys.KeyStore.scope_ok(ent, "/api/screen/input"), "scoped key DENIED outside its scopes")

kid2, key2 = ks.create("owner", label="full")
ent2 = ks.resolve(key2)
check(ent2["scopes"] == [], "unscoped key has empty scopes")
check(apikeys.KeyStore.scope_ok(ent2, "/api/admin/overview"), "unscoped key allowed everywhere (full access)")

lst = ks.list()
check(len(lst) == 2 and all("hash" not in k for k in lst), "list() returns 2, never the hash")
check(ks.list("smarthome") == [k for k in lst if k["uid"] == "smarthome"], "list(uid) filters by principal")

check(ks.revoke(kid) is True, "revoke smarthome key -> True")
check(ks.resolve(key) is None, "revoked key no longer resolves")
check(ks.revoke("deadbeef") is False, "revoke unknown id -> False")
check(ks.revoke(kid2, owner_uid="smarthome") is False, "revoke wrong-owner denied")
check(ks.resolve(key2) is not None, "key2 still valid after failed cross-owner revoke")

ks2 = apikeys.KeyStore(d)
check(ks2.resolve(key2) is not None, "reloaded store still resolves key2")
check(ks2.resolve(key) is None, "reloaded store keeps revocation")

kide, keye = ks.create("owner", label="exp", ttl_days=7)
check(ks.resolve(keye) is not None, "ttl key resolves before expiry")
ks._keys[kide]["expires_at"] = 1
check(ks.resolve(keye) is None, "expired key -> None")
check(ks.create("owner", ttl_days=0)[0] and ks._keys[ks.create("owner")[0]]["expires_at"] == 0,
      "ttl_days=0 -> never expires (expires_at 0)")

kidr, keyr = ks.create("owner", label="rl", rate_per_min=3)
oks = [ks.rate_ok(kidr) for _ in range(4)]
check(oks == [True, True, True, False], f"rate_per_min=3 -> 3 ok then deny (got {oks})")
kidu, keyu = ks.create("owner", label="unl")
check(all(ks.rate_ok(kidu) for _ in range(30)), "unlimited key (rate_per_min=0) never limited")
check(ks.rate_ok("nonexistent") is False, "rate_ok on unknown id -> False")

kidx, keyx = ks.create("smarthome", label="pi", scopes=["/api/llm"])
before_hash = ks._keys[kidx]["hash"]
m = ks.update(kidx, scopes=["/api/llm", "/api/queue", "/api/tts"], label="pi-2", rate_per_min=5)
check(m is not None and set(m["scopes"]) == {"/api/llm", "/api/queue", "/api/tts"}, "update replaces scopes")
check(m["label"] == "pi-2" and m["rate_per_min"] == 5, "update relabels + re-rates")
check("hash" not in m, "update metadata never leaks the hash")
check(ks._keys[kidx]["hash"] == before_hash, "update does NOT change the secret hash")
r = ks.resolve(keyx)
check(r is not None and set(r["scopes"]) == {"/api/llm", "/api/queue", "/api/tts"},
      "rescope is LIVE — resolve() returns new scopes for the SAME secret")
check(apikeys.KeyStore.scope_ok(ks.resolve(keyx), "/api/queue/9/cancel") is True, "new scope grants sub-paths")
check(apikeys.KeyStore.scope_ok(ks.resolve(keyx), "/api/admin") is False, "un-granted path still denied after rescope")

ks.update(kidx, label="pi-3")
check(set(ks._keys[kidx]["scopes"]) == {"/api/llm", "/api/queue", "/api/tts"} and ks._keys[kidx]["label"] == "pi-3",
      "partial update leaves other fields intact")

ks.update(kidx, ttl_days=7)
check(ks._keys[kidx]["expires_at"] > 0, "update ttl_days>0 sets an expiry")
ks.update(kidx, ttl_days=0)
check(ks._keys[kidx]["expires_at"] == 0, "update ttl_days=0 clears expiry")

ks.update(kidx, scopes=["/api/llm", "bogus", 123, "/v1"])
check(set(ks._keys[kidx]["scopes"]) == {"/api/llm", "/v1"}, "update filters invalid scope entries")

check(ks.update(kidx, label="x", owner_uid="owner") is None, "update denied when owner_uid != key uid")
check(ks.update("nonexistent", label="x") is None, "update unknown id -> None")
ks.revoke(kidx)
check(ks.update(kidx, label="y") is None, "update on a revoked key -> None")

kidtb, keytb = ks.create("bob", label="time-boxed", scopes=["/api/llm"], ttl_days=7)
ks._keys[kidtb]["expires_at"] = 1
check(ks.update(kidtb, ttl_days=365) is None, "update on an EXPIRED key -> None (no revival)")
check(ks._keys[kidtb]["expires_at"] == 1, "rejected update did NOT re-anchor the expiry")
check(ks.resolve(keytb) is None, "expired key stays dead after a rejected update")

ks2 = apikeys.KeyStore(d)
kidp, keyp = ks2.create("owner", scopes=["/api/status"])
ks2.update(kidp, scopes=["/api/status", "/api/jobs"])
ks3 = apikeys.KeyStore(d)
check(set(ks3._keys[kidp]["scopes"]) == {"/api/status", "/api/jobs"}, "update persists to disk (survives reload)")

def _katalog_prefix_ok(pfx):

    if not isinstance(pfx, str):
        return False
    teile = pfx.split(" ", 1)
    if len(teile) == 2 and teile[0].isupper() and teile[0].isalpha():
        return teile[1].startswith("/")
    return pfx.startswith("/")

check(isinstance(apikeys.SCOPE_CATALOG, list) and len(apikeys.SCOPE_CATALOG) > 0
      and all(_katalog_prefix_ok(c.get("prefix")) for c in apikeys.SCOPE_CATALOG),
      "SCOPE_CATALOG entries are '/'-prefixed prefixes")

mode = oct(os.stat(os.path.join(d, "api_keys.json")).st_mode & 0o777)
check(mode == "0o600", f"api_keys.json is 0600 (got {mode})")

print(f"\n=== test_apikeys: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
