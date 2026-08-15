

import os
import glob
import json
import time
import threading
import subprocess

ADMIN_ROLES = ("owner", "admin")
FEATURES = ("streaming", "wan", "queue", "screen", "voice", "browser", "terminal")

def require_admin(role):

    return str(role or "").strip().lower() in ADMIN_ROLES

def _g(ctx, key, default=None):
    try:
        if isinstance(ctx, dict):
            return ctx.get(key, default)
    except Exception:
        pass
    return default

def _call(ctx, key, *a, **k):

    fn = _g(ctx, key)
    if not callable(fn):
        return None
    try:
        return fn(*a, **k)
    except Exception:
        return None

def _uid_safe(ctx, uid):
    fn = _g(ctx, "uid_safe")
    if callable(fn):
        try:
            return fn(uid)
        except Exception:
            pass

    import re
    return re.sub(r"[^A-Za-z0-9_.-]", "", str(uid or "owner"))[:64] or "owner"

def _data_dir(ctx):
    d = _g(ctx, "DATA_DIR")
    if not d:
        d = os.path.join(os.path.expanduser("~"), ".local", "share", "brainbox-portal")
    return d

def _cgroup_root(ctx):
    return _g(ctx, "cgroup_root", "/sys/fs/cgroup")

def _nproc(ctx):
    n = _g(ctx, "nproc")
    try:
        n = int(n) if n else (os.cpu_count() or 2)
    except Exception:
        n = os.cpu_count() or 2
    return max(1, n)

def _default_reserved(ctx):
    try:
        return max(0, int(_g(ctx, "default_reserved_cores", 1)))
    except Exception:
        return 1

def _prov(ctx, verb, principal, args_text, meta=None):

    fn = _g(ctx, "prov_log")
    if callable(fn):
        try:
            fn(verb, principal or "owner", args_text or "", meta or {})
        except Exception:
            pass

def _scope_glob(ctx, uid):

    root = _cgroup_root(ctx)
    u = _uid_safe(ctx, uid)
    try:
        hits = glob.glob(os.path.join(root, "**", "phantom-%s-*.scope" % u), recursive=True)
        return sorted(d for d in hits if os.path.isdir(d))
    except Exception:
        return []

def _all_scope_dirs(ctx):
    root = _cgroup_root(ctx)
    try:
        hits = glob.glob(os.path.join(root, "**", "phantom-*.scope"), recursive=True)
        return sorted(d for d in hits if os.path.isdir(d))
    except Exception:
        return []

def _uid_of_scope(dirname):

    b = os.path.basename(dirname)
    if not (b.startswith("phantom-") and b.endswith(".scope")):
        return None
    core = b[len("phantom-"):-len(".scope")]
    parts = core.rsplit("-", 2)
    return parts[0] if len(parts) == 3 else core

def _slice_dir(ctx, name):

    root = _cgroup_root(ctx)
    try:
        for d in glob.glob(os.path.join(root, "**", name), recursive=True):
            if os.path.isdir(d):
                return d
    except Exception:
        pass
    return None

def _read_text(path):
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return ""

def _read_int(path):
    t = _read_text(path).strip()
    if not t or t == "max":
        return None
    try:
        return int(t.split()[0])
    except Exception:
        return None

def _cpu_usage_usec(scope_dir):

    for line in _read_text(os.path.join(scope_dir, "cpu.stat")).splitlines():
        if line.startswith("usage_usec"):
            try:
                return int(line.split()[1])
            except Exception:
                return None
    return None

def _cpu_sample(scope_dirs, dt=0.12):

    dt = max(0.03, float(dt or 0.12))
    t0 = {}
    start = time.time()
    for d in scope_dirs:
        t0[d] = _cpu_usage_usec(d)
    try:
        time.sleep(dt)
    except Exception:
        pass
    elapsed = max(1e-6, time.time() - start)
    out = {}
    for d in scope_dirs:
        u1 = _cpu_usage_usec(d)
        u0 = t0.get(d)
        if u0 is None or u1 is None or u1 < u0:
            out[d] = 0.0
        else:
            out[d] = round((u1 - u0) / 1e6 / elapsed * 100.0, 1)
    return out

