

import json
import urllib.error
import urllib.request

import portal_placement as _plc

_NODE_HTTP_TIMEOUT = 10

def _node_call(nid, method, path, body=None, timeout=_NODE_HTTP_TIMEOUT):

    endpoint = _plc.node_endpoint(nid)
    token = _plc.node_token(nid)
    if not endpoint:
        return False, ("Node „%s“ hat keinen Node-Agent-Endpoint — nur gepaarte Worker-Nodes sind "
                       "fern-ausfuehrbar; die lokale Box laeuft ohne Node-Agent." % nid)
    if not token:
        return False, "Node „%s“ hat kein Node-Token hinterlegt (Pairing unvollstaendig)." % nid
    url = endpoint.rstrip("/") + path
    headers = {"X-Node-Token": token}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(65536).decode("utf-8", "replace")
        try:
            return True, json.loads(raw)
        except ValueError:
            return True, {"raw": raw[:400]}
    except urllib.error.HTTPError as e:
        try:
            detail = e.read(2048).decode("utf-8", "replace")
        except Exception:
            detail = ""
        return False, "Node-Agent HTTP %s: %s" % (e.code, detail[:300])
    except Exception as e:
        return False, "Node-Agent nicht erreichbar: %s" % (str(e)[:200])

def node_exec(nid, argv, *, mem_mb=None, timeout_s=None, cwd=None, env=None):

    body = {"argv": [str(a) for a in argv]}
    if mem_mb:
        try:
            body["mem_mb"] = int(mem_mb)
        except (TypeError, ValueError):
            pass
    if timeout_s:
        try:
            body["timeout_s"] = float(timeout_s)
        except (TypeError, ValueError):
            pass
    if cwd:
        body["cwd"] = str(cwd)
    if isinstance(env, dict) and env:
        body["env"] = {str(k): str(v) for k, v in env.items()}
    return _node_call(nid, "POST", "/exec", body)

def node_job(nid, job_id):

    return _node_call(nid, "GET", "/jobs/%s" % job_id)

def node_kill(nid, job_id):

    return _node_call(nid, "POST", "/jobs/%s/kill" % job_id, body={})

def _pnd():
    try:
        import pn_governed as g
        return g
    except Exception:
        return None

def _cmd_argv(cmd):

    if isinstance(cmd, list):
        return [str(x) for x in cmd]
    if isinstance(cmd, str):
        try:
            v = json.loads(cmd)
            if isinstance(v, list):
                return [str(x) for x in v]
        except ValueError:
            pass
        return [cmd] if cmd else []
    return []

def _session_sid(row):

    tag = str(row.get("client_tag") or "")
    return tag.split("-")[-1].split("_")[0] if tag else ""

def _resolve_workload(sid, workload):

    if isinstance(workload, dict):
        argv = workload.get("argv")
        if argv is None and workload.get("cmd") is not None:
            argv = _cmd_argv(workload.get("cmd"))
        if argv:
            return {"kind": "job", "resolved": True, "row": None,
                    "argv": [str(a) for a in argv],
                    "mem_mb": workload.get("mem_mb"),
                    "timeout_s": workload.get("timeout_s"),
                    "cwd": workload.get("cwd"), "env": workload.get("env"),
                    "source_job_id": workload.get("source_job_id"),
                    "source_node": workload.get("source_node"),
                    "principal": workload.get("principal"),
                    "label": workload.get("label") or (argv[0] if argv else "job")}

    sid_s = str(sid or "")
    g = _pnd()
    rows = []
    if g is not None and sid_s:
        try:
            r = g.pn_req({"verb": "list", "limit": 400})
            if isinstance(r, dict) and r.get("ok"):
                rows = r.get("jobs") or []
        except Exception:
            rows = []
    match = None
    for j in rows:
        if str(j.get("id")) == sid_s:
            match = j
            break
    if match is None:
        for j in rows:
            if j.get("source") == "session":
                tag = str(j.get("client_tag") or "")
                if sid_s and (_session_sid(j) == sid_s or sid_s in tag):
                    match = j
                    break
    if match is not None:
        if match.get("source") == "session":
            return {"kind": "cell", "resolved": True, "row": match,
                    "argv": None, "mem_mb": match.get("mem_estimate"),
                    "timeout_s": None, "cwd": None, "env": None,
                    "source_job_id": match.get("id"),
                    "source_node": match.get("node"),
                    "principal": match.get("principal"),
                    "label": _session_sid(match) or str(match.get("id"))}
        return {"kind": "job", "resolved": True, "row": match,
                "argv": _cmd_argv(match.get("cmd")),
                "mem_mb": match.get("mem_estimate"), "timeout_s": None,
                "cwd": match.get("cwd"), "env": None,
                "source_job_id": match.get("id"),
                "source_node": match.get("node"),
                "principal": match.get("principal"),
                "label": str(match.get("client_tag") or match.get("id"))}

    return {"kind": "cell", "resolved": False, "row": None, "argv": None,
            "mem_mb": None, "timeout_s": None, "cwd": None, "env": None,
            "source_job_id": None, "source_node": None, "principal": None,
            "label": sid_s or "workload"}

