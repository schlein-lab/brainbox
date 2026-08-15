#!/usr/bin/env python3

SENSITIVE = ("/api/admin", "/api/secret", "/api/vault", "/api/keys", "/api/key",
             "/api/pair", "/api/vpn", "/api/user", "/api/shutdown", "/api/kids")

def client_is_lan(ip):
    if ip in ("127.0.0.1", "::1") or ip.startswith(("192.168.", "10.", "169.254.")):
        return True
    if ip.startswith("172."):
        try:
            return 16 <= int(ip.split(".")[1]) <= 31
        except Exception:
            return False
    return False

def route_sensitive(path):
    return any(path == s or path.startswith(s) for s in SENSITIVE)

def authed(*, pin, lan_open, cred, ip, path, key_scoped=False):
    if not pin:
        return True
    if cred == "session":
        return True
    if cred == "key" and key_scoped:
        return True
    if lan_open and client_is_lan(ip) and not route_sensitive(path):
        return True
    return False

def principal(*, pin, lan_open, cred, key_uid=None):
    if cred == "session":
        return "owner"
    if cred == "key":
        return key_uid or "owner"
    if pin and lan_open:
        return "lan-guest"
    return "owner"

C = []
def case(name, got, exp):
    C.append((name, got == exp, got, exp))

LAN = "192.168.1.44"; WAN = "203.0.113.9"

case("PIN, lan_open OFF, no cred, LAN, /api/v1/jobs -> DENIED",
     authed(pin=True, lan_open=False, cred=None, ip=LAN, path="/api/v1/jobs"), False)
case("PIN, lan_open ON, no cred, LAN, /api/v1/jobs -> ALLOWED",
     authed(pin=True, lan_open=True, cred=None, ip=LAN, path="/api/v1/jobs"), True)
case("PIN, lan_open ON, no cred, LAN, /api/admin/users -> DENIED (sensitive)",
     authed(pin=True, lan_open=True, cred=None, ip=LAN, path="/api/admin/users"), False)
case("PIN, lan_open ON, no cred, LAN, /api/secret/x -> DENIED (sensitive)",
     authed(pin=True, lan_open=True, cred=None, ip=LAN, path="/api/secret/x"), False)
case("PIN, lan_open ON, no cred, LAN, /api/keys -> DENIED (key mgmt)",
     authed(pin=True, lan_open=True, cred=None, ip=LAN, path="/api/keys"), False)
case("PIN, lan_open ON, no cred, OFF-LAN(WAN), /api/v1/jobs -> DENIED",
     authed(pin=True, lan_open=True, cred=None, ip=WAN, path="/api/v1/jobs"), False)
case("PIN, lan_open ON, valid session, /api/admin -> ALLOWED",
     authed(pin=True, lan_open=True, cred="session", ip=LAN, path="/api/admin/users"), True)
case("PIN, lan_open ON, scoped key, /api/v1/jobs -> ALLOWED",
     authed(pin=True, lan_open=True, cred="key", ip=WAN, path="/api/v1/jobs", key_scoped=True), True)
case("PIN, lan_open ON, key NOT scoped, /api/v1/jobs from WAN -> DENIED",
     authed(pin=True, lan_open=True, cred="key", ip=WAN, path="/api/v1/jobs", key_scoped=False), False)
case("no PIN, no cred, /api/admin -> ALLOWED (legacy full-open unchanged)",
     authed(pin=False, lan_open=False, cred=None, ip=LAN, path="/api/admin/users"), True)
case("PIN, lan_open ON, no cred, LAN, /api/vpn/request -> DENIED",
     authed(pin=True, lan_open=True, cred=None, ip=LAN, path="/api/vpn/request"), False)
case("PIN, lan_open ON, no cred, LAN, loopback 127.0.0.1 non-sensitive -> ALLOWED",
     authed(pin=True, lan_open=True, cred=None, ip="127.0.0.1", path="/api/queue"), True)

case("open-LAN guest is 'lan-guest', not owner",
     principal(pin=True, lan_open=True, cred=None), "lan-guest")
case("no-PIN credential-less is owner (legacy)",
     principal(pin=False, lan_open=False, cred=None), "owner")
case("PIN, lan_open OFF, credential-less falls back owner (but authed() blocks it anyway)",
     principal(pin=True, lan_open=False, cred=None), "owner")
case("session is owner", principal(pin=True, lan_open=True, cred="session"), "owner")
case("key is its bound uid", principal(pin=True, lan_open=True, cred="key", key_uid="pi-node"), "pi-node")

ok = sum(1 for _n, good, _g, _e in C if good)
for n, good, g, e in C:
    print("  [%s] %s" % ("PASS" if good else "FAIL got=%r exp=%r" % (g, e), n))
print("\n%d/%d — %s" % (ok, len(C), "LAN_OPEN_AUTHZ_PROVEN" if ok == len(C) else "NEEDS_LOOK"))
