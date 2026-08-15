#!/usr/bin/env python3

import os, sys, json, subprocess, tempfile
REPO = os.environ.get("BRAINBOX_REPO", os.path.expanduser("~/portioneer"))
sys.path.insert(0, REPO)
from relaylib import deaddrop
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption, PublicFormat

RUNNER = os.path.join(REPO, "tools", "pn-cell-sealed-run")

cx_priv, cx_pub, cid_seed, cid_pub = deaddrop.gen_cell_keys()
ks = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
json.dump({"cell_x_priv": cx_priv.hex(), "cell_x_pub": cx_pub.hex(),
           "cell_id_seed": cid_seed.hex(), "cell_id_pub": cid_pub.hex()}, ks)
ks.close()

dev_id = Ed25519PrivateKey.generate()
dev_id_priv = dev_id.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
dev_x = X25519PrivateKey.generate()
dev_x_priv = dev_x.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
dev_x_pub = dev_x.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

def run_case(command, level, default_autonomy=1):
    inner = {"command": command, "autonomy": level}
    env = deaddrop.seal_cell_request(cx_pub, dev_id_priv, "topic", "cell.exec", inner, counter=1)
    p = subprocess.run(["/usr/bin/python3", RUNNER, "run", "--device-x-pub", dev_x_pub.hex(),
                        "--autonomy", str(default_autonomy)],
                       input=json.dumps(env), capture_output=True, text=True,
                       env={**os.environ, "BRAINBOX_CELL_KEYS": ks.name})
    if p.returncode != 0:
        return {"state": "RUNNER_ERROR", "stderr": p.stderr[:200]}
    renv = json.loads(p.stdout)
    return deaddrop.device_open_result(dev_x_priv, dev_x_pub, cid_pub, renv)

cases = [
    ("echo lesen-ist-ok",            1, "done",           "read @ L1 -> auto-run"),
    ("rm -rf /tmp/pn-dry-old",       2, "needs_approval",  "destructive @ L2 -> APPROVAL (not run)"),
    ("rm -f /tmp/pn-dry-old",        3, "done",            "destructive @ L3 -> auto-run"),
    ("echo send mail an client",     4, "needs_approval",  "external @ L4 -> APPROVAL"),
    ("echo send mail an client",     5, "done",            "external @ L5 -> auto-run"),
    ("echo hi",                      0, "needs_approval",  "L0 -> confirm everything (even read)"),
    ("touch /tmp/pn-dry.txt",        2, "done",            "write @ L2 -> auto-run"),
]

print("=== GOVERNED AUTONOMY dry-run (real seal -> in-cell gate -> seal -> device open) ===")
ok = True
for command, level, expect, desc in cases:
    res = run_case(command, level)
    got = res.get("state")
    good = (got == expect)
    ok = ok and good
    extra = ""
    if got == "done" and "stdout" in res:
        extra = " out=%r" % (res.get("stdout", "").strip()[:30])
    if got == "needs_approval":
        extra = " class=%s" % res.get("action_class")
    print("  [%s] %-42s L%d -> %-14s (want %s)%s" %
          ("PASS" if good else "FAIL", desc, level, got, expect, extra))

os.unlink(ks.name)
print("\n  RESULT:", "AUTONOMY ENFORCEMENT PASS" if ok else "INCOMPLETE")
sys.exit(0 if ok else 1)
