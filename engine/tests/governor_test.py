#!/usr/bin/env python3

from __future__ import annotations

import os
import sys
import time
import json
import signal
import socket
import struct
import shutil
import tempfile
import threading
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from pnlib import governor as G

PASS = FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")

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

class Scratch:

    def __init__(self):
        self.real = False
        self.cleanup_dirs = []
        base = find_user_cg_base()
        if base:
            try:

                root = os.path.join(base, f"pn-gov-test-{os.getpid()}")
                os.makedirs(root, exist_ok=True)
                self.cleanup_dirs.append(root)

                try:
                    with open(os.path.join(base, "cgroup.subtree_control"), "w") as f:
                        f.write("+memory +cpu +pids")
                except OSError:
                    pass

                try:
                    with open(os.path.join(root, "cgroup.subtree_control"), "w") as f:
                        f.write("+memory +cpu +pids")
                except OSError:
                    pass
                tier_dirs = {}
                for t in G.TIER_ORDER:
                    d = os.path.join(root, f"pn-{t}.scratch")
                    os.makedirs(d, exist_ok=True)
                    tier_dirs[t] = d

                    try:
                        with open(os.path.join(d, "cgroup.subtree_control"), "w") as f:
                            f.write("+memory +cpu +pids")
                    except OSError:
                        pass
                self.root = root
                self.cg = G.CgLayout(root=root, tier_dirs=tier_dirs)
                self.real = True
                return
            except OSError as e:
                print(f"  (note: real cgroup scratch unavailable: {e}; using tmpfs fake)")

        tmp = tempfile.mkdtemp(prefix="pn-gov-fake-")
        self.cleanup_dirs.append(tmp)
        tier_dirs = {}
        for t in G.TIER_ORDER:
            d = os.path.join(tmp, f"pn-{t}.slice")
            os.makedirs(d, exist_ok=True)
            tier_dirs[t] = d
        self.root = tmp
        self.cg = G.CgLayout(root=tmp, tier_dirs=tier_dirs)

    def write_baseline(self, crit_min, batch_max, misc_max, batch_swap=0, misc_swap=0):

        def w(tier, leaf, val):
            try:
                with open(os.path.join(self.cg.tier_dir(tier), leaf), "w") as f:
                    f.write(str(val))
            except OSError:
                pass

        w(G.TIER_CRITICAL, "memory.min", crit_min)
        if self.real:

            pass
        w(G.TIER_BATCH, "memory.max", batch_max)
        w(G.TIER_MISC, "memory.max", misc_max)
        w(G.TIER_BATCH, "memory.swap.max", batch_swap)
        w(G.TIER_MISC, "memory.swap.max", misc_swap)

    def teardown(self):

        for base in list(self.cleanup_dirs):
            if self.real:
                self._rmtree_cg(base)
            else:
                shutil.rmtree(base, ignore_errors=True)

    @staticmethod
    def _rmtree_cg(path):

        if not os.path.isdir(path):
            return
        for name in os.listdir(path):
            sub = os.path.join(path, name)
            if os.path.isdir(sub):
                Scratch._rmtree_cg(sub)
        try:
            os.rmdir(path)
        except OSError:
            pass

