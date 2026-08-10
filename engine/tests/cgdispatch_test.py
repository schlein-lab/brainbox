#!/usr/bin/env python3

from __future__ import annotations
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

def _in_delegated_tree() -> bool:

    try:
        with open("/proc/self/cgroup") as f:
            rel = f.read().strip().split("::", 1)[1]
    except (OSError, IndexError):
        return False
    return "/user@" in rel + "/"

if not _in_delegated_tree() and not os.environ.get("PN_CGD_TEST_REEXEC"):
    import shutil as _sh
    if _sh.which("systemd-run"):
        env = dict(os.environ, PN_CGD_TEST_REEXEC="1")
        try:
            os.execvpe("systemd-run",
                       ["systemd-run", "--user", "--scope", "--quiet",
                        sys.executable, os.path.realpath(__file__)], env)
        except OSError:
            pass

from pnlib import dispatch as facade
from pnlib import cgdispatch as CG
from pnlib import cgsandbox as SB
from pnlib import slice as SL
from pnlib.profile import ResourceProfile

PASS, FAIL = 0, 0

def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {msg}")
    else:
        FAIL += 1
        print(f"  FAIL  {msg}")

def find_user_cg_base():
    try:
        with open("/proc/self/cgroup") as f:
            rel = f.read().strip().split("::", 1)[1]
    except (OSError, IndexError):
        return None
    base = "/sys/fs/cgroup" + rel
    cur = base
    while cur and cur != "/sys/fs/cgroup":
        if os.path.basename(cur).startswith("user@") and os.access(cur, os.W_OK):
            return cur
        cur = os.path.dirname(cur)
    cand = f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service"
    return cand if os.access(cand, os.W_OK) else None

class ScratchTier:

    def __init__(self):
        self.real = False
        self.root = None
        self.batch_dir = None
        self.scratch_files = tempfile.mkdtemp(prefix="pn-cgd-files-")
        base = find_user_cg_base()
        if not base:
            return
        try:
            root = os.path.join(base, f"pn-cgd-test-{os.getpid()}")
            os.makedirs(root, exist_ok=True)
            self.root = root

            for d in (base, root):
                try:
                    with open(os.path.join(d, "cgroup.subtree_control"), "w") as f:
                        f.write("+memory +cpu +pids")
                except OSError:
                    pass
            batch = os.path.join(root, "pn-batch.slice")
            os.makedirs(batch, exist_ok=True)

            try:
                with open(os.path.join(batch, "cgroup.subtree_control"), "w") as f:
                    f.write("+memory +cpu +pids")
            except OSError:
                pass
            self.batch_dir = batch
            self.real = True
        except OSError as e:
            print(f"  (note: real cgroup scratch unavailable: {e})")

    def cleanup(self):

        try:
            if self.batch_dir and os.path.isdir(self.batch_dir):
                for ent in os.listdir(self.batch_dir):
                    leaf = os.path.join(self.batch_dir, ent)
                    if os.path.isdir(leaf):
                        try:
                            with open(os.path.join(leaf, "cgroup.kill"), "w") as f:
                                f.write("1")
                        except OSError:
                            pass
                        time.sleep(0.05)
                        try:
                            os.rmdir(leaf)
                        except OSError:
                            pass
                try:
                    os.rmdir(self.batch_dir)
                except OSError:
                    pass
            if self.root and os.path.isdir(self.root):
                try:
                    os.rmdir(self.root)
                except OSError:
                    pass
        finally:
            shutil.rmtree(self.scratch_files, ignore_errors=True)

def _files(sc, jid):
    d = os.path.join(sc.scratch_files, f"job-{jid}")
    os.makedirs(d, exist_ok=True)
    return (os.path.join(d, "out"), os.path.join(d, "err"), os.path.join(d, "rc"))

def _wait_rc(proc, rc_path, out, err, timeout=20):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if proc is not None and proc.poll() is not None:
            break
        if proc is None and os.path.exists(rc_path):
            break
        time.sleep(0.05)

    for _ in range(40):
        rc = CG.read_rc(rc_path)
        if rc is not None:
            break
        time.sleep(0.05)
    o = open(out).read() if os.path.exists(out) else ""
    e = open(err).read() if os.path.exists(err) else ""
    return CG.read_rc(rc_path), o, e