def migrate(sid, target_id, *, dry_run=True, workload=None, prov_log=None):

    def _audit(verb, meta):
        if not prov_log:
            return
        try:
            payload = {"sid": str(sid), "target": str(target_id)}
            payload.update(meta or {})
            prov_log(verb, "owner", json.dumps(payload)[:400], {"wire": "api", "fleet": "migrate"})
        except Exception:
            pass

    tgt = _plc.node_by_id(target_id)
    if not tgt:
        _audit("fleet.migrate.reject", {"reason": "unknown_target"})
        return {"ok": False, "error": "Ziel-Node „%s“ unbekannt." % target_id}

    wl = _resolve_workload(sid, workload)
    is_cell = (wl["kind"] == "cell")

    src_row = wl.get("row")
    if src_row is None and wl.get("source_node"):
        src_row = {"node": wl.get("source_node")}
    src_place = _plc.placement_of(src_row)
    src = _plc.node_by_id(src_place.get("node")) or _plc.local_node()

    cls = _plc.migration_class(src, tgt, is_cell=is_cell)

    base = {"ok": True, "planned": True, "sid": str(sid),
            "workload_kind": wl["kind"], "workload_resolved": bool(wl.get("resolved")),
            "migration_class": cls["migration_class"], "cross_arch": cls["cross_arch"],
            "reversible": cls["reversible"],
            "source": {"node": src.get("id"), "name": src.get("name"),
                       "arch": src.get("arch")},
            "target": {"node": tgt.get("id"), "name": tgt.get("name"),
                       "arch": tgt.get("arch")},
            "plan": cls["plan"], "note": cls["note"]}

    if is_cell and not _plc.can_run_cells(tgt):
        _audit("fleet.migrate.reject", {"reason": "target_no_cells"})
        return dict(base, ok=False, planned=True,
                    error=("Ziel-Node „%s“ kann keine Session-Zellen ausfuehren (braucht KVM + ein "
                           "pn-vmm-Binary fuer seine Arch). Nur governte Sandbox-/Container-Jobs "
                           "koennen dorthin." % tgt.get("name")))
    if not is_cell:
        if not (tgt.get("caps") or {}).get("cell_sandbox", True):
            _audit("fleet.migrate.reject", {"reason": "target_no_sandbox"})
            return dict(base, ok=False, planned=True,
                        error="Ziel-Node „%s“ ist nicht sandbox-faehig." % tgt.get("name"))
        if tgt.get("local") or _plc.node_endpoint(target_id) is None:
            _audit("fleet.migrate.reject", {"reason": "target_no_agent"})
            return dict(base, ok=False, planned=True,
                        error=("Ziel-Node „%s“ hat keinen Node-Agent-Endpoint — der Fern-Restart "
                               "laeuft ueber den Node-Agent eines gepaarten Worker-Nodes (die "
                               "lokale Box hat keinen)." % tgt.get("name")))

    if dry_run:
        base["requires_ceremony"] = bool(is_cell)
        _audit("fleet.migrate.plan", {"kind": wl["kind"], "class": cls["migration_class"],
                                      "cross_arch": cls["cross_arch"],
                                      "resolved": bool(wl.get("resolved"))})
        return base

    if is_cell:
        out = dict(base, executed=False, requires_ceremony=True)
        out["ceremony_reason"] = (
            "Interaktive Owner-Zell-Session: ein echter Umzug ist Stop + Re-Provision + Re-Adopt "
            "einer LAUFENDEN Live-Agent-Zelle — ein eigener, gefaehrlicher Schritt. Er wird NICHT "
            "blind ausgefuehrt; die Ausfuehrung braucht Owner-Bestaetigung (Zeremonie). Geplant, "
            "nicht vollzogen.")
        _audit("fleet.migrate.ceremony_required",
               {"class": cls["migration_class"], "cross_arch": cls["cross_arch"]})
        return out

    argv = wl.get("argv")
    if not argv:
        _audit("fleet.migrate.reject", {"reason": "no_argv"})
        return dict(base, ok=False, executed=False,
                    error=("Kein argv/cmd fuer den governten Job aufloesbar — stateless-restart "
                           "braucht das Startkommando (per `workload.argv` uebergeben oder ueber "
                           "eine aufloesbare pnd-Job-id)."))

    drain = {"attempted": False}
    src_jid = wl.get("source_job_id")
    if src_jid is not None:
        if src.get("local") or src.get("id") == _plc.LOCAL_ID:
            g = _pnd()
            if g is not None:
                drain["attempted"] = True
                try:
                    cr = g.cancel(int(src_jid))
                    drain["ok"] = bool(cr.get("ok")) if isinstance(cr, dict) else False
                    if isinstance(cr, dict) and cr.get("error"):
                        drain["detail"] = str(cr.get("error"))[:200]
                except Exception as e:
                    drain["ok"] = False
                    drain["detail"] = str(e)[:200]
            else:
                drain["note"] = "pnd nicht erreichbar (drain uebersprungen)"
        elif _plc.node_endpoint(src.get("id")):
            drain["attempted"] = True
            ok_k, info_k = node_kill(src.get("id"), str(src_jid))
            drain["ok"] = bool(ok_k)
            drain["detail"] = (info_k if isinstance(info_k, str) else "signalled")
        else:
            drain["note"] = "Quelle nicht fern-adressierbar (drain uebersprungen)"
    else:
        drain["note"] = "keine adressierbare Quell-Job-id (best-effort drain uebersprungen)"

    ok_e, info_e = node_exec(target_id, argv, mem_mb=wl.get("mem_mb"),
                             timeout_s=wl.get("timeout_s"), cwd=wl.get("cwd"),
                             env=wl.get("env"))
    if not ok_e:
        _audit("fleet.migrate.exec_failed", {"error": str(info_e)[:200]})
        return dict(base, ok=False, executed=False, drain=drain,
                    error="Ziel-Dispatch fehlgeschlagen: %s" % info_e)
    job_id = info_e.get("job_id") if isinstance(info_e, dict) else None
    if not job_id:
        _audit("fleet.migrate.exec_failed", {"error": "no_job_id"})
        return dict(base, ok=False, executed=False, drain=drain,
                    error="Node-Agent lieferte keine job_id (%r)." % info_e)

    out = dict(base, executed=True, drain=drain,
               target_job={"node": tgt.get("id"), "job_id": job_id})
    out["note"] = ("stateless-restart ausgefuehrt: Quelle best-effort gedrained, Ziel-Job via "
                   "Node-Agent /exec gestartet. " + cls["note"])
    _audit("fleet.migrate.executed",
           {"kind": "job", "target_job": job_id, "cross_arch": cls["cross_arch"],
            "drained": drain.get("ok"), "argv0": argv[0]})
    return out
