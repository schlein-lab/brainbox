#!/usr/bin/env python3

import os, sys, json, socket, time, tempfile, subprocess

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))

SCRATCH = tempfile.mkdtemp(prefix="pn-lease-e2e-")
SOCK = os.path.join(SCRATCH, "pn-llmd.sock")
SECRETS_DIR = os.path.join(SCRATCH, "secrets")
FAKE = os.path.join(SCRATCH, "fake_backend.sh")
with open(FAKE, "w") as f:
    f.write("#!/bin/sh\necho ok\n")
os.chmod(FAKE, 0o755)

def req(d, timeout=10):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(timeout)
    s.connect(SOCK)
    s.sendall((json.dumps(d) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        c = s.recv(65536)
        if not c:
            break
        buf += c
    s.close()
    return json.loads(buf.split(b"\n", 1)[0])

def main():
    env = dict(os.environ)
    env.update({
        "PN_LLM_SOCK": SOCK,
        "PN_SECRETS_DIR": SECRETS_DIR,
        "PN_SECRETS_ALLOW_INSECURE": "1",
        "PN_SECRETS_PASSPHRASE": "lease-e2e",
        "PN_LLM_CMD": f"{FAKE} {{model}}",
        "PN_LLM_POOL": "4",
        "PN_LLM_CANARY_INTERVAL": "99999",
    })
    proc = subprocess.Popen([sys.executable, os.path.join(REPO, "tools", "pn-llmd")],
                            env=env, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(80):
            if os.path.exists(SOCK):
                break
            time.sleep(0.1)
        print("\n== llm lease e2e: real pn-llmd, pool=4, FAKE backend ==")

        hr = req({"verb": "llm.headroom"})
        check("llm.headroom responds", hr.get("ok"), str(hr)[:120])
        check("fresh pool: 4/4 free", hr.get("llm_free") == 4 and hr.get("llm_pool") == 4,
              f"free={hr.get('llm_free')}")

        r = req({"verb": "lease", "job_id": 101, "weight": 2, "kind": "dedicated"})
        check("dedicated lease(2) ok", r.get("ok") and r.get("leased"), str(r)[:120])
        check("free -> 2 after dedicated(2)",
              req({"verb": "llm.headroom"}).get("llm_free") == 2)

        r = req({"verb": "lease", "job_id": 102, "weight": 8, "kind": "loose"})
        check("loose lease(8)=2 slots ok", r.get("ok"), str(r)[:120])
        check("free -> 0 (pool now full)",
              abs(req({"verb": "llm.headroom"}).get("llm_free")) < 1e-6)

        r = req({"verb": "lease", "job_id": 103, "weight": 1, "kind": "dedicated"})
        check("saturating lease BLOCKED (backpressure)", not r.get("ok") and r.get("blocked"),
              str(r)[:120])
        check("free still 0 after a blocked lease",
              abs(req({"verb": "llm.headroom"}).get("llm_free")) < 1e-6)

        req({"verb": "release", "job_id": 101})
        check("release restores 2 slots -> free 2",
              req({"verb": "llm.headroom"}).get("llm_free") == 2)
        r = req({"verb": "lease", "job_id": 103, "weight": 1, "kind": "dedicated"})
        check("after release the lease now fits", r.get("ok"), str(r)[:120])

        r = req({"verb": "release", "job_id": 999999})
        check("release of unknown job is a no-op", r.get("ok") and not r.get("released"))

        st = req({"verb": "status"})
        check("status surfaces llm_headroom", "llm_headroom" in st, str(st.keys()))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    print(f"\n=== llm_lease_e2e: {len(PASS)} passed, {len(FAIL)} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