PROBE = r'''
import json, os, ctypes
libc = ctypes.CDLL(None, use_errno=True)
d = {}
# cgroup membership (from inside the job)
try:
    with open("/proc/self/cgroup") as f: d["cgroup"] = f.read().strip()
except Exception as ex: d["cgroup"] = f"ERR:{ex}"
# /proc/self/status security bits
for line in open("/proc/self/status"):
    for k in ("NoNewPrivs","Seccomp","CapEff","CapBnd"):
        if line.startswith(k+":"): d[k] = line.split()[1]
# write to /usr must FAIL under ProtectSystem=strict (Landlock)
try:
    open("/usr/pn-cgd-should-fail","w").close(); d["usr_write"]="WROTE"
except OSError as ex: d["usr_write"]=f"DENIED:{ex.errno}"
# write to TMPDIR (private tmp) must SUCCEED
try:
    p=os.path.join(os.environ.get("TMPDIR","/tmp"),"pn_priv_ok")
    open(p,"w").close(); d["tmp_write"]="OK"; os.unlink(p)
except OSError as ex: d["tmp_write"]=f"DENIED:{ex.errno}"
# forbidden socket family AF_PACKET(17) must FAIL (RestrictAddressFamilies)
try:
    import socket
    s=socket.socket(17, socket.SOCK_RAW); s.close(); d["af_packet"]="ALLOWED"
except OSError as ex: d["af_packet"]=f"DENIED:{ex.errno}"
# allowed family AF_INET must WORK
try:
    import socket
    s=socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.close(); d["af_inet"]="OK"
except OSError as ex: d["af_inet"]=f"DENIED:{ex.errno}"
# mount() must FAIL (SystemCallFilter denylist) — raw syscall so no privilege pre-check masks it
r = libc.syscall(165, b"none", b"/mnt", b"tmpfs", 0, 0)  # x86_64 mount
d["mount_syscall"] = f"rc={r} errno={ctypes.get_errno()}"
print(json.dumps(d))
'''

def test_placement_and_enforcement(sc):
    print("[A]+[B] cgroup placement + sandbox enforcement (strict profile)")
    if not sc.real:
        print("  SKIP  no delegated cgroup on this box")
        return
    os.environ["PN_DISPATCH_BACKEND"] = "cgroup"
    os.environ["PN_CG_BATCH_DIR"] = sc.batch_dir
    os.environ["PN_JOB_SCRATCH_ROOT"] = os.path.join(sc.scratch_files, "scratch")
    facade.backend_name(force=True)
    check(facade.backend_name() == "cgroup", "facade forced to cgroup backend")

    jid = 90001
    out, err, rc = _files(sc, jid)
    cwd = os.path.join(sc.scratch_files, f"work-{jid}")
    os.makedirs(cwd, exist_ok=True)

    prof = ResourceProfile(mem=64, cpu_weight=30, sandbox="strict")
    props = prof.systemd_properties()

    props.append(f"ReadWritePaths={cwd}")
    argv = [sys.executable, "-c", PROBE]

    proc = facade.dispatch(jid, argv, cwd, {}, props, out, err, rc)
    check(proc is not None, "dispatch returned a live handle (not refused)")

    leaf = os.path.join(sc.batch_dir, f"pn-job-{jid}")
    code, o, e = _wait_rc(proc, rc, out, err)
    try:
        d = json.loads(o.strip().splitlines()[-1]) if o.strip() else {}
    except Exception:
        d = {}
    print("    probe:", json.dumps(d))
    if e.strip():
        print("    stderr:", e.strip()[:400])

    check(code == 0, f"job exited 0 (rc={code})")
    check(d.get("cgroup", "").endswith(f"/pn-job-{jid}"),
          f"[A] job pid was IN <tier>/pn-job-{jid}/cgroup.procs (self-reported cgroup={d.get('cgroup')})")
    check(d.get("NoNewPrivs") == "1", f"[B] NoNewPrivs:1 (got {d.get('NoNewPrivs')})")
    check(d.get("Seccomp") == "2", f"[B] Seccomp:2 active (got {d.get('Seccomp')})")
    check(d.get("CapEff") == "0000000000000000", f"[B] CapEff:0 all caps dropped (got {d.get('CapEff')})")
    check(str(d.get("usr_write", "")).startswith("DENIED"),
          f"[B] ProtectSystem: write to /usr DENIED (got {d.get('usr_write')})")
    check(d.get("tmp_write") == "OK", f"[B] PrivateTmp: TMPDIR writable (got {d.get('tmp_write')})")
    check(str(d.get("af_packet", "")).startswith("DENIED"),
          f"[B] RestrictAddressFamilies: AF_PACKET DENIED (got {d.get('af_packet')})")
    check(d.get("af_inet") == "OK", f"[B] allowed AF_INET still works (got {d.get('af_inet')})")
    check("errno=" in d.get("mount_syscall", "") and "errno=0" not in d.get("mount_syscall", ""),
          f"[B] SystemCallFilter: mount() blocked (got {d.get('mount_syscall')})")

