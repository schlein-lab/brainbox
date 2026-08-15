
import os, json, base64, secrets, subprocess, time
import shutil, html
import urllib.parse, urllib.request
import portal_pwpolicy

RESET_HTML = None
SESSIONS = None
_save_sessions = None
_session_new = None
_DEVICE_REG = None
_pairing = None
_CerEntry = None
_IDENTITY_OBSERVE_ONLY = None
_OAUTH_LOCK = None
_OAUTH_SESS = None
_ZKConflict = None
_ZKTooBig = None
_acct = None
_apikeys = None
_apikeys_mod = None
_authorize_llm = None
_ceremony = None
_durable_vault = None
_idreg = None
_idverifier = None
_idverify = None
_inject_once = None
_mailer = None
_oauth_cell_uid = None
_oauth_sess_name = None
_parse_funding = None
_prov_log = None
_secret_vault = None
_tmux = None
_uid_safe = None
_vault_durable = None
_zklink = None
_zkmod = None
_zkrelease = None
_zkvault = None
auth_token_consume = None
auth_token_new = None
cell = None
mailjet_configured = None
mailjet_send = None
mailjet_sender = None
notify_email = None
seat_enumerate = None
seat_focused = None
seat_forge = None
seat_stop = None
system_secret = None
system_secret_set = None
user_get = None
user_get_by_email = None
user_list = None
user_set_email_verified = None
user_set_password = None

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

_RELAY_ST_CACHE = []

import portal_zustand as _zst
_zst.register("portal_routes_identity._RELAY_ST_CACHE", "cache", __name__, ref=_RELAY_ST_CACHE, ttl_s=8.0,
              beschreibung="Relay-Zustand fuer LESE-Anzeigen (Ein-Feld-Zelle); jede frische Probe aktualisiert mit; 2FA-/Freigabe-ENTSCHEIDUNGEN proben IMMER frisch",
              neustart="verfaellt", schreiber="_relay_state() (jede frische Probe)")

