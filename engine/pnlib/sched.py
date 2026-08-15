
from __future__ import annotations
import os
from dataclasses import dataclass
from . import meters
from .profile import ResourceProfile

DEFAULT_RESERVED_CORES = 1

@dataclass
class Config:
    mem_floor: int
    batch_high: int
    psi_stop: float = 25.0
    psi_resume: float = 10.0

    psi_stop_thrash: float = 60.0
    psi_resume_thrash: float = 30.0

    interactive_reserve: int = 0
    interactive_slots: int = 0
    max_concurrent: int = 8
    poll_interval: float = 2.0
    slack: int = 256
    max_per_tick: int = 3

    cpu_budget: float = float("inf")

    @staticmethod
    def autoscale() -> "Config":
        mi = meters.meminfo()
        T = mi["total"] or 9943
        cpus = meters.cpu_count()
        def _ov(name, dflt):
            v = os.environ.get(name, '').strip()
            return int(v) if v.lstrip('-').isdigit() else dflt
        def _cpu_budget():

            exp = os.environ.get('PN_CPU_BUDGET', '').strip().lower()
            if exp in ('inf', 'off', 'none', 'disable', 'disabled'):
                return float('inf')
            if exp.isdigit():
                return float(int(exp))
            rc = os.environ.get('PN_RESERVED_CORES', '').strip()
            reserved = int(rc) if rc.lstrip('-').isdigit() else DEFAULT_RESERVED_CORES
            return float(max(1, cpus - max(0, reserved)))
        def _fv(name, dflt):
            v = os.environ.get(name, '').strip()
            try:
                return float(v)
            except ValueError:
                return dflt
        bh = _ov('PN_BATCH_HIGH', int(T * 0.50))
        return Config(
            mem_floor=_ov('PN_MEM_FLOOR', max(1024, int(T * 0.16))),
            batch_high=bh,
            max_concurrent=_ov('PN_MAX_CONCURRENT', max(4, cpus + 2)),
            cpu_budget=_cpu_budget(),
            psi_stop_thrash=_fv('PN_PSI_STOP_THRASH', 60.0),
            psi_resume_thrash=_fv('PN_PSI_RESUME_THRASH', 30.0),
            interactive_reserve=_ov('PN_INTERACTIVE_RESERVE', max(0, int(bh * 0.25))),
            interactive_slots=_ov('PN_INTERACTIVE_SLOTS', 2),
        )

@dataclass
class Decision:
    admit: bool
    reason: str

def _default_unset_width() -> float:
    v = os.environ.get("PN_CPU_WIDTH_UNSET", "").strip()
    try:
        return float(v) if v else max(2.0, meters.cpu_count() * 0.75)
    except ValueError:
        return max(2.0, meters.cpu_count() * 0.75)

CPU_WIDTH_UNSET = _default_unset_width()

def cpu_width(prof: ResourceProfile) -> float:

    q = getattr(prof, "cpu_quota_pct", None)
    return max(0.0, q / 100.0) if q else CPU_WIDTH_UNSET

TRACKS = ("interactive", "batch", "filler")

def track_of(prof: ResourceProfile) -> str:

    lat = getattr(prof, "latency", None)
    if lat == "realtime":
        return "interactive"
    if lat == "filler":
        return "filler"
    if lat == "deferrable":
        return "batch"
    if int(getattr(prof, "prio", 100) or 100) >= 200:
        return "filler"
    if getattr(prof, "sandbox", "") == "llm" or int(getattr(prof, "llm_weight", 0) or 0) > 0:
        return "interactive"
    return "batch"

