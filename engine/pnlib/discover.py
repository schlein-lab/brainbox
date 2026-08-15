
from __future__ import annotations

import ipaddress
import json
import os
import sqlite3
import time

from . import transport as T

_OUI = {
    "00:11:22": "Brother",
    "00:1B:A9": "Brother Industries",
    "00:00:48": "Seiko Epson",
    "AC:CF:23": "Hi-Link / generic IoT",
    "B8:27:EB": "Raspberry Pi Foundation",
    "DC:A6:32": "Raspberry Pi Trading",
    "00:80:77": "Brother (printer)",
    "00:24:21": "Western Digital (NAS)",
    "00:90:A9": "Western Digital",
    "00:0C:29": "VMware (virtual)",
    "08:00:27": "VirtualBox",
    "00:1A:2B": "Ayecom / legacy",
    "00:50:F2": "Microsoft",
}

def oui_vendor(mac: str | None) -> str | None:
    if not mac:
        return None
    prefix = mac.upper().replace("-", ":")[:8]
    return _OUI.get(prefix)

class DiscoveryError(Exception):
    pass

class OutOfScope(DiscoveryError):
    pass

def assert_own_cidr(ip: str, own_cidrs: list[str]):

    addr = ipaddress.ip_address(ip)
    for c in own_cidrs:
        if addr in ipaddress.ip_network(c, strict=False):
            return
    raise OutOfScope(f"{ip} is not within own CIDR(s) {own_cidrs} — refusing (own-LAN only)")

class RateLimiter:

    def __init__(self, rate_per_sec: float = 5.0, clock=time.monotonic):
        self.min_interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self._clock = clock
        self._last = None
        self.probes = 0

    def acquire(self, sleep=time.sleep):
        self.probes += 1
        if self.min_interval <= 0:
            return
        now = self._clock()
        if self._last is not None:
            wait = self.min_interval - (now - self._last)
            if wait > 0:
                sleep(wait)
        self._last = self._clock()

_CLASS_HINTS = {
    "ipp": "printer", "smb": "nas", "nfs": "nas", "rtsp": "camera",
    "mqtt": "iot", "zigbee": "iot", "rf433": "sensor", "telnet": "legacy",
}

_RISK_RULES = [
    ("smb1", "SMB1 is wormable — isolate on a dedicated VLAN, appliance-only reachable"),
    ("telnet", "Telnet is cleartext + often default-credentialed — high risk legacy control"),
    ("ftp", "FTP is cleartext — credentials/data exposed on the wire"),
    ("tftp", "TFTP is unauthenticated — firmware-transfer surface; gate hard"),
]

def classify(candidate: dict, registry: T.TransportRegistry | None = None) -> dict:

    reg = registry or T.default_registry()
    services = candidate.get("services") or []
    schemes = sorted({s["scheme"] for s in services if s.get("scheme")})
    vendor = oui_vendor(candidate.get("mac"))

    dev_class = "unknown"
    for s in services:
        hint = _CLASS_HINTS.get(s.get("scheme"))
        if hint:
            dev_class = hint
            break
    blob = " ".join(filter(None, [
        candidate.get("hostname", ""),
        " ".join(s.get("sysDescr", "") for s in services),
        " ".join(s.get("banner", "") for s in services),
        json.dumps([s.get("model", "") for s in services]),
    ])).lower()
    if "printer" in blob or "laserjet" in blob or "brother" in blob:
        dev_class = "printer"
    elif "camera" in blob or "ipcam" in blob or "onvif" in blob:
        dev_class = "camera"
    elif "nas" in blob or "synology" in blob or "samba" in blob:
        dev_class = "nas"
    elif "router" in blob or "openwrt" in blob or "dd-wrt" in blob:
        dev_class = "router"

    capabilities = T.compose_capabilities(schemes, reg)

    risks = []
    facts = set(schemes)
    for s in services:
        if s.get("smb1"):
            facts.add("smb1")
    for fact, note in _RISK_RULES:
        if fact in facts:
            risks.append(note)
    if dev_class == "legacy" or any("legacy" in (s.get("sysDescr", "").lower()) for s in services):
        risks.append("legacy/abandonware device — likely unpatched; maintenance/revival is gated")

    return {
        "ip": candidate.get("ip"),
        "mac": candidate.get("mac"),
        "hostname": candidate.get("hostname"),
        "vendor": vendor,
        "device_class": dev_class,
        "transports": schemes,
        "services": services,
        "capabilities": capabilities,
        "risks": risks,
        "observed_at": time.time(),
    }

