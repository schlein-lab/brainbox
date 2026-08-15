#!/usr/bin/env python3

import os, sys, json, socket, tempfile, threading, time, subprocess, grp, stat

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from pnlib import ipc

PASS, FAIL = 0, 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")

def _send(sockpath, req, timeout=10, read=True):

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(sockpath)
    try:
        s.sendall((json.dumps(req) + "\n").encode())
        if not read:
            return None
        buf = b""
        while b"\n" not in buf:
            ch = s.recv(65536)
            if not ch:
                break
            buf += ch
        return json.loads(buf.split(b"\n", 1)[0].decode()) if buf else {}
    finally:
        s.close()

def _pick_test_group():

    my_gids = set(os.getgroups()) | {os.getgid()}

    for gid in sorted(my_gids):
        try:
            g = grp.getgrgid(gid)
            return g.gr_name, g.gr_gid
        except KeyError:
            continue
    g = grp.getgrgid(os.getgid())
    return g.gr_name, g.gr_gid

def _boot_pnd(rt, data, broker_sock, broker_group, broker_uids, extra_setup=""):

    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = rt
    env["XDG_DATA_HOME"] = data
    env["PN_DURABILITY"] = "normal"
    env.pop("NOTIFY_SOCKET", None)
    env["PND_BROKER_SOCK"] = broker_sock
    env["PND_BROKER_GROUP"] = broker_group
    env["PND_BROKER_UIDS"] = broker_uids
    boot = os.path.join(rt, "boot.py")
    with open(boot, "w") as f:
        f.write(
            "import sys, runpy\n"
            f"sys.path.insert(0, {ROOT!r})\n"
            "from pnlib import sched, db, DB_PATH\n"
            "_orig = sched.Config.autoscale\n"
            "def _permissive():\n"
            "    c=_orig(); c.psi_stop=1e9; c.mem_floor=1; c.batch_high=1<<30; c.slack=0; return c\n"
            "sched.Config.autoscale = staticmethod(_permissive)\n"
            "cx = db.connect(DB_PATH)\n"
            + extra_setup +
            "cx.commit()\n"
            f"runpy.run_path({os.path.join(ROOT, 'tools', 'pnd')!r}, run_name='__main__')\n")
    proc = subprocess.Popen([sys.executable, boot], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return proc

def _wait_for(path, tries=80, delay=0.1):
    for _ in range(tries):
        if os.path.exists(path):
            return True
        time.sleep(delay)
    return False

def run():
    uid = os.getuid()
    gname, gid = _pick_test_group()
    tmp = tempfile.mkdtemp(prefix="pn_brokersock_")
    rt = os.path.join(tmp, "rt"); os.makedirs(rt)
    data = os.path.join(tmp, "data"); os.makedirs(data)
    broker_sock = os.path.join(tmp, "pnd-broker.sock")
    default_sock = os.path.join(rt, "pnd.sock")

    setup = (
        f"cx.execute(\"INSERT OR REPLACE INTO principals(name,uid,kind,note) "
        f"VALUES('relay-adapter',{uid},'system','de-privileged broker (test)')\")\n"
        "cx.execute(\"INSERT OR IGNORE INTO principals(name,uid,kind,note) "
        "VALUES('dev-owner',60011,'user','remote device owner (test)')\")\n"
        "for p,c in [('relay-adapter','act-as'),('relay-adapter','task_type:echo.test'),"
        "('dev-owner','task_type:echo.test'),('dev-owner','task_type:sleep.test')]:\n"
        "    if not cx.execute('SELECT 1 FROM grants WHERE principal=? AND cap=?',(p,c)).fetchone():\n"
        "        cx.execute('INSERT INTO grants(principal,cap) VALUES(?,?)',(p,c))\n"
        "db.bind_identity(cx, 'device-channel', 'dev1', 'dev-owner', verified=1)\n"
    )
    proc = _boot_pnd(rt, data, broker_sock, gname, str(uid), extra_setup=setup)
    try:
        print("[A] BOTH sockets bind, run the same handle(); modes/group are as specified")
        up_default = _wait_for(default_sock)
        up_broker = _wait_for(broker_sock)
        check(up_default, "default uid-only socket is up")
        check(up_broker, "broker socket is up (second listener)")
        if not (up_default and up_broker):
            err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
            print("  --- pnd stderr ---\n" + err[-2000:])
            return

        rp_def = _send(default_sock, {"verb": "ping"})
        rp_brk = _send(broker_sock, {"verb": "ping"})
        check(rp_def.get("ok") and rp_brk.get("ok"), "ping succeeds on BOTH sockets")
        check(rp_def.get("pid") == rp_brk.get("pid") and rp_def.get("pid"),
              f"both sockets are served by the SAME pnd process (pid {rp_def.get('pid')}) -> same daemon state")
        check(rp_def.get("version") == rp_brk.get("version"),
              f"both sockets run the same handle() (version {rp_def.get('version')})")

        st_def = os.stat(default_sock)
        st_brk = os.stat(broker_sock)
        m_def = stat.S_IMODE(st_def.st_mode)
        m_brk = stat.S_IMODE(st_brk.st_mode)
        check(m_def == 0o600, f"default socket is 0600 uid-only (got {oct(m_def)})")
        check(m_brk == 0o660, f"broker socket is 0660 group-accessible (got {oct(m_brk)})")
        check(st_brk.st_gid == gid,
              f"broker socket is group-owned by '{gname}' (gid {gid}; got gid {st_brk.st_gid})")
        check(st_def.st_gid != gid or os.getgid() == gid,
              "default socket group ownership is NOT widened to the broker group "
              f"(default gid {st_def.st_gid})")

        print("[B] a broker connection over the broker socket resolves de-privileged + intersects caps")

        r_ok = _send(broker_sock, {"verb": "submit", "task_type": "echo.test",
                                   "params": {"msg": "hi"},
                                   "_method": "device-channel", "_selector": "dev1",
                                   "via_device": "dev1"})
        check(r_ok.get("ok") and r_ok.get("id"),
              f"broker submit (echo.test, broker∩submitter) ACCEPTED over the broker socket: {r_ok}")

        r_int = _send(broker_sock, {"verb": "submit", "task_type": "sleep.test",
                                    "params": {"s": "1"},
                                    "_method": "device-channel", "_selector": "dev1",
                                    "via_device": "dev1"})
        check(not r_int.get("ok") and "sleep.test" in (r_int.get("error") or ""),
              f"broker submit of a task_type the BROKER lacks is REJECTED (relay∩submitter): {r_int.get('error')!r}")

        r_raw = _send(broker_sock, {"verb": "submit", "cmd": ["/bin/sh", "-c", "echo pwned"],
                                    "_method": "device-channel", "_selector": "dev1",
                                    "via_device": "dev1"})
        check(not r_raw.get("ok") and "raw" in (r_raw.get("error") or "").lower(),
              f"a RAW command over the broker socket is REJECTED (task.raw not in eff caps): {r_raw.get('error')!r}")

        r_ceil = _send(broker_sock, {"verb": "submit", "task_type": "echo.test",
                                     "params": {"msg": "x"}, "_ceiling_caps": [],
                                     "_method": "device-channel", "_selector": "dev1",
                                     "via_device": "dev1"})
        check(not r_ceil.get("ok"),
              f"an empty _ceiling_caps clamps eff caps to nothing (broker∩submitter∩ceiling): {r_ceil.get('error')!r}")

        print("[C] the #20 IPC hardening applies to the broker socket too")
        blob = b"A" * (ipc.MAX_FRAME + (1 << 16))
        dropped = False
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5); s.connect(broker_sock)
        try:
            try:
                for _ in range(0, len(blob), 1 << 16):
                    s.sendall(blob[:1 << 16])
                s.shutdown(socket.SHUT_WR)
                dropped = (s.recv(65536) == b"")
            except (BrokenPipeError, ConnectionResetError, OSError):
                dropped = True
        finally:
            s.close()
        check(dropped, "oversize newline-free frame on the broker socket -> connection dropped (MAX_FRAME ceiling)")

        after = _send(broker_sock, {"verb": "ping"})
        check(after.get("ok"), "the broker socket keeps serving a normal request after the flood")

        check(_send(default_sock, {"verb": "ping"}).get("ok"),
              "the default socket is unaffected by a broker-socket flood (shared slot released)")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    tmp2 = tempfile.mkdtemp(prefix="pn_brokersock_neg_")
    rt2 = os.path.join(tmp2, "rt"); os.makedirs(rt2)
    data2 = os.path.join(tmp2, "data"); os.makedirs(data2)
    broker_sock2 = os.path.join(tmp2, "pnd-broker.sock")

    setup2 = (
        f"cx.execute(\"INSERT OR REPLACE INTO principals(name,uid,kind,note) "
        f"VALUES('plain-user',{uid},'user','non-broker caller (test)')\")\n"
        "cx.execute(\"INSERT OR IGNORE INTO principals(name,uid,kind,note) "
        "VALUES('dev-owner',60011,'user','remote device owner (test)')\")\n"
        "for p,c in [('plain-user','task_type:echo.test'),"
        "('dev-owner','task_type:echo.test'),('dev-owner','task_type:sleep.test')]:\n"
        "    if not cx.execute('SELECT 1 FROM grants WHERE principal=? AND cap=?',(p,c)).fetchone():\n"
        "        cx.execute('INSERT INTO grants(principal,cap) VALUES(?,?)',(p,c))\n"
        "db.bind_identity(cx, 'device-channel', 'dev1', 'dev-owner', verified=1)\n"
    )
    proc2 = _boot_pnd(rt2, data2, broker_sock2, gname, str(uid + 9999), extra_setup=setup2)
    try:
        print("[D] NEGATIVE: a non-broker uid connecting to the broker socket gets ONLY its own authz")
        check(_wait_for(broker_sock2), "negative-case broker socket up")

        r_actas = _send(broker_sock2, {"verb": "submit", "task_type": "echo.test",
                                       "params": {"msg": "x"},
                                       "_method": "device-channel", "_selector": "dev1"})
        check(not r_actas.get("ok") and "broker" in (r_actas.get("error") or "").lower(),
              f"a NON-broker uid in the group cannot use _method/_selector to act-as: {r_actas.get('error')!r}")

        r_self = _send(broker_sock2, {"verb": "submit", "task_type": "echo.test",
                                      "params": {"msg": "self"}})
        check(r_self.get("ok") and r_self.get("id"),
              f"a direct submit over the broker socket runs AS the connecting uid's own principal: {r_self}")

        r_self_raw = _send(broker_sock2, {"verb": "submit", "cmd": ["/bin/sh", "-c", "echo x"]})
        check(not r_self_raw.get("ok") and "raw" in (r_self_raw.get("error") or "").lower(),
              f"a non-broker, non-admin caller still cannot run raw over the broker socket: {r_self_raw.get('error')!r}")
    finally:
        proc2.terminate()
        try:
            proc2.wait(timeout=5)
        except Exception:
            proc2.kill()

    tmp3 = tempfile.mkdtemp(prefix="pn_brokersock_fc_")
    rt3 = os.path.join(tmp3, "rt"); os.makedirs(rt3)
    data3 = os.path.join(tmp3, "data"); os.makedirs(data3)
    broker_sock3 = os.path.join(tmp3, "pnd-broker.sock")

    setup3 = (
        f"cx.execute(\"INSERT OR REPLACE INTO principals(name,uid,kind,note) "
        f"VALUES('admin-broker',{uid},'user','over-privileged broker (test)')\")\n"
        "cx.execute(\"INSERT OR IGNORE INTO principals(name,uid,kind,note) "
        "VALUES('dev-owner',60011,'user','remote device owner (test)')\")\n"
        "for p,c in [('admin-broker','act-as'),('admin-broker','task.raw'),"
        "('admin-broker','task_type:*'),('admin-broker','view:all'),"
        "('dev-owner','task_type:echo.test')]:\n"
        "    if not cx.execute('SELECT 1 FROM grants WHERE principal=? AND cap=?',(p,c)).fetchone():\n"
        "        cx.execute('INSERT INTO grants(principal,cap) VALUES(?,?)',(p,c))\n"
        "db.bind_identity(cx, 'device-channel', 'dev1', 'dev-owner', verified=1)\n"
    )
    proc3 = _boot_pnd(rt3, data3, broker_sock3, gname, str(uid), extra_setup=setup3)
    try:
        print("[E] NEGATIVE: broker-fail-closed rejects an admin-resolving broker over the broker socket")
        check(_wait_for(broker_sock3), "fail-closed-case broker socket up")
        r_fc = _send(broker_sock3, {"verb": "submit", "task_type": "echo.test",
                                    "params": {"msg": "x"},
                                    "_method": "device-channel", "_selector": "dev1"})
        check(not r_fc.get("ok") and "de-privileged" in (r_fc.get("error") or "").lower(),
              f"an admin-wildcard broker is REFUSED over the broker socket (#20 fail-closed): {r_fc.get('error')!r}")
    finally:
        proc3.terminate()
        try:
            proc3.wait(timeout=5)
        except Exception:
            proc3.kill()
        subprocess.run(["rm", "-rf", tmp, tmp2, tmp3])

def main():
    print("=== pnd broker socket — group-accessible second listener, authz UNCHANGED ===")
    run()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