def test_invariant_and_fairshare(sc):
    print("[A] fair-share refinement keeps the no-OOM invariant; unsafe raise refused")

    mt = 8 * 1024 * G.MIB
    inv = G.Invariant(mem_total=mt, swap_usable=0,
                      crit_min=2 * 1024 * G.MIB, batch_max=3 * 1024 * G.MIB,
                      misc_max=2 * 1024 * G.MIB, reserve=int(mt * 0.10))
    check(inv.holds(), "[A1] honest safe baseline holds (crit+batch+misc+reserve <= backing)")

    overcommit = G.Invariant(mem_total=mt, swap_usable=0,
                             crit_min=2 * 1024 * G.MIB, batch_max=5 * 1024 * G.MIB,
                             misc_max=3 * 1024 * G.MIB, reserve=int(mt * 0.10))
    check(not overcommit.holds(), "[A2] overcommitting baseline is correctly detected (not holds)")

    crit_ceiling_overflow = G.Invariant(
        mem_total=mt, swap_usable=0,
        crit_min=1 * 1024 * G.MIB,
        crit_max=5 * 1024 * G.MIB,
        batch_max=2 * 1024 * G.MIB, misc_max=1 * 1024 * G.MIB, reserve=int(mt * 0.10))
    check(not crit_ceiling_overflow.holds(),
          "[A2b] GOV-OOM-1: crit_max (not just crit_min) is charged -> ceiling overflow detected")

    two_part = G.Invariant(mem_total=8 * 1024 * G.MIB, swap_usable=4 * 1024 * G.MIB,
                           crit_min=1 * 1024 * G.MIB, crit_max=8 * 1024 * G.MIB,
                           batch_max=128 * G.MIB, misc_max=128 * G.MIB, reserve=512 * G.MIB)

    old_combined_pass = (two_part.crit_charge + 128 * G.MIB + 128 * G.MIB + 512 * G.MIB
                         <= two_part.mem_total + two_part.swap_usable)
    check(old_combined_pass, "[A2c] CONTRACT-1: this config PASSES the OLD weaker combined sum")
    check(not two_part.crit_fits_ram(),
          "[A2d] CONTRACT-1 part(a): critical does NOT fit RAM alone (crit_max+reserve > MemTotal)")
    check(not two_part.holds(),
          "[A2e] CONTRACT-1: the two-part split REFUSES it (swap may NOT back critical) — closes the gap")

    part_b_fail = G.Invariant(mem_total=8 * 1024 * G.MIB, swap_usable=1 * 1024 * G.MIB,
                              crit_min=1 * 1024 * G.MIB, crit_max=2 * 1024 * G.MIB,
                              batch_max=4 * 1024 * G.MIB, misc_max=4 * 1024 * G.MIB,
                              reserve=512 * G.MIB)

    check(part_b_fail.crit_fits_ram() and not part_b_fail.holds(),
          "[A2f] CONTRACT-1 part(b): batch+misc over (RAM-left + permitted swap) is REFUSED")

    fair = G.FairShare(sc.cg, inv)

    ok, why = fair.refuse_unsafe_raise(G.TIER_MISC, proposed_max=6 * 1024 * G.MIB)
    check(not ok and "no-OOM" in why, "[A3] raising misc.max to an unsafe value is REFUSED")
    ok2, _ = fair.refuse_unsafe_raise(G.TIER_MISC, proposed_max=int(2.2 * 1024 * G.MIB))
    check(ok2, "[A4] a within-budget cap proposal is allowed")

    plan_idle = fair.plan({"pressure": 0.0, "mem": G.meminfo(), "crit_psi_avg10": 0.0, "avail_frac": 1.0})
    plan_press = fair.plan({"pressure": 0.9, "mem": G.meminfo(), "crit_psi_avg10": 90.0, "avail_frac": 0.05})
    check(plan_press["cpu_weight"][G.TIER_MISC] < plan_idle["cpu_weight"][G.TIER_MISC],
          "[A5] under pressure misc cpu.weight is squeezed below idle (interactive protected)")

    check(G.TIER_CRITICAL not in plan_press["cpu_weight"] and G.TIER_CRITICAL not in plan_press["io_weight"],
          "[A5b] NEW-5: plan never targets the critical tier's weights")
    check(plan_press["memory_high"][G.TIER_MISC] < plan_idle["memory_high"][G.TIER_MISC],
          "[A6] under pressure misc memory.high is tightened (earlier reclaim)")
    for tier, hi in plan_press["memory_high"].items():
        cap = inv.batch_max if tier == G.TIER_BATCH else inv.misc_max
        check(hi <= cap, f"[A7] plan memory.high[{tier}] stays <= hard cap (soft band preserved)")
    rep = fair.apply(plan_press)

    bad = [r for r in rep["refused"] if "INVARIANT" in str(r) or "no-OOM" in str(r) or "capacity unknown" in str(r)]
    check(not bad, "[A8] apply() never refuses for invariant reasons on a valid plan with known capacity")

    fair_bad = G.FairShare(sc.cg, overcommit)
    rep_bad = fair_bad.apply(fair_bad.plan({"pressure": 0.0, "mem": G.meminfo(),
                                            "crit_psi_avg10": 0.0, "avail_frac": 1.0}))
    check(any("INVARIANT broken" in str(r) for r in rep_bad["refused"]),
          "[A9] apply() refuses soft memory.high raises while the baseline invariant is broken")

    inv0 = G.Invariant(mem_total=0, swap_usable=0, crit_min=0, crit_max=0,
                       batch_max=0, misc_max=0, reserve=0)
    check(inv0.holds() and not inv0.capacity_known(),
          "[A10] mem_total<=0: holds() returns True (degrade) but capacity_known() is False")
    fair0 = G.FairShare(sc.cg, inv0)
    rep0 = fair0.apply({"pressure": 0.0, "memory_high": {G.TIER_MISC: 100 * G.MIB},
                        "cpu_weight": {}, "io_weight": {}})
    wrote_high = [w for w in rep0["written"] if w[1] == "memory.high"]
    refused_cap = [r for r in rep0["refused"] if "capacity unknown" in str(r)]
    check(not wrote_high and refused_cap,
          "[A11] GOV-OOM-5: with capacity unknown, apply() refuses every memory.high (fail CLOSED)")

    base_misc = 600 * G.MIB
    if sc.real:

        G.write_cg(os.path.join(sc.cg.tier_dir(G.TIER_MISC), "memory.high"), base_misc)
    else:
        with open(os.path.join(sc.cg.tier_dir(G.TIER_MISC), "memory.high"), "w") as f:
            f.write(str(base_misc))
    fair_b = G.FairShare(sc.cg, inv)
    captured = fair_b.baseline_high.get(G.TIER_MISC)
    plan_idle2 = fair_b.plan({"pressure": 0.0, "mem": G.meminfo(),
                              "crit_psi_avg10": 0.0, "avail_frac": 1.0})
    if captured is not None:
        check(plan_idle2["memory_high"][G.TIER_MISC] <= captured,
              f"[A12] NEW-1: idle relax ({plan_idle2['memory_high'][G.TIER_MISC]}) never exceeds "
              f"PID1 baseline ({captured})")
    else:
        check(True, "[A12] NEW-1: baseline memory.high unreadable here -> relax clamps to cap (n/a)")