def test_cgroup_caps(sc):
    print("[A2] cgroup CAPS — the job's leaf carries the profile-derived memory.max/oom.group/"
          "cpu.max/pids.max (governed, not just placed)")
    if not sc.real:
        print("  SKIP  no delegated cgroup on this box")
        return
    os.environ["PN_DISPATCH_BACKEND"] = "cgroup"
    os.environ["PN_CG_BATCH_DIR"] = sc.batch_dir
    facade.backend_name(force=True)
    jid = 90002
    out, err, rc = _files(sc, jid)
    cwd = os.path.join(sc.scratch_files, f"work-{jid}")
    os.makedirs(cwd, exist_ok=True)

    prof = ResourceProfile(mem=128, mem_max=64, cpu_weight=40, cpu_quota_pct=50, sandbox="compute")
    props = prof.systemd_properties()
    leaf = os.path.join(sc.batch_dir, f"pn-job-{jid}")

    proc = facade.dispatch(jid, [sys.executable, "-c", "import time;time.sleep(1.5)"],
                           cwd, {}, props, out, err, rc)
    check(proc is not None, "dispatch returned a handle")

    time.sleep(0.4)

    def rd(name):
        try:
            return open(os.path.join(leaf, name)).read().strip()
        except OSError:
            return None
    mm = rd("memory.max")
    oomg = rd("memory.oom.group")
    cpumax = rd("cpu.max")
    pmax = rd("pids.max")
    cw = rd("cpu.weight")
    procs = rd("cgroup.procs")
    print(f"    leaf caps: memory.max={mm} oom.group={oomg} cpu.max={cpumax} "
          f"pids.max={pmax} cpu.weight={cw}")
    check(mm == str(64 * 1024 * 1024), f"[A2] memory.max = 64MiB (got {mm})")
    check(oomg == "1", f"[A2] memory.oom.group = 1 (got {oomg})")
    check(cpumax == "50000 100000", f"[A2] cpu.max = 50% of one core (got {cpumax})")
    check(cw == "40", f"[A2] cpu.weight = 40 (got {cw})")
    check(pmax and pmax != "max", f"[A2] pids.max bounded (got {pmax})")
    check(bool(procs and procs.strip()), "[A2] leaf has the running job's pid")
    _wait_rc(proc, rc, out, err)

WSPROBE = r'''
import json, os
d = {}
secret = os.environ["PN_SECRET_FILE"]; work = os.environ["PN_WORK_DIR"]
try:
    open(secret).read(); d["read_secret"]="READ"          # must be DENIED (InaccessiblePaths)
except OSError as ex: d["read_secret"]=f"DENIED:{ex.errno}"
try:
    open(os.path.join(work,"artifact"),"w").close(); d["write_work"]="OK"  # must be OK (ReadWritePaths)
except OSError as ex: d["write_work"]=f"DENIED:{ex.errno}"
try:
    open("/usr/pn-x","w").close(); d["write_usr"]="WROTE"  # must be DENIED (ProtectSystem)
except OSError as ex: d["write_usr"]=f"DENIED:{ex.errno}"
try:
    d["read_etc"]="OK" if open("/etc/hostname").read() else "EMPTY"  # must be OK
except OSError as ex: d["read_etc"]=f"DENIED:{ex.errno}"
print(json.dumps(d))
'''

def test_worker_strict_inaccessible(sc):
    print("[B2] worker-strict InaccessiblePaths — job CANNOT read the sealed secrets, CAN write "
          "only its own scratch (The Record invariant 2)")
    if not sc.real:
        print("  SKIP  no delegated cgroup on this box")
        return
    os.environ["PN_DISPATCH_BACKEND"] = "cgroup"
    os.environ["PN_CG_BATCH_DIR"] = sc.batch_dir
    facade.backend_name(force=True)
    jid = 90005
    out, err, rc = _files(sc, jid)

    root = os.path.join(sc.scratch_files, f"record-{jid}")
    work = os.path.join(root, "work")
    secrets = os.path.join(root, "secrets")
    os.makedirs(work, exist_ok=True)
    os.makedirs(secrets, exist_ok=True)
    secret_file = os.path.join(secrets, "brain.key")
    with open(secret_file, "w") as f:
        f.write("TOPSECRET-BRAIN-CREDENTIAL")
    prof = ResourceProfile(mem=64, sandbox="worker-strict")
    props = prof.systemd_properties()
    props.append(f"ReadWritePaths={work}")
    props.append(f"InaccessiblePaths={secrets}")
    env = {"PN_SECRET_FILE": secret_file, "PN_WORK_DIR": work}
    proc = facade.dispatch(jid, [sys.executable, "-c", WSPROBE], work, env, props, out, err, rc)
    code, o, e = _wait_rc(proc, rc, out, err)
    try:
        d = json.loads(o.strip().splitlines()[-1]) if o.strip() else {}
    except Exception:
        d = {}
    print("    probe:", json.dumps(d))
    if e.strip():
        print("    stderr:", e.strip()[:300])
    check(str(d.get("read_secret", "")).startswith("DENIED"),
          f"[B2] InaccessiblePaths: secret UNREADABLE (got {d.get('read_secret')})")
    check(d.get("write_work") == "OK", f"[B2] own scratch writable (got {d.get('write_work')})")
    check(str(d.get("write_usr", "")).startswith("DENIED"),
          f"[B2] ProtectSystem: /usr read-only (got {d.get('write_usr')})")
    check(d.get("read_etc") == "OK", f"[B2] non-secret paths still readable (got {d.get('read_etc')})")

