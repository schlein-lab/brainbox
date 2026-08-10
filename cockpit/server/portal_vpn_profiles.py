
import base64
import json
import os
import re
import subprocess
import time
import urllib.parse

DATA_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "brainbox-portal")
HOME = os.path.expanduser("~")
DEFAULT_PRINCIPAL = "owner"
_prov_log = None

def _uid_safe(uid):

    return re.sub(r"[^A-Za-z0-9_.-]", "", str(uid or "owner"))[:64] or "owner"

def _netns_uid(principal):

    import zlib
    return 1000 + (zlib.crc32((principal or "owner").encode()) % 200)

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,23}$")
_TYPES = ("wireguard", "openvpn", "openconnect")
_OC_PROTOS = ("anyconnect", "gp", "pulse", "nc", "f5", "fortinet", "array")
_REQUIRE_TUN = {"wireguard": "wg0", "openvpn": "tun0", "openconnect": "tun0"}
_GATEWAY_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")
_CONF_MAX = 256 * 1024
_NETNS_ASKPASS = "/tmp/.pnvpn-portal-askpass.sh"
_ST_TTL = 8
_ST_CACHE = {}

_HELPER = os.path.join(os.path.dirname(os.path.realpath(__file__)), "pn_vpn_profiles.py")

_OVPN_VERBOTEN = frozenset((
    "up", "down", "up-restart", "route-up", "route-pre-down", "ipchange",
    "client-connect", "client-disconnect", "learn-address", "auth-user-pass-verify",
    "tls-verify", "plugin", "script-security", "management", "management-client",
    "cd", "chroot", "daemon", "log", "log-append", "status", "writepid",
    "askpass", "config",
))

_WG_VERBOTEN = frozenset(("preup", "postup", "predown", "postdown", "saveconfig"))

def _write0600(path, data):

    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)

def _vpnprof_root():
    return os.path.join(DATA_DIR, "vpn-profiles")

def _vpnprof_dir(usafe):
    d = os.path.join(_vpnprof_root(), usafe)
    os.makedirs(d, mode=0o700, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d

def _vpnprof_load(udir):
    try:
        with open(os.path.join(udir, "profiles.json")) as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("profiles"), dict):
            return d
    except Exception:
        pass
    return {"v": 1, "profiles": {}}

