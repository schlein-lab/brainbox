

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, Optional, Tuple

from tag import FundingKind, FundingTag

CENTRAL_SERVICE_CAPS: FrozenSet[str] = frozenset(
    {
        "pn-llmd:central-pool",
        "vpnd:central-lane",
        "db:shared",
        "cas:members-shared-mount",
        "notifyd:central",
        "egress:central",
    }
)

class BoundaryClass(enum.Enum):

    CGROUP_CELL = "cgroup-cell"
    NAMESPACES_SECCOMP = "namespaces+seccomp"
    GVISOR = "gvisor"
    MICROVM = "microvm"

    @property
    def is_microvm_or_gvisor_class(self) -> bool:

        return self in (BoundaryClass.GVISOR, BoundaryClass.MICROVM)

class GuestProvisioningError(Exception):
    pass

@dataclass(frozen=True)
class GuestIsolation:

    principal: str
    funding: FundingTag
    cas_namespace: str
    mount_namespace: str
    object_caps: FrozenSet[str]
    seccomp_profile: str
    user_ns: bool
    boundary: BoundaryClass
    refused_central_caps: Tuple[str, ...]

    def holds_central_cap(self, cap: Optional[str] = None) -> bool:

        if cap is not None:
            return cap in self.object_caps and cap in CENTRAL_SERVICE_CAPS
        return bool(self.object_caps & CENTRAL_SERVICE_CAPS)

    def satisfies_invariant(self) -> bool:

        return (
            not self.holds_central_cap()
            and self.cas_namespace != "cas:members-shared-mount"
            and self.mount_namespace != "mnt:members-shared"
            and self.boundary.is_microvm_or_gvisor_class
            and self.funding.is_byo
        )

    def to_dict(self) -> dict:

        return {
            "principal": self.principal,
            "funding": self.funding.to_wire(),
            "cas_namespace": self.cas_namespace,
            "mount_namespace": self.mount_namespace,
            "object_caps": sorted(self.object_caps),
            "seccomp_profile": self.seccomp_profile,
            "user_ns": self.user_ns,
            "boundary": self.boundary.value,
            "refused_central_caps": list(self.refused_central_caps),
            "microvm_or_gvisor_class": self.boundary.is_microvm_or_gvisor_class,
        }

def provision_guest(
    *,
    principal: str,
    funding: FundingTag,
    requested_caps: Iterable[str] = (),
    boundary: BoundaryClass = BoundaryClass.GVISOR,
    seccomp_profile: str = "guest-strict",
    user_ns: bool = True,
) -> GuestIsolation:

    if not principal.startswith("guest:"):
        raise GuestProvisioningError(
            f"guest principal must be namespaced 'guest:<AG>', got {principal!r}"
        )
    if not funding.is_byo:
        raise GuestProvisioningError(
            "a guest MUST be BYO — no member-subsidized tag is available to a "
            "guest principal (contract §8.2)"
        )
    if not boundary.is_microvm_or_gvisor_class:
        raise GuestProvisioningError(
            f"guest boundary must be microVM/gVisor-class (invariant #6), "
            f"got {boundary.value!r}"
        )

    requested = set(requested_caps)
    refused = tuple(sorted(requested & CENTRAL_SERVICE_CAPS))
    guest_local_caps = frozenset(requested - CENTRAL_SERVICE_CAPS)

    ag = principal.split(":", 1)[1] if ":" in principal else principal
    cas_ns = f"cas:guest:{ag}"
    mnt_ns = f"mnt:guest:{ag}"

    return GuestIsolation(
        principal=principal,
        funding=funding,
        cas_namespace=cas_ns,
        mount_namespace=mnt_ns,
        object_caps=guest_local_caps,
        seccomp_profile=seccomp_profile,
        user_ns=user_ns,
        boundary=boundary,
        refused_central_caps=refused,
    )

def apply_kernel_isolation(desc: GuestIsolation) -> None:

    raise NotImplementedError(
        "apply_kernel_isolation is a PLAN (owner-open Q1); needs root + host "
        "substrate. The descriptor's cap-zero + namespace-separation invariant "
        "is real and tested; the live boundary is the next increment."
    )