def test_fail_closed_no_leaf(sc):
    print("[C1] FAIL-CLOSED — cgroup leaf cannot be created -> job REFUSED")
    os.environ["PN_DISPATCH_BACKEND"] = "cgroup"

    bad = "/proc/sys/pn-does-not-exist"
    os.environ["PN_CG_BATCH_DIR"] = bad
    facade.backend_name(force=True)
    jid = 90010
    out, err, rc = _files(sc, jid)
    cwd = sc.scratch_files
    prof = ResourceProfile(sandbox="strict")
    proc = facade.dispatch(jid, argv=[sys.executable, "-c", "open('/usr/x','w')"],
                           cwd=cwd, env={}, props=prof.systemd_properties(),
                           out_path=out, err_path=err, rc_path=rc)
    check(proc is None, "dispatch REFUSED (returned None) when the cgroup leaf is unavailable")
    check(CG.read_rc(rc) == CG.RC_SANDBOX_REFUSED,
          f"refusal rc={CG.RC_SANDBOX_REFUSED} written (got {CG.read_rc(rc)})")
    ee = open(err).read() if os.path.exists(err) else ""
    check("REFUSED" in ee, "refusal reason logged to .err (never ran unsandboxed/ungoverned)")

    if sc.real:
        os.environ["PN_CG_BATCH_DIR"] = sc.batch_dir

def test_fail_closed_no_landlock(sc):
    print("[C2] FAIL-CLOSED — Landlock unavailable -> job REFUSED (never runs)")
    if not sc.real:
        print("  SKIP  no delegated cgroup on this box")
        return
    os.environ["PN_DISPATCH_BACKEND"] = "cgroup"
    os.environ["PN_CG_BATCH_DIR"] = sc.batch_dir
    facade.backend_name(force=True)

    orig = SB.landlock_available
    CG.cgsandbox.landlock_available = lambda: 0
    try:
        jid = 90020
        out, err, rc = _files(sc, jid)
        cwd = os.path.join(sc.scratch_files, f"work-{jid}")
        os.makedirs(cwd, exist_ok=True)
        proc = facade.dispatch(jid, argv=[sys.executable, "-c", "print('SHOULD NOT RUN')"],
                               cwd=cwd, env={}, props=ResourceProfile(sandbox="strict").systemd_properties(),
                               out_path=out, err_path=err, rc_path=rc)
        check(proc is None, "dispatch REFUSED (None) when Landlock is unavailable")
        check(CG.read_rc(rc) == CG.RC_SANDBOX_REFUSED, "refusal rc written")
        o = open(out).read() if os.path.exists(out) else ""
        check("SHOULD NOT RUN" not in o, "the job body NEVER executed (no stdout produced)")
    finally:
        CG.cgsandbox.landlock_available = orig

def test_seccomp_program_builds():
    print("[B'] seccomp ALLOWLIST BPF: builds, no 8-bit-offset fail-open, default-denies, x32 kill")
    prog = SB.build_seccomp_filter([SB._AF["AF_INET"], SB._AF["AF_INET6"]])
    check(10 < len(prog) < 4096, f"program length sane ({len(prog)} insns)")

    ok = all(0 <= jt <= 255 and 0 <= jf <= 255
             for (code, jt, jf, k) in prog if code & 0x07 == 0x05)
    check(ok, "all seccomp jump offsets fit 8 bits (fail-open structurally impossible)")

    last = prog[-1]
    is_deny_default = (last[0] == (SB._BPF_RET | SB._BPF_K)
                       and last[3] != SB.SECCOMP_RET_ALLOW)
    check(is_deny_default, f"default action is DENY, not ALLOW (final ret k=0x{last[3]:x})")

    has_x32 = (SB._X32_BIT == 0) or any(
        c == (SB._BPF_JMP | SB._BPF_JGE | SB._BPF_K) and k == SB._X32_BIT for (c, jt, jf, k) in prog)
    check(has_x32, "x32 gate present (JGE X32_BIT -> KILL) on x86_64")

    allow_nrs = set()
    for i, (c, jt, jf, k) in enumerate(prog):
        if (c & 0x07) == 0x05 and (c & 0xf0) == 0x10 and i + 1 < len(prog):
            nxt = prog[i + 1]
            if nxt[0] == (SB._BPF_RET | SB._BPF_K) and nxt[3] == SB.SECCOMP_RET_ALLOW:
                allow_nrs.add(k)
    danger = {n: SB._NR[n] for n in ("io_uring_setup", "userfaultfd", "memfd_create", "unshare",
              "setns", "ptrace", "mount", "bpf", "name_to_handle_at", "pidfd_getfd", "keyctl",
              "move_pages", "personality") if n in SB._NR}
    leaks = [n for n, nr in danger.items() if nr in allow_nrs]
    check(not leaks, f"no dangerous syscall in the ALLOW set (leaks={leaks})")

