#!/usr/bin/env python3

import os, sys, json, time, re, socket, hashlib, base64

ATSPID_SOCK = sys.argv[1]
RECORD = sys.argv[2]
if not os.path.exists(ATSPID_SOCK):
    print("seat socket missing (%s) — run seat_up.sh first" % ATSPID_SOCK); sys.exit(1)
os.environ["PN_ATSPID_SOCK"] = ATSPID_SOCK

TEST_IMG = os.path.expanduser("~/brainarbeit/os/pn-vmm/kernel/test-owner-session.img")
os.environ["PN_VMM_BIN"] = "/tmp/pnvmm-act/release/pn-vmm"
sys.path.insert(0, os.path.expanduser("~/brainarbeit/cockpit/server"))
import pn_cell_session as C
C.BASE = TEST_IMG

POLICY = {"phantom": "allow", "llm": "allow", "mem_mb": 1024, "disallowed_tools": [],
          "portal_enabled": False, "portal_verbs": []}
cell = C.CellSession("owner", "seat%d" % (int(time.time()) % 100000), 6007, policy=POLICY)
print("run_dir:", cell.run_dir, "| act_sock:", cell.act_sock, "| seat:", ATSPID_SOCK)

def atspid_direct(req):

    s = socket.socket(socket.AF_UNIX); s.settimeout(10); s.connect(ATSPID_SOCK)
    s.sendall((json.dumps(req) + "\n").encode())
    data = b""
    while True:
        try:
            d = s.recv(65536)
        except Exception:
            break
        if not d:
            break
        data += d
    s.close()
    return json.loads(data.decode())

INCELL = r'''
import socket, struct, json
S = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM); S.settimeout(30); S.connect((2, 9400))
def _rn(n):
    b=b""
    while len(b)<n:
        d=S.recv(n-len(b))
        if not d: raise IOError("closed")
        b+=d
    return b
def call(op, **kw):
    req={"op":op}; req.update(kw); body=json.dumps(req).encode()
    S.sendall(struct.pack("!I",len(body))+body)
    while True:
        (ln,)=struct.unpack("!I", _rn(4)); msg=json.loads(_rn(ln))
        if msg.get("status")!="queued": return msg
out={}
lt=call("list_tools"); out["list"]=[t["name"] for t in lt.get("result",[])]
out["read_before"]=call("call", tool="read_screen", args={"app":"coop"})
out["drive"]=call("call", tool="drive_program", args={"app":"coop","role":"push button","name":"Increment","action":"click"})
out["read_after"]=call("call", tool="read_screen", args={"app":"coop"})
out["inject"]=call("call", tool="inject_input", args={"app":"coop","keys":"x"})
out["drive_pw"]=call("call", tool="drive_program", args={"app":"coop","role":"password text","name":"Secret","action":"activate"})
out["drive_pw_lie"]=call("call", tool="drive_program", args={"app":"coop","role":"text","name":"Secret","action":"activate"})
print("PNCAP_RESULT="+json.dumps(out))
'''

def counts_in(reply):
    return sorted(set(int(x) for x in re.findall(r"count: (\d+)", json.dumps(reply))))

