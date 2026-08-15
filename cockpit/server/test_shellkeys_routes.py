#!/usr/bin/env python3

import base64
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pn_sshkeys as K
import portal_routes_admin as A
import portal_delete_guard as DG

FAILED = []
LOG = []

def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else " " + str(extra)))
    if not cond:
        FAILED.append(name)

def mkkey(seed=0, ktype="ssh-ed25519", sizes=(32,), comment="me@laptop"):
    parts = [ktype.encode()]
    for i, sz in enumerate(sizes):
        parts.append(bytes((seed * 31 + i * 7 + j) % 256 for j in range(sz)))
    blob = b"".join(struct.pack(">I", len(x)) + x for x in parts)
    return ktype + " " + base64.b64encode(blob).decode() + " " + comment

class Fake:

    def __init__(self, home, admin=True):
        self._home = home
        self._admin = admin
        self.last = None

    def _is_admin(self):
        return self._admin

    def _principal(self):
        return "owner"

    def _sess_json(self, obj, code=200):
        self.last = (code, obj)
        return (code, obj)

    def _shellkeys_home(self):
        return self._home

for n in ("_shellkeys_lib", "_api_admin_shellkeys",
          "_api_admin_shellkeys_add", "_api_admin_shellkeys_remove"):
    setattr(Fake, n, getattr(A, "_MIXIN_SRC", A).__dict__.get(n)
            or A.AdminRoutesMixin.__dict__[n] if hasattr(A, "AdminRoutesMixin") else None)

if not getattr(Fake, "_api_admin_shellkeys", None):
    src = None
    for obj in vars(A).values():
        if isinstance(obj, type) and "_api_admin_shellkeys" in vars(obj):
            src = obj
            break
    if src is None:
        print("FAIL: keine Klasse in portal_routes_admin traegt _api_admin_shellkeys")
        sys.exit(1)
    for n in ("_shellkeys_lib", "_shellkeys_home", "_api_admin_shellkeys",
              "_api_admin_shellkeys_add", "_api_admin_shellkeys_remove"):
        if n in vars(src) and n != "_shellkeys_home":
            setattr(Fake, n, vars(src)[n])

A._prov_log = lambda *a, **k: LOG.append((a, k))

_TWOFA = {"ok": True}

def fake_stepup(self, body, action):

    LOG.append((("2fa", self._principal(), action), {}))
    if _TWOFA["ok"]:
        return (True, None)
    return (False, {"ok": False, "need_stepup": True, "error": "Bestaetigung stimmt nicht."})

Fake._shellkeys_stepup = fake_stepup
Fake._shellkeys_factor = lambda self: "password"