def evaluate(cfg: Config, snap: dict, prof: ResourceProfile,
             running: int, reserved: int, pressure_blocked: bool,
             cpu_running: float = 0.0, track: str = "batch",
             batch_blocked: bool = False, track_running: int | None = None,
             slotless: bool = False, disk_degraded: bool = False) -> Decision:

    if not slotless:
        if running >= cfg.max_concurrent:
            return Decision(False, f"max_concurrent {running}/{cfg.max_concurrent}")

        if track == "batch" and cfg.interactive_slots:
            batch_slots = max(1, cfg.max_concurrent - cfg.interactive_slots)
            if running >= batch_slots:
                return Decision(False,
                                f"batch slots {running}/{batch_slots} (reserved for latency work)")
    if pressure_blocked:
        return Decision(False, "machine unhealthy (protected tiers psi_full=%.0f, avail=%sMiB)"
                        % (snap.get("psi_ctl_full_avg10") or 0, snap.get("mem_available")))

    if track == "batch" and batch_blocked:
        return Decision(False, f"batch tier throttled psi={snap.get('psi_avg10') or 0:.0f}")

    if track != "interactive" and disk_degraded:
        return Decision(False, "DATA-Platte unter der harten Marke: Massenarbeit wartet, "
                               "latenzkritische Arbeit laeuft weiter (es wird nichts geloescht)")

    if snap["disk_free"] < prof.disk_min_free:
        return Decision(False, f"disk_free {snap['disk_free']}<{prof.disk_min_free}MiB")

    track_idle = (track_running if track_running is not None else running) == 0

    if track != "interactive" and cfg.cpu_budget != float("inf"):
        width = cpu_width(prof)
        if not track_idle and cpu_running + width > cfg.cpu_budget + 1e-9:
            return Decision(False, f"cpu budget {cpu_running:.1f}+{width:.1f}>{cfg.cpu_budget:.1f}")

    if track == "interactive":
        used = snap.get("interactive_current") or 0
        budget = cfg.interactive_reserve or cfg.batch_high
    else:
        used = max(snap["batch_current"], reserved)
        budget = cfg.batch_high

    if used + prof.mem > budget:

        if track_idle and snap["mem_available"] - prof.mem > cfg.mem_floor:
            return Decision(True, "escape-valve (idle, single job)")
        return Decision(False, f"{track} budget {used}+{prof.mem}>{budget}")
    if snap["mem_available"] - prof.mem < cfg.mem_floor + cfg.slack:

        if running == 0 and track_idle:
            return Decision(True, "escape-valve (idle, floor)")
        return Decision(False, f"mem floor {snap['mem_available']}-{prof.mem}<{cfg.mem_floor}")

    return Decision(True, "ok")

LLM_LOOSE_PER_SLOT = 4
_SLOT_TENTHS = 10

