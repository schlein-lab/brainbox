import os, sys, json, secrets, subprocess, threading, time
import re, html, hashlib
import urllib.parse, urllib.request
try:
    import portal_passkeys as _passkeys
except Exception:
    _passkeys = None

DATA_DIR = None
PN_ACCT_DB = None
_CER_HOLD_MS = None
_CerEntry = None
_DBLOCK = None
_DEVICE_REG = None
_DISPLAY_REG = None
_IDENTITY_OBSERVE_ONLY = None
_NoSubsidy = None
_acct = None
_admin_ctx = None
_cer_done = None
_cer_target = None
_ceremony = None
_contract = None
_creg = None
_cval = None
_identity_observe = None
_known_principals = None
_llm_router = None
_parse_funding = None
_placement_for = None
_policy = None
_prov_log = None
_sesscell_reg = None
_sesscells = None
_stats = None
_uservpn_set = None
_validate_control = None
_vext = None
_vext_ctx = None
_voice_rights_changed = None
db = None
pending_actions_drain = None
portal_admin = None
seat_enumerate = None
seat_focused = None
seat_low_stakes = None
seat_sense = None
voice_ask = None

_CER_TWOFA = {}

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

_RES_TTL = 5.0
_RES_LOCK = threading.Lock()
_RES_CACHE = {"t": 0.0, "v": None, "inflight": False}
_ACCTD_TTL = 5.0
_ACCTD_CACHE = {"t": 0.0, "v": None}

import portal_zustand as _zst
_zst.register("portal_routes_admin._RES_CACHE", "cache", __name__, ref=_RES_CACHE, ttl_s=5.0,
              beschreibung="resource_overview stale-while-revalidate (Single-Flight); Fehlerergebnisse werden mitgecacht, nie verschluckt",
              neustart="verfaellt", schreiber="_resources_refresh() (Hintergrund) unter _RES_LOCK")
_zst.register("portal_routes_admin._ACCTD_CACHE", "cache", __name__, ref=_ACCTD_CACHE, ttl_s=5.0,
              beschreibung="_acctd_running-/proc-Scan, 5 s gecacht",
              neustart="verfaellt", schreiber="Statistik-Poller")
_zst.register("portal_routes_admin._CER_TWOFA", "snapshot", __name__, ref=_CER_TWOFA,
              beschreibung="2FA-Auflage je bewaffneter Zeremonie (re_id, Deckel 128); die Zeremonien selbst leben im selben Prozess (ceremony-Plane) — beim Neustart verfallen beide GEMEINSAM, nichts rutscht ohne TOTP durch",
              neustart="verfaellt", schreiber="Zeremonie-Bewaffnung; _ceremony_confirm liest")

def _resources_refresh():
    try:
        v = portal_admin.resource_overview(_admin_ctx())
        with _RES_LOCK:
            _RES_CACHE.update(t=time.time(), v=v)
    finally:
        with _RES_LOCK:
            _RES_CACHE["inflight"] = False

def _resources_cached():

    now = time.time()
    with _RES_LOCK:
        v = _RES_CACHE["v"]
        if v is not None:
            if (now - _RES_CACHE["t"]) >= _RES_TTL and not _RES_CACHE["inflight"]:
                _RES_CACHE["inflight"] = True
                threading.Thread(target=_resources_refresh, name="admin-resources-refresh",
                                 daemon=True).start()
            return v
    v = portal_admin.resource_overview(_admin_ctx())
    with _RES_LOCK:
        _RES_CACHE.update(t=time.time(), v=v)
    return v

