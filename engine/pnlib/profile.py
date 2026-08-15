
from __future__ import annotations
from dataclasses import dataclass, asdict, field
import json

@dataclass
class ResourceProfile:

    mem: int = 256
    mem_high_mult: float = 4.0

    mem_max: int | None = None

    cpu_weight: int = 50
    cpu_quota_pct: int | None = None

    io_weight: int = 50
    disk_min_free: int = 2048
    disk_max: int | None = None

    sandbox: str = "default"

    trusted: bool = False

    llm_weight: int = 0
    llm_kind: str = "loose"

    kerne: int | None = None
    kerne_wunsch: int | None = None
    dauer_s: int | None = None
    flexibel: bool = False

    prio: int = 100

    latency: str = "deferrable"
    timeout_s: int = 3600

    max_extend_s: int = 0

    oom_grow: bool = False
    oom_grow_mult: float = 1.5
    max_oom_retries: int = 2

    idempotent: bool = False
    max_retries: int = 3

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @staticmethod
    def from_json(s: str) -> "ResourceProfile":
        try:
            d = json.loads(s) if s else {}
        except Exception:
            d = {}

        f = {k: v for k, v in d.items() if k in ResourceProfile.__dataclass_fields__}
        return ResourceProfile(**f)

    def systemd_properties(self, io_enabled: bool = False, mem_high: int | None = None) -> list[str]:

        props = [
            f"CPUWeight={self.cpu_weight}",
            "OOMScoreAdjust=600",

            "TimeoutStopSec=10s",
        ]

        props.append(f"MemoryLow={int(self.mem)}M")
        high = mem_high if mem_high else max(self.mem, int(self.mem * self.mem_high_mult))
        props.append(f"MemoryHigh={int(high)}M")
        if self.mem_max:
            props.append(f"MemoryMax={int(self.mem_max)}M")
        if self.cpu_quota_pct:
            props.append(f"CPUQuota={self.cpu_quota_pct}%")
        if io_enabled:
            props.append(f"IOWeight={self.io_weight}")
        props += SANDBOXES.get(self.sandbox, SANDBOXES["default"])
        return props

_SEAL_HOME = (
    "%h/.ssh", "%h/.gnupg", "%h/.netrc", "%h/.git-credentials", "%h/.aws",
    "%h/.config/gh", "%h/.config/ha-llt.token", "%h/.config/homeassistant-owner.env",
    "%h/.config/brainbox-workers",
)

_SEAL_PN = (
    "%h/.local/share/portioneer/secrets",
    "%h/.local/share/portioneer/broker-secrets",
    "%h/.local/share/portioneer/relay",
    "%h/.local/share/portioneer/device",
    "%h/.local/share/portioneer/queue.db",
    "%h/.local/share/portioneer/queue.db-wal",
    "%h/.local/share/portioneer/queue.db-shm",
    "%h/.local/share/portioneer/queue-archiv.db",
)

_SEAL_HIRN = ("%h/.claude",)

_SEAL_POOL = ("%h/.pn-poolhome", "%h/.llmpool")

_SEAL_FLOTTE = ("%h/data-pipeline/.pipeline.key",)

def _riegel(*gruppen) -> str:

    return "InaccessiblePaths=" + " ".join(p for g in gruppen for p in g)

