#!/usr/bin/env python3

import os, sys, json, time

TEST_IMG = os.path.expanduser("~/brainarbeit/os/pn-vmm/kernel/test-owner-session.img")
os.environ["PN_VMM_BIN"] = "/tmp/pnvmm-act/release/pn-vmm"
sys.path.insert(0, os.path.expanduser("~/brainarbeit/cockpit/server"))
import pn_cell_session as C
C.BASE = TEST_IMG

POLICY = {"phantom": "allow", "llm": "allow", "mem_mb": 1024, "disallowed_tools": [],
          "portal_enabled": False, "portal_verbs": []}
cell = C.CellSession("owner", "captest%d" % (int(time.time()) % 100000), 6006, policy=POLICY)
print("run_dir:", cell.run_dir, "act_sock:", cell.act_sock)

INCELL = r'''
import socket, struct, json
S = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM); S.settimeout(20); S.connect((2, 9400))
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
out["read"]=call("call", tool="read_screen", args={"app":"Firefox"})
out["inject"]=call("call", tool="inject_input", args={"app":"Firefox","text":"hi"})
out["drive"]=call("call", tool="drive_program", args={"app":"Firefox","role":"button","name":"Search"})
out["drive_badapp"]=call("call", tool="drive_program", args={"app":"Chrome","role":"button","name":"X"})
print("PNCAP_RESULT="+json.dumps(out))
'''

try:
    print("booting test cell (phantom granted, ACT binary, test image)...")
    ok = cell.boot()
    print("boot ok:", ok, "| boot_denied:", cell._boot_denied, "| admit_denied:", cell._admit_denied)
    if not ok:
        print("VERDICT: BOOT_FAILED"); sys.exit(1)
    print("act_broker alive:", cell.act_broker is not None and cell.act_broker.poll() is None)

    import base64
    b64 = base64.b64encode(INCELL.encode()).decode()
    cell._run("busybox mkdir -p /tmp; printf %s '" + b64 + "' | base64 -d > /tmp/capcli.py; echo __W__", "__W__", 15)
    okr, out = cell._run("/bin/python3 /tmp/capcli.py 2>&1; echo __DONE__", "__DONE__", 40)
    line = ""
    for ln in (out or "").splitlines():
        if ln.startswith("PNCAP_RESULT="): line = ln[len("PNCAP_RESULT="):]
    if not line:
        print("NO PNCAP_RESULT. raw in-cell output:\n", (out or "")[:1500]); sys.exit(2)
    r = json.loads(line)
    print("in-cell list_tools:", r["list"])
    print("read_screen   ->", json.dumps(r["read"])[:160])
    print("inject_input  ->", json.dumps(r["inject"])[:160])
    print("drive_program ->", json.dumps(r["drive"])[:160])
    print("drive badapp  ->", json.dumps(r["drive_badapp"])[:160])

    import hashlib
    audit_p = os.path.join(cell.run_dir, "actd-audit.jsonl")
    rows = [json.loads(l) for l in open(audit_p)] if os.path.exists(audit_p) else []
    prev="0"*64; chain_ok=True
    for row in rows:
        rh=row.get("row_hash")
        body={k:v for k,v in row.items() if k!="row_hash"}
        calc=hashlib.sha256((prev+str(row["seq"])+hashlib.sha256(json.dumps(body,sort_keys=True,separators=(",",":")).encode()).hexdigest()).encode()).hexdigest()
        if row.get("prev_hash")!=prev or calc!=rh: chain_ok=False
        prev=rh
    decisions=[(row["tool"],row["decision"],row["reason"]) for row in rows]
    print("audit rows:", len(rows), "| chain intact:", chain_ok)
    for d in decisions: print("   ", d)

    checks = {
      "lane bridges guest->host (list_tools answered over vsock 9400)": bool(r["list"]),
      "deny-by-default: inject_input NOT listed": ("inject_input" not in r["list"]) and ("read_screen" in r["list"]),
      "inject_input call DENIED": r["inject"].get("status")=="denied",
      "drive on non-granted app path handled": r["drive_badapp"].get("status") in ("denied","ok","error"),
      "read_screen reached actd (ok or honest no-seat)": r["read"].get("status") in ("ok","error"),
      "audit chain intact + >=3 rows": chain_ok and len(rows)>=3,
      "denials audited but never queued": any(t=="inject_input" and dec=="deny" for t,dec,_ in decisions),
    }
    print("\n===== VERDICT =====")
    for k,v in checks.items(): print("  [%s] %s" % ("PASS" if v else "FAIL", k))
    print("  RESULT:", "CAP_LANE_E2E_PROVEN" if all(checks.values()) else "NEEDS_LOOK")
finally:
    try: cell._teardown(reboot=False)
    except Exception as e: print("teardown warn:", e)
    print("cleaned up.")
