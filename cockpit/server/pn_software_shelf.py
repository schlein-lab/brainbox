#!/usr/bin/env python3

import os, sys, json, re, glob, time, shutil, subprocess, threading

HOME       = os.path.expanduser("~")
SHELF_ROOT = os.path.join(HOME, ".local", "share", "brainarbeit")
KITS_ROOT  = os.path.join(SHELF_ROOT, "kits")
CATALOG    = os.path.join(SHELF_ROOT, "catalog", "catalog.json")
CARDS_DIR  = os.path.join(SHELF_ROOT, "catalog", "cards")
STATUS     = os.path.join(SHELF_ROOT, "catalog", "onboard-status.json")

def catalog():

    try:
        base = json.load(open(CATALOG))
        if not isinstance(base, dict):
            base = {"kits": {}}
    except Exception:
        base = {"kits": {}}
    kits = base.setdefault("kits", {})
    try:
        for kid in sorted(os.listdir(KITS_ROOT)):
            if kid.startswith("_") or kid in kits:
                continue
            mpath = os.path.join(KITS_ROOT, kid, "current", "manifest.json")
            if not os.path.exists(mpath):
                continue
            try:
                man = json.load(open(mpath))
            except Exception:
                man = {}
            entry = {"kit": kid, "version": man.get("version"),
                     "kind": man.get("kind") or "kit", "bin_count": man.get("bin_count"),
                     "primary": man.get("primary") or [], "discovered": True}
            try:
                rec = card_get(kid) or {}
                progs = ((rec.get("manual") or {}).get("programs")) or []
                if progs:
                    entry["program_count"] = len(progs)
                    entry["zweck"] = "; ".join(str(p.get("name")) for p in progs[:6])
            except Exception:
                pass
            kits[kid] = entry
    except Exception:
        pass
    return base

def kit_img(kit_id):

    p = os.path.join(KITS_ROOT, kit_id, "current", "kit.img")
    return p if os.path.exists(p) else None

def card_get(kit_id):
    try:
        return json.load(open(os.path.join(CARDS_DIR, kit_id + ".json")))
    except Exception:
        return None

def set_status(kit, state, msg=""):
    os.makedirs(os.path.dirname(STATUS), exist_ok=True)
    try:
        d = json.load(open(STATUS))
    except Exception:
        d = {}
    d[kit] = {"state": state, "msg": msg, "at": time.time()}
    tmp = STATUS + ".tmp"
    json.dump(d, open(tmp, "w"), ensure_ascii=False)
    os.replace(tmp, STATUS)

def get_status():
    try:
        return json.load(open(STATUS))
    except Exception:
        return {}

def card_all():
    out = {}
    for p in glob.glob(os.path.join(CARDS_DIR, "*.json")):
        try:
            out[os.path.basename(p)[:-5]] = json.load(open(p))
        except Exception:
            pass
    return out

def _persist_card(kit_id, card, runtime):
    os.makedirs(CARDS_DIR, exist_ok=True)
    rec = {"kit": kit_id, "written_by": runtime, "at": time.time(), "manual": card}
    tmp = os.path.join(CARDS_DIR, kit_id + ".json.tmp")
    with open(tmp, "w") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(CARDS_DIR, kit_id + ".json"))
    return rec