ALLOWPROBE = r'''
import json, os, ctypes, socket
libc = ctypes.CDLL(None, use_errno=True)
d = {}
def sc(nr, *a):
    ctypes.set_errno(0)
    r = libc.syscall(nr, *a)
    return f"rc={r} errno={ctypes.get_errno()}"
# allowlist default-DENY on syscalls NOT in @system-service / subtracted:
d["memfd_create"] = sc(319, b"x", 0)              # must be EPERM(1)
d["io_uring_setup"] = sc(425, 1, 0)               # must be EPERM(1)
d["userfaultfd"] = sc(323, 0)                     # must be EPERM(1)
d["name_to_handle_at"] = sc(303, 0, 0, 0, 0, 0)   # must be EPERM(1)
d["pidfd_getfd"] = sc(438, 0, 0, 0)               # must be EPERM(1)
d["ptrace"] = sc(101, 0, 0, 0, 0)                 # must be EPERM(1)
# IORING_OP_SOCKET path is moot once io_uring_setup is denied; assert setup denied (above).
# forbidden families:
def fam(f):
    try:
        s = socket.socket(f, socket.SOCK_DGRAM if f != 17 else socket.SOCK_RAW); s.close(); return "ALLOWED"
    except OSError as e: return f"DENIED:{e.errno}"
d["af_vsock"] = fam(40)      # AF_VSOCK must be DENIED
d["af_packet"] = fam(17)     # AF_PACKET must be DENIED
d["af_unix"] = fam(1)        # AF_UNIX must be DENIED (default set = INET/INET6 only)
d["af_inet"] = fam(2)        # AF_INET must be ALLOWED
# essential allowed calls still work:
try: d["getpid"] = "OK:%d" % os.getpid()
except OSError as e: d["getpid"] = f"DENIED:{e.errno}"
try:
    fd = os.open("/etc/hostname", os.O_RDONLY); os.read(fd, 4); os.close(fd); d["read_etc"] = "OK"
except OSError as e: d["read_etc"] = f"DENIED:{e.errno}"
print(json.dumps(d))
'''

def _dispatch_probe(sc, jid, probe, sandbox="strict", env=None, extra_props=None, cwd=None):

    os.environ["PN_DISPATCH_BACKEND"] = "cgroup"
    os.environ["PN_CG_BATCH_DIR"] = sc.batch_dir
    os.environ["PN_JOB_SCRATCH_ROOT"] = os.path.join(sc.scratch_files, "scratch")
    facade.backend_name(force=True)
    out, err, rc = _files(sc, jid)
    if cwd is None:
        cwd = os.path.join(sc.scratch_files, f"work-{jid}")
        os.makedirs(cwd, exist_ok=True)
    prof = ResourceProfile(mem=64, sandbox=sandbox)
    props = prof.systemd_properties()
    if extra_props:
        props += extra_props
    proc = facade.dispatch(jid, [sys.executable, "-c", probe], cwd, env or {}, props, out, err, rc)
    code, o, e = _wait_rc(proc, rc, out, err)
    try:
        d = json.loads(o.strip().splitlines()[-1]) if o.strip() else {}
    except Exception:
        d = {}
    return proc, code, d, e

def test_allowlist_enforcement(sc):
    print("[H1] seccomp ALLOWLIST enforced in a real job: io_uring/memfd/userfaultfd/x32-class "
          "default-DENIED; AF_VSOCK/AF_PACKET/AF_UNIX denied; AF_INET + essentials work")
    if not sc.real:
        print("  SKIP  no delegated cgroup on this box")
        return

    proc, code, d, e = _dispatch_probe(sc, 91001, ALLOWPROBE, sandbox="compute")
    print("    probe:", json.dumps(d))
    if e.strip():
        print("    stderr:", e.strip()[:300])
    check(code == 0, f"probe exited 0 (rc={code})")
    for name in ("memfd_create", "io_uring_setup", "userfaultfd", "name_to_handle_at",
                 "pidfd_getfd", "ptrace"):
        check("errno=1" in d.get(name, ""),
              f"[H1] {name} default-DENIED (EPERM) (got {d.get(name)})")
    check(str(d.get("af_vsock", "")).startswith("DENIED"),
          f"[H1] AF_VSOCK denied (got {d.get('af_vsock')})")
    check(str(d.get("af_packet", "")).startswith("DENIED"),
          f"[H1] AF_PACKET denied (got {d.get('af_packet')})")
    check(str(d.get("af_unix", "")).startswith("DENIED"),
          f"[H1] AF_UNIX denied by DEFAULT family set (LL-03) (got {d.get('af_unix')})")
    check(d.get("af_inet") == "ALLOWED", f"[H1] AF_INET allowed (got {d.get('af_inet')})")
    check(str(d.get("getpid", "")).startswith("OK"), f"[H1] getpid works (got {d.get('getpid')})")
    check(d.get("read_etc") == "OK", f"[H1] read /etc works (got {d.get('read_etc')})")

