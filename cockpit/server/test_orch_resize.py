#!/usr/bin/env python3

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="orchresize")
os.environ["PN_CELL_MEM_MB"] = "1536"

import portal_metasessions as PM

FAILED = []

def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else " " + str(extra)))
    if not cond:
        FAILED.append(name)

PM._METASESS_FILE = os.path.join(TMP, "metasessions.json")
PM._RESIZE_INTENTS = os.path.join(TMP, "resize-approvals.json")

PROV = {}
PM._sessprov_get = lambda uid, sid: dict(PROV.get((uid, sid), {}))
PM._sessprov_set = lambda uid, sid, patch: PROV.setdefault((uid, sid), {}).update(patch)
PM._prov_log = lambda *a, **k: None
PM._meta_work_delta = lambda sid: True
STOPPED = []
PM._meta_cell_stop = lambda owner, sid, erase=False: STOPPED.append(sid)

ASKED = []
ANSWERS = {}

class _MF:
    @staticmethod
    def ask_owner(uid, sid, question, options=None, urgent=False, kind=None):
        aid = "q%03d" % (len(ASKED) + 1)
        ASKED.append({"aid": aid, "uid": uid, "sid": sid, "q": question, "kind": kind})
        return {"ok": True, "aid": aid, "kind": kind, "state": "pending"}

    @staticmethod
    def ask_owner_result(uid, sid, aid):
        a = ANSWERS.get(aid)
        if a is None:
            return {"ok": True, "aid": aid, "state": "pending", "answer": None}
        return {"ok": True, "aid": aid, "state": "answered", "answer": a}

sys.modules["portal_metafeatures"] = _MF

def fresh(nkids=2, state="running"):
    PROV.clear(); ASKED.clear(); ANSWERS.clear(); STOPPED[:] = []
    for f in (PM._METASESS_FILE, PM._RESIZE_INTENTS):
        try:
            os.unlink(f)
        except OSError:
            pass
    tasks = [{"tid": "t%d" % i, "sid": "kid%d" % i, "state": state} for i in range(1, nkids + 1)]
    d = {"orch1": {"owner": "owner", "id": "m1", "tasks": tasks, "state": "running"}}
    with open(PM._METASESS_FILE, "w") as f:
        json.dump(d, f)

def R(**kw):
    kw.setdefault("owner", "owner"); kw.setdefault("sid", "orch1")
    return PM.orch_resize(kw.pop("owner"), kw.pop("sid"), kw.pop("tid", None),
                          kw.pop("disk_gb", None), kw.pop("mem_mb", None),
                          kw.pop("reason", "Test"), kw.pop("approval", None),
                          kw.pop("restart", None))