def _vpnprof_save(udir, doc):
    _write0600(os.path.join(udir, "profiles.json"),
               json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8"))

def _vpnprof_materialize(udir, prof):

    p = os.path.join(udir, prof["id"] + ".json")
    _write0600(p, (json.dumps(prof, ensure_ascii=False) + "\n").encode("utf-8"))
    return p

def _vpnprof_public(prof):

    return {"id": prof.get("id"), "name": prof.get("name"), "type": prof.get("type"),
            "shared": bool(prof.get("shared")), "gateway": prof.get("gateway") or "",
            "protocol": prof.get("protocol") or "", "user": prof.get("user") or "",
            "auth": {"mode": (prof.get("auth") or {}).get("mode") or "ask",
                     "otp": bool((prof.get("auth") or {}).get("otp"))},
            "require_tun": prof.get("require_tun") or "", "created": int(prof.get("created") or 0)}

def _vpnprof_sanitize_config(vtype, text):

    if vtype == "openvpn":
        for ln in text.splitlines():
            s = ln.strip()
            if not s or s[0] in ("#", ";", "<"):
                continue
            toks = s.split()
            d = toks[0].lstrip("-").lower()
            if d in _OVPN_VERBOTEN:
                return False, "OpenVPN-Direktive '%s' ist hier nicht erlaubt" % d
            if d == "auth-user-pass" and len(toks) > 1:

                return False, "OpenVPN-Direktive 'auth-user-pass' nur ohne Datei-Argument"
    elif vtype == "wireguard":
        if "[interface]" not in text.lower():
            return False, "WireGuard-Config ohne [Interface]-Abschnitt"
        for ln in text.splitlines():
            s = ln.strip()
            if not s or s[0] in ("#", ";", "["):
                continue
            key = s.split("=", 1)[0].strip().lower()
            if key in _WG_VERBOTEN:
                return False, "WireGuard-Schluessel '%s' ist hier nicht erlaubt" % s.split("=", 1)[0].strip()
    return True, None

def _vpnprof_slug(name):

    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    if s and not s[0].isalpha():
        s = "p" + s
    return s[:24].rstrip("-")

def _vpnprof_askpass():
    if not os.path.exists(_NETNS_ASKPASS):
        with open(_NETNS_ASKPASS, "w") as f:
            f.write("#!/bin/bash\n%s/.local/bin/phantom secret get sudo_pass\n" % HOME)
        os.chmod(_NETNS_ASKPASS, 0o755)

def _vpnprof_helper(cmd, nuid, session, prof_path, stdin_obj=None, timeout=90, force=False):

    if not os.path.isfile(_HELPER):
        return {"ok": False, "error": "VPN-Profil-Helfer ist auf dieser Box nicht installiert"}
    _vpnprof_askpass()
    env = dict(os.environ)
    env["SUDO_ASKPASS"] = _NETNS_ASKPASS

    args = ["sudo", "-A", "PN_PORTAL_DATA=%s" % DATA_DIR, "python3", _HELPER,
            cmd, "--uid", str(nuid), "--session", session, "--profile", prof_path]
    if force:
        args.append("--force")
    line = (json.dumps(stdin_obj) + "\n") if stdin_obj else ""
    try:
        pr = subprocess.run(args, input=line, capture_output=True, text=True,
                            timeout=timeout, env=env)
    except subprocess.TimeoutExpired:

        return {"ok": False, "error": "VPN-Profil-Helfer: Zeitueberschreitung (%ss)" % timeout}
    except Exception as e:
        return {"ok": False, "error": "VPN-Profil-Helfer fehlgeschlagen (%s)" % e.__class__.__name__}
    out = (pr.stdout or "").strip().splitlines()
    try:
        r = json.loads(out[-1]) if out else None
    except Exception:
        r = None
    if not isinstance(r, dict):

        return {"ok": False, "error": "VPN-Profil-Helfer: unlesbare Antwort (Exit %s)" % pr.returncode}
    return r

def _vpnprof_status(usafe, principal, udir, prof):

    key = (usafe, prof.get("id"))
    now = time.time()
    ent = _ST_CACHE.get(key)
    if ent and now - ent[0] < _ST_TTL:
        return ent[1]
    ppath = _vpnprof_materialize(udir, prof)
    r = _vpnprof_helper("status", _netns_uid(principal), "acct", ppath, None, timeout=15)
    if r.get("ok"):
        st = {"up": bool(r.get("up")), "connected": bool(r.get("connected")), "ns": r.get("ns") or ""}
    else:
        st = {"up": False, "connected": False, "error": r.get("error") or "Status nicht verfuegbar"}
    _ST_CACHE[key] = (now, st)
    return st

def _vpnprof_stdin(udir, prof, body):

    pay = {}
    pw = body.get("password")
    if not pw and (prof.get("auth") or {}).get("mode") == "saved":
        try:
            with open(os.path.join(udir, prof["id"] + ".auth")) as f:
                pw = (json.load(f) or {}).get("password")
        except Exception:
            pw = None
    if pw:
        pay["password"] = str(pw)
    if body.get("otp"):
        pay["otp"] = str(body.get("otp"))
    return pay

def _vpnprof_session(prof, body):

    if prof.get("shared"):
        return "acct"
    s = re.sub(r"[^A-Za-z0-9_.-]", "", str(body.get("session") or ""))[:32]
    return s or "acct"

def _vpnprof_prov(verb, principal, info):

    try:
        if callable(_prov_log):
            _prov_log(verb, principal, json.dumps(info, ensure_ascii=False), {"wire": "api"})
    except Exception:
        pass

class VpnProfileRoutes:

    _VPNPROF_GET = {"/api/vpn/profiles": "_api_vpn_profiles_get"}
    _VPNPROF_POST = {
        "/api/vpn/profiles": "_api_vpn_profiles_post",
        "/api/vpn/profiles/delete": "_api_vpn_profiles_delete",
        "/api/vpn/profiles/test": "_api_vpn_profiles_test",
        "/api/vpn/profiles/connect": "_api_vpn_profiles_connect",
        "/api/vpn/profiles/disconnect": "_api_vpn_profiles_disconnect",
    }

    def _vpnprof_json(self, obj, code=200):
        return self.send_html(json.dumps(obj, ensure_ascii=False), code,
                              [("Content-Type", "application/json")])

    def _vpnprof_gate(self):

        if self._apikey_entry() is not None:
            return False, "keine Maschinen-/Agent-Keys"
        ca = ((self.client_address[0] if self.client_address else "") or "").replace("::ffff:", "")
        if ca.startswith("127.") or ca == "::1":
            return False, "nur vom LAN-Client (nicht Loopback/Zelle)"
        return True, None

    def _vpnprof_dispatch(self, method, path, query="", raw=None):

        m = (method or "").upper()
        name = (self._VPNPROF_GET if m == "GET" else
                self._VPNPROF_POST if m == "POST" else {}).get(path)
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

    def _api_vpn_profiles_get(self, u):
        q = urllib.parse.parse_qs(u or "")
        want_all = (q.get("all", ["0"])[0] or "0").lower() in ("1", "true", "yes")
        if want_all and not self._is_admin():
            return self._vpnprof_json({"ok": False, "error": "?all=1 nur fuer Admin"}, 403)
        out = []
        if want_all:
            root = _vpnprof_root()
            try:
                users = sorted(d for d in os.listdir(root)
                               if os.path.isdir(os.path.join(root, d)))
            except OSError:
                users = []
            for usafe in users:
                udir = os.path.join(root, usafe)
                doc = _vpnprof_load(udir)
                principal = doc.get("principal") or usafe
                for pid in sorted(doc["profiles"]):
                    prof = doc["profiles"][pid]
                    pub = _vpnprof_public(prof)
                    pub["uid"] = usafe
                    pub["status"] = _vpnprof_status(usafe, principal, udir, prof)
                    out.append(pub)
        else:
            principal = self._principal()
            usafe = _uid_safe(principal)
            udir = _vpnprof_dir(usafe)
            doc = _vpnprof_load(udir)
            for pid in sorted(doc["profiles"]):
                prof = doc["profiles"][pid]
                pub = _vpnprof_public(prof)
                pub["status"] = _vpnprof_status(usafe, principal, udir, prof)
                out.append(pub)
        return self._vpnprof_json({"ok": True, "profiles": out})

    def _api_vpn_profiles_post(self, raw):
        ok, why = self._vpnprof_gate()
        if not ok:
            return self._vpnprof_json({"ok": False, "error": why}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        name = re.sub(r"[\x00-\x1f]", "", str(body.get("name") or "")).strip()[:64]
        vtype = str(body.get("type") or "").strip().lower()
        pid = str(body.get("id") or "").strip().lower() or _vpnprof_slug(name)
        if not _ID_RE.match(pid or ""):
            return self._vpnprof_json(
                {"ok": False, "error": "Profil-id ungueltig (Muster: ^[a-z][a-z0-9-]{1,23}$)"}, 400)
        if vtype not in _TYPES:
            return self._vpnprof_json(
                {"ok": False, "error": "type muss wireguard, openvpn oder openconnect sein"}, 400)
        if not name:
            name = pid
        principal = self._principal()
        usafe = _uid_safe(principal)
        udir = _vpnprof_dir(usafe)
        doc = _vpnprof_load(udir)
        existing = doc["profiles"].get(pid)
        if existing and existing.get("type") != vtype:
            return self._vpnprof_json(
                {"ok": False, "error": "Profil existiert mit anderem Typ — erst loeschen"}, 409)

        auth_mode = str(body.get("auth_mode") or
                        (existing or {}).get("auth", {}).get("mode") or "ask").strip().lower()
        if auth_mode not in ("ask", "saved"):
            return self._vpnprof_json({"ok": False, "error": "auth_mode muss ask oder saved sein"}, 400)
        otp = bool(body.get("otp", (existing or {}).get("auth", {}).get("otp", False)))
        shared = bool(body.get("shared", (existing or {}).get("shared", False)))
        gateway = str(body.get("gateway") or (existing or {}).get("gateway") or "").strip()
        protocol = str(body.get("protocol") or (existing or {}).get("protocol") or "").strip().lower()
        user = re.sub(r"[\x00-\x1f]", "", str(body.get("user") or (existing or {}).get("user") or "")).strip()[:64]
        if gateway and not _GATEWAY_RE.match(gateway):
            return self._vpnprof_json({"ok": False, "error": "Gateway enthaelt unzulaessige Zeichen"}, 400)
        if vtype == "openconnect":

            if not gateway:
                return self._vpnprof_json({"ok": False, "error": "openconnect braucht ein gateway"}, 400)
            if protocol not in _OC_PROTOS:
                return self._vpnprof_json(
                    {"ok": False, "error": "protocol muss eins von %s sein" % ", ".join(_OC_PROTOS)}, 400)

        conf_path = os.path.join(udir, pid + ".conf")
        cfg_b64 = body.get("config_b64")
        if cfg_b64:
            try:
                blob = base64.b64decode(str(cfg_b64), validate=True)
            except Exception:
                return self._vpnprof_json({"ok": False, "error": "config_b64 ist kein gueltiges Base64"}, 400)
            if len(blob) > _CONF_MAX:
                return self._vpnprof_json({"ok": False, "error": "Config zu gross (max 256 KiB)"}, 400)
            try:
                text = blob.decode("utf-8")
            except UnicodeDecodeError:
                return self._vpnprof_json({"ok": False, "error": "Config ist kein UTF-8-Text"}, 400)
            cok, cerr = _vpnprof_sanitize_config(vtype, text)
            if not cok:
                return self._vpnprof_json({"ok": False, "error": cerr}, 400)
            _write0600(conf_path, blob)
        elif vtype in ("wireguard", "openvpn") and not (existing and os.path.exists(conf_path)):

            return self._vpnprof_json(
                {"ok": False, "error": "%s braucht config_b64 beim Anlegen" % vtype}, 400)

        auth_path = os.path.join(udir, pid + ".auth")
        pw = body.get("password")
        if auth_mode == "saved":

            if pw:
                _write0600(auth_path, (json.dumps({"password": str(pw)}) + "\n").encode("utf-8"))
            elif vtype != "wireguard" and not os.path.exists(auth_path):
                return self._vpnprof_json(
                    {"ok": False, "error": "auth_mode 'saved' braucht ein password (einmalig)"}, 400)
        else:
            try:
                os.remove(auth_path)
            except OSError:
                pass

        prof = {"id": pid, "name": name, "type": vtype, "shared": shared,
                "config": pid + ".conf" if os.path.exists(conf_path) else "",
                "gateway": gateway, "protocol": protocol, "user": user,
                "auth": {"mode": auth_mode, "otp": otp},
                "require_tun": _REQUIRE_TUN.get(vtype, "tun0"),
                "created": int((existing or {}).get("created") or time.time())}
        doc["principal"] = principal
        doc["profiles"][pid] = prof
        _vpnprof_save(udir, doc)
        _vpnprof_materialize(udir, prof)
        _ST_CACHE.pop((usafe, pid), None)
        _vpnprof_prov("vpnprofile.save", principal,
                      {"id": pid, "type": vtype, "shared": shared, "auth_mode": auth_mode,
                       "update": bool(existing)})
        return self._vpnprof_json({"ok": True, "profile": _vpnprof_public(prof)})

    def _api_vpn_profiles_delete(self, raw):
        ok, why = self._vpnprof_gate()
        if not ok:
            return self._vpnprof_json({"ok": False, "error": why}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        pid = str(body.get("id") or "").strip().lower()
        principal = self._principal()
        usafe = _uid_safe(principal)
        udir = _vpnprof_dir(usafe)
        doc = _vpnprof_load(udir)
        prof = doc["profiles"].get(pid)
        if not prof:
            return self._vpnprof_json({"ok": False, "error": "Profil unbekannt"}, 404)

        try:
            ppath = _vpnprof_materialize(udir, prof)
            _vpnprof_helper("down", _netns_uid(principal), "acct", ppath, None,
                            timeout=30, force=True)
        except Exception:
            pass
        for suffix in (".conf", ".auth", ".json"):
            try:
                os.remove(os.path.join(udir, pid + suffix))
            except OSError:
                pass
        del doc["profiles"][pid]
        _vpnprof_save(udir, doc)
        _ST_CACHE.pop((usafe, pid), None)
        _vpnprof_prov("vpnprofile.delete", principal, {"id": pid})
        return self._vpnprof_json({"ok": True})

    def _vpnprof_action(self, raw, cmd, verb, with_stdin):
        ok, why = self._vpnprof_gate()
        if not ok:
            return self._vpnprof_json({"ok": False, "error": why}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        pid = str(body.get("id") or "").strip().lower()
        principal = self._principal()
        usafe = _uid_safe(principal)
        udir = _vpnprof_dir(usafe)
        doc = _vpnprof_load(udir)
        prof = doc["profiles"].get(pid)
        if not prof:
            return self._vpnprof_json({"ok": False, "error": "Profil unbekannt"}, 404)
        ppath = _vpnprof_materialize(udir, prof)
        sess = _vpnprof_session(prof, body)
        stdin_obj = _vpnprof_stdin(udir, prof, body) if with_stdin else None
        r = _vpnprof_helper(cmd, _netns_uid(principal), sess, ppath, stdin_obj, timeout=90)
        _ST_CACHE.pop((usafe, pid), None)
        _vpnprof_prov(verb, principal, {"id": pid, "session": sess, "ok": bool(r.get("ok"))})

        return self._vpnprof_json(r, 200 if r.get("ok") else 500)

    def _api_vpn_profiles_test(self, raw):

        return self._vpnprof_action(raw, "test", "vpnprofile.test", with_stdin=True)

    def _api_vpn_profiles_connect(self, raw):

        return self._vpnprof_action(raw, "connect", "vpnprofile.connect", with_stdin=True)

    def _api_vpn_profiles_disconnect(self, raw):

        return self._vpnprof_action(raw, "down", "vpnprofile.disconnect", with_stdin=False)