def _mem_current(scope_dir):
    v = _read_int(os.path.join(scope_dir, "memory.current"))
    return v if v is not None else 0

def _cpu_max_pct(scope_dir):

    t = _read_text(os.path.join(scope_dir, "cpu.max")).split()
    if not t or t[0] == "max":
        return None
    try:
        quota = int(t[0]); period = int(t[1]) if len(t) > 1 else 100000
        return round(quota / period * 100.0, 0)
    except Exception:
        return None

def _cpu_weight(scope_dir):
    return _read_int(os.path.join(scope_dir, "cpu.weight"))

def _mem_max_mb(scope_dir):
    v = _read_int(os.path.join(scope_dir, "memory.max"))
    return round(v / (1024 * 1024)) if v is not None else None

def _psi_some_avg10(cgroup_dir, resource="cpu"):

    if not cgroup_dir:
        return None
    for line in _read_text(os.path.join(cgroup_dir, "%s.pressure" % resource)).splitlines():
        if line.startswith("some"):
            for tok in line.split():
                if tok.startswith("avg10="):
                    try:
                        return float(tok.split("=", 1)[1])
                    except Exception:
                        return None
    return None

_STORE_LOCK = threading.Lock()

def _store_path(ctx, name):
    return os.path.join(_data_dir(ctx), name)

def _load_json(path, default):
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else default
    except Exception:
        return dict(default) if isinstance(default, dict) else default

def _save_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False

def _alloc_load(ctx):
    return _load_json(_store_path(ctx, "admin_alloc.json"), {})

def _alloc_save(ctx, data):
    return _save_json(_store_path(ctx, "admin_alloc.json"), data)

def _bans_load(ctx):
    return _load_json(_store_path(ctx, "admin_feature_bans.json"), {})

def _bans_save(ctx, data):
    return _save_json(_store_path(ctx, "admin_feature_bans.json"), data)

def _in_window(window, now=None):

    try:
        a, b = str(window).split("-", 1)
        ah, am = [int(x) for x in a.strip().split(":")]
        bh, bm = [int(x) for x in b.strip().split(":")]
    except Exception:
        return False
    lt = time.localtime(now if now is not None else time.time())
    cur = lt.tm_hour * 60 + lt.tm_min
    start = ah * 60 + am
    end = bh * 60 + bm
    if start == end:
        return False
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end

def _ban_active(rec, now=None):

    now = now if now is not None else time.time()
    if not isinstance(rec, dict):
        return False
    window = rec.get("window")
    until = rec.get("until")
    if window:

        if until is not None:
            try:
                if now >= float(until):
                    return False
            except Exception:
                pass
        return _in_window(window, now)
    if until is not None:
        try:
            return now < float(until)
        except Exception:
            return False
    return True

def _active_bans_for(rec_map, now=None):
    out = {}
    for feat, rec in (rec_map or {}).items():
        if _ban_active(rec, now):
            out[feat] = rec
    return out

