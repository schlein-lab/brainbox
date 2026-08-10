
import os, json, subprocess, time, threading
import re
import urllib.parse, urllib.request
import shutil as _shutil
from concurrent.futures import ThreadPoolExecutor as _TPE

_NEWLINK_JOBS = {}
_NEWLINK_LK = threading.Lock()

import portal_zustand as _zst
_zst.register("portal_routes_device._NEWLINK_JOBS", "snapshot", __name__, ref=_NEWLINK_JOBS,
              beschreibung="2FA-Link-Erzeugung im Hintergrund je (principal, vpn): POST startet, GET .../status holt ab; Verlust => Client startet den Job neu",
              neustart="verfaellt", schreiber="Link-Jobs unter _NEWLINK_LK")

HOME = None
HPC_VPN_BIN = None
PN_BIN = None
_ACTION_BUS = None
_DEVICE_REG = None
_DEVINPUT_AGENTS = None
_DEVINPUT_LK = None
_DISPLAY_REG = None
_HPC_DOWN_MSG = None
_SCAN_LOCK = None
_SCAN_TOKENS = None
_WORKER_REG = None
_adapter_ctx = None
_apikeys = None
_device_host_scan = None
_devinput_send = None
_hpc_auth_url = None
_hpc_netns_status = None
_hpc_ssh = None
_hpc_status = None
_hpc_submit = None
_kiosk_post = None
_known_principals = None
_netns_backends = None
_netns_uid = None
_netns_vpn = None
_netns_vpn_status_cached = None
_node_health_get = None
_policy = None
_prov_log = None
_uid_safe = None
_user_may_verb = None
_uservpn_allowed = None
_uservpn_grants = None
_vext = None
_vpn_registry = None
_vpn_status = None
pn_chanadapter = None
user_get = None

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

_VPN_CYC = {}
_zst.register("portal_routes_device._VPN_CYC", "cache", __name__, ref=_VPN_CYC,
              beschreibung="Entprellung des VPN-Auto-Rebind je sid (ts des letzten Rebind)",
              neustart="verfaellt", schreiber="Rebind-Pfad")

_HPC_VPN_ID_LEGACY = "hpc"

def _hpc_vpn_id():

    v = (os.environ.get("PN_HPC_VPN_ID") or os.environ.get("HPC_VPN_ID") or "").strip()
    if v:
        return v
    try:
        p = os.path.join(HOME or os.path.expanduser("~"), ".config", "brainbox-portal", "config.json")
        with open(p) as f:
            v = str((json.load(f) or {}).get("hpc_vpn_id") or "").strip()
        if v:
            return v
    except Exception:
        pass
    for sf in ("/etc/brainbox/site.conf", "/run/brainbox/site.env"):
        try:
            with open(sf) as f:
                for ln in f:
                    k, _, val = ln.strip().partition("=")
                    k = k.strip()
                    if k.startswith("export "):
                        k = k[7:].strip()
                    if k == "HPC_VPN_ID":
                        val = val.strip().strip('"').strip("'")
                        if val:
                            return val
        except Exception:
            pass
    return _HPC_VPN_ID_LEGACY

def _vpn_cell_reconcile(uid, sid):

    try:
        import pn_cell_session as _cs
        mgr = _cs.get_manager()

        sfx = "-" + str(sid) if sid else "\x00nie"

        acct_sfxs = []
        try:
            _nu = _netns_uid(uid)
            acct_sfxs = ["-%s-acct" % _nu, "-%s-default" % _nu]
        except Exception:
            acct_sfxs = []
        found = ""
        for d in ("/run/netns", "/var/run/netns"):
            try:
                for n in os.listdir(d):
                    if n.startswith("pnv-") and (n.endswith(sfx) or any(n.endswith(a) for a in acct_sfxs)):
                        found = n
                        break
            except OSError:
                pass
            if found:
                break
        if not found:
            return

        konto_tunnel = any(found.endswith(a) for a in (acct_sfxs or []))
        try:
            from pn_cell_lifecycle import braucht_ssh_bahn as _will_bahn
        except Exception:
            def _will_bahn(pol, vpn_on=False):
                return False

        kandidaten = []
        if konto_tunnel:
            for (p, s), c in list(getattr(mgr, "_cells", {}).items()):
                if p != uid or not s:
                    continue
                try:
                    if not c.alive() or getattr(c, "vpn_netns_active", ""):
                        continue
                    if not _will_bahn(getattr(c, "policy", None) or {}):
                        continue
                except Exception:
                    continue
                kandidaten.append(s)
        elif sid:
            c = mgr.get(uid, sid)
            if c is not None and c.alive() and not getattr(c, "vpn_netns_active", ""):
                kandidaten.append(sid)
        if not kandidaten:
            return

        now = time.time()
        faellig = [s for s in kandidaten if now - _VPN_CYC.get(s, 0) >= 180]
        for s in faellig:
            _VPN_CYC[s] = now
        if not faellig:
            return
        try:
            _prov_log("vpn.cell_rebind", uid,
                      json.dumps({"sids": faellig, "netns": found, "konto_tunnel": konto_tunnel}),
                      {"wire": "auto"})
        except Exception:
            pass

        def _cyc():

            try:
                import portal_session_svc as _psvc
            except Exception:
                return
            for s in faellig:
                try:
                    _psvc._cell_power(uid, s, False)
                    _psvc._cell_power(uid, s, True)
                except Exception:
                    pass
        threading.Thread(target=_cyc, daemon=True,
                         name="vpn-rebind-" + str(faellig[0])[:8]).start()
    except Exception:
        pass

