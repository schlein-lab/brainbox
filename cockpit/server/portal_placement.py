

import json
import os
import socket
import threading

_CFG = os.path.expanduser("~/.config/brainbox-portal")
LOCAL_ID = "local"
_LOCK = threading.Lock()
_LOCAL_CACHE = None

def _arch():
    try:
        return os.uname().machine
    except Exception:
        return "unknown"

def _hostname():
    try:
        return os.uname().nodename
    except Exception:
        try:
            return socket.gethostname()
        except Exception:
            return "box"

def _lan_ip():
    try:
        return (json.load(open(os.path.join(_CFG, "config.json"))) or {}).get("lan_ip")
    except Exception:
        return None

def _local_caps():

    caps = {"arch": _arch(), "kvm": False, "cells": False, "cell_ns": False,
            "cell_sandbox": True, "microvm": False}
    try:
        import sys
        for base in (os.environ.get("PNLIB_HOME"),
                     os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "engine"),
                     os.path.expanduser("~/portioneer")):
            if base and os.path.isdir(os.path.join(base, "pnlib")) and base not in sys.path:
                sys.path.insert(0, base)
        from pnlib import cell as _cell
        hc = _cell.host_capabilities() or {}
        caps["kvm"] = bool(hc.get("kvm_usable"))
        caps["cells"] = bool(hc.get("cell_vm_available"))
        caps["cell_ns"] = bool(hc.get("cell_ns_rootless"))
        caps["cell_sandbox"] = bool(hc.get("cell_sandbox_available", True))
    except Exception:
        pass
    return caps

def _arch_family(arch):

    a = str(arch or "").lower()
    if a.startswith(("aarch64", "arm")):
        return "arm"
    if a in ("x86_64", "amd64") or (a.startswith("i") and a.endswith("86")):
        return "x86"
    return a or "unknown"

def _infer_kind(arch, caps):

    caps = caps or {}
    if str(arch or "").startswith(("aarch64", "arm")):
        return "pi"
    if caps.get("cells"):
        return "box"
    return "vm"

def can_run_cells(node) -> bool:

    if not isinstance(node, dict):
        return False
    return bool((node.get("caps") or {}).get("cells"))

def _local_res():

    res = {"nproc": os.cpu_count()}
    try:
        res["load1"] = round(os.getloadavg()[0], 2)
    except OSError:
        pass
    try:
        total = avail = None
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemTotal:"):
                    total = int(ln.split()[1]) // 1024
                elif ln.startswith("MemAvailable:"):
                    avail = int(ln.split()[1]) // 1024
        if total:
            res["mem_total_mb"] = total
            res["mem_avail_mb"] = avail
    except (OSError, ValueError, IndexError):
        pass
    return res

def local_node():

    global _LOCAL_CACHE
    with _LOCK:
        if _LOCAL_CACHE is None:
            caps = _local_caps()
            a = _arch()
            _LOCAL_CACHE = {"id": LOCAL_ID, "name": _hostname() or "Diese Box",
                            "node_kind": _infer_kind(a, caps), "arch": a,
                            "lan_ip": _lan_ip(), "caps": caps,
                            "can_cells": bool(caps.get("cells")),
                            "state": "online", "local": True}
        n = dict(_LOCAL_CACHE)
        n["res"] = _local_res()
        return n

def _worker_registry():

    try:
        import portal_routes_device as prd
        reg = getattr(prd, "_WORKER_REG", None)
        if reg is not None:
            return reg
    except Exception:
        pass
    try:
        import portal_voice_ext as _vext
        path = os.path.join(os.path.expanduser("~"), ".local", "share",
                            "brainbox-portal", "workers.json")
        return _vext.WorkerRegistry(path)
    except Exception:
        return None

def node_endpoint(nid):

    if not nid or nid == LOCAL_ID:
        return None
    reg = _worker_registry()
    if reg is None:
        return None
    try:
        rec = reg.get(nid)
    except Exception:
        return None
    return (rec or {}).get("endpoint")

def node_token(nid):

    if not nid or nid == LOCAL_ID:
        return None
    reg = _worker_registry()
    if reg is None:
        return None
    try:
        rec = reg.get(nid)
    except Exception:
        return None
    return (rec or {}).get("token")

