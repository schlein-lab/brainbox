
import json

try:
    import portal_passkeys as PK
except Exception:
    PK = None

try:
    from portal_users import _login_locked, _login_fail, _login_ok
except Exception:
    _login_locked = _login_fail = _login_ok = None

_session_new = None

def configure(session_new=None):

    global _session_new
    _session_new = session_new

def _read_json(h):
    try:
        raw = h._body() or b"{}"
        obj = json.loads(raw or b"{}")
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}

def _client_ip(h):
    try:
        return h.client_address[0] if h.client_address else "?"
    except Exception:
        return "?"

def dispatch(h, method, path):

    if not (path == "/api/passkey" or path.startswith("/api/passkey/")):
        return False
    if PK is None or not PK.available():
        h._json({"ok": False, "error": "Passkey/Biometrie ist auf dieser Box nicht verfügbar."}, 503)
        return True

    if method == "GET" and path == "/api/passkey/available":
        h._json({"ok": True, "available": True, "rp_id": PK.RP_ID})
        return True

    if method == "POST" and path == "/api/passkey/login/begin":
        body = _read_json(h)
        username = str(body.get("username") or "").strip() or None
        principal = None
        if username:
            try:
                from portal_users import _resolve_login_uid as _rlu
                principal = _rlu(username)
            except Exception:
                principal = None
        opts = PK.login_options(principal)
        h._json({"ok": True, **(opts or {})})
        return True

    if method == "POST" and path == "/api/passkey/login/finish":
        ip = _client_ip(h)
        key = "pk@" + ip
        if _login_locked and _login_locked(key):
            h._json({"ok": False, "error": "Zu viele Versuche — kurz warten."}, 429)
            return True
        body = _read_json(h)
        ok, res = PK.verify_login(str(body.get("cid") or ""), body.get("response") or body)
        if not ok:
            if _login_fail:
                _login_fail(key)
            h._json({"ok": False, "error": "Passkey-Anmeldung fehlgeschlagen (%s)." % res}, 401)
            return True
        principal = res
        if _login_ok:
            _login_ok(key)
        try:
            from portal_users import user_touch_login as _utl
            _utl(principal)
        except Exception:
            pass
        if _session_new is None:
            h._json({"ok": False, "error": "Sitzungsdienst nicht bereit."}, 500)
            return True
        tok = _session_new(principal)
        h.send_html(json.dumps({"ok": True, "user": principal, "redirect": "/"}), 200,
                    [("Content-Type", "application/json"),
                     ("Set-Cookie", "pp_session=%s; %s" % (tok, h._cookie_flags()))])
        return True

    if not h.authed():
        h._json({"ok": False, "error": "nicht angemeldet"}, 403)
        return True
    principal = h._principal()

    if method == "GET" and path == "/api/passkey/list":
        h._json({"ok": True, **PK.list_public(principal)})
        return True

    if method == "POST" and path == "/api/passkey/register/begin":
        try:
            from portal_users import user_get as _ug
            disp = (_ug(principal) or {}).get("display_name") or principal
        except Exception:
            disp = principal
        opts = PK.registration_options(principal, disp)
        h._json({"ok": True, **(opts or {})})
        return True

    if method == "POST" and path == "/api/passkey/register/finish":
        body = _read_json(h)
        ok, info = PK.verify_and_store_registration(
            principal, str(body.get("cid") or ""), body.get("response") or body,
            label=body.get("label"))
        if not ok:
            h._json({"ok": False, "error": "Registrierung fehlgeschlagen (%s)." % info}, 400)
            return True
        h._json({"ok": True, "credential": info})
        return True

    if method == "POST" and path == "/api/passkey/remove":
        body = _read_json(h)
        removed = PK.remove(principal, str(body.get("id") or ""))
        h._json({"ok": bool(removed), "removed": bool(removed)})
        return True

    if method == "POST" and path == "/api/passkey/biometric":
        body = _read_json(h)

        on = bool(body.get("on"))
        val = PK.set_biometric(principal, on)
        h._json({"ok": True, "biometric_confirm": val, "effective": PK.biometric_enabled(principal)})
        return True

    h._json({"ok": False, "error": "unbekannte Passkey-Route"}, 404)
    return True