def build_conda_kit(kit_id):

    src = os.path.join("/opt/kits", kit_id)
    envd = os.path.join(src, "env")
    if not os.path.isdir(os.path.join(envd, "bin")):
        return False, "kein conda-env unter " + envd
    ver = time.strftime("%Y%m%d-%H%M%S")
    vdir = os.path.join(KITS_ROOT, kit_id, ver)
    os.makedirs(vdir, exist_ok=True)

    libd = os.path.join(src, "lib")
    os.makedirs(libd, exist_ok=True)
    for _ln in ("libutil.so.1", "libnsl.so.1"):
        if os.path.exists(os.path.join(libd, _ln)):
            continue
        for _cand in ("/usr/lib/x86_64-linux-gnu/" + _ln, "/lib/x86_64-linux-gnu/" + _ln):
            if os.path.exists(_cand):
                try:
                    shutil.copy2(os.path.realpath(_cand), os.path.join(libd, _ln))
                except Exception:
                    pass
                break
    try:
        used = int(subprocess.check_output(["du", "-sb", envd]).split()[0])
    except Exception:
        used = 2 * 1024 * 1024 * 1024
    size_mb = int(used / (1024 * 1024) * 1.25) + 256
    img = os.path.join(vdir, "kit.img")
    r = subprocess.run(["mke2fs", "-q", "-t", "ext4", "-d", src, "-r", "1", "-N", "0",
                        "-m", "0", "-F", img, "%dM" % size_mb], capture_output=True, text=True)
    if r.returncode != 0:
        return False, "mke2fs: " + (r.stderr or "")[:200]
    try:
        tools = sorted(os.listdir(os.path.join(envd, "bin")))
    except Exception:
        tools = []
    primary = []
    pf = os.path.join(src, ".primary")
    if os.path.exists(pf):
        primary = [x for x in open(pf).read().split() if x]
    json.dump({"kit": kit_id, "version": ver, "kind": "conda", "bin_count": len(tools), "primary": primary},
              open(os.path.join(vdir, "manifest.json"), "w"), ensure_ascii=False, indent=1)
    cur = os.path.join(KITS_ROOT, kit_id, "current")
    tmp = cur + ".tmp"
    try:
        os.remove(tmp)
    except OSError:
        pass
    os.symlink(ver, tmp)
    os.replace(tmp, cur)
    return True, "conda-Kiste gebaut: %s (%d bin, %d MB)" % (ver, len(tools), size_mb)