SANDBOXES: dict[str, list[str]] = {
    "default": [
        "NoNewPrivileges=yes",

        _riegel(_SEAL_HOME, _SEAL_PN, _SEAL_HIRN, _SEAL_POOL, _SEAL_FLOTTE),
    ],

    "compute": [
        "NoNewPrivileges=yes",
        "RestrictSUIDSGID=yes",
        "ProtectKernelTunables=yes",
        "ProtectControlGroups=yes",

        _riegel(_SEAL_HOME, _SEAL_PN, _SEAL_HIRN, _SEAL_FLOTTE),
    ],

    "netjob": [
        "NoNewPrivileges=yes",
        "RestrictSUIDSGID=yes",
        "ProtectKernelTunables=yes",

        _riegel(_SEAL_HOME, _SEAL_PN, _SEAL_HIRN, _SEAL_POOL),
    ],

    "llm": [
        "NoNewPrivileges=yes",

        _riegel(_SEAL_HOME, _SEAL_PN, _SEAL_FLOTTE),
    ],

    "strict": [
        "NoNewPrivileges=yes",
        "RestrictSUIDSGID=yes",
        "ProtectKernelTunables=yes",
        "ProtectControlGroups=yes",
        "PrivateTmp=yes",
        "ProtectSystem=strict",
        "ProtectHome=read-only",
        "ReadWritePaths=%h/.local/share/portioneer",
        "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK",
        "SystemCallFilter=@system-service",
        "SystemCallErrorNumber=EPERM",

        _riegel(_SEAL_HOME, _SEAL_HIRN, _SEAL_POOL, _SEAL_FLOTTE),
    ],

    "worker-strict": [
        "NoNewPrivileges=yes",
        "RestrictSUIDSGID=yes",
        "ProtectKernelTunables=yes",
        "ProtectControlGroups=yes",
        "PrivateTmp=yes",
        "ProtectSystem=strict",
        "ProtectHome=read-only",
        "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK",
        "SystemCallFilter=@system-service",
        "SystemCallErrorNumber=EPERM",

        _riegel(_SEAL_HOME, _SEAL_PN, _SEAL_HIRN, _SEAL_POOL, _SEAL_FLOTTE),
    ],

    "cell_isolated": [
        "NoNewPrivileges=yes",
        "RestrictSUIDSGID=yes",
        "ProtectKernelTunables=yes",
        "ProtectControlGroups=yes",
        "PrivateTmp=yes",
        "ProtectSystem=strict",
        "ProtectHome=read-only",
        "RestrictAddressFamilies=AF_UNSPEC",
        "SystemCallFilter=@system-service",
        "SystemCallErrorNumber=EPERM",

        _riegel(_SEAL_HOME, _SEAL_PN, _SEAL_HIRN, _SEAL_POOL, _SEAL_FLOTTE),
    ],
}

CLASSES: dict[str, ResourceProfile] = {

    "tiny":      ResourceProfile(mem=96,  cpu_weight=30, cpu_quota_pct=50, sandbox="compute", prio=120,
                                 timeout_s=600, disk_max=256),

    "compute":   ResourceProfile(mem=256, cpu_weight=50, sandbox="compute", prio=100, timeout_s=2400,
                                 disk_max=4096),

    "dq_compute":ResourceProfile(mem=2048, cpu_weight=50, sandbox="netjob",  disk_min_free=4096, prio=100, timeout_s=2400, idempotent=True, oom_grow=False),

    "dq_run":    ResourceProfile(mem=2200, cpu_weight=50, sandbox="netjob",  disk_min_free=4096, prio=100, timeout_s=3000, idempotent=True,
                                 oom_grow=False, disk_max=1024),
    "enum":      ResourceProfile(mem=96,  cpu_weight=30, sandbox="netjob",  prio=110, timeout_s=900, idempotent=True, oom_grow=False),

    "inventory": ResourceProfile(mem=400, cpu_weight=40, sandbox="netjob",  prio=105, timeout_s=1800, idempotent=True, oom_grow=False),
    "llm":       ResourceProfile(mem=320, cpu_weight=40, cpu_quota_pct=50, sandbox="llm",     llm_weight=100, llm_kind="dedicated", prio=90, timeout_s=600, latency="realtime"),
    "llm_small": ResourceProfile(mem=64,  cpu_weight=20, cpu_quota_pct=30, sandbox="llm",     llm_weight=20, llm_kind="loose", prio=95, timeout_s=300, latency="realtime"),

    "commission":ResourceProfile(mem=512, cpu_weight=50, sandbox="llm",     llm_weight=1, llm_kind="dedicated", prio=80, timeout_s=7200,
                                 latency="realtime", trusted=True, disk_max=4096),

    "media.cell":ResourceProfile(mem=512, cpu_weight=60, cpu_quota_pct=200, sandbox="netjob",
                                 llm_weight=0, prio=85,
                                 timeout_s=43200, latency="realtime", trusted=True),

    "worker":    ResourceProfile(mem=900, cpu_weight=50, sandbox="netjob",  disk_min_free=2048, prio=100, timeout_s=3600, idempotent=True,
                                 oom_grow=False, max_extend_s=3600),

    "filler":    ResourceProfile(mem=512, cpu_weight=20, sandbox="llm", llm_weight=1, llm_kind="dedicated", prio=200, timeout_s=1800,
                                 idempotent=True, oom_grow=False, max_extend_s=1800, latency="filler"),
    "repro.room":ResourceProfile(mem=512, cpu_weight=20, sandbox="llm", llm_weight=1, llm_kind="dedicated", prio=200, timeout_s=1800,
                                 idempotent=True, oom_grow=False, max_extend_s=1800, latency="filler"),

    "spreadsheet.calc": ResourceProfile(mem=200, cpu_weight=40, cpu_quota_pct=50, sandbox="llm", llm_weight=6, llm_kind="loose", prio=100, timeout_s=1200, latency="realtime"),

    "cell.compute": ResourceProfile(mem=512, cpu_weight=40, sandbox="cell_isolated", llm_weight=0, prio=110, timeout_s=3600, latency="deferrable",
                                 idempotent=False, oom_grow=False, trusted=False),
}

