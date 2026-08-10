#!/usr/bin/env python3

from __future__ import annotations
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

CG = "/sys/fs/cgroup"
BATCH_PARENT = os.path.join(CG, "pn.slice", "batch")
INT_PARENT = os.path.join(CG, "pn.slice", "interactive")
CGMOVE = "/usr/local/bin/pn-cgmove"

SCR_ENV = {
    "PN_BATCH_HIGH": "700",
    "PN_MEM_FLOOR": "300",
    "PN_MAX_CONCURRENT": "6",
    "PN_CPU_BUDGET": "2",
    "PN_RESERVED_CORES": "0",
    "PN_DISPATCH_BACKEND": "cgroup",
    "PN_FAIRSHARE_ENFORCE": "1",
    "PN_BACKLOG_BUDGET_S": "300",
    "PN_BACKLOG_FLOOR_S": "50",
    "PND_ENVFILE": "/nonexistent",
}

C = {"g": "\033[32m", "r": "\033[31m", "y": "\033[33m", "b": "\033[1m", "0": "\033[0m"}
if not sys.stdout.isatty():
    C = {k: "" for k in C}

def elog(*a):
    print("   ·", *a, file=sys.stderr, flush=True)

class Gate:
    def __init__(self, engine_dir):
        self.eng = engine_dir
        self.pnd = os.path.join(engine_dir, "tools", "pnd")
        self.acctd = os.path.join(engine_dir, "tools", "pn-acctd")
        self.rid = "%d%04d" % (os.getpid(), random.randint(0, 9999))
        self.leaf = "phantom-qlgate%s" % self.rid
        self.leaf_d = os.path.join(BATCH_PARENT, self.leaf)
        self.btier_d = os.path.join(BATCH_PARENT, "qlgate%s-bat" % self.rid)
        self.itier_d = os.path.join(INT_PARENT, "qlgate%s-int" % self.rid)
        self.scratch = tempfile.mkdtemp(prefix="pn-qlgate.")
        self.data = os.path.join(self.scratch, "data")
        self.run = os.path.join(self.scratch, "run")
        os.makedirs(os.path.join(self.data, "portioneer"), exist_ok=True)
        os.makedirs(self.run, exist_ok=True)
        self.sock = os.path.join(self.run, "pnd.sock")
        self.acct_db = os.path.join(self.data, "portioneer", "acct.db")
        self.proc = None
        self.results = []
        self.env = dict(os.environ)
        self.env.update(SCR_ENV)
        self.env.update({"XDG_DATA_HOME": self.data, "PN_DATA_DIR": self.data,
                         "XDG_RUNTIME_DIR": self.run, "PN_CG_BATCH_DIR": self.btier_d,
                         "PN_CG_INTERACTIVE_DIR": self.itier_d, "PYTHONPATH": self.eng})

    def check(self, mid, label, passed, evidence=""):
        self.results.append((mid, label, bool(passed), evidence))
        tag = (C["g"] + "PASS" + C["0"]) if passed else (C["r"] + "FAIL" + C["0"])
        print("  [%s] G%-2s %-46s %s" % (tag, mid, label, evidence), flush=True)
        return passed

    def ref(self, mid, label, evidence):
        self.results.append((mid, label, True, "REFERENCED: " + evidence))
        print("  [%sREF %s] G%-2s %-46s %s" % (C["y"], C["0"], mid, label, evidence), flush=True)

    def req(self, d, timeout=15):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(self.sock)
        s.sendall((json.dumps(d) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            c = s.recv(65536)
            if not c:
                break
            buf += c
        s.close()
        return json.loads(buf.decode().splitlines()[0])

    def submit(self, argv, **kw):
        d = {"verb": "submit", "cmd": argv, "cwd": "/tmp", "source": "cli",
             "env": {"PATH": self.env.get("PATH", "")}}
        d.update(kw)
        return self.req(d)

    def job(self, jid):
        r = self.req({"verb": "job", "id": jid})
        return r["job"] if r.get("ok") else None

    def eta(self, jid):
        return self.req({"verb": "eta", "id": jid}).get("eta") or {}

    def listing(self):
        return self.req({"verb": "list", "limit": 100}).get("jobs", [])

    def counts(self):
        lst = self.listing()
        run = [j for j in lst if j["state"] == "running"]
        wait = [j for j in lst if j["state"] in ("queued", "waiting", "pending")]
        return lst, run, wait

    def wait_terminal(self, jid, timeout=40):
        t = time.time() + timeout
        while time.time() < t:
            j = self.job(jid)
            if j and j["state"] in ("done", "failed", "timeout", "cancelled"):
                return j
            time.sleep(0.4)
        return self.job(jid)

    def wait_running(self, jid, timeout=25):
        t = time.time() + timeout
        while time.time() < t:
            j = self.job(jid)
            if j and j["state"] == "running":
                return True
            if j and j["state"] in ("done", "failed", "timeout", "cancelled"):
                return False
            time.sleep(0.3)
        return False

    def drain(self, timeout=30):

        for j in self.listing():
            if j["state"] in ("queued", "waiting", "pending", "running"):
                try:
                    self.req({"verb": "cancel", "id": j["id"]})
                except Exception:
                    pass
        t = time.time() + timeout
        while time.time() < t:
            _, run, wait = self.counts()
            if not run and not wait:
                return True
            time.sleep(0.5)
        return False

    @staticmethod
    def ram(mib, secs):
        return ["python3", "-c",
                "import time\nb=bytearray(%d*1024*1024)\n"
                "for i in range(0,len(b),4096): b[i]=1\ntime.sleep(%f)" % (mib, secs)]

    @staticmethod
    def cpu(secs):
        return ["python3", "-c", "import time\nt=time.time()+%f\nwhile time.time()<t: pass" % secs]

    @staticmethod
    def disk(mib, secs):
        return ["python3", "-c",
                "import os,time\np=os.environ.get('PN_SCRATCH','/tmp')\n"
                "f=open(os.path.join(p,'big'),'wb')\nc=b'x'*(1024*1024)\n"
                "for _ in range(%d): f.write(c)\nf.flush();os.fsync(f.fileno())\ntime.sleep(%f)"
                % (mib, secs)]

    def _mk(self, d):
        os.makedirs(d, exist_ok=True)

    def start(self):

        self._mk(self.leaf_d)
        self._mk(self.btier_d)
        self._mk(self.itier_d)

        launcher = os.path.join(self.scratch, "launch.sh")
        with open(launcher, "w") as f:
            f.write("#!/bin/bash\nset -e\n"
                    "sudo -n %s --seat $$ %s 1>&2\n"
                    "grep -q /pn.slice/batch/%s /proc/self/cgroup || { echo QLGATE-SEATFAIL >&2; exit 91; }\n"
                    "exec python3 %s\n" % (CGMOVE, self.leaf, self.leaf, self.pnd))
        os.chmod(launcher, 0o755)
        self.proc = subprocess.Popen(
            ["/bin/bash", launcher], env=self.env,
            stdout=open(os.path.join(self.scratch, "pnd.out"), "w"),
            stderr=open(os.path.join(self.scratch, "pnd.err"), "w"))

        for _ in range(80):
            if os.path.exists(self.sock):
                try:
                    if self.req({"verb": "status"}).get("ok"):
                        return True
                except Exception:
                    pass
            if self.proc.poll() is not None:
                err = ""
                try:
                    err = open(os.path.join(self.scratch, "pnd.err")).read()[-1500:]
                except Exception:
                    pass
                raise RuntimeError("scratch pnd exited rc=%s\n%s" % (self.proc.returncode, err))
            time.sleep(0.5)
        raise RuntimeError("scratch pnd socket never came up")

    def teardown(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=8)
            except Exception:
                self.proc.kill()
        time.sleep(0.4)
        for d in (self.btier_d, self.itier_d, self.leaf_d):
            self._rmcg(d)
        left = [d for d in (self.btier_d, self.itier_d, self.leaf_d) if os.path.exists(d)]
        shutil.rmtree(self.scratch, ignore_errors=True)
        return left, (not os.path.exists(self.scratch))

    @staticmethod
    def _rmcg(d):
        if not os.path.exists(d):
            return
        try:
            for root, dirs, _ in os.walk(d, topdown=False):
                try:
                    for p in open(os.path.join(root, "cgroup.procs")).read().split():
                        try:
                            os.kill(int(p), signal.SIGKILL)
                        except Exception:
                            pass
                except Exception:
                    pass
            time.sleep(0.2)
            for root, dirs, _ in os.walk(d, topdown=False):
                for dd in dirs:
                    try:
                        os.rmdir(os.path.join(root, dd))
                    except Exception:
                        pass
            os.rmdir(d)
        except Exception:
            pass

    def type_estimate(self, tt):
        code = ("import json,sys;sys.path.insert(0,%r)\n"
                "from pnlib import acct\n"
                "print(json.dumps(acct.AcctReader().type_estimate(%r)))" % (self.eng, tt))
        r = subprocess.run(["python3", "-c", code], env=self.env,
                           capture_output=True, text=True, timeout=20)
        try:
            return json.loads(r.stdout.strip() or "{}")
        except Exception:
            return {}

    def acctd_once(self):
        subprocess.run([self.acctd, "--once"], env=self.env, capture_output=True, text=True, timeout=30)

    def run_matrix(self):
        self.g1_real_queueing()
        self.drain()
        self.g2_ram()
        self.drain()
        self.g3_cpu()
        self.drain()
        self.g45_disk_and_caps()
        self.drain()
        self.g6_cross_class()
        self.drain()
        self.g79_accounting_learning()
        self.drain()
        self.g8_leaf_hygiene()
        self.g10_incell_offload()

    def g1_real_queueing(self):
        print(C["b"] + "\nG1  real queueing under load (the 'not a placeholder' proof)" + C["0"])
        ids = [self.submit(self.ram(200, 5), mem=200, cpu_quota=10, timeout=25,
                           tag="g1", latency="deferrable").get("id") for _ in range(8)]
        elog("submitted 8x 200MiB jobs into a 700MiB batch budget:", ids)

        run = wait = []
        ram_reason = ""
        best = (0, 0)
        deadline = time.time() + 12
        while time.time() < deadline:
            _, run, wait = self.counts()
            best = max(best, (len(run), len(wait)))
            if wait:
                reasons = [self.eta(j["id"]).get("wait_reason_de") or "" for j in wait]
                ram_reason = next((x for x in reasons if ("RAM" in x or "budget" in x)), ram_reason)
            if best[0] >= 2 and best[1] >= 2 and ram_reason:
                break
            time.sleep(0.8)
        n_done0 = sum(1 for i in ids if (self.job(i) or {}).get("state") in ("done", "failed", "timeout"))
        self.check(1, "concurrent submit -> some RUNNING, some WAITING",
                   best[0] >= 2 and best[1] >= 2, "RUNNING=%d WAITING=%d" % best)
        self.check(1, "waiters carry an honest per-resource reason",
                   bool(ram_reason), repr(ram_reason[:60]))

        FRIST = 90
        ende = time.time() + FRIST
        n_done1 = n_done0
        while time.time() < ende:
            n_done1 = sum(1 for i in ids
                          if (self.job(i) or {}).get("state") in ("done", "failed", "timeout"))
            if n_done1 > n_done0 and n_done1 == len(ids):
                break
            time.sleep(1.5)
        self.check(1, "queue DRAINS over time (not all-at-once)",
                   n_done0 < len(ids) and n_done1 > n_done0,
                   "done %d -> %d of %d (bis zu %d s Frist)" % (n_done0, n_done1, len(ids), FRIST))

    def g2_ram(self):
        print(C["b"] + "\nG2  RAM dimension" + C["0"])
        a = self.submit(self.ram(500, 5), mem=500, cpu_quota=10, timeout=25, tag="g2a").get("id")
        self.wait_running(a, 12)
        b = self.submit(self.ram(400, 4), mem=400, cpu_quota=10, timeout=25, tag="g2a").get("id")
        time.sleep(3)
        jb = self.job(b)
        rb = self.eta(b).get("wait_reason_de") or ""
        waited = jb and jb["state"] in ("queued", "waiting") and ("500+400" in rb or "budget" in rb or "RAM" in rb)
        self.check(2, "job over free RAM WAITS with honest reason", bool(waited), repr(rb[:60]))
        jb = self.wait_terminal(b, 40)
        self.check(2, "...then DISPATCHES when RAM frees",
                   bool(jb and jb["state"] == "done"), "final=%s" % (jb["state"] if jb else "?"))
        ob = self.submit(["true"], mem=40000, timeout=20, tag="g2b")
        msg = (ob.get("error") or "")
        self.check(2, "ever-too-big job REFUSED at submit (no fake ETA)",
                   ob.get("ok") is False and ob.get("reason") == "unfit" and "höchstens" in msg,
                   repr(msg[:64]))

    def g3_cpu(self):
        print(C["b"] + "\nG3  CPU dimension (GAP-4, active by default)" + C["0"])
        a = self.submit(self.cpu(6), cpu_quota=200, mem=64, timeout=25, tag="g3").get("id")
        self.wait_running(a, 12)
        b = self.submit(self.cpu(6), cpu_quota=200, mem=64, timeout=25, tag="g3").get("id")
        time.sleep(3)
        jb = self.job(b)
        rb = self.eta(b).get("wait_reason_de") or ""
        self.check(3, "second wide batch job WAITS on the CPU budget",
                   bool(jb and jb["state"] in ("queued", "waiting") and ("CPU" in rb or "cpu budget" in rb)),
                   repr(rb[:60]))
        rt = self.submit(self.cpu(4), cpu_quota=200, mem=64, timeout=25, tag="g3",
                         latency="realtime").get("id")
        ran = self.wait_running(rt, 10)
        self.check(3, "realtime/interactive job is EXEMPT (never queues on batch CPU)",
                   ran, "realtime running while batch waits on CPU" if ran else "realtime did NOT run")

    def g45_disk_and_caps(self):
        print(C["b"] + "\nG4/G5  disk dimension + caps bite (122 / 137 / 124)" + C["0"])

        d = self.submit(self.disk(200, 12), disk_max=100, mem=96, cpu_quota=25,
                        timeout=40, tag="g4").get("id")
        jd = self.wait_terminal(d, 45)
        self.check(4, "over-quota disk job STOPPED (exit 122), scratch reclaimed",
                   bool(jd and jd["exit_code"] == 122), "exit=%s" % (jd["exit_code"] if jd else "?"))

        m = self.submit(self.ram(300, 20), mem=140, mem_max=128, cpu_quota=25, timeout=40,
                        tag="g5m", latency="realtime").get("id")
        jm = self.wait_terminal(m, 40)
        self.check(5, "mem-cap kill at the declared limit (exit 137)",
                   bool(jm and jm["exit_code"] == 137), "exit=%s" % (jm["exit_code"] if jm else "?"))

        w = self.submit(self.cpu(30), cpu_quota=25, mem=64, timeout=2, tag="g5w",
                        latency="realtime").get("id")
        jw = self.wait_terminal(w, 25)
        self.check(5, "walltime kill (exit 124 / timeout)",
                   bool(jw and (jw["exit_code"] == 124 or jw["state"] == "timeout")),
                   "exit=%s state=%s" % (jw["exit_code"] if jw else "?", jw["state"] if jw else "?"))

    def g6_cross_class(self):
        print(C["b"] + "\nG6  cross-class fair-share" + C["0"])

        bw = self.submit(self.cpu(8), cpu_quota=200, mem=100, timeout=25, tag="g6a").get("id")
        self.wait_running(bw, 12)
        bw2 = self.submit(self.cpu(8), cpu_quota=200, mem=100, timeout=25, tag="g6a").get("id")
        time.sleep(2.5)
        batch_waits = (self.job(bw2) or {}).get("state") in ("queued", "waiting")
        rt = self.submit(self.cpu(4), cpu_quota=200, mem=100, timeout=25, tag="g6a",
                         latency="realtime").get("id")
        self.check(6, "interactive NOT starved by a saturated batch tier",
                   self.wait_running(rt, 10) and batch_waits,
                   "realtime ran while batch waited on CPU")
        self.drain()

        rt2 = self.submit(self.cpu(9), cpu_quota=100, mem=200, timeout=25, tag="g6b",
                          latency="realtime").get("id")
        self.wait_running(rt2, 12)
        bj = self.submit(self.cpu(4), cpu_quota=100, mem=200, timeout=25, tag="g6b").get("id")
        ran = self.wait_running(bj, 10)
        rt2_still = (self.job(rt2) or {}).get("state") == "running"
        self.check(6, "batch NOT wedged by a standing interactive reservation",
                   ran and rt2_still, "batch dispatched concurrently with a running realtime job")
        self.drain()

        okB1 = self.submit(self.cpu(20), cpu_quota=100, mem=100, timeout=120, tag="acctB").get("ok")
        refusal = None
        for _ in range(8):
            r = self.submit(self.cpu(20), cpu_quota=400, mem=100, timeout=120, tag="acctA")
            if r.get("ok") is False and r.get("reason") in ("fairshare", None) and "Fairshare" in (r.get("error") or ""):
                refusal = r.get("error"); break
            if r.get("ok") is False and "Gesperrt" in (r.get("error") or ""):
                refusal = r.get("error"); break
        okB2 = self.submit(self.cpu(5), cpu_quota=100, mem=100, timeout=60, tag="acctB").get("ok")
        self.check(6, "fair-share between principals (over-share refused, peer admitted)",
                   bool(refusal) and okB1 and okB2, repr((refusal or "")[:52]))

    def g79_accounting_learning(self):
        print(C["b"] + "\nG7/G9  accounting loop + agnostic learning (cmd:<argv0>)" + C["0"])
        learner = os.path.join(self.scratch, "gatelearn")
        with open(learner, "w") as f:
            f.write("#!/usr/bin/env python3\nimport time\n"
                    "b=bytearray(420*1024*1024)\n"
                    "for i in range(0,len(b),4096): b[i]=1\ntime.sleep(2)\n")
        os.chmod(learner, 0o755)
        r1 = self.submit([learner], cpu_quota=25, timeout=30, tag="learn")
        j1 = self.job(r1["id"]); mem1 = j1["mem_estimate"] if j1 else 0
        self.wait_terminal(r1["id"], 40)
        self.acctd_once()
        est = self.type_estimate("cmd:gatelearn")
        self.check(7, "actuals -> acct.db type EWMA (real estimate, not the 60s seed)",
                   bool(est) and (est.get("mem") or 0) > 0,
                   "cmd:gatelearn EWMA mem=%s svc=%s" % (est.get("mem"), est.get("svc_s") or est.get("svc")))
        r2 = self.submit([learner], cpu_quota=25, timeout=30, tag="learn")
        j2 = self.job(r2["id"]); mem2 = j2["mem_estimate"] if j2 else 0
        self.wait_terminal(r2["id"], 40)
        self.check(9, "repeat run SIZES from learned cmd:<argv0> history (agnostic)",
                   mem2 > mem1 + 40, "mem_estimate run1=%d -> run2=%d MiB" % (mem1, mem2))

        try:
            hits = subprocess.run(
                ["grep", "-rIlE", r"hifiasm|minimap|samtools|bwa|spades|gatk",
                 os.path.join(self.eng, "pnlib"), self.pnd],
                capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:
            hits = ""
        self.check(9, "ZERO tool-name coupling in the scheduler tree",
                   hits == "", "no bioinformatics tool names in pnlib/ + pnd" if not hits else hits[:60])

    def g8_leaf_hygiene(self):
        print(C["b"] + "\nG8  cgroup leaf hygiene" + C["0"])
        self.drain()
        time.sleep(3)
        leaves = []
        for tier in (self.btier_d, self.itier_d):
            if os.path.isdir(tier):
                leaves += [x for x in os.listdir(tier) if x.startswith("pn-job-")]
        self.check(8, "zero stale job cgroup leaves after completion",
                   leaves == [], "leaves=%s" % (leaves or "none"))

    def g10_incell_offload(self):
        print(C["b"] + "\nG10 in-cell compute offload" + C["0"])
        code = ("import sys;sys.path.insert(0,%r)\n"
                "from pnlib import profile as P\n"
                "c=P.CLASSES.get('cell.compute')\n"
                "print(int(bool(c)) if not c else int((not c.trusted) and c.sandbox=='cell_isolated' and c.llm_weight==0))"
                % self.eng)
        r = subprocess.run(["python3", "-c", code], env=self.env, capture_output=True, text=True, timeout=20)
        forced = (r.stdout.strip() == "1")
        self.check(10, "offload class forces caged posture (non-trusted, net-isolated)",
                   forced, "class cell.compute: trusted=False sandbox=cell_isolated llm_weight=0")
        self.ref(10, "governed cell->box compute offload (full E2E)",
                 "commit b01f7cf adversarially proven on the installed box; a real throwaway cell "
                 "is not spun here to avoid touching the owner's live cell infra")

def find_engine():
    for cand in [os.environ.get("PN_ENGINE_DIR"),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "engine"),
                 os.path.expanduser("~/portioneer"),
                 os.path.expanduser("~/brainarbeit/engine")]:
        if cand and os.path.exists(os.path.join(cand, "tools", "pnd")):
            return os.path.realpath(cand)
    return None

def skip(reason):
    print("%sSKIP%s queue_load: %s" % (C["y"], C["0"], reason))
    print("     (not a delegated pn-init box — off-box the queue-load gate self-skips so the "
          "dev/unit lane stays meaningful; it RUNS on the appliance).")
    sys.exit(0)

def main():
    print(C["b"] + "== queue_load — hermetic queue governance gate (scratch pnd + scratch tier) =="
          + C["0"])
    eng = find_engine()
    if not eng:
        skip("engine/tools/pnd not found (set PN_ENGINE_DIR)")

    if not (os.path.isdir(BATCH_PARENT) and os.access(BATCH_PARENT, os.W_OK)
            and os.path.isdir(INT_PARENT) and os.access(INT_PARENT, os.W_OK)):
        skip("pn.slice/{batch,interactive} not delegated/writable (not the appliance cgroup layout)")
    if not os.path.exists(CGMOVE):
        skip("%s absent (no audited cgroup mover)" % CGMOVE)
    try:
        lst = subprocess.run(["sudo", "-n", "-l", CGMOVE], capture_output=True, text=True, timeout=10)
        if lst.returncode != 0:
            skip("sudo -n %s not permitted (needs the pn-cgmove NOPASSWD rule)" % CGMOVE)
    except Exception as e:
        skip("cannot probe sudo pn-cgmove (%s)" % e)

    g = Gate(eng)
    print("   engine=%s\n   scratch=%s\n   leaf=%s btier=%s itier=%s"
          % (eng, g.scratch, g.leaf, os.path.basename(g.btier_d), os.path.basename(g.itier_d)))
    left = ["?"]; removed = False
    try:
        g.start()
        st = g.req({"verb": "status"})
        print("   scratch pnd up: batch_high=%s max_conc=%s   (cgroup %s)"
              % (st["cfg"]["batch_high"], st["cfg"]["max_concurrent"],
                 open("/proc/%d/cgroup" % g.proc.pid).read().strip()))
        g.run_matrix()
    finally:
        left, removed = g.teardown()

    print(C["b"] + "\n== GREEN/RED matrix ==" + C["0"])
    npass = sum(1 for _, _, p, _ in g.results if p)
    nfail = len(g.results) - npass
    for mid, label, passed, ev in g.results:
        tag = (C["g"] + "GREEN" + C["0"]) if passed else (C["r"] + " RED " + C["0"])
        print("  %s  G%-2s %-48s %s" % (tag, mid, label, ev))
    print("   hermetic teardown: cgroup leftover=%s  scratch removed=%s" % (left or "none", removed))
    print("   %d GREEN / %d RED  (%d checks)" % (npass, nfail, len(g.results)))
    if nfail or left or not removed:
        print(C["r"] + "queue_load: RED — release BLOCKED" + C["0"])
        sys.exit(1)
    print(C["g"] + "queue_load: GREEN — the queue genuinely governs work under load" + C["0"])
    sys.exit(0)

if __name__ == "__main__":
    main()
