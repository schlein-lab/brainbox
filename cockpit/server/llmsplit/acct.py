

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Tuple

from tag import FundingKind, FundingTag

METERED_RESOURCES = frozenset(
    {"cpu_seconds", "mem_seconds", "llm_calls", "egress_bytes", "cas_bytes"}
)

FORBIDDEN_METER_RESOURCES = frozenset({"secret", "credential"})

class SecretMeteringRefused(Exception):
    pass

BucketKey = Tuple[str, str, str]

@dataclass
class Meter:

    principal: str
    funding_wire: str
    resource: str
    amount: float = 0.0
    events: int = 0

    def add(self, amount: float) -> None:
        if amount < 0:
            raise ValueError("metered amount must be non-negative")
        self.amount += amount
        self.events += 1

class Accountant:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: Dict[BucketKey, Meter] = {}

    def meter(
        self,
        *,
        principal: str,
        tag: FundingTag,
        resource: str,
        amount: float,
    ) -> None:

        if resource in FORBIDDEN_METER_RESOURCES:
            raise SecretMeteringRefused(
                f"refusing to meter secret-classed resource {resource!r} "
                "(contract §8.2 / invariant #9.2)"
            )
        if resource not in METERED_RESOURCES:
            raise ValueError(f"unknown metered resource {resource!r}")
        if not principal:
            raise ValueError("principal required for attribution")

        key: BucketKey = (principal, tag.to_wire(), resource)
        with self._lock:
            m = self._buckets.get(key)
            if m is None:
                m = Meter(principal=principal, funding_wire=tag.to_wire(), resource=resource)
                self._buckets[key] = m
            m.add(float(amount))

    def total_for(self, *, principal: str, tag: FundingTag, resource: str) -> float:
        key: BucketKey = (principal, tag.to_wire(), resource)
        with self._lock:
            m = self._buckets.get(key)
            return m.amount if m else 0.0

    def subsidized_total(self, resource: str) -> float:

        member_wire = FundingKind.MEMBER_SUBSIDIZED.value
        with self._lock:
            return sum(
                m.amount
                for (p, fw, r), m in self._buckets.items()
                if fw == member_wire and r == resource
            )

    def byo_total(self, *, principal: str, resource: str) -> float:

        with self._lock:
            return sum(
                m.amount
                for (p, fw, r), m in self._buckets.items()
                if p == principal and fw.startswith("byo") and r == resource
            )

    def snapshot(self) -> Dict[str, Dict[str, float]]:

        with self._lock:
            return {
                f"{m.principal}|{m.funding_wire}|{m.resource}": {
                    "amount": m.amount,
                    "events": float(m.events),
                }
                for m in self._buckets.values()
            }

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