class DeviceRoutes:
    def _api_hpc_status(self, query):
        uid = self._principal()
        if not (self._is_admin() or _user_may_verb(uid, "hpc_status")):
            return self._sess_json({"ok": False, "error": "nicht freigegeben"}, 403)
        nst = _hpc_netns_status(uid)
        connected = bool(nst.get("connected")) or bool(_hpc_status().get("connected"))
        q = urllib.parse.parse_qs(query or "")
        jid = re.sub(r"[^0-9_]", "", (q.get("job_id", [""])[0] or ""))[:20]
        out = None
        if connected:
            cmd = ("sacct -j %s --format=JobID,JobName%%20,State,Elapsed -n 2>/dev/null || squeue -j %s" % (jid, jid)) \
                  if jid else "squeue --me -o '%.10i %.20j %.8T %.10M' 2>/dev/null | head -25"
            res, err = _hpc_ssh(cmd, uid=uid)
            out = (res or {}).get("out") if not err else err
        return self._sess_json({"ok": True, "connected": connected, "via": ("netns" if nst.get("connected") else "operator"),
                                "jobs": out, "hint": None if connected else _HPC_DOWN_MSG})

    def _api_hpc_submit(self, raw):
        uid = self._principal()
        if not (self._is_admin() or _user_may_verb(uid, "hpc_submit")):
            return self._sess_json({"ok": False, "error": "nicht freigegeben"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        res, err = _hpc_submit(command=body.get("command"), script=body.get("script"),
                                  name=body.get("name"), uid=uid)
        _prov_log("hpc.submit", uid, json.dumps({"name": body.get("name"), "ok": err is None}), {"wire": "api"})
        if err:
            return self._sess_json({"ok": False, "error": err, "vpn_down": err == _HPC_DOWN_MSG},
                                   503 if err == _HPC_DOWN_MSG else 502)
        return self._sess_json({"ok": True, "job_id": res["job_id"]})

    def _api_devinput_agents(self):
        with _DEVINPUT_LK:
            names = sorted(_DEVINPUT_AGENTS.keys())
        return self._vext_json({"ok": True, "agents": names})

    def _api_devinput_send(self, raw):
        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        try: body = json.loads(raw or b"{}")
        except Exception: body = {}
        name = str(body.get("agent") or "")
        evs = body.get("events") or ([body.get("event")] if body.get("event") else [])
        if not isinstance(evs, list) or not evs:
            return self._vext_json({"ok": False, "error": "events fehlen"}, 400)
        ok, err = _devinput_send(name, evs[:64])
        return self._vext_json({"ok": True} if ok else {"ok": False, "error": err}, 200 if ok else 409)

    def _vpn_owner_gate(self, vpn=None):

        if self._apikey_entry() is not None:
            return False, "keine Maschinen-/Agent-Keys"
        ca = ((self.client_address[0] if self.client_address else "") or "").replace("::ffff:", "")
        if ca.startswith("127.") or ca == "::1":
            return False, "nur vom LAN-Client (nicht Loopback/Zelle)"
        if self._is_admin():
            return True, None
        if vpn is None:
            return False, "nur Owner/Admin"
        if not self.authed():
            return False, "nicht angemeldet"
        uid = self._principal()
        try:
            u = user_get(uid)
        except Exception:
            u = None
        role = ((u or {}).get("role") or "").strip().lower()
        if u is None or role in ("kid", "guest"):
            return False, "Kinder- und Gast-Konten koennen kein VPN verbinden"
        if _uservpn_allowed(uid, vpn):
            return True, None
        return False, "keine VPN-Berechtigung — Admin kann sie in Fairshare & Nutzer erteilen"

    def _hpc_vpn_bin_ready(self):

        b = HPC_VPN_BIN or ""
        p = b if (b and os.sep in b) else (_shutil.which(b) if b else None)
        if p and os.path.exists(p) and os.access(p, os.X_OK):
            return True, None
        return False, ("Diese Box hat keinen Cluster-VPN-Client installiert — "
                       "die VPN-Steuerung ist hier nicht verfuegbar.")

    _hpc_bin_ready = _hpc_vpn_bin_ready

    def _vpn_unavailable(self, msg):
        return self._vext_json({"ok": False, "state": "unavailable", "error": msg}, 501)

    def _connect_worker(self, key, vid, principal, uid, vsid):

        def phase(p):
            with _NEWLINK_LK:
                j = _NEWLINK_JOBS.get(key) or {}
                if j.get("state") == "running":
                    j["phase"] = p
                    _NEWLINK_JOBS[key] = j
        out, err = {}, None
        try:
            phase("Netz-Kammer wird angehaengt")

            _netns_vpn("attach", uid, vid, timeout=15, session=vsid)
            phase("Status wird geprueft")
            st = _netns_vpn("status", uid, vid, timeout=20, session=vsid)
            if st.get("connected"):
                _vpn_cell_reconcile(principal, vsid)
                out = {"state": "connected"}
            elif st.get("url"):
                out = {"state": "ready", "link": st["url"], "link_kind": "saml",
                       "cell": st.get("stream_cell")}
            else:
                if not st.get("ns_exists"):
                    phase("Netz-Kammer wird gebaut")
                    _netns_vpn("up", uid, vid, timeout=45, session=vsid)
                phase("Anmelde-Fenster wird gestartet")
                r = _netns_vpn("login", uid, vid, timeout=90, session=vsid)
                if r.get("connected"):
                    _vpn_cell_reconcile(principal, vsid)
                    out = {"state": "connected", "cell": r.get("stream_cell")}
                elif r.get("url"):

                    out = {"state": "ready", "link": r["url"], "link_kind": "saml",
                           "cell": r.get("stream_cell")}
                elif r.get("in_progress"):

                    phase("warte auf 2FA-Bestaetigung")
                    out = {"state": "error", "cell": r.get("stream_cell")}
                    err = ("Die 2FA wurde nicht bestaetigt (Frist abgelaufen) — bitte "
                           "„Neuen 2FA-Link“ erzeugen.")
                    for _ in range(40):
                        time.sleep(15)
                        s2 = _netns_vpn("status", uid, vid, timeout=20, session=vsid)
                        if s2.get("connected"):
                            _vpn_cell_reconcile(principal, vsid)
                            out, err = {"state": "connected", "cell": r.get("stream_cell")}, None
                            break
                        if s2.get("url"):
                            out, err = {"state": "ready", "link": s2["url"], "link_kind": "saml",
                                        "cell": s2.get("stream_cell") or r.get("stream_cell")}, None
                            break
                else:
                    err = r.get("error") or "keine SAML-URL erhalten"
                    out = {"cell": r.get("stream_cell")}
        except Exception as e:
            err = "Verbindung fehlgeschlagen (%s)." % e.__class__.__name__
        with _NEWLINK_LK:
            j = _NEWLINK_JOBS.get(key) or {}
            if err:
                j.update({"state": "error", "error": err, "cell": out.get("cell"), "done": time.time()})
            else:
                j.update({k: v for k, v in out.items() if v is not None})
                j["done"] = time.time()
            _NEWLINK_JOBS[key] = j

    def _api_vpn_request(self, raw):

        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        vid = str(body.get("vpn") or _hpc_vpn_id())
        ok, why = self._vpn_owner_gate(vid)
        if not ok:
            return self._vext_json({"ok": False, "state": "forbidden",
                                    "error": "VPN-Start verweigert: %s" % why}, 403)
        if vid in _netns_backends():
            uid = _netns_uid(self._principal())
            _vsid = str(body.get("session") or "").strip() or None
            key = (self._principal(), vid)
            with _NEWLINK_LK:
                j = _NEWLINK_JOBS.get(key)
                if j and j.get("state") == "running" and (time.time() - j.get("t0", 0)) < 900:

                    return self._vext_json({"ok": True, "vpn": vid, "state": "generating",
                                            "elapsed": int(time.time() - j["t0"]),
                                            "phase": j.get("phase"), "auth_url": j.get("link"),
                                            "stream_cell": j.get("cell"),
                                            "note": "Ein Anmelde-Vorgang laeuft bereits — bitte kurz warten."})
                _NEWLINK_JOBS[key] = {"state": "running", "t0": time.time(), "kind": "connect",
                                      "phase": "Netz-Kammer wird angehaengt"}
            _prov_log("vpn.request", self._principal(),
                      json.dumps({"vpn": vid, "backend": "netns", "session": _vsid}), {"wire": "api"})
            threading.Thread(target=self._connect_worker,
                             args=(key, vid, self._principal(), uid, _vsid), daemon=True).start()
            return self._vext_json({"ok": True, "vpn": vid, "state": "generating", "elapsed": 0,
                                    "phase": "Netz-Kammer wird angehaengt",
                                    "note": "Verbindung wird aufgebaut — Fortschritt und Link erscheinen hier."})
        _bin_ok, _bin_msg = self._hpc_vpn_bin_ready()
        if not _bin_ok:
            return self._vpn_unavailable(_bin_msg)
        if _hpc_status().get("connected"):
            return self._vext_json({"ok": True, "vpn": vid, "state": "connected"})
        url = _hpc_auth_url()
        if url:
            return self._vext_json({"ok": True, "vpn": vid, "state": "auth_pending", "auth_url": url})

        try:
            env = dict(os.environ); env["HV_OPERATOR"] = "1"
            subprocess.Popen([HPC_VPN_BIN, "connect"], env=env,
                             stdout=open("/tmp/vpn-request.log", "ab"), stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        except Exception as e:
            return self._vext_json({"ok": False, "state": "error",
                                    "error": "VPN-Verbindung konnte nicht gestartet werden (%s)."
                                             % e.__class__.__name__}, 500)
        _prov_log("vpn.request", self._principal(), json.dumps({"vpn": vid}), {"wire": "api"})
        for _ in range(8):
            url = _hpc_auth_url()
            if url:
                return self._vext_json({"ok": True, "vpn": vid, "state": "auth_pending", "auth_url": url})
            time.sleep(1)
        return self._vext_json({"ok": True, "vpn": vid, "state": "connecting",
                                "note": "Aufbau gestartet — SSO-Link folgt via /api/vpn/status"})

    def _api_vpn_cancel(self, raw):
        try:
            _cbody = json.loads(raw or b"{}")
        except Exception:
            _cbody = {}
        _cvid = str(_cbody.get("vpn") or _hpc_vpn_id())
        ok, why = self._vpn_owner_gate(_cvid)
        if not ok:
            return self._vext_json({"ok": False, "state": "forbidden", "error": why}, 403)
        if _cvid in _netns_backends():
            _cvsid = str(_cbody.get("session") or "").strip() or None
            _netns_vpn("down", _netns_uid(self._principal()), _cvid, timeout=30, session=_cvsid)
            _prov_log("vpn.cancel", self._principal(), json.dumps({"vpn": _cvid, "session": _cvsid}), {"wire": "api"})
            return self._vext_json({"ok": True, "state": "disconnecting"})
        _bin_ok, _bin_msg = self._hpc_vpn_bin_ready()
        if not _bin_ok:
            return self._vpn_unavailable(_bin_msg)
        try:
            env = dict(os.environ); env["HV_OPERATOR"] = "1"
            subprocess.Popen([HPC_VPN_BIN, "down"], env=env, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)
        except Exception as e:
            return self._vext_json({"ok": False, "state": "error",
                                    "error": "VPN-Trennung konnte nicht gestartet werden (%s)."
                                             % e.__class__.__name__}, 500)
        _prov_log("vpn.cancel", self._principal(), "{}", {"wire": "api"})
        return self._vext_json({"ok": True, "state": "disconnecting"})

    def _api_vpn_restart(self, raw):

        try:
            b = json.loads(raw or b"{}")
        except Exception:
            b = {}
        vid = str(b.get("vpn") or _hpc_vpn_id())
        ok, why = self._vpn_owner_gate(vid)
        if not ok:
            return self._vext_json({"ok": False, "state": "forbidden", "error": why}, 403)
        vsid = str(b.get("session") or "").strip() or None
        if vid in _netns_backends():

            _netns_vpn("down", _netns_uid(self._principal()), vid, timeout=30, session=vsid, force=True)
            _prov_log("vpn.restart", self._principal(), json.dumps({"vpn": vid, "session": vsid}), {"wire": "api"})
            return self._vext_json({"ok": True, "state": "reset",
                                    "note": "VPN-/Cisco-Stack zurueckgesetzt — jetzt neu verbinden."})
        _bin_ok, _bin_msg = self._hpc_vpn_bin_ready()
        if not _bin_ok:
            return self._vpn_unavailable(_bin_msg)
        try:
            env = dict(os.environ); env["HV_OPERATOR"] = "1"
            subprocess.Popen([HPC_VPN_BIN, "down"], env=env, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)
        except Exception as e:
            return self._vext_json({"ok": False, "state": "error",
                                    "error": "VPN-Neustart konnte nicht gestartet werden (%s)."
                                             % e.__class__.__name__}, 500)
        _prov_log("vpn.restart", self._principal(), "{}", {"wire": "api"})
        return self._vext_json({"ok": True, "state": "reset", "note": "VPN-Stack zurueckgesetzt."})

    def _newlink_worker(self, key, vid, nuid, helper):

        r, err = {}, None
        try:
            pr = subprocess.run(["bash", helper, "--uid", str(nuid), "--vpn", vid, "--no-wait", "--quiet"],
                                capture_output=True, text=True, timeout=300)
            out = (pr.stdout or "").strip().splitlines()
            try:
                r = json.loads(out[-1]) if out else {}
            except Exception:
                r = {}
        except subprocess.TimeoutExpired:
            err = "Zeitueberschreitung — bitte erneut versuchen."
        except Exception as e:
            err = "2FA-Link fehlgeschlagen (%s)." % e.__class__.__name__
        with _NEWLINK_LK:
            j = _NEWLINK_JOBS.get(key) or {}
            if not err and r.get("ok") and r.get("connected") and not r.get("link"):

                j.update({"state": "connected", "note": r.get("note") or "Der Tunnel steht bereits.",
                          "cell": r.get("cell"), "done": time.time()})
            elif err or not r.get("ok") or not r.get("link"):
                j.update({"state": "error", "error": err or r.get("error") or "Es kam kein 2FA-Link zurueck.",
                          "cell": r.get("cell"), "done": time.time()})
            else:

                j.update({"state": "ready", "link": r["link"], "link_kind": "2fa",
                          "cell": r.get("cell"), "done": time.time()})
            _NEWLINK_JOBS[key] = j

    def _api_vpn_newlink(self, raw):

        try:
            b = json.loads(raw or b"{}")
        except Exception:
            b = {}
        vid = str(b.get("vpn") or "hpc")
        ok, why = self._vpn_owner_gate(vid)
        if not ok:
            return self._vext_json({"ok": False, "state": "forbidden", "error": why}, 403)
        if vid not in _netns_backends():
            return self._vpn_unavailable("Neue 2FA-Links gibt es nur fuer VPNs mit netns-Login-Stream.")
        helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vpn-2fa")
        if not os.path.isfile(helper):
            return self._vpn_unavailable("2FA-Helfer ist auf dieser Box nicht installiert.")
        key = (self._principal(), vid)
        with _NEWLINK_LK:
            j = _NEWLINK_JOBS.get(key)
            if j and j.get("state") == "running" and (time.time() - j.get("t0", 0)) < 900:
                return self._vext_json({"ok": True, "state": "generating", "vpn": vid,
                                        "elapsed": int(time.time() - j["t0"]), "phase": j.get("phase"),
                                        "auth_url": j.get("link"), "stream_cell": j.get("cell"),
                                        "note": "Es laeuft bereits ein Anmelde-Vorgang — bitte kurz warten."})
            _NEWLINK_JOBS[key] = {"state": "running", "t0": time.time(), "kind": "newlink",
                                  "phase": "Anmelde-Sitzung wird gebaut"}
        _prov_log("vpn.newlink", self._principal(), json.dumps({"vpn": vid}), {"wire": "api"})
        threading.Thread(target=self._newlink_worker, args=(key, vid, _netns_uid(self._principal()), helper),
                         daemon=True).start()
        return self._vext_json({"ok": True, "state": "generating", "vpn": vid, "elapsed": 0,
                                "note": "Neue Anmelde-Sitzung wird gebaut — der Link erscheint hier von selbst."})

    def _api_vpn_newlink_status(self, query):

        q = urllib.parse.parse_qs(query or "")
        vid = (q.get("vpn", ["hpc"])[0] or "hpc")
        ok, why = self._vpn_owner_gate(vid)
        if not ok:
            return self._vext_json({"ok": False, "state": "forbidden", "error": why}, 403)
        with _NEWLINK_LK:
            j = dict(_NEWLINK_JOBS.get((self._principal(), vid)) or {})
        st = j.get("state") or "idle"
        out = {"ok": True, "state": "generating" if st == "running" else st, "vpn": vid,
               "kind": j.get("kind"), "phase": j.get("phase"),
               "elapsed": int(time.time() - j["t0"]) if j.get("t0") else 0}
        if j.get("link"):
            out["auth_url"] = j["link"]

            out["link_kind"] = j.get("link_kind") or "saml"
        if st == "error":
            out["error"] = j.get("error")
        if j.get("cell"):
            out["stream_cell"] = j["cell"]
        return self._vext_json(out)

    _api_vpn_job = _api_vpn_newlink_status

    def _api_channels_enroll(self, raw):

        if pn_chanadapter is None:
            return self._sess_json({"ok": False, "error": "channels unavailable"}, 503)
        body = self._json_obj()
        if body is None:
            return
        payload, code = pn_chanadapter.enroll(_adapter_ctx(), self._principal(),
                                              str(body.get("channel") or "telegram"),
                                              {"token": body.get("token"), "phone_id": body.get("phone_id")})
        return self._sess_json(payload, code)

    def _api_channels_bind(self, raw):

        if pn_chanadapter is None:
            return self._sess_json({"ok": False, "error": "channels unavailable"}, 503)
        body = self._json_obj()
        if body is None:
            return
        payload, code = pn_chanadapter.set_chat(_adapter_ctx(), self._principal(),
                                                str(body.get("channel") or "telegram"), body.get("chat_id"),
                                                body.get("inbound"))
        return self._sess_json(payload, code)

    def _api_channels_disable(self, raw):

        if pn_chanadapter is None:
            return self._sess_json({"ok": False, "error": "channels unavailable"}, 503)
        body = self._json_obj()
        if body is None:
            return
        payload, code = pn_chanadapter.disable(_adapter_ctx(), self._principal(),
                                               str(body.get("channel") or "telegram"))
        return self._sess_json(payload, code)

    def _api_channels_status(self):

        if pn_chanadapter is None:
            return self._sess_json({"ok": False, "error": "channels unavailable"}, 503)
        payload, code = pn_chanadapter.status(_adapter_ctx(), self._principal())
        return self._sess_json(payload, code)

    def _api_channels_verbose(self, raw):

        if pn_chanadapter is None or not hasattr(pn_chanadapter, "set_verbose"):
            return self._sess_json({"ok": False, "error": "channels unavailable"}, 503)
        body = self._json_obj()
        if body is None:
            return
        payload, code = pn_chanadapter.set_verbose(_adapter_ctx(), self._principal(),
                                                   str(body.get("channel") or "telegram"),
                                                   bool(body.get("on")))
        return self._sess_json(payload, code)

    def _api_uservpn(self):

        uid = self._principal()
        vpns = []
        try:
            for e in _vpn_registry():
                vpns.append({"id": e.get("id"), "name": e.get("name") or e.get("id"),
                             "backend": e.get("backend")})
        except Exception:
            vpns = []
        allowed = [v["id"] for v in vpns if _uservpn_allowed(uid, v["id"])]
        out = {"ok": True, "principal": uid, "allowed": allowed, "vpns": vpns,
               "is_admin": bool(self._is_admin())}
        if self._is_admin():
            out["grants"] = _uservpn_grants()
            try:
                out["users"] = sorted(_known_principals())
            except Exception:
                out["users"] = []
        return self._sess_json(out)

    def _devices_live_page(self):
        p = os.path.join(os.path.dirname(os.path.realpath(__file__)), "devices_live.html")
        try:
            return self.send_html(open(p, "rb").read())
        except Exception:
            return self.send_html("devices view not deployed", 404)

    def _api_client_actions(self, query):

        if _ACTION_BUS is None:
            return self._vext_json({"ok": True, "next": 0, "actions": []})
        q = urllib.parse.parse_qs(query)
        try:
            since = int(q.get("since", ["0"])[0])
        except Exception:
            since = 0
        got = _ACTION_BUS.since(_uid_safe(self._principal()), max(0, since))
        return self._vext_json({"ok": True, "next": got["next"], "actions": got["actions"]})

    def _api_vpn(self):

        out = []

        netns_st = {}
        try:
            uid = self._principal()
            _probe = _netns_vpn_status_cached or (lambda u, v, session=None:
                                                  _netns_vpn("status", u, v, timeout=20, session=session))
            backends = sorted(_netns_backends())
            if backends:

                with _TPE(max_workers=max(1, min(len(backends), 4))) as tp:
                    for _nv, _st in zip(backends, tp.map(
                            lambda v: _probe(_netns_uid(uid), v, session=None), backends)):
                        netns_st[_nv] = _st if isinstance(_st, dict) else {}
        except Exception:
            pass
        reg = list(_vpn_registry())

        cmds = [(e.get("id"), e.get("status_cmd")) for e in reg if e.get("status_cmd")]
        st_by_vid = {}
        if cmds:
            with _TPE(max_workers=max(1, min(len(cmds), 4))) as tp:
                for (_vid, _), _st in zip(cmds, tp.map(lambda c: _vpn_status(c[0], c[1]), cmds)):
                    st_by_vid[_vid] = _st
        for e in reg:
            vid = e.get("id")
            item = {"id": vid, "name": e.get("name"), "type": e.get("type", "vpn"),
                    "endpoint": e.get("gateway"), "gateway": e.get("gateway"),
                    "purpose": e.get("purpose"), "user": e.get("user"),
                    "operator_gated": bool(e.get("operator_gated", True)),
                    "configured": True, "active": False, "status": "down", "detail": {}}
            if e.get("status_cmd"):
                st = st_by_vid.get(vid)
                if st is not None:
                    item["detail"] = st
                    item["active"] = bool(st.get("connected"))
                    item["status"] = "up" if item["active"] else "down"
                    item["reachable"] = True
                else:
                    item["reachable"] = False
                    item["status"] = "unknown"
            nst = netns_st.get(vid)
            if nst is not None:
                if nst.get("connected"):
                    item["active"] = True
                    item["status"] = "up"
                    item["reachable"] = True
                elif nst.get("error") and not e.get("status_cmd"):

                    item["status"] = "unknown"
                    item["reachable"] = False
                    item["detail"] = {"error": nst.get("error")}
            out.append(item)
        return self._vext_json({"ok": True, "vpns": out, "now": time.time()})

    def _api_vpn_status(self, query):

        q = urllib.parse.parse_qs(query)
        session = q.get("session", [None])[0]
        active = []
        probe_errors = {}

        cmds = [(e.get("id"), e.get("status_cmd")) for e in _vpn_registry() if e.get("status_cmd")]
        if cmds:
            with _TPE(max_workers=max(1, min(len(cmds), 4))) as tp:
                for (_vid, _), st in zip(cmds, tp.map(lambda c: _vpn_status(c[0], c[1]), cmds)):
                    if st and st.get("connected"):
                        active.append(_vid)
                    elif st is None:
                        probe_errors[_vid] = "status_cmd fehlgeschlagen"
        _netns_url = None
        _netns_stream = None
        _probe = _netns_vpn_status_cached or (lambda u, v, session=None:
                                              _netns_vpn("status", u, v, timeout=20, session=session))
        backends = sorted(_netns_backends())
        if backends:
            with _TPE(max_workers=max(1, min(len(backends), 4))) as tp:
                results = list(tp.map(lambda v: _probe(_netns_uid(self._principal()), v,
                                                       session=(session or None)), backends))
            for _nv, _ns in zip(backends, results):
                _ns = _ns if isinstance(_ns, dict) else {}
                if _ns.get("connected"):
                    active.append(_nv)
                    _vpn_cell_reconcile(self._principal(), session)
                elif _ns.get("url"):
                    _netns_url = _ns.get("url"); _netns_stream = _ns.get("stream_cell")
                elif _ns.get("error"):

                    probe_errors[_nv] = _ns.get("error")
        _au = _hpc_auth_url() or _netns_url
        out = {"ok": True, "session": session, "active": active,
               "per_session": bool(session), "auth_url": _au, "stream_cell": _netns_stream,
               "state": ("connected" if active else ("auth_pending" if _au else "down")),
               "now": time.time()}
        if probe_errors:
            out["probe_errors"] = probe_errors
        return self._vext_json(out)

    def _api_devices(self):

        if _DEVICE_REG is None:
            return self._vext_json({"ok": True, "devices": (_policy.device_roster() if _policy else [])})
        return self._vext_json({"ok": True, "devices": _DEVICE_REG.list_all()})

    def _api_device_register(self, raw):

        if _DEVICE_REG is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "admin only"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        did = re.sub(r"[^A-Za-z0-9_.:-]", "", str(body.get("id") or ""))[:64]
        if not did:
            return self._vext_json({"ok": False, "error": "id required"}, 400)
        kind = re.sub(r"[^a-z-]", "", str(body.get("kind") or "device"))[:24] or "device"
        tr = body.get("transport") if isinstance(body.get("transport"), dict) else None
        rec = _DEVICE_REG.register(did, str(body.get("name") or did)[:80], kind, transport=tr,
                                   location=str(body.get("location") or "")[:40],
                                   driver=(str(body.get("driver") or "") or None))
        _prov_log("device.register", self._principal(), json.dumps({"id": did, "kind": kind}), {"wire": "api"})
        return self._vext_json({"ok": True, "device": rec})

    def _api_device_label(self, raw):

        if _DEVICE_REG is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "admin only"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        did = str(body.get("id") or body.get("device") or "")
        rec = _DEVICE_REG.label(did, str(body.get("label") or "")[:80])
        if rec is None:
            return self._vext_json({"ok": False, "error": "unknown device"}, 404)
        _prov_log("device.label", self._principal(), json.dumps({"id": did}), {"wire": "api"})
        return self._vext_json({"ok": True, "device": rec})

    def _api_device_forget(self, raw):

        if _DEVICE_REG is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "admin only"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        did = str(body.get("id") or body.get("device") or "")

        import portal_delete_guard as _dg
        _2fa_ok, _2fa_resp = _dg.require_2fa(self._principal(),
                                             str(body.get("totp") or body.get("code") or ""),
                                             None, prov_log=_prov_log, action="device.forget")
        if not _2fa_ok:
            return self._vext_json(_2fa_resp, 403)
        if not _DEVICE_REG.forget(did):
            return self._vext_json({"ok": False, "error": "unknown device %r" % did}, 404)
        _prov_log("device.forget", self._principal(), json.dumps({"id": did}), {"wire": "api"})
        return self._vext_json({"ok": True, "forgot": did})

    def _api_device_unpair(self, raw):

        if _DEVICE_REG is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        did = str(body.get("id") or body.get("device") or "")
        rec = _DEVICE_REG.get(did)
        if not rec:
            return self._vext_json({"ok": False, "error": "unknown device %r" % did}, 404)
        if not (self._is_admin() or rec.get("principal") == self._principal()):
            return self._vext_json({"ok": False, "error": "not allowed"}, 403)

        import portal_delete_guard as _dg
        _2fa_ok, _2fa_resp = _dg.require_2fa(self._principal(),
                                             str(body.get("totp") or body.get("code") or ""),
                                             None, prov_log=_prov_log, action="device.unpair")
        if not _2fa_ok:
            return self._vext_json(_2fa_resp, 403)
        kid = rec.get("apikey_id")

        key_revoked = None
        if kid:
            key_revoked = bool(_apikeys.revoke(kid)) if _apikeys is not None else False
        with _DEVICE_REG._lock:
            new = _DEVICE_REG._put(did, {"hidden": True, "state": "revoked", "revoked": True})
        _prov_log("device.unpair", self._principal(),
                  json.dumps({"id": did, "kid": kid, "key_revoked": key_revoked}), {"wire": "api"})
        if not (new or {}).get("revoked"):
            return self._vext_json({"ok": False, "error": "Geraet konnte nicht abgemeldet werden.",
                                    "key_revoked": key_revoked}, 500)
        out = {"ok": True, "unpaired": did, "key_revoked": key_revoked, "device": new}
        if kid and key_revoked is False:
            out["note"] = ("Geraet abgemeldet, aber sein Schluessel war nicht (mehr) im Schluesselbund — "
                           "bitte pruefen.")
        return self._vext_json(out)

    def _api_device_hide(self, raw):

        if _DEVICE_REG is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "admin only"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        did = str(body.get("id") or ""); hide = bool(body.get("hidden", True))
        if not _DEVICE_REG.set_hidden(did, hide):
            return self._vext_json({"ok": False, "error": "unknown device"}, 404)
        return self._vext_json({"ok": True, "id": did, "hidden": hide})

    def _api_device_attach(self, raw):

        if _DEVICE_REG is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "admin only"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        did = str(body.get("id") or "")
        sid = re.sub(r"[^a-z0-9]", "", str(body.get("sid") or ""))[:16]
        if not did or not sid:
            return self._vext_json({"ok": False, "error": "id+sid noetig"}, 400)
        rec = _DEVICE_REG.attach(did, sid)
        if rec is None:
            return self._vext_json({"ok": False, "error": "unknown device"}, 404)
        _prov_log("device.attach", self._principal(), json.dumps({"id": did, "sid": sid}), {"wire": "api"})
        return self._vext_json({"ok": True, "device": rec})

    def _api_device_detach(self, raw):

        if _DEVICE_REG is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "admin only"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        did = str(body.get("id") or "")
        sid = re.sub(r"[^a-z0-9]", "", str(body.get("sid") or ""))[:16]
        rec = _DEVICE_REG.detach(did, sid)
        if rec is None:
            return self._vext_json({"ok": False, "error": "unknown device"}, 404)
        _prov_log("device.detach", self._principal(), json.dumps({"id": did, "sid": sid}), {"wire": "api"})
        return self._vext_json({"ok": True, "device": rec})

    def _api_device_control(self, raw):

        if _DEVICE_REG is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        uid = self._principal()
        sid = re.sub(r"[^a-z0-9]", "", str(body.get("sid") or ""))[:16]
        action = str(body.get("action") or "list")
        if not sid:
            return self._vext_json({"ok": False, "error": "sid noetig"}, 400)
        mine = _DEVICE_REG.for_session(sid)
        if action == "list":
            return self._vext_json({"ok": True, "devices": [
                {"id": d.get("id"), "name": d.get("label") or d.get("name"), "kind": d.get("kind"),
                 "addr": (d.get("transport") or {}).get("addr"), "state": d.get("state")} for d in mine]})
        did = str(body.get("id") or "")
        dev = next((d for d in mine if d.get("id") == did), None)
        if dev is None:
            return self._vext_json({"ok": False, "error": "Geraet ist dieser Session nicht zugeordnet"}, 403)
        if action == "info":
            return self._vext_json({"ok": True, "device": dev})
        if action in ("show", "cast"):
            url = str(body.get("url") or "")
            if not url:
                return self._vext_json({"ok": False, "error": "url noetig"}, 400)
            if _DISPLAY_REG is None:
                return self._vext_json({"ok": False, "error": "Anzeige-Lane nicht verfuegbar"}, 503)
            target = (_DISPLAY_REG.resolve(dev.get("label") or dev.get("name") or did)
                      or _DISPLAY_REG.resolve((dev.get("transport") or {}).get("addr") or "") or did)
            res, err = _DISPLAY_REG.show(uid, target, {"kind": "url", "url": url}, kiosk_post=_kiosk_post)
            if err:
                return self._vext_json({"ok": False, "error": err}, 400)
            _prov_log("device.control", uid, json.dumps({"sid": sid, "id": did, "action": "show"}), {"wire": "api"})
            return self._vext_json({"ok": True, "result": res})
        if action == "stop":
            if _DISPLAY_REG is None:
                return self._vext_json({"ok": False, "error": "Anzeige-Lane nicht verfuegbar"}, 503)
            target = _DISPLAY_REG.resolve(dev.get("label") or dev.get("name") or did) or did
            res, err = _DISPLAY_REG.restore_idle(uid, target, kiosk_post=_kiosk_post)
            if err:
                return self._vext_json({"ok": False, "error": err}, 400)
            return self._vext_json({"ok": True, "result": res})
        return self._vext_json({"ok": False, "error": "unbekannte Aktion %r" % action}, 400)

    def _api_device_scan(self, raw):

        if _DEVICE_REG is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "admin only"}, 403)
        methods = []

        host_merged = 0
        _t0 = time.time()
        try:
            host_merged = _device_host_scan(angefordert=True)
            methods.append({"method": "host-neighbors", "ran": True, "merged": host_merged,
                            "secs": round(time.time() - _t0, 2)})
        except Exception as e:
            methods.append({"method": "host-neighbors", "ran": False, "merged": 0,
                            "reason": "Nachbar-Scan fehlgeschlagen (%s)." % e.__class__.__name__})

        script = os.path.join(HOME or "", ".local", "bin", "device_discover.py")
        missing = [n for n, p in (("pn", PN_BIN), ("device_discover.py", script))
                   if not p or not os.path.exists(p)]
        mdns_merged = 0
        if missing:
            methods.append({"method": "mdns-ssdp", "ran": False, "merged": 0,
                            "reason": "Uebersprungen — auf dieser Box nicht installiert: %s."
                                      % ", ".join(missing)})
        else:
            _t1 = time.time()
            pr, fail = None, None
            try:

                pr = subprocess.run([PN_BIN, "run", "--mem", "128", "--latency", "realtime",
                                     "--timeout", "40", "--tag", "device.discover", "--",
                                     "/usr/bin/python3", script, "--dur", "6", "--angefordert"],
                                    capture_output=True, text=True, timeout=90)
            except subprocess.TimeoutExpired:
                fail = "mDNS/SSDP-Job hat das Zeitlimit (90 s) ueberschritten."
            except Exception as e:
                fail = "mDNS/SSDP-Job nicht startbar (%s)." % e.__class__.__name__
            if pr is not None and pr.returncode != 0:
                fail = "mDNS/SSDP-Job endete mit Code %d." % pr.returncode
            payload = None
            for ln in ((pr.stdout if pr else "") or "").splitlines():
                if ln.startswith("DEVICES_JSON="):
                    try:
                        payload = json.loads(ln[len("DEVICES_JSON="):])
                    except Exception:
                        fail = fail or "mDNS/SSDP-Job lieferte unlesbare Ergebnisse."
                    break
            if payload is not None:
                try:
                    mdns_merged = _DEVICE_REG.merge_discovered(payload)
                except Exception as e:
                    fail = "Ergebnisse nicht uebernehmbar (%s)." % e.__class__.__name__
                    mdns_merged = 0
            elif not fail:
                fail = "mDNS/SSDP-Job lieferte kein Ergebnis."
            if fail and mdns_merged == 0:
                methods.append({"method": "mdns-ssdp", "ran": False, "merged": 0, "reason": fail,
                                "secs": round(time.time() - _t1, 2)})
            else:
                methods.append({"method": "mdns-ssdp", "ran": True, "merged": mdns_merged,
                                "secs": round(time.time() - _t1, 2)})

        merged = host_merged + mdns_merged
        ran = [m["method"] for m in methods if m.get("ran")]
        skipped = [m["method"] for m in methods if not m.get("ran")]
        note = None
        if skipped:
            note = ("Teil-Scan: %s gelaufen, %s uebersprungen. Ohne mDNS/SSDP behalten neu gefundene "
                    "Geraete ihren IP-Namen." % (", ".join(ran) or "nichts", ", ".join(skipped)))
        _prov_log("device.scan", self._principal(),
                  json.dumps({"merged": merged, "host": host_merged, "mdns": mdns_merged,
                              "ran": ran, "skipped": skipped}), {"wire": "api"})
        return self._vext_json({"ok": True, "scanned": bool(ran), "complete": not skipped,
                                "merged": merged, "methods": methods, "note": note,
                                "devices": _DEVICE_REG.list_all()})

    def _api_device_discovered(self, raw):

        if _DEVICE_REG is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        ip = self.client_address[0] if self.client_address else "?"
        if ip not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return self._vext_json({"ok": False, "error": "loopback only"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        tok = str(body.get("token") or "")
        with _SCAN_LOCK:
            exp = _SCAN_TOKENS.pop(tok, None)
        if not exp or exp < time.time():
            return self._vext_json({"ok": False, "error": "bad/expired token"}, 403)
        n = _DEVICE_REG.merge_discovered(body.get("devices") or [])
        return self._vext_json({"ok": True, "merged": n})

    def _device_scan_cfg_path(self):
        return os.path.join(HOME, ".local", "share", "brainbox-portal", "device_scan.json")

    def _device_scan_cfg_read(self):

        cfg = {"enabled": True, "aktiv_suchen": False, "interval_min": 1, "paused_until": None}
        try:
            with open(self._device_scan_cfg_path()) as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                cfg["enabled"] = bool(raw.get("enabled", True))
                cfg["aktiv_suchen"] = bool(raw.get("aktiv_suchen", False))
                try:
                    cfg["interval_min"] = max(1, int(raw.get("interval_min", 1)))
                except Exception:
                    pass
                pu = raw.get("paused_until")
                try:
                    cfg["paused_until"] = float(pu) if pu else None
                except Exception:
                    cfg["paused_until"] = None
        except Exception:
            pass
        return cfg

    def _device_scan_cfg_state(self, cfg):

        now = time.time()
        paused = bool(cfg["paused_until"] and now < cfg["paused_until"])
        out = dict(cfg)
        out["active"] = bool(cfg["enabled"]) and not paused
        out["paused_remaining_s"] = int(cfg["paused_until"] - now) if paused else 0
        return out

    def _api_device_scan_config_get(self):

        return self._vext_json({"ok": True,
                                "config": self._device_scan_cfg_state(self._device_scan_cfg_read())})

    def _api_device_scan_config_set(self, raw):

        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "admin only"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        if not isinstance(body, dict):
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        cfg = self._device_scan_cfg_read()
        changed = {}
        if "enabled" in body:
            cfg["enabled"] = bool(body.get("enabled"))
            changed["enabled"] = cfg["enabled"]
        if "aktiv_suchen" in body:

            cfg["aktiv_suchen"] = bool(body.get("aktiv_suchen"))
            changed["aktiv_suchen"] = cfg["aktiv_suchen"]
        if body.get("interval_min") is not None:
            try:
                iv = int(body.get("interval_min"))
            except Exception:
                return self._vext_json({"ok": False, "error": "interval_min must be an integer >= 1"}, 400)
            if iv < 1:
                return self._vext_json({"ok": False, "error": "interval_min must be an integer >= 1"}, 400)
            cfg["interval_min"] = min(iv, 7 * 24 * 60)
            changed["interval_min"] = cfg["interval_min"]
        if body.get("pause_hours") is not None:
            try:
                ph = float(body.get("pause_hours"))
            except Exception:
                return self._vext_json({"ok": False, "error": "pause_hours must be a number >= 0"}, 400)
            if ph < 0:
                return self._vext_json({"ok": False, "error": "pause_hours must be a number >= 0"}, 400)
            cfg["paused_until"] = (time.time() + ph * 3600.0) if ph > 0 else None
            changed["paused_until"] = cfg["paused_until"]
        if not changed:
            return self._vext_json({"ok": False,
                                    "error": "nothing to change (enabled/interval_min/pause_hours)"}, 400)
        path = self._device_scan_cfg_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"enabled": cfg["enabled"], "aktiv_suchen": cfg["aktiv_suchen"],
                           "interval_min": cfg["interval_min"],
                           "paused_until": cfg["paused_until"]}, f, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            return self._vext_json({"ok": False, "error": "write failed: %s" % str(e)[:120]}, 500)
        _prov_log("device.scan_config", self._principal(), json.dumps(changed), {"wire": "api"})
        return self._vext_json({"ok": True, "config": self._device_scan_cfg_state(cfg)})

    def _api_workers(self):

        if _WORKER_REG is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "admin only"}, 403)
        return self._vext_json({"ok": True, "workers": _WORKER_REG.list()})

    def _api_worker_register(self, raw):

        if _WORKER_REG is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "admin only"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        wid = re.sub(r"[^A-Za-z0-9_-]", "", str(body.get("id") or ""))[:40]
        endpoint = str(body.get("endpoint") or "")
        token = str(body.get("token") or "")
        if not wid or not endpoint.startswith(("http://", "https://")):
            return self._vext_json({"ok": False, "error": "id + http(s) endpoint required"}, 400)
        if not token:
            return self._vext_json({"ok": False, "error": "token required"}, 400)
        caps = body.get("caps") if isinstance(body.get("caps"), dict) else None
        rec = _WORKER_REG.register(wid, str(body.get("name") or wid)[:60], endpoint, token, caps)
        _prov_log("worker.register", self._principal(),
                  json.dumps({"id": wid, "endpoint": endpoint}), {"wire": "api"})
        return self._vext_json({"ok": True, "worker": _vext.WorkerRegistry.public(rec)})

    def _api_worker_remove(self, wid):

        if _WORKER_REG is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "admin only"}, 403)

        try:
            _wb = json.loads(self._body() or b"{}")
        except Exception:
            _wb = {}
        import portal_delete_guard as _dg
        _2fa_ok, _2fa_resp = _dg.require_2fa(self._principal(),
                                             str(_wb.get("totp") or _wb.get("code") or ""),
                                             None, prov_log=_prov_log, action="worker.remove")
        if not _2fa_ok:
            return self._vext_json(_2fa_resp, 403)
        if not _WORKER_REG.remove(wid):
            return self._vext_json({"ok": False, "error": "unknown worker %r" % wid}, 404)
        _prov_log("worker.remove", self._principal(), json.dumps({"id": wid}), {"wire": "api"})
        return self._vext_json({"ok": True, "removed": wid})

    def _api_worker_health(self, wid):

        if _WORKER_REG is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "admin only"}, 403)
        rec = _WORKER_REG.get(wid)
        if rec is None:
            return self._vext_json({"ok": False, "error": "unknown worker %r" % wid}, 404)
        ok, info = _node_health_get(rec.get("endpoint"), rec.get("token"))
        if not ok:
            _WORKER_REG.mark_offline(wid)
            return self._vext_json({"ok": False, "error": "unreachable", "detail": str(info),
                                    "worker": _vext.WorkerRegistry.public(_WORKER_REG.get(wid))}, 502)
        state = info.get("state") if isinstance(info, dict) else None
        upd = _WORKER_REG.update_health(wid, facts=info if isinstance(info, dict) else {"raw": info},
                                        state=state if state in ("idle", "busy") else "idle")
        return self._vext_json({"ok": True, "health": info,
                                "worker": _vext.WorkerRegistry.public(upd)})

    def _winthin_totp_qr(self):
        try:
            reg = self._relay_registry()
            import urllib.parse as _up, io as _io, base64 as _b64, segno as _sg
            cx = reg.connect()
            row = cx.execute("SELECT secret_enc FROM principals_2fa WHERE principal=?", ("win-thin",)).fetchone()
            if not row:
                return None
            sec = reg._unwrap_secret(cx, row[0]).strip().replace(" ", "")
            uri = ("otpauth://totp/%s:%s?secret=%s&issuer=%s&algorithm=SHA1&digits=6&period=30"
                   % (_up.quote("Brainbox"), _up.quote("win-thin"), sec, _up.quote("Brainbox")))
            buf = _io.BytesIO(); _sg.make(uri, error="m").save(buf, kind="png", scale=6, border=3)
            return "data:image/png;base64," + _b64.b64encode(buf.getvalue()).decode()
        except Exception:
            return None

    def _verify_winthin_totp(self, code):
        try:
            reg = self._relay_registry()
            cx = reg.connect()

            ok, reason = reg.verify_2fa(cx, "win-thin", str(code).strip())
            return bool(ok), reason
        except Exception as e:
            return False, "verify error: %s" % e

    def _netprofile_mod(self):

        try:
            import sys as _s, os as _o
            for _p in (_o.environ.get("PNLIB_HOME"), _o.path.expanduser("~/portioneer")):
                if _p and _o.path.isdir(_o.path.join(_p, "pnlib")) and _p not in _s.path:
                    _s.path.insert(0, _p)
            from pnlib import netprofile as _np
            return _np
        except Exception:
            return None

    def _api_netprofile(self):

        if not self._is_admin():
            return self.send_html(json.dumps({"ok": False, "error": "nur Owner/Admin"}),
                                  403, [("Content-Type", "application/json")])
        np = self._netprofile_mod()
        if np is None:
            return self.send_html(json.dumps({"ok": False, "error": "netprofile-Modul nicht ladbar"}),
                                  200, [("Content-Type", "application/json")])
        return self.send_html(json.dumps({"ok": True, "profile": np.profile(),
                                          "capabilities": np.capabilities(),
                                          "ledger": np.ledger_tail(60)}, ensure_ascii=False),
                              200, [("Content-Type", "application/json")])

    def _netprofile_page(self):
        if not self._is_admin():
            return self.send_html("Netzverhalten ist nur fuer den Owner/Admin sichtbar.", 403)
        return self._html_asset("netprofile.html", "netprofile view not deployed")

    def _vpn_page(self):
        if not self._is_admin():
            return self.send_html("VPN ist nur fuer den Owner/Admin sichtbar.", 403)
        return self._html_asset("vpn.html", "vpn view not deployed")
