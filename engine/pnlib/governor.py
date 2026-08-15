
from __future__ import annotations

import os
import re
import copy
import json
import time
import errno
import shutil
import signal
import socket
import struct
import threading

CG_ROOT = "/sys/fs/cgroup"
TIER_CRITICAL, TIER_BATCH, TIER_MISC = "critical", "batch", "misc"
TIER_ORDER = (TIER_CRITICAL, TIER_BATCH, TIER_MISC)

MIB = 1024 * 1024

DEFAULT_SACRED = ("sshd", "pnd", "pn-llmd", "portal", "pn-governord")

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

SCALE_MAX = 16
STOP_GRACE_S = 8.0
DEFAULT_PIDS_MAX = 512

def valid_name(name) -> bool:

    return bool(name) and isinstance(name, str) and bool(_NAME_RE.match(name))

class CgLayout:

    def __init__(self, root: str = CG_ROOT, tier_dirs: dict | None = None):
        self.root = root

        self.tier_dirs = tier_dirs or {
            TIER_CRITICAL: os.path.join(root, "pn-critical.slice"),
            TIER_BATCH: os.path.join(root, "pn-batch.slice"),
            TIER_MISC: os.path.join(root, "pn-misc.slice"),
        }

    def tier_dir(self, tier: str) -> str:
        return self.tier_dirs[tier]

    def leaf_dir(self, tier: str, name: str, instance: int) -> str:

        return os.path.join(self.tier_dir(tier), f"{name}#{instance}")

    def critical_dir_real(self) -> str:

        return os.path.realpath(self.tier_dir(TIER_CRITICAL))

    def pid_in_critical(self, pid) -> bool:

        if not pid:
            return False
        txt = _read(f"/proc/{int(pid)}/cgroup")
        if not txt:
            return False

        rel = None
        for line in txt.splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[0] == "0":
                rel = parts[2]
                break
        if rel is None:
            return False
        abspath = os.path.realpath(os.path.join(self.root, rel.lstrip("/")))
        crit = self.critical_dir_real()
        return abspath == crit or abspath.startswith(crit + os.sep)

def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None

def read_int(path):

    v = _read(path)
    if v is None:
        return None
    v = v.strip()
    if v == "" or v == "max":
        return None
    try:
        return int(v.split()[0])
    except (ValueError, IndexError):
        return None

CAP_UNCAPPED = "uncapped"
CAP_ABSENT = "absent"

def read_cap(path):

    v = _read(path)
    if v is None:
        return CAP_ABSENT
    v = v.strip()
    if v == "max":
        return CAP_UNCAPPED
    if v == "":
        return CAP_ABSENT
    try:
        return int(v.split()[0])
    except (ValueError, IndexError):
        return CAP_ABSENT

def write_cg(path, value) -> bool:

    try:
        with open(path, "w") as f:
            f.write(str(value))
        return True
    except OSError:
        return False

def read_psi_some_avg10(cg_dir) -> float:

    txt = _read(os.path.join(cg_dir, "memory.pressure"))
    if not txt:
        return 0.0
    for line in txt.splitlines():
        if line.startswith("some"):
            for tok in line.split():
                if tok.startswith("avg10="):
                    try:
                        return float(tok.split("=", 1)[1])
                    except ValueError:
                        return 0.0
    return 0.0

def meminfo():

    d = {}
    txt = _read("/proc/meminfo") or ""
    for line in txt.splitlines():
        k, _, rest = line.partition(":")
        try:
            d[k] = int(rest.split()[0]) * 1024
        except (ValueError, IndexError):
            pass
    return {
        "total": d.get("MemTotal", 0),
        "available": d.get("MemAvailable", 0),
        "swap_total": d.get("SwapTotal", 0),
        "swap_free": d.get("SwapFree", 0),
    }

class Invariant:

    def __init__(self, mem_total, swap_usable, crit_min, batch_max, misc_max, reserve,
                 crit_max=None):
        self.mem_total = int(mem_total)
        self.swap_usable = int(swap_usable)
        self.crit_min = int(crit_min)

        self.crit_max = int(crit_max) if crit_max is not None else self.crit_min
        if self.crit_max < self.crit_min:
            self.crit_max = self.crit_min

        self.batch_max = int(batch_max) if batch_max is not None else None
        self.misc_max = int(misc_max) if misc_max is not None else None
        self.reserve = int(reserve)

    @property
    def backing(self) -> int:
        return self.mem_total + self.swap_usable

    @property
    def crit_charge(self) -> int:

        return max(self.crit_min, self.crit_max)

    def demand(self, batch_max=None, misc_max=None) -> int:

        b = self.batch_max if batch_max is None else batch_max
        m = self.misc_max if misc_max is None else misc_max
        b = self.backing if b is None else int(b)
        m = self.backing if m is None else int(m)
        return self.crit_charge + b + m + self.reserve

    def capacity_known(self) -> bool:
        return self.mem_total > 0

    def crit_fits_ram(self) -> bool:

        if self.mem_total <= 0:
            return True
        return self.crit_charge + self.reserve <= self.mem_total

    def holds(self, batch_max=None, misc_max=None) -> bool:

        if self.mem_total <= 0:
            return True

        if not self.crit_fits_ram():
            return False

        b = self.batch_max if batch_max is None else batch_max
        m = self.misc_max if misc_max is None else misc_max
        if b is None or m is None:
            return False
        ram_left = self.mem_total - self.crit_charge - self.reserve
        if ram_left < 0:
            return False
        return int(b) + int(m) <= ram_left + self.swap_usable

    def check_memory_high(self, tier, value_bytes, tier_caps) -> tuple[bool, str]:

        cap = tier_caps.get(tier)
        if cap is not None and value_bytes is not None and value_bytes > cap:
            return False, (f"refused: memory.high ({value_bytes}) > {tier} memory.max ({cap}) "
                           f"would disable the soft reclaim band")
        if value_bytes is not None and value_bytes < MIB:
            return False, "refused: memory.high below 1 MiB floor"
        return True, "ok"

