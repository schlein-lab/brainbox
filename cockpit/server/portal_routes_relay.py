
import os
import json
import time
import secrets
import hashlib
import threading

def _prov_log(*_a, **_k):
    return None

passkeys_mod = None

_ARM_CHALLENGES = {}
_ARM_CH_TTL = 300.0
_ARM_LK = threading.RLock()

def configure(**kw):

    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

def _pk():

    if passkeys_mod is not None:
        return passkeys_mod
    try:
        import portal_passkeys as _PK
        return _PK
    except Exception:
        return None

def _arm_chal_put(principal, challenge):
    re_id = secrets.token_hex(8)
    now = time.time()
    with _ARM_LK:
        for k in [k for k, v in _ARM_CHALLENGES.items() if v.get("exp", 0) < now]:
            _ARM_CHALLENGES.pop(k, None)
        if len(_ARM_CHALLENGES) > 128:
            for k in list(_ARM_CHALLENGES)[:64]:
                _ARM_CHALLENGES.pop(k, None)
        _ARM_CHALLENGES[re_id] = {"principal": str(principal), "challenge": challenge,
                                  "exp": now + _ARM_CH_TTL}
    return re_id

def _arm_chal_take(re_id, principal):
    with _ARM_LK:
        rec = _ARM_CHALLENGES.pop(str(re_id or ""), None)
    if not rec or rec.get("exp", 0) < time.time():
        return None
    if rec.get("principal") != str(principal):
        return None
    return rec

