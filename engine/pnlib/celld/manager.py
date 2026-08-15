
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Optional

from .model import (
    Cell, ResourceEnvelope, CellError, QuotaExceeded, AuthorityError, next_state,
    CREATED,
)

TENANTS_SLICE_ROOT = "pn-tenants.slice"
CONTROL_SLICE = "control.slice"

_JAILER_BASE_UID = 900000
_JAILER_UID_SPAN = 60000

@dataclass(frozen=True)
class MicroVMSpec:

    cell_id: str
    tenant: str
    vcpus: int
    mem_bytes: int
    kernel_ref: str
    initramfs_ref: str
    rootfs_base_ref: str
    rootfs_delta_ref: str
    cmdline: str
    virtio: tuple
    jailer_uid: int
    seccomp: str = "strict"
    boots_own_kernel: bool = True
    host_mounts: tuple = ()
    host_cred_refs: tuple = ()

    def validate(self) -> "MicroVMSpec":
        if self.host_mounts:
            raise AuthorityError(
                f"microVM spec must have NO host mounts, got {tuple(self.host_mounts)!r}")
        if self.host_cred_refs:
            raise AuthorityError(
                f"microVM spec must carry NO host creds, got {tuple(self.host_cred_refs)!r}")
        if not self.boots_own_kernel:
            raise AuthorityError("a tenant cell must boot its OWN kernel (not share the host's)")
        if self.vcpus < 1:
            raise QuotaExceeded("microVM spec needs at least 1 vcpu")
        if self.mem_bytes < 1:
            raise QuotaExceeded("microVM spec needs a positive memory size")
        return self

    def to_dict(self) -> dict:
        return {
            "cell_id": self.cell_id, "tenant": self.tenant,
            "vcpus": self.vcpus, "mem_bytes": self.mem_bytes,
            "kernel": self.kernel_ref, "initramfs": self.initramfs_ref,
            "rootfs": {"base_ro": self.rootfs_base_ref, "delta_cow_encrypted": self.rootfs_delta_ref,
                       "delta_ephemeral": True},
            "cmdline": self.cmdline,
            "virtio": [dict(d) for d in self.virtio],
            "jailer": {"uid": self.jailer_uid, "seccomp": self.seccomp, "unprivileged": True},
            "boots_own_kernel": self.boots_own_kernel,
            "host_mounts": list(self.host_mounts),
            "host_cred_refs": list(self.host_cred_refs),

            "note": "MODELL/Beschreibung — dieses Modul bootet NICHTS. Der Start laeuft ueber "
                    "pn-vmm (~/brainarbeit/os/pn-vmm); ob dort KVM benutzt wird, entscheidet "
                    "die Wirtsmaschine, nicht diese Zeile.",
        }