ENVPROBE = r'''
import json, os
d = {"env_keys": sorted(os.environ.keys())}
# the injected fake secrets MUST NOT be visible to the job:
d["saw_BRAIN"] = "BRAIN_API_KEY" in os.environ
d["saw_ANTHROPIC"] = "ANTHROPIC_API_KEY" in os.environ
d["saw_RELAY"] = "PN_RELAY_SECRET" in os.environ
d["saw_declared"] = os.environ.get("PN_JOB_ID")   # a profile-declared var SHOULD be present
print(json.dumps(d))
'''

def test_env_scrub(sc):
    print("[H2] ENV SCRUB (N1): the job inherits NO pnd secret; only allowlisted + declared vars")
    if not sc.real:
        print("  SKIP  no delegated cgroup on this box")
        return

    os.environ["BRAIN_API_KEY"] = "sk-brain-SECRET"
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-SECRET"
    os.environ["PN_RELAY_SECRET"] = "relay-SECRET"
    try:
        _, code, d, e = _dispatch_probe(sc, 91002, ENVPROBE, sandbox="strict",
                                        env={"PN_JOB_ID": "91002"})
        print("    job env keys:", d.get("env_keys"))
        check(code == 0, f"probe exited 0 (rc={code})")
        check(d.get("saw_BRAIN") is False, "[H2] job does NOT see BRAIN_API_KEY")
        check(d.get("saw_ANTHROPIC") is False, "[H2] job does NOT see ANTHROPIC_API_KEY")
        check(d.get("saw_RELAY") is False, "[H2] job does NOT see PN_RELAY_SECRET")
        check(d.get("saw_declared") == "91002", "[H2] profile-declared PN_JOB_ID IS present")
    finally:
        for k in ("BRAIN_API_KEY", "ANTHROPIC_API_KEY", "PN_RELAY_SECRET"):
            os.environ.pop(k, None)

CGESCAPE_PROBE = r'''
import json, os
d = {}
# read our own cgroup
try: d["cgroup"] = open("/proc/self/cgroup").read().strip()
except Exception as ex: d["cgroup"] = f"ERR:{ex}"
# try to ESCAPE the leaf: write our pid to the parent tier's cgroup.procs (governance bypass)
tier = os.environ["PN_TIER_DIR"]
target = os.path.join(tier, "cgroup.procs")
try:
    with open(target, "w") as f: f.write(str(os.getpid()))
    d["escape"] = "WROTE"      # BAD: we left our leaf
except OSError as ex: d["escape"] = f"DENIED:{ex.errno}"
# also try creating a sibling cgroup + moving there
sib = os.path.join(tier, "pn-escape")
try:
    os.mkdir(sib); d["mkdir"] = "MADE"
except OSError as ex: d["mkdir"] = f"DENIED:{ex.errno}"
# and try writing to /sys/fs/cgroup root
try:
    open("/sys/fs/cgroup/cgroup.procs", "w").write(str(os.getpid())); d["root_cg"] = "WROTE"
except OSError as ex: d["root_cg"] = f"DENIED:{ex.errno}"
print(json.dumps(d))
'''

def test_cgroup_leaf_escape_blocked(sc):
    print("[H3] leaf-escape blocked (F1 CRIT): job cannot write ANY cgroup.procs to leave its leaf")
    if not sc.real:
        print("  SKIP  no delegated cgroup on this box")
        return
    _, code, d, e = _dispatch_probe(sc, 91003, CGESCAPE_PROBE, sandbox="strict",
                                    env={"PN_TIER_DIR": sc.batch_dir})
    print("    probe:", json.dumps(d))
    if e.strip():
        print("    stderr:", e.strip()[:300])
    check(code == 0, f"probe exited 0 (rc={code})")
    check(str(d.get("escape", "")).startswith("DENIED"),
          f"[H3] writing the tier cgroup.procs DENIED (got {d.get('escape')})")
    check(str(d.get("mkdir", "")).startswith("DENIED"),
          f"[H3] creating a sibling cgroup DENIED (got {d.get('mkdir')})")
    check(str(d.get("root_cg", "")).startswith("DENIED"),
          f"[H3] writing the cgroup root procs DENIED (got {d.get('root_cg')})")

