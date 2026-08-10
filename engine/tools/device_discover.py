#!/usr/bin/env python3

import socket, struct, time, sys, json, re, argparse, urllib.request, ssl
import os as _os2
for _p in (_os2.environ.get('PNLIB_HOME'), _os2.path.expanduser('~/portioneer')):
    if _p and _os2.path.isdir(_os2.path.join(_p,'pnlib')):
        if _p not in sys.path: sys.path.insert(0,_p)
        break
try:
    from pnlib import netprofile as _np
except Exception:
    _np = None

MCAST, MPORT = "224.0.0.251", 5353
QNAME = b"_googlecast._tcp.local"

def _enc(n):
    return b"".join(bytes([len(p)]) + p for p in n.split(b".")) + b"\x00"

def _dec(data, off):
    parts, jumped, seen, orig = [], False, set(), off
    while True:
        if off >= len(data) or off in seen:
            break
        seen.add(off)
        l = data[off]
        if l == 0:
            off += 1; break
        if l & 0xC0 == 0xC0:
            ptr = struct.unpack(">H", data[off:off+2])[0] & 0x3FFF
            if not jumped:
                orig = off + 2
            off = ptr; jumped = True; continue
        parts.append(data[off+1:off+1+l]); off += 1 + l
    return b".".join(parts), (orig if jumped else off)

def mdns_googlecast(dur=4.0):
    q = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + _enc(QNAME) + struct.pack(">HH", 12, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("", 5353))
    except OSError:
        s.bind(("", 0))
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                     socket.inet_aton(MCAST) + socket.inet_aton("0.0.0.0"))
    except OSError:
        pass
    s.settimeout(1.0)
    for _ in range(3):
        try:
            s.sendto(q, (MCAST, MPORT))
        except OSError:
            break
        time.sleep(0.2)
    devices, t0 = {}, time.time()
    while time.time() - t0 < dur:
        try:
            data, addr = s.recvfrom(9000)
        except socket.timeout:
            continue
        except OSError:
            break
        if b"_googlecast" not in data:
            continue
        try:
            flags = struct.unpack(">H", data[2:4])[0]
            if not (flags & 0x8000):
                continue

            qd, an, ns, ar = struct.unpack(">HHHH", data[4:12]); off = 12
            for _ in range(qd):
                _, off = _dec(data, off); off += 4
            for _ in range(an + ns + ar):
                nm, off = _dec(data, off)
                typ, cls, ttl, rdlen = struct.unpack(">HHIH", data[off:off+10]); off += 10
                rd = data[off:off+rdlen]; off += rdlen
                if typ not in (33, 16) or not nm.endswith(b"._googlecast._tcp.local"):
                    continue

                dev = devices.setdefault((addr[0], nm), {"ip": addr[0]})
                if typ == 33 and len(rd) >= 6:
                    dev["port"] = struct.unpack(">H", rd[4:6])[0]
                elif typ == 16:
                    i = 0
                    while i < len(rd):
                        l = rd[i]; kv = rd[i+1:i+1+l]; i += 1 + l
                        if b"=" in kv:
                            k, v = kv.split(b"=", 1)
                            if k in (b"fn", b"md", b"id"):

                                dev[k.decode()] = v.decode(errors="replace")
        except Exception:
            pass
    s.close()

    return [d for d in devices.values() if d.get("port") or d.get("fn") or d.get("id")]

SSDP_ADDR, SSDP_PORT = "239.255.255.250", 1900

def ssdp(dur=4.0):
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: %s:%d\r\nMAN: \"ssdp:discover\"\r\n"
           "MX: 2\r\nST: ssdp:all\r\n\r\n" % (SSDP_ADDR, SSDP_PORT)).encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.settimeout(1.0)
    try:
        for _ in range(2):
            s.sendto(msg, (SSDP_ADDR, SSDP_PORT)); time.sleep(0.2)
    except OSError:
        s.close(); return []
    seen, t0 = {}, time.time()
    while time.time() - t0 < dur:
        try:
            data, addr = s.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError:
            break
        txt = data.decode(errors="replace")
        loc = re.search(r"(?im)^LOCATION:\s*(\S+)", txt)
        st = re.search(r"(?im)^ST:\s*(\S+)", txt)
        usn = re.search(r"(?im)^USN:\s*(\S+)", txt)
        rec = seen.setdefault(addr[0], {"ip": addr[0]})
        if loc and "location" not in rec:
            rec["location"] = loc.group(1)
        if st:
            rec.setdefault("st", st.group(1))
        if usn:
            rec.setdefault("usn", usn.group(1))
    s.close()
    return list(seen.values())