class CellManager:

    def __init__(self):
        self._cells: dict[str, Cell] = {}
        self._quota: dict[str, ResourceEnvelope] = {}
        self._authority: dict[str, frozenset] = {}
        self._jailer_uids: dict[str, int] = {}

    def set_tenant_quota(self, tenant: str, quota: ResourceEnvelope) -> None:
        self._quota[tenant] = quota.validate()

    def set_parent_authority(self, tenant: str, capabilities) -> None:
        self._authority[tenant] = frozenset(str(c) for c in capabilities)

    def create(self, *, id: str, tenant: str, envelope: ResourceEnvelope,
               capabilities, parent: Optional[str] = None) -> Cell:

        if id in self._cells:
            raise CellError(f"cell {id!r} already exists")
        envelope.validate()
        caps = frozenset(str(c) for c in capabilities)

        if envelope.cpu_pct < 1 or envelope.mem_bytes < 1:
            raise QuotaExceeded(
                f"cell {id!r} envelope is non-runnable (cpu_pct={envelope.cpu_pct}, "
                f"mem_bytes={envelope.mem_bytes}); a cell needs a positive cpu and memory cap")

        quota = self._quota.get(tenant)
        if quota is not None:
            over = envelope.exceeds(quota)
            if over:
                raise QuotaExceeded(
                    f"cell {id!r} envelope exceeds tenant {tenant!r} quota on {list(over)}")

            agg = self._aggregate_with(tenant, envelope)
            over_agg = agg.exceeds(quota)
            if over_agg:
                used = self._tenant_usage(tenant)
                raise QuotaExceeded(
                    f"cell {id!r} would push tenant {tenant!r} AGGREGATE over quota on "
                    f"{list(over_agg)} (already used {used.to_dict()}, quota {quota.to_dict()}, "
                    f"requested {envelope.to_dict()})")

        authority = self._authority.get(tenant)
        if authority is not None and not caps <= authority:
            raise AuthorityError(
                f"cell {id!r} capabilities {sorted(caps - authority)} exceed the parent authority "
                f"of tenant {tenant!r}")

        cell = Cell(id=id, tenant=tenant, envelope=envelope, capabilities=caps,
                    state=CREATED, parent=parent)
        self._cells[id] = cell
        return cell

    def _tenant_usage(self, tenant: str) -> ResourceEnvelope:

        dims = ResourceEnvelope._DIMS
        tot = {d: 0 for d in dims}
        for c in self._cells.values():
            if c.tenant == tenant:
                for d in dims:
                    tot[d] += getattr(c.envelope, d)
        return ResourceEnvelope(cpu_pct=tot["cpu_pct"], mem_bytes=tot["mem_bytes"],
                                io_bps=tot["io_bps"], net_bps=tot["net_bps"], pids=tot["pids"])

    def _aggregate_with(self, tenant: str, envelope: ResourceEnvelope) -> ResourceEnvelope:

        used = self._tenant_usage(tenant)
        dims = ResourceEnvelope._DIMS
        s = {d: getattr(used, d) + getattr(envelope, d) for d in dims}
        return ResourceEnvelope(cpu_pct=s["cpu_pct"], mem_bytes=s["mem_bytes"],
                                io_bps=s["io_bps"], net_bps=s["net_bps"], pids=s["pids"])

    def _jailer_uid_for(self, tenant: str) -> int:

        existing = self._jailer_uids.get(tenant)
        if existing is not None:
            return existing
        seed = int.from_bytes(hashlib.sha256(tenant.encode("utf-8")).digest()[:4], "big")
        taken = set(self._jailer_uids.values())
        offset = seed % _JAILER_UID_SPAN
        for probe in range(_JAILER_UID_SPAN):
            uid = _JAILER_BASE_UID + ((offset + probe) % _JAILER_UID_SPAN)
            if uid not in taken:
                self._jailer_uids[tenant] = uid
                return uid
        raise QuotaExceeded("jailer uid window exhausted — cannot allocate a unique cell uid")

    def _transition(self, id: str, action: str) -> Cell:
        cell = self.get(id)
        new = cell.with_state(next_state(cell.state, action))
        self._cells[id] = new
        return new

    def start(self, id: str) -> Cell:   return self._transition(id, "start")
    def freeze(self, id: str) -> Cell:  return self._transition(id, "freeze")
    def resume(self, id: str) -> Cell:  return self._transition(id, "resume")
    def stop(self, id: str) -> Cell:    return self._transition(id, "stop")

    def destroy(self, id: str) -> Cell:

        cell = self._transition(id, "destroy")

        self._cells.pop(id, None)
        return cell

    def get(self, id: str) -> Cell:
        cell = self._cells.get(id)
        if cell is None:
            raise CellError(f"no such cell {id!r}")
        return cell

    def cells(self) -> tuple:
        return tuple(self._cells.values())

    def slice_descriptor(self, cell: Cell) -> dict:

        env = cell.envelope
        unit = f"pn-cell-{cell.tenant}-{cell.id}.slice"
        return {
            "unit": unit,
            "parent_slice": TENANTS_SLICE_ROOT,
            "is_control_slice": False,
            "controllers": {
                "CPUQuota": f"{env.cpu_pct}%",
                "MemoryMax": env.mem_bytes,
                "MemoryHigh": int(env.mem_bytes * 0.9),
                "IOReadBandwidthMax": env.io_bps,
                "IOWriteBandwidthMax": env.io_bps,
                "TasksMax": env.pids,
            },

            "Delegate": False,
        }

    def microvm_spec(self, cell: Cell) -> MicroVMSpec:

        env = cell.envelope
        vcpus = max(1, math.ceil(env.cpu_pct / 100))
        spec = MicroVMSpec(
            cell_id=cell.id, tenant=cell.tenant, vcpus=vcpus, mem_bytes=env.mem_bytes,
            kernel_ref="cas:kernel/vmlinux.bin",
            initramfs_ref="cas:kernel/initramfs.cpio",
            rootfs_base_ref="cas:base/appliance-ro",
            rootfs_delta_ref=f"delta:{cell.tenant}/{cell.id}",
            cmdline="console=ttyS0 reboot=t panic=-1 ro init=/init",
            virtio=(

                {"type": "vsock", "cid": "per-cell", "purpose": "control+seat (RFB/stream/input)"},
                {"type": "blk", "target": "delta", "readonly": False, "encrypted": True,
                 "size_cap_bytes": env.mem_bytes},
                {"type": "blk", "target": "base", "readonly": True},
                {"type": "net", "mode": "per-tenant-netns-or-byo-vpn", "egress_bps": env.net_bps},
            ),
            jailer_uid=self._jailer_uid_for(cell.tenant),
            host_mounts=(),
            host_cred_refs=(),
        )
        return spec.validate()

__all__ = ["CellManager", "MicroVMSpec", "TENANTS_SLICE_ROOT", "CONTROL_SLICE"]