class RelayArmRoutes:

    _RELAY_GET = {"/api/relay/status": "_api_relay_status"}
    _RELAY_POST = {"/api/relay/arm": "_api_relay_arm",
                   "/api/relay/disarm": "_api_relay_disarm"}

    def _relay_json(self, obj, code=200):
        return self.send_html(json.dumps(obj, ensure_ascii=False), code,
                              [("Content-Type", "application/json")])

    def _relay_dispatch(self, method, path, query="", raw=None):

        m = (method or "").upper()
        name = (self._RELAY_GET if m == "GET" else
                self._RELAY_POST if m == "POST" else {}).get(path)
        if not name:
            return False
        if not self.authed():
            self.send_html("unauthorized", 403)
            return True
        if m == "GET":
            getattr(self, name)(query or "")
        else:
            getattr(self, name)(raw if raw is not None else self._body())
        return True

    def _api_relay_status(self, query):

        try:
            st = self._relay_state_cached()
        except Exception as e:
            return self._relay_json({"ok": False, "error": "Relay-Status nicht abfragbar (%s)" % e,
                                     "state": "unknown", "armed": None, "known": False}, 200)
        return self._relay_json({"ok": True, "state": st.get("state"), "armed": st.get("armed"),
                                 "known": st.get("known"), "detail": st.get("detail"),
                                 "requires_totp": True, "can_arm": bool(self._is_admin())}, 200)

    @staticmethod
    def _relay_body(raw):
        try:
            if isinstance(raw, (bytes, bytearray)):
                b = json.loads(raw.decode("utf-8", "replace") or "{}")
            else:
                b = json.loads(raw or "{}")
        except Exception:
            b = {}
        return b if isinstance(b, dict) else {}

    def _relay_action_digest(self):
        return "sha256:" + hashlib.sha256(b"relay-arm|start").hexdigest()

    def _relay_verify_factor(self, principal, body):

        factor = str(body.get("factor") or "").strip().lower()
        pk_resp = body.get("passkey")
        totp = body.get("totp")

        if factor == "passkey" or pk_resp is not None:
            pk = _pk()
            if pk is None or not getattr(pk, "available", lambda: False)():
                return (False, "error", {"need_2fa": True,
                        "error": "Passkey/Biometrie ist auf dieser Box nicht verfügbar — "
                                 "bitte den Handy-Code (2FA) verwenden."})
            try:
                if not pk.biometric_enabled(principal):
                    return (False, "error", {"need_2fa": True,
                            "error": "Für dein Konto ist die Passkey-Bestätigung nicht aktiviert — "
                                     "aktiviere sie unter „Sicherheit“ oder nutze den Handy-Code (2FA)."})
            except Exception:
                return (False, "error", {"need_2fa": True,
                        "error": "Passkey-Status nicht prüfbar — Scharfschalten gesperrt."})

            if pk_resp is not None:
                rec = _arm_chal_take(body.get("re"), principal)
                if not rec:
                    return (False, "error", {"need_2fa": True,
                            "error": "Passkey-Challenge abgelaufen/unbekannt — bitte erneut beginnen."})
                try:
                    okp, info = pk.verify_ceremony(principal, pk_resp, rec["challenge"])
                except Exception as e:
                    return (False, "error", {"need_2fa": True,
                            "error": "Passkey-Prüfung fehlgeschlagen (%s) — Scharfschalten gesperrt." % e})
                if not okp:
                    return (False, "error", {"need_2fa": True,
                            "error": "Passkey-Bestätigung ungültig — nichts wurde scharfgeschaltet."})
                return (True, "verified", {"factor": "passkey"})

            try:
                re_id = secrets.token_hex(8)
                wa = pk.ceremony_challenge(principal, re_id, self._relay_action_digest())
            except Exception as e:
                return (False, "error", {"need_2fa": True,
                        "error": "Passkey-Challenge nicht erzeugbar (%s)." % e})
            if not wa or not wa.get("challenge"):
                return (False, "error", {"need_2fa": True,
                        "error": "Kein Passkey hinterlegt — bitte den Handy-Code (2FA) verwenden."})
            re2 = _arm_chal_put(principal, wa.get("challenge"))
            return (False, "begin", {"need_2fa": True, "stage": "passkey", "re": re2,
                    "webauthn": {"challenge": wa.get("challenge"), "rpId": wa.get("rpId"),
                                 "allowCredentials": wa.get("allowCredentials", []),
                                 "userVerification": wa.get("userVerification", "required")},
                    "error": "Bitte per Fingerabdruck/Passkey bestätigen."})

        code = str(totp or "").strip()
        if not code:
            return (False, "need_2fa", {"need_2fa": True,
                    "error": "Scharfschalten verlangt einen frischen zweiten Faktor: dein "
                             "Handy-Code (2FA) oder eine Passkey-Bestätigung."})
        try:
            ok2, reason = self._verify_principal_totp(principal, code)
        except Exception as e:
            return (False, "error", {"need_2fa": True,
                    "error": "2FA-Prüfung nicht möglich (%s) — Scharfschalten gesperrt." % e})
        if not ok2:
            msg = ("Für dein Konto ist noch kein 2FA/Handy-Code eingerichtet — richte ihn unter "
                   "„Sicherheit“ ein (pn-pair --arm-2fa)." if reason == "kein-2fa" else
                   "2FA-Code ungültig — es wurde NICHTS scharfgeschaltet.")
            return (False, "need_2fa", {"need_2fa": True, "error": msg, "reason": reason})
        return (True, "verified", {"factor": "totp"})

    def _api_relay_arm(self, raw):

        principal = self._principal()

        if not self._is_admin():
            _prov_log("relay.arm.denied", principal, "relay-arm", {"reason": "not-owner-admin"})
            return self._relay_json({"ok": False,
                                     "error": "Nur der Owner/Admin darf den Relay scharfschalten."}, 403)
        body = self._relay_body(raw)

        ok, stage, payload = self._relay_verify_factor(principal, body)
        if not ok:

            code = 200 if stage == "begin" else 403
            out = {"ok": False}
            out.update(payload)
            if stage != "begin":
                _prov_log("relay.arm.need_2fa", principal, "relay-arm",
                          {"stage": stage, "reason": payload.get("reason")})
            return self._relay_json(out, code)

        factor = payload.get("factor", "?")
        try:
            os.makedirs(self._APPROVALS_DIR, exist_ok=True)
            with open(os.path.join(self._APPROVALS_DIR, "relay-arm.approved"), "w") as f:
                f.write(json.dumps({"by": principal, "ts": int(time.time()), "factor": factor}))
        except Exception as e:
            _prov_log("relay.arm.failed", principal, "relay-arm", {"reason": "token", "err": str(e)})
            return self._relay_json({"ok": False, "error": "Freigabe-Token nicht schreibbar: %s" % e}, 500)

        out, rc = self._relay_arm_ctl("start")
        st = self._relay_state()

        if rc != 0 or not st.get("known") or not st.get("armed"):
            _prov_log("relay.arm.failed", principal, "relay-arm",
                      {"rc": rc, "out": str(out)[:160], "state": st.get("state"), "factor": factor})
            return self._relay_json({"ok": False, "state": st.get("state"), "armed": st.get("armed"),
                                     "known": st.get("known"),
                                     "error": "Scharfschalten nicht bestätigt (%s)." % st.get("state"),
                                     "detail": st.get("detail"), "out": out}, 503)
        _prov_log("relay.arm", principal, "relay-arm",
                  {"rc": rc, "out": str(out)[:160], "state": st.get("state"), "factor": factor})
        return self._relay_json({"ok": True, "state": st.get("state"), "armed": st.get("armed"),
                                 "known": st.get("known"), "detail": st.get("detail"), "out": out}, 200)

    def _api_relay_disarm(self, raw):

        principal = self._principal()
        if not self._is_admin():
            _prov_log("relay.disarm.denied", principal, "relay-arm", {"reason": "not-owner-admin"})
            return self._relay_json({"ok": False,
                                     "error": "Nur der Owner/Admin darf den Relay abschalten."}, 403)
        out, rc = self._relay_arm_ctl("stop")
        st = self._relay_state()

        if rc != 0 or not st.get("known") or st.get("armed"):
            _prov_log("relay.disarm.failed", principal, "relay-arm",
                      {"rc": rc, "out": str(out)[:160], "state": st.get("state")})
            return self._relay_json({"ok": False, "state": st.get("state"), "armed": st.get("armed"),
                                     "known": st.get("known"),
                                     "error": "Abschalten nicht bestätigt (%s). Es wurde KEIN Abschalten "
                                              "protokolliert — bitte den Relay-Schalter auf der Box "
                                              "prüfen." % st.get("state"),
                                     "detail": st.get("detail"), "out": out}, 503)
        _prov_log("relay.disarm", principal, "relay-arm",
                  {"rc": rc, "out": str(out)[:160], "state": st.get("state")})
        return self._relay_json({"ok": True, "state": st.get("state"), "armed": st.get("armed"),
                                 "known": st.get("known"), "detail": st.get("detail"), "out": out}, 200)