class AdminRoutes:
    def _notes_page(self):
        return self._html_asset("notes.html", "notes view not deployed")

    def _verb_sense(self, query):

        _uid = self._principal()
        apps = seat_enumerate(_uid)
        foc = seat_focused(apps, _uid)
        out = {"apps": apps, "focused": foc}
        if foc:
            out["sense"] = seat_sense(foc["cid"], "text", _uid)
        return self._cer_json(out)

    def _verb_act(self, body):

        try:
            req = json.loads(body or "{}")
        except Exception:
            req = {}
        verb = req.get("verb") or {"click": "app.click", "scroll": "app.scroll", "text": "app.type",
                                   "type": "app.type", "enter": "app.enter", "sense": "app.sense"}.get(
                                       str(req.get("action", "")), "")
        if not verb:
            return self._cer_json({"ok": False, "earcon": "error", "speech": "Kein Verb angegeben."})
        params = {"x": req.get("x"), "y": req.get("y"), "n": req.get("n"), "text": req.get("text"),
                  "btn": req.get("btn"), "mode": req.get("mode", "text"), "secret": bool(req.get("secret"))}

        res = seat_low_stakes(verb, params, self._principal())
        _prov_log(verb, self._principal(), (res.get("forged") or {}).get("cmd", ""),
                  {"phase": "forge", "ok": res.get("ok"),
                   "focused": (res.get("extra", {}).get("focused") or {}).get("title")})
        return self._cer_json(res)

    def _contract_selfcheck(self):

        if _contract is None:
            return self._cer_json({"available": False})
        cases = []
        ok_all = True
        for fn, (sid, expect_valid) in _contract.CASES.items():
            try:
                with open(os.path.join(_contract.EX_DIR, fn)) as f:
                    inst = json.load(f)
                errs = _cval.validate(_creg[sid], inst, "$")
                valid = not errs
            except Exception as e:
                valid, errs = False, [str(e)]
            passed = (valid == expect_valid)
            ok_all = ok_all and passed
            cases.append({"file": fn, "expect_valid": expect_valid, "valid": valid, "pass": passed})
        try:
            tax = _contract.check_taxonomy(_creg)
        except Exception as e:
            tax = ["taxonomy check error: %s" % e]
        ok_all = ok_all and not tax
        return self._cer_json({"available": True, "ok": ok_all, "cases": cases, "taxonomy_errors": tax})

    def _control(self, body):

        try:
            env = json.loads(body or "{}")
        except Exception:
            env = None
        if not isinstance(env, dict):
            return self._cer_json({"ok": False, "error": {"code": "ERR_SCHEMA",
                                   "message": "body is not a JSON object"}})
        errs = _validate_control(env)
        if errs:
            return self._cer_json({"ok": False, "id": env.get("id"),
                                   "error": {"code": "ERR_SCHEMA", "message": "; ".join(errs)[:400],
                                             "details": errs[:8]}})
        verb = env.get("verb", "")
        args = env.get("args") or {}

        principal = self._principal()
        wire_principal = env.get("principal")

        identity = _identity_observe(env)
        _prov_log(verb, principal, json.dumps(args, sort_keys=True),
                  {"wire": "control", "phase": "dispatch", "funding": env.get("funding"),
                   "identity": (identity.get("code") if not identity.get("verified") else "verified"),
                   "wire_principal": (wire_principal if wire_principal and wire_principal != principal else None),
                   "identity_observe_only": _IDENTITY_OBSERVE_ONLY})
        result, err = self._control_dispatch(verb, args, principal, env)
        out = {"ok": err is None, "id": env.get("id"), "verb": verb, "identity": identity}
        if err is None:
            out["result"] = result
        else:
            out["error"] = err
        return self._cer_json(out)

    def _control_dispatch(self, verb, args, principal, env):

        try:
            if verb == "conversation.say":

                if _llm_router is not None:
                    try:
                        rr = _llm_router.route(principal=principal, funding=env.get("funding"),
                                               prompt=args.get("text", ""))
                    except _NoSubsidy as e:
                        return None, {"code": getattr(e, "code", "ERR_NO_SUBSIDY"),
                                      "message": str(e) or "BYO capacity exhausted, provide more"}
                    comp = rr.completion if isinstance(rr.completion, dict) else {"text": str(rr.completion)}
                    if _acct is not None and _parse_funding is not None:
                        try:
                            _acct.meter(principal=principal, tag=_parse_funding(env.get("funding")),
                                        resource="llm_calls", amount=1)
                        except Exception:
                            pass
                    return {"speak": comp.get("text"), "pool": rr.pool.value}, None
                uid = self._principal()
                out = {"speak": voice_ask(args.get("text", ""), uid)}
                acts = pending_actions_drain(uid)
                if acts:
                    out["actions"] = acts
                return out, None
            if verb == "app.sense":
                return seat_low_stakes("app.sense", {"mode": args.get("mode", "text")}), None
            if verb in ("app.scroll", "app.type", "app.enter", "app.click"):
                return seat_low_stakes(verb, args), None
            if verb == "placement.decide":
                return _placement_for(args.get("caps") or {}, args.get("policy", "auto"),
                                      args.get("workload", "default")), None
            if verb in ("app.open", "screen.show"):
                return {"action": "summon", "lens": args.get("lens") or "screen"}, None
            if verb.startswith("verb."):
                if _ceremony is None:
                    return None, {"code": "ERR_UNAVAILABLE", "message": "ceremony engine down"}
                text = args.get("text") or json.dumps(args, sort_keys=True)
                tgt = _cer_target(verb, text)
                re_id = "re-" + secrets.token_hex(6)

                def _do(_v=verb):
                    return {"committed": True, "verb": _v, "effect": "recorded-intent (kein Backend)"}

                cer, prompt = _ceremony.begin(re_id=re_id, verb=verb, target=tgt, action=_do,
                                              subject=tgt.meta.get("subject"))
                return {"ceremony": prompt, "re": re_id}, None
            return None, {"code": "ERR_UNKNOWN_VERB",
                          "message": "verb %r accepted by contract but not dispatched here" % verb}
        except Exception as e:
            return None, {"code": "ERR_INTERNAL", "message": str(e)}

    def _admin_stats(self):
        if _stats is None:
            return {"ok": False, "msg": "stats unavailable"}
        out = _stats.aggregate(os.path.join(DATA_DIR, "provenance.jsonl"))
        try:
            with _DBLOCK:
                c = db()
                rows = c.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()
                c.close()
            out["jobs"] = {str(r[0]): r[1] for r in rows}
            out["jobs_total"] = sum(r[1] for r in rows)
        except Exception:
            out["jobs"] = {}; out["jobs_total"] = 0
        try:
            if portal_admin is not None:
                out["resources"] = _resources_cached()
        except Exception:
            out["resources"] = {}
        return out

    @staticmethod
    def _acctd_running():

        now = time.time()
        if _ACCTD_CACHE["v"] is not None and (now - _ACCTD_CACHE["t"]) < _ACCTD_TTL:
            return _ACCTD_CACHE["v"]
        val = False
        try:
            for pid in os.listdir("/proc"):
                if not pid.isdigit():
                    continue
                try:
                    with open("/proc/%s/cmdline" % pid, "rb") as fh:
                        argv = fh.read().split(b"\0")
                except OSError:
                    continue
                for part in argv[:2]:
                    if os.path.basename(part.decode("utf-8", "replace")) == "pn-acctd":
                        val = True
                        break
                if val:
                    break
        except OSError:
            pass
        _ACCTD_CACHE.update(t=now, v=val)
        return val

    def _admin_usage(self):
        if _stats is None:
            return {"ok": False, "msg": "stats unavailable"}
        out = _stats.usage_history_cached(PN_ACCT_DB)
        out["source"] = "acct.db/job_actuals"
        if out.get("ok") and not self._acctd_running():

            return {"ok": False, "source": out["source"], "rows": out.get("rows", 0),
                    "error": "Verbrauchsdaten fehlen: der Abrechnungsdienst (pn-acctd) läuft nicht."}
        return out

    def _cer_json(self, obj, code=200):
        return self.send_html(json.dumps(obj), code, [("Content-Type", "application/json")])

    @staticmethod
    def _verb_action_class(verb):

        v = str(verb or "")
        if any(k in v for k in ("delete", "remove", "destroy", "wipe", "drop")):
            return "destructive"
        return "external"

    def _begin_ceremony(self, verb, text, session=None):

        if _ceremony is None or _CerEntry is None:
            return self._cer_json({"action": "refuse", "verb": verb,
                "speak": "Das ist irreversibel (%s) und die Ceremony-Engine ist gerade nicht geladen — "
                         "ich mache das nicht ungefragt." % verb})
        try:
            target = _cer_target(verb, text)
            re_id = "re-" + secrets.token_hex(6)

            def _do(_v=verb, _t=text):

                return {"committed": True, "verb": _v, "effect": "recorded-intent (kein Backend)"}

            cer, prompt = _ceremony.begin(re_id=re_id, verb=verb, target=target, action=_do,
                                          subject=target.meta.get("subject"))
            uid = self._principal()
            ac = self._verb_action_class(verb)
            try:
                lvl = _sesscell_reg().get_autonomy(uid, session) if (session and _sesscells) \
                    else (_sesscells.DEFAULT_AUTONOMY if _sesscells else None)

                need2fa = bool(_sesscells and (_sesscells.requires_2fa(lvl, ac)
                                               or _sesscells.user_requires_2fa(uid, ac)))
            except Exception:
                need2fa = True
            entry = {"principal": uid, "need": need2fa, "action_class": ac}

            wa = None
            if need2fa and _passkeys is not None:
                try:
                    if _passkeys.biometric_enabled(uid):
                        subj = ""
                        try:
                            subj = str(target.meta.get("subject") or "")
                        except Exception:
                            subj = ""
                        action_digest = "sha256:" + hashlib.sha256(
                            ("%s|%s" % (verb, subj)).encode()).hexdigest()
                        wa = _passkeys.ceremony_challenge(uid, re_id, action_digest)
                        if wa:
                            entry["wa_challenge"] = wa.get("challenge")
                except Exception:
                    wa = None
            _CER_TWOFA[re_id] = entry
            if len(_CER_TWOFA) > 128:
                for k in list(_CER_TWOFA)[:64]:
                    _CER_TWOFA.pop(k, None)
            out = {"action": "ceremony", "re": re_id, "verb": verb,
                   "readback": prompt.get("readback"), "challenge": prompt.get("challenge"),
                   "hold_ms": prompt.get("hold_ms"), "speak": prompt.get("spoken"),
                   "twofa_required": need2fa}
            if wa:
                out["webauthn"] = {"challenge": wa["challenge"], "rpId": wa["rpId"],
                                   "allowCredentials": wa["allowCredentials"],
                                   "userVerification": wa.get("userVerification", "required")}
                out["biometric_available"] = True
            if need2fa:
                _oder = (" — oder per Fingerabdruck („Mit Passkey bestätigen“)." if wa
                         else " (2FA).")
                out["speak"] = (prompt.get("spoken") or "") + \
                    " Diese Aktion geht nach außen — zum Bestätigen brauche ich deinen Handy-Code" + _oder
            return self._cer_json(out)
        except Exception as e:
            return self._cer_json({"action": "refuse", "verb": verb,
                "speak": "Konnte die Ceremony nicht starten (%s) — ich mache das nicht ungefragt." % e})

    @staticmethod
    def _cer_body(body):

        if isinstance(body, dict):
            return body
        if isinstance(body, (bytes, bytearray, str)):
            try:
                parsed = json.loads(body or "{}")
            except Exception:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    def _ceremony_confirm(self, body):
        if _ceremony is None:
            return self._cer_json({"accepted": False, "reason": "no-engine"})
        body = self._cer_body(body)
        if body is None:
            return self._cer_json({"accepted": False, "reason": "bad-request",
                                   "speak": "Die Bestätigung war unlesbar — nichts passiert."}, 400)
        re_id = body.get("re") or ""
        resp = body.get("nonce_response") or body.get("text") or ""

        tw = _CER_TWOFA.get(re_id)
        if tw and tw.get("need"):
            factor_ok = False
            reason = "kein-faktor"

            pk = body.get("passkey")
            if pk and _passkeys is not None and tw.get("wa_challenge"):
                try:
                    okp, info = _passkeys.verify_ceremony(tw.get("principal"), pk, tw.get("wa_challenge"))
                    factor_ok = bool(okp)
                    reason = "passkey-ok" if okp else ("passkey:%s" % info)
                except Exception as e:
                    reason = "passkey-fehler:%s" % e

            if not factor_ok and (body.get("totp") or not pk):
                ok2, reason2 = self._verify_principal_totp(tw.get("principal"), body.get("totp"))
                if ok2:
                    factor_ok, reason = True, "totp-ok"
                else:
                    reason = reason2
            if not factor_ok:
                spk = ("Für dein Konto ist noch kein 2FA/Handy-Code eingerichtet — richte ihn unter "
                       "„Freigaben“ ein. Ohne zweiten Faktor wird nichts gesendet."
                       if reason == "kein-2fa" else
                       ("Die Passkey-Bestätigung hat nicht geklappt — abgebrochen, nichts wurde gesendet."
                        if str(reason).startswith("passkey") else
                        "Ich brauche deinen gültigen Handy-Code (2FA) zum Bestätigen — abgebrochen, "
                        "nichts wurde gesendet."))
                return self._cer_json({"accepted": False, "reason": "need-2fa", "need_2fa": True,
                                       "speak": spk})
        res = _ceremony.confirm(re_id, nonce_response=resp)
        if res.get("accepted"):
            _CER_TWOFA.pop(re_id, None)

        if res.get("accepted"):
            _prov_log("verb.ceremony_confirm", "owner", re_id, {"re": re_id, "phase": "holding"})
            res["speak"] = "Bestätigt. Sende in %d Sekunden — sag stopp zum Abbrechen." % (
                int(res.get("holding_ms", _CER_HOLD_MS)) // 1000)
        else:
            reason = res.get("reason", "")
            res["speak"] = {"no-nonce": "Ich brauche die Bestätigungszahl, kein bloßes Ja — abgebrochen.",
                            "wrong-nonce": "Falsche Zahl — abgebrochen, nichts passiert.",
                            "unknown-ceremony": "Diese Bestätigung ist abgelaufen."}.get(
                reason, "Abgebrochen (%s) — nichts passiert." % reason)
        return self._cer_json(res)

    def _ceremony_cancel(self, body):
        if _ceremony is None:
            return self._cer_json({"cancelled": False, "reason": "no-engine"})
        body = self._cer_body(body)
        if body is None:
            return self._cer_json({"cancelled": False, "reason": "bad-request",
                                   "speak": "Der Stopp-Befehl war unlesbar — bitte sofort neu senden. "
                                            "Es wurde nichts ausgeführt."}, 400)
        re_id = body.get("re") or ""
        res = _ceremony.cancel(re_id)
        if res.get("cancelled"):
            _prov_log("verb.ceremony_cancel", "owner", re_id,
                      {"re": re_id, "phase": "cancelled", "from": res.get("from", "")})
            res["speak"] = "Gestoppt. Nichts wurde ausgeführt."
        else:

            reason = res.get("reason", "")

            done = (_cer_done or {}).get(re_id) if re_id else None
            executed = bool(res.get("executed")) or (done or {}).get("state") == "executed"
            if executed:
                res["executed"] = True
                res["speak"] = "Zu spät — die Aktion wurde bereits ausgeführt und ist im Protokoll signiert."
            elif reason == "unknown-ceremony":
                res["speak"] = "Diese Bestätigung gibt es nicht mehr — es läuft nichts, nichts wird ausgeführt."
            else:
                res["speak"] = "Nichts zu stoppen — es wurde nichts ausgeführt (%s)." % reason
        return self._cer_json(res)

    def _ceremony_status(self, query):
        re_id = (query.get("re", [""])[0]) if isinstance(query, dict) else ""
        if _ceremony is None:
            return self._cer_json({"state": "no-engine"})
        cer = _ceremony.get(re_id)
        if cer is None:

            done = _cer_done.get(re_id)
            if done and done.get("state") == "executed":
                return self._cer_json({"state": "executed", "verb": done.get("verb"),
                                       "speak": "Ausgeführt und im Protokoll signiert.",
                                       "result": done.get("result")})
            return self._cer_json({"state": "gone", "speak": ""})
        st = cer.state.value
        speak = ""
        if st == "executed":
            speak = "Ausgeführt und im Protokoll signiert."
        elif st == "failed":
            speak = "Ausführung fehlgeschlagen: %s" % (cer.error or "")
        elif st == "cancelled":
            speak = "Abgebrochen."
        return self._cer_json({"state": st, "verb": cer.verb, "speak": speak,
                               "result": getattr(cer, "result", None)})

    def _policy_store(self):
        return _policy.PolicyStore(os.path.join(DATA_DIR, "session-policies"))

    def _policy_floor(self):
        try:
            return _policy.validate(json.load(open(os.path.join(DATA_DIR, "session-policies", "policy-floor.json"))))
        except Exception:
            return None

    def _policy_qs(self, query):
        q = urllib.parse.parse_qs(query or "")
        kind = re.sub(r"[^a-z]", "", (q.get("kind", ["voice"])[0] or "voice"))[:16] or "voice"
        sid = re.sub(r"[^A-Za-z0-9_.-]", "", (q.get("sid", ["default"])[0] or "default"))[:64] or "default"
        return kind, sid

    def _api_policy_catalog(self):
        if _policy is None:
            return self._sess_json({"ok": False, "error": "policy module missing"}, 500)
        displays = []
        try:
            if _DISPLAY_REG is not None:
                displays = [d.get("name") or d.get("id") for d in (_DISPLAY_REG.list() or [])]
        except Exception:
            displays = []
        if not displays:
            displays = ["local"]

        speakers = []
        try:
            import pn_sprachausgabe as _sprach
            speakers = _sprach.lautsprecher()
        except Exception:
            speakers = []
        return self._sess_json({"ok": True, "catalog": _policy.CATALOG, "presets": _policy.PRESETS,
                                "speakers": speakers,

                                "preset_meta": getattr(_policy, "PRESET_META", {}),
                                "default_preset": _policy.DEFAULT_PRESET,
                                "displays": displays,
                                "devices": (_DEVICE_REG.list() if _DEVICE_REG is not None else _policy.device_roster())})

    def _api_policy_get(self, query):
        if _policy is None:
            return self._sess_json({"ok": False, "error": "policy module missing"}, 500)
        kind, sid = self._policy_qs(query)
        pol = self._policy_store().get(self._principal(), kind, sid)
        pol = _policy.apply_floor(pol, self._policy_floor())
        return self._sess_json({"ok": True, "kind": kind, "sid": sid, "policy": pol})

    def _api_policy_default_get(self):
        if _policy is None:
            return self._sess_json({"ok": False, "error": "policy module missing"}, 500)
        return self._sess_json({"ok": True, "policy": self._policy_store().get_default()})

    def _api_policy_set(self, raw):
        if _policy is None:
            return self._sess_json({"ok": False, "error": "policy module missing"}, 500)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        kind = re.sub(r"[^a-z]", "", str(body.get("kind") or "voice"))[:16] or "voice"
        sid = re.sub(r"[^A-Za-z0-9_.-]", "", str(body.get("sid") or "default"))[:64] or "default"
        try:
            pol = _policy.apply_floor(_policy.validate(body.get("policy") or {}), self._policy_floor())
            saved = self._policy_store().set(self._principal(), kind, sid, pol)
            if kind == "voice" and sid == "default":
                try:
                    _voice_rights_changed(self._principal(), "Du hast die Rechte dieser Session angepasst.")
                except Exception:
                    pass
            return self._sess_json({"ok": True, "kind": kind, "sid": sid, "policy": saved})
        except Exception as e:
            return self._sess_json({"ok": False, "error": str(e)}, 500)

    def _api_policy_default_set(self, raw):
        if _policy is None:
            return self._sess_json({"ok": False, "error": "policy module missing"}, 500)

        if not self._is_admin():
            return self._sess_json({"ok": False, "error": "admin only"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return self._sess_json({"ok": False, "error":
                "Der Anfrage-Body muss ein JSON-Objekt sein."}, 400)
        try:
            saved = self._policy_store().set_default(body.get("policy") or {})
            return self._sess_json({"ok": True, "policy": saved})
        except Exception:
            try:
                import traceback
                sys.stderr.write("[policy default set] %s\n" % traceback.format_exc())
            except Exception:
                pass
            return self._sess_json({"ok": False, "error":
                "Vorlage konnte nicht gespeichert werden."}, 500)

    def _api_admin_rights_list(self):

        if not self._is_admin():
            return self._sess_json({"ok": False, "error": "admin only"}, 403)
        base = os.path.join(DATA_DIR, "session-policies")
        users = {}
        try:
            for name in os.listdir(base):
                d = os.path.join(base, name)
                if not os.path.isdir(d):
                    continue

                try:
                    if not os.listdir(d):
                        continue
                except OSError:
                    continue
                users[name] = {"uid": name, "floor": os.path.exists(os.path.join(d, "user-floor.json"))}
        except OSError:
            pass
        for u in _known_principals():
            users.setdefault(u, {"uid": u, "floor": False})
        return self._sess_json({"ok": True, "users": sorted(users.values(), key=lambda x: x["uid"]),
                                "presets": list(_policy.PRESETS.keys()),
                                "preset_meta": getattr(_policy, "PRESET_META", {})})

    def _api_admin_user_floor_get(self, query):
        if not self._is_admin():
            return self._sess_json({"ok": False, "error": "admin only"}, 403)
        q = urllib.parse.parse_qs(query or "")
        uid = re.sub(r"[^A-Za-z0-9_.-]", "", (q.get("uid", [""])[0] or ""))[:64]
        if not uid:
            return self._sess_json({"ok": False, "error": "uid required"}, 400)
        floor = self._policy_store().get_user_floor(uid)
        return self._sess_json({"ok": True, "uid": uid, "floor": floor})

    def _api_admin_user_floor_set(self, raw):
        if not self._is_admin():
            return self._sess_json({"ok": False, "error": "admin only"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        uid = re.sub(r"[^A-Za-z0-9_.-]", "", str(body.get("uid") or ""))[:64]
        if not uid:
            return self._sess_json({"ok": False, "error": "uid required"}, 400)

        preset = body.get("preset")
        if preset in _policy.PRESETS:
            floor = {"caps": dict(_policy.PRESETS[preset])}
        else:
            floor = body.get("floor")

        wants_clear = bool(body.get("remove")) or ("floor" in body and body.get("floor") is None
                                                   and preset is None)
        asked_caps = isinstance(floor, dict) and bool(floor.get("caps"))
        saved = self._policy_store().set_user_floor(uid, floor)
        if asked_caps and not saved:
            return self._sess_json({"ok": False, "uid": uid, "floor": None,
                                    "error": "Kein einziges der angegebenen Rechte ist gültig — es wurde "
                                             "NICHTS gespeichert. Der Nutzer behält seine bisherigen Rechte."},
                                   400)
        if wants_clear:
            self._prune_policy_dir(uid)

        try:
            _voice_rights_changed(uid, "Admin hat deine Nutzerrechte angepasst.")
        except Exception:
            pass
        return self._sess_json({"ok": True, "uid": uid, "floor": saved,
                                "removed": bool(wants_clear and not saved)})

    def _prune_policy_dir(self, uid):

        d = os.path.join(DATA_DIR, "session-policies", str(uid).replace("/", "_"))
        try:
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
                return True
        except OSError:
            pass
        return False

    def _api_admin_ram(self):

        if not self._is_admin():
            return self._sess_json({"ok": False, "error": "admin only"}, 403)
        try:
            import pn_ram_admission as _ra
            return self._sess_json({"ok": True, "ram": _ra.snapshot()})
        except Exception as e:
            return self._sess_json({"ok": False, "error": str(e)}, 500)

    def _api_admin_ram_stop(self, raw):

        if not self._is_admin():
            return self._sess_json({"ok": False, "error": "admin only"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        rid = str(body.get("id") or "").strip()
        if not rid:
            return self._sess_json({"ok": False, "error": "id required"}, 400)
        try:
            import pn_ram_admission as _ra
            rec = next((r for r in _ra.snapshot()["running"] if r.get("id") == rid), None)
            if rec is None:
                return self._sess_json({"ok": False, "error": "unbekannte VM (evtl. schon gestoppt)"}, 404)
            stopped = False
            if rec.get("kind") == "session":
                import pn_cell_session as _cs
                owner, session = rec.get("owner"), rec.get("session")
                if owner and session:
                    stopped = bool(_cs.get_manager().stop(owner, session, erase=False))
            else:
                import signal as _sig
                pid = rec.get("ctl_pid") or rec.get("pid")
                try:
                    os.kill(int(pid), _sig.SIGTERM); stopped = True
                except (OSError, TypeError, ValueError):
                    stopped = False
            _ra.release(rid)
            try:
                _prov_log("admin.ram.stop", self._principal(), rid,
                          {"kind": rec.get("kind"), "ok": stopped})
            except Exception:
                pass
            return self._sess_json({"ok": stopped, "id": rid, "kind": rec.get("kind"),
                                    "ram": _ra.snapshot()})
        except Exception as e:
            return self._sess_json({"ok": False, "error": str(e)}, 500)

 
 
 
 
 
 
 
 
 
 
 
 
 
    _FEATURE_FLAGS = ("MEDIA_SERVER_ENABLED", "RELAY_ENABLED", "VOICE_ENABLED",
                      "CELLS_ENABLED")

    def _feature_flag_values(self):
        vals = {}
        try:
            with open("/etc/brainbox/site.conf", encoding="utf-8", errors="replace") as f:
                for ln in f:
                    ln = ln.strip()
                    if "=" in ln and not ln.startswith("#"):
                        k, _, v = ln.partition("=")
                        if k in self._FEATURE_FLAGS:
                            vals[k] = v.split("#")[0].strip().strip("\"'")
        except OSError:
            pass
        return vals

    @staticmethod
    def _local_port_open(port):
        import socket as _s
        try:
            with _s.create_connection(("127.0.0.1", int(port)), timeout=0.4):
                return True
        except OSError:
            return False

    def _api_admin_features(self):
        if not self._is_admin():
            return self._sess_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        vals = self._feature_flag_values()
        return self._sess_json({
            "ok": True,
            "flags": {k: (vals.get(k) == "1") for k in self._FEATURE_FLAGS},
            "media": {"smb": self._local_port_open(445),
                      "dlna": self._local_port_open(8200),
                      "advert": os.path.exists("/etc/avahi/services/smb.service")},
        })

    def _api_admin_features_set(self, raw):
        if not self._is_admin():
            return self._sess_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._sess_json({"ok": False, "error": "bad json"}, 400)
        key = str(body.get("key") or "")
        if key not in self._FEATURE_FLAGS:
            return self._sess_json({"ok": False, "error": "unbekannter Schalter"}, 400)
        want = 1 if body.get("enabled") else 0
        helper = "/usr/local/sbin/pn-mediashare-provision"
        if key == "MEDIA_SERVER_ENABLED":
            argv = [helper, "media", "on" if want else "off"]
        else:
            argv = [helper, "setflag", key, str(want)]
        if os.geteuid() != 0:
            argv = ["sudo", "-n"] + argv
        try:
            r = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, timeout=60)
        except Exception as e:
            return self._sess_json({"ok": False, "error": "Helfer nicht erreichbar: %s" % e}, 500)
        if r.returncode != 0:
            return self._sess_json({"ok": False, "error":
                                    (r.stderr or r.stdout or "Helfer meldet Fehler").strip()[:300]}, 500)
        try:
            _prov_log("features.set", self._principal(), key, {"enabled": want})
        except Exception:
            pass
        return self._api_admin_features()

    def _settings_page(self):
        p = os.path.join(os.path.dirname(os.path.realpath(__file__)), "settings.html")
        try:
            return self.send_html(open(p, encoding="utf-8").read(), 200)
        except OSError:
            return self.send_html("settings.html fehlt", 404)

    def _policy_editor_page(self):
        p = os.path.join(os.path.dirname(os.path.realpath(__file__)), "policy_editor.html")
        try:
            return self.send_html(open(p, "rb").read())
        except Exception:
            return self.send_html("policy editor not deployed", 404)

    def _shellkeys_lib(self):
        import pn_sshkeys
        return pn_sshkeys

    def _shellkeys_home(self):
        import pwd as _pwd
        return _pwd.getpwuid(os.getuid()).pw_dir

    def _shellkeys_stepup(self, body, action):

        import portal_delete_guard as _dg
        armed = None
        try:
            reg = _dg._relay_registry()
            cx = reg.connect()
            try:
                armed = bool(reg.has_2fa(cx, _dg.GATE_PRINCIPAL))
            finally:
                try:
                    cx.close()
                except Exception:
                    pass
        except Exception:
            armed = None
        if armed:
            return _dg.require_2fa(self._principal(),
                                   str(body.get("totp") or body.get("code") or ""),
                                   None, prov_log=_prov_log, action=action)
        if armed is None:
            _prov_log(action + ".blocked", self._principal(),
                      "registry_unreadable", {"wire": "api"})
            return (False, {"ok": False, "error":
                            "Der zweite Faktor ist gerade nicht pruefbar — die Aenderung ist zur "
                            "Sicherheit gesperrt."})

        import portal_users as _pu
        who = self._principal()

        key = "shellkey-stepup:%s@%s" % (who, self.client_address[0]
                                         if getattr(self, "client_address", None) else "?")
        if _pu._login_locked(key):
            return (False, {"ok": False, "need_stepup": True, "factor": "password",
                            "error": "Zu viele Fehlversuche — bitte ein paar Minuten warten."})
        pw = str(body.get("password") or "")
        if not pw:
            return (False, {"ok": False, "need_stepup": True, "factor": "password",
                            "error": "Zur Bestaetigung bitte das eigene Portal-Passwort eingeben."})
        if not _pu.user_verify(who, pw):
            _pu._login_fail(key)
            _prov_log(action + ".blocked", who, "bad_password", {"wire": "api"})
            return (False, {"ok": False, "need_stepup": True, "factor": "password",
                            "error": "Passwort stimmt nicht."})
        _pu._login_ok(key)
        return (True, None)

    def _shellkeys_factor(self):

        try:
            import portal_delete_guard as _dg
            reg = _dg._relay_registry()
            cx = reg.connect()
            try:
                return "totp" if reg.has_2fa(cx, _dg.GATE_PRINCIPAL) else "password"
            finally:
                try:
                    cx.close()
                except Exception:
                    pass
        except Exception:
            return "unavailable"

    def _api_admin_shellkeys(self):

        if not self._is_admin():
            return self._sess_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        import pwd as _pwd
        try:
            sk = self._shellkeys_lib()
        except Exception as e:
            return self._sess_json({"ok": False, "error": "pn_sshkeys fehlt: %s" % e}, 503)
        home = self._shellkeys_home()
        keys = sk.read(home)
        pw = sk.password_auth()
        return self._sess_json({
            "ok": True,
            "user": _pwd.getpwuid(os.getuid()).pw_name,
            "path": sk.path_for(home),
            "keys": keys,

            "password_auth": pw,
            "count": len([k for k in keys if k.get("parsed")]),
            "stepup_factor": self._shellkeys_factor(),
        })

    def _api_admin_shellkeys_add(self, raw):

        if not self._is_admin():
            return self._sess_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._sess_json({"ok": False, "error": "bad json"}, 400)
        sk = self._shellkeys_lib()
        ok, res = sk.parse(body.get("keys") or "")
        if not ok:
            return self._sess_json({"ok": False, "error_key": res,
                                    "error": "Schluessel nicht lesbar"}, 400)
        if not res:
            return self._sess_json({"ok": False, "error": "kein Schluessel eingegeben"}, 400)

        _su_ok, _su_resp = self._shellkeys_stepup(body, "shellkey.add")
        if not _su_ok:
            return self._sess_json(_su_resp, 403)
        home = self._shellkeys_home()
        try:
            added, total = sk.add(home, res)
        except Exception as e:
            return self._sess_json({"ok": False, "error": "Schreiben fehlgeschlagen: %s" % e}, 500)

        _prov_log("shellkey.add", self._principal(),
                  json.dumps({"added": added, "total": total,
                              "fp": [sk.fingerprint(k) for k in res]}), {"wire": "api"})
        return self._sess_json({"ok": True, "added": added, "total": total,
                                "fingerprints": [sk.fingerprint(k) for k in res]})

    def _api_admin_shellkeys_remove(self, raw):

        if not self._is_admin():
            return self._sess_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._sess_json({"ok": False, "error": "bad json"}, 400)
        fp = str(body.get("fp") or "").strip()
        if not fp.startswith("SHA256:"):
            return self._sess_json({"ok": False, "error": "Fingerabdruck noetig"}, 400)
        sk = self._shellkeys_lib()
        home = self._shellkeys_home()
        keys = [k for k in sk.read(home) if k.get("parsed")]
        if not any(k.get("fp") == fp for k in keys):
            return self._sess_json({"ok": False, "error": "Schluessel nicht gefunden"}, 404)

        if len(keys) == 1 and sk.password_auth() is not True and not body.get("force"):
            return self._sess_json({
                "ok": False, "would_lock_out": True,
                "error": "Das ist der letzte Schluessel, und die Passwortanmeldung ueber SSH ist "
                         "nicht aktiv. Nach dem Entfernen kommt niemand mehr per SSH auf diese "
                         "Box — nur noch ueber Bildschirm und Tastatur.",
            }, 409)
        _su_ok, _su_resp = self._shellkeys_stepup(body, "shellkey.remove")
        if not _su_ok:
            return self._sess_json(_su_resp, 403)
        try:
            removed, total = sk.remove(home, fp)
        except Exception as e:
            return self._sess_json({"ok": False, "error": "Schreiben fehlgeschlagen: %s" % e}, 500)
        _prov_log("shellkey.remove", self._principal(),
                  json.dumps({"fp": fp, "removed": bool(removed), "total": total,
                              "forced": bool(body.get("force"))}), {"wire": "api"})
        return self._sess_json({"ok": bool(removed), "removed": bool(removed), "total": total})

    def _api_admin_user_vpn(self, raw):

        if not self._is_admin():
            return self._sess_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        uid = str(body.get("uid") or "").strip()
        vpn = str(body.get("vpn") or "").strip()
        if not uid or not vpn:
            return self._sess_json({"ok": False, "error": "uid+vpn noetig"}, 400)

        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            return self._sess_json({"ok": False, "error": "enabled muss true oder false sein"}, 400)
        cur = _uservpn_set(uid, vpn, enabled)
        removed = False
        if not cur:
            removed = self._uservpn_prune(uid)
        _prov_log("uservpn.set", self._principal(),
                  json.dumps({"uid": uid, "vpn": vpn, "on": enabled}), {"wire": "api"})
        return self._sess_json({"ok": True, "uid": uid, "allowed": cur or [], "removed": removed})

    def _uservpn_prune(self, uid):

        p = os.path.join(DATA_DIR, "user-vpn-grants.json")
        try:
            with open(p) as f:
                d = json.load(f)
            if not isinstance(d, dict) or d.get(str(uid)):
                return False
            d.pop(str(uid), None)
            tmp = p + ".tmp"
            with open(tmp, "w") as f:
                json.dump(d, f)
            os.replace(tmp, p)
            return True
        except Exception:
            return False

    def _blueprints_mod(self):
        try:
            import sys as _s, os as _o
            for _p in (_o.environ.get("PNLIB_HOME"), _o.path.expanduser("~/portioneer")):
                if _p and _o.path.isdir(_o.path.join(_p, "pnlib")) and _p not in _s.path:
                    _s.path.insert(0, _p)
            from pnlib import blueprints as _bp
            return _bp
        except Exception:
            return None

    def _api_blueprints(self):

        if not self._is_admin():
            return self.send_html(json.dumps({"ok": False, "error": "nur Owner/Admin"}),
                                  403, [("Content-Type", "application/json")])
        bp = self._blueprints_mod()
        if bp is None:
            return self.send_html(json.dumps({"ok": False, "error": "blueprints-Modul nicht ladbar"}),
                                  200, [("Content-Type", "application/json")])
        try:
            devs = [{"name": d.get("name"), "kind": d.get("kind"), "blueprints": d.get("blueprints", [])}
                    for d in bp.annotate() if d.get("blueprints")]

            try:
                errors = bp.load_errors()
            except Exception:
                errors = []
            return self.send_html(json.dumps({"ok": True, "cards": bp.summary(), "devices": devs,
                                              "actions": bp.action_summary(), "errors": errors},
                                             ensure_ascii=False), 200, [("Content-Type", "application/json")])
        except Exception as e:
            return self.send_html(json.dumps({"ok": False, "error": str(e)}),
                                  200, [("Content-Type", "application/json")])

    def _api_blueprint_resolve(self, query):

        if not self._is_admin():
            return self.send_html(json.dumps({"ok": False, "error": "nur Owner/Admin"}),
                                  403, [("Content-Type", "application/json")])
        bp = self._blueprints_mod()
        if bp is None:
            return self.send_html(json.dumps({"ok": False, "error": "blueprints-Modul nicht ladbar"}),
                                  200, [("Content-Type", "application/json")])
        q = urllib.parse.parse_qs(query or "")
        cid = (q.get("id", [""])[0]) or ""
        role = (q.get("role", [""])[0]) or None
        if not cid:
            return self.send_html(json.dumps({"ok": False, "error": "id fehlt"}),
                                  400, [("Content-Type", "application/json")])
        return self.send_html(json.dumps(bp.resolve_driver(cid, role), ensure_ascii=False),
                              200, [("Content-Type", "application/json")])

    def _api_actions(self, query):

        bp = self._blueprints_mod()
        if bp is None:
            return self.send_html(json.dumps({"ok": False, "error": "blueprints-Modul nicht ladbar"}),
                                  200, [("Content-Type", "application/json")])
        q = ""
        try:
            q = (urllib.parse.parse_qs(query or "").get("q", [""])[0]) or ""
        except Exception:
            q = ""
        res = bp.match_action(q) if q else bp.action_summary()
        return self.send_html(json.dumps({"ok": True, "query": q, "actions": res}, ensure_ascii=False),
                              200, [("Content-Type", "application/json")])

    def _blueprints_page(self):
        if not self._is_admin():
            return self.send_html("Blueprints sind nur fuer den Owner/Admin sichtbar.", 403)
        return self._html_asset("blueprints.html", "blueprints view not deployed")

    def _portal_nav_js(self):

        p = os.path.join(os.path.dirname(os.path.realpath(__file__)), "portal-nav.js")
        try:
            data = open(p, "rb").read()
        except Exception:
            return self.send_html("// portal-nav.js missing", 404)
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _portal_ca_crt(self):

        try:
            import pn_certs
            p = pn_certs.ca_cert_path()
            data = open(p, "rb").read()
        except Exception:
            return self.send_html("CA nicht verfuegbar", 404)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-x509-ca-cert")
        self.send_header("Content-Disposition", "attachment; filename=\"brainbox-ca.crt\"")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def _trust_page(self):

        fp = ""
        try:
            import pn_certs; fp = pn_certs.ca_fingerprint_sha256()
        except Exception:
            pass
        p = os.path.join(os.path.dirname(os.path.realpath(__file__)), "trust.html")
        try:
            html = open(p, encoding="utf-8").read().replace("%FINGERPRINT%", fp or "(nicht verfuegbar)")
            return self.send_html(html)
        except Exception:
            return self.send_html("trust page not deployed", 404)

    def _api_read(self, raw):

        if _vext is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        payload, code = _vext.read(_vext_ctx(), self._principal(), body)
        return self._vext_json(payload, code)

    @staticmethod
    def _autonomy_meta():

        sc = _sesscells
        levels = list(getattr(sc, "LEVELS", ()))
        return {
            "levels": {str(k): v for k, v in sc.AUTONOMY_LABELS.items()},
            "autonomy_levels": {str(k): v for k, v in sc.AUTONOMY_LABELS.items()},
            "autonomy_short": {str(k): getattr(sc, "AUTONOMY_SHORT", {}).get(k, str(k)) for k in levels},
            "autonomy_experience": {str(k): getattr(sc, "AUTONOMY_EXPERIENCE", {}).get(k, "") for k in levels},
            "autonomy_order": levels,
            "autonomy_default": sc.DEFAULT_AUTONOMY,
        }

    def _api_autonomy(self, query):

        if _sesscells is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        q = urllib.parse.parse_qs(query)
        session = q.get("session", [""])[0]
        reg = _sesscell_reg()
        lvl = reg.get_autonomy(self._principal(), session) if session else _sesscells.DEFAULT_AUTONOMY
        out = {"ok": True, "session": session, "level": lvl,
               "label": _sesscells.AUTONOMY_LABELS.get(lvl, str(lvl)),
               "short": getattr(_sesscells, "AUTONOMY_SHORT", {}).get(lvl, str(lvl)),
               "experience": getattr(_sesscells, "AUTONOMY_EXPERIENCE", {}).get(lvl, "")}
        out.update(self._autonomy_meta())
        return self._vext_json(out)

    def _api_autonomy_set(self, raw):

        if _sesscells is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        session = str(body.get("session") or "")
        if not session:
            return self._vext_json({"ok": False, "error": "session required"}, 400)
        raw_level = body.get("level")
        if isinstance(raw_level, bool):
            return self._vext_json({"ok": False, "error": "level muss eine Zahl sein, kein true/false"}, 400)
        try:
            int(raw_level)
        except (TypeError, ValueError):
            return self._vext_json({"ok": False, "error": "level muss eine ganze Zahl sein"}, 400)
        level = _sesscells.normalize_level(raw_level)
        reg = _sesscell_reg()
        uid = self._principal()
        if reg.get(uid, session) is None:
            reg.provision(uid, session, autonomy=level)
        else:
            reg.set_autonomy(uid, session, level)
        _prov_log("autonomy.set", uid, json.dumps({"session": session, "level": level}), {"wire": "api"})
        lvl = reg.get_autonomy(uid, session)
        out = {"ok": True, "session": session, "level": lvl,
               "label": _sesscells.AUTONOMY_LABELS.get(lvl, str(lvl)),
               "short": getattr(_sesscells, "AUTONOMY_SHORT", {}).get(lvl, str(lvl)),
               "experience": getattr(_sesscells, "AUTONOMY_EXPERIENCE", {}).get(lvl, "")}
        out.update(self._autonomy_meta())
        return self._vext_json(out)

    def _api_freigaben(self, query):

        if _sesscells is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        uid = self._principal()
        prof = _sesscells.load_user_freigaben(uid)
        lvl = prof["level"]
        return self._vext_json({
            "ok": True, "level": lvl,
            "presets": [{"level": L, "short": _sesscells.AUTONOMY_SHORT.get(L, str(L)),
                         "experience": _sesscells.AUTONOMY_EXPERIENCE.get(L, "")}
                        for L in _sesscells.LEVELS],
            "matrix": _sesscells.freigaben_matrix(lvl, prof["overrides"]),
            "tier_labels": {"auto": "Keine", "confirm": "Bestätigung", "twofa": "2FA"}})

    def _api_freigaben_set(self, raw):

        if _sesscells is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        uid = self._principal()
        cur = _sesscells.load_user_freigaben(uid)
        lvl = cur["level"]
        if body.get("level") is not None:
            if isinstance(body.get("level"), bool):
                return self._vext_json({"ok": False, "error": "level muss eine Zahl sein"}, 400)
            try:
                int(body.get("level"))
            except (TypeError, ValueError):
                return self._vext_json({"ok": False, "error": "level muss eine Zahl sein"}, 400)
            lvl = _sesscells.normalize_level(body.get("level"))
        ov = dict(cur["overrides"])
        if isinstance(body.get("overrides"), dict):
            ov = body["overrides"]
        elif body.get("action_class"):
            ov[str(body["action_class"])] = str(body.get("tier") or "auto")
        saved = _sesscells.save_user_freigaben(uid, lvl, ov)
        _prov_log("freigaben.set", uid, json.dumps({"level": saved["level"],
                  "overrides": saved["overrides"]}), {"wire": "api"})
        return self._vext_json({"ok": True, "level": saved["level"],
            "matrix": _sesscells.freigaben_matrix(saved["level"], saved["overrides"]),
            "tier_labels": {"auto": "Keine", "confirm": "Bestätigung", "twofa": "2FA"}})

    def _verify_principal_totp(self, principal, code):

        try:
            reg = self._relay_registry()
            cx = reg.connect()
            if not reg.has_2fa(cx, principal):
                return (False, "kein-2fa")
            ok, reason = reg._check_2fa_code(cx, principal, str(code or "").strip(), allow_step_reuse=False)
            return (bool(ok), reason)
        except Exception as e:
            return (False, "verify-fehler: %s" % e)

    def _autonomy_gate(self, principal, session, action_class, command=None, totp=None):

        reg = _sesscell_reg()
        level = reg.get_autonomy(principal, session) if session else _sesscells.DEFAULT_AUTONOMY
        ac = action_class or _sesscells.classify_action(command or "")
        if ac not in _sesscells._ACTION_CLASSES:
            ac = "external"

        need = _sesscells.requires_2fa(level, ac) or _sesscells.user_requires_2fa(principal, ac)
        info = {"session": session, "level": level, "action_class": ac,
                "short": getattr(_sesscells, "AUTONOMY_SHORT", {}).get(level, str(level)),
                "gate": "twofa" if need else "auto"}
        if not need:
            info["proceed"] = True
            return True, info

        code = str(totp or "").strip()
        if not code:
            info.update({"proceed": False, "need_2fa": True,
                         "error": "Diese Aktion (%s) verlangt auf Stufe „%s“ eine Bestätigung mit "
                                  "deinem Handy-Code (2FA)." % (ac, info["short"])})
            return False, info
        ok, reason = self._verify_principal_totp(principal, code)
        if not ok:
            msg = ("Für dein Konto ist noch kein 2FA/Handy-Code eingerichtet — richte ihn unter "
                   "„Freigaben“ ein; ohne zweiten Faktor wird diese Aktion nicht ausgeführt."
                   if reason == "kein-2fa" else
                   "2FA-Code ungültig — die Aktion wurde NICHT ausgeführt.")
            info.update({"proceed": False, "need_2fa": True, "error": msg, "reason": reason})
            return False, info
        info.update({"proceed": True, "verified": True})
        return True, info

    def _api_autonomy_gate(self, raw):

        if _sesscells is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        session = str(body.get("session") or "")
        if not session:
            return self._vext_json({"ok": False, "error": "session required"}, 400)
        ac = body.get("action_class")
        cmd = body.get("command")
        totp = body.get("totp")
        uid = self._principal()
        proceed, info = self._autonomy_gate(uid, session, ac, command=cmd, totp=totp)
        info["ok"] = True
        _prov_log("autonomy.gate", uid, json.dumps({"session": session, "action_class": info.get("action_class")}),
                  {"wire": "api", "gate": info.get("gate"), "proceed": proceed,
                   "verified": info.get("verified", False)})
        return self._vext_json(info, 200 if proceed else 403)

    def _api_approvals(self, query):
        if not self._is_admin():
            return self.send_html(json.dumps({"ok": False, "error": "nur Owner/Admin"}),
                                  403, [("Content-Type", "application/json")])

        st = self._relay_state_cached()
        item = {"id": "relay-arm", "title": "Off-LAN Fernzugriff scharfschalten",
                "desc": ("Die Box waehlt nach aussen zu wss://relay.brainarbeit.com. Off-LAN cell.exec laeuft "
                         "IN der isolierten Zelle (die microVM IST die Grenze, E2E bewiesen), und JEDER "
                         "Schreib-Job verlangt einen frischen TOTP-Code. Reversibel (Sperren = dunkel)."),
                "armed": st["armed"], "state": st["state"], "known": st["known"],
                "detail": st["detail"], "requires_totp": True,
                "qr_url": "/api/approvals/qr"}
        return self.send_html(json.dumps({"ok": True, "approvals": [item], "pending": self._approval_list_pending()}),
                              200, [("Content-Type", "application/json")])

    _QR_CACHE = []

    def _api_approvals_qr(self):

        if not self._is_admin():
            return self.send_html(json.dumps({"ok": False, "error": "nur Owner/Admin"}),
                                  403, [("Content-Type", "application/json")])
        ent = self._QR_CACHE[0] if self._QR_CACHE else None
        if ent and time.time() - ent[0] < 600:
            qr = ent[1]
        else:
            qr = self._winthin_totp_qr()
            if qr:
                self._QR_CACHE[:] = [(time.time(), qr)]
        if not qr:
            return self.send_html(json.dumps({"ok": False, "error": "2FA-QR nicht verfuegbar "
                                              "(kein Secret hinterlegt oder segno fehlt)"}),
                                  200, [("Content-Type", "application/json")])
        return self.send_html(json.dumps({"ok": True, "qr": qr}),
                              200, [("Content-Type", "application/json")])

    def _api_approval_approve(self, raw):
        ok, why = self._vpn_owner_gate()
        if not ok:
            return self.send_html(json.dumps({"ok": False, "error": "Freigabe nur durch Owner am LAN-Client: %s" % why}),
                                  403, [("Content-Type", "application/json")])
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        code = str(body.get("totp") or "").strip()
        if not code:
            return self.send_html(json.dumps({"ok": False, "error": "TOTP-Code fehlt"}),
                                  400, [("Content-Type", "application/json")])
        vok, reason = self._verify_winthin_totp(code)
        if not vok:
            _prov_log("approval.deny", self._principal(), "relay-arm", {"reason": reason})
            return self.send_html(json.dumps({"ok": False, "error": "TOTP ungueltig (%s)" % reason}),
                                  403, [("Content-Type", "application/json")])
        try:
            os.makedirs(self._APPROVALS_DIR, exist_ok=True)
            with open(os.path.join(self._APPROVALS_DIR, "relay-arm.approved"), "w") as f:
                f.write(json.dumps({"by": self._principal(), "ts": int(time.time())}))
        except Exception as e:
            return self.send_html(json.dumps({"ok": False, "error": "token: %s" % e}),
                                  500, [("Content-Type", "application/json")])
        out, rc = self._relay_arm_ctl("start")
        st = self._relay_state()

        if rc != 0 or not st["known"] or not st["armed"]:
            _prov_log("approval.arm.failed", self._principal(), "relay-arm",
                      {"rc": rc, "out": out[:160], "state": st["state"]})
            return self.send_html(json.dumps({"ok": False, "state": st["state"], "armed": st["armed"],
                                              "known": st["known"],
                                              "error": "Scharfschalten nicht bestätigt (%s)." % st["state"],
                                              "detail": st["detail"], "out": out}),
                                  503, [("Content-Type", "application/json")])
        _prov_log("approval.arm", self._principal(), "relay-arm",
                  {"rc": rc, "out": out[:160], "state": st["state"]})
        return self.send_html(json.dumps({"ok": True, "state": st["state"], "armed": st["armed"],
                                          "known": st["known"], "detail": st["detail"], "out": out}),
                              200, [("Content-Type", "application/json")])

    def _approval_pending_dir(self):
        d = os.path.join(self._APPROVALS_DIR, "pending")
        os.makedirs(d, exist_ok=True)
        return d

    def _approval_create(self, action, detail="", ttl=300, principal=None):
        aid = secrets.token_hex(6)
        rec = {"id": aid, "action": str(action)[:100], "detail": str(detail)[:500],
               "principal": principal or self._principal(), "created": int(time.time()),
               "ttl": int(ttl or 300), "status": "pending"}
        with open(os.path.join(self._approval_pending_dir(), aid + ".json"), "w") as f:
            f.write(json.dumps(rec))
        _prov_log("approval.action.request", rec["principal"], rec["action"], {"id": aid})
        return aid

    def _approval_load(self, aid):
        aid = re.sub(r"[^A-Fa-f0-9]", "", str(aid or ""))[:12]
        if not aid:
            return None
        p = os.path.join(self._approval_pending_dir(), aid + ".json")
        try:
            return json.load(open(p)), p
        except Exception:
            return None

    def _approval_list_pending(self):
        out = []; now = int(time.time()); d = self._approval_pending_dir()
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(d, fn)
            try: rec = json.load(open(p))
            except Exception: continue
            if rec.get("status") == "pending" and (now - rec.get("created", 0)) > rec.get("ttl", 300):
                rec["status"] = "expired"
                try: open(p, "w").write(json.dumps(rec))
                except Exception: pass
            if rec.get("status") == "pending" or (now - rec.get("decided_ts", rec.get("created", 0))) < 300:
                out.append(rec)
        out.sort(key=lambda r: r.get("created", 0), reverse=True)
        return out

    def _approval_wait(self, aid, timeout=120):

        end = time.time() + timeout
        while time.time() < end:
            r = self._approval_load(aid)
            if not r:
                return "gone"
            st = r[0].get("status")
            if st != "pending":
                return st
            time.sleep(1.0)
        return "timeout"

    def _api_approval_action_request(self, raw):
        if not self._is_admin():
            return self.send_html(json.dumps({"ok": False, "error": "nur Owner/Admin"}), 403, [("Content-Type", "application/json")])
        try: body = json.loads(raw or b"{}")
        except Exception: body = {}
        aid = self._approval_create(body.get("action") or "Aktion", body.get("detail") or "", body.get("ttl") or 300)
        return self.send_html(json.dumps({"ok": True, "id": aid}), 200, [("Content-Type", "application/json")])

    def _api_approval_action_decide(self, raw):
        if not self._is_admin():
            return self.send_html(json.dumps({"ok": False, "error": "nur Owner/Admin"}), 403, [("Content-Type", "application/json")])
        try: body = json.loads(raw or b"{}")
        except Exception: body = {}
        r = self._approval_load(body.get("id"))
        if not r:
            return self.send_html(json.dumps({"ok": False, "error": "unbekannte Freigabe"}), 404, [("Content-Type", "application/json")])
        rec, path = r
        if rec.get("status") != "pending":
            return self.send_html(json.dumps({"ok": False, "error": "schon entschieden (%s)" % rec.get("status")}), 409, [("Content-Type", "application/json")])
        decision = str(body.get("decision") or "").lower()
        if decision == "approve":
            vok, reason = self._verify_winthin_totp(str(body.get("totp") or "").strip())
            if not vok:
                return self.send_html(json.dumps({"ok": False, "error": "TOTP ungueltig (%s)" % reason}), 403, [("Content-Type", "application/json")])
            rec["status"] = "approved"
        elif decision == "deny":
            rec["status"] = "denied"
        else:
            return self.send_html(json.dumps({"ok": False, "error": "decision approve|deny"}), 400, [("Content-Type", "application/json")])
        rec["decided_by"] = self._principal(); rec["decided_ts"] = int(time.time())
        try: open(path, "w").write(json.dumps(rec))
        except Exception as e:
            return self.send_html(json.dumps({"ok": False, "error": str(e)}), 500, [("Content-Type", "application/json")])
        _prov_log("approval.action." + rec["status"], self._principal(), rec.get("action", ""), {"id": rec["id"]})
        return self.send_html(json.dumps({"ok": True, "status": rec["status"]}), 200, [("Content-Type", "application/json")])

    def _api_approval_action_status(self, query):
        aid = urllib.parse.parse_qs(query or "").get("id", [""])[0]
        r = self._approval_load(aid)
        if not r:
            return self.send_html(json.dumps({"ok": False, "error": "unbekannt"}), 404, [("Content-Type", "application/json")])
        return self.send_html(json.dumps({"ok": True, "status": r[0].get("status"), "action": r[0].get("action")}), 200, [("Content-Type", "application/json")])

    def _api_approval_revoke(self, raw):
        ok, why = self._vpn_owner_gate()
        if not ok:
            return self.send_html(json.dumps({"ok": False, "error": why}),
                                  403, [("Content-Type", "application/json")])
        out, rc = self._relay_arm_ctl("stop")
        st = self._relay_state()

        if rc != 0 or not st["known"] or st["armed"]:
            _prov_log("approval.disarm.failed", self._principal(), "relay-arm",
                      {"rc": rc, "out": out[:160], "state": st["state"]})
            err = ("Abschalten nicht bestätigt (%s). Es wurde KEIN Abschalten protokolliert — "
                   "bitte den Relay-Schalter auf der Box prüfen." % st["state"])
            return self.send_html(json.dumps({"ok": False, "state": st["state"], "armed": st["armed"],
                                              "known": st["known"], "error": err,
                                              "detail": st["detail"], "out": out}),
                                  503, [("Content-Type", "application/json")])
        _prov_log("approval.disarm", self._principal(), "relay-arm",
                  {"rc": rc, "out": out[:160], "state": st["state"]})
        return self.send_html(json.dumps({"ok": True, "state": st["state"], "armed": st["armed"],
                                          "known": st["known"], "detail": st["detail"], "out": out}),
                              200, [("Content-Type", "application/json")])

    def _api_selftest(self, query):

        if not self._is_admin():
            return self.send_html(json.dumps({"ok": False, "error": "nur Owner/Admin"}),
                                  403, [("Content-Type", "application/json")])
        if query and "run=1" in query:
            try:
                import subprocess
                subprocess.run([os.path.expanduser("~/.local/bin/pn-selftest")],
                               timeout=45, capture_output=True)
            except Exception:
                pass
        try:
            data = json.load(open("/run/brainbox/selftest.json"))
            return self.send_html(json.dumps({"ok": True, "selftest": data}, ensure_ascii=False),
                                  200, [("Content-Type", "application/json")])
        except Exception as e:
            return self.send_html(json.dumps({"ok": False, "error": "kein Boot-Check-Ergebnis: %s" % e}),
                                  200, [("Content-Type", "application/json")])

    def _selftest_page(self):
        if not self._is_admin():
            return self.send_html("Boot-Check ist nur fuer den Owner/Admin sichtbar.", 403)
        return self._html_asset("selftest.html", "selftest view not deployed")

    def _freigaben_page(self):
        if not self._is_admin():
            return self.send_html("Freigaben sind nur fuer den Owner/Admin sichtbar.", 403)
        return self._html_asset("freigaben.html", "freigaben view not deployed")

    def _admin_body(self):
        try:
            return json.loads(self._body().decode("utf-8", "replace") or "{}") or {}
        except Exception:
            return {}

    def _admin_json(self, fn):

        if portal_admin is None:
            return self.send_html(json.dumps({"ok": False, "msg": "admin plane unavailable"}), 503,
                                  [("Content-Type", "application/json")])
        if not self._is_admin():
            return self.send_html(json.dumps({"ok": False, "msg": "forbidden"}), 403,
                                  [("Content-Type", "application/json")])
        try:
            out = fn()
        except Exception as e:
            out = {"ok": False, "msg": "admin error: %s" % e}
        return self.send_html(json.dumps(out, default=str), 200, [("Content-Type", "application/json")])