def resolve(name: str | None, **overrides) -> ResourceProfile:
    base = CLASSES.get(name or "compute", CLASSES["compute"])
    p = ResourceProfile(**{**asdict(base), **{k: v for k, v in overrides.items() if v is not None}})
    return p

_INT_FIELDS = frozenset({"mem", "mem_max", "cpu_weight", "cpu_quota_pct", "io_weight",
                         "disk_min_free", "disk_max", "llm_weight", "prio", "timeout_s",
                         "max_retries", "max_extend_s", "max_oom_retries",
                         "kerne", "kerne_wunsch", "dauer_s"})
_FLOAT_FIELDS = frozenset({"mem_high_mult", "oom_grow_mult"})
_STR_FIELDS = frozenset({"sandbox", "llm_kind"})
_BOOL_FIELDS = frozenset({"idempotent", "oom_grow", "flexibel"})
_NULLABLE_INT_FIELDS = frozenset({"mem_max", "cpu_quota_pct", "disk_max",
                                  "kerne", "kerne_wunsch", "dauer_s"})

def _coerce_field(k, v):

    if k in _NULLABLE_INT_FIELDS and v is None:
        return True, None
    if v is None:
        return False, None
    if k in _INT_FIELDS:
        if isinstance(v, bool):
            return False, None
        if isinstance(v, int):
            return True, v
        if isinstance(v, float) and v.is_integer():
            return True, int(v)
        if isinstance(v, str):
            try:
                return True, int(v.strip())
            except (TypeError, ValueError):
                return False, None
        return False, None
    if k in _FLOAT_FIELDS:
        if isinstance(v, bool):
            return False, None
        if isinstance(v, (int, float)):
            return True, float(v)
        if isinstance(v, str):
            try:
                return True, float(v.strip())
            except (TypeError, ValueError):
                return False, None
        return False, None
    if k in _BOOL_FIELDS:
        if isinstance(v, bool):
            return True, v
        return False, None
    if k in _STR_FIELDS:
        return (True, v) if isinstance(v, str) else (False, None)

    return True, v

def _patch_dict(patch) -> dict:

    if not patch:
        return {}
    if isinstance(patch, str):
        try:
            patch = json.loads(patch)
        except Exception:
            return {}
    if not isinstance(patch, dict):
        return {}
    out = {}
    for k, v in patch.items():
        if k not in ResourceProfile.__dataclass_fields__:
            continue

        if k == "trusted":
            continue

        if k in ("kerne", "kerne_wunsch", "flexibel"):
            continue
        ok, cv = _coerce_field(k, v)
        if ok:
            out[k] = cv
    return out

def estimate(klass: str | None, template_patch=None, history=None, **overrides) -> ResourceProfile:

    base = asdict(CLASSES.get(klass or "compute", CLASSES["compute"]))
    merged = {**base, **_patch_dict(template_patch)}

    hist = _patch_dict(history)
    floor_dims = ("mem", "cpu_weight", "llm_weight", "disk_min_free")
    for k, v in hist.items():
        if k in floor_dims and isinstance(v, (int, float)) and isinstance(merged.get(k), (int, float)):
            merged[k] = max(merged[k], v)
        else:
            merged[k] = v

    for k, v in overrides.items():
        if v is None or k not in ResourceProfile.__dataclass_fields__:
            continue
        ok, cv = _coerce_field(k, v)
        if ok:
            merged[k] = cv
    f = {k: v for k, v in merged.items() if k in ResourceProfile.__dataclass_fields__}
    return ResourceProfile(**f)