class Program:

    def __init__(self, name, tier=TIER_MISC, sacred=False):
        self.name = name
        self.tier = tier
        self.sacred = sacred
        self.enabled = True
        self.blocked = False
        self.scale = 1
        self.cap_mem = 0
        self.cap_cpu = 0
        self.cap_pids = 0

        self.ipids: dict[int, int] = {}
        self.restarts = 0
        self.start_at = 0.0

    def running(self) -> int:
        return sum(1 for p in self.ipids.values() if p)

    def to_status(self) -> dict:
        return {
            "name": self.name,
            "tier": self.tier,
            "sacred": self.sacred,
            "enabled": self.enabled,
            "blocked": self.blocked,
            "scale": self.scale,
            "running": self.running(),
            "cap_mem": self.cap_mem,
            "cap_cpu": self.cap_cpu,
            "cap_pids": self.cap_pids or DEFAULT_PIDS_MAX,
            "restarts": self.restarts,
        }

class Klaviatur:

    def __init__(self, cg: CgLayout, invariant: Invariant | None = None,
                 sacred=DEFAULT_SACRED, killer=None, spawner=None, log=None,
                 submit_fn=None):
        self.cg = cg
        self.invariant = invariant
        self.sacred_names = set(sacred)
        self.programs: dict[str, Program] = {}

        self._kill = killer or self._default_kill
        self._spawn = spawner

        self._submit = submit_fn
        self._log = log or (lambda m: None)
        self._lock = threading.Lock()

    def register(self, prog: Program):
        if not valid_name(prog.name):
            raise ValueError(f"invalid service name: {prog.name!r}")

        if prog.name in self.sacred_names or prog.tier == TIER_CRITICAL:
            prog.sacred = True
        self.programs[prog.name] = prog
        return prog

    def is_sacred(self, prog: Program) -> bool:

        if prog.sacred or prog.name in self.sacred_names or prog.tier == TIER_CRITICAL:
            return True

        for pid in prog.ipids.values():
            if pid and self.cg.pid_in_critical(pid):
                return True
        return False

    def get(self, name):
        return self.programs.get(name)

    @staticmethod
    def _default_kill(pid, sig):
        try:
            os.kill(pid, sig)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False

    def _alive(self, pid) -> bool:
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    def stop_instances(self, prog: Program, keep_below: int = 0, grace=STOP_GRACE_S) -> dict:

        termed, killed = [], []
        targets = [(i, p) for i, p in list(prog.ipids.items()) if p and i >= keep_below]
        for i, p in targets:
            if self._kill(p, signal.SIGTERM):
                termed.append(p)

        deadline = time.monotonic() + grace
        pending = list(targets)
        while pending and time.monotonic() < deadline:
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
            pending = [(i, p) for (i, p) in pending if self._alive(p)]
        for i, p in pending:

            if self._alive(p) and self._kill(p, signal.SIGKILL):
                killed.append(p)
            prog.ipids[i] = 0

        for i, p in targets:
            if i >= keep_below:
                prog.ipids[i] = 0
        return {"termed": termed, "killed": killed}

    def _leaf_live_floor(self, leaf: str, pid) -> int:

        floor = 0
        cur = read_int(os.path.join(leaf, "memory.current"))
        if cur:
            floor = cur
        if pid:
            try:
                with open(f"/proc/{int(pid)}/status") as fh:
                    for ln in fh:
                        if ln.startswith("VmRSS:"):
                            floor = max(floor, int(ln.split()[1]) * 1024)
                            break
            except (OSError, ValueError, IndexError):
                pass
        return floor

    def apply_leaf_caps(self, prog: Program, instance: int) -> bool:

        leaf = self.cg.leaf_dir(prog.tier, prog.name, instance)
        try:
            os.makedirs(leaf, exist_ok=True)
        except OSError as e:
            self._log(f"klaviatur: leaf mkdir {leaf} failed ({e}); instance runs in tier cgroup")
            return False
        ok = True
        if prog.cap_mem:
            target = int(prog.cap_mem)

            live = self._leaf_live_floor(leaf, prog.ipids.get(instance))
            if live and target < live:
                self._log(f"klaviatur: mem cap {target}B for {prog.name}#{instance} is below live "
                          f"{live}B — holding memory.max at {live}B, throttling to {target}B via "
                          f"memory.high (never-OOM: a lower memory.max would group-OOM the instance)")
                ok &= write_cg(os.path.join(leaf, "memory.high"), target)
                ok &= write_cg(os.path.join(leaf, "memory.max"), live)
            else:

                ok &= write_cg(os.path.join(leaf, "memory.max"), target)

            ok &= write_cg(os.path.join(leaf, "memory.oom.group"), "1")
        if prog.cap_cpu:
            ok &= write_cg(os.path.join(leaf, "cpu.weight"), prog.cap_cpu)

        ok &= write_cg(os.path.join(leaf, "pids.max"), prog.cap_pids or DEFAULT_PIDS_MAX)

        pid = prog.ipids.get(instance)
        if pid:
            if not write_cg(os.path.join(leaf, "cgroup.procs"), str(pid)):
                self._log(f"klaviatur: failed to place pid {pid} into {leaf} (caps not bound)")
                ok = False
        return ok

    def leaf_exists(self, prog: Program, instance: int) -> bool:
        return os.path.isdir(self.cg.leaf_dir(prog.tier, prog.name, instance))

    def gc_stale_leaves(self) -> dict:

        removed, kept, skipped_pid1 = [], [], []
        for prog in self.programs.values():
            tdir = self.cg.tier_dir(prog.tier)
            try:
                entries = os.listdir(tdir)
            except OSError:
                continue
            prefix = prog.name + "#"
            live_idx = {i for i, p in prog.ipids.items() if p}
            for ent in entries:

                if ent == prog.name:
                    skipped_pid1.append(ent)
                    continue
                if not ent.startswith(prefix):
                    continue
                idx_str = ent[len(prefix):]

                if not idx_str.isdigit():
                    continue
                idx = int(idx_str)
                if idx in live_idx:
                    kept.append(ent)
                    continue
                leaf = os.path.join(tdir, ent)
                procs = _read(os.path.join(leaf, "cgroup.procs"))
                if procs and procs.strip():
                    kept.append(ent)
                    continue
                try:
                    os.rmdir(leaf)
                    removed.append(ent)
                except OSError:
                    kept.append(ent)
        if removed:
            self._log(f"klaviatur: GC removed {len(removed)} stale Governor leaf cgroup(s): {removed}")
        if skipped_pid1:
            self._log(f"klaviatur: GC left pn-init-owned bare-name leaf/leaves untouched: {skipped_pid1}")
        return {"removed": removed, "kept": kept, "skipped_pid1_owned": skipped_pid1}

    def exec_cmd(self, argv: list, peer_uid=None) -> dict:

        if not argv:
            return {"ok": False, "error": "empty command"}
        cmd = argv[0]
        with self._lock:
            try:
                return self._dispatch(cmd, argv, peer_uid)
            except Exception as e:
                self._log(f"klaviatur: exec {cmd} raised {type(e).__name__}: {e}")
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _dispatch(self, cmd, argv, peer_uid):
        if cmd == "status":
            return {"ok": True, "programs": [p.to_status() for p in self.programs.values()]}
        if cmd == "list":
            return {"ok": True, "names": sorted(self.programs)}

        if len(argv) < 2:
            return {"ok": False, "error": "usage: on|off|block|unblock|cap|scale <svc> ..."}
        name = argv[1]
        if not valid_name(name):
            return {"ok": False, "error": "refused: invalid service name (path-escape guard)"}
        prog = self.get(name)
        if not prog:
            return {"ok": False, "error": "no such svc"}

        sacred = self.is_sacred(prog)
        if sacred and cmd in ("off", "block"):
            verb = {"off": "stopped", "block": "blocked"}[cmd]
            return {"ok": False, "error": f"refused: '{name}' is sacred and may not be {verb}"}

        control_only = self._spawn is None

        if cmd == "on":
            prog.enabled = True

            if self._submit is not None and not sacred:
                want = max(1, prog.scale or 1)
                rep = self._route_submit(prog, want)
                return rep
            if control_only:
                return {"ok": False, "applied": False, "control_only": True,
                        "status": "deferred: lifecycle requires PID1 wiring",
                        "msg": f"{name} on (intent recorded; PID1 owns spawning)"}
            return {"ok": True, "applied": True, "msg": f"{name} on"}

        if cmd == "off":
            prog.enabled = False
            if control_only:
                return {"ok": False, "applied": False, "control_only": True,
                        "status": "deferred: lifecycle requires PID1 wiring",
                        "msg": f"{name} off (intent recorded; PID1 owns spawning)"}
            rep = self.stop_instances(prog, keep_below=0)
            return {"ok": True, "applied": True, "msg": f"{name} off", **rep}

        if cmd == "block":
            prog.blocked = True
            prog.enabled = False
            if control_only:
                return {"ok": False, "applied": False, "control_only": True,
                        "status": "deferred: lifecycle requires PID1 wiring",
                        "msg": f"{name} block (intent recorded; PID1 owns spawning)"}
            rep = self.stop_instances(prog, keep_below=0)
            return {"ok": True, "applied": True,
                    "msg": f"{name} blocked (un-startable until unblock)", **rep}

        if cmd == "unblock":
            prog.blocked = False
            if control_only:
                return {"ok": False, "applied": False, "control_only": True,
                        "status": "deferred: lifecycle requires PID1 wiring",
                        "msg": f"{name} unblock (intent recorded; PID1 owns spawning)"}
            return {"ok": True, "applied": True, "msg": f"{name} unblocked"}

        if cmd == "cap":
            return self._cap(prog, argv[2:], sacred)

        if cmd == "scale":
            return self._scale(prog, argv[2:], sacred)

        return {"ok": False, "error": f"unknown cmd: {cmd}"}

    def _route_submit(self, prog, n: int) -> dict:

        if n <= 0:
            return {"ok": True, "governed": True, "queued": [], "msg": f"{prog.name}: nothing to start"}
        try:
            rep = self._submit(prog, n) or {}
        except Exception as e:
            self._log(f"klaviatur: governed submit for {prog.name} raised {type(e).__name__}: {e}")
            return {"ok": False, "error": f"governed submit failed: {type(e).__name__}: {e}"}
        queued = rep.get("queued", [])
        return {"ok": True, "applied": True, "governed": True, "queued": queued,
                "msg": f"{prog.name} on: {len(queued)} instance(s) enqueued (scheduler admits "
                       f"by headroom, max_per_tick/tick)"}

    def _tier_ceiling(self, prog) -> int | None:

        if self.invariant is None:
            return None
        if prog.tier == TIER_BATCH:
            return self.invariant.batch_max or None
        if prog.tier == TIER_MISC:
            return self.invariant.misc_max or None
        if prog.tier == TIER_CRITICAL:
            return self.invariant.crit_max or None
        return None

    def _cap(self, prog, kvs, sacred):
        new_mem, new_cpu, new_pids = prog.cap_mem, prog.cap_cpu, prog.cap_pids
        for kv in kvs:
            if kv.startswith("mem="):

                if sacred:
                    return {"ok": False, "error": f"refused: cannot memory-cap sacred '{prog.name}'"}
                new_mem = parse_size(kv[4:])
                if new_mem is None:
                    return {"ok": False, "error": f"bad size: {kv[4:]!r}"}
                if new_mem < MIB:
                    return {"ok": False, "error": "refused: memory cap below 1 MiB floor"}

                ceil = self._tier_ceiling(prog)
                if ceil and new_mem > ceil:
                    return {"ok": False,
                            "error": f"refused: leaf mem cap {new_mem} > {prog.tier} hard cap {ceil}"}
            elif kv.startswith("cpu="):
                w = parse_size(kv[4:])
                if w is None:
                    return {"ok": False, "error": f"bad weight: {kv[4:]!r}"}
                new_cpu = max(1, min(10000, int(w)))
            elif kv.startswith("pids="):

                if sacred:
                    return {"ok": False, "error": f"refused: cannot pids-cap sacred '{prog.name}'"}
                pv = parse_size(kv[5:])
                if pv is None or pv < 1:
                    return {"ok": False, "error": f"bad pids: {kv[5:]!r}"}
                new_pids = int(pv)
            else:
                return {"ok": False, "error": f"bad cap arg: {kv!r} (use mem=/cpu=/pids=)"}
        prog.cap_mem, prog.cap_cpu, prog.cap_pids = new_mem, new_cpu, new_pids

        applied = 0
        running = prog.running()
        for i, p in prog.ipids.items():
            if p and self.apply_leaf_caps(prog, i):
                applied += 1
        all_ok = (applied == running)
        detail = "applied live" if all_ok else f"PARTIAL: {applied}/{running} instances"
        return {"ok": all_ok,
                "msg": f"cap mem={prog.cap_mem} cpu={prog.cap_cpu} pids={prog.cap_pids or DEFAULT_PIDS_MAX} ({detail})",
                "instances_updated": applied, "running": running}

    def _scale(self, prog, args, sacred):
        if not args:
            return {"ok": False, "error": "usage: scale <svc> <N>"}
        n = parse_size(args[0])
        if n is None:
            return {"ok": False, "error": f"bad N: {args[0]!r}"}
        n = int(n)

        if sacred and n < max(prog.scale, prog.running(), 1):
            return {"ok": False,
                    "error": (f"refused: cannot scale sacred '{prog.name}' down "
                              f"(scale={prog.scale}, running={prog.running()})")}
        n = max(0, min(SCALE_MAX, n))
        old = prog.scale

        if self._submit is not None and not sacred:
            prog.scale = n
            prog.enabled = (n > 0)
            grow = max(0, n - prog.running())
            rep = {}
            if n < prog.running():
                rep = self.stop_instances(prog, keep_below=n)
            sub = self._route_submit(prog, grow) if grow > 0 else {"queued": []}
            return {"ok": True, "applied": True, "governed": True,
                    "msg": f"scale {old}->{n} (grow {grow} via queue)",
                    "queued": sub.get("queued", []), **rep}

        if self._spawn is None:
            prog.scale = n
            prog.enabled = (n > 0)
            return {"ok": False, "applied": False, "control_only": True,
                    "status": "deferred: lifecycle requires PID1 wiring",
                    "msg": f"{prog.name} scale {old}->{n} (intent recorded; PID1 owns spawning)"}
        prog.scale = n
        prog.enabled = (n > 0)

        rep = {}
        if n < prog.running():
            rep = self.stop_instances(prog, keep_below=n)
        return {"ok": True, "applied": True, "msg": f"scale {old}->{n}", **rep}