def _worker_nodes():

    reg = _worker_registry()
    try:
        rows = (reg.list() or []) if reg is not None else []
    except Exception:
        rows = []
    out = []
    for r in rows:
        caps = r.get("caps") if isinstance(r.get("caps"), dict) else {}
        facts = r.get("facts") if isinstance(r.get("facts"), dict) else {}
        health = facts.get("health") if isinstance(facts.get("health"), dict) else {}
        arch = caps.get("arch") or facts.get("arch") or health.get("arch") or "unknown"

        res = {k: health.get(k) for k in ("nproc", "mem_total_mb", "mem_avail_mb",
                                          "mem_budget_mb", "mem_cgroup_max_mb",
                                          "mem_cgroup_used_mb",
                                          "load1", "disk_free_mb", "running_cells")
               if health.get(k) is not None}
        out.append({"id": r.get("id"), "name": r.get("name") or r.get("id"),
                    "node_kind": r.get("node_kind") or _infer_kind(arch, caps),
                    "arch": arch, "lan_ip": None, "caps": caps,
                    "can_cells": bool(caps.get("cells")),
                    "res": res,

                    "draining": bool(r.get("draining") or health.get("draining")),
                    "state": r.get("state") or "offline", "local": False})
    return out

def nodes():

    return [local_node()] + _worker_nodes()

_PICK_W_MEM = 1.0
_PICK_W_LOAD = 0.5
_PICK_W_CELLS = 0.3
_PICK_MEM_HEADROOM_MB = 1024

def _placement_enabled():

    return str(os.environ.get("PN_FLEET_PLACEMENT", "1")).strip().lower() not in ("0", "false", "no")

def _box_reserve_mb():

    try:
        return max(0, int(os.environ.get("PN_BOX_INTERACTIVE_RESERVE_MB", "4096")))
    except (TypeError, ValueError):
        return 4096

def _mem_nutzbar(res):

    b = (res or {}).get("mem_budget_mb")
    if b is not None:
        try:
            return float(b)
        except (TypeError, ValueError):
            pass
    try:
        return float((res or {}).get("mem_avail_mb") or 0)
    except (TypeError, ValueError):
        return 0.0

def _score_node(node, mem_avail_override=None):
    res = node.get("res") or {}
    mt = float(res.get("mem_total_mb") or 0) or 1.0
    ma = _mem_nutzbar(res) if mem_avail_override is None else float(mem_avail_override)
    nproc = float(res.get("nproc") or 1) or 1.0
    load1 = float(res.get("load1") or 0)
    rc = float(res.get("running_cells") or 0)
    return (_PICK_W_MEM * (ma / mt)
            - _PICK_W_LOAD * (load1 / nproc)
            - _PICK_W_CELLS * (rc / nproc))

def _node_cell_ready(node):

    caps = node.get("caps") or {}
    if not caps.get("cells"):
        return False
    if node.get("local"):
        return True
    return bool(caps.get("cell_base_staged"))

_CAP_DISK_PER_CELL_MB = int(os.environ.get("PN_CAP_DISK_PER_CELL_MB", 6 * 1024))
_CAP_MEM_PER_CELL_MB = int(os.environ.get("PN_CAP_MEM_PER_CELL_MB", 2 * 1024))

_CAP_MAX = int(os.environ.get("PN_CAP_MAX", "8"))

