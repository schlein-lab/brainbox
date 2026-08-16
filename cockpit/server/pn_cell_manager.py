#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pn_cell_basis import (
    CLOCK_DRIFT_MAX_S,
    CLOCK_SYNC_S,
    IDLE_STOP_S,
    MEM_MB,
    READOPT_ON,
    REMOTE_READOPT_WAIT_S,
    RUN_DIR,
    VOL_DIR,
    _cell_name,
    _sc,
    cells_enabled,
    llm_lane_reason,
    preflight)
from pn_cell_broker import _kill_cell_brokers, _reap_dead_cell_brokers

BUSY_PROBE_GRACE_S = float(os.environ.get("PN_BUSY_PROBE_GRACE_S", 300))
from pn_cell_zelle import CellSession
from pn_cell_zelle import CellSession

class CellManager:
    def __init__(self):
        self._cells = {}
        self._cids = set()
        self._lock = threading.Lock()

    def _alloc_cid(self):

        for c in range(40, 250):
            if c not in self._cids:
                self._cids.add(c); return c
        raise RuntimeError("no free vsock CID")

    def adopt_survivors(self):

        try:
            names = os.listdir(RUN_DIR)
        except OSError:

            names = []
        adopted = 0
        remote_kandidaten = []
        for name in names:
            d = os.path.join(RUN_DIR, name)
            mf = os.path.join(d, "cell.json"); tf = os.path.join(d, "adopt.token")
            if not (os.path.isfile(mf) and os.path.isfile(tf)):
                continue
            try:
                meta = json.load(open(mf))
            except Exception:
                continue
            principal = meta.get("principal"); session = meta.get("session")
            cid = meta.get("cid"); delta = meta.get("delta")
            if not principal or not session or not delta or cid is None:
                continue
            key = (principal, session)
            with self._lock:
                if key in self._cells:
                    continue

            if meta.get("node"):

                remote_kandidaten.append((key, meta))
                continue
            if not os.path.exists(os.path.join(d, "seat_adopt.sock")):
                _reap_dead_cell_brokers(d, meta)
                continue
            try:
                mem_mb = int(meta.get("mem_mb") or MEM_MB)
            except (ValueError, TypeError):
                mem_mb = int(MEM_MB)
            try:
                cell = CellSession(principal, session, cid,
                                   policy={"mem_mb": mem_mb, "desktop": bool(meta.get("desktop"))})
                adopted_ok = cell.adopt_in_place(mem_mb)
            except Exception:
                continue
            if not adopted_ok:
                _reap_dead_cell_brokers(d, meta)
                continue
            with self._lock:
                race_lost = key in self._cells
                if not race_lost:
                    self._cells[key] = cell
                    self._cids.add(cid)
                    adopted += 1
            if race_lost:

                for _s in (cell.conn, cell.term_conn):
                    try:
                        if _s is not None: _s.close()
                    except OSError: pass
                cell.conn = cell.term_conn = None
                cell.proc = None
                continue
            try:
                import sys as _sys
                _sys.stderr.write("[cell-adopt] re-adopted %s pid=%s IN PLACE (no reboot)\n"
                                  % (cell.cell, cell.proc.pid))
            except Exception:
                pass
            try:
                cell._stage_exchange()
                cell._stage_ca()
            except Exception:
                pass
        try:

            remote_kandidaten += self._fern_ueberlebende({k for k, _m in remote_kandidaten})
        except Exception:
            pass
        adopted += self._readopt_remote_cells(remote_kandidaten)
        try:
            import sys as _sys
            _sys.stderr.write("[cell-adopt] scan complete: %d run dir(s), %d re-adopted drive-in-place\n"
                              % (len(names), adopted))
        except Exception:
            pass

    def _fern_ueberlebende(self, bekannt):

        try:
            import pn_cell_fern as _fern
            zellen, _frisch = _fern.fern_zellen_uebersicht()
        except Exception:
            return []
        if not zellen:
            return []
        if _sc is None:
            return []
        try:
            akte = {r.get("cell"): r
                    for r in _sc.SessionCellRegistry(os.path.dirname(VOL_DIR)).list_all()
                    if r.get("cell")}
        except Exception:
            return []
        out = []
        for cell_id, info in sorted(zellen.items()):
            e = info.get("eintrag") or {}
            if e.get("state") != "running":
                continue
            r = akte.get(cell_id)
            if not r:
                continue
            key = (r.get("principal"), r.get("session"))
            if not key[0] or not key[1] or key in bekannt:
                continue
            with self._lock:
                if key in self._cells:
                    continue
            if os.path.isfile(os.path.join(RUN_DIR, cell_id, "cell.json")):
                continue
            meta = {"principal": key[0], "session": key[1], "cid": e.get("cid"),
                    "mem_mb": e.get("mem_mb"), "node": info.get("node"), "desktop": False}
            out.append((key, meta))
            try:
                import sys as _sys
                _sys.stderr.write("[cell-adopt] %s: lebt auf Node %s weiter, Laufverzeichnis fehlt "
                                  "(Box-Neustart) — rekonstruiere und biete Bahnen neu an\n"
                                  % (cell_id, info.get("node")))
            except Exception:
                pass
        return out

    def _fern_zeugen(self):

        try:
            import pn_cell_fern as _fern
            zellen, frisch = _fern.fern_zellen_uebersicht()
        except Exception:
            return set(), False
        lebend = {cid for cid, info in (zellen or {}).items()
                  if ((info.get("eintrag") or {}).get("state") == "running")}
        return lebend, bool(frisch)

    def _fern_fort(self, cell_id, lebend, beweiskraft):

        return bool(beweiskraft) and bool(cell_id) and cell_id not in (lebend or set())

    def waisen_einsammeln(self):

        try:
            names = os.listdir(RUN_DIR)
        except OSError:
            return (0, 0)
        with self._lock:
            lebende_ordner = {str(getattr(c, "run_dir", "")).rstrip("/")
                              for c in self._cells.values() if getattr(c, "run_dir", None)}
        lebend = None
        beweiskraft = False
        eingesammelt = archiviert = 0
        for name in sorted(names):
            d = os.path.join(RUN_DIR, name)
            if not os.path.isdir(d) or d.rstrip("/") in lebende_ordner:
                continue
            try:
                with open(os.path.join(d, "cell.json"), encoding="utf-8") as fh:
                    meta = json.load(fh)
            except Exception:
                continue
            if meta.get("node"):
                if lebend is None:
                    lebend, beweiskraft = self._fern_zeugen()
                if not self._fern_fort(name, lebend, beweiskraft):
                    continue
                grund = "fern: Knoten antwortete vollstaendig und nennt die Zelle nicht mehr"
            else:
                pid = meta.get("vmm_pid")
                if not pid:
                    continue
                try:
                    with open("/proc/%d/comm" % int(pid), encoding="utf-8") as fh:
                        if fh.read().strip() == "pn-vmm":
                            continue
                except (OSError, ValueError):
                    pass
                grund = "lokal: vmm_pid %s ist kein pn-vmm mehr" % pid
            _kill_cell_brokers(d)
            eingesammelt += 1
            if self._zellordner_archivieren(d, name, grund):
                archiviert += 1
        return (eingesammelt, archiviert)

    def _zellordner_archivieren(self, run_dir, cell_id, grund):

        ziel = os.path.join(os.path.dirname(VOL_DIR), "zell-archiv", cell_id)
        try:
            os.makedirs(ziel, exist_ok=True)
            kopiert = 0
            for f in sorted(os.listdir(run_dir)):
                q = os.path.join(run_dir, f)
                if os.path.islink(q) or not os.path.isfile(q):
                    continue
                shutil.copy2(q, os.path.join(ziel, f))
                kopiert += 1
            with open(os.path.join(ziel, "ARCHIV.txt"), "a", encoding="utf-8") as fh:
                fh.write("%s  %s  (%d Datei(en) aus %s)\n"
                         % (time.strftime("%Y-%m-%dT%H:%M:%S"), grund, kopiert, run_dir))
        except OSError:
            return False
        try:
            shutil.rmtree(run_dir)
        except OSError:
            return False
        try:
            import portal_archive as _pa
            _pa.journal(was="zellordner-archiviert", zelle=cell_id, grund=grund,
                        von=run_dir, nach=ziel, dateien=kopiert)
        except Exception:
            pass
        return True

    def _readopt_remote_cells(self, kandidaten):

        if not kandidaten:
            return 0
        import sys as _sys
        lebend, beweiskraft = self._fern_zeugen()
        vorbereitet = []
        for key, meta in kandidaten:
            principal, session = key
            zell_id = _cell_name(principal, session)
            if self._fern_fort(zell_id, lebend, beweiskraft):

                _kill_cell_brokers(os.path.join(RUN_DIR, zell_id))
                _sys.stderr.write("[cell-adopt] %s: Knoten kennt sie nicht mehr -- Bahnen NICHT "
                                  "neu angeboten, Broker eingesammelt\n" % zell_id)
                continue
            try:
                mem_mb = int(meta.get("mem_mb") or MEM_MB)
            except (TypeError, ValueError):
                mem_mb = int(MEM_MB)
            cell = None
            try:
                cell = CellSession(principal, session, meta.get("cid"),
                                   policy={"mem_mb": mem_mb, "desktop": bool(meta.get("desktop")),
                                           "node": meta.get("node")})
                try:

                    cell.fern_broker_sicherstellen()
                except Exception:
                    pass
                p = cell.readopt_prepare()
            except Exception:
                p = None
            if p is None:
                grund = getattr(cell, "_readopt_grund", None) if cell is not None else None
                if grund:
                    import sys as _s
                    _s.stderr.write("[cell-adopt] %s: nicht uebernommen — %s\n" % (key[1], grund))
                continue
            vorbereitet.append((key, cell, p, mem_mb))
        if not vorbereitet:
            return 0
        _sys.stderr.write("[cell-adopt] %d Remote-Zelle(n) laufen weiter — Bahnen neu angeboten, "
                          "warte auf die Wiederanwahl der Knoten\n" % len(vorbereitet))
        deadline = time.time() + REMOTE_READOPT_WAIT_S
        ergebnis = {}
        threads = []
        for key, cell, p, mem_mb in vorbereitet:
            def _lauf(key=key, cell=cell, p=p, mem_mb=mem_mb):
                try:
                    ergebnis[key] = cell.readopt_finish(p, mem_mb, deadline)
                except Exception:
                    ergebnis[key] = False
            t = threading.Thread(target=_lauf, name="readopt-%s" % cell.cell[:24], daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(max(1.0, deadline - time.time() + 5.0))
        n = 0
        for key, cell, _p, mem_mb in vorbereitet:
            if not ergebnis.get(key):
                _sys.stderr.write("[cell-adopt] %s: Knoten hat nicht neu angewaehlt — unberuehrt gelassen\n"
                                  % cell.cell)
                continue
            with self._lock:
                if key in self._cells:
                    continue
                self._cells[key] = cell
                try:
                    self._cids.add(cell.cid)
                except Exception:
                    pass
            n += 1
            _sys.stderr.write("[cell-adopt] %s: REMOTE wieder uebernommen (kein Neustart, Gast antwortet)\n"
                              % cell.cell)
            try:

                if _sc is not None:
                    _reg = _sc.SessionCellRegistry(os.path.dirname(VOL_DIR))
                    _reg.provision(key[0], key[1])
                    if hasattr(_reg, "set_node"):
                        _reg.set_node(key[0], key[1], cell._remote_node())
            except Exception:
                pass
            try:
                cell._stage_exchange()
                cell._stage_ca()
            except Exception:
                pass
        return n

    def ensure(self, principal="owner", session="voice", portal_url=None, portal_token=None, policy=None):
        if not cells_enabled():

            return None
        key = (principal, session)
        with self._lock:
            cell = self._cells.get(key)
            if cell is None:
                cell = CellSession(principal, session, self._alloc_cid(),
                                   portal_url=portal_url, portal_token=portal_token, policy=policy)
                self._cells[key] = cell
            else:
                if portal_token and not cell.portal_token:
                    cell.portal_url, cell.portal_token = portal_url, portal_token
                if policy is not None:
                    cell.policy = policy
        cell.boot()
        if _sc is not None:
            try:

                _reg = _sc.SessionCellRegistry(os.path.dirname(VOL_DIR))
                _reg.provision(principal, session)

                if hasattr(_reg, "set_node"):
                    _reg.set_node(principal, session, cell._remote_node())
            except Exception:
                pass
        return cell

    def get(self, principal="owner", session="voice"):
        return self._cells.get((principal, session))

    def boot_reason(self, principal="owner", session="voice"):

        c = self._cells.get((principal, session))
        return c.boot_reason() if c is not None else preflight()

    def stop(self, principal="owner", session="voice", erase=False, owner_2fa=False):

        if erase and not owner_2fa:
            try:
                import sys as _sys
                import traceback as _tb
                _sys.stderr.write(
                    "[cell-stop] ERASE VERWEIGERT fuer %s/%s: Loeschen eines Zell-Deltas braucht eine "
                    "ausdrueckliche Owner-Anordnung mit 2FA (owner_2fa=True). Es wird nur gestoppt; "
                    "Delta, Transkripte und Zwischenstaende bleiben liegen. Aufrufer:\n%s"
                    % (principal, session, "".join(_tb.format_stack(limit=6))))
            except Exception:
                pass
            erase = False
        with self._lock:
            cell = self._cells.pop((principal, session), None)
            if cell is not None:
                self._cids.discard(cell.cid)
        did = False
        if cell is not None:

            cell._teardown(reboot=False)
            if erase:
                cell._erase_state()
            did = True
        if erase and cell is None:

            name = _cell_name(principal, session)
            rd = os.path.join(RUN_DIR, name)
            _kill_cell_brokers(rd)
            try:
                dp = os.path.join(VOL_DIR, name + "-delta.img")
                if os.path.exists(dp): os.unlink(dp)
            except OSError:
                pass
            shutil.rmtree(rd, ignore_errors=True)
            did = True
        return did

    def freeze(self, principal="owner", session="voice", on=True):

        c = self._cells.get((principal, session))
        return bool(c and c.freeze(on))

    def is_warm(self, principal="owner", session="voice"):

        c = self._cells.get((principal, session))
        return bool(c and c.alive())

    def warm_set(self, principal):

        out = set()
        for (p, s), c in list(self._cells.items()):
            if p != principal:
                continue
            try:
                if c.alive():
                    out.add(s)
            except Exception:
                pass
        return out

    def cell(self, principal="owner", session="voice"):

        return self._cells.get((principal, session))

    def sessions_for(self, principal):

        return [s for (p, s) in list(self._cells.keys()) if p == principal]

    def list_live(self):
        return [c.info() for c in list(self._cells.values())]

    def idle_sweep(self, now=None):

        now = now or time.time()
        for (p, s), c in list(self._cells.items()):
            if not (c.last and (now - c.last) > IDLE_STOP_S):
                continue
            try:
                _pfad = c._incell_active_jsonl()
                beschaeftigt = bool(_pfad and c._incell_turn_busy(_pfad))
                c._busy_probe_blind_seit = 0.0
                if beschaeftigt:
                    c.last = now
                    continue
            except Exception as e:
                seit = getattr(c, "_busy_probe_blind_seit", 0.0) or now
                c._busy_probe_blind_seit = seit
                if (now - seit) < BUSY_PROBE_GRACE_S:
                    continue
                if _vmm_brennt(c):
                    try:
                        c._log("Leerlauf: Sonde stumm seit %d s, aber der vmm "
                               "rechnet — nicht eingesammelt" % (now - seit))
                    except Exception:
                        pass
                    continue
                try:
                    c._log("Leerlauf: seit %d s keine Antwort auf die Beschaeftigt-Frage "
                           "(%s) — wird als leerlaufend eingesammelt"
                           % (now - seit, e))
                except Exception:
                    pass
            self.stop(p, s)

    def clock_sweep(self, now=None):

        now = now or time.time()
        for (p, s), c in list(self._cells.items()):
            try:
                if now - float(getattr(c, "_clock_at", 0) or 0) < CLOCK_SYNC_S:
                    continue
                if not c.alive():
                    continue
                drift = c._stage_clock()
                if isinstance(drift, int) and drift > CLOCK_DRIFT_MAX_S:
                    c._log("clock: nachgezogen (Abweichung war %ds)" % drift)
            except Exception:
                pass

_MANAGER = None
_MGR_LOCK = threading.Lock()

def get_manager():
    global _MANAGER
    with _MGR_LOCK:
        if _MANAGER is None:
            _MANAGER = CellManager()
            if READOPT_ON and cells_enabled():
                try:
                    _MANAGER.adopt_survivors()
                except Exception:
                    pass
    return _MANAGER

def _selftest():

    pf = preflight()
    if pf:
        print("PREFLIGHT: %s" % pf)
        print("CELL: FAIL"); print("SELFTEST: FAIL")
        return 1
    mgr = get_manager()
    cell = mgr.ensure("owner", "selftest")
    if cell is None or not cell.alive():
        print("REASON:", mgr.boot_reason("owner", "selftest") or "unbekannt")
        print("CELL: FAIL"); print("SELFTEST: FAIL")
        return 1
    print("booted:", cell.alive(), "cid:", cell.cid, "mem_mb:", cell.policy.get("mem_mb") or MEM_MB)

    _lane0 = llm_lane_reason()
    if _lane0:
        print("LLM-LANE:", _lane0)
    _tmo = 60 if _lane0 else 240

    def _turn(prompt, timeout=None):
        r = cell.ask(prompt, timeout=timeout or _tmo)
        return (r.get("text") or "") if isinstance(r, dict) else str(r or "")

    r1 = _turn("Merke dir die Zauberzahl 8237. Antworte nur mit OK.")
    print("turn1:", repr(r1[:300]))
    r2 = _turn("Welche Zauberzahl? Antworte nur mit der Zahl.")
    print("turn2:", repr(r2[:300]))

    seat_ok = cell.seat_echo()
    bin_ok = cell._claude_runnable()
    term_ok = bool(cell.term_on and cell.term_runner_alive())
    cell_ok = bool(cell.alive() and seat_ok and bin_ok and term_ok)
    print("  cell.alive=%s seat=%s claude=%r term_runner=%s"
          % (cell.alive(), seat_ok, cell._claude_probe, term_ok))
    llm_ok = "8237" in r2
    lane = llm_lane_reason()
    llm_err = next((t for t in (r2, r1) if "API Error" in t or "error" in t.lower()), "")
    print("CELL:", "PASS" if cell_ok else "FAIL")
    print("LLM:", "PASS" if llm_ok else
          ("FAIL — " + (lane or " ".join(llm_err.split())[:200] or "keine Antwort")))
    mgr.stop("owner", "selftest")
    print("SELFTEST:", "PASS" if (cell_ok and llm_ok) else "FAIL")
    return 0 if (cell_ok and llm_ok) else 1

def _portaltest():

    import json
    home = os.path.expanduser("~")
    try:
        cfg = json.load(open(os.path.join(home, ".config/brainbox-portal/config.json")))
    except Exception:
        cfg = {}
    purl = "%s://127.0.0.1:%s" % ("https" if cfg.get("cert") else "http", cfg.get("port", 8076))
    tok = None
    try:
        d = json.load(open(os.path.join(home, ".local/share/brainbox-portal/sessions.json")))

        _u = lambda v: (v.get("uid") if isinstance(v, dict) else v)
        tok = next((t for t, u in d.items() if _u(u) == "owner" and t.startswith("agent-")), None) \
            or next((t for t, u in d.items() if _u(u) == "owner"), None)
    except Exception:
        pass
    if not tok:
        print("no owner token; is the portal up?"); return 2
    mgr = get_manager()
    cell = mgr.ensure("owner", "portaltest", portal_url=purl, portal_token=tok)
    if cell is None or not cell.alive():
        print("REASON:", mgr.boot_reason("owner", "portaltest") or "unbekannt")
        print("PORTALTEST: FAIL")
        return 1
    print("booted:", cell.alive(), "cid:", cell.cid)

    _r = cell.ask("Rufe genau einmal das Kommando `portalctl state` auf und nenne mir den Wert von uid. "
                  "Antworte in einem kurzen Satz.", timeout=240,
                  system="Du bist ein isolierter Test-Agent. Du hast das Kommando `portalctl` (portalctl state / "
                         "portalctl <verb> '<json>'), mit dem du das Portal bedienst. Nutze es wirklich.")
    r = (_r.get("text") or "") if isinstance(_r, dict) else str(_r or "")
    print("turn:", repr(r[:400]))
    ok = "owner" in r.lower()
    mgr.stop("owner", "portaltest")
    print("PORTALTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1

CPU_LEBENSZEICHEN_TICKS = int(os.environ.get("PN_CPU_LEBENSZEICHEN_TICKS", "30"))
CPU_LEBENSZEICHEN_LUECKE_S = float(os.environ.get("PN_CPU_LEBENSZEICHEN_LUECKE_S", "0.6"))


def _vmm_ticks(pid):
    try:
        with open("/proc/%d/stat" % int(pid), encoding="utf-8") as fh:
            teile = fh.read().rsplit(") ", 1)[-1].split()
        return int(teile[11]) + int(teile[12])
    except Exception:
        return None


def _vmm_brennt(cell):
    proc = getattr(cell, "proc", None)
    pid = getattr(proc, "pid", None) if proc is not None else None
    pid = pid or getattr(cell, "vmm_pid", None)
    if not pid:
        return False
    a = _vmm_ticks(pid)
    if a is None:
        return False
    time.sleep(CPU_LEBENSZEICHEN_LUECKE_S)
    b = _vmm_ticks(pid)
    if b is None:
        return False
    return (b - a) >= CPU_LEBENSZEICHEN_TICKS