def fingerprint_key(profile: dict) -> str:

    return profile.get("mac") or f"{profile.get('ip')}::{profile.get('device_class')}"

def gentle_active_probe(target: dict, own_cidrs: list[str], registry: T.TransportRegistry,
                        limiter: RateLimiter, timeout: float = 2.0,
                        sleep=time.sleep) -> dict:

    ip = target["ip"]
    assert_own_cidr(ip, own_cidrs)
    services = []
    for p in target.get("ports", []):
        scheme, port = p.get("scheme"), p.get("port")
        limiter.acquire(sleep=sleep)
        pr = registry.probe(scheme, ip, port, timeout=timeout)
        svc = {"scheme": scheme, "port": port}
        if pr.ok:

            if "sysDescr" in pr.data:
                svc["sysDescr"] = pr.data["sysDescr"]
            if "printer" in pr.data:
                svc["model"] = pr.data["printer"].get("printer-make-and-model", "")
            if "shares" in pr.data:
                svc["shares"] = pr.data["shares"]
        else:
            svc["probe_error"] = pr.error
            svc["stub"] = pr.stub

        if p.get("smb1") or (scheme == "smb" and target.get("smb1")):
            svc["smb1"] = True
        services.append(svc)
    return {"ip": ip, "mac": target.get("mac"), "hostname": target.get("hostname"),
            "services": services}

def discover(targets: list[dict], own_cidrs: list[str], registry: T.TransportRegistry | None = None,
             rate_per_sec: float = 5.0, timeout: float = 2.0, sleep=time.sleep,
             clock=time.monotonic) -> list[dict]:

    reg = registry or T.default_registry()
    limiter = RateLimiter(rate_per_sec, clock=clock)
    out = []
    for t in targets:
        raw = gentle_active_probe(t, own_cidrs, reg, limiter, timeout=timeout, sleep=sleep)
        out.append(classify(raw, reg))
    return out