try:

    print("== Zugang")
    fresh()
    res, spoken, ok = PM.orch_resize("owner", "gibtesnicht", "t1", 2, None, "x", None, None)
    check("fremde/unbekannte Session wird abgewiesen", not ok and res["error"] == "no_orchestrator", res)
    res, spoken, ok = PM.orch_resize("jemand-anders", "orch1", "t1", 2, None, "x", None, None)
    check("anderer Besitzer wird abgewiesen", not ok, res)
    res, spoken, ok = R(tid="t99", disk_gb=2)
    check("unbekannte tid wird abgewiesen", not ok and res["error"] == "unknown_tid", res)
    res, spoken, ok = R(tid="t1")
    check("ohne Wunsch passiert nichts", not ok and res["error"] == "nothing_requested", res)

    print("== Disk unter dem Deckel (5 GB frei)")
    fresh()
    res, spoken, ok = R(tid="t1", disk_gb=5)
    check("5 GB gehen ohne Owner", ok and res["changed"]["disk_mb"] == 5120, res)
    check("beim Kind gelandet", PROV[("owner", "kid1")]["disk_mb"] == 5120)
    check("Kind wurde neu gestartet", res["restarted"] == ["t1"] and STOPPED == ["kid1"], (res, STOPPED))
    check("kein Owner gefragt", not ASKED, ASKED)

    fresh()
    res, spoken, ok = R(tid="t1", disk_gb=2, restart=False)
    check("restart:false laesst das Kind laufen", ok and not res["restarted"] and not STOPPED, (res, STOPPED))

    fresh()
    PROV[("owner", "kid1")] = {"disk_mb": 4096}
    res, spoken, ok = R(tid="t1", disk_gb=1)
    check("Verkleinern ist ein no-op, kein Fehler",
          ok and "disk_mb" not in res["changed"] and PROV[("owner", "kid1")]["disk_mb"] == 4096, res)

    print("== Disk ueber dem Deckel -> Owner")
    fresh()
    res, spoken, ok = R(tid="t1", disk_gb=9)
    check("9 GB fragen den Owner", ok and res.get("need_owner") and res["aid"], res)
    check("Freigabe-Karte ist eine GENEHMIGUNG (2FA-Klasse)",
          ASKED and ASKED[0]["kind"] == "approval", ASKED)
    check("nichts wurde still angewandt", "disk_mb" not in PROV.get(("owner", "kid1"), {}),
          PROV.get(("owner", "kid1")))
    check("die Frage nennt Ziel und Zahlen",
          "9216" in ASKED[0]["q"] and "t1" in ASKED[0]["q"], ASKED[0]["q"])

    print("== RAM: Grundausstattung + 2 GB")
    fresh()
    res, spoken, ok = R(tid="t1", mem_mb=3584)
    check("1536+2048=3584 MB gehen allein", ok and res["changed"]["mem_mb"] == 3584, res)

    fresh()
    res, spoken, ok = R(tid="t1", mem_mb=3585)
    check("ein MB darueber fragt den Owner", ok and res.get("need_owner"), res)

    fresh()
    PROV[("owner", "kid1")] = {"mem_mb": 3584}
    res, spoken, ok = R(tid="t1", mem_mb=5632)
    check("ein schon angehobenes Kind kann NICHT nochmal +2 GB bekommen",
          ok and res.get("need_owner") and PROV[("owner", "kid1")]["mem_mb"] == 3584, res)

    print("== Owner-Freigabe einloesen")
    fresh()
    res, _, _ = R(tid="t1", mem_mb=8192)
    aid = res["aid"]
    r2, _, ok2 = R(tid="t1", mem_mb=8192, approval=aid)
    check("noch nicht entschieden -> abgelehnt", not ok2 and r2["error"] == "approval_invalid", r2)

    ANSWERS[aid] = "ABGELEHNT"
    r3, _, ok3 = R(tid="t1", mem_mb=8192, approval=aid)
    check("abgelehnt -> abgelehnt", not ok3, r3)

    ANSWERS[aid] = "GENEHMIGT (2FA-verifiziert)"
    r4, _, ok4 = R(tid="t1", mem_mb=8192, approval=aid)
    check("genehmigt -> wird angewandt", ok4 and PROV[("owner", "kid1")]["mem_mb"] == 8192, r4)

    r5, _, ok5 = R(tid="t1", mem_mb=8192, approval=aid)
    check("dieselbe Freigabe ein ZWEITES Mal wird abgelehnt", not ok5, r5)

    fresh()
    res, _, _ = R(tid="t1", mem_mb=4096)
    aid = res["aid"]
    ANSWERS[aid] = "GENEHMIGT (2FA-verifiziert)"
    r6, _, ok6 = R(tid="t1", mem_mb=12288, approval=aid)
    check("die genehmigten Zahlen gewinnen gegen den neuen Wunsch",
          ok6 and PROV[("owner", "kid1")]["mem_mb"] == 4096, PROV.get(("owner", "kid1")))

    print("== Geltungsbereiche")
    fresh(nkids=3)
    res, spoken, ok = R(tid="*", disk_gb=4)
    check("'*' trifft alle laufenden Kinder",
          ok and all(PROV[("owner", "kid%d" % i)]["disk_mb"] == 4096 for i in (1, 2, 3)), res)
    check("'*' setzt auch die Vorlage",
          PROV[("owner", "orch1")]["worker_disk_mb"] == 4096, PROV.get(("owner", "orch1")))
    check("'*' startet alle neu", sorted(res["restarted"]) == ["t1", "t2", "t3"], res)

    fresh(nkids=2)
    res, spoken, ok = R(tid="new", disk_gb=4)
    check("'new' aendert NUR die Vorlage",
          ok and PROV[("owner", "orch1")]["worker_disk_mb"] == 4096
          and ("owner", "kid1") not in PROV, PROV)
    check("'new' startet nichts neu", not res["restarted"] and not STOPPED, (res, STOPPED))

    fresh()
    res, spoken, ok = R(tid="self", mem_mb=3000)
    check("'self' trifft die eigene Zelle", ok and PROV[("owner", "orch1")]["mem_mb"] == 3000, res)
    check("die eigene Zelle wird NIE neu gestartet", not STOPPED and not res["restarted"], STOPPED)
    check("...und sagt das auch", any("naechsten regulaeren Start" in n for n in res["notes"]), res["notes"])

    fresh(nkids=2)
    PROV[("owner", "kid2")] = {"mem_mb": 3584}
    res, spoken, ok = R(tid="*", mem_mb=5000)
    check("Sammelaufruf misst am groessten Ist-Wert -> Owner",
          ok and res.get("need_owner"), res)

    print("== Wiederaufnahme")
    fresh()
    R(tid="t1", disk_gb=3)
    d = json.load(open(PM._METASESS_FILE))
    t = [x for x in d["orch1"]["tasks"] if x["tid"] == "t1"][0]
    check("Kind steht wieder auf 'pending'", t["state"] == "pending", t)
    check("...mit resume_sid, damit der Arbeitsstand weiterlebt", t["resume_sid"] == "kid1", t)
    check("...und sofort wieder einplanbar", t.get("retry_after") == 0, t)
    check("das Wiederaufnahme-Budget wurde NICHT belastet", not t.get("resumes"), t)

    fresh(state="done")
    res, spoken, ok = R(tid="t1", disk_gb=3)
    check("ein fertiges Kind wird nicht neu gestartet", ok and not res["restarted"], res)

    print("== Vorlage -> neues Kind")
    src = open(os.path.join(HERE, "portal_metasessions.py")).read()

    for fn in ("_orch_template", "_meta_ensure_for_session"):
        body = src.split("def " + fn, 1)[1].split("\ndef ", 1)[0]
        check("%s reicht worker_disk_mb weiter" % fn, "worker_disk_mb" in body)
        check("%s reicht worker_mem_mb weiter" % fn, "worker_mem_mb" in body)
    spawn = src.split("def _meta_spawn_worker", 1)[1].split("\ndef ", 1)[0]
    check("_meta_spawn_worker stempelt disk_mb/mem_mb aufs Kind",
          '"disk_mb": int(_wequip["disk"])' in spawn and '"mem_mb": int(_wequip["mem"])' in spawn)
    check("...und liest die Ausstattung LIVE nach (gespeicherte Vorlage kann veraltet sein)",
          'worker_disk_mb' in spawn and '_sessprov_get(owner, _lead)' in spawn)

    print("== session_restart (eigenstaendig)")

    def RS(**kw):
        return PM.orch_restart("owner", kw.pop("sid", "orch1"), kw.pop("tid", None),
                               kw.pop("reason", "Test"))

    fresh(nkids=2)
    res, spoken, ok = PM.orch_restart("owner", "gibtesnicht", "t1", "x")
    check("fremde Session wird abgewiesen", not ok and res["error"] == "no_children", res)

    res, spoken, ok = RS(tid="self")
    check("sich selbst kann niemand von innen neu starten",
          not ok and res["error"] == "not_self", res)

    res, spoken, ok = RS(tid="t99")
    check("unbekannte tid -> nichts laeuft", not ok and res["error"] == "not_running", res)

    fresh(nkids=2)
    res, spoken, ok = RS(tid="t1", reason="Disk angehoben")
    check("ein Kind wird neu gestartet", ok and res["resumed"] == ["t1"], res)
    check("die Zelle wurde angehalten", STOPPED == ["kid1"], STOPPED)
    d = json.load(open(PM._METASESS_FILE))
    t1 = [x for x in d["orch1"]["tasks"] if x["tid"] == "t1"][0]
    t2 = [x for x in d["orch1"]["tasks"] if x["tid"] == "t2"][0]
    check("Auftrag bleibt OFFEN (pending), NICHT abgebrochen",
          t1["state"] == "pending" and not t1.get("error"), t1)
    check("...mit resume_sid, der Arbeitsstand lebt weiter", t1["resume_sid"] == "kid1", t1)
    check("...sofort wieder einplanbar", t1.get("retry_after") == 0, t1)
    check("das Wiederaufnahme-Budget wurde NICHT belastet", not t1.get("resumes"), t1)
    check("der Grund steht in der Metasession",
          "Disk angehoben" in json.dumps(d["orch1"].get("_resume_reason") or {}), d["orch1"].get("_resume_reason"))
    check("das andere Kind blieb unangetastet", t2["state"] == "running", t2)

    fresh(nkids=3)
    res, spoken, ok = RS(tid="*")
    check("'*' startet alle laufenden neu", ok and sorted(res["resumed"]) == ["t1", "t2", "t3"], res)

    fresh(nkids=1)
    PM._meta_work_delta = lambda sid: False
    try:
        res, spoken, ok = RS(tid="t1")
        check("ohne Arbeitsstand wird trotzdem neu gestartet (frisch)",
              ok and res["fresh"] == ["t1"] and not res["failed"], res)
        d = json.load(open(PM._METASESS_FILE))
        t = d["orch1"]["tasks"][0]
        check("...ohne resume_sid, denn es gibt kein Delta, auf das man zeigen koennte",
              t["state"] == "pending" and "resume_sid" not in t, t)
    finally:
        PM._meta_work_delta = lambda sid: True

    fresh(nkids=1)
    PM._meta_work_delta = lambda sid: False
    try:
        res, spoken, ok = R(tid="t1", disk_gb=3)
        check("auch session_resize startet ein Kind ohne Delta jetzt neu",
              ok and res["restarted"] == ["t1"], res)
    finally:
        PM._meta_work_delta = lambda sid: True

    fresh(nkids=1)
    _bak = PM._meta_cell_stop

    def _boom(owner, sid, erase=False):
        raise RuntimeError("Zelle klemmt")

    PM._meta_cell_stop = _boom
    try:
        res, spoken, ok = RS(tid="t1")
        check("ein klemmender Stopp wird als Fehler gemeldet, nicht als Erfolg",
              not ok and res["failed"], res)
    finally:
        PM._meta_cell_stop = _bak
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
if FAILED:
    print("FAILED %d: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("alle orch_resize-Tests bestanden")