CWDPROBE = r'''
import json, os
d = {}
# cwd is where we start; under F2 it must NOT be writable (only declared rw_paths + private tmp are)
try:
    open(os.path.join(os.getcwd(), "pn_cwd_write"), "w").close(); d["cwd_write"] = "WROTE"
except OSError as ex: d["cwd_write"] = f"DENIED:{ex.errno}"
# the declared workspace (PN_WORKSPACE) MUST be writable
ws = os.environ.get("PN_WORKSPACE")
try:
    open(os.path.join(ws, "pn_ws_write"), "w").close(); d["ws_write"] = "OK"
except OSError as ex: d["ws_write"] = f"DENIED:{ex.errno}"
d["cwd"] = os.getcwd(); d["ws"] = ws
print(json.dumps(d))
'''

def test_cwd_not_writable(sc):
    print("[H4] cwd NOT writable (F2): attacker-controlled cwd does not punch through ProtectSystem; "
          "only the DECLARED workspace is writable")
    if not sc.real:
        print("  SKIP  no delegated cgroup on this box")
        return

    cwd = os.path.join(sc.scratch_files, "attacker-cwd")
    ws = os.path.join(sc.scratch_files, "declared-ws")
    os.makedirs(cwd, exist_ok=True)
    os.makedirs(ws, exist_ok=True)
    _, code, d, e = _dispatch_probe(sc, 91004, CWDPROBE, sandbox="strict",
                                    env={"PN_WORKSPACE": ws},
                                    extra_props=[f"ReadWritePaths={ws}"], cwd=cwd)
    print("    probe:", json.dumps(d))
    check(code == 0, f"probe exited 0 (rc={code})")
    check(str(d.get("cwd_write", "")).startswith("DENIED"),
          f"[H4] writing the (undeclared) cwd DENIED (got {d.get('cwd_write')})")
    check(d.get("ws_write") == "OK",
          f"[H4] the DECLARED workspace is writable (got {d.get('ws_write')})")

def test_default_profile_has_landlock(sc):
    print("[H5] default/compute profile HAS Landlock + family filter (F3): no zero-confinement job")
    if not sc.real:
        print("  SKIP  no delegated cgroup on this box")
        return

    proc, code, d, e = _dispatch_probe(sc, 91005, PROBE, sandbox="compute")
    print("    probe:", json.dumps(d))
    check(code == 0, f"compute-profile probe exited 0 (rc={code})")
    check(d.get("NoNewPrivs") == "1", f"[H5] NoNewPrivs set for compute (got {d.get('NoNewPrivs')})")
    check(d.get("Seccomp") == "2", f"[H5] seccomp active for compute (got {d.get('Seccomp')})")
    check(str(d.get("usr_write", "")).startswith("DENIED"),
          f"[H5] Landlock: /usr read-only for compute (got {d.get('usr_write')})")
    check(str(d.get("af_packet", "")).startswith("DENIED"),
          f"[H5] family filter: AF_PACKET denied for compute (got {d.get('af_packet')})")

def test_swap_max_set(sc):
    print("[H6] memory.swap.max set on the leaf (N6): a job cannot evade memory.max via swap")
    if not sc.real:
        print("  SKIP  no delegated cgroup on this box")
        return
    os.environ["PN_DISPATCH_BACKEND"] = "cgroup"
    os.environ["PN_CG_BATCH_DIR"] = sc.batch_dir
    facade.backend_name(force=True)
    jid = 91006
    out, err, rc = _files(sc, jid)
    cwd = os.path.join(sc.scratch_files, f"work-{jid}")
    os.makedirs(cwd, exist_ok=True)
    prof = ResourceProfile(mem=128, mem_max=64, sandbox="compute")
    leaf = os.path.join(sc.batch_dir, f"pn-job-{jid}")
    proc = facade.dispatch(jid, [sys.executable, "-c", "import time;time.sleep(1.2)"],
                           cwd, {}, prof.systemd_properties(), out, err, rc)
    time.sleep(0.35)
    try:
        swap = open(os.path.join(leaf, "memory.swap.max")).read().strip()
    except OSError:
        swap = None
    print(f"    memory.swap.max = {swap}")
    check(swap == "0", f"[H6] memory.swap.max = 0 (no-swap) on a hard-capped leaf (got {swap})")
    _wait_rc(proc, rc, out, err)

REALJOB = r'''
# a representative "real" job: imports stdlib heavy modules, spawns a subprocess (fork+exec+wait),
# does file IO in the workspace, opens a TCP socket, uses threads — must all WORK under the allowlist.
import json, os, subprocess, socket, threading, hashlib, tempfile
ws = os.environ["PN_WORKSPACE"]
d = {}
# subprocess (fork+execve+wait): a common real-job pattern (ffmpeg-like external tool)
try:
    r = subprocess.run(["/bin/echo", "hello"], capture_output=True, text=True, timeout=10)
    d["subprocess"] = r.stdout.strip()
except Exception as ex: d["subprocess"] = f"ERR:{type(ex).__name__}:{ex}"
# file IO in the declared workspace
try:
    p = os.path.join(ws, "out.bin")
    with open(p, "wb") as f: f.write(os.urandom(4096))
    d["file_io"] = hashlib.sha256(open(p,"rb").read()).hexdigest()[:8]
except Exception as ex: d["file_io"] = f"ERR:{ex}"
# a TCP socket (AF_INET allowed)
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.settimeout(0.1); s.close(); d["tcp"] = "OK"
except Exception as ex: d["tcp"] = f"ERR:{ex}"
# threads (clone for a thread must be allowed)
res = []
t = threading.Thread(target=lambda: res.append(sum(range(1000)))); t.start(); t.join()
d["thread"] = res[0] if res else None
print(json.dumps(d))
'''