def list_users(ctx, sample_dt=0.12):

    try:
        users = _call(ctx, "user_list") or []
        scopes = _all_scope_dirs(ctx)
        cpu = _cpu_sample(scopes, sample_dt) if scopes else {}

        by_uid = {}
        for d in scopes:
            u = _uid_of_scope(d)
            if u is not None:
                by_uid.setdefault(u, []).append(d)
        alloc = _alloc_load(ctx)
        bans = _bans_load(ctx)
        now = time.time()
        out = []
        for u in users:
            if not isinstance(u, dict):
                continue
            uid = u.get("uid")
            suid = _uid_safe(ctx, uid)
            dirs = by_uid.get(suid, [])
            cpu_pct = round(sum(cpu.get(d, 0.0) for d in dirs), 1)
            mem_mb = round(sum(_mem_current(d) for d in dirs) / (1024 * 1024))
            frozen = _is_frozen(dirs)
            live = {}
            if dirs:
                d0 = dirs[0]
                live = {"cpu_max_pct": _cpu_max_pct(d0), "mem_max_mb": _mem_max_mb(d0),
                        "weight": _cpu_weight(d0)}
            running = bool(_call(ctx, "seat_running", uid))
            out.append({
                "uid": suid,
                "name": u.get("name") or suid,
                "role": u.get("role") or "user",
                "status": u.get("status") or "active",
                "email": u.get("email") or "",
                "email_verified": int(u.get("email_verified") or 0),
                "email_optout": int(u.get("email_optout") or 0),
                "created": u.get("created") or 0,

                "last_login": u.get("last_login") or 0,
                "auth_source": u.get("auth_source") or "local",
                "birthdate": u.get("birthdate") or "",
                "admin": require_admin(u.get("role")),
                "seat_running": running,
                "scopes": len(dirs),
                "frozen": frozen,
                "usage": {"cpu_pct": cpu_pct, "mem_mb": mem_mb},
                "allocation": alloc.get(suid) or None,
                "live_limits": live,
                "bans": _active_bans_for(bans.get(suid, {}), now),
            })
        return {"ok": True, "users": out, "count": len(out)}
    except Exception as e:
        return {"ok": False, "msg": "list_users failed: %s" % e, "users": []}

def _fleet_totals():

    try:
        import portal_placement as _pp
        cores = mem_total = mem_avail = nodes_on = 0
        for _n in (_pp.nodes() or []):
            if (_n.get("state") or "offline") != "online":
                continue
            if _n.get("draining"):
                continue
            _res = _n.get("res") or {}
            _np = _res.get("nproc")
            if not _np:
                continue
            cores += int(_np)
            mem_total += int(_res.get("mem_total_mb") or 0)
            mem_avail += int(_res.get("mem_avail_mb") or 0)
            nodes_on += 1
        if nodes_on <= 0:
            return None
        return {"nproc": cores, "mem_total_mb": mem_total,
                "mem_avail_mb": mem_avail, "nodes": nodes_on}
    except Exception:
        return None

def resource_overview(ctx):

    try:
        nproc = _nproc(ctx)
        reserved = get_reserved_cores(ctx).get("reserved", _default_reserved(ctx))
        cap_pct = max(1, nproc - reserved) * 100
        batch = _slice_dir(ctx, "pn-batch.slice")
        inter = _slice_dir(ctx, "pn-interactive.slice")
        scopes = _all_scope_dirs(ctx)
        cpu = _cpu_sample(scopes) if scopes else {}
        tenant_cpu = round(sum(cpu.values()), 1)
        cells = sorted(set(_uid_of_scope(d) for d in scopes if _uid_of_scope(d)))
        _fleet = _fleet_totals()
        return {
            "ok": True,
            "nproc": nproc,
            "fleet": _fleet,
            "admin_reserved_cores": reserved,
            "tenant_cap_pct": cap_pct,
            "tenant_cpu_pct": tenant_cpu,
            "free_headroom_pct": round(cap_pct - tenant_cpu, 1),
            "active_cells": len(cells),
            "cells": cells,
            "pn_batch": {
                "present": bool(batch),
                "mem_mb": round(_mem_current(batch) / (1024 * 1024)) if batch else None,
                "cpu_psi_avg10": _psi_some_avg10(batch, "cpu"),
                "mem_psi_avg10": _psi_some_avg10(batch, "memory"),
            },
            "pn_interactive": {
                "present": bool(inter),
                "mem_mb": round(_mem_current(inter) / (1024 * 1024)) if inter else None,
                "cpu_psi_avg10": _psi_some_avg10(inter, "cpu"),
            },
        }
    except Exception as e:
        return {"ok": False, "msg": "resource_overview failed: %s" % e}

def _scope_units(ctx, uid):

    return [os.path.basename(d) for d in _scope_glob(ctx, uid)]