def build_kit(kit_id):

    stage_bin = os.path.join(KITS_ROOT, kit_id, "bin")
    if not os.path.isdir(stage_bin) or not os.listdir(stage_bin):
        return False, "keine gestageten Binaries unter " + stage_bin
    ver = time.strftime("%Y%m%d-%H%M%S")
    vdir = os.path.join(KITS_ROOT, kit_id, ver)
    root = os.path.join(vdir, "root")
    os.makedirs(os.path.join(root, "bin"), exist_ok=True)
    total = 0
    for fn in os.listdir(stage_bin):
        src = os.path.join(stage_bin, fn)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(root, "bin", fn))
            os.chmod(os.path.join(root, "bin", fn), 0o755)
            total += os.path.getsize(src)
    stage_lib = os.path.join(KITS_ROOT, kit_id, "lib")
    if os.path.isdir(stage_lib):
        os.makedirs(os.path.join(root, "lib"), exist_ok=True)
        for fn in os.listdir(stage_lib):
            src = os.path.join(stage_lib, fn)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(root, "lib", fn))
                total += os.path.getsize(src)
    size_mb = max(64, int(total / (1024 * 1024) * 1.4) + 32)
    img = os.path.join(vdir, "kit.img")
    r = subprocess.run(["mke2fs", "-q", "-t", "ext4", "-d", root, "-r", "1", "-N", "0",
                        "-m", "0", "-F", img, "%dM" % size_mb],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return False, "mke2fs fehlgeschlagen: " + (r.stderr or "")[:200]
    manifest = {"kit": kit_id, "version": ver, "bytes": total,
                "tools": sorted(os.listdir(os.path.join(root, "bin")))}
    json.dump(manifest, open(os.path.join(vdir, "manifest.json"), "w"), ensure_ascii=False, indent=1)
    cur = os.path.join(KITS_ROOT, kit_id, "current")
    tmp = cur + ".tmp"
    try:
        os.remove(tmp)
    except OSError:
        pass
    os.symlink(ver, tmp)
    os.replace(tmp, cur)
    shutil.rmtree(root, ignore_errors=True)
    return True, "gebaut: %s (%d Tools, %d MB)" % (ver, len(manifest["tools"]), size_mb)

_BRIEF = """Du arbeitest in einer isolierten Brainbox-microVM mit VOLLER Autonomie. Deine Shell IST
die gehaertete exec/Syscall-Lane der Box: jedes Kommando, das du ausfuehrst, ist die Brainbox-Art,
ein Programm zu bedienen. Du darfst frei Kommandos ausfuehren und Dateien schreiben.

AUFGABE: Arbeite dich in die frisch installierte Werkzeug-Kiste "%(kit)s" ein. Ihre ausfuehrbaren
Programme liegen unter %(bindir)s. Setze zuerst den PATH: export PATH=%(bindir)s:$PATH (bei conda-
Kisten laufen die Tools ueber ihren eigenen RPATH — einfach aufrufen). Liste sie: busybox ls %(bindir)s.
%(focus)s
Fuer JEDES (Haupt-)Programm:
  1. MODALITAET bestimmen. Die meisten hier sind reine CLI-Werkzeuge und haben KEINE GUI — zwinge sie
     NICHT in eine GUI. Nur ein Programm MIT GUI muesste ueber die Phantom-GUI-Injektion bedient
     werden; vermerke das dann als modality "gui", ohne es hier zu starten.
  2. SINN, FAEHIGKEITEN und BEDIENWEGE herausfinden — per --help/--version und ECHTEM Ausprobieren
     (z. B. ein winziges Beispiel mit echo/printf durch das Programm schicken).
  3. VERIFIZIEREN: dokumentiere NUR, was du selbst ausgefuehrt und in der Ausgabe gesehen hast.

Schreibe das Ergebnis als striktes JSON nach /root/_card.json:
{"kit":"%(kit)s","programs":[{"name":"...","modality":"cli|tui|gui","purpose":"...",
"capabilities":["..."],"recipes":[{"goal":"...","command":"...","verified":true}]}]}
Wenn fertig, antworte NUR mit: CARD_WRITTEN <anzahl_programme>."""

def _extract_json(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None

def onboard(kit_id, runtime="claude", uid="owner", timeout=1200, log=None):

    def _l(m):
        if log:
            try:
                log(m)
            except Exception:
                pass
    import portal_session_svc as svc
    import portal_voice_core as vc
    import pn_cell_session as cs
    if getattr(vc, "_sessprov_get", None) is None:
        vc._sessprov_get = svc._sessprov_get
    if kit_img(kit_id) is None:
        return {"ok": False, "reason": "Kiste '%s' ist nicht gebaut (kein current/kit.img)" % kit_id}
    if runtime == "claude":
        reason = None
        try:
            reason = cs.llm_lane_reason()
        except Exception:
            pass
        if reason:
            return {"ok": False, "reason": "Box-LLM-Lane nicht verfuegbar: " + reason}

    sid = None
    try:
        import portal_jobs_persist as _pjp
        _rec = _pjp._session_store(uid).create("Einarbeitung: %s" % kit_id)
        sid = _rec.get("id")
        _l("Board-Session angelegt: 'Einarbeitung: %s' (sid=%s) — sichtbar auf dem Board" % (kit_id, sid))
    except Exception as _se:
        _l("Board-Session-Anlage fehlgeschlagen (%r) — unsichtbarer Fallback" % _se)
    if not sid:
        sid = "onboard-" + kit_id
    svc._sessprov_set(uid, sid, {"runtime": runtime, "kits": [kit_id], "autonomy": "high"})
    enf = vc._cockpit_policy_enf(uid, sid) or {}
    enf.setdefault("kits", [kit_id])
    mgr = cs.get_manager()
    try:
        mgr.stop(uid, sid, erase=False)
    except Exception:
        pass
    _l("boote Onboarding-Zelle (runtime=%s, kit=%s)" % (runtime, kit_id))
    cell = mgr.ensure(uid, sid, policy=enf)
    if not (cell and cell.alive()):
        return {"ok": False, "reason": mgr.boot_reason(uid, sid) or "Zelle bootete nicht"}
    cell.start_terminal()
    time.sleep(4)

    _src = os.path.join("/opt/kits", kit_id)
    _bindir = ("/opt/kits/%s/env/bin" % kit_id) if os.path.isdir(os.path.join(_src, "env", "bin")) \
              else ("/opt/kits/%s/bin" % kit_id)
    _focus = ""
    try:
        _pf = os.path.join(_src, ".primary")
        if os.path.exists(_pf):
            _names = [x for x in open(_pf).read().split() if x]
            if _names:
                _focus = ("FOKUS: dokumentiere vor allem diese HAUPT-Programme (nicht jede transitive "
                          "Abhaengigkeit im bin-Verzeichnis): " + ", ".join(_names) + ".")
    except Exception:
        pass

    ntools = "?"
    try:
        _ok, mnt = cell._run("busybox ls %s 2>/dev/null | busybox wc -w; echo __N__" % _bindir,
                             "__N__", 15)
        ntools = (mnt or "").split("__N__")[0].strip()
    except Exception:
        pass
    _l("Kiste gemountet: %s Werkzeuge unter /opt/kits/%s/bin" % (ntools, kit_id))
    _l("Agent erkundet '%s' vollautonom ..." % kit_id)
    cell.voice_turn(_BRIEF % {"kit": kit_id, "bindir": _bindir, "focus": _focus}, timeout=120, settle=2.0)

    card = None; ans = ""; nudges = 0; deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(15)
        try:
            _ok, raw = cell._run("busybox cat /root/_card.json 2>/dev/null; echo __CARD__", "__CARD__", 15)
            raw = (raw or "").split("__CARD__")[0].strip()
        except Exception:
            raw = ""
        c = _extract_json(raw)
        if c and (c.get("programs") is not None):
            card = c
            _l("Kartei-Datei erkannt (%d Programme)" % len(c.get("programs") or []))
            break
        if nudges < 6:
            nudges += 1
            r = cell.voice_turn("Mach autonom weiter: die restlichen Haupt-Programme unter %s "
                                "erkunden und am ENDE /root/_card.json als striktes JSON schreiben." % _bindir,
                                timeout=90, settle=2.0)
            ans = (r or {}).get("text", "") or ans
            _l("Stups %d (Agent: %s)" % (nudges, (ans[-90:] if ans else "")))
    card = card or _extract_json(ans) or {"raw_answer": ans, "mounted_tools": ntools}
    rec = _persist_card(kit_id, card, runtime)
    nprog = len((card or {}).get("programs") or [])
    _l("Bedien-Kartei geschrieben: %d Programme -> %s.json" % (nprog, kit_id))

    _l("Einarbeitung fertig — Session bleibt SICHTBAR auf dem Board (sid=%s)" % sid)
    return {"ok": True, "programs": nprog, "card": rec, "sid": sid}

_WARDEN_MARK = os.path.join(SHELF_ROOT, "warden.json")

_METHODIK = """# METHODIK: Shelf Warden

Du bist der SHELF WARDEN dieser Brainbox - eine STEHENDE Orchestrator-Session. Dein einziger
Auftrag: das Software-Regal (zentrale Werkzeug-Kisten + ihre Bedien-Karteien) vollstaendig,
aktuell und bedienbar halten. Du arbeitest VOLLAUTONOM: keine Rueckfragen an Menschen. Probleme
loest du selbst oder dokumentierst sie - du wartest nie.

## Werkzeuge (governte Portal-Verben ueber portalctl)
Der Host fuehrt aus, du BEANTRAGST:
- portalctl store_status '{}'
  Zustand aller Kisten: Version, Programme, verifizierte Rezepte, Einarbeitungs-Status.
- portalctl store_onboard '{"kit":"<id>"}'
  Host startet eine SICHTBARE Einarbeitungs-Session (ein Agent erkundet die Programme und
  schreibt die Bedien-Kartei). Laeuft schon eine, kommt eine ehrliche Ablehnung.
- portalctl session_spawn '{"task":"..."}' | session_status | session_tell '{"tid":"..","text":".."}'
  Eigene Sub-Sessions fuer Analyse-Sonderauftraege (sparsam einsetzen).
- portalctl schedule '{"do":{"verb":"session_wake","args":{"text":"Aufsichts-Durchlauf: fuehre die METHODIK aus."}},"every":"6h","label":"shelf-watch"}'
  Host-seitiger Timer, der DICH weckt (Cron laeuft AUSSERHALB deiner Zelle; du beantragst nur).
  portalctl schedule_list '{}' zeigt Timer; portalctl schedule_cancel '{"id":"<id>"}' loescht.

## Aufsichts-Durchlauf (bei jedem Weckruf KOMPLETT ausfuehren)
1. portalctl store_status '{}' - Ist-Zustand holen.
2. Jede Kiste mit verified=0 und ohne laufende Einarbeitung: portalctl store_onboard.
   Maximal 2 Anlaeufe je Kiste und Tag - danach PROBLEM dokumentieren statt endlos neu starten.
3. Laufende Einarbeitungen pruefen: steht eine laut onboard_at/onboard_msg seit ueber 90 Minuten
   unveraendert auf running, EINMAL neu anstossen; sonst nur notieren.
4. Befund an /root/AUFSICHT.md ANFUEGEN: Datum/Uhrzeit, je Kiste eine Zeile
   (ok | onboarding | PROBLEM + Grund), getroffene Aktionen.
5. portalctl schedule_list '{}': existiert dein wiederkehrender Weckruf "shelf-watch"? Wenn
   nein: mit dem schedule-Beispiel oben NEU stellen. Nie doppelt stellen.
6. Kurz melden, was du getan hast. Dann endet dein Zug - der Timer weckt dich wieder.

## Eskalationsleiter (Werkstufen-Doktrin)
Beobachten (store_status) -> anstossen (store_onboard) -> analysieren (session_spawn).
Immer die kleinste Stufe zuerst. GUI-Erkundung ist NICHT dein Job (macht die Einarbeitung).

## Grenzen
- Du installierst nichts und baust keine Kisten - Bedarf dokumentierst du in /root/AUFSICHT.md.
- Wird ein Verb abgelehnt, notiere die ehrliche Ablehnung als PROBLEM - nicht raten, nicht umgehen.
- Deine Berichte liest der Betreiber in AUFSICHT.md und auf dem Board - niemand antwortet dir live.
"""

def warden_peek(uid="owner", tail=40):

    import json as _j
    sid = warden_sid()
    out = {"ok": True, "sid": sid, "cell": "keine", "aufsicht": "", "methodik": False,
           "actions": [], "timers": []}
    try:
        import portal_metasessions as _pm
        out["timers"] = [{"label": t.get("label"), "every_s": t.get("every_s"),
                          "next_ts": t.get("next_ts")} for t in _pm._sched_load()
                         if t.get("uid") == uid and "aufsicht" in (t.get("label") or "").lower()]
    except Exception:
        pass
    if not sid:
        out["ok"] = False
        return out
    try:
        import pn_cell_session as _cs
        cell = _cs.get_manager().get(uid, sid)
    except Exception as e:
        out["error"] = str(e)
        return out
    if cell is None or not cell.alive():
        out["cell"] = "kalt" if cell else "keine"
        return out
    out["cell"] = "warm"
    try:
        cell.start_terminal()
    except Exception:
        pass
    try:
        ok, o = cell._run("busybox cat /root/AUFSICHT.md 2>/dev/null | busybox tail -c 3000; echo __A__", "__A__", 12)
        out["aufsicht"] = (o or "").split("__A__")[0]
    except Exception as e:
        out["aufsicht_err"] = str(e)
    try:
        ok, o = cell._run("busybox test -f /root/METHODIK.md && echo YES; echo __M__", "__M__", 8)
        out["methodik"] = "YES" in (o or "")
    except Exception:
        pass
    try:
        path = cell._incell_active_jsonl()
        if path:
            ok, o = cell._run("busybox tail -c 60000 %s 2>/dev/null | busybox grep -a -E "
                              "'portalctl|store_status|store_onboard|schedule|AUFSICHT' | "
                              "busybox tail -n %d; echo __T__" % (path, int(tail)), "__T__", 15)
            body = (o or "").split("__T__")[0]
            acts = []
            for ln in body.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = _j.loads(ln)
                    s = _j.dumps(d, ensure_ascii=False)
                except Exception:
                    s = ln
                acts.append(s[:240])
            out["actions"] = acts[-tail:]
    except Exception as e:
        out["actions_err"] = str(e)
    return out

def warden_sid():
    try:
        return (json.load(open(_WARDEN_MARK)) or {}).get("sid")
    except Exception:
        return None

def status_brief():

    cat = (catalog() or {}).get("kits") or {}
    sts = get_status() or {}
    kits, missing, running = [], [], []
    for kid in sorted(cat):
        man = cat.get(kid) or {}
        rec = card_get(kid) or {}
        progs = ((rec.get("manual") or {}).get("programs")) or []
        nver = sum(1 for p in progs
                   if any(r.get("verified") for r in (p.get("recipes") or [])))
        st = (sts.get(kid) or {})
        kits.append({"kit": kid, "version": str(man.get("version") or ""),
                     "bins": man.get("bin_count"), "primary": len(man.get("primary") or []),
                     "programs": len(progs), "verified": nver,
                     "onboard_state": st.get("state"), "onboard_msg": str(st.get("msg") or "")[:140],
                     "onboard_at": st.get("at")})
        if st.get("state") == "running":
            running.append(kid)
        elif nver == 0:
            missing.append(kid)
    spoken = ("Regal: %d Kisten; ohne verifizierte Kartei: %s; Einarbeitung laeuft: %s."
              % (len(kits), ", ".join(missing) or "keine", ", ".join(running) or "keine"))
    return {"ok": True, "kits": kits, "missing_cards": missing, "onboarding": running, "spoken": spoken}

def onboard_request(kit_id):

    kit_id = str(kit_id or "").strip()
    if not kit_id:
        return {"ok": False, "reason": "kit fehlt", "spoken": "Welche Kiste soll eingearbeitet werden?"}
    if kit_img(kit_id) is None:
        return {"ok": False, "reason": "Kiste '%s' ist nicht gebaut" % kit_id,
                "spoken": "Die Kiste %s ist nicht gebaut - keine Einarbeitung moeglich." % kit_id}
    st = (get_status() or {}).get(kit_id) or {}
    if st.get("state") == "running":
        return {"ok": False, "reason": "laeuft bereits", "already": True,
                "spoken": "Die Einarbeitung von %s laeuft bereits - kein zweiter Start." % kit_id}
    def _go():
        set_status(kit_id, "running", "Einarbeitung beantragt (Shelf Warden)")
        try:
            r = onboard(kit_id, log=lambda m: set_status(kit_id, "running", m)) or {}
            set_status(kit_id, "done" if r.get("ok") else "error",
                       str(r.get("reason") or "fertig")[:200])
        except Exception as e:
            set_status(kit_id, "error", str(e)[:200])
    threading.Thread(target=_go, daemon=True).start()
    return {"ok": True, "started": kit_id,
            "spoken": "Einarbeitung von %s gestartet - als sichtbare Session auf dem Board." % kit_id}

def warden_ensure(uid="owner", log=None):

    import base64 as _b64
    def _l(m):
        if log:
            try:
                log(m)
            except Exception:
                pass
    import portal_session_svc as svc
    import portal_voice_core as vc
    import pn_cell_session as cs
    import portal_jobs_persist as pjp
    try:
        import pn_session_policy as _pol
    except Exception:
        _pol = None
    if getattr(vc, "_sessprov_get", None) is None:
        vc._sessprov_get = svc._sessprov_get
    try:
        reason = cs.llm_lane_reason()
    except Exception:
        reason = None
    if reason:
        return {"ok": False, "reason": "Box-LLM-Lane nicht verfuegbar: " + reason}
    store = pjp._session_store(uid)
    sid = warden_sid()
    if sid and not store.get(sid):
        sid = None
    if not sid:
        rec = store.create("Shelf Warden")
        sid = rec.get("id")
        json.dump({"sid": sid}, open(_WARDEN_MARK, "w"))
        _l("Shelf-Warden-Session 'Shelf Warden' angelegt (sid=%s)" % sid)
    try:
        cur = svc._sess_policy_get(uid, sid) or (_pol.new_policy(_pol.DEFAULT_PRESET) if _pol else {"caps": {}})
        cur.setdefault("caps", {})["orchestrate"] = "allow"
        if _pol:
            cur = _pol.apply_floor(_pol.validate(cur), {})
        svc._sess_policy_store().set(uid, "cockpit", sid, cur)
    except Exception as e:
        return {"ok": False, "reason": "orchestrate-Recht setzen fehlgeschlagen: %r" % e, "sid": sid}
    svc._sessprov_set(uid, sid, {"runtime": "claude", "autonomy": "high"})
    enf = vc._cockpit_policy_enf(uid, sid) or {}
    mgr = cs.get_manager()
    cell = mgr.ensure(uid, sid, portal_url=vc._portal_base_url(),
                      portal_token=vc._voice_agent_token(uid), policy=enf)
    if not (cell and cell.alive()):
        return {"ok": False, "reason": mgr.boot_reason(uid, sid) or "Zelle bootete nicht", "sid": sid}
    cell.start_terminal()
    time.sleep(3)
    b64 = _b64.b64encode(_METHODIK.encode()).decode()
    cell._run("printf %%s '%s' | base64 -d > /root/METHODIK.md; "
              "busybox grep -q '@METHODIK.md' /root/CLAUDE.md 2>/dev/null || "
              "printf '\\n@METHODIK.md\\n' >> /root/CLAUDE.md; echo __MW__" % b64, "__MW__", 15)
    _l("METHODIK.md in die Zelle gestaged")
    cell.voice_turn("Du bist der Shelf Warden dieser Box. Lies /root/METHODIK.md und fuehre JETZT "
                    "deinen ersten Aufsichts-Durchlauf vollstaendig aus - vollautonom, ohne "
                    "Rueckfragen. Vergiss Schritt 5 nicht (dein wiederkehrender Weckruf).",
                    timeout=90, settle=1.0)
    _l("Aufseher genudged - arbeitet ab jetzt selbst")
    return {"ok": True, "sid": sid}

def orch_proof(uid="owner", log=None):

    import time as _t
    def _l(m):
        if log:
            try:
                log(m)
            except Exception:
                pass
    import portal_metasessions as pm
    import portal_jobs_persist as pjp
    import portal_session_svc as svc
    try:
        import pn_session_policy as _pol
    except Exception:
        _pol = None
    try:
        rec = pjp._session_store(uid).create("Orchestrator-Probe")
        osid = rec.get("id")
    except Exception as e:
        return {"ok": False, "reason": "Session-Anlage fehlgeschlagen: %r" % e}
    try:
        cur = svc._sess_policy_get(uid, osid) or (_pol.new_policy(_pol.DEFAULT_PRESET) if _pol else {"caps": {}})
        cur.setdefault("caps", {})["orchestrate"] = "allow"
        if _pol:
            cur = _pol.apply_floor(_pol.validate(cur), {})
        svc._sess_policy_store().set(uid, "cockpit", osid, cur)
    except Exception as e:
        return {"ok": False, "reason": "orchestrate-Recht setzen fehlgeschlagen: %r" % e, "orch_sid": osid}
    _l("Orchestrator-Session 'Orchestrator-Probe' (sid=%s) angelegt, orchestrate=allow" % osid)
    token = "HALLO-VOM-SUBAGENT-%d" % int(_t.time() % 100000)
    task = ("Antworte in GENAU einer Zeile mit exakt diesem Text und sonst nichts: %s . "
            "Fuehre keine weiteren Schritte aus, danach bist du fertig." % token)
    res, spoken, ok = pm.orch_spawn(uid, osid, task, title="Subsession-Probe")
    _l("session_spawn: ok=%s tid=%s (%s)" % (ok, (res or {}).get("tid"), spoken))
    if not ok:
        return {"ok": False, "reason": "orch_spawn abgelehnt: %s" % ((res or {}).get("error")), "orch_sid": osid}
    tid = (res or {}).get("tid")
    deadline = _t.time() + 600
    result = None
    wsid = None
    while _t.time() < deadline:
        _t.sleep(15)
        stt, sp, sok = pm.orch_status(uid, osid)
        for t in (stt or {}).get("tasks", []):
            if t.get("tid") == tid:
                wsid = t.get("sid") or wsid
                st = t.get("state")
                _l("Sub-Session %s: state=%s sid=%s" % (tid, st, wsid))
                if st == "done":
                    result = t.get("result") or ""
                elif st == "error":
                    return {"ok": False, "reason": "Sub-Session-Fehler: %s" % t.get("error"),
                            "orch_sid": osid, "worker_sid": wsid}
        if result is not None:
            break
    got = token in (result or "")
    _l("ERGEBNIS der Sub-Session: %r | Token gefunden: %s" % ((result or "")[:120], got))
    return {"ok": bool(got), "token": token, "found": got, "orch_sid": osid, "worker_sid": wsid,
            "result": (result or "")[:500]}

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    if len(sys.argv) < 2:
        print("usage: pn_software_shelf.py [catalog|build <kit>|onboard <kit>|cards]")
        sys.exit(2)
    cmd = sys.argv[1]
    if cmd == "catalog":
        print(json.dumps(catalog(), ensure_ascii=False, indent=1))
    elif cmd == "cards":
        print(json.dumps(card_all(), ensure_ascii=False, indent=1))
    elif cmd == "build" and len(sys.argv) > 2:
        ok, msg = build_kit(sys.argv[2])
        print(("OK " if ok else "FEHLER ") + msg)
    elif cmd == "build-conda" and len(sys.argv) > 2:
        ok, msg = build_conda_kit(sys.argv[2])
        print(("OK " if ok else "FEHLER ") + msg)
    elif cmd == "onboard" and len(sys.argv) > 2:
        rt = sys.argv[3] if len(sys.argv) > 3 else "claude"
        print(json.dumps(onboard(sys.argv[2], runtime=rt, log=lambda m: print("[onboard]", m, flush=True)),
                         ensure_ascii=False, indent=1))
    else:
        print("unbekanntes Kommando")
        sys.exit(2)