def parse_size(s):
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    m = re.match(r"^(\d+)\s*([kKmMgG]?)[bB]?$", s)
    if not m:
        return None
    v = int(m.group(1))
    mul = {"": 1, "k": 1024, "m": MIB, "g": 1024 * MIB}[m.group(2).lower()]
    return v * mul

BASE_CPU_WEIGHT = {TIER_BATCH: 1000, TIER_MISC: 100}
BASE_IO_WEIGHT = {TIER_BATCH: 1000, TIER_MISC: 100}
REFINE_TIERS = (TIER_BATCH, TIER_MISC)

class FairShare:

    def __init__(self, cg: CgLayout, invariant: Invariant, log=None):
        self.cg = cg
        self.inv = invariant
        self._log = log or (lambda m: None)

        self.high_floor_frac = {TIER_BATCH: 0.50, TIER_MISC: 0.40}
        self.high_relaxed_frac = {TIER_BATCH: 0.90, TIER_MISC: 0.90}

        self.baseline_high = self._read_baseline_high()

    def _read_baseline_high(self) -> dict:
        out = {}
        for tier in REFINE_TIERS:
            out[tier] = read_int(os.path.join(self.cg.tier_dir(tier), "memory.high"))
        return out

    def _relax_ceiling(self, tier, cap) -> int:

        ceil = int(cap * self.high_relaxed_frac[tier])
        base = self.baseline_high.get(tier)
        if base is not None:
            ceil = min(ceil, base)
        return min(ceil, cap)

    def headroom(self) -> dict:
        mi = meminfo()
        crit_psi = read_psi_some_avg10(self.cg.tier_dir(TIER_CRITICAL))
        avail_frac = (mi["available"] / mi["total"]) if mi["total"] else 1.0

        pressure = max(min(crit_psi / 100.0, 1.0), max(0.0, (0.20 - avail_frac) / 0.20))
        return {"mem": mi, "crit_psi_avg10": crit_psi, "avail_frac": avail_frac,
                "pressure": round(pressure, 4)}

    def plan(self, hr: dict | None = None) -> dict:

        hr = hr or self.headroom()
        pressure = hr["pressure"]
        plan = {"cpu_weight": dict(BASE_CPU_WEIGHT), "io_weight": dict(BASE_IO_WEIGHT),
                "memory_high": {}, "pressure": pressure}

        if pressure > 0.0:
            plan["cpu_weight"][TIER_MISC] = max(10, int(BASE_CPU_WEIGHT[TIER_MISC] * (1.0 - 0.8 * pressure)))
            plan["io_weight"][TIER_MISC] = max(10, int(BASE_IO_WEIGHT[TIER_MISC] * (1.0 - 0.8 * pressure)))
            plan["cpu_weight"][TIER_BATCH] = max(50, int(BASE_CPU_WEIGHT[TIER_BATCH] * (1.0 - 0.5 * pressure)))
            plan["io_weight"][TIER_BATCH] = max(50, int(BASE_IO_WEIGHT[TIER_BATCH] * (1.0 - 0.5 * pressure)))

        for tier in REFINE_TIERS:
            cap = self.inv.batch_max if tier == TIER_BATCH else self.inv.misc_max
            if not cap:
                continue
            relax = self._relax_ceiling(tier, cap)
            floor = int(cap * self.high_floor_frac[tier])
            if floor > relax:
                floor = relax
            plan["memory_high"][tier] = int(relax - (relax - floor) * pressure)
        return plan

    def apply(self, plan: dict | None = None) -> dict:

        plan = plan or self.plan()
        report = {"written": [], "refused": [], "pressure": plan.get("pressure")}
        tier_caps = {TIER_BATCH: self.inv.batch_max, TIER_MISC: self.inv.misc_max}

        capacity_ok = self.inv.capacity_known()

        for knob, table in (("cpu.weight", plan.get("cpu_weight", {})),
                            ("io.weight", plan.get("io_weight", {}))):
            for tier, val in table.items():
                if tier not in REFINE_TIERS:
                    continue
                path = os.path.join(self.cg.tier_dir(tier), knob)
                if write_cg(path, int(val)):
                    report["written"].append((tier, knob, int(val)))
                else:
                    report["refused"].append((tier, knob, int(val), "write-failed:absent-tier-or-controller"))

        if not capacity_ok or not self.inv.holds():
            report["refused"].append(("ALL", "memory.high", None,
                                      "capacity unknown (MemTotal<=0) or INVARIANT broken -> refusing soft raises"))
        else:
            for tier, val in plan.get("memory_high", {}).items():
                ok, why = self.inv.check_memory_high(tier, val, tier_caps)
                if not ok:
                    report["refused"].append((tier, "memory.high", val, why))
                    continue
                path = os.path.join(self.cg.tier_dir(tier), "memory.high")
                if write_cg(path, int(val)):
                    report["written"].append((tier, "memory.high", int(val)))
                else:
                    report["refused"].append((tier, "memory.high", int(val), "write-failed:absent-tier-or-controller"))

        report["enforced"] = len(report["written"])
        report["write_failures"] = sum(1 for r in report["refused"]
                                       if str(r[-1]).startswith("write-failed"))
        report["zero_enforcement"] = (report["enforced"] == 0 and report["write_failures"] > 0)
        return report

    def restore_baseline(self) -> dict:

        restored = []
        for tier in REFINE_TIERS:
            base = self.baseline_high.get(tier)
            if base is None:
                continue
            if write_cg(os.path.join(self.cg.tier_dir(tier), "memory.high"), int(base)):
                restored.append((tier, int(base)))
        if restored:
            self._log(f"fairshare: restored memory.high to PID1 baseline on exit: {restored}")
        return {"restored": restored}

    def refuse_unsafe_raise(self, tier, proposed_max) -> tuple[bool, str]:

        if tier == TIER_BATCH:
            ok = self.inv.holds(batch_max=proposed_max)
        elif tier == TIER_MISC:
            ok = self.inv.holds(misc_max=proposed_max)
        else:
            return False, "refused: critical hard cap is PID1's domain"
        if not ok:
            return False, (f"refused: raising {tier} memory.max to {proposed_max} breaks the "
                           f"no-OOM invariant (demand>{self.inv.backing})")
        return True, "ok"

