
from __future__ import annotations
import json, os, time

from . import discover as D
from . import transport as T

def mock_observations(own_cidrs):
    base = (own_cidrs[0].split("/")[0].rsplit(".", 1)[0]) if own_cidrs else \
        os.environ.get("PN_DISCOVER_MOCK_PREFIX", "192.0.2")
    return [
        {"ip": f"{base}.41", "mac": "02:00:00:00:00:01", "hostname": "BRN13ABCD",
         "services": [
             {"scheme": "ipp", "port": 631,
              "model": "Brother HL-L2350DW series", "sysDescr": "Brother NC-8200w, Firmware 1.34"},
             {"scheme": "snmp", "port": 161, "sysDescr": "Brother HL-L2350DW series"},
         ]},
        {"ip": f"{base}.52", "mac": "02:00:00:00:00:02", "hostname": "WDMYCLOUD",
         "services": [
             {"scheme": "smb", "port": 445, "shares": ["Public", "TimeMachine"], "smb1": True},
             {"scheme": "snmp", "port": 161, "sysDescr": "WD My Cloud EX2 Linux 3.2"},
         ]},
        {"ip": f"{base}.73", "mac": "02:00:00:00:00:03", "hostname": "pi-hole",
         "services": [
             {"scheme": "snmp", "port": 161, "sysDescr": "Raspberry Pi OS Linux 6.1 aarch64"},
         ]},
        {"ip": f"{base}.99", "mac": "02:00:00:00:00:04", "hostname": "OLDCTRL",
         "services": [
             {"scheme": "telnet", "port": 23, "banner": "legacy control unit v1 login:",
              "sysDescr": "Ayecom legacy controller"},
         ]},
    ]

def fixture_observations(path):
    with open(path) as f:
        obs = json.load(f)
    return obs if isinstance(obs, list) else obs.get("observations", [])

def gather_observations(mode, own_cidrs, *, fixture=None, live_targets=None,
                        registry=None, rate_per_sec=5.0):

    if mode == "mock":
        return mock_observations(own_cidrs), False
    if mode == "fixture":
        if not fixture:
            raise D.DiscoveryError("mode=fixture requires PN_DISCOVER_FIXTURE")
        return fixture_observations(fixture), False
    if mode == "live":

        reg = registry or T.default_registry()
        out = []
        limiter = D.RateLimiter(rate_per_sec)
        for t in (live_targets or []):
            D.assert_own_cidr(t["ip"], own_cidrs)
            out.append(D.gentle_active_probe(t, own_cidrs, reg, limiter))
        return out, True
    raise D.DiscoveryError(f"unknown discover mode {mode!r} (mock|fixture|live)")

def run_discovery(*, own_cidrs, mode="mock", store_path=None, fixture=None,
                  live_targets=None, registry=None, rate_per_sec=5.0, now=None):

    now = time.time() if now is None else now
    reg = registry or T.default_registry()
    raw, live = gather_observations(mode, own_cidrs, fixture=fixture,
                                    live_targets=live_targets, registry=reg,
                                    rate_per_sec=rate_per_sec)
    profiles = [D.classify(o, reg) for o in raw]
    store = D.DiscoveryStore(store_path)
    try:
        diff = store.rescan_diff(profiles)
        by_fp = {D.fingerprint_key(p): p for p in profiles}
        proposals = []
        for fp in diff["new"] + diff["changed"]:
            p = by_fp.get(fp)
            if p is not None:
                proposals.append(D.propose_payload(p))
    finally:
        store.close()
    return {"ok": True, "mode": mode, "scanned": len(profiles), "scanned_live": live,
            "candidates": profiles, "proposals": proposals, "diff": diff, "at": now}

def proposals_to_actions(report, *, task_type_allowlist=None):

    actions = []
    for pay in report.get("proposals", []):
        dev = pay.get("device", {})
        ip = dev.get("ip") or "0.0.0.0"
        actions.append({
            "op": "propose",
            "kind": "onboard",
            "summary": pay.get("summary", "discovered device"),

            "task_type": "net.discover",
            "params": {"cidr": f"{ip}/32"},
            "detail": {"device": dev, "risks": pay.get("risks"),
                       "composable_capabilities": pay.get("composable_capabilities"),
                       "fingerprint": pay.get("fingerprint")},
            "reason": "observe-only discovery candidate (human approves onboarding; brain cannot)",
        })
    return actions
