

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

CGROUP2_ROOT = "/sys/fs/cgroup"

class SandboxError(Exception):
    pass

@dataclass
class SecretLeafSpec:

    name: str
    parent: str = "brainarbeit/secret"
    memory_high_bytes: Optional[int] = None
    memory_max_bytes: Optional[int] = None
    swap_max: int = 0
    disk_write_denied: bool = True

    tmpfs_scratch_bytes: Optional[int] = 16 * 1024 * 1024

    def __post_init__(self):
        if self.swap_max != 0:
            raise SandboxError(
                f"secret-bearing leaf {self.name!r} must have swap.max=0 "
                f"(invariant §8.3); got {self.swap_max}"
            )
        if not self.disk_write_denied:
            raise SandboxError(
                f"secret-bearing leaf {self.name!r} must be disk-write-denied "
                f"(invariant §8.3)"
            )

    @property
    def path(self) -> str:
        return os.path.join(CGROUP2_ROOT, self.parent, self.name)

    def controls(self) -> dict:

        ctrl = {"memory.swap.max": str(self.swap_max)}
        if self.memory_high_bytes is not None:
            ctrl["memory.high"] = str(self.memory_high_bytes)
        if self.memory_max_bytes is not None:
            ctrl["memory.max"] = str(self.memory_max_bytes)
        return ctrl

    def mount_plan(self) -> dict:

        plan = {"durable_paths_readonly": True, "writable": []}
        if self.tmpfs_scratch_bytes and not self.disk_write_denied:

            plan["writable"].append(
                {"type": "tmpfs", "path": "/scratch",
                 "size_bytes": self.tmpfs_scratch_bytes})
        elif self.tmpfs_scratch_bytes:

            plan["writable"].append(
                {"type": "tmpfs", "path": "/scratch",
                 "size_bytes": self.tmpfs_scratch_bytes, "ram_only": True})
        return plan

    @staticmethod
    def host_supports_cgroup2() -> bool:
        return os.path.isdir(CGROUP2_ROOT) and os.path.exists(
            os.path.join(CGROUP2_ROOT, "cgroup.controllers"))

    def realize(self, *, dry_run: Optional[bool] = None) -> dict:

        if dry_run is None:
            dry_run = not (self.host_supports_cgroup2() and os.geteuid() == 0)

        report = {"path": self.path, "controls": self.controls(),
                  "mount_plan": self.mount_plan(), "dry_run": dry_run,
                  "swap_pinned": None}

        if dry_run:
            report["swap_pinned"] = (self.swap_max == 0)
            report["note"] = ("dry-run: spec validated; not applied (needs root "
                              "+ cgroup-v2 host). swap.max=0 guaranteed by spec.")
            return report

        os.makedirs(self.path, exist_ok=True)
        for cfile, val in self.controls().items():
            with open(os.path.join(self.path, cfile), "w") as fh:
                fh.write(val)

        with open(os.path.join(self.path, "memory.swap.max")) as fh:
            got = fh.read().strip()
        if got not in ("0",):
            raise SandboxError(
                f"post-write memory.swap.max={got!r} for {self.name!r}; refusing "
                f"to run a secret-bearing leaf that can swap (invariant §8.3)"
            )
        report["swap_pinned"] = True
        return report

def secret_leaf(name: str, **kw) -> SecretLeafSpec:

    return SecretLeafSpec(name=name, swap_max=0, disk_write_denied=True, **kw)

