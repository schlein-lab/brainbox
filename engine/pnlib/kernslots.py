
from __future__ import annotations
import os

from . import eta as _eta
from . import fleetpick as _fp

NPROC_FALLBACK = 4

_SIM_GUARD = 4096

def _env_int(name, dflt):
    v = os.environ.get(name, "").strip()
    try:
        return int(v) if v else int(dflt)
    except (TypeError, ValueError):
        return int(dflt)

def system_reserve() -> int:

    return max(0, _env_int("PN_KERNE_SYSTEM_RESERVE", 1))

def node_reserve() -> int:

    return max(0, _env_int("PN_KERNE_NODE_RESERVE", 0))

def eta_absage_s() -> float:

    v = os.environ.get("PN_ETA_ABSAGE_S", "").strip()
    try:
        return max(0.0, float(v)) if v else 4.0 * 3600.0
    except (TypeError, ValueError):
        return 4.0 * 3600.0

def buchbare_kerne(nproc, reserved_cores, reserve) -> int:

    try:
        n = int(nproc or 0)
    except (TypeError, ValueError):
        n = 0
    return max(0, n - max(0, int(reserved_cores or 0)) - max(0, int(reserve or 0)))

def nodes_snapshot(workers, box_nproc, box_reserved_cores, *, kind="process",
                   box_arch=None, now=None,
                   max_health_age_s=_fp.DEFAULT_MAX_HEALTH_AGE_S) -> dict:

    snap = {None: {"buchbar": buchbare_kerne(box_nproc, box_reserved_cores, system_reserve()),
                   "nproc": int(box_nproc or 0), "arch": box_arch}}
    nres = node_reserve()
    for nid, rec in (workers or {}).items():
        if not rec or rec.get("state") != "online" or not rec.get("endpoint"):
            continue
        ls = rec.get("last_seen")
        if now is not None and ls is not None and (now - ls) > max_health_age_s:
            continue
        if not _fp._accepting(rec):
            continue
        if not _fp._caps_ok(rec, kind):
            continue
        h = _fp._health(rec)
        nproc = h.get("nproc") or h.get("cpu_count") or NPROC_FALLBACK
        rc = (rec.get("caps") or {}).get("reserved_cores") or 0
        snap[nid] = {"buchbar": buchbare_kerne(nproc, rc, nres),
                     "nproc": int(nproc), "arch": _fp._node_arch(rec)}
    return snap

def eligible_nodes(snapshot, *, pinned=..., portable=False, arch_ok=False,
                   box_arch=None) -> list:

    if pinned is not ...:
        return [pinned] if pinned in snapshot else []
    out = [None]
    if portable:
        for nid, ent in snapshot.items():
            if nid is None:
                continue
            if arch_ok or not box_arch or ent.get("arch") == box_arch:
                out.append(nid)
    return out

def frei_je_node(snapshot, running) -> dict:

    frei = {n: ent["buchbar"] for n, ent in snapshot.items()}
    for r in running or []:
        n = r.get("node")
        if n in frei:
            frei[n] -= int(r.get("kerne") or 0)
    return frei

