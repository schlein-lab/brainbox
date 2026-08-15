

INFLIGHT_LOAD_PER_JOB = 1.0

DEFAULT_MAX_HEALTH_AGE_S = 180.0

DEFAULT_RAM_WEIGHT = 0.5

def ram_weight():
    import os
    try:
        w = float(os.environ.get("PN_FLEETPICK_RAM_WEIGHT", "") or DEFAULT_RAM_WEIGHT)
    except (TypeError, ValueError):
        w = DEFAULT_RAM_WEIGHT
    return max(0.0, min(w, 1.0))

def _blend(cpu_util, mem_avail_mb, mem_total_mb, w_ram):

    try:
        if w_ram > 0 and mem_avail_mb is not None and mem_total_mb:
            ram_util = 1.0 - (float(mem_avail_mb) / float(mem_total_mb))
            ram_util = max(0.0, min(ram_util, 1.0))
            return (1.0 - w_ram) * cpu_util + w_ram * ram_util
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return cpu_util

def _health(rec):
    return (rec or {}).get("facts", {}).get("health", {}) or {}

def _caps_ok(rec, kind):

    caps = (rec or {}).get("caps") or {}
    kinds = caps.get("kinds")
    if isinstance(kinds, list):
        from .remotedispatch import _KIND_ALIASES
        return any(a in kinds for a in _KIND_ALIASES.get(kind, (kind,)))
    return True

def _node_arch(rec):

    rec = rec or {}
    a = (rec.get("caps") or {}).get("arch")
    if a:
        return a
    return _health(rec).get("arch")

def _arch_ok(rec, box_arch, arch_ok):

    if arch_ok:
        return True
    if not box_arch:
        return True
    return _node_arch(rec) == box_arch

def _accepting(rec):

    h = _health(rec)
    if h.get("draining") is True:
        return False
    if h.get("node_active") is False:
        return False
    if str(h.get("mode") or "").strip().lower() == "off":
        return False
    return True

def _node_util(rec, inflight, w_ram=0.0):

    h = _health(rec)
    load1 = h.get("load1")
    nproc = h.get("nproc") or h.get("cpu_count")
    if load1 is None or not nproc:
        return None
    eff = max(1, int(nproc))
    cpu = (float(load1) + inflight * INFLIGHT_LOAD_PER_JOB) / eff
    return _blend(cpu,
                  h.get("mem_avail_mb", h.get("mem_available_mb")),
                  h.get("mem_total_mb") or h.get("mem_total"), w_ram)

def pick_compute_node(workers, kind, box_load1, box_nproc, *,
                      reserved_cores=1, inflight=None, now=None, max_health_age_s=DEFAULT_MAX_HEALTH_AGE_S,
                      local_bias=0.0, box_arch=None, arch_ok=False,
                      box_mem_avail_mb=None, box_mem_total_mb=None):

    inflight = inflight or {}
    w_ram = ram_weight()

    box_eff = max(1, int(box_nproc or 1) - int(reserved_cores or 0))
    box_util = (float(box_load1 or 0.0) + inflight.get(None, 0) * INFLIGHT_LOAD_PER_JOB) / box_eff
    box_util = _blend(box_util, box_mem_avail_mb, box_mem_total_mb, w_ram)
    box_util -= float(local_bias or 0.0)
    best_id, best_util = None, box_util

    for nid, rec in (workers or {}).items():
        if not rec or rec.get("state") != "online":
            continue
        if not rec.get("endpoint"):
            continue
        ls = rec.get("last_seen")
        if now is not None and ls is not None and (now - ls) > max_health_age_s:
            continue
        if not _accepting(rec):
            continue
        if not _caps_ok(rec, kind):
            continue
        if not _arch_ok(rec, box_arch, arch_ok):
            continue
        util = _node_util(rec, inflight.get(nid, 0), w_ram=w_ram)
        if util is None:
            continue

        if util < best_util - 1e-9:
            best_id, best_util = nid, util

    return best_id