def llm_demand_slots(prof: ResourceProfile) -> float:

    w = max(0, int(getattr(prof, "llm_weight", 0) or 0))
    if w == 0:
        return 0.0
    if getattr(prof, "llm_kind", "loose") == "dedicated":
        return float(w)
    tenths = -(-w * _SLOT_TENTHS // LLM_LOOSE_PER_SLOT)
    return tenths / float(_SLOT_TENTHS)

def llm_ok(hr: dict | None, prof: ResourceProfile, llm_reserved: float = 0.0) -> Decision:

    demand = llm_demand_slots(prof)
    if demand <= 0:
        return Decision(True, "ok (no llm)")
    if hr is None or "llm_free" not in hr:
        return Decision(True, "ok (llm gate skipped)")
    free = hr.get("llm_free")
    if free is None:
        return Decision(False, "llm pool unavailable (refuse-all)")
    avail = float(free) - float(llm_reserved or 0.0)
    if demand > avail + 1e-9:
        return Decision(False, f"llm pool {avail:.2f}<{demand:.2f} slots")
    return Decision(True, "ok")

def dynamic_mem_high(cfg: Config, snap: dict, reserved: int, prof: ResourceProfile) -> int:

    used = max(snap.get("batch_current", 0), reserved)
    free_batch = max(0, cfg.batch_high - used)
    return max(prof.mem, min(cfg.batch_high, prof.mem + free_batch))

FILLER_CAP_DEFAULT = 2
FILLER_RESERVE_FRAC = 0.20

def filler_fits(cfg: Config, proj: dict, prof: ResourceProfile, reserved: int,
                hr: dict | None, llm_reserved: float, filler_running: int,
                pressure_blocked: bool, filler_cap: int | None = None,
                reserve_frac: float = FILLER_RESERVE_FRAC,
                cpu_running: float = 0.0) -> Decision:

    cap = FILLER_CAP_DEFAULT if filler_cap is None else filler_cap
    if filler_running >= cap:
        return Decision(False, f"filler_cap {filler_running}/{cap}")
    if pressure_blocked:
        return Decision(False, "filler yields under pressure")
    if cpu_running + cpu_width(prof) > cfg.cpu_budget + 1e-9:
        return Decision(False,
                        f"filler cpu {cpu_running:.1f}+{cpu_width(prof):.1f}>{cfg.cpu_budget:.1f}")
    if proj["disk_free"] < prof.disk_min_free:
        return Decision(False, "filler disk")
    used = max(proj["batch_current"], reserved)

    filler_ceiling = int(cfg.batch_high * (1.0 - reserve_frac))
    if used + prof.mem > filler_ceiling:
        return Decision(False, f"filler reserve {used}+{prof.mem}>{filler_ceiling}")
    if proj["mem_available"] - prof.mem < cfg.mem_floor + cfg.slack:
        return Decision(False, "filler mem floor")
    ld = llm_ok(hr, prof, llm_reserved)
    if not ld.admit:
        return Decision(False, f"filler {ld.reason}")
    return Decision(True, "filler ok")

def preempt_plan(need_mem: int, need_llm: float, fillers: list) -> list:

    if need_mem <= 0 and need_llm <= 0:
        return []

    def score(f):
        s = 0.0
        if need_llm > 0:
            s += f.get("llm", 0.0) * 1000.0
        if need_mem > 0:
            s += f.get("mem", 0)
        return s
    ordered = sorted(fillers, key=score, reverse=True)
    out, freed_mem, freed_llm = [], 0, 0.0
    for f in ordered:
        if freed_mem >= need_mem and freed_llm >= need_llm:
            break

        helps = ((need_mem > freed_mem and f.get("mem", 0) > 0) or
                 (need_llm > freed_llm and f.get("llm", 0.0) > 0))
        if not helps:
            continue
        out.append(f["id"])
        freed_mem += f.get("mem", 0)
        freed_llm += f.get("llm", 0.0)
    return out

WALLTIME_WARN_FRAC = float(os.environ.get("PN_WALLTIME_WARN_FRAC", "0.8"))

def walltime_phase(prof: ResourceProfile, wall_s: float, extra_s: float = 0.0,
                   warned: bool = False) -> str | None:

    limit = prof.timeout_s + max(0.0, extra_s)
    if wall_s > limit:
        return "kill"
    if not warned and wall_s >= limit * WALLTIME_WARN_FRAC:
        return "warn"
    return None

def oom_grow_target(prof: ResourceProfile, oom_retries: int, exit_code: int | None,
                    oom_kill: int | None = None) -> int | None:

    if not getattr(prof, "oom_grow", False):
        return None
    if oom_retries >= prof.max_oom_retries:
        return None
    oomed = (oom_kill or 0) > 0 or (oom_kill is None and exit_code == 137)
    if not oomed:
        return None

    grown = max(int(prof.mem * prof.oom_grow_mult), prof.mem + 128)
    if prof.mem_max:
        grown = min(grown, prof.mem_max)
    return grown if grown > prof.mem else None

class PressureGate:

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.blocked = False

    def update(self, psi: float) -> bool:
        if self.blocked:
            if psi <= self.cfg.psi_resume:
                self.blocked = False
        else:
            if psi >= self.cfg.psi_stop:
                self.blocked = True
        return self.blocked