def propose_payload(profile: dict) -> dict:

    caps = sorted(profile.get("capabilities", {}).keys())
    return {
        "gate": "pre",
        "kind": "device-onboard-proposal",
        "summary": (f"discovered {profile.get('device_class', 'device')} "
                    f"{profile.get('vendor') or ''} at {profile.get('ip')}").strip(),
        "device": {
            "ip": profile.get("ip"), "mac": profile.get("mac"),
            "hostname": profile.get("hostname"), "vendor": profile.get("vendor"),
            "device_class": profile.get("device_class"),
            "transports": profile.get("transports"),
        },
        "composable_capabilities": caps,
        "capability_detail": profile.get("capabilities"),
        "risks": profile.get("risks"),

        "action": {"task_type": "device.onboard",
                   "note": "observe-only: onboarding requires human approval (single-use nonce "
                           "minted by pnd — the brain cannot mint it). Discovery NEVER auto-binds."},
        "auto_bind": False,
        "fingerprint": fingerprint_key(profile),
    }

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
  fingerprint TEXT PRIMARY KEY,
  ip          TEXT,
  mac         TEXT,
  hostname    TEXT,
  device_class TEXT,
  profile     TEXT NOT NULL,          -- the full classified profile (json)
  status      TEXT NOT NULL DEFAULT 'proposed',  -- proposed | onboarded | ignored
  first_seen  REAL,
  last_seen   REAL
);
CREATE TABLE IF NOT EXISTS device_profiles (
  fingerprint TEXT PRIMARY KEY,
  ip          TEXT,
  mac         TEXT,
  device_class TEXT,
  principal   TEXT,                    -- the channel-principal bound at onboard (identity.py)
  profile     TEXT NOT NULL,
  onboarded_at REAL
);
"""

def store_path(base_dir: str | None = None) -> str:

    base = base_dir or os.environ.get("PN_DEVICE_DIR") or os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "portioneer", "device")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "devices.db")

class DiscoveryStore:

    def __init__(self, path: str | None = None):
        self.path = path or store_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.cx = sqlite3.connect(self.path, timeout=10)
        self.cx.row_factory = sqlite3.Row
        self.cx.executescript(_SCHEMA)
        self.cx.commit()

    def close(self):
        self.cx.close()

    def upsert_candidate(self, profile: dict) -> dict:

        fp = fingerprint_key(profile)
        now = time.time()
        prev = self.cx.execute(
            "SELECT profile, first_seen FROM candidates WHERE fingerprint=?", (fp,)).fetchone()
        body = json.dumps(profile, sort_keys=True, default=str)
        if prev is None:
            self.cx.execute(
                "INSERT INTO candidates(fingerprint,ip,mac,hostname,device_class,profile,status,"
                "first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?)",
                (fp, profile.get("ip"), profile.get("mac"), profile.get("hostname"),
                 profile.get("device_class"), body, "proposed", now, now))
            self.cx.commit()
            return {"change": "new", "fingerprint": fp}

        prev_body = json.loads(prev["profile"])
        def _cmp(p):
            return {k: p.get(k) for k in ("transports", "device_class", "vendor", "risks", "ip")}
        changed = _cmp(prev_body) != _cmp(profile)
        self.cx.execute("UPDATE candidates SET profile=?, ip=?, hostname=?, device_class=?, "
                        "last_seen=? WHERE fingerprint=?",
                        (body, profile.get("ip"), profile.get("hostname"),
                         profile.get("device_class"), now, fp))
        self.cx.commit()
        return {"change": "changed" if changed else "unchanged", "fingerprint": fp}

    def rescan_diff(self, profiles: list[dict]) -> dict:

        seen = set()
        new, changed = [], []
        for p in profiles:
            v = self.upsert_candidate(p)
            seen.add(v["fingerprint"])
            if v["change"] == "new":
                new.append(v["fingerprint"])
            elif v["change"] == "changed":
                changed.append(v["fingerprint"])
        prior = {r["fingerprint"] for r in
                 self.cx.execute("SELECT fingerprint FROM candidates WHERE status='proposed'")}
        gone = sorted(prior - seen)
        return {"new": new, "changed": changed, "gone": gone, "all": sorted(seen)}

    def candidates(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self.cx.execute("SELECT * FROM candidates WHERE status=? ORDER BY last_seen DESC",
                                   (status,)).fetchall()
        else:
            rows = self.cx.execute("SELECT * FROM candidates ORDER BY last_seen DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["profile"] = json.loads(d["profile"])
            out.append(d)
        return out

    def mark_onboarded(self, fingerprint: str, principal: str):

        row = self.cx.execute("SELECT profile, ip, mac, device_class FROM candidates "
                              "WHERE fingerprint=?", (fingerprint,)).fetchone()
        if not row:
            raise DiscoveryError(f"unknown candidate {fingerprint}")
        now = time.time()
        self.cx.execute(
            "INSERT OR REPLACE INTO device_profiles(fingerprint,ip,mac,device_class,principal,"
            "profile,onboarded_at) VALUES(?,?,?,?,?,?,?)",
            (fingerprint, row["ip"], row["mac"], row["device_class"], principal, row["profile"], now))
        self.cx.execute("UPDATE candidates SET status='onboarded' WHERE fingerprint=?",
                        (fingerprint,))
        self.cx.commit()

    def profiles(self) -> list[dict]:
        rows = self.cx.execute("SELECT * FROM device_profiles ORDER BY onboarded_at DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["profile"] = json.loads(d["profile"])
            out.append(d)
        return out