try:
    print("booting seat-proof cell (phantom granted, ACT binary, test image)...")
    ok = cell.boot()
    print("boot ok:", ok, "| boot_denied:", cell._boot_denied, "| admit_denied:", cell._admit_denied)
    if not ok:
        print("VERDICT: BOOT_FAILED"); sys.exit(1)
    print("act_broker alive:", cell.act_broker is not None and cell.act_broker.poll() is None)

    b64 = base64.b64encode(INCELL.encode()).decode()
    cell._run("busybox mkdir -p /tmp; printf %s '" + b64 + "' | base64 -d > /tmp/seatcli.py; echo __W__", "__W__", 15)
    okr, out = cell._run("/bin/python3 /tmp/seatcli.py 2>&1; echo __DONE__", "__DONE__", 90)
    line = ""
    for ln in (out or "").splitlines():
        if ln.startswith("PNCAP_RESULT="):
            line = ln[len("PNCAP_RESULT="):]
    if not line:
        print("NO PNCAP_RESULT. raw in-cell output:\n", (out or "")[:1800]); sys.exit(2)
    r = json.loads(line)
    drv = r["drive"].get("result") or {}
    print("tools:", r["list"])
    print("drive       ->", json.dumps(r["drive"])[:200])
    print("inject      ->", json.dumps(r["inject"])[:160])
    print("drive_pw    ->", json.dumps(r["drive_pw"])[:200])
    print("drive_pw_lie->", json.dumps(r["drive_pw_lie"])[:200])
    before, after = counts_in(r["read_before"]), counts_in(r["read_after"])
    print("counter before:", before, "| after:", after)

    rec_rows = [json.loads(l) for l in open(RECORD)] if os.path.exists(RECORD) else []
    invoked = [x for x in rec_rows if x.get("verb") == "invoke" and x.get("ok")]

    tree = atspid_direct({"verb": "read_tree", "app": "coop", "text": 1})
    pw_nodes = [n for n in tree.get("nodes", []) if "password" in (n.get("role") or "").lower()]
    leak = "s3cret-fixture" in json.dumps(tree)
    redact_ok = bool(pw_nodes) and all(n.get("text_redacted") and "text" not in n for n in pw_nodes) and not leak

    audit_p = os.path.join(cell.run_dir, "actd-audit.jsonl")
    rows = [json.loads(l) for l in open(audit_p)] if os.path.exists(audit_p) else []
    prev = "0" * 64; chain_ok = True
    for row in rows:
        rh = row.get("row_hash")
        body = {k: v for k, v in row.items() if k != "row_hash"}
        calc = hashlib.sha256((prev + str(row["seq"]) + hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()).encode()).hexdigest()
        if row.get("prev_hash") != prev or calc != rh:
            chain_ok = False
        prev = rh
    decisions = [(row["tool"], row["decision"], row["reason"]) for row in rows]
    print("audit rows:", len(rows), "| chain intact:", chain_ok)
    for d in decisions:
        print("   ", d)

    checks = {
        "lane up + deny-by-default toolset (inject_input absent)":
            bool(r["list"]) and "inject_input" not in r["list"] and "drive_program" in r["list"],
        "REAL button driven: invoke ok, tier 1, did=click":
            r["drive"].get("status") == "ok" and drv.get("ok") is True
            and drv.get("tier") == 1 and "click" in str(drv.get("did", "")).lower(),
        "REAL effect: counter label advanced 0 -> 1":
            0 in before and 1 in after and 1 not in before,
        "seat record sink saw the invoke": bool(invoked),
        "inject_input still DENIED on a live seat": r["inject"].get("status") == "denied",
        "password field DENIED at the gate (secure_field_blocked)":
            r["drive_pw"].get("status") == "denied"
            and "secure_field_blocked" in json.dumps(r["drive_pw"]),
        "lying about the role STILL cannot act on the password node":
            r["drive_pw_lie"].get("status") == "ok"
            and (r["drive_pw_lie"].get("result") or {}).get("ok") is False
            and "secure field" in str((r["drive_pw_lie"].get("result") or {}).get("error", "")).lower(),
        "password field REDACTED in read_tree text (no fixture leak)": redact_ok,
        "audit chain intact, drive allowed + denials recorded":
            chain_ok and any(t == "drive_program" and d == "allow" for t, d, _ in decisions)
            and any(t == "inject_input" and d == "deny" for t, d, _ in decisions),
    }
    print("\n===== VERDICT =====")
    for k, v in checks.items():
        print("  [%s] %s" % ("PASS" if v else "FAIL", k))
    print("  RESULT:", "CAP_SEAT_E2E_PROVEN" if all(checks.values()) else "NEEDS_LOOK")
finally:
    try:
        cell._teardown(reboot=False)
    except Exception as e:
        print("teardown warn:", e)
    print("cleaned up.")