def projektion(snapshot, eligible, running, ahead, kerne, now=None) -> dict:

    elig = [e for e in (eligible or []) if e in snapshot]
    if not elig:
        return {"status": "nie", "eta_s": None,
                "grund": "kein passender Node (Pinnung/Arch/Drain-Gates)"}
    cap = {e: int(snapshot[e]["buchbar"]) for e in elig}
    maxcap = max(cap.values())
    try:
        k = int(kerne)
    except (TypeError, ValueError):
        return {"status": "nie", "eta_s": None, "grund": "kerne ist keine Zahl"}
    if k < 1:
        return {"status": "nie", "eta_s": None, "grund": "kerne muss >= 1 sein"}
    if k > maxcap:
        return {"status": "nie", "eta_s": None,
                "grund": "braucht %d Kerne, buchbares Maximum der passenden Nodes ist %d"
                         % (k, maxcap)}
    frei = dict(cap)
    events = []
    unbekannt_belegt = False
    for r in running or []:
        n = r.get("node")
        if n not in frei:
            continue
        rk = int(r.get("kerne") or 0)
        frei[n] -= rk
        rest = r.get("rest_s")
        if rest is None:
            unbekannt_belegt = True
        else:
            events.append([max(0.0, float(rest)), n, rk])
    pend = [dict(a) for a in (ahead or [])]
    t = 0.0
    for _ in range(_SIM_GUARD):

        placed = True
        while placed:
            placed = False
            for a in list(pend):
                targets = [e for e in (a.get("eligible") or []) if e in frei]
                if not targets:
                    pend.remove(a)
                    continue
                best = max(targets, key=lambda e: frei[e])
                ak = int(a.get("kerne") or 0)
                if ak <= max(int(snapshot[e]["buchbar"]) for e in targets) and frei[best] >= ak:
                    frei[best] -= ak
                    if a.get("dauer_s") is None:
                        unbekannt_belegt = True
                    else:
                        events.append([t + max(0.0, float(a["dauer_s"])), best, ak])
                    pend.remove(a)
                    placed = True
        if any(frei[e] >= k for e in elig):
            if t <= 0.0:
                return {"status": "sofort", "eta_s": 0.0, "grund": ""}
            return {"status": "eta", "eta_s": t, "grund": ""}
        if not events:
            if unbekannt_belegt or pend:
                return {"status": "unbekannt", "eta_s": None,
                        "grund": "blockierende Buchungen ohne dauer_s (oder Planung bereits "
                                 "überzogen) — Restzeit nicht seriös schätzbar"}

            return {"status": "unbekannt", "eta_s": None, "grund": "keine Freigabe absehbar"}
        events.sort(key=lambda ev: ev[0])
        t2, n2, k2 = events.pop(0)
        t = max(t, float(t2))
        frei[n2] += k2
    return {"status": "unbekannt", "eta_s": None,
            "grund": "Simulationsdeckel erreicht — nicht seriös schätzbar"}

def angebot(snapshot, eligible, running, ahead, wunsch, now=None) -> list:

    elig = [e for e in (eligible or []) if e in snapshot]
    maxcap = max((int(snapshot[e]["buchbar"]) for e in elig), default=0)
    out = []
    for k in range(1, maxcap + 1):
        p = projektion(snapshot, elig, running, ahead, k, now=now)
        ent = {"kerne": k, "status": p["status"], "eta_s": p.get("eta_s")}
        if p["status"] == "sofort":
            ent["human"] = "sofort"
        elif p["status"] == "eta":
            ent["human"] = _eta.human(p["eta_s"])
        else:
            ent["human"] = p["status"]
            if p.get("grund"):
                ent["grund"] = p["grund"]
        out.append(ent)
    try:
        w = int(wunsch)
    except (TypeError, ValueError):
        w = 0
    if w > maxcap:
        out.append({"kerne": w, "status": "nie", "eta_s": None, "human": "nie",
                    "grund": "übersteigt das buchbare Maximum (%d) der passenden Nodes" % maxcap})
    return out

def downgrade_kerne(angebot_liste, wunsch):

    try:
        w = int(wunsch)
    except (TypeError, ValueError):
        return None
    sofort = [a["kerne"] for a in (angebot_liste or [])
              if a.get("status") == "sofort" and 1 <= int(a.get("kerne") or 0) <= w]
    if not sofort or w in sofort:
        return None
    return max(sofort)

def absage_pruefen(proj, schwelle_s=None):

    s = eta_absage_s() if schwelle_s is None else max(0.0, float(schwelle_s))
    if (proj or {}).get("status") == "nie":
        return True, ("strukturell unerfüllbar: %s" % (proj.get("grund") or "passt auf keinen Node"))
    if s > 0 and proj.get("status") == "eta" and float(proj.get("eta_s") or 0.0) > s:
        return True, ("Start-ETA %s liegt über der Absage-Schwelle %s (PN_ETA_ABSAGE_S)"
                      % (_eta.human(proj["eta_s"]), _eta.human(s)))
    return False, ""

def rest_s(started_at, dauer_s, now):

    if not dauer_s or not started_at:
        return None
    r = (float(started_at) + float(dauer_s)) - float(now)
    return r if r > 0 else None
