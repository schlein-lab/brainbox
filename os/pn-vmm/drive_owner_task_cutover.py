#!/usr/bin/env python3

import subprocess, threading, os, sys, time, tempfile, shutil, json

D = os.path.dirname(os.path.realpath(__file__)); os.chdir(D)
BIN = "./target/release/pn-vmm"; KERNEL = "kernel/vmlinux.bin"; INITRD = "kernel/initramfs-cell.cpio"
BASE = "kernel/base-owner-task.img"; DELTA = "kernel/delta-ownertask.img"; VOL = "kernel/taskvol.img"
CID = "18"; TIMEOUT = 210
LLM_SOCK = os.path.join(tempfile.gettempdir(), "pn-ot-llm.sock"); BLOG = "/tmp/pn-http-broker.log"
FXSTATE = os.path.join(tempfile.gettempdir(), "pn-effects-ot"); GRANT_OUT = os.path.join(tempfile.gettempdir(), "pn-ot-grantout")
env_fx = dict(os.environ); env_fx["PN_FX_STATE"] = FXSTATE
TASK_TEXT = "Brainarbeit runs each tenant inside a de-privileged microVM cell that reaches the model and files only through governed vsock planes.\n"

for pth in (FXSTATE, GRANT_OUT):
    shutil.rmtree(pth, ignore_errors=True)
os.makedirs(GRANT_OUT)
for f in (LLM_SOCK, BLOG):
    if os.path.exists(f):
        os.unlink(f)

def seed_vol():
    grant = tempfile.mkdtemp(prefix="pn-tgrant-")
    open(os.path.join(grant, "task.txt"), "w").write(TASK_TEXT)
    subprocess.run([sys.executable, "build_work_volume.py", "seed", grant, VOL, "128", "4096"],
                   check=True, capture_output=True, text=True)

def fresh_delta():
    subprocess.run(["truncate", "-s", "128M", DELTA], check=True)
    stg = tempfile.mkdtemp(); os.makedirs(os.path.join(stg, "upper")); os.makedirs(os.path.join(stg, "work"))
    open(os.path.join(stg, "upper", "seed"), "wb").write(os.urandom(64))
    subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q", "-d", stg, DELTA], check=True)

def fx(*a):
    return subprocess.run([sys.executable, "pn_effects.py", *a], capture_output=True, text=True, env=env_fx)

def boot_once():
    seed_vol(); fresh_delta()
    broker = subprocess.Popen([sys.executable, "pn_cell_http_broker.py", "--unix-mux", LLM_SOCK])
    t0 = time.time()
    while not os.path.exists(LLM_SOCK) and time.time() - t0 < 10:
        time.sleep(0.1)
    time.sleep(0.4)
    env = dict(os.environ); env["PN_VMM_BLK"] = "%s,%s,%s" % (BASE, DELTA, VOL)
    env["PN_VMM_VSOCK"] = CID; env["PN_VMM_VSOCK_LLM"] = LLM_SOCK
    p = subprocess.Popen([BIN, KERNEL, INITRD], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0, env=env)
    serial = []

    def sr():
        for raw in iter(p.stdout.readline, b""):
            serial.append(raw.decode(errors="replace"))

    threading.Thread(target=sr, daemon=True).start()
    t0 = time.time()
    while time.time() - t0 < TIMEOUT:
        if any("PN_CELL_WORK_DONE" in x for x in serial):
            break
        if p.poll() is not None:
            break
        time.sleep(0.5)
    time.sleep(1)
    for proc in (p, broker):
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return "".join(serial)

sblob = ""
for attempt in range(1, 4):
    sblob = boot_once()
    if "PN_CELL_OWNERTASK_ALIVE" in sblob:
        break
    print("attempt %d: cell stall, retrying" % attempt)

blog = open(BLOG).read() if os.path.exists(BLOG) else ""
result_line = [l for l in sblob.splitlines() if l.startswith("PN_TASK_RESULT=")]
model_result = result_line[0].split("PN_TASK_RESULT=", 1)[1].strip() if result_line else ""
print("===== CAGED OWNER-AGENT SERIAL =====")
for l in sblob.splitlines():
    if l.startswith("PN_CELL") or l.startswith("PN_HOME") or l.startswith("PN_WORK") or l.startswith("PN_INCELL") or l.startswith("PN_TASK"):
        print(l[:220])

print("INGEST:", fx("ingest", VOL, "cell18", GRANT_OUT).stdout.strip())
rows = [json.load(open(os.path.join(FXSTATE, "pending", f)))
        for f in os.listdir(os.path.join(FXSTATE, "pending"))] if os.path.isdir(os.path.join(FXSTATE, "pending")) else []
pend = [r for r in rows if r.get("state") == "PENDING" and r.get("target_name") == "summary.txt"]
before = os.path.exists(os.path.join(GRANT_OUT, "summary.txt"))
applied = provenance = False
if pend:
    fid = pend[0]["id"]
    print("APPROVE:", fx("approve", fid).stdout.strip())
    dst = os.path.join(GRANT_OUT, "summary.txt")
    applied = os.path.exists(dst) and open(dst).read().strip() == model_result.strip() and bool(model_result)
    prov = open(os.path.join(FXSTATE, "provenance.log")).read() if os.path.exists(os.path.join(FXSTATE, "provenance.log")) else ""
    provenance = fid in prov and "host_sig" in prov

checks = {
    "caged owner-agent booted (1 GiB)":              "PN_CELL_OWNERTASK_ALIVE" in sblob,
    "$HOME ABSENT":                        "PN_HOME=ABSENT_GOOD" in sblob,
    "working volume /work mounted":                  "PN_WORK_MOUNTED" in sblob,
    "in-cell model proxy up (vsock 9100)":           "PN_INCELL_PROXY_READY" in sblob,
    "broker served the model over governed vsock":   "/v1/messages" in blog,
    "caged claude produced a task RESULT":           bool(model_result),
    "result PROPOSED as a governed effect":          "PN_TASK_PROPOSED" in sblob,
    "effect ingested PENDING (needs confirm)":       bool(pend),
    "NOT applied before human confirm":              not before,
    "relay applied result ONLY after confirm":       applied,
    "signed provenance recorded":                    provenance,
}
print("\n===== VERDICT (LIVE CUTOVER: caged owner-agent, real governed task, all planes) =====")
for k, v in checks.items():
    print("  [%s] %s" % ("PASS" if v else "FAIL", k))
print("  CAGED AGENT SAID:", repr(model_result[:160]))
print("  RESULT:", "CUTOVER FULL-STACK PASS" if all(checks.values()) else "INCOMPLETE")