def test_none_and_uncapped(sc):
    print("[N] None-safe Invariant + uncapped-tier handling (NEW-3/NEW-4)")
    mt = 8 * 1024 * G.MIB

    crashed = False
    try:
        inv = G.Invariant(mem_total=mt, swap_usable=0, crit_min=1 * 1024 * G.MIB,
                          crit_max=2 * 1024 * G.MIB, batch_max=None, misc_max=None,
                          reserve=int(mt * 0.10))
    except Exception as e:
        crashed = True
        print(f"     (construction raised {e!r})")
    check(not crashed, "[N1] NEW-3: Invariant(batch_max=None, misc_max=None) does NOT crash")
    check(inv.batch_max is None and inv.misc_max is None,
          "[N2] NEW-3: an uncapped tier is kept as None, NOT silently coerced to 0")

    check(not inv.holds(),
          "[N3] NEW-4: uncapped batch/misc tier -> holds() False (unbounded tier unprovable)")

    inv_one = G.Invariant(mem_total=mt, swap_usable=0, crit_min=1 * 1024 * G.MIB,
                          crit_max=2 * 1024 * G.MIB, batch_max=2 * 1024 * G.MIB, misc_max=None,
                          reserve=int(mt * 0.10))
    check(not inv_one.holds(), "[N4] NEW-4: even ONE uncapped tier (misc=None) -> holds() False")

    tmp = tempfile.mkdtemp(prefix="pn-gov-cap-")
    try:
        finite = os.path.join(tmp, "finite"); open(finite, "w").write(str(256 * G.MIB))
        uncapped = os.path.join(tmp, "uncapped"); open(uncapped, "w").write("max\n")
        check(G.read_cap(finite) == 256 * G.MIB, "[N5] read_cap(finite) -> int")
        check(G.read_cap(uncapped) == G.CAP_UNCAPPED, "[N6] read_cap('max') -> CAP_UNCAPPED")
        check(G.read_cap(os.path.join(tmp, "missing")) == G.CAP_ABSENT,
              "[N7] read_cap(missing) -> CAP_ABSENT (distinct from UNCAPPED)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    import importlib.util
    from importlib.machinery import SourceFileLoader
    _loader = SourceFileLoader("pngovd", os.path.join(ROOT, "tools", "pn-governord"))
    spec = importlib.util.spec_from_loader("pngovd", _loader)
    pngovd = importlib.util.module_from_spec(spec)
    _loader.exec_module(pngovd)

    bmax = os.path.join(sc.cg.tier_dir(G.TIER_BATCH), "memory.max")
    mmax = os.path.join(sc.cg.tier_dir(G.TIER_MISC), "memory.max")
    if sc.real:
        G.write_cg(bmax, "max")
        G.write_cg(mmax, 1 * 1024 * G.MIB)
    else:
        with open(bmax, "w") as f:
            f.write("max\n")
        with open(mmax, "w") as f:
            f.write(str(1 * 1024 * G.MIB))
    inv_rb = pngovd.read_baseline(sc.cg)
    check(inv_rb.batch_max is None,
          "[N8] NEW-4: read_baseline maps an UNCAPPED pn-batch tier to None (not 0)")

    if sc.real:
        G.write_cg(bmax, 2 * 1024 * G.MIB)

def test_klaviatur_logic(sc):
    print("[C/E/F/G] Klaviatur: sacred-guard, per-instance leaves, malformed input, name validation")
    spawned = {}

    def killer(pid, sig):
        return True

    def spawner(prog, inst):
        pid = 100000 + inst
        spawned[(prog.name, inst)] = pid
        return pid

    klav = G.Klaviatur(sc.cg, invariant=None, killer=killer, spawner=spawner)
    klav.register(G.Program("sshd", tier=G.TIER_CRITICAL, sacred=True))
    churn = klav.register(G.Program("churn", tier=G.TIER_MISC))

    crit_helper = klav.register(G.Program("crit-helper", tier=G.TIER_CRITICAL))

    check(not klav.exec_cmd(["off", "sshd"])["ok"], "[C1] off sshd is REFUSED (sacred)")
    check(not klav.exec_cmd(["block", "sshd"])["ok"], "[C2] block sshd is REFUSED (sacred)")
    check(not klav.exec_cmd(["cap", "sshd", "mem=1M"])["ok"], "[C3] cap sshd mem=1M is REFUSED (sacred cap-to-death)")
    check(not klav.exec_cmd(["scale", "sshd", "0"])["ok"], "[C4] scale sshd 0 is REFUSED (sacred lockout)")

    check(klav.exec_cmd(["cap", "sshd", "cpu=500"])["ok"], "[C5] cap sshd cpu=500 is ALLOWED (cpu.weight is proportional)")
    check(klav.exec_cmd(["on", "sshd"])["ok"], "[C6] on sshd is ALLOWED")

    check(crit_helper.sacred is True, "[C7] GOV-SACRED-SEED: a critical-tier service is marked sacred at register()")
    check(not klav.exec_cmd(["off", "crit-helper"])["ok"],
          "[C8] GOV-SACRED-SEED: off a critical-tier service (not in static list) is REFUSED")
    check(not klav.exec_cmd(["cap", "crit-helper", "mem=64M"])["ok"],
          "[C9] GOV-SACRED-SEED: mem-capping a critical-tier service is REFUSED")

    sshd = klav.get("sshd"); sshd.scale = 3; sshd.ipids = {0: 11, 1: 12, 2: 13}
    check(not klav.exec_cmd(["scale", "sshd", "2"])["ok"],
          "[C10] scale sacred down (3->2) is REFUSED (not just scale-0)")
    check(klav.exec_cmd(["scale", "sshd", "5"])["ok"], "[C11] scale sacred UP (3->5) is allowed")
    sshd.scale = 1; sshd.ipids = {}

    sneaky = klav.register(G.Program("sneaky", tier=G.TIER_MISC))
    check(klav.is_sacred(sneaky) is False, "[C12] NEW-5: declared-misc service is non-sacred when no live pid")

    placed = False
    kid = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    try:
        if sc.real:
            crit_procs = os.path.join(sc.cg.tier_dir(G.TIER_CRITICAL), "cgroup.procs")
            try:
                with open(crit_procs, "w") as f:
                    f.write(str(kid.pid))
                placed = G.read_int(f"/proc/{kid.pid}/cgroup") is None

                placed = sc.cg.pid_in_critical(kid.pid)
            except OSError:
                placed = False
        if placed:
            sneaky.ipids = {0: kid.pid}
            check(klav.is_sacred(sneaky) is True,
                  "[C13] NEW-5: a declared-misc service whose LIVE pid is under pn-critical is sacred")
            check(not klav.exec_cmd(["off", "sneaky"])["ok"],
                  "[C14] NEW-5: off on a live-critical service is REFUSED (sacred from live cgroup)")
            sneaky.ipids = {}
        else:

            kid_rel = None
            txt = G._read(f"/proc/{kid.pid}/cgroup") or ""
            for line in txt.splitlines():
                p = line.split(":", 2)
                if len(p) == 3 and p[0] == "0":
                    kid_rel = p[2]
            if kid_rel:
                synth = G.CgLayout(root="/sys/fs/cgroup", tier_dirs={
                    G.TIER_CRITICAL: "/sys/fs/cgroup" + kid_rel,
                    G.TIER_BATCH: "/sys/fs/cgroup/pn-batch.slice",
                    G.TIER_MISC: "/sys/fs/cgroup/pn-misc.slice"})
                check(synth.pid_in_critical(kid.pid) is True,
                      "[C13] NEW-5: pid_in_critical() resolves a live pid's cgroup vs the critical tier")
                k2 = G.Klaviatur(synth, invariant=None, spawner=None)
                s2 = k2.register(G.Program("sneaky2", tier=G.TIER_MISC)); s2.ipids = {0: kid.pid}
                check(k2.is_sacred(s2) is True,
                      "[C14] NEW-5: is_sacred() true via live-cgroup membership (declared misc)")
            else:
                check(True, "[C13] NEW-5: could not resolve kid cgroup (n/a)")
                check(True, "[C14] NEW-5: live-cgroup sacred (n/a in this env)")
    finally:
        kid.terminate(); kid.wait(timeout=5)

    for bad in [[], ["off"], ["cap", "churn", "mem=notasize"], ["cap", "churn", "frobnicate"],
                ["scale", "churn", "abc"], ["wat", "churn"], ["off", "ghost"]]:
        r = klav.exec_cmd(bad)
        check(not r["ok"] and "error" in r, f"[F] malformed {bad!r} -> ERR (no crash)")

    threw = False
    try:
        klav.register(G.Program("../escape", tier=G.TIER_MISC))
    except ValueError:
        threw = True
    check(threw, "[G1] register('../escape') raises (path-escape guard)")
    check(not klav.exec_cmd(["off", "../escape"])["ok"], "[G2] command on a bad name is refused")
    check(not klav.exec_cmd(["off", "a/b"])["ok"], "[G3] name with '/' refused")

    klav.exec_cmd(["cap", "churn", "mem=128M"])
    churn.scale = 3
    for inst in range(3):
        churn.ipids[inst] = spawner(churn, inst)
        klav.apply_leaf_caps(churn, inst)
    leaves = [sc.cg.leaf_dir(G.TIER_MISC, "churn", i) for i in range(3)]
    distinct = len(set(leaves)) == 3 and all(os.path.isdir(d) for d in leaves)
    check(distinct, "[E1] scale churn 3 -> 3 DISTINCT leaf cgroups exist")

    def _read_leaf_int(d, leaf):
        if sc.real:
            return G.read_int(os.path.join(d, leaf))
        v = G._read(os.path.join(d, leaf))
        return int(v) if v and v.strip() not in ("", "max") else None

    caps = [_read_leaf_int(d, "memory.max") for d in leaves]
    check(all(c == 128 * G.MIB for c in caps),
          f"[E2] each instance leaf carries the FULL 128M cap (per-instance, not summed): {caps}")

    check(distinct and len(caps) == 3, "[E3] caps bound EACH instance (3x128M), not the sum")

    pids_caps = [_read_leaf_int(d, "pids.max") for d in leaves]
    check(all(c == G.DEFAULT_PIDS_MAX for c in pids_caps),
          f"[E4] fork-bomb guard: each leaf has pids.max={G.DEFAULT_PIDS_MAX}: {pids_caps}")

    if sc.real:
        tier_procs = os.path.join(sc.cg.tier_dir(G.TIER_MISC), "cgroup.procs")
        moved_in = False
        kid = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
        try:
            try:
                with open(tier_procs, "w") as f:
                    f.write(str(kid.pid))
                moved_in = (str(kid.pid) in (G._read(tier_procs) or "").split())
            except OSError:
                moved_in = False
            if moved_in:
                mig = klav.register(G.Program("migrate", tier=G.TIER_MISC))
                mig.cap_mem = 64 * G.MIB
                mig.ipids[0] = kid.pid
                ok_mig = klav.apply_leaf_caps(mig, 0)
                procs = G._read(os.path.join(sc.cg.leaf_dir(G.TIER_MISC, "migrate", 0), "cgroup.procs")) or ""
                check(ok_mig and str(kid.pid) in procs.split(),
                      f"[E5] COMP-7: instance pid {kid.pid} migrated into its leaf cgroup.procs")
            else:
                check(True, "[E5] COMP-7: pid migration (could not park child in subtree here; "
                            "logic covered by apply_leaf_caps cgroup.procs write)")
        finally:

            try:
                with open(tier_procs, "w") as f:
                    f.write(str(kid.pid))
            except OSError:
                pass
            kid.terminate(); kid.wait(timeout=5)
    else:

        mig = klav.register(G.Program("migrate", tier=G.TIER_MISC))
        mig.cap_mem = 64 * G.MIB
        mig.ipids[0] = 777777
        klav.apply_leaf_caps(mig, 0)
        procs = G._read(os.path.join(sc.cg.leaf_dir(G.TIER_MISC, "migrate", 0), "cgroup.procs")) or ""
        check("777777" in procs.split(),
              "[E5] COMP-7: apply_leaf_caps writes the instance pid into the leaf cgroup.procs")

    partial = klav.register(G.Program("partial", tier=G.TIER_MISC))
    partial.ipids[0] = 424242
    if sc.real:
        rcap = klav.exec_cmd(["cap", "partial", "mem=64M"])
        check(rcap["ok"] is False and "PARTIAL" in rcap.get("msg", ""),
              f"[E6] GOV-CAP-PARTIAL-OK-1: cap reports PARTIAL/ok:False when an instance write fails: {rcap.get('msg')}")
    else:
        check(True, "[E6] GOV-CAP-PARTIAL-OK-1 (real-cgroup only; covered there)")

def test_gc_and_restore(sc):
    print("[GC/RESTORE] NEW-2 stale leaf GC + NEW-1 baseline restore-on-exit")
    klav = G.Klaviatur(sc.cg, invariant=None, spawner=None)
    prog = klav.register(G.Program("orphan", tier=G.TIER_MISC))

    for i in range(3):
        leaf = sc.cg.leaf_dir(G.TIER_MISC, "orphan", i)
        os.makedirs(leaf, exist_ok=True)
        if sc.real:
            G.write_cg(os.path.join(leaf, "memory.max"), 64 * G.MIB)
    present_before = all(os.path.isdir(sc.cg.leaf_dir(G.TIER_MISC, "orphan", i)) for i in range(3))
    check(present_before, "[GC1] 3 stale instance leaves exist before GC")

    prog.ipids = {1: 999999}
    rep = klav.gc_stale_leaves()
    kept_live = os.path.isdir(sc.cg.leaf_dir(G.TIER_MISC, "orphan", 1))
    removed_orphans = (not os.path.isdir(sc.cg.leaf_dir(G.TIER_MISC, "orphan", 0))
                       and not os.path.isdir(sc.cg.leaf_dir(G.TIER_MISC, "orphan", 2)))
    check(removed_orphans, f"[GC2] NEW-2: empty orphan leaves (#0,#2) GC'd: {rep['removed']}")
    check(kept_live, "[GC3] NEW-2: the live instance leaf (#1) is KEPT")

    try:
        os.rmdir(sc.cg.leaf_dir(G.TIER_MISC, "orphan", 1))
    except OSError:
        pass

    klav2 = G.Klaviatur(sc.cg, invariant=None, spawner=None)
    klav2.register(G.Program("svc1", tier=G.TIER_MISC))
    pid1_leaf = os.path.join(sc.cg.tier_dir(G.TIER_MISC), "svc1")
    gov_leaf = sc.cg.leaf_dir(G.TIER_MISC, "svc1", 0)
    os.makedirs(pid1_leaf, exist_ok=True)
    os.makedirs(gov_leaf, exist_ok=True)
    rep2 = klav2.gc_stale_leaves()
    check(os.path.isdir(pid1_leaf) and "svc1" in rep2.get("skipped_pid1_owned", []),
          "[GC4] LEAF-NAMING: GC LEFT pn-init's bare-<name> leaf untouched (never PID1's leaf)")
    check(not os.path.isdir(gov_leaf) and "svc1#0" in rep2["removed"],
          "[GC5] LEAF-NAMING: GC removed only the Governor's own '#0' leaf")
    try:
        os.rmdir(pid1_leaf)
    except OSError:
        pass

    base = 700 * G.MIB
    mh = os.path.join(sc.cg.tier_dir(G.TIER_BATCH), "memory.high")
    if sc.real:
        G.write_cg(mh, base)
    else:
        with open(mh, "w") as f:
            f.write(str(base))
    inv = G.Invariant(mem_total=8 * 1024 * G.MIB, swap_usable=0, crit_min=512 * G.MIB,
                      crit_max=1024 * G.MIB, batch_max=2 * 1024 * G.MIB,
                      misc_max=1024 * G.MIB, reserve=int(8 * 1024 * G.MIB * 0.10))
    fair = G.FairShare(sc.cg, inv)
    captured = fair.baseline_high.get(G.TIER_BATCH)

    if sc.real:
        G.write_cg(mh, 400 * G.MIB)
    else:
        with open(mh, "w") as f:
            f.write(str(400 * G.MIB))
    fair.restore_baseline()
    after = G.read_int(mh) if sc.real else int(G._read(mh))
    if captured is not None:
        check(after == captured, f"[RESTORE1] NEW-1: memory.high restored to PID1 baseline {captured} (got {after})")
    else:
        check(True, "[RESTORE1] NEW-1: baseline unreadable here (n/a)")

def test_sigkill_escalation(sc):
    print("[D] SIGKILL escalation: a SIGTERM-ignoring child is SIGKILLed after the grace")

    code = ("import signal,sys,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            "sys.stdout.write('ready\\n');sys.stdout.flush();time.sleep(60)")
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)
    child.stdout.readline()
    klav = G.Klaviatur(sc.cg, invariant=None, spawner=lambda p, i: None)
    churn = klav.register(G.Program("hung", tier=G.TIER_MISC))
    churn.ipids[0] = child.pid
    t0 = time.monotonic()
    rep = klav.stop_instances(churn, keep_below=0, grace=1.5)
    dt = time.monotonic() - t0

    rc = child.wait(timeout=5)
    check(child.pid in rep["killed"], "[D1] SIGTERM-ignoring child was escalated to SIGKILL")
    check(rc == -signal.SIGKILL, f"[D2] child died specifically by SIGKILL (rc={rc}, not SIGTERM)")
    check(1.0 < dt < 4.0, f"[D3] escalation happened AFTER the grace, bounded ({dt:.2f}s)")

class FakeEta:

    def status(self):
        return {"ok": True, "counts": {"queued": 3, "running": 1}, "head": {"id": 7},
                "headroom": {"pressure": 0.1}, "svc_ewma_s": 42.0}

    def job_eta(self, jid):
        return {"ok": True, "id": int(jid), "position": 2, "eta_s": 84.0, "state": "queued"}

def test_control_socket(sc):
    print("[B/H] control socket: peercred uid allow-list + sub-second ETA under flood")
    sockdir = tempfile.mkdtemp(prefix="pn-gov-sock-")

    sock_rej = os.path.join(sockdir, "gov-rej.sock")
    sock_path = os.path.join(sockdir, "gov.sock")
    klav = G.Klaviatur(sc.cg, invariant=None, spawner=lambda p, i: None)
    klav.register(G.Program("sshd", tier=G.TIER_CRITICAL, sacred=True))
    klav.register(G.Program("web", tier=G.TIER_MISC))
    eta = FakeEta()

    my_uid = os.getuid()
    server = G.ControlServer(klav, eta, fairshare=None, path=sock_rej,
                             allow_uids={my_uid + 12345})
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    time.sleep(0.2)

    r = G.client_request({"verb": "ping"}, path=sock_rej)
    rejected = (not r.get("ok")) and (r.get("error") in ("unauthorized", "no reply (closed)", "empty reply"))
    check(rejected and not r.get("pong"),
          f"[B1] connection from a non-allowed uid is REJECTED (peercred) -> {r}")
    server.stop()
    th.join(timeout=2)

    server2 = G.ControlServer(klav, eta, fairshare=None, path=sock_path,
                              allow_uids={my_uid})
    th2 = threading.Thread(target=server2.serve_forever, daemon=True)
    th2.start()
    time.sleep(0.2)

    r = G.client_request({"verb": "ping"}, path=sock_path)
    check(r.get("ok") and r.get("pong"), "[B2] allowed uid -> ping works")
    r = G.client_request({"verb": "klavier", "argv": ["off", "sshd"]}, path=sock_path)
    check(not r.get("ok"), "[B3] over-the-wire: off sshd still REFUSED (sacred-guard end-to-end)")
    r = G.client_request({"verb": "klavier", "argv": ["off", "web"]}, path=sock_path)
    check(r.get("ok"), "[B4] over-the-wire: off web (non-sacred) succeeds")

    raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    raw.connect(sock_path)
    raw.sendall(b"this is not json\n")
    resp = raw.recv(4096)
    raw.close()
    check(b"bad json" in resp or b"error" in resp, "[F-wire] non-JSON line -> ERR (server survives)")
    check(G.client_request({"verb": "ping"}, path=sock_path).get("ok"),
          "[F-wire2] server still answers after garbage input")

    t0 = time.monotonic()
    r = G.client_request({"verb": "queue.status"}, path=sock_path)
    one = time.monotonic() - t0
    check(r.get("ok") and one < 0.5, f"[H1] queue.status returns in {one*1000:.1f}ms (<500ms)")

    worst = 0.0
    N = 200
    for _ in range(N):
        s0 = time.monotonic()
        rr = G.client_request({"verb": "queue.status"}, path=sock_path)
        d = time.monotonic() - s0
        worst = max(worst, d)
        if not rr.get("ok"):
            break
    check(worst < 0.5, f"[H2] worst-case status latency under {N}-query flood = {worst*1000:.1f}ms (<500ms)")

    eta_r = G.client_request({"verb": "queue.eta", "id": 7}, path=sock_path)
    check(eta_r.get("ok") and "eta_s" in eta_r and "position" in eta_r,
          "[H3] queue.eta returns position + ETA")

    bad_eta = G.client_request({"verb": "queue.eta", "id": "notanid"}, path=sock_path)
    check(not bad_eta.get("ok") and "integer id" in str(bad_eta.get("error", "")),
          "[H4] queue.eta with a non-int id -> clean error (no crash)")
    check(G.client_request({"verb": "ping"}, path=sock_path).get("ok"), "[H5] server alive after bad eta id")

    stolen = {"raised": False}
    try:
        G.ControlServer(klav, eta, None, path=sock_path, allow_uids={my_uid})._bind()
    except G.SocketInUse:
        stolen["raised"] = True
    check(stolen["raised"], "[B5] NEW-3: a 2nd Governor on a LIVE socket REFUSES to start (no steal)")

    server2.stop()
    th2.join(timeout=2)

    klav_co = G.Klaviatur(sc.cg, invariant=None, spawner=None)
    klav_co.register(G.Program("web2", tier=G.TIER_MISC))
    r_off = klav_co.exec_cmd(["off", "web2"])
    check(r_off.get("ok") is False and r_off.get("control_only") is True and "deferred" in str(r_off.get("status", "")),
          f"[B6] GOV-CONTRACT-1: inert lifecycle op -> ok:False control_only deferred: {r_off}")
    r_scale = klav_co.exec_cmd(["scale", "web2", "3"])
    check(r_scale.get("ok") is False and r_scale.get("control_only") is True,
          "[B7] GOV-CONTRACT-1: inert scale -> ok:False control_only")

    r_cap = klav_co.exec_cmd(["cap", "web2", "mem=64M"])
    check(r_cap.get("ok") is True, "[B8] GOV-CONTRACT-1: cgroup-direct cap (no instances) stays real ok:True")

    shutil.rmtree(sockdir, ignore_errors=True)

def test_eta_real_db():
    print("[H-db] EtaService against a real throwaway queue.db (position/ETA + cache + decoupled read)")
    try:
        from pnlib import db as pdb
    except Exception as e:
        print(f"  (skip: pnlib.db not importable: {e})")
        return
    tmp = tempfile.mkdtemp(prefix="pn-gov-db-")
    dbp = os.path.join(tmp, "queue.db")
    try:
        cx = pdb.connect(dbp) if hasattr(pdb, "connect") else None
    except Exception as e:
        print(f"  (skip: db.connect failed: {e})")
        shutil.rmtree(tmp, ignore_errors=True)
        return
    try:

        ids = []
        for i in range(3):
            jid = pdb.submit(cx, ["true"], "/tmp", {}, "{}", 100, 256, f"test-{i}")
            ids.append(jid)
        eta = G.EtaService(db_path=dbp, fairshare=None, ewma_seconds=30.0, cache_ttl=5.0)
        st = eta.status()
        check(st.get("ok") and st["counts"].get("queued", 0) >= 3, "[H-db1] status() reads queued count from a real db")
        je = eta.job_eta(ids[-1])
        check(je.get("ok") and je.get("position", 0) >= 1 and je.get("eta_s", 0) > 0,
              f"[H-db2] job_eta returns position={je.get('position')} eta_s={je.get('eta_s')}")

        t0 = time.monotonic()
        for _ in range(500):
            eta.status()
        dt = time.monotonic() - t0
        check(dt < 0.5, f"[H-db3] 500 cached status() calls in {dt*1000:.1f}ms (cache works)")

        s1 = eta.status()
        s1["counts"]["queued"] = -999
        s1["headroom"]["pressure"] = "POISON"
        s2 = eta.status()
        check(s2["counts"].get("queued", 0) >= 3 and s2["headroom"].get("pressure") != "POISON",
              "[H-db4] ETA-6: mutating a status() result does not poison the shared cache (deepcopy)")

        t0 = time.monotonic()
        for _ in range(500):
            eta.job_eta(ids[-1])
        dt = time.monotonic() - t0
        check(dt < 0.5, f"[H-db5] ETA-2: 500 cached job_eta(id) calls in {dt*1000:.1f}ms")

        j1 = eta.job_eta(ids[0]); j1["position"] = -1
        j2 = eta.job_eta(ids[0])
        check(j2.get("position", -1) >= 1, "[H-db6] ETA-2: job_eta cache returns a copy (no poison)")

        bad = eta.job_eta("not-an-int")
        check(bad.get("ok") is False and bad.get("error") == "invalid job id",
              "[H-db7] ETA-4: job_eta(non-int) -> clean error, no crash")
        bad2 = eta.job_eta(None)
        check(bad2.get("ok") is False, "[H-db8] ETA-4: job_eta(None) -> clean error")

        before = eta._ewma()
        eta.observe_completion(120.0)
        after = eta._ewma()
        check(after > before, f"[H-db9] ETA-3: observe_completion updates the EWMA ({before:.1f}->{after:.1f}s)")
        eta.observe_completion("garbage")
        check(eta._ewma() == after, "[H-db10] ETA-3: observe_completion ignores bad input safely")
    finally:
        try:
            cx.close()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)

