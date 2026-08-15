

def _title_of(uid, sid):
    try:
        import portal_jobs_persist as pjp
        r = pjp._session_store(uid).get(sid) or {}
        return r.get("title") or sid
    except Exception:
        return sid

def _title_map(uid):

    try:
        import portal_jobs_persist as pjp
        return {str(r.get("id")): (r.get("title") or str(r.get("id")))
                for r in (pjp._session_store(uid).list() or []) if r.get("id")}
    except Exception:
        return {}

def _health_of(wd, uid, sid):
    try:
        h = wd.health(uid, sid)
        return h or {"state": "ok"}
    except Exception:
        return {"state": "unknown"}

def _is_orchestrator(uid, sid):
    try:
        import portal_session_svc as svc
        caps = ((svc._sess_policy_get(uid, sid) or {}).get("caps") or {})
        return caps.get("orchestrate") == "allow"
    except Exception:
        return False

def _err_sig(e):

    import re
    s = str(e or "").strip() or "(kein Fehlertext)"
    s = re.sub(r"[0-9a-fA-F]{8,}", "#", s)
    s = re.sub(r"\d+", "#", s)
    return " ".join(s.split())[:90]

_WIN_FAIL_H = 24
_WIN_DONE_H = 4
_WIN_PEND_H = 48
_MAX_CHILDREN = 8
_MAX_ERR_GROUPS = 3

def fleet_status(ctx=None, compact=False):

    import pn_session_watchdog as wd
    ctx = ctx or getattr(wd, "_CTX", None)
    out = {"ok": True, "sessions": [], "trees": [], "heartbeat": None}
    try:
        import pn_watchdog_deadman as dm
        out["heartbeat"] = dm.heartbeat()
    except Exception:
        pass

    live = {}
    try:
        mgr = wd._mgr(ctx) if ctx else None
        if mgr:
            for key, cell in list(getattr(mgr, "_cells", {}).items()):
                live[key] = cell
    except Exception:
        pass

    meta = {}
    try:
        import portal_metasessions as pm

        meta = (getattr(pm, "_meta_load_ro", None) or pm._meta_load)() or {}
    except Exception:
        pass

    try:
        import pn_cell_session as _cs
        rn = _cs.node_status_info()
        if rn:
            out["remote_nodes"] = rn
    except Exception:
        pass

    child_sids = set()
    for osid, ms in meta.items():
        for t in (ms.get("tasks") or []):
            if t.get("sid"):
                child_sids.add(t.get("sid"))

    _titel_maps = {}

    def _titel(uid, sid):
        m = _titel_maps.get(uid)
        if m is None:
            m = _title_map(uid)
            _titel_maps[uid] = m
        return m.get(str(sid)) or sid

    for (uid, sid), cell in live.items():
        try:
            alive = bool(cell and cell.alive())
        except Exception:
            alive = False
        out["sessions"].append({
            "sid": sid, "uid": uid, "title": _titel(uid, sid),
            "alive": alive, "health": _health_of(wd, uid, sid),
            "orchestrator": (sid in meta) or _is_orchestrator(uid, sid),
            "is_child": sid in child_sids,
        })

    import time
    now = time.time()
    hidden = {"n": 0}
    for osid, ms in meta.items():
        uid = ms.get("owner")
        if not uid:
            continue
        children = []
        for t in (ms.get("tasks") or []):
            csid = t.get("sid")
            children.append({
                "tid": t.get("tid"), "sid": csid, "state": t.get("state"),
                "title": (_titel(uid, csid) if csid else (t.get("prompt") or "")[:48]),
                "health": (_health_of(wd, uid, csid) if csid else {"state": "n/a"}),
                "error": t.get("error"),
                "result_preview": (t.get("result") or "")[:120],
                "started": t.get("started"), "ended": t.get("ended"),
            })
        states = [c["state"] for c in children]
        n = len(children)
        nerr = states.count("error")
        nrun = states.count("running") + states.count("starting")
        npend = states.count("pending"); ndone = states.count("done")
        orch_h = _health_of(wd, uid, osid).get("state", "ok")
        if orch_h == "failed" or (n and nerr / float(n) > 0.5):
            agg = "failed"
        elif nerr or orch_h == "restarting":
            agg = "degraded"
        elif nrun or npend:
            agg = "running"
        elif n and ndone == n:
            agg = "done"
        else:
            agg = "idle"
        last = max([ms.get("created") or 0]
                   + [c.get("started") or 0 for c in children]
                   + [c.get("ended") or 0 for c in children])
        tree = {
            "orchestrator": osid, "uid": uid, "title": ms.get("title") or _titel(uid, osid),
            "orch_health": orch_h, "aggregate": agg, "last_activity": last,
            "counts": {"children": n, "done": ndone, "running": nrun,
                       "pending": npend, "error": nerr},
            "children": children,
        }
        if compact:
            fresh_h = ((now - last) / 3600.0) if last else 1e9
            keep = (nrun > 0
                    or (npend and ms.get("state") == "running" and fresh_h < _WIN_PEND_H)
                    or (agg in ("failed", "degraded") and fresh_h < _WIN_FAIL_H)
                    or (agg == "done" and fresh_h < _WIN_DONE_H))
            if not keep:
                hidden["n"] += 1
                hidden[agg] = hidden.get(agg, 0) + 1
                continue
            groups = {}
            fresh_err = []
            for c in children:
                if c.get("state") != "error":
                    continue
                g = groups.setdefault(_err_sig(c.get("error")),
                                      {"sig": _err_sig(c.get("error")), "count": 0, "last": 0})
                g["count"] += 1
                g["last"] = max(g["last"], c.get("ended") or 0)
                if (now - (c.get("ended") or 0)) < _WIN_FAIL_H * 3600:
                    fresh_err.append(c)
            fresh_err.sort(key=lambda c: -(c.get("ended") or 0))
            live = [c for c in children if c.get("state") in ("running", "starting", "pending")]
            tree["children"] = (live + fresh_err)[:_MAX_CHILDREN]
            tree["more_children"] = n - len(tree["children"])
            gs = sorted(groups.values(), key=lambda g: -g["count"])
            tree["error_groups"] = gs[:_MAX_ERR_GROUPS]
            tree["more_error_groups"] = max(0, len(gs) - _MAX_ERR_GROUPS)
        out["trees"].append(tree)
    if compact:

        rank = {"failed": 0, "degraded": 1, "running": 2, "idle": 3, "done": 4}
        out["trees"].sort(key=lambda t: (rank.get(t["aggregate"], 3),
                                         -(t.get("last_activity") or 0)))
        out["compact"] = True
        out["hidden_trees"] = hidden
    return out

if __name__ == "__main__":
    import json
    print(json.dumps(fleet_status(), ensure_ascii=False, indent=1)[:2000])