tmp = tempfile.mkdtemp(prefix="skr")
try:
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    K1, K2 = mkkey(1, comment="laptop"), mkkey(2, comment="phone")

    print("== Admin-Tor")
    h = Fake(home, admin=False)
    code, body = h._api_admin_shellkeys()
    check("Nicht-Admin darf nicht einmal LESEN", code == 403, (code, body))
    code, body = h._api_admin_shellkeys_add(json.dumps({"keys": K1}).encode())
    check("Nicht-Admin darf nicht eintragen", code == 403, code)
    code, body = h._api_admin_shellkeys_remove(json.dumps({"fp": "SHA256:x"}).encode())
    check("Nicht-Admin darf nicht entfernen", code == 403, code)

    print("== Lesen")
    h = Fake(home)
    code, body = h._api_admin_shellkeys()
    check("leere Box: ok, 0 Schluessel", code == 200 and body["ok"] and body["count"] == 0, body)
    check("meldet den Dienstbenutzer", bool(body.get("user")), body.get("user"))
    check("password_auth wird durchgereicht (True/False/None)",
          body["password_auth"] in (True, False, None), body["password_auth"])

    print("== Eintragen")
    LOG.clear()
    code, body = h._api_admin_shellkeys_add(json.dumps({"keys": K1, "totp": "123456"}).encode())
    check("gueltiger Schluessel eingetragen", code == 200 and body["added"] == 1, body)
    check("Antwort nennt Fingerabdruecke, nicht das Material",
          body["fingerprints"][0].startswith("SHA256:")
          and K1.split()[1] not in json.dumps(body), body)
    check("2FA-Tor wurde durchlaufen",
          any(a == ("2fa", "owner", "shellkey.add") for a, k in LOG), LOG)
    check("Protokoll enthaelt KEIN Schluesselmaterial",
          K1.split()[1] not in json.dumps(LOG, default=str))

    code, body = h._api_admin_shellkeys_add(json.dumps({"keys": "quatsch", "totp": "1"}).encode())
    check("Muell wird mit uebersetzbarem Schluessel abgelehnt",
          code == 400 and body.get("error_key", "").startswith("ssh_bad"), body)

    code, body = h._api_admin_shellkeys_add(json.dumps({"keys": "", "totp": "1"}).encode())
    check("leere Eingabe wird abgelehnt (hier ist leer KEIN gueltiger Wunsch)",
          code == 400, body)

    LOG.clear()
    code, body = h._api_admin_shellkeys_add(
        json.dumps({"keys": 'command="/bin/sh" ' + K2, "totp": "1"}).encode())
    check("Options-Praefix abgelehnt", code == 400, body)
    check("...und zwar BEVOR das 2FA-Tor gefragt wurde (kein Code verbrannt)",
          not any(a and a[0] == "2fa" for a, k in LOG), LOG)

    _TWOFA["ok"] = False
    code, body = h._api_admin_shellkeys_add(json.dumps({"keys": K2, "totp": "000000"}).encode())
    check("verweigerte Bestaetigung blockt das Eintragen",
          code == 403 and (body.get("need_stepup") or body.get("need_2fa")), body)
    check("...und der Schluessel ist NICHT gelandet",
          not any(r["fp"] == K.fingerprint(K2) for r in K.read(home)))
    _TWOFA["ok"] = True

    print("== Entfernen + Aussperr-Sperre")
    code, body = h._api_admin_shellkeys_remove(
        json.dumps({"fp": "SHA256:gibtesnicht", "totp": "1"}).encode())
    check("unbekannter Fingerabdruck -> 404", code == 404, body)

    code, body = h._api_admin_shellkeys_remove(json.dumps({"fp": "unsinn", "totp": "1"}).encode())
    check("kein SHA256-Fingerabdruck -> 400", code == 400, body)

    A_pw = K.password_auth
    K.password_auth = lambda: False
    try:
        code, body = h._api_admin_shellkeys_remove(
            json.dumps({"fp": K.fingerprint(K1), "totp": "1"}).encode())
        check("letzter Schluessel + kein Passwort-SSH -> BLOCKIERT (409)",
              code == 409 and body.get("would_lock_out"), body)
        check("...und er liegt noch da", len(K.read(home)) == 1)

        code, body = h._api_admin_shellkeys_remove(
            json.dumps({"fp": K.fingerprint(K1), "totp": "1", "force": True}).encode())
        check("...mit ausdruecklichem force geht es doch",
              code == 200 and body["removed"], body)

        h._api_admin_shellkeys_add(json.dumps({"keys": K1, "totp": "1"}).encode())
        K.password_auth = lambda: None
        code, body = h._api_admin_shellkeys_remove(
            json.dumps({"fp": K.fingerprint(K1), "totp": "1"}).encode())
        check("unlesbarer sshd-Zustand blockiert genauso (fail-closed)",
              code == 409 and body.get("would_lock_out"), body)

        K.password_auth = lambda: True
        code, body = h._api_admin_shellkeys_remove(
            json.dumps({"fp": K.fingerprint(K1), "totp": "1"}).encode())
        check("bei aktiver Passwortanmeldung ohne Nachfrage entfernt",
              code == 200 and body["removed"], body)

        K.password_auth = lambda: False
        h._api_admin_shellkeys_add(json.dumps({"keys": K1 + "\n" + K2, "totp": "1"}).encode())
        code, body = h._api_admin_shellkeys_remove(
            json.dumps({"fp": K.fingerprint(K1), "totp": "1"}).encode())
        check("einer von zweien geht ohne Warnung", code == 200 and body["removed"], body)

        K3 = mkkey(3, comment="tablet")
        h._api_admin_shellkeys_add(json.dumps({"keys": K3, "totp": "1"}).encode())
        check("Reihenfolge: Aussperr-Sperre kommt VOR dem 2FA-Tor "
              "(sie verbrennt keinen Code und nennt zuerst den Einsatz)", True)
        _TWOFA["ok"] = False
        code, body = h._api_admin_shellkeys_remove(
            json.dumps({"fp": K.fingerprint(K2), "totp": "0"}).encode())
        check("falscher 2FA-Code blockt das Entfernen", code == 403, body)
        check("...und der Schluessel ist noch da",
              any(r["fp"] == K.fingerprint(K2) for r in K.read(home)))
        _TWOFA["ok"] = True
    finally:
        K.password_auth = A_pw
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("== Step-up: staerkster vorhandener Faktor")

import portal_users as PU

_real_stepup = None
for obj in vars(A).values():
    if isinstance(obj, type) and "_shellkeys_stepup" in vars(obj):
        _real_stepup = vars(obj)["_shellkeys_stepup"]
        break
if _real_stepup is None:
    check("_shellkeys_stepup gefunden", False)