def test_real_job_runs(sc):
    print("[H7] a representative real job (subprocess + file IO + TCP + threads) RUNS under the "
          "allowlist (not over-restricted)")
    if not sc.real:
        print("  SKIP  no delegated cgroup on this box")
        return
    ws = os.path.join(sc.scratch_files, "realjob-ws")
    os.makedirs(ws, exist_ok=True)
    _, code, d, e = _dispatch_probe(sc, 91007, REALJOB, sandbox="compute",
                                    env={"PN_WORKSPACE": ws},
                                    extra_props=[f"ReadWritePaths={ws}"])
    print("    probe:", json.dumps(d))
    if e.strip():
        print("    stderr:", e.strip()[:300])
    check(code == 0, f"[H7] real job exited 0 (rc={code})")
    check(d.get("subprocess") == "hello", f"[H7] subprocess (fork+exec+wait) works (got {d.get('subprocess')})")
    check(len(str(d.get("file_io", ""))) == 8, f"[H7] workspace file IO works (got {d.get('file_io')})")
    check(d.get("tcp") == "OK", f"[H7] TCP socket works (got {d.get('tcp')})")
    check(d.get("thread") == sum(range(1000)), f"[H7] threads work (got {d.get('thread')})")

def test_dual_backend_systemd():
    print("[D] DUAL-BACKEND — systemd path still works on THIS box (existing dispatch green)")

    os.environ.pop("PN_DISPATCH_BACKEND", None)
    os.environ.pop("PN_CG_BATCH_DIR", None)
    facade.backend_name(force=True)
    sel = facade.backend_name()
    print("    auto-selected backend:", sel, "| describe:", facade.describe())
    if sel != "systemd":
        print("  SKIP  no systemd user bus here (would use cgroup backend)")
        return
    check(True, "auto-selection picks systemd when the user bus is reachable")

    base = os.path.join(os.path.expanduser("~"), ".cache", "pn-cgd-test")
    os.makedirs(base, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="pn-sysd-", dir=base)
    try:
        out, err, rc = (os.path.join(tmp, "o"), os.path.join(tmp, "e"), os.path.join(tmp, "rc"))
        prof = ResourceProfile(mem=64, sandbox="compute")
        props = prof.systemd_properties()
        argv = [sys.executable, "-c",
                "import sys;print(open('/proc/self/status').read().count('NoNewPrivs'));"
                "sys.exit(0)"]
        proc = facade.dispatch(70001, argv, tmp, {}, props, out, err, rc)
        check(proc is not None, "systemd-run dispatch returned a handle")
        t0 = time.monotonic()
        while proc.poll() is None and time.monotonic() - t0 < 25:
            time.sleep(0.1)
        code = facade.read_rc(rc)
        check(code == 0, f"systemd-dispatched job exited 0 (rc={code})")
        st = facade.unit_status(70001)
        check(isinstance(st, dict) and "active" in st, "unit_status works via facade -> slice")
        facade.stop_unit(70001)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def main():
    print("=" * 78)
    print("cgdispatch_test — cgroup-direct dispatch security boundary")
    print(f"  arch={SB._MACH}  landlock_abi={SB.landlock_available()}  "
          f"seccomp_arch=0x{SB.AUDIT_ARCH:x}  bwrap_usable={facade.bwrap_usable()}")
    print("=" * 78)
    sc = ScratchTier()
    print(f"  scratch cgroup real={sc.real} batch_dir={sc.batch_dir}")
    try:
        test_placement_and_enforcement(sc)
        test_cgroup_caps(sc)
        test_worker_strict_inaccessible(sc)

        test_allowlist_enforcement(sc)
        test_env_scrub(sc)
        test_cgroup_leaf_escape_blocked(sc)
        test_cwd_not_writable(sc)
        test_default_profile_has_landlock(sc)
        test_swap_max_set(sc)
        test_real_job_runs(sc)

        test_fail_closed_no_leaf(sc)
        test_fail_closed_no_landlock(sc)
        test_seccomp_program_builds()
        test_dual_backend_systemd()
    finally:
        sc.cleanup()

        for k in ("PN_DISPATCH_BACKEND", "PN_CG_BATCH_DIR", "PN_JOB_SCRATCH_ROOT"):
            os.environ.pop(k, None)
    print("=" * 78)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    print("=" * 78)
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