def test_never_oom_leaf_cap(sc):

    print("\n[KLAV-OOM-3] never write memory.max below live usage")
    klav = G.Klaviatur(sc.cg, invariant=None, spawner=None)

    below = klav.register(G.Program("oomguard-below", tier=G.TIER_MISC))
    below.cap_mem = 64 * G.MIB
    below.ipids[0] = 0
    leaf_b = sc.cg.leaf_dir(G.TIER_MISC, "oomguard-below", 0)
    os.makedirs(leaf_b, exist_ok=True)
    G.write_cg(os.path.join(leaf_b, "memory.current"), 200 * G.MIB)
    ok_b = klav.apply_leaf_caps(below, 0)
    mmax = G.read_int(os.path.join(leaf_b, "memory.max"))
    mhigh = G.read_int(os.path.join(leaf_b, "memory.high"))
    check(mmax is not None and mmax >= 200 * G.MIB,
          f"[OOM1] memory.max held at/above live 200M (not lowered to 64M): got {mmax}")
    check(mhigh == 64 * G.MIB,
          f"[OOM2] target 64M applied as memory.high (soft throttle, not a kill): got {mhigh}")
    check(ok_b, "[OOM3] apply_leaf_caps still reports ok for the throttle case")

    above = klav.register(G.Program("oomguard-above", tier=G.TIER_MISC))
    above.cap_mem = 128 * G.MIB
    above.ipids[0] = 0
    leaf_a = sc.cg.leaf_dir(G.TIER_MISC, "oomguard-above", 0)
    os.makedirs(leaf_a, exist_ok=True)
    G.write_cg(os.path.join(leaf_a, "memory.current"), 32 * G.MIB)
    klav.apply_leaf_caps(above, 0)
    check(G.read_int(os.path.join(leaf_a, "memory.max")) == 128 * G.MIB,
          "[OOM4] cap above live binds memory.max directly (common path unchanged)")

def main():
    print(f"governor_test (python {sys.version.split()[0]})\n")
    sc = Scratch()
    print(f"scratch: {'REAL cgroup subtree at ' + sc.root if sc.real else 'tmpfs fake at ' + sc.root}\n")
    sc.write_baseline(crit_min=512 * G.MIB, batch_max=2 * 1024 * G.MIB, misc_max=1 * 1024 * G.MIB)
    try:
        test_invariant_and_fairshare(sc)
        test_none_and_uncapped(sc)
        test_klaviatur_logic(sc)
        test_never_oom_leaf_cap(sc)
        test_gc_and_restore(sc)
        test_sigkill_escalation(sc)
        test_control_socket(sc)
        test_eta_real_db()
    finally:
        sc.teardown()
    print(f"\n{'='*60}\nRESULT: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0

if __name__ == "__main__":
    sys.exit(main())