def _row_llm_weight(row) -> int:

    try:
        prof = row["profile"]
    except (KeyError, IndexError, TypeError):
        prof = None
    if not prof:
        return 0
    try:
        d = json.loads(prof)
        w = d.get("llm_weight", 0)
        return int(w) if isinstance(w, (int, float)) and not isinstance(w, bool) else 0
    except Exception:
        return 0

def _eff_key(row, now):

    return (row["prio"] - int((now - row["submitted_at"]) // 600), row["id"])

def _db_filler_source(_db) -> str:

    return getattr(_db, "FILLER_SOURCE", "filler")

class EtaService:

    def __init__(self, db_path=None, fairshare: FairShare | None = None,
                 ewma_seconds=None, cache_ttl=0.25, log=None,
                 acct_reader=None, llm_headroom_fn=None):

        from pnlib import db as _db
        self._db = _db
        if db_path is None:
            from pnlib import DB_PATH
            db_path = DB_PATH
        self.db_path = db_path
        self.fair = fairshare
        self._log = log or (lambda m: None)

        self._svc_ewma = float(ewma_seconds) if ewma_seconds else 60.0
        self._alpha = 0.2

        self._acct = acct_reader
        self._llm_headroom_fn = llm_headroom_fn
        self._cache = None
        self._cache_at = 0.0
        self._cache_ttl = cache_ttl

        self._eta_cache: dict[int, tuple] = {}
        self._eta_cache_max = 4096
        self._lock = threading.Lock()

    def observe_completion(self, seconds):

        try:
            sec = float(seconds)
        except (TypeError, ValueError):
            return
        if sec > 0:
            with self._lock:
                self._svc_ewma = (1 - self._alpha) * self._svc_ewma + self._alpha * sec
                self._eta_cache.clear()

    def _ewma(self) -> float:
        with self._lock:
            return self._svc_ewma

    def _svc_for_type(self, task_type) -> float:

        if self._acct is not None and task_type:
            try:
                v = self._acct.type_service_time(task_type)
            except Exception:
                v = None
            if v and v > 0:
                return float(v)
        return self._ewma()

    def _llm_pool(self) -> dict | None:

        if self._llm_headroom_fn is None:
            return None
        try:
            hr = self._llm_headroom_fn()
        except Exception:
            return None
        if isinstance(hr, dict) and isinstance(hr.get("llm_pool"), (int, float)) and hr["llm_pool"] > 0:
            return hr
        return None

    def _connect(self):

        import sqlite3
        try:
            cx = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=0.25)
        except sqlite3.OperationalError:
            cx = sqlite3.connect(self.db_path, timeout=0.25)
        cx.row_factory = sqlite3.Row
        cx.execute("PRAGMA busy_timeout=200")
        return cx

    def _headroom(self) -> dict:
        if not self.fair:
            mi = meminfo()
            return {"pressure": None, "mem_total_mib": mi["total"] // MIB,
                    "mem_available_mib": mi["available"] // MIB}
        hr = self.fair.headroom()
        mi = hr["mem"]
        return {
            "pressure": hr["pressure"],
            "crit_psi_avg10": hr["crit_psi_avg10"],
            "mem_total_mib": mi["total"] // MIB,
            "mem_available_mib": mi["available"] // MIB,
            "swap_used_mib": (mi["swap_total"] - mi["swap_free"]) // MIB,
        }

    def status(self) -> dict:

        now = time.monotonic()
        with self._lock:
            if self._cache and (now - self._cache_at) < self._cache_ttl:
                return copy.deepcopy(self._cache)
        snap = self._compute_status()
        with self._lock:
            self._cache, self._cache_at = snap, now
        return copy.deepcopy(snap)

    def _compute_status(self) -> dict:
        out = {"ok": True, "ts": time.time(), "svc_ewma_s": round(self._ewma(), 2)}
        try:
            cx = self._connect()
            try:
                out["counts"] = self._db.counts(cx)
                head = self._db.next_queued(cx)
                out["head"] = ({"id": head["id"], "prio": head.get("prio")} if head else None)
                out["running"] = len(self._db.running(cx))
            finally:
                cx.close()
        except Exception as e:
            out["ok"] = False
            out["queue_error"] = f"{type(e).__name__}: {e}"
            out["counts"] = {}
        out["headroom"] = self._headroom()
        return out

    def _ahead_of(self, cx, jid) -> list:

        me = cx.execute("SELECT id,state,source,prio,submitted_at FROM jobs WHERE id=?",
                        (jid,)).fetchone()
        if not me or me["state"] != "queued":
            return []
        filler = _db_filler_source(self._db)
        is_filler = (me["source"] == filler)
        op = "=" if is_filler else "!="
        rows = cx.execute(
            f"SELECT id,prio,submitted_at,profile,task_type FROM jobs "
            f"WHERE state='queued' AND source {op} ?", (filler,)).fetchall()
        nowt = time.time()
        mykey = _eff_key(me, nowt)
        return [dict(r) for r in rows if _eff_key(r, nowt) < mykey]

    def job_eta(self, job_id) -> dict:

        try:
            jid = int(job_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid job id",
                    "id": job_id if isinstance(job_id, (int, str)) else None}

        now = time.monotonic()
        with self._lock:
            hit = self._eta_cache.get(jid)
            if hit and (now - hit[0]) < self._cache_ttl:
                return copy.deepcopy(hit[1])
        seed = self._ewma()
        out = {"ok": True, "id": jid, "ts": time.time(), "svc_ewma_s": round(seed, 2)}
        try:
            cx = self._connect()
            try:
                row = cx.execute("SELECT id,state,prio,profile,task_type FROM jobs WHERE id=?",
                                 (jid,)).fetchone()
                if not row:
                    return {"ok": False, "error": "no such job", "id": jid}
                out["state"] = row["state"]
                out["task_type"] = row["task_type"]
                svc = self._svc_for_type(row["task_type"])
                out["svc_s"] = round(svc, 2)
                run = self._db.running(cx)
                concurrency = max(1, len(run))
                if row["state"] == "running":
                    out["position"] = 0
                    out["eta_s"] = round(svc / concurrency, 1)
                    out["waiting_on"] = None
                elif row["state"] == "queued":
                    pos = self._db.position(cx, jid)
                    out["position"] = pos
                    ahead = (pos - 1) + len(run)
                    count_eta = (ahead * svc) / concurrency
                    eta = count_eta
                    waiting_on = "position" if pos > 1 else None

                    my_llm = _row_llm_weight(row)
                    pool = self._llm_pool() if my_llm > 0 else None
                    if pool:
                        psize = max(1, int(pool["llm_pool"]))
                        ahead_rows = self._ahead_of(cx, jid)
                        llm_ahead = sum(1 for r in ahead_rows if _row_llm_weight(r) > 0)
                        llm_ahead += sum(1 for r in run if _row_llm_weight(r) > 0)
                        llm_svc = self._svc_for_type(row["task_type"])
                        llm_eta = (llm_ahead * llm_svc) / psize
                        if llm_eta > eta:
                            eta = llm_eta
                            waiting_on = "llm"
                    out["eta_s"] = round(eta, 1)
                    out["waiting_on"] = waiting_on
                elif row["state"] == "blocked":
                    out["position"] = None
                    out["eta_s"] = None
                    out["waiting_on"] = "dep"
                else:
                    out["position"] = self._db.position(cx, jid)
                    out["eta_s"] = 0.0 if row["state"] == "done" else None
                    out["waiting_on"] = None
            finally:
                cx.close()
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "id": jid}
        out["headroom"] = self._headroom()
        with self._lock:

            if len(self._eta_cache) >= self._eta_cache_max:
                self._eta_cache.clear()
            self._eta_cache[jid] = (now, out)
        return copy.deepcopy(out)

CTRL_SOCK = "/run/pn-governord.sock"
MAX_FRAME = 1 << 16

def peer_uid(conn) -> int | None:

    try:
        creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", creds)
        return uid
    except OSError:
        return None

def socket_owned_by_live_peer(path) -> bool:

    if not os.path.exists(path):
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(path)
        return True
    except (ConnectionRefusedError, FileNotFoundError):
        return False
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass

class SocketInUse(Exception):
    pass

class ControlServer:

    def __init__(self, klaviatur: Klaviatur, eta: EtaService | None, fairshare: FairShare | None,
                 path=CTRL_SOCK, allow_uids=None, group=None, log=None, max_workers=16,
                 last_apply=None):
        self.klav = klaviatur
        self.eta = eta
        self.fair = fairshare
        self.path = path

        if allow_uids is None:
            allow_uids = {0, os.getuid()}
        self.allow_uids = set(allow_uids)
        self.group = group
        self._log = log or (lambda m: None)
        self._sock = None
        self._stop = False

        self._sem = threading.BoundedSemaphore(value=max(1, max_workers))

        self._last_apply = last_apply or (lambda: None)

    def _bind(self):

        if os.path.exists(self.path):
            if socket_owned_by_live_peer(self.path):
                raise SocketInUse(f"control socket {self.path} is already owned by a live Governor")
            try:
                os.unlink(self.path)
            except OSError:
                pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        s.bind(self.path)

        if self.group:
            try:
                import grp
                os.chown(self.path, -1, grp.getgrnam(self.group).gr_gid)
                os.chmod(self.path, 0o660)
            except (KeyError, OSError) as e:
                self._log(f"control: group '{self.group}' not applied ({e}); leaving uid-only 0600")
                os.chmod(self.path, 0o600)
        else:
            os.chmod(self.path, 0o600)
        s.listen(16)
        self._sock = s
        return s

    def serve_forever(self):

        self._bind()
        self._log(f"control: up at {self.path} (peercred uid allow-list {sorted(self.allow_uids)}, "
                  f"max_workers={self._sem._initial_value})")
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except OSError as e:
                if self._stop:
                    break
                if e.errno in (errno.EMFILE, errno.ENFILE, errno.ECONNABORTED, errno.EINTR):
                    time.sleep(0.05)
                    continue
                raise

            if not self._sem.acquire(blocking=False):
                self._reply(conn, {"ok": False, "error": "busy"})
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            try:
                threading.Thread(target=self._handle, args=(conn,), daemon=True).start()
            except RuntimeError:

                self._sem.release()
                try:
                    conn.close()
                except OSError:
                    pass

    def stop(self):
        self._stop = True
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass

    def _handle(self, conn):
        try:
            conn.settimeout(5)
            uid = peer_uid(conn)

            if uid is None or uid not in self.allow_uids:
                self._reply(conn, {"ok": False, "error": "unauthorized"})
                return
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
                if len(buf) > MAX_FRAME:
                    self._reply(conn, {"ok": False, "error": "frame too large"})
                    return
            try:
                req = json.loads(buf.split(b"\n", 1)[0].decode())
            except (ValueError, UnicodeDecodeError):
                self._reply(conn, {"ok": False, "error": "bad json"})
                return
            self._reply(conn, self.dispatch(req, uid))
        except Exception:
            pass
        finally:

            try:
                conn.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
            self._sem.release()

    def dispatch(self, req, uid) -> dict:

        if not isinstance(req, dict):
            return {"ok": False, "error": "request must be an object"}
        verb = req.get("verb")

        if verb == "queue.status":
            return self.eta.status() if self.eta else {"ok": False, "error": "eta unavailable"}
        if verb == "queue.eta":
            if not self.eta:
                return {"ok": False, "error": "eta unavailable"}
            jid = req.get("id")

            if not isinstance(jid, int) and not (isinstance(jid, str) and jid.strip().lstrip("-").isdigit()):
                return {"ok": False, "error": "queue.eta needs integer id"}
            return self.eta.job_eta(jid)
        if verb == "queue.observe":

            if not self.eta:
                return {"ok": False, "error": "eta unavailable"}
            self.eta.observe_completion(req.get("seconds"))
            return {"ok": True, "msg": "observed"}

        if verb == "fairshare.headroom":
            return {"ok": True, "headroom": self.fair.headroom()} if self.fair else \
                {"ok": False, "error": "fairshare unavailable"}
        if verb == "fairshare.plan":
            return {"ok": True, "plan": self.fair.plan()} if self.fair else \
                {"ok": False, "error": "fairshare unavailable"}
        if verb == "fairshare.status":

            last = self._last_apply()
            if last is None:
                return {"ok": True, "enforced": None, "note": "no apply pass recorded yet"}
            return {"ok": True, "enforced": last.get("enforced"),
                    "write_failures": last.get("write_failures"),
                    "zero_enforcement": last.get("zero_enforcement"),
                    "pressure": last.get("pressure")}

        if verb == "klavier":
            argv = req.get("argv")
            if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
                return {"ok": False, "error": "klavier needs argv: [str,...]"}
            return self.klav.exec_cmd(argv, peer_uid=uid)
        if verb == "ping":
            return {"ok": True, "pong": True}
        return {"ok": False, "error": f"unknown verb: {verb!r}"}

    @staticmethod
    def _reply(conn, obj):
        try:
            conn.sendall((json.dumps(obj) + "\n").encode())
        except OSError:
            pass

def client_request(req: dict, path=CTRL_SOCK, timeout=5.0) -> dict:

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        try:
            s.sendall((json.dumps(req) + "\n").encode())
        except (BrokenPipeError, ConnectionResetError):

            pass
        buf = b""
        while b"\n" not in buf:
            try:
                chunk = s.recv(65536)
            except (ConnectionResetError, OSError):
                break
            if not chunk:
                break
            buf += chunk
            if len(buf) > (1 << 20):
                return {"ok": False, "error": "response too large"}
        line = buf.split(b"\n", 1)[0]
        return json.loads(line.decode()) if line else {"ok": False, "error": "no reply (closed)"}
    finally:
        s.close()