def _node_cell_cap(node):

    nid = str(node.get("id") or "").strip()
    for schl in ("PN_NODE_CELL_CAP_" + nid.upper().replace("-", "_"), "PN_NODE_CELL_CAP"):
        roh = os.environ.get(schl)
        if roh:
            try:
                return max(1, int(roh))
            except ValueError:
                pass
    res = node.get("res") or {}
    mt, df = res.get("mem_total_mb"), res.get("disk_free_mb")
    grenzen = []
    try:
        if mt:
            grenzen.append(int(float(mt) // _CAP_MEM_PER_CELL_MB))
    except (TypeError, ValueError):
        pass
    try:
        if df:
            grenzen.append(int(float(df) // _CAP_DISK_PER_CELL_MB))
    except (TypeError, ValueError):
        pass
    if not grenzen:
        return None
    return max(1, min(_CAP_MAX, min(grenzen)))

def _pick_node_core(roster, mem_needed_mb, arch_pref=None, box_reserve_mb=0):

    fam_pref = _arch_family(arch_pref) if arch_pref else None
    try:
        need = float(mem_needed_mb or 0)
    except (TypeError, ValueError):
        need = 0.0
    try:
        reserve = max(0.0, float(box_reserve_mb or 0))
    except (TypeError, ValueError):
        reserve = 0.0
    best_id = None
    best_score = None
    for n in roster:
        if n.get("state") != "online":
            continue
        if n.get("draining"):
            continue
        if not _node_cell_ready(n):
            continue
        if fam_pref and _arch_family(n.get("arch")) != fam_pref:
            continue
        res = n.get("res") or {}

        ma = res.get("mem_budget_mb")
        if ma is None:
            ma = res.get("mem_avail_mb")
        if ma is None:
            continue
        is_local = bool(n.get("local"))
        ma_eff = (float(ma) - reserve) if is_local else float(ma)
        if ma_eff - _PICK_MEM_HEADROOM_MB < need:
            continue
        deckel = _node_cell_cap(n)
        if deckel is not None:
            try:
                if float(res.get("running_cells") or 0) >= deckel:
                    continue
            except (TypeError, ValueError):
                pass
        s = _score_node(n, mem_avail_override=(ma_eff if is_local else None))
        if best_score is None or s > best_score:
            best_score = s
            best_id = n.get("id")
    return best_id

def _local_running_cells():

    try:
        import pn_cell_session
        return len(pn_cell_session.get_manager().list_live())
    except Exception:
        return 0

def _placement_roster():

    roster = []
    for n in nodes():
        n = dict(n)
        if n.get("local"):
            res = dict(n.get("res") or {})
            res.setdefault("running_cells", _local_running_cells())
            n["res"] = res
            n.setdefault("draining", False)
        else:
            n.setdefault("draining", bool(n.get("draining")))
        roster.append(n)
    return roster

def pick_node(mem_needed_mb, arch_pref=None):

    if not _placement_enabled():
        return LOCAL_ID
    return _pick_node_core(_placement_roster(), mem_needed_mb, arch_pref,
                           box_reserve_mb=_box_reserve_mb())

def _selftest_pick_node():

    def node(nid, arch, mem_total, mem_avail, load1, nproc, rc, cells=True, staged=True,
             state="online", draining=False, local=False):
        return {"id": nid, "arch": arch, "state": state, "draining": draining, "local": local,
                "caps": {"cells": cells, "cell_base_staged": staged},
                "res": {"mem_total_mb": mem_total, "mem_avail_mb": mem_avail,
                        "load1": load1, "nproc": nproc, "running_cells": rc}}

    cases = []

    cases.append(("even-util bevorzugt die leerere Box", 1024, None, [
        node("local", "x86_64", 16000, 4000, 3.0, 8, 4, local=True),
        node("remote1", "x86_64", 6000, 3000, 0.2, 4, 0),
    ], "remote1"))

    cases.append(("RAM-Gate schliesst zu vollen Node aus", 2500, None, [
        node("local", "x86_64", 16000, 8000, 1.0, 8, 1, local=True),
        node("remote1", "x86_64", 6000, 3000, 0.1, 4, 0),
    ], "local"))

    cases.append(("Arch-Filter: aarch64 nur auf arm-Nodes", 1024, "aarch64", [
        node("local", "x86_64", 16000, 12000, 0.1, 8, 0, local=True),
        node("pi1", "aarch64", 8000, 6000, 0.5, 4, 1),
    ], "pi1"))

    cases.append(("draining/ungestaged/offline uebersprungen -> Fallback local", 1024, None, [
        node("local", "x86_64", 16000, 9000, 0.5, 8, 2, local=True),
        node("drain", "x86_64", 6000, 5000, 0.0, 4, 0, draining=True),
        node("nobase", "x86_64", 6000, 5000, 0.0, 4, 0, staged=False),
        node("down", "x86_64", 6000, 5000, 0.0, 4, 0, state="offline"),
    ], "local"))

    cases.append(("kein Kandidat -> None (Kern)", 99000, None, [
        node("local", "x86_64", 16000, 2000, 0.5, 8, 0, local=True),
        node("remote1", "x86_64", 6000, 3000, 0.1, 4, 0),
    ], None))

    cases.append(("Box-Cap: Reserve laesst gleich-freien Remote gewinnen", 1024, None, [
        node("local", "x86_64", 16000, 8000, 0.1, 8, 0, local=True),
        node("remote1", "x86_64", 16000, 8000, 0.1, 8, 0),
    ], "remote1", 4096))

    cases.append(("Box-Cap: Box gewinnt wenn klar leerer trotz Reserve", 500, None, [
        node("local", "x86_64", 16000, 14000, 0.1, 8, 0, local=True),
        node("remote1", "x86_64", 16000, 2000, 0.1, 8, 0),
    ], "local", 4096))

    cases.append(("Box-Cap: Reserve schliesst knappe Box aus dem Gate", 4000, None, [
        node("local", "x86_64", 16000, 6000, 0.1, 8, 0, local=True),
    ], None, 4096))

    ok = 0
    for case in cases:
        name, need, arch, roster, expect = case[:5]
        reserve = case[5] if len(case) > 5 else 0
        got = _pick_node_core(roster, need, arch, box_reserve_mb=reserve)
        good = (got == expect)
        ok += good
        print("[%s] %-52s need=%dMB arch=%s reserve=%d -> %s (erwartet %s)"
              % ("PASS" if good else "FAIL", name, need, arch or "-", reserve, got, expect))

    _prev = os.environ.get("PN_FLEET_PLACEMENT")
    try:
        os.environ["PN_FLEET_PLACEMENT"] = "0"
        ks = pick_node(1024)
        ks_good = (ks == LOCAL_ID) and (not _placement_enabled())
    finally:
        if _prev is None:
            os.environ.pop("PN_FLEET_PLACEMENT", None)
        else:
            os.environ["PN_FLEET_PLACEMENT"] = _prev
    ok += ks_good
    total = len(cases) + 1
    print("[%s] %-52s reserve=- -> %s (erwartet %s, Kill-Switch)"
          % ("PASS" if ks_good else "FAIL", "Kill-Switch erzwingt local", ks, LOCAL_ID))
    print("pick_node self-test: %d/%d PASS" % (ok, total))
    return ok == total

if __name__ == "__main__":
    import sys as _sys
    _sys.exit(0 if _selftest_pick_node() else 1)

def node_by_id(nid):
    for n in nodes():
        if n.get("id") == nid:
            return n
    return None

def placement_of(row=None):

    ln = local_node()
    nid = row.get("node") or row.get("node_id") if isinstance(row, dict) else None
    if nid and nid != LOCAL_ID:
        n = node_by_id(nid)
        if n:
            return {"node": n["id"], "node_kind": n["node_kind"], "node_name": n["name"]}
    return {"node": ln["id"], "node_kind": ln["node_kind"], "node_name": ln["name"]}

def stamp(row):

    if isinstance(row, dict):
        row.update(placement_of(row))
        row.setdefault("migration_class", "stateless-restart")
    return row

def migration_class(src, tgt, is_cell=True):

    src_arch = (src or {}).get("arch")
    tgt_arch = (tgt or {}).get("arch")
    src_fam = _arch_family(src_arch)
    tgt_fam = _arch_family(tgt_arch)
    cross_arch = bool(src_fam != "unknown" and tgt_fam != "unknown" and src_fam != tgt_fam)
    tgt_name = (tgt or {}).get("name") or (tgt or {}).get("id") or "Ziel"

    live_possible = False
    if is_cell:

        carry = [
            {"volume": "work", "role": "vdc -> /work", "arch_agnostic": True, "travels": True,
             "note": "Client-Datenvolumen (Dateien/Ergebnisse) — reist IMMER mit."},
            {"volume": "delta", "role": "vdb (/root-Overlay)", "arch_agnostic": False,
             "travels": (not cross_arch),
             "note": ("gleiche Arch: darf mitreisen (Agent-State/.claude)"
                      if not cross_arch else
                      "Cross-Arch: reist NICHT — arch-Binaere; wird auf dem Ziel frisch gebaut.")},
            {"volume": "base", "role": "vda (RO rootfs/kernel)", "arch_agnostic": False,
             "travels": False,
             "note": "Arch-spezifische Basis — wird auf dem Ziel FUER DESSEN Arch neu bereitgestellt."},
        ]
        if cross_arch:
            mclass = "contents-preserving-restart"
            stateful_possible = False
            plan = ["Zelle anhalten (kein Live-Transfer moeglich)",
                    "arch-agnostisches Datenvolumen (/work) auf „%s“ uebertragen" % tgt_name,
                    "FRISCHE Ziel-Arch-Basis + uebertragenes Datenvolumen bereitstellen",
                    "Sitzung wieder adoptieren"]
            note = ("Cross-Arch-Zell-Umzug (%s → %s): CONTENTS-PRESERVING-RESTART — nur das arch-"
                    "agnostische Datenvolumen (vdc -> /work) reist mit; Gastkernel/rootfs (base) und "
                    "der /root-Delta unterscheiden sich je Architektur und werden auf dem Ziel FUER "
                    "DESSEN Arch frisch gebaut. Es reist KEIN Binaerzustand, kein Live-/stateful-"
                    "Transfer (heute UND grundsaetzlich unmoeglich) — die INHALTE bleiben erhalten."
                    % (src_arch, tgt_arch))
        else:
            mclass = "stateless-restart"
            stateful_possible = True
            plan = ["Zelle anhalten", "Datenvolumen (/work) + /root-Delta auf „%s“ uebertragen" % tgt_name,
                    "auf „%s“ mit gleicher Arch-Basis neu bereitstellen" % tgt_name,
                    "Sitzung wieder adoptieren"]
            note = ("Kalter stateless-restart der Zelle (gleiche Architektur %s): Datenvolumen UND "
                    "/root-Delta koennen mitreisen. Kein verlustfreier Live-Transfer — den gibt es "
                    "fuer pn-vmm-Zellen nicht." % (tgt_arch or "?"))
    else:
        carry = []
        mclass = "stateless-restart"
        stateful_possible = False
        plan = ["Quell-Job best-effort stoppen (drain)",
                "Kommando auf „%s“ via Node-Agent /exec neu starten" % tgt_name,
                "Job-Handle zurueckgeben"]
        if cross_arch:
            note = ("Governter Job, Cross-Arch (%s → %s): stateless-restart des Kommandos auf dem "
                    "Ziel. Nur sinnvoll fuer arch-portable Kommandos (Interpreter/Skript); ein "
                    "arch-spezifisches Binary muss auf dem Ziel fuer DESSEN Arch vorliegen."
                    % (src_arch, tgt_arch))
        else:
            note = ("Governter Job, stateless-restart (gleiche Architektur %s): Quelle best-effort "
                    "stoppen, Kommando auf dem Ziel via Node-Agent /exec neu starten."
                    % (tgt_arch or "?"))
    return {"migration_class": mclass, "cross_arch": cross_arch,
            "reversible": True, "source_arch": src_arch, "target_arch": tgt_arch,
            "stateful_possible": stateful_possible, "live_possible": live_possible,
            "carry": carry, "plan": plan, "note": note}

def migrate_plan(sid, target_id):

    try:
        import portal_migrate as _mig
        return _mig.migrate(sid, target_id, dry_run=True)
    except Exception:
        return _legacy_migrate_plan(sid, target_id)

def _legacy_migrate_plan(sid, target_id):

    tgt = node_by_id(target_id)
    if not tgt:
        return {"ok": False, "error": "Ziel-Node unbekannt"}
    if not can_run_cells(tgt):
        return {"ok": False, "planned": True, "target": tgt.get("name"),
                "error": "Ziel-Node „%s“ kann keine Session-Zellen ausfuehren "
                         "(braucht KVM + ein pn-vmm-Binary fuer seine Arch: x86-Box/VM oder "
                         "aarch64-Pi mit arm-pn-vmm). Nur governte Sandbox-/Container-Jobs "
                         "koennen dorthin." % tgt.get("name")}

    src = local_node()
    src_arch = src.get("arch")
    tgt_arch = tgt.get("arch")
    src_fam = _arch_family(src_arch)
    tgt_fam = _arch_family(tgt_arch)
    cross_arch = bool(src_fam != "unknown" and tgt_fam != "unknown" and src_fam != tgt_fam)
    plan = ["Zelle anhalten", "auf „%s“ neu bereitstellen" % tgt.get("name"),
            "Sitzung wieder adoptieren"]
    if cross_arch:
        note = ("Cross-Arch-Umzug (%s → %s): NUR kalter stateless-restart moeglich — Gastkernel "
                "und rootfs unterscheiden sich je Architektur, es reist KEIN Binaerzustand mit. "
                "Ein Live-/stateful-Transfer ist ausgeschlossen (heute UND grundsaetzlich). "
                "Ausfuehrung ab Phase 3." % (src_arch, tgt_arch))
    else:
        note = ("Kalt-Umzug (Stop → Neustart auf Ziel-Node, gleiche Architektur %s). Kein "
                "verlustfreier Live-Transfer — den gibt es fuer Zellen heute nicht. "
                "Ausfuehrung ab Phase 3." % (tgt_arch or "?"))
    return {"ok": True, "planned": True, "kind": "cold", "migration_class": "stateless-restart",
            "cross_arch": cross_arch, "source_arch": src_arch, "target_arch": tgt_arch,
            "target": tgt.get("name"), "plan": plan, "note": note}
