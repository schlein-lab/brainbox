
from __future__ import annotations
import os, subprocess, shlex

BATCH_MARK = 0x20
TABLE = "portioneer"
CONF = "/etc/portioneer/net.conf"

DEFAULTS = {
    "ENABLE": "1",
    "IFACE": "",
    "UID": str(os.getuid()),
    "RATE_MBIT": "1000",
    "INTER_PCT": "80",
    "BATCH_PCT": "10",
    "BATCH_CEIL_PCT": "60",
}

def detect_iface() -> str | None:
    try:
        out = subprocess.run(["ip", "route", "show", "default"],
                             capture_output=True, text=True, timeout=5).stdout.split()
        if "dev" in out:
            return out[out.index("dev") + 1]
    except Exception:
        pass
    return None

def batch_cgroup(uid: int) -> str:

    return (f"user.slice/user-{uid}.slice/user@{uid}.service/pn.slice/pn-batch.slice")

def cgroup_level(path: str) -> int:
    return len([c for c in path.split("/") if c])

def load_conf(path: str = CONF) -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.split("#", 1)[0].strip().strip('"')
                cfg[k.strip()] = v
    except FileNotFoundError:
        pass
    if not cfg.get("IFACE"):
        cfg["IFACE"] = detect_iface() or ""
    return cfg

def plan(cfg: dict) -> dict:

    iface = cfg["IFACE"]
    rate = int(cfg["RATE_MBIT"])
    inter = max(1, rate * int(cfg["INTER_PCT"]) // 100)
    brate = max(1, rate * int(cfg["BATCH_PCT"]) // 100)
    bceil = max(brate, rate * int(cfg["BATCH_CEIL_PCT"]) // 100)
    uid = int(cfg["UID"])
    cg = batch_cgroup(uid)
    lvl = cgroup_level(cg)
    mark = hex(BATCH_MARK)

    tc = [
        ["tc", "qdisc", "add", "dev", iface, "root", "handle", "1:", "htb", "default", "10"],
        ["tc", "class", "add", "dev", iface, "parent", "1:", "classid", "1:1",
         "htb", "rate", f"{rate}mbit", "ceil", f"{rate}mbit"],

        ["tc", "class", "add", "dev", iface, "parent", "1:1", "classid", "1:10",
         "htb", "rate", f"{inter}mbit", "ceil", f"{rate}mbit", "prio", "0"],

        ["tc", "class", "add", "dev", iface, "parent", "1:1", "classid", "1:20",
         "htb", "rate", f"{brate}mbit", "ceil", f"{bceil}mbit", "prio", "1"],
        ["tc", "qdisc", "add", "dev", iface, "parent", "1:10", "handle", "110:", "fq_codel"],
        ["tc", "qdisc", "add", "dev", iface, "parent", "1:20", "handle", "120:", "fq_codel"],
        ["tc", "filter", "add", "dev", iface, "parent", "1:", "protocol", "all",
         "handle", mark, "fw", "flowid", "1:20"],
    ]
    nft = (
        f"table inet {TABLE} {{\n"
        f"  chain mark_batch {{\n"
        f"    type filter hook output priority mangle; policy accept;\n"
        f'    socket cgroupv2 level {lvl} "{cg}" meta mark set {mark} counter\n'
        f"  }}\n"
        f"}}\n"
    )
    clear = [
        ["tc", "qdisc", "del", "dev", iface, "root"],
        ["nft", "delete", "table", "inet", TABLE],
    ]
    return {"tc": tc, "nft": nft, "clear": clear, "summary":
            f"iface={iface} rate={rate}mbit inter>={inter} batch={brate}..{bceil} "
            f"mark={mark} cgroup(level {lvl})={cg}"}

def _run(argv, check=False):
    r = subprocess.run(argv, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()

def clear(cfg: dict) -> list[str]:
    log = []
    for argv in plan(cfg)["clear"]:
        rc, out = _run(argv)
        log.append(f"{'ok ' if rc == 0 else 'skip'} {' '.join(argv)}" + (f"  ({out})" if out and rc else ""))
    return log

def apply(cfg: dict) -> tuple[bool, list[str]]:
    p = plan(cfg)
    log = [f"plan: {p['summary']}"]

    for argv in p["clear"]:
        _run(argv)
    ok = True
    for argv in p["tc"]:
        rc, out = _run(argv)
        log.append(f"{'ok ' if rc == 0 else 'ERR'} {' '.join(argv)}" + (f"  ({out})" if out else ""))
        if rc != 0:
            ok = False
    rc, out = subprocess.run(["nft", "-f", "-"], input=p["nft"], capture_output=True, text=True).returncode, ""
    log.append(("ok " if rc == 0 else "ERR") + " nft -f (mark_batch)")
    if rc != 0:
        ok = False
    return ok, log

def status(cfg: dict) -> str:
    iface = cfg["IFACE"]
    out = []
    out.append("=== tc -s class show dev %s ===" % iface)
    out.append(_run(["tc", "-s", "class", "show", "dev", iface])[1])
    out.append("=== nft list table inet %s ===" % TABLE)
    out.append(_run(["nft", "list", "table", "inet", TABLE])[1])
    return "\n".join(out)
