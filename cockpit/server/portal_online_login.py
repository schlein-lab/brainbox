
import json
import os

_TRUE = ("1", "true", "yes", "on", "ja")
_FALSE = ("0", "false", "no", "off", "nein")

ENV_REQUIRE_2FA = "PN_LOGIN_ONLINE_REQUIRE_2FA"

ENV_IP_HEURISTIC = "PN_LOGIN_ONLINE_IP_HEURISTIC"

def _truthy(v, default=False):
    if v is None:
        return default
    s = str(v).strip().lower()
    if s == "":
        return default
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return default

def online_requires_2fa():

    return _truthy(os.environ.get(ENV_REQUIRE_2FA), default=True)

def _ip_is_remote(ip):

    ip = str(ip or "").strip()
    if not ip or ip in ("?", "::1"):
        return False
    try:
        import ipaddress
        a = ipaddress.ip_address(ip)
        return not (a.is_private or a.is_loopback or a.is_link_local
                    or a.is_reserved or a.is_unspecified)
    except Exception:
        return False

def is_online(header_online=None, form=None, client_ip=None):

    if _truthy(header_online):
        return True
    if form is not None:
        try:
            fv = form.get("online", [""])
            fv = fv[0] if isinstance(fv, (list, tuple)) else fv
        except Exception:
            fv = ""
        if _truthy(fv):
            return True
    if _truthy(os.environ.get(ENV_IP_HEURISTIC), default=False) and _ip_is_remote(client_ip):
        return True
    return False

def _form_val(form, name):
    if form is None:
        return ""
    try:
        v = form.get(name, [""])
        v = v[0] if isinstance(v, (list, tuple)) else v
        return str(v or "")
    except Exception:
        return ""

def second_factor_ok(principal, form, totp_verify=None, passkey_verify=None):

    code = _form_val(form, "totp").strip()
    if code and totp_verify is not None:
        try:
            ok, reason = totp_verify(principal, code)
        except Exception as e:
            ok, reason = False, "totp-fehler:%s" % e
        if ok:
            return True, "totp"
    blob = _form_val(form, "passkey").strip()
    if blob and passkey_verify is not None:
        try:
            obj = json.loads(blob)
            cid = str(obj.get("cid") or "")
            resp = obj.get("response") or obj
            okp, who = passkey_verify(cid, resp)
            if okp and who == principal:
                return True, "passkey"
        except Exception:
            pass
    return False, "kein-faktor"

class Decision(object):
    __slots__ = ("ok", "online", "factor", "reason")

    def __init__(self, ok, online, factor, reason):
        self.ok = ok
        self.online = online
        self.factor = factor
        self.reason = reason

    def __repr__(self):
        return "Decision(ok=%r, online=%r, factor=%r, reason=%r)" % (
            self.ok, self.online, self.factor, self.reason)

def gate(principal, header_online=None, form=None, client_ip=None,
         totp_verify=None, passkey_verify=None):

    online = is_online(header_online, form, client_ip)
    if not online:
        return Decision(True, False, "lan", "on-lan")
    if not online_requires_2fa():
        return Decision(True, True, "disabled", "deckel-aus")
    ok, factor = second_factor_ok(principal, form, totp_verify, passkey_verify)
    if ok:
        return Decision(True, True, factor, "2fa-ok")
    return Decision(False, True, None, factor)