def _systemctl_user(*args):

    import shutil
    if not shutil.which("systemctl"):
        return False, "systemctl absent"
    try:
        r = subprocess.run(["systemctl", "--user"] + list(args),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
        return r.returncode == 0, (r.stdout or b"").decode("utf-8", "replace").strip()
    except Exception as e:
        return False, str(e)

def _cap_enforceable():

    import shutil
    if not shutil.which("systemctl"):
        return False
    try:
        return os.path.isdir("/run/user/%d/systemd" % os.getuid())
    except Exception:
        return False

def hard_stop(ctx, uid):

    suid = _uid_safe(ctx, uid)
    done = []
    try:
        _call(ctx, "seat_stop", uid)
        done.append("seat_stop")
    except Exception:
        pass

    try:
        import shutil
        sess = _call(ctx, "voice_sess", uid)
        if sess and shutil.which("tmux"):
            subprocess.run(["tmux", "kill-session", "-t", sess],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            done.append("agent_killed")
    except Exception:
        pass

    units = _scope_units(ctx, suid)
    stopped = 0
    for unit in units:
        ok, _ = _systemctl_user("stop", unit)
        if ok:
            stopped += 1
    if units:
        done.append("scopes_stopped=%d/%d" % (stopped, len(units)))
    _prov(ctx, "admin.hard_stop", suid, ",".join(done), {"admin": True, "uid": suid})
    return {"ok": True, "msg": "hard-stopped %s (%s)" % (suid, ", ".join(done) or "nothing running")}

def soft_stop(ctx, uid):

    suid = _uid_safe(ctx, uid)
    try:
        _set_frozen(ctx, uid, False)
    except Exception:
        pass
    r = _call(ctx, "seat_stop", uid)
    ok = True if (r is None or (isinstance(r, dict) and r.get("ok", True))) else False
    _prov(ctx, "admin.soft_stop", suid, "", {"admin": True, "uid": suid})
    return {"ok": ok, "msg": "soft-stopped %s" % suid}

def _freeze_targets(ctx, uid):

    slice_dir = _slice_dir(ctx, "phantom-%s.slice" % _uid_safe(ctx, uid))
    if slice_dir:
        return [slice_dir]
    return _scope_glob(ctx, uid)

def _is_frozen(dirs):

    if not dirs:
        return False
    for d in dirs:
        if _read_text(os.path.join(d, "cgroup.freeze")).strip() != "1":
            return False
    return True

def _set_frozen(ctx, uid, freeze):
    dirs = _freeze_targets(ctx, uid)
    if not dirs:
        return 0, 0
    val = "1" if freeze else "0"
    ok = 0
    for d in dirs:
        try:
            with open(os.path.join(d, "cgroup.freeze"), "w") as f:
                f.write(val)
            ok += 1
        except Exception:
            pass
    return ok, len(dirs)

def pause(ctx, uid):

    suid = _uid_safe(ctx, uid)
    ok, total = _set_frozen(ctx, uid, True)
    _prov(ctx, "admin.pause", suid, "", {"admin": True, "uid": suid, "frozen": ok})
    if total == 0:
        return {"ok": False, "msg": "no cell cgroup for %s (nothing to freeze)" % suid}
    return {"ok": ok > 0, "msg": "paused %s (froze %d/%d cgroups)" % (suid, ok, total)}

def resume(ctx, uid):

    suid = _uid_safe(ctx, uid)
    ok, total = _set_frozen(ctx, uid, False)
    _prov(ctx, "admin.resume", suid, "", {"admin": True, "uid": suid, "thawed": ok})
    if total == 0:
        return {"ok": False, "msg": "no cell cgroup for %s (nothing to thaw)" % suid}
    return {"ok": ok > 0, "msg": "resumed %s (thawed %d/%d cgroups)" % (suid, ok, total)}

def _alloc_props(cpu_pct, mem_mb, weight):

    props = []
    if cpu_pct is not None:
        try:
            props.append("CPUQuota=%d%%" % max(1, int(cpu_pct)))
        except Exception:
            pass
    if mem_mb is not None:
        try:
            props.append("MemoryMax=%dM" % max(1, int(mem_mb)))
        except Exception:
            pass
    if weight is not None:
        try:
            props.append("CPUWeight=%d" % min(10000, max(1, int(weight))))
        except Exception:
            pass
    return props

def reallocate(ctx, uid, cpu_pct=None, mem_mb=None, weight=None):

    suid = _uid_safe(ctx, uid)
    props = _alloc_props(cpu_pct, mem_mb, weight)
    if not props:
        return {"ok": False, "msg": "no allocation given (cpu_pct/mem_mb/weight all empty)"}

    try:
        with _STORE_LOCK:
            alloc = _alloc_load(ctx)
            rec = alloc.get(suid, {}) if isinstance(alloc.get(suid), dict) else {}
            if cpu_pct is not None:
                rec["cpu_pct"] = int(cpu_pct)
            if mem_mb is not None:
                rec["mem_mb"] = int(mem_mb)
            if weight is not None:
                rec["weight"] = int(weight)
            rec["updated"] = time.time()
            alloc[suid] = rec
            _alloc_save(ctx, alloc)
    except Exception:
        pass

    units = _scope_units(ctx, suid)
    applied = 0
    for unit in units:
        ok, _ = _systemctl_user("set-property", unit, *props)
        if ok:
            applied += 1
    _prov(ctx, "admin.reallocate", suid, ",".join(props),
          {"admin": True, "uid": suid, "applied": applied, "of": len(units)})
    live = (" — applied to %d/%d live scope(s)" % (applied, len(units))) if units else \
           " — no live scope (persisted; applies on next start)"
    return {"ok": True, "msg": "reallocated %s: %s%s" % (suid, " ".join(props), live)}

def apply_allocation(ctx, uid):

    suid = _uid_safe(ctx, uid)
    try:
        rec = _alloc_load(ctx).get(suid)
        if not isinstance(rec, dict):
            return {"ok": True, "msg": "no saved allocation for %s" % suid}
        props = _alloc_props(rec.get("cpu_pct"), rec.get("mem_mb"), rec.get("weight"))
        if not props:
            return {"ok": True, "msg": "empty allocation for %s" % suid}
        units = _scope_units(ctx, suid)
        applied = 0
        for unit in units:
            ok, _ = _systemctl_user("set-property", unit, *props)
            if ok:
                applied += 1
        return {"ok": True, "msg": "re-applied %s to %d/%d scope(s) for %s" %
                (" ".join(props), applied, len(units), suid)}
    except Exception as e:
        return {"ok": False, "msg": "apply_allocation failed: %s" % e}

def set_feature_ban(ctx, uid, feature, until_ts=None, window=None):

    suid = _uid_safe(ctx, uid)
    feat = str(feature or "").strip().lower()
    if not feat:
        return {"ok": False, "msg": "no feature named"}
    try:
        with _STORE_LOCK:
            bans = _bans_load(ctx)
            umap = bans.get(suid, {}) if isinstance(bans.get(suid), dict) else {}
            rec = {"set_ts": time.time()}
            if until_ts is not None:
                try:
                    rec["until"] = float(until_ts)
                except Exception:
                    pass
            if window:
                rec["window"] = str(window)
            umap[feat] = rec
            bans[suid] = umap
            _bans_save(ctx, bans)
    except Exception as e:
        return {"ok": False, "msg": "set_feature_ban failed: %s" % e}
    _prov(ctx, "admin.ban", suid, feat, {"admin": True, "uid": suid, "feature": feat,
                                         "until": until_ts, "window": window})
    span = window or (("until %d" % int(until_ts)) if until_ts else "until cleared")
    return {"ok": True, "msg": "banned '%s' for %s (%s)" % (feat, suid, span)}

def clear_feature_ban(ctx, uid, feature=None):

    suid = _uid_safe(ctx, uid)
    feat = str(feature or "").strip().lower()
    try:
        with _STORE_LOCK:
            bans = _bans_load(ctx)
            umap = bans.get(suid, {})
            if not isinstance(umap, dict) or not umap:
                return {"ok": True, "msg": "no bans for %s" % suid}
            if feat:
                umap.pop(feat, None)
                bans[suid] = umap
            else:
                bans.pop(suid, None)
            _bans_save(ctx, bans)
    except Exception as e:
        return {"ok": False, "msg": "clear_feature_ban failed: %s" % e}
    _prov(ctx, "admin.unban", suid, feat or "*", {"admin": True, "uid": suid, "feature": feat or "*"})
    return {"ok": True, "msg": "cleared ban(s) for %s%s" % (suid, ("/" + feat) if feat else "")}

def is_banned(ctx, uid, feature):

    try:
        suid = _uid_safe(ctx, uid)
        feat = str(feature or "").strip().lower()
        rec = (_bans_load(ctx).get(suid, {}) or {}).get(feat)
        return _ban_active(rec)
    except Exception:
        return False

def list_bans(ctx, uid=None):

    try:
        bans = _bans_load(ctx)
        now = time.time()
        if uid is not None:
            suid = _uid_safe(ctx, uid)
            bans = {suid: bans.get(suid, {})}
        out = {}
        for u, umap in bans.items():
            out[u] = {f: {**(r if isinstance(r, dict) else {}), "active": _ban_active(r, now)}
                      for f, r in (umap or {}).items()}
        return {"ok": True, "bans": out}
    except Exception as e:
        return {"ok": False, "msg": "list_bans failed: %s" % e, "bans": {}}

def get_reserved_cores(ctx):

    nproc = _nproc(ctx)
    reserved = _default_reserved(ctx)
    try:
        cfg = _call(ctx, "load_cfg") or {}
        if isinstance(cfg, dict) and cfg.get("admin_reserved_cores") is not None:
            reserved = int(cfg.get("admin_reserved_cores"))
    except Exception:
        pass
    reserved = min(max(0, reserved), max(0, nproc - 1))
    return {"ok": True, "reserved": reserved, "nproc": nproc, "max_reserved": max(0, nproc - 1),
            "tenant_cap_pct": max(1, nproc - reserved) * 100,

            "cap_enforceable": _cap_enforceable(),
            "default": _default_reserved(ctx)}

def set_reserved_cores(ctx, n):

    nproc = _nproc(ctx)
    if isinstance(n, bool):
        return {"ok": False, "msg": "reserved cores must be an integer, not a boolean"}
    try:
        n = int(n)
    except Exception:
        return {"ok": False, "msg": "reserved cores must be an integer"}

    hi = max(0, nproc - 1)
    if n < 0 or n > hi:
        return {"ok": False, "reserved": None, "nproc": nproc,
                "msg": "reserved cores must be 0..%d on this %d-core box (>=1 core always stays for "
                       "tenants) — refused %d instead of silently clamping it" % (hi, nproc, n)}

    saved = False
    try:
        cfg = _call(ctx, "load_cfg")
        if isinstance(cfg, dict):
            cfg["admin_reserved_cores"] = n
            saver = _g(ctx, "save_cfg")
            if callable(saver):
                saver(cfg)
                saved = True
    except Exception:
        pass

    cap = max(1, nproc - n) * 100
    ok, msg = _systemctl_user("set-property", "pn-batch.slice", "CPUQuota=%d%%" % cap)

    _call(ctx, "ensure_tenant_cap")
    _prov(ctx, "admin.set_reserved_cores", "owner", str(n),
          {"admin": True, "reserved": n, "cap_pct": cap, "applied": ok})

    note = "" if saved else " (config not persisted — save_cfg missing)"
    if not ok:
        note += (" — NOT ENFORCED: %s. The number is stored and will be honoured by whoever applies "
                 "the cap, but no live CPU limit was changed." % msg)
    return {"ok": bool(saved and ok), "reserved": n, "nproc": nproc,
            "tenant_cap_pct": cap, "persisted": bool(saved), "applied": bool(ok),
            "enforced": bool(ok),
            "msg": "reserved %d core(s) for the control plane → tenant cap %d%%%s" % (n, cap, note)}