class IdentityRoutes:
    def _vault_get(self):

        if _zkvault is None:
            return self.send_html(json.dumps({"error": "vault unavailable"}), 503,
                                  [("Content-Type", "application/json")])
        try:
            got = _zkvault.get(self._principal()) or {"version": 0}
        except Exception as e:
            return self.send_html(json.dumps({"error": str(e)}), 500,
                                  [("Content-Type", "application/json")])
        return self.send_html(json.dumps(got), 200, [("Content-Type", "application/json")])

    def _vault_put(self, body):

        if _zkvault is None:
            return self.send_html(json.dumps({"error": "vault unavailable"}), 503,
                                  [("Content-Type", "application/json")])
        try:
            req = json.loads(body.decode("utf-8", "replace") or "{}") if body else {}
        except Exception:
            return self.send_html(json.dumps({"error": "bad json"}), 400,
                                  [("Content-Type", "application/json")])
        b64 = req.get("blob_b64")
        if not isinstance(b64, str) or not b64:
            return self.send_html(json.dumps({"error": "blob_b64 required"}), 400,
                                  [("Content-Type", "application/json")])
        try:
            blob = base64.b64decode(b64, validate=True)
        except Exception:
            return self.send_html(json.dumps({"error": "blob_b64 not valid base64"}), 400,
                                  [("Content-Type", "application/json")])
        try:
            r = _zkvault.put(self._principal(), blob, req.get("base_version"))
        except _ZKConflict:
            cur = _zkvault.head(self._principal()) or {"version": 0}
            return self.send_html(json.dumps({"error": "conflict", "version": cur["version"]}), 409,
                                  [("Content-Type", "application/json")])
        except _ZKTooBig:
            mx = getattr(_zkmod, "MAX_BLOB_BYTES", 0)
            return self.send_html(json.dumps({"error": "too big", "max": mx}), 413,
                                  [("Content-Type", "application/json")])
        except Exception as e:
            return self.send_html(json.dumps({"error": str(e)}), 400,
                                  [("Content-Type", "application/json")])
        return self.send_html(json.dumps(r), 200, [("Content-Type", "application/json")])

    def _vault_delete(self, body):

        if _zkvault is None:
            return self.send_html(json.dumps({"error": "vault unavailable"}), 503,
                                  [("Content-Type", "application/json")])
        try:
            req = json.loads(body.decode("utf-8", "replace") or "{}") if body else {}
        except Exception:
            req = {}
        try:
            r = _zkvault.delete(self._principal(), req.get("base_version"))
        except _ZKConflict:
            cur = _zkvault.head(self._principal()) or {"version": 0}
            return self.send_html(json.dumps({"error": "conflict", "version": cur["version"]}), 409,
                                  [("Content-Type", "application/json")])
        return self.send_html(json.dumps(r), 200, [("Content-Type", "application/json")])

    def _relbody(self, body):
        try:
            return json.loads(body.decode("utf-8", "replace") or "{}") if body else {}
        except Exception:
            return None

    def _rel_request(self, body):

        if _zkrelease is None:
            return self._json({"error": "release unavailable"}, 503)
        req = self._relbody(body)
        if req is None:
            return self._json({"error": "bad json"}, 400)
        name = (req.get("name") or "").strip()
        if not name:
            return self._json({"error": "name required"}, 400)
        try:
            rid, box_pub = _zkrelease.request(self._principal(), name)
        except Exception as e:
            return self._json({"error": str(e)}, 400)
        return self._json({"req_id": rid, "box_pub": box_pub})

    def _rel_pending(self):

        if _zkrelease is None:
            return self._json({"error": "release unavailable"}, 503)
        return self._json({"pending": _zkrelease.pending(self._principal())})

    def _rel_fulfill(self, body):

        if _zkrelease is None:
            return self._json({"error": "release unavailable"}, 503)
        req = self._relbody(body)
        if req is None:
            return self._json({"error": "bad json"}, 400)
        rid, sealed = req.get("req_id"), req.get("sealed")
        if not rid or not isinstance(sealed, dict):
            return self._json({"error": "req_id + sealed required"}, 400)
        ok = _zkrelease.fulfill(self._principal(), rid, sealed)
        return self._json({"ok": bool(ok)}, 200 if ok else 409)

    def _rel_deny(self, body):

        if _zkrelease is None:
            return self._json({"error": "release unavailable"}, 503)
        req = self._relbody(body) or {}
        ok = _zkrelease.deny(self._principal(), req.get("req_id"), req.get("reason", ""))
        return self._json({"ok": bool(ok)})

    def _rel_consume(self, body):

        if _zkrelease is None:
            return self._json({"error": "release unavailable"}, 503)
        req = self._relbody(body)
        if req is None:
            return self._json({"error": "bad json"}, 400)
        rid = req.get("req_id")
        if not rid:
            return self._json({"error": "req_id required"}, 400)
        out = _zkrelease.consume(self._principal(), rid,
                                 lambda mv: base64.b64encode(bytes(mv)).decode("ascii"))
        if out is None:
            return self._json({"error": "not ready", "state": _zkrelease.status(self._principal(), rid)}, 409)
        return self._json({"value_b64": out})

    def _rel_status(self, query):
        if _zkrelease is None:
            return self._json({"error": "release unavailable"}, 503)
        rid = urllib.parse.parse_qs(query).get("req_id", [""])[0]
        return self._json({"state": _zkrelease.status(self._principal(), rid)})

    def _link_start(self, body):

        if _zklink is None:
            return self._json({"error": "link unavailable"}, 503)
        req = self._relbody(body)
        if req is None:
            return self._json({"error": "bad json"}, 400)
        new_pub = req.get("new_pub")
        if not new_pub or not isinstance(new_pub, str):
            return self._json({"error": "new_pub required"}, 400)
        try:
            link_id, code = _zklink.start(self._principal(), new_pub)
        except Exception as e:
            return self._json({"error": str(e)}, 400)
        return self._json({"link_id": link_id, "code": code})

    def _link_resolve(self, query):

        if _zklink is None:
            return self._json({"error": "link unavailable"}, 503)
        code = urllib.parse.parse_qs(query).get("code", [""])[0]
        res = _zklink.resolve(self._principal(), code)
        return self._json(res if res else {"error": "no such code"}, 200 if res else 404)

    def _link_offer(self, body):

        if _zklink is None:
            return self._json({"error": "link unavailable"}, 503)
        req = self._relbody(body)
        if req is None:
            return self._json({"error": "bad json"}, 400)
        ok = _zklink.offer(self._principal(), req.get("link_id"), req.get("sealed"))
        return self._json({"ok": bool(ok)}, 200 if ok else 409)

    def _link_get(self, query):

        if _zklink is None:
            return self._json({"error": "link unavailable"}, 503)
        link_id = urllib.parse.parse_qs(query).get("link_id", [""])[0]
        res = _zklink.get(self._principal(), link_id)
        return self._json(res if res else {"error": "no such link"}, 200 if res else 404)

    def _identity_status(self):

        if _idverifier is None:
            return self._cer_json({"available": False, "observe_only": _IDENTITY_OBSERVE_ONLY})
        try:
            keys = _idreg.list_keys()
            has_owner = _idreg.has_active_owner()
        except Exception as e:
            return self._cer_json({"available": True, "observe_only": _IDENTITY_OBSERVE_ONLY,
                                   "error": str(e)})
        return self._cer_json({"available": True, "observe_only": _IDENTITY_OBSERVE_ONLY,
                               "has_active_owner": has_owner, "enrolled_keys": len(keys),
                               "keys": keys})

    def _identity_selftest(self):

        if _idverify is None:
            return self._cer_json({"available": False})
        try:
            import ed25519_backend as eb
            from registry import Registry as Reg
            from verify import Verifier, sign_request
            kp = eb.keygen()
            a, b = kp
            if eb.pub_from_priv(a) == b:
                priv, pub = a, b
            elif eb.pub_from_priv(b) == a:
                priv, pub = b, a
            else:
                priv, pub = a, b
            reg = Reg(path=None)
            reg.bootstrap_owner(pub)
            vf = Verifier(reg)
            env = sign_request(priv, pub, principal="owner", verb="terminal.read",
                               args={"tail": 10}, funding="member-subsidized")
            verified = False
            try:
                vf.verify(env); verified = True
            except Exception:
                verified = False
            bad = dict(env)
            bad["sig"] = bad["sig"][:-4] + ("AAAA" if not bad["sig"].endswith("AAAA") else "BBBB")
            tamper_rejected = False
            try:
                vf.verify(bad)
            except Exception:
                tamper_rejected = True
            return self._cer_json({"available": True, "backend": getattr(eb, "BACKEND", "?"),
                                   "verified_good_sig": verified, "tamper_rejected": tamper_rejected,
                                   "ok": bool(verified and tamper_rejected)})
        except Exception as e:
            return self._cer_json({"available": True, "ok": False, "error": str(e)})

    _ENROLL_MAX_PENDING = 32
    _ENROLL_MAX_LABEL = 64

    def _identity_enroll(self):

        if _idreg is None:
            return self._json({"ok": False, "error": "identity nicht verfuegbar"}, 503)
        body = self._json_obj()
        if body is None:
            return
        try:
            if not _idreg.has_active_owner():

                return self._json({"ok": False, "error": "Diese Box hat noch keinen Owner-"
                                   "Schluessel. Der erste Schluessel wird lokal an der Box "
                                   "eingerollt (POST /api/identity/bootstrap)."}, 409)
            wartend = [k for k in _idreg.list_keys() if k.get("state") == "pending"]
            if len(wartend) >= self._ENROLL_MAX_PENDING:
                return self._json({"ok": False, "error": "Zu viele wartende Anmeldungen (%d). "
                                   "Der Owner muss die Liste erst abarbeiten."
                                   % len(wartend)}, 429)
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 500)

        roh = (body.get("pubkey_b64u") or body.get("pubkey") or "").strip()
        label = (body.get("device_label") or "").strip()[:self._ENROLL_MAX_LABEL]
        prinz = (body.get("principal") or "").strip() or "owner"
        if not roh or not label:
            return self._json({"ok": False, "error": "pubkey_b64u und device_label noetig"}, 400)
        try:
            import canonical as _can
            pub = _can.b64u_decode(roh)
        except Exception:
            return self._json({"ok": False, "error": "pubkey_b64u ist kein gueltiges base64url"}, 400)
        if len(pub) != 32:
            return self._json({"ok": False, "error": "Ed25519-Schluessel hat 32 Bytes, dieser %d"
                               % len(pub)}, 400)
        try:
            rec = _idreg.enroll_request(pub, label, prinz)
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 409)
        try:
            _prov_log("identity.enroll_request", prinz, "",
                      {"key_id": rec.key_id, "device_label": label,
                       "von": self.client_address[0]})
        except Exception:
            pass

        return self._json({"ok": True, "key_id": rec.key_id, "state": rec.state,
                           "hinweis": "Aufgenommen als WARTEND. Dieser Schluessel darf nichts, "
                                      "bis der Owner ihn freigibt."}, 202)

    def _identity_pending(self):

        if _idreg is None:
            return self._json({"ok": False, "error": "identity nicht verfuegbar"}, 503)
        if not self._is_admin():
            return self._json({"ok": False, "error": "nur Owner/Admin"}, 403)
        try:
            alle = _idreg.list_keys()
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 500)
        return self._json({"ok": True,
                           "wartend": [k for k in alle if k.get("state") == "pending"],
                           "aktiv": [k for k in alle if k.get("state") == "active"],
                           "widerrufen": [k for k in alle if k.get("state") == "revoked"]})

    def _identity_geraete(self):

        if _idreg is None:
            return self._json({"ok": False, "verfuegbar": False,
                               "error": "Identitaetsschicht nicht verfuegbar"}, 503)
        if not self._is_admin():
            return self._json({"ok": False, "error": "nur Owner/Admin"}, 403)
        try:
            alle = _idreg.list_keys()
            owner_da = _idreg.has_active_owner()
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 500)

        nach_kid = {k.get("key_id"): k for k in alle}

        def _kid_aus_pubkey(roh):

            if not roh:
                return None, "hat beim Koppeln keinen Geraeteschluessel mitgeschickt"
            s = str(roh).strip()
            try:
                if len(s) == 64 and all(c in "0123456789abcdefABCDEF" for c in s):
                    pub = bytes.fromhex(s)
                else:
                    import canonical as _can
                    pub = _can.b64u_decode(s)
            except Exception:
                return None, "der mitgeschickte Schluessel ist unlesbar"
            if len(pub) != 32:
                return None, ("kein Ed25519-Schluessel (%d Bytes statt 32) — dieser Client "
                              "faellt auf ECDSA zurueck, den die Box nicht fuehrt" % len(pub))
            try:
                import canonical as _can
                return _can.key_id(pub), None
            except Exception:
                return None, "Schluessel-ID nicht berechenbar"

        lebt = None
        if _apikeys is not None:
            try:
                jetzt = int(time.time())
                lebt = {k["id"] for k in _apikeys.list()
                        if not k.get("revoked")
                        and not (k.get("expires_at") and jetzt > k["expires_at"])}
            except Exception:
                lebt = None

        ohne = []
        if _DEVICE_REG is not None:
            try:
                geraete = _DEVICE_REG.list_all()
            except Exception:
                geraete = []
            for g in geraete:
                if g.get("source") != "paired" and not str(g.get("id") or "").startswith("client-"):
                    continue
                akid = g.get("apikey_id")
                if lebt is not None and akid and akid not in lebt:
                    continue
                kid = g.get("identity_key_id")
                grund = None
                if not kid:
                    kid, grund = _kid_aus_pubkey(g.get("device_pubkey"))
                rec = nach_kid.get(kid) if kid else None
                if rec is not None and rec.get("state") == "active":
                    continue
                if rec is not None:
                    grund = "Geraeteschluessel ist %s" % rec.get("state")
                ohne.append({"did": g.get("id"), "name": g.get("label") or g.get("name"),
                             "principal": g.get("principal"), "apikey_id": akid,
                             "seit": g.get("updated"), "grund": grund or "unbekannt",
                             "ausgeblendet": bool(g.get("hidden"))})

        return self._json({"ok": True, "verfuegbar": True,
                           "beobachtet_nur": _IDENTITY_OBSERVE_ONLY,
                           "owner_vorhanden": owner_da,
                           "wartend": [k for k in alle if k.get("state") == "pending"],
                           "aktiv": [k for k in alle if k.get("state") == "active"],
                           "widerrufen": [k for k in alle if k.get("state") == "revoked"],
                           "ohne_geraeteschluessel": ohne})

    def _identity_approve(self):

        if _idreg is None or _ceremony is None or _CerEntry is None:
            return self._json({"ok": False, "error": "identity/ceremony nicht verfuegbar"}, 503)
        if not (self._is_admin() and self._is_session_auth()):

            return self._json({"ok": False, "error": "Freigabe braucht eine angemeldete "
                               "Owner-Sitzung (kein Maschinenschluessel)."}, 403)
        body = self._json_obj()
        if body is None:
            return
        kid = (body.get("key_id") or "").strip()
        if not kid:
            return self._json({"ok": False, "error": "key_id fehlt"}, 400)
        try:
            rec = _idreg.get(kid)
        except Exception:
            rec = None
        if rec is None:
            return self._json({"ok": False, "error": "unbekannte key_id"}, 404)
        if getattr(rec, "state", "") == "revoked":
            return self._json({"ok": False, "error": "widerrufener Schluessel wird nicht "
                               "freigegeben"}, 409)

        label = getattr(rec, "device_label", "") or "(ohne Namen)"
        prinz = getattr(rec, "principal", "") or "?"
        gesprochen = ("geraet „%s\" als %s, schluessel %s" % (label, prinz, kid[:8]))
        tgt = _CerEntry(ref="idkey:" + kid, type="identity", label=gesprochen,
                        meta={"key_id": kid, "device_label": label, "principal": prinz})
        re_id = "re-" + secrets.token_hex(6)

        def _do(_kid=kid, _label=label):
            try:
                r = _idreg.approve(_kid)
                try:
                    _prov_log("identity.approve", "owner", "",
                              {"key_id": _kid, "device_label": _label})
                except Exception:
                    pass
                return {"approved": True, "key_id": _kid, "state": getattr(r, "state", "?")}
            except Exception as e:
                return {"approved": False, "error": type(e).__name__ + ": " + str(e)}

        cer, prompt = _ceremony.begin(re_id=re_id, verb="verb.identity_approve",
                                      target=tgt, action=_do)
        return self._cer_json({"action": "ceremony", "re": re_id, "verb": "verb.identity_approve",
                               "readback": prompt.get("readback"), "challenge": prompt.get("challenge"),
                               "hold_ms": prompt.get("hold_ms"), "speak": prompt.get("spoken")})

    def _identity_revoke(self):

        if _idreg is None:
            return self._json({"ok": False, "error": "identity nicht verfuegbar"}, 503)
        if not (self._is_admin() and self._is_session_auth()):
            return self._json({"ok": False, "error": "nur eine angemeldete Owner-Sitzung"}, 403)
        body = self._json_obj()
        if body is None:
            return
        kid = (body.get("key_id") or "").strip()
        if not kid:
            return self._json({"ok": False, "error": "key_id fehlt"}, 400)
        try:
            rec = _idreg.revoke(kid)
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 404)
        try:
            _prov_log("identity.revoke", "owner", "", {"key_id": kid})
        except Exception:
            pass
        return self._json({"ok": True, "key_id": kid, "state": getattr(rec, "state", "revoked")})

    def _identity_rotate(self):

        if _idreg is None:
            return self._json({"ok": False, "error": "identity nicht verfuegbar"}, 503)
        if not (self._is_admin() and self._is_session_auth()):
            return self._json({"ok": False, "error": "nur eine angemeldete Owner-Sitzung"}, 403)
        body = self._json_obj()
        if body is None:
            return
        alt = (body.get("old_key_id") or "").strip()
        roh = (body.get("pubkey_b64u") or body.get("pubkey") or "").strip()
        label = (body.get("device_label") or "").strip()[:self._ENROLL_MAX_LABEL]
        if not alt or not roh:
            return self._json({"ok": False, "error": "old_key_id und pubkey_b64u noetig"}, 400)
        try:
            import canonical as _can
            pub = _can.b64u_decode(roh)
        except Exception:
            return self._json({"ok": False, "error": "pubkey_b64u ist kein gueltiges base64url"}, 400)
        if len(pub) != 32:
            return self._json({"ok": False, "error": "Ed25519-Schluessel hat 32 Bytes"}, 400)
        try:
            rec = _idreg.rotate(alt, pub, label or None)
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 409)
        try:
            _prov_log("identity.rotate", "owner", "", {"alt": alt, "neu": rec.key_id})
        except Exception:
            pass
        return self._json({"ok": True, "alt": alt, "neu": rec.key_id,
                           "state": getattr(rec, "state", "pending"),
                           "hinweis": "Der neue Schluessel WARTET und braucht dieselbe Freigabe "
                                      "wie eine Erstanmeldung."}, 202)

    def _identity_bootstrap(self):

        if _idreg is None:
            return self._json({"ok": False, "error": "identity nicht verfuegbar"}, 503)
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return self._json({"ok": False, "error": "Der erste Owner-Schluessel wird nur "
                               "LOKAL an der Box eingerollt, nie ueber das Netz."}, 403)
        body = self._json_obj()
        if body is None:
            return
        roh = (body.get("pubkey_b64u") or body.get("pubkey") or "").strip()
        label = (body.get("device_label") or "bootstrap-console").strip()[:self._ENROLL_MAX_LABEL]
        if not roh:
            return self._json({"ok": False, "error": "pubkey_b64u fehlt"}, 400)
        try:
            import canonical as _can
            pub = _can.b64u_decode(roh)
        except Exception:
            return self._json({"ok": False, "error": "pubkey_b64u ist kein gueltiges base64url"}, 400)
        if len(pub) != 32:
            return self._json({"ok": False, "error": "Ed25519-Schluessel hat 32 Bytes"}, 400)
        try:
            rec = _idreg.bootstrap_owner(pub, label)
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 409)
        try:
            _prov_log("identity.bootstrap_owner", "owner", "",
                      {"key_id": rec.key_id, "device_label": label})
        except Exception:
            pass
        return self._json({"ok": True, "key_id": rec.key_id, "state": rec.state,
                           "principal": rec.principal})

    def _funding_route(self, query):

        if _parse_funding is None or _authorize_llm is None:
            return self._cer_json({"available": False})
        q = query if isinstance(query, dict) else urllib.parse.parse_qs(query)
        flat = {k: (v[0] if isinstance(v, list) else v) for k, v in q.items()}
        tag = _parse_funding(flat.get("funding"))

        dec = _authorize_llm(tag=tag, byo_remaining_calls=int(flat.get("byo_remaining", 0) or 0),
                             central_has_capacity=True)
        return self._cer_json({"available": True, "funding": tag.to_wire(),
                               "principal": flat.get("principal", "owner"),
                               "granted": dec.granted, "pool": (dec.pool.value if dec.pool else None),
                               "code": dec.code, "message": dec.message})

    def _funding_acct(self):

        if _acct is None:
            return self._cer_json({"available": False})
        try:
            snap = _acct.snapshot()
        except Exception as e:
            return self._cer_json({"available": True, "error": str(e)})
        return self._cer_json({"available": True, "snapshot": snap})

    def _svkey(self, name):
        return self._principal() + "\x00" + str(name)

    def _svault_own_names(self):
        pref = self._principal() + "\x00"
        try:
            return sorted(k[len(pref):] for k in _secret_vault.list_names() if k.startswith(pref))
        except Exception:
            return []

    def _secret_store(self, body):

        if _secret_vault is None:
            return self._cer_json({"ok": False, "error": "vault unavailable"})
        try:
            req = json.loads(body or "{}")
        except Exception:
            req = {}
        name = (req.get("name") or "").strip()
        value = req.get("value")
        if not name or value is None:
            return self._cer_json({"ok": False, "error": "name+value required"})
        _secret_vault.store(self._svkey(name), str(value).encode("utf-8"))

        _prov_log("secret.enroll", self._principal(), name, {"phase": "vault-store", "name": name})
        return self._cer_json({"ok": True, "stored": name, "durable": _vault_durable()})

    def _secret_list(self):

        if _secret_vault is None:
            return self._cer_json({"available": False, "names": []})
        return self._cer_json({"available": True, "names": self._svault_own_names(),
                               "durable": _vault_durable()})

    def _secret_forget(self, body):
        if _secret_vault is None:
            return self._cer_json({"ok": False})
        try:
            name = (json.loads(body or "{}").get("name") or "").strip()
        except Exception:
            name = ""
        key = self._svkey(name)
        if name and _secret_vault.has(key):
            _secret_vault._secrets.pop(key, None)
            _prov_log("secret.forget", self._principal(), name, {"name": name})
            return self._cer_json({"ok": True, "forgot": name})
        return self._cer_json({"ok": False, "error": "unknown name"})

    def _secrets_set(self, body):

        if _durable_vault is None:
            return self._cer_json({"ok": False, "error": "vault unavailable"})
        try:
            req = json.loads(body or "{}")
        except Exception:
            req = {}
        name = (req.get("name") or "").strip()
        value = req.get("value")
        kind = req.get("kind") or None
        if not name or value in (None, ""):
            return self._cer_json({"ok": False, "error": "name+value required"})
        try:
            _durable_vault.set(self._principal(), name, str(value), kind=kind)
        except Exception as e:
            return self._cer_json({"ok": False, "error": "store failed: %s" % type(e).__name__})

        _prov_log("secret.vault_set", self._principal(), name, {"kind": kind, "durable": True})
        return self._cer_json({"ok": True, "name": name, "kind": kind})

    def _secrets_list(self):

        if _durable_vault is None:
            return self._cer_json({"available": False, "durable": True, "secrets": []})
        return self._cer_json({"available": True, "durable": True,
                               "secrets": _durable_vault.list_names(self._principal())})

    def _secrets_delete(self, body):

        if _durable_vault is None:
            return self._cer_json({"ok": False, "error": "vault unavailable"})
        try:
            name = (json.loads(body or "{}").get("name") or "").strip()
        except Exception:
            name = ""
        if name and _durable_vault.delete(self._principal(), name):
            _prov_log("secret.vault_delete", self._principal(), name, {"durable": True})
            return self._cer_json({"ok": True, "deleted": name})
        return self._cer_json({"ok": False, "error": "unknown name"})

    def _auth_forgot(self, body):

        jct = [("Content-Type", "application/json")]
        try:
            req = json.loads(body or "{}")
        except Exception:
            req = {}
        ident = (req.get("user") or req.get("email") or "").strip()
        uid = None
        if ident:
            uid = user_get_by_email(ident) if "@" in ident else ((user_get(ident) or {}).get("uid"))
        if uid:
            to = (user_get(uid) or {}).get("email")
            if to:
                tok = auth_token_new(uid, "reset", email=to, ttl=3600)
                link = self._client_base() + "/reset?token=" + tok
                notify_email(to, "Brainarbeit - Passwort zuruecksetzen",
                    "Es wurde ein Zuruecksetzen deines Brainarbeit-Passworts angefordert.\n\n"
                    "Oeffne diesen Link (1 Stunde gueltig):\n" + link + "\n\n"
                    "Warst du das nicht, ignoriere diese E-Mail - dein Passwort bleibt unveraendert.\n\n- Brainarbeit")
            _prov_log("auth.forgot", uid, "reset", {"emailed": bool(to)})
        return self.send_html(json.dumps({"ok": True,
            "msg": "Falls ein Konto existiert, wurde eine E-Mail mit einem Reset-Link gesendet."}), 200, jct)

    def _auth_reset(self, body):

        jct = [("Content-Type", "application/json")]
        try:
            req = json.loads(body or "{}")
        except Exception:
            req = {}
        tok = (req.get("token") or "").strip(); pw = req.get("password") or ""

        _pok, _pwhy = portal_pwpolicy.check(pw)
        if not _pok:
            return self.send_html(json.dumps({"ok": False, "error": _pwhy}), 400, jct)
        uid = auth_token_consume(tok, "reset")
        if not uid:
            return self.send_html(json.dumps({"ok": False, "error": "Link ungueltig oder abgelaufen."}), 400, jct)
        ok = user_set_password(uid, pw)
        _prov_log("auth.reset", uid, "reset", {"ok": ok})
        return self.send_html(json.dumps({"ok": ok, "msg": "Passwort aktualisiert." if ok else "Fehlgeschlagen."}),
                              (200 if ok else 400), jct)

    def _auth_reset_page(self, query):

        tok = urllib.parse.parse_qs(query or "").get("token", [""])[0]
        return self.send_html(RESET_HTML.replace("%TOKEN%", html.escape(tok, quote=True)))

    def _auth_verify(self, query):

        tok = urllib.parse.parse_qs(query or "").get("token", [""])[0]
        uid = auth_token_consume(tok, "verify")
        if uid:
            user_set_email_verified(uid, 1); _prov_log("auth.verify", uid, "verify", {})
            m = "E-Mail-Adresse bestaetigt. Du kannst dieses Fenster schliessen."
        else:
            m = "Bestaetigungslink ungueltig oder abgelaufen."
        page = ("<!doctype html><meta charset=utf-8><title>Brainarbeit</title>"
                "<body style='font-family:system-ui;background:#0e1116;color:#e6e9ef;display:grid;"
                "place-items:center;height:100vh;margin:0'><div style='max-width:28rem;padding:2rem;"
                "text-align:center'><h1 style='font-size:1.15rem'>Brainarbeit</h1><p>" + html.escape(m) +
                "</p><a href='/login' style='color:#6ea8fe'>Zum Login</a></div></body>")
        return self.send_html(page)

    def _auth_send_verification(self, body):

        jct = [("Content-Type", "application/json")]
        uid = self._principal(); u = user_get(uid) or {}
        try:
            to = (json.loads(body or "{}").get("email") or "").strip() or u.get("email")
        except Exception:
            to = u.get("email")
        if not to:
            return self.send_html(json.dumps({"ok": False, "error": "Keine E-Mail-Adresse hinterlegt."}), 400, jct)
        tok = auth_token_new(uid, "verify", email=to, ttl=86400)
        link = self._client_base() + "/api/auth/verify?token=" + tok
        ok, detail = notify_email(to, "Brainarbeit - E-Mail bestaetigen",
            "Bitte bestaetige deine E-Mail-Adresse fuer Brainarbeit:\n\n" + link + "\n\n(24 Stunden gueltig)\n\n- Brainarbeit")
        _prov_log("auth.send_verification", uid, "verify", {"ok": ok})
        return self.send_html(json.dumps({"ok": ok, "detail": detail}), 200, jct)

    def _mail_config_set(self, body):
        n = 0
        for src, name in (("mailjet_apikey", "mailjet_apikey"), ("mailjet_apisecret", "mailjet_apisecret"),
                          ("sender", "mailjet_sender"), ("sender_name", "mailjet_sender_name")):
            v = body.get(src)
            if v not in (None, "") and system_secret_set(name, str(v)):
                n += 1
        _prov_log("mail.config_set", self._principal(), "mailjet", {"fields": n})
        return {"ok": True, "updated": n, "configured": mailjet_configured(), "sender": mailjet_sender()}

    def _mail_test(self, body):
        to = (body.get("to") or "").strip()
        if not to:
            return {"ok": False, "error": "Feld 'to' (Empfaenger) erforderlich."}
        ok, detail = mailjet_send(to, "Brainarbeit - Test-E-Mail",
            "Test vom Brainarbeit-System (zentraler Mailjet-Versand). Siehst du das, funktioniert der Portal-Mailversand.")
        _prov_log("mail.test", self._principal(), to, {"ok": ok})
        return {"ok": ok, "detail": detail, "to": to}

    def _mail_senders(self):
        key = system_secret("mailjet_apikey"); sec = system_secret("mailjet_apisecret")
        if not (key and sec) or _mailer is None:
            return {"ok": False, "error": "mailjet not configured"}
        ok, data = _mailer.probe_senders(key, sec)
        return {"ok": ok, "senders": data if ok else [], "error": None if ok else data}

    def _oauth_teardown(self, aid):

        aid = str(aid)
        with _OAUTH_LOCK:
            _OAUTH_SESS.pop(aid, None)
        _tmux("kill-session", "-t", _oauth_sess_name(aid))
        cell_uid = _oauth_cell_uid(aid)
        try:
            seat_stop(cell_uid)
        except Exception:
            pass
        try:
            prof = cell(cell_uid).profile
            if prof and os.path.isdir(prof):
                shutil.rmtree(prof, ignore_errors=True)
        except Exception:
            pass

    def _keys_create(self, body):

        jct = [("Content-Type", "application/json")]
        if _apikeys is None:
            return self.send_html(json.dumps({"ok": False, "error": "api keys unavailable"}), 503, jct)
        if not self._is_session_auth():
            return self.send_html(json.dumps({"ok": False, "error": "key management requires an interactive login"}), 403, jct)
        try:
            req = json.loads(body or "{}")
        except Exception:
            req = {}
        label = str(req.get("label") or "")[:80]
        scopes = [s for s in (req.get("scopes") or []) if isinstance(s, str) and s.startswith("/")]
        try:
            ttl_days = int(req.get("ttl_days") or 0); rate_per_min = int(req.get("rate_per_min") or 0)
        except (TypeError, ValueError):
            ttl_days = rate_per_min = 0
        target = self._principal()
        fp = str(req.get("for_principal") or "").strip()
        if fp and fp != target:
            if not self._is_admin():
                return self.send_html(json.dumps({"ok": False, "error": "only admin may mint keys for another principal"}), 403, jct)
            target = _uid_safe(fp)
        kid, key = _apikeys.create(target, label=label, scopes=scopes, ttl_days=ttl_days, rate_per_min=rate_per_min)
        _prov_log("apikey.create", self._principal(), kid,
                  {"for": target, "scopes": scopes, "label": label, "ttl_days": ttl_days, "rate_per_min": rate_per_min})
        return self.send_html(json.dumps({"ok": True, "id": kid, "key": key, "uid": target, "label": label,
                                          "scopes": scopes, "ttl_days": ttl_days, "rate_per_min": rate_per_min,
                                          "note": "store this key now — it is shown only once"}), 200, jct)

    def _keys_list(self, query=""):

        jct = [("Content-Type", "application/json")]
        if _apikeys is None:
            return self.send_html(json.dumps({"ok": True, "keys": []}), 200, jct)
        if not self._is_session_auth():
            return self.send_html(json.dumps({"ok": False, "error": "key management requires an interactive login"}), 403, jct)
        is_admin = self._is_admin()
        allf = urllib.parse.parse_qs(query).get("all", ["0"])[0] in ("1", "true")
        uid = None if (allf and is_admin) else self._principal()
        out = {"ok": True, "keys": _apikeys.list(uid), "is_admin": is_admin,
               "catalog": getattr(_apikeys_mod, "SCOPE_CATALOG", [])}
        if is_admin:
            try:
                out["principals"] = sorted({u.get("uid") for u in user_list() if u.get("uid")})
            except Exception:
                out["principals"] = []
        return self.send_html(json.dumps(out), 200, jct)

    def _keys_revoke(self, kid):

        jct = [("Content-Type", "application/json")]
        if _apikeys is None:
            return self.send_html(json.dumps({"ok": False, "error": "api keys unavailable"}), 503, jct)
        if not self._is_session_auth():
            return self.send_html(json.dumps({"ok": False, "error": "key management requires an interactive login"}), 403, jct)
        owner = None if self._is_admin() else self._principal()
        ok = _apikeys.revoke(kid, owner_uid=owner)
        if ok:
            _prov_log("apikey.revoke", self._principal(), kid, {})
        return self.send_html(json.dumps({"ok": ok, "revoked": kid if ok else None}), (200 if ok else 404), jct)

    def _keys_update(self, kid, body):

        jct = [("Content-Type", "application/json")]
        if _apikeys is None:
            return self.send_html(json.dumps({"ok": False, "error": "api keys unavailable"}), 503, jct)
        if not self._is_session_auth():
            return self.send_html(json.dumps({"ok": False, "error": "key management requires an interactive login"}), 403, jct)
        try:
            req = json.loads(body or "{}")
        except Exception:
            req = {}
        kw = {}
        if isinstance(req.get("scopes"), list):
            kw["scopes"] = [s for s in req["scopes"] if isinstance(s, str) and s.startswith("/")]
        if "label" in req:
            kw["label"] = str(req.get("label") or "")[:80]
        if "rate_per_min" in req:
            kw["rate_per_min"] = req.get("rate_per_min")
        if "ttl_days" in req:
            kw["ttl_days"] = req.get("ttl_days")
        if not kw:
            return self.send_html(json.dumps({"ok": False, "error": "nothing to update"}), 400, jct)
        owner = None if self._is_admin() else self._principal()
        meta = _apikeys.update(kid, owner_uid=owner, **kw)
        if meta is None:
            return self.send_html(json.dumps({"ok": False, "error": "unknown / not owned / revoked"}), 404, jct)
        _prov_log("apikey.update", self._principal(), kid,
                  {"scopes": kw.get("scopes"), "label": kw.get("label"),
                   "rate_per_min": kw.get("rate_per_min"), "ttl_days": kw.get("ttl_days")})
        return self.send_html(json.dumps({"ok": True, "key": meta}), 200, jct)

    def _pair_qr(self, uri):

        try:
            import io as _io, base64 as _b64, segno as _sg
            buf = _io.BytesIO()
            _sg.make(uri, error="m").save(buf, kind="png", scale=6, border=3)
            return "data:image/png;base64," + _b64.b64encode(buf.getvalue()).decode()
        except Exception:
            return None

    def _pair_app_qr(self, body):

        import time as _t
        jct = [("Content-Type", "application/json")]
        if not self._is_session_auth():
            return self.send_html(json.dumps({"ok": False, "error": "Bitte anmelden."}), 403, jct)
        try:
            req = json.loads(body or "{}")
        except Exception:
            req = {}
        principal = self._principal()
        try:
            reg = self._relay_registry(); cx = reg.connect()
        except Exception:
            return self.send_html(json.dumps({"ok": False, "error": "Relay-Registry nicht verfuegbar."}), 503, jct)
        try:
            if not reg.has_2fa(cx, principal):
                reg.arm_2fa(cx, principal)
        except Exception:
            return self.send_html(json.dumps({"ok": False, "error": "2FA konnte nicht eingerichtet werden."}), 500, jct)
        code = (req.get("code") or "").strip() or None
        if code:
            r = cx.execute("SELECT used_at,expires_at,locked_until,principal FROM pairings WHERE code_hash=?",
                           (reg._hk(cx, code),)).fetchone()
            now = _t.time()
            if not (r and r["principal"] == principal and r["used_at"] is None and now < r["expires_at"]
                    and not (r["locked_until"] and now < r["locked_until"])):
                code = None
        if not code:
            try:
                code = reg.mint_pairing(cx, principal, ["task_type:cell.exec", "msg:write"], parent_principal=principal,
                                        label="App (app.brainarbeit.com)", ttl_s=1800)
            except Exception as e:
                return self.send_html(json.dumps({"ok": False, "error": "Konnte keinen Code erzeugen: %s" % e}), 500, jct)
        try:
            row = cx.execute("SELECT secret_enc FROM principals_2fa WHERE principal=?", (principal,)).fetchone()
            code2fa = reg._totp.code_at(reg._unwrap_secret(cx, row["secret_enc"]))
        except Exception:
            code2fa = ""
        url = "https://app.brainarbeit.com/#c=" + urllib.parse.quote(code, safe="") + "&t=" + code2fa
        return self.send_html(json.dumps({"ok": True, "code": code, "url": url, "qr": self._pair_qr(url),
                                          "refresh_in": 25, "app": "app.brainarbeit.com"}), 200, jct)

    def _pair_mint(self, body):

        jct = [("Content-Type", "application/json")]
        if _pairing is None:
            return self.send_html(json.dumps({"ok": False, "error": "pairing unavailable"}), 503, jct)
        if not self._is_session_auth():
            return self.send_html(json.dumps({"ok": False, "error": "pairing requires an interactive login"}), 403, jct)
        try:
            req = json.loads(body or "{}")
        except Exception:
            req = {}
        label = str(req.get("label") or "")[:80]
        scopes = [s for s in (req.get("scopes") or []) if isinstance(s, str) and s.startswith("/")]
        try:
            ttl_s = int(req.get("ttl_s") or 600)
            key_ttl_days = int(req.get("key_ttl_days") or 0)
            rate_per_min = int(req.get("rate_per_min") or 0)
        except (TypeError, ValueError):
            ttl_s, key_ttl_days, rate_per_min = 600, 0, 0
        ttl_s = max(60, min(3600, ttl_s))
        target = self._principal()
        fp = str(req.get("for_principal") or "").strip()
        if fp and fp != target:
            if not self._is_admin():
                return self.send_html(json.dumps({"ok": False, "error": "only admin may mint pairings for another principal"}), 403, jct)
            target = _uid_safe(fp)
        try:
            _pairing.gc()
        except Exception:
            pass
        pid, code = _pairing.mint(target, scopes=scopes, label=label, ttl_s=ttl_s,
                                  minted_by=self._principal(), key_ttl_days=key_ttl_days, rate_per_min=rate_per_min)
        url = self._client_base() + "/pair?code=" + urllib.parse.quote(code, safe="")
        qr = self._pair_qr(url)
        _prov_log("pair.mint", self._principal(), pid, {"for": target, "scopes": scopes, "label": label, "ttl_s": ttl_s})
        return self.send_html(json.dumps({"ok": True, "pid": pid, "code": code, "url": url, "qr": qr,
                                          "principal": target, "expires_in": ttl_s, "scopes": scopes}), 200, jct)

    def _pair_info(self, query):

        jct = [("Content-Type", "application/json")]
        if _pairing is None:
            return self.send_html(json.dumps({"ok": False, "error": "pairing unavailable"}), 503, jct)
        code = urllib.parse.parse_qs(query or "").get("code", [""])[0]
        meta = _pairing.peek(code) if code else None
        if not meta:
            return self.send_html(json.dumps({"ok": False, "error": "Code ungueltig oder abgelaufen."}), 404, jct)
        totp_required = False
        try:
            reg = self._relay_registry()
            cx = reg.connect()
            totp_required = bool(reg.has_2fa(cx, meta["uid"]))
        except Exception:
            totp_required = False
        now = int(time.time())
        return self.send_html(json.dumps({"ok": True, "box": self.cfg.get("name") or "Brainbox",
                                          "principal": meta["uid"], "label": meta.get("label", ""),
                                          "totp_required": totp_required,
                                          "expires_in": max(0, int(meta.get("expires", now)) - now)}), 200, jct)

    def _pair_page(self, query=""):

        return self._html_asset("pair.html", "pairing view not deployed")

    def _pair_redeem(self, body, client_ip):

        jct = [("Content-Type", "application/json")]
        if _pairing is None or _apikeys is None:
            return self.send_html(json.dumps({"ok": False, "error": "pairing unavailable"}), 503, jct)
        from portal_users import _login_locked, _login_fail, _login_ok
        ip = client_ip or "?"
        thr = "pair@" + ip
        if _login_locked(thr):
            return self.send_html(json.dumps({"ok": False, "error": "Zu viele Versuche — kurz warten."}), 429, jct)
        try:
            raw = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else (body or "{}")
            req = json.loads(raw or "{}")
        except Exception:
            req = {}
        code = (req.get("code") or "").strip()
        label = str(req.get("label") or "")[:80]
        totp = str(req.get("totp") or "").strip()
        device_pubkey = req.get("device_pubkey") or None
        want_session = bool(req.get("session"))
        meta = _pairing.peek(code) if code else None
        if not meta:
            _login_fail(thr)
            return self.send_html(json.dumps({"ok": False, "error": "Code ungueltig, abgelaufen oder bereits benutzt."}), 400, jct)

        reg = cx = None
        try:
            reg = self._relay_registry()
            cx = reg.connect()
            totp_required = bool(reg.has_2fa(cx, meta["uid"]))
        except Exception:
            totp_required = False
        if totp_required:
            ok2 = False
            try:
                ok2, _reason = reg.verify_2fa(cx, meta["uid"], totp)
            except Exception:
                ok2 = False
            if not ok2:
                _pairing.fail(code); _login_fail(thr)
                return self.send_html(json.dumps({"ok": False, "need_2fa": True,
                                                  "error": "Zwei-Faktor-Code erforderlich oder falsch."}), 403, jct)
        meta = _pairing.redeem(code)
        if not meta:
            _login_fail(thr)
            return self.send_html(json.dumps({"ok": False, "error": "Code ungueltig, abgelaufen oder bereits benutzt."}), 400, jct)
        _login_ok(thr)
        kid, keyval = _apikeys.create(meta["uid"], label=("Geraet: " + (label or "Gepairtes Geraet"))[:80],
                                      scopes=meta.get("scopes") or [], ttl_days=meta.get("key_ttl_days") or 0,
                                      rate_per_min=meta.get("rate_per_min") or 0)
        did = "client-" + kid
        if _DEVICE_REG is not None:
            try:
                _DEVICE_REG.register(did, (label or "Gepairtes Geraet"), "client",
                                     transport={"addr": ip}, source="paired")
                with _DEVICE_REG._lock:
                    _DEVICE_REG._put(did, {"apikey_id": kid, "principal": meta["uid"],
                                           "device_pubkey": device_pubkey})
            except Exception:
                pass
        extra = list(jct)
        if want_session and _session_new is not None:

            stok = _session_new(meta["uid"])
            extra = list(jct) + [("Set-Cookie", "pp_session=%s; %s" % (stok, self._cookie_flags()))]

        identitaet = None
        if device_pubkey and _idreg is not None:
            roh = str(device_pubkey).strip()
            pub = None
            fehler = None
            try:
                if len(roh) == 64 and all(c in "0123456789abcdefABCDEF" for c in roh):
                    pub = bytes.fromhex(roh)
                else:
                    import canonical as _can
                    pub = _can.b64u_decode(roh)
            except Exception as e:
                fehler = "unlesbarer Schluessel (%s)" % type(e).__name__
            if pub is not None and len(pub) != 32:

                fehler = ("kein Ed25519-Schluessel (%d Bytes statt 32) — dieses Geraet ist "
                          "gekoppelt, aber NICHT mit einem Gerateschluessel bekannt" % len(pub))
                pub = None
            if pub is not None:
                try:
                    _rec = _idreg.enroll_request(pub, (label or "Gepairtes Geraet")[:64], meta["uid"])
                    _idreg.approve(_rec.key_id, approver_key_id="pairing:" + did)
                    identitaet = {"eingerollt": True, "freigegeben": True, "key_id": _rec.key_id}
                    if _DEVICE_REG is not None:

                        try:
                            with _DEVICE_REG._lock:
                                _DEVICE_REG._put(did, {"identity_key_id": _rec.key_id})
                        except Exception:
                            pass
                    _prov_log("identity.enroll_via_pairing", meta["uid"], did,
                              {"key_id": _rec.key_id, "device_label": label})
                except Exception as e:
                    fehler = "%s: %s" % (type(e).__name__, e)
            if identitaet is None:
                identitaet = {"eingerollt": False, "fehler": fehler or "unbekannt"}
        elif device_pubkey and _idreg is None:
            identitaet = {"eingerollt": False, "fehler": "Identitaetsschicht nicht verfuegbar"}

        _prov_log("pair.redeem", meta["uid"], did, {"kid": kid, "scopes": meta.get("scopes") or [], "session": want_session})

        _antwort = {"ok": True, "principal": meta["uid"], "token": keyval,
                    "did": did, "caps": meta.get("scopes") or []}
        if identitaet is not None:
            _antwort["identitaet"] = identitaet
        return self.send_html(json.dumps(_antwort), 200, extra)

    def _pair_pending(self):

        jct = [("Content-Type", "application/json")]
        if _pairing is None:
            return self.send_html(json.dumps({"ok": True, "pending": []}), 200, jct)
        if not self._is_session_auth():
            return self.send_html(json.dumps({"ok": False, "error": "pairing requires an interactive login"}), 403, jct)
        uid = None if self._is_admin() else self._principal()
        return self.send_html(json.dumps({"ok": True, "pending": _pairing.pending(uid)}), 200, jct)

    def _pair_cancel(self, body):

        jct = [("Content-Type", "application/json")]
        if _pairing is None:
            return self.send_html(json.dumps({"ok": False, "error": "pairing unavailable"}), 503, jct)
        if not self._is_session_auth():
            return self.send_html(json.dumps({"ok": False, "error": "pairing requires an interactive login"}), 403, jct)
        try:
            req = json.loads(body or "{}")
        except Exception:
            req = {}
        pid = str(req.get("pid") or "")
        owner = None if self._is_admin() else self._principal()
        ok = _pairing.cancel(pid, owner_uid=owner)
        if ok:
            _prov_log("pair.cancel", self._principal(), pid, {})
        return self.send_html(json.dumps({"ok": ok, "cancelled": pid if ok else None}), (200 if ok else 404), jct)

    def _credential_enter(self, body):

        if _secret_vault is None or _ceremony is None or _inject_once is None:
            return self._cer_json({"action": "refuse", "speak": "Secret-Subsystem ist nicht geladen."})
        try:
            req = json.loads(body or "{}")
        except Exception:
            req = {}
        name = (req.get("name") or "").strip()
        if not name:
            return self._cer_json({"action": "refuse",
                "speak": "Welche Zugangsdaten? Nenne den Namen — nie das Passwort selbst."})
        vkey = self._svkey(name)
        if not _secret_vault.has(vkey):
            return self._cer_json({"action": "refuse",
                "speak": "Ich kenne keine Zugangsdaten namens „%s\". Der Client muss sie erst hinterlegen." % name})
        apps = seat_enumerate()
        foc = seat_focused(apps)
        if not foc:
            return self._cer_json({"action": "refuse",
                "speak": "Kein Fenster ist aktiv, in das ich Zugangsdaten eintragen könnte."})
        origin = foc["title"]

        spoken = "login-formular auf %s — Zugangsdaten „%s\"" % (origin, name)
        tgt = _CerEntry(ref="cred:" + name, type="credential", label=spoken,
                        meta={"origin": origin})
        re_id = "re-" + secrets.token_hex(6)

        def _do(_name=name, _origin=origin, _key=vkey):

            def provider():
                return _secret_vault.fetch(_key)

            def consumer(view):
                val = bytes(view).decode("utf-8", "replace")
                ok, _reply, _forged = seat_forge("text", text=val)
                return {"typed": bool(ok)}
            try:
                r = _inject_once(provider, consumer, require_mlock=False)
            except Exception as e:
                return {"injected": False, "error": type(e).__name__}
            typed = bool(r.get("typed")) if isinstance(r, dict) else bool(r)
            return {"injected": typed, "origin": _origin}

        cer, prompt = _ceremony.begin(re_id=re_id, verb="verb.credential_enter", target=tgt, action=_do)
        return self._cer_json({"action": "ceremony", "re": re_id, "verb": "verb.credential_enter",
                               "readback": prompt.get("readback"), "challenge": prompt.get("challenge"),
                               "hold_ms": prompt.get("hold_ms"), "speak": prompt.get("spoken")})

    def _relay_arm_ctl(self, verb):

        try:
            r = subprocess.run(["sudo", "-n", "/usr/local/sbin/pn-relay-arm", verb],
                               capture_output=True, text=True, timeout=25)
            return (((r.stdout or "").strip() or (r.stderr or "").strip()), r.returncode)
        except Exception as e:
            return ("ctl error: %s" % e), 1

    def _relay_state_cached(self, ttl=8.0):

        ent = _RELAY_ST_CACHE[0] if _RELAY_ST_CACHE else None
        if ent and time.time() - ent[0] < ttl:
            return dict(ent[1])
        return self._relay_state()

    def _relay_state(self):

        out, rc = self._relay_arm_ctl("status")
        if rc == 0 and out.startswith("active"):
            st = {"state": "armed", "armed": True, "known": True,
                  "detail": "Scharf — die Box ist nach aussen verbunden.", "raw": out}
        elif rc == 0:
            st = {"state": "disarmed", "armed": False, "known": True,
                  "detail": "Dunkel — kein Fernzugriff.", "raw": out}
        else:
            st = {"state": "unknown", "armed": None, "known": False,
                  "detail": self._relay_unknown_detail(out), "raw": out}
        _RELAY_ST_CACHE[:] = [(time.time(), dict(st))]
        return st

    @staticmethod
    def _relay_unknown_detail(out):

        low = (out or "").lower()
        if "a password is required" in low or "may not run" in low or "not allowed" in low:
            why = "die Box darf den Relay-Schalter nicht abfragen (keine sudo-Regel für pn-relay-arm)"
        elif "no such file" in low or "command not found" in low:
            why = "der Relay-Schalter ist auf dieser Box nicht installiert (pn-relay-arm fehlt)"
        elif "timed out" in low or "timeout" in low:
            why = "der Relay-Schalter hat nicht geantwortet (Zeitüberschreitung)"
        else:
            why = "der Relay-Schalter ist nicht abfragbar"
        return "Status unbekannt — %s. Solange das so ist, kann die Box weder bestätigen noch " \
               "ausschliessen, dass der Fernzugriff scharf ist." % why

    def _relay_registry(self):

        import sys as _s
        for _p in (os.environ.get("PNLIB_HOME"), os.path.expanduser("~/portioneer")):
            if _p and os.path.isdir(os.path.join(_p, "relaylib")):
                if _p in _s.path:
                    _s.path.remove(_p)
                _s.path.insert(0, _p)
                break
        from relaylib import registry
        return registry
