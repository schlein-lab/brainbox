#!/usr/bin/env python3
import os, socket, subprocess, time, tempfile, sys, signal

PH = os.environ.get("PHANTOM_BIN", os.path.expanduser("~/phantom/target/release/phantom"))

def start(policy):
    rt = tempfile.mkdtemp(prefix=f"phcap-{policy}-")
    env = dict(os.environ, XDG_RUNTIME_DIR=rt)
    if policy: env["PHANTOM_CAP"] = policy
    p = subprocess.Popen([PH, "--headless", "phantom-0"], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ctl = os.path.join(rt, "phantom.ctl")
    for _ in range(100):
        if os.path.exists(ctl): break
        time.sleep(0.03)
    return p, rt, ctl

def send(ctl, line):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(ctl)
    s.sendall((line + "\n").encode())
    s.shutdown(socket.SHUT_WR)
    data = b""
    while True:
        c = s.recv(4096)
        if not c: break
        data += c
    s.close()
    return data.decode(errors="replace").strip()

fails = []
def check(name, cond, got):
    print(("PASS" if cond else "FAIL"), name, "->", repr(got))
    if not cond: fails.append(name)

p, rt, ctl = start("cold")
tokfile = os.path.join(rt, "phantom", "cap.token")
token = open(tokfile).read().strip() if os.path.exists(tokfile) else ""
check("cold: token file exists & is 64 hex", len(token) == 64, token[:12] + "…")
check("cold: token file is 0600", oct(os.stat(tokfile).st_mode & 0o777) == "0o600", oct(os.stat(tokfile).st_mode & 0o777))
check("cold: list is allowed (discovery)", "no clients" in send(ctl, "list").lower() or "cid=" in send(ctl, "list"), send(ctl, "list"))
r = send(ctl, "act @x type hi")
check("cold: act DENIED without token", "capability denied" in r, r)
r = send(ctl, "snapshot 0 /tmp/x.png")
check("cold: snapshot DENIED without token", "capability denied" in r, r)
r = send(ctl, f"tok:{token} act @x type hi")
check("cold: act ALLOWED with token (passes gate)", "capability denied" not in r, r)
r = send(ctl, f"tok:deadbeef act @x type hi")
check("cold: act DENIED with WRONG token", "capability denied" in r, r)
r = send(ctl, "cap-status")
check("cold: cap-status reports policy=cold", "policy=cold" in r, r)
p.send_signal(signal.SIGTERM); p.wait()

p2, rt2, ctl2 = start(None)
r = send(ctl2, "act @x type hi")
check("default(same-uid): act NOT denied for owner", "capability denied" not in r, r)
r = send(ctl2, "cap-status")
check("default: cap-status reports policy=same-uid", "policy=same-uid" in r, r)
p2.send_signal(signal.SIGTERM); p2.wait()

print()
if fails:
    print("FAILURES:", fails); sys.exit(1)
print("ALL CAPABILITY-GATE TESTS PASSED")
