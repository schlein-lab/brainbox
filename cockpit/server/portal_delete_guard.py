

import os
import sys

GATE_PRINCIPAL = "owner"

def _relay_registry():

    for _p in (os.environ.get("PNLIB_HOME"), os.path.expanduser("~/portioneer")):
        if _p and os.path.isdir(os.path.join(_p, "relaylib")):
            if _p in sys.path:
                sys.path.remove(_p)
            sys.path.insert(0, _p)
            break
    from relaylib import registry
    return registry

def _close(cx):
    try:
        if cx is not None:
            cx.close()
    except Exception:
        pass

def _audit(prov_log, action, principal, meta):

    if prov_log is None:
        return
    try:
        prov_log("delete.2fa_gate", (principal or GATE_PRINCIPAL), str(action or "delete"),
                 dict(meta or {}, wire="delete-guard"))
    except Exception:
        pass

def require_2fa(principal, code, device_did=None, *, prov_log=None, action="delete"):

    code = str(code or "").strip()

    reg = cx = None
    try:
        reg = _relay_registry()
        cx = reg.connect()
        armed = bool(reg.has_2fa(cx, GATE_PRINCIPAL))
    except Exception as e:
        _close(cx)
        _audit(prov_log, action, principal,
               {"result": "blocked", "reason": "registry_error", "err": type(e).__name__})
        return (False, {"ok": False, "need_2fa": True,
                        "error": "2FA-Prüfung nicht möglich — Löschen ist zur Sicherheit gesperrt."})

    if not armed:
        _close(cx)
        _audit(prov_log, action, principal, {"result": "blocked", "reason": "no_2fa_enrolled"})
        return (False, {
            "ok": False,
            "error": "2FA ist nicht eingerichtet — Löschen ist gesperrt, bis ein zweiter Faktor "
                     "scharfgeschaltet ist.",
            "need_2fa_enrollment": True,
            "enroll_hint": "/settings → Sicherheit → 2FA",
        })

    if not code:
        _close(cx)
        _audit(prov_log, action, principal, {"result": "blocked", "reason": "code_required"})
        return (False, {"ok": False, "need_2fa": True,
                        "error": "2FA-Code erforderlich zum Löschen."})

    ok = False
    try:
        if device_did:
            ok, _reason = reg.verify_stepup_2fa(cx, GATE_PRINCIPAL, str(device_did), code)
        else:
            ok, _reason = reg.verify_2fa(cx, GATE_PRINCIPAL, code)
    except Exception as e:
        _close(cx)
        _audit(prov_log, action, principal,
               {"result": "blocked", "reason": "verify_error", "err": type(e).__name__})
        return (False, {"ok": False, "need_2fa": True,
                        "error": "2FA-Prüfung fehlgeschlagen — Löschen gesperrt."})
    _close(cx)

    if ok:
        _audit(prov_log, action, principal, {"result": "allowed", "stepup": bool(device_did)})
        return (True, None)

    _audit(prov_log, action, principal,
           {"result": "blocked", "reason": "code_rejected", "stepup": bool(device_did)})
    return (False, {"ok": False, "need_2fa": True, "error": "2FA-Code abgelehnt."})
