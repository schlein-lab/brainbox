#!/usr/bin/env python3

import subprocess, threading, socket, os, sys, time

SOCK = "/tmp/pn-seat.sock"
CELL = "seatcell"
PNC = ["python3", "pn-celld.py"]

HOST_HOME = os.path.expanduser("~")
HOST_USER = os.path.basename(HOST_HOME)

subprocess.run(PNC + ["destroy", CELL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run(PNC + ["create", CELL, "--tenant", "seatuser", "--mem", "512M", "--cpu", "50%"], check=True)

if os.path.exists(SOCK):
    os.unlink(SOCK)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(SOCK); srv.listen(1); srv.settimeout(90)
proof = {"buf": b""}

def portal():
    try:
        conn, _ = srv.accept()
    except socket.timeout:
        print("!! portal: VMM never connected"); return
    conn.settimeout(30)
    while b"PN_SEAT_READY" not in proof["buf"]:
        try:
            d = conn.recv(4096)
        except socket.timeout:
            break
        if not d:
            break
        proof["buf"] += d
    time.sleep(0.5)
    for c in (b"id\n",
              b"echo SEAT_HOSTHOME=$([ -e %s ] && echo BAD || echo GOOD)\n" % HOST_HOME.encode(),
              b"echo SEAT_LS=$(ls / | tr '\\n' ',')\n",
              b"echo SEAT_DONE\n"):
        try:
            conn.sendall(c)
        except OSError:
            break
        time.sleep(0.2)
    t0 = time.time()
    while b"SEAT_DONE" not in proof["buf"] and time.time() - t0 < 15:
        try:
            d = conn.recv(4096)
        except socket.timeout:
            break
        if not d:
            break
        proof["buf"] += d
    try:
        conn.sendall(b"busybox reboot -f\n")
    except OSError:
        pass
    time.sleep(1); conn.close()

threading.Thread(target=portal, daemon=True).start()

env = dict(os.environ); env["PN_VMM_VSOCK_SEAT"] = SOCK
p = subprocess.Popen(PNC + ["run", CELL, "--no-quota"], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0, env=env)
serial = []

def sreader():
    for raw in iter(p.stdout.readline, b""):
        s = raw.decode(errors="replace"); sys.stdout.write("[serial] " + s); sys.stdout.flush(); serial.append(s)

threading.Thread(target=sreader, daemon=True).start()
try:
    rc = p.wait(timeout=110)
except subprocess.TimeoutExpired:
    p.kill(); rc = -9

out = proof["buf"].decode(errors="replace")
sblob = "".join(serial)
ls_line = out.split("SEAT_LS=")[1].split("\n")[0] if "SEAT_LS=" in out else ""
print("\n===== PORTAL received OVER VSOCK =====\n" + out)
print("===== VERDICT (vsock portal seat) =====")
checks = {
    "VMM in BRIDGE mode":                 "BRIDGE ->" in sblob,
    "cell handed shell to vsock":          "PN_SEAT_READY" in out,
    "shell driven over vsock (uid=0)":     "uid=0" in out,
    "cell sees NO host home":              "SEAT_HOSTHOME=GOOD" in out,
    "cell root=overlay, no host home":      bool(ls_line) and HOST_USER not in ls_line,
    "clean exit":                          rc == 0 or "clean guest exit" in sblob,
}
for k, v in checks.items():
    print("  [%s] %s" % ("PASS" if v else "FAIL", k))
print("  exit:", rc, " root ls:", ls_line)
print("  RESULT:", "VSOCK PORTAL SEAT PASS" if all(checks.values()) else "INCOMPLETE")
