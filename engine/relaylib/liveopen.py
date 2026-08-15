

LANE_KINDS = ("chat", "voice")
BASE_CAP = "task_type:live.open"

def _err(code, msg, **extra):
    d = {"ok": False, "code": code, "error": msg}
    d.update(extra)
    return (False, d)

def authorize_live_open(req, *, alliance_lookup, verify_stepup, mint_fn,
                        relay_url_default=None, ttl_s=120, require_stepup=True):

    did = str(req.get("did") or "").strip()
    if not did:
        return _err("no_did", "kein Geräteschlüssel im Request")
    if str(req.get("task_type") or "") != "live.open":
        return _err("wrong_verb", "kein live.open-Request")
    kind = str(req.get("kind") or "").strip().lower()
    if kind not in LANE_KINDS:
        return _err("bad_kind", "unbekannte Lane-Art %r" % kind)

    al = alliance_lookup(did)
    if not al:
        return _err("no_alliance", "Gerät ist nicht registriert")
    if al.get("revoked"):
        return _err("revoked", "Gerät ist widerrufen")
    principal = str(al.get("principal") or "").strip()
    if not principal:
        return _err("no_principal", "Alliance ohne Principal")

    caps = set(al.get("caps") or [])
    if BASE_CAP not in caps:
        return _err("cap", "dieses Gerät darf keine Live-Lane öffnen")

    if require_stepup:
        code = str(req.get("stepup_totp") or "").strip()
        if not code:
            return _err("need_stepup", "Live-Lane öffnen verlangt einen frischen 2FA/Biometrie-Beweis",
                        need_2fa=True)
        ok, reason = verify_stepup(principal, did, code)
        if not ok:
            return _err("stepup_failed", "Step-up-2FA ungültig — es wurde nichts geöffnet",
                        need_2fa=True, reason=reason)

    relay_url = str(req.get("relay_url") or relay_url_default or "").strip()
    if not relay_url:
        return _err("no_relay", "kein relay_url angegeben und kein Default gesetzt")

    ticket = mint_fn(principal=principal, device_did=did, relay_url=relay_url, ttl_s=ttl_s)
    return (True, {"ok": True, "principal": principal, "kind": kind, "did": did, "ticket": ticket})