def fetch_upnp(location, timeout=3):
    try:
        with urllib.request.urlopen(location, timeout=timeout) as r:
            xml = r.read(20000).decode(errors="replace")
    except Exception:
        return {}
    def g(tag):
        m = re.search(r"(?is)<%s>(.*?)</%s>" % (tag, tag), xml)
        return re.sub(r"<.*?>", "", m.group(1)).strip() if m else ""
    return {"friendlyName": g("friendlyName"), "manufacturer": g("manufacturer"),
            "modelName": g("modelName"), "deviceType": g("deviceType")}

def slug(x):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (x or "").lower())).strip("-")[:48] or "x"

def from_cast(d):
    md = d.get("md", "") or ""
    fn = d.get("fn") or md or d.get("ip")
    low = md.lower()
    if "hub" in low or "display" in low:
        kind = "nest-hub"
    elif any(w in low for w in ("chromecast", "shield", "androidtv", "google tv")):
        kind = "cast"
    else:
        kind = "cast"

    uuid = re.sub(r"[^0-9a-f]", "", (d.get("id") or "").lower())
    did = ("cast-" + uuid) if uuid else ("cast-" + slug(fn))

    name_src = "fn" if d.get("fn") else ("md" if md else "ip")
    return {"id": did, "name": fn, "name_src": name_src, "kind": kind, "driver": "cast",
            "state": "online", "transport": {"proto": "googlecast", "addr": d.get("ip"),
                                             "port": d.get("port", 8009), "model": md,
                                             "uuid": (uuid or None)}}

def from_upnp(d):
    info = fetch_upnp(d["location"]) if d.get("location") else {}
    fn = info.get("friendlyName") or d.get("ip")
    man = (info.get("manufacturer") or "").lower()
    dt = (info.get("deviceType") or "").lower()
    model = info.get("modelName") or ""
    if "sonos" in man or "sonos" in fn.lower():
        kind, driver = "speaker", "sonos"
    elif "samsung" in man or "samsung" in model.lower():
        kind, driver = "tv", "dlna"
    elif "mediarenderer" in dt:
        kind, driver = "renderer", "dlna"
    elif "mediaserver" in dt:
        kind, driver = "media-server", "http"
    else:
        kind, driver = "upnp", "http"
    return {"id": ("%s-%s" % (kind, slug(fn))), "name": fn,
            "name_src": ("fn" if info.get("friendlyName") else "ip"),
            "kind": kind, "driver": driver,
            "state": "online", "transport": {"proto": "upnp", "addr": d.get("ip"),
                                             "location": d.get("location"), "manufacturer": info.get("manufacturer"),
                                             "model": model, "deviceType": info.get("deviceType")}}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--post"); ap.add_argument("--token", default="")
    ap.add_argument("--dur", type=float, default=4.0)
    ap.add_argument("--angefordert", action="store_true",
                    help="jemand hat ausdruecklich um die Suche gebeten (Knopf / eingeschaltete "
                         "Dauersuche). Ohne das lehnt das netprofile-Gate ab: die Box sucht nicht "
                         "von selbst.")
    a = ap.parse_args()

    def _darf(cap, was):
        if _np is None:
            return bool(a.angefordert)
        try:
            return _np.require(cap, was, angefordert=bool(a.angefordert))
        except TypeError:
            return _np.require(cap, was) and bool(a.angefordert)

    do_mdns = _darf('mdns_browse', 'Geraetesuche')
    do_ssdp = _darf('ssdp', 'Geraetesuche')
    out, seen_ids = [], set()
    for d in (mdns_googlecast(a.dur) if do_mdns else []):
        r = from_cast(d)
        if r["id"] not in seen_ids:
            seen_ids.add(r["id"]); out.append(r)
    for d in (ssdp(a.dur) if do_ssdp else []):
        try:
            r = from_upnp(d)
        except Exception:
            continue
        if r["id"] not in seen_ids:
            seen_ids.add(r["id"]); out.append(r)
    summary = ", ".join("%s(%s)" % (r["name"], r["kind"]) for r in out) or "none"
    print("discovered %d device(s): %s" % (len(out), summary[:400]))

    print("DEVICES_JSON=" + json.dumps(out))
    if a.post:
        body = json.dumps({"token": a.token, "devices": out}).encode()
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        variants = ([a.post] if a.post.startswith("https://")
                    else ["https://" + a.post[7:], a.post] if a.post.startswith("http://")
                    else [a.post])
        last = None
        for u in variants:
            try:
                req = urllib.request.Request(u, data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
                    print("callback %s -> %s" % (u, r.read(200).decode(errors="replace")))
                    last = None
                    break
            except Exception as e:
                last = e
        if last is not None:
            print("callback failed:", last)

if __name__ == "__main__":
    main()
