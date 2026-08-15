
import os
import sys
import json
import time

try:
    from pnlib import site as _site
except Exception:
    _site = None

_LEDGER = "/run/brainbox/netprofile.jsonl"

_CAPS = {
    "mdns_respond":  ("Auf den EIGENEN Namen antworten (<name>.local)",       "keins",     "immer", True),
    "mdns_browse":   ("Fremde Geraete per mDNS SUCHEN (224.0.0.251:5353)",    "hoch",      "gated", False),
    "ssdp":          ("SSDP/UPnP M-SEARCH senden (239.255.255.250:1900)",     "hoch",      "gated", False),
    "ssdp_announce": ("SSDP-Announce (eigenen DLNA-Server sichtbar machen)",  "hoch",      "gated", True),
    "mdns_announce": ("Eigene Dienste ankuendigen (Dateifreigabe im Explorer)", "mittel",   "gated", True),
    "lan_scan":      ("Aktiver LAN-Scan (Ping-/ARP-/Port-Sweep)",             "hoch",      "gated", False),
    "vip_claim":     ("Virtuelle IP selbst beanspruchen + Gratuitous-ARP",    "sehr hoch", "gated", True),
    "dlna_push":     ("DLNA/UPnP-Push an einen Renderer (unicast)",           "mittel",    "gated", True),
    "cast_discover": ("Google-Cast-Discovery (Multicast)",                    "hoch",      "gated", False),
    "own_dns":       ("Eigener DNS-Responder im LAN",                         "sehr hoch", "guarantee", False),
    "own_dhcp":      ("Eigener DHCP-Server im LAN",                           "sehr hoch", "guarantee", False),
}

_ALIAS = {"mdns": "mdns_browse"}

_ENFORCED = {"mdns_browse", "ssdp", "vip_claim", "lan_scan"}

def _cfg(key, default=None):
    if _site is not None:
        return _site.get(key, default)
    v = os.environ.get(key)
    if v is not None:
        return v
    try:
        for line in open("/etc/brainbox/site.conf"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, val = line.partition("=")
                if k.strip() == key:
                    return val.strip().strip('"').strip("'")
    except OSError:
        pass
    return default

def _truthy(v):
    return str(v).lower() in ("1", "true", "yes", "on") if v is not None else False

def _norm(cap):
    return _ALIAS.get(cap, cap)

def profile():
    return (_cfg("NET_PROFILE", "home") or "home").strip().lower()

def opt_in_flag(cap):
    return "ALLOW_" + _norm(cap).upper()

def allowed(cap):

    cap = _norm(cap)
    mode = _CAPS.get(cap, (None, None, "gated", False))[2]
    if mode == "immer":
        return True
    if mode == "guarantee":
        return False
    if profile() != "managed":
        return True
    return _truthy(_cfg(opt_in_flag(cap)))

def unaufgefordert_erlaubt(cap):

    cap = _norm(cap)
    return bool(_CAPS.get(cap, (None, None, "gated", False))[3])

def _record(cap, action, what, angefordert):
    rec = {"ts": int(time.time()), "cap": cap, "action": action, "angefordert": bool(angefordert),
           "profile": profile(), "what": (what or "")[:120]}
    try:
        with open(_LEDGER, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return rec

def require(cap, what="", angefordert=False):

    cap = _norm(cap)
    ok = allowed(cap)
    grund = "" if ok else "Profil '%s'" % profile()
    if ok and not angefordert and not unaufgefordert_erlaubt(cap):
        ok = False
        grund = "ungefragt (nur auf ausdrueckliche Bitte)"
    _record(cap, "emit" if ok else "block", what, angefordert)
    if not ok:
        hinweis = ("Freischalten via %s=1 in /etc/brainbox/site.conf." % opt_in_flag(cap)
                   if grund.startswith("Profil")
                   else "Der Aufrufer muss angefordert=True setzen und dann sagen, WER gefragt hat.")
        sys.stderr.write(
            "[netprofile] BLOCKED %s (%s) — %s, guter Gast: nicht gesendet. %s\n"
            % (cap, what or "-", grund, hinweis))
    return ok

def capabilities():

    out = []
    for cap, (title, risk, mode, unauf) in _CAPS.items():
        a = allowed(cap)
        if mode == "immer":
            why = "immer: so wird die Box ueberhaupt gefunden — sie ANTWORTET nur"
        elif mode == "guarantee":
            why = "Garantie: die Box tut das NIE"
        elif not a:
            why = "geblockt (deny-by-default im managed-Profil)"
        elif profile() != "managed":
            why = "erlaubt (Profil home)"
        else:
            why = "freigeschaltet via %s=1" % opt_in_flag(cap)
        if a and not unauf:
            why += " — aber nur auf ausdrueckliche Bitte, nie von selbst"
        out.append({
            "cap": cap, "title": title, "risk": risk, "mode": mode,
            "opt_in": (opt_in_flag(cap) if mode == "gated" else None),
            "allowed": a, "unaufgefordert": unauf, "enforced": cap in _ENFORCED,
            "why": why,
        })
    return out

def ledger_tail(n=50):
    try:
        lines = open(_LEDGER).read().splitlines()[-n:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out

if __name__ == "__main__":
    print("profile:", profile())
    for c in capabilities():
        print("  %-14s allow=%-5s ungefragt=%-5s enforced=%-5s  %s"
              % (c["cap"], c["allowed"], c["unaufgefordert"], c["enforced"], c["title"]))