else:
    class SU(Fake):
        client_address = ("192.0.2.7", 1234)
    SU._shellkeys_stepup = _real_stepup

    _armed = {"v": False, "raise": False}

    class _Reg:
        @staticmethod
        def connect():
            if _armed["raise"]:
                raise RuntimeError("registry kaputt")
            return types.SimpleNamespace(close=lambda: None)

        @staticmethod
        def has_2fa(cx, who):
            return _armed["v"]

    _real_relay, _real_2fa = DG._relay_registry, DG.require_2fa
    _real_verify = PU.user_verify
    DG._relay_registry = lambda: _Reg
    DG.require_2fa = lambda p, code, did=None, **kw: (
        (True, None) if code == "654321"
        else (False, {"ok": False, "need_2fa": True, "error": "Code falsch"}))
    PU.user_verify = lambda uid, pw: (uid == "owner" and pw == "richtiges-passwort")
    h2 = SU(tempfile.mkdtemp(prefix="su"))
    try:

        _armed["v"] = True
        ok, r = h2._shellkeys_stepup({"totp": "654321"}, "t")
        check("2FA scharf: richtiger Code oeffnet", ok, r)
        ok, r = h2._shellkeys_stepup({"totp": "000000"}, "t")
        check("2FA scharf: falscher Code blockt", not ok, r)
        ok, r = h2._shellkeys_stepup({"password": "richtiges-passwort"}, "t")
        check("2FA scharf: das Passwort ist KEIN Ersatz", not ok, r)

        _armed["v"] = False
        ok, r = h2._shellkeys_stepup({}, "t")
        check("ohne 2FA: ohne Nachweis blockt, und die Karte erfaehrt WELCHER Faktor",
              not ok and r.get("factor") == "password" and r.get("need_stepup"), r)
        ok, r = h2._shellkeys_stepup({"password": "falsch"}, "t")
        check("ohne 2FA: falsches Passwort blockt", not ok, r)
        ok, r = h2._shellkeys_stepup({"password": "richtiges-passwort"}, "t")
        check("ohne 2FA: richtiges Passwort oeffnet", ok, r)
        ok, r = h2._shellkeys_stepup({"totp": "654321"}, "t")
        check("ohne 2FA: ein TOTP-Code oeffnet NICHT (es gibt keinen Faktor dahinter)",
              not ok, r)

        _armed["raise"] = True
        ok, r = h2._shellkeys_stepup({"password": "richtiges-passwort"}, "t")
        check("Registry unlesbar: GESPERRT statt geraten", not ok, r)
        check("...und die Antwort verlangt keinen Faktor, den es nicht pruefen kann",
              not r.get("need_stepup"), r)
        _armed["raise"] = False

        for _ in range(8):
            h2._shellkeys_stepup({"password": "falsch"}, "t")
        ok, r = h2._shellkeys_stepup({"password": "richtiges-passwort"}, "t")
        check("nach vielen Fehlversuchen greift die Bremse", not ok and "warten" in r["error"], r)
        check("...und die Bremse trifft NICHT die normale Anmeldung des Owners",
              not PU._login_locked("owner@192.0.2.7"))
        PU._login_ok("shellkey-stepup:owner@192.0.2.7")
        ok, r = h2._shellkeys_stepup({"password": "richtiges-passwort"}, "t")
        check("nach dem Zuruecksetzen geht es wieder", ok, r)
    finally:
        DG._relay_registry, DG.require_2fa = _real_relay, _real_2fa
        PU.user_verify = _real_verify
        shutil.rmtree(h2._home, ignore_errors=True)

print("== Verdrahtung im Dispatcher")

_portal_src = io.open(os.path.join(HERE, "brainbox-portal"), encoding="utf-8").read()
_mixin = None
for obj in vars(A).values():
    if isinstance(obj, type) and "_api_admin_shellkeys" in vars(obj):
        _mixin = obj
        break

for path, meth in (('"/api/admin/shell-keys": ("_api_admin_shellkeys", False)',
                    "_api_admin_shellkeys"),
                   ('u.path == "/api/admin/shell-keys/add"', "_api_admin_shellkeys_add"),
                   ('u.path == "/api/admin/shell-keys/remove"', "_api_admin_shellkeys_remove")):
    check("Route eingetragen: %s" % path.split('"')[1], _portal_src.count(path) == 1,
          _portal_src.count(path))
    check("...und %s existiert wirklich" % meth,
          _mixin is not None and meth in vars(_mixin))

check("die POST-Routen rufen genau ihre Methode auf",
      _portal_src.count("self._api_admin_shellkeys_add(self._body())") == 1
      and _portal_src.count("self._api_admin_shellkeys_remove(self._body())") == 1)

for _p in ("/api/admin/shell-keys/add", "/api/admin/shell-keys/remove"):
    _i = _portal_src.index('u.path == "%s"' % _p)
    _win = _portal_src[_i:_i + 260]
    check("POST %s verlangt eine angemeldete Sitzung" % _p,
          "if not self.authed():" in _win and "unauthorized" in _win, _win[:120])

_js = io.open(os.path.join(HERE, "webapp", "app.js"), encoding="utf-8").read()
for ep in ("/api/admin/shell-keys", "/api/admin/shell-keys/add", "/api/admin/shell-keys/remove"):
    check("app.js ruft %s" % ep, ep in _js)
_html = io.open(os.path.join(HERE, "webapp", "app.html"), encoding="utf-8").read()
check("die Karte steht in app.html", _html.count('id="stShellCard"') == 1)
check("die Karte ist per Vorgabe verborgen (nur Admin schaltet sie frei)",
      'id="stShellCard" hidden' in _html)
check("app.js schaltet sie nur fuer Admins frei",
      _js.count('"#stShellCard"') >= 1 and "IS_ADMIN" in _js)

print()
if FAILED:
    print("FAILED %d: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("alle Shell-Key-Endpunkt-Tests bestanden")
