
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple

class PolicyModelError(Exception):
    pass

POLICY_VERSION_TAG = 1

WILDCARD = "*"

EFFECT_ALLOW = "allow"
EFFECT_DENY = "deny"
EFFECT_REQUIRE_CEREMONY = "require-ceremony"
_EFFECTS = (EFFECT_ALLOW, EFFECT_DENY, EFFECT_REQUIRE_CEREMONY)

_W_PRINCIPAL = 4
_W_TASK_TYPE = 2
_W_RESOURCE = 1

@dataclass(frozen=True)
class Rule:

    principal: str = WILDCARD
    task_type: str = WILDCARD
    resource: str = WILDCARD
    effect: str = EFFECT_ALLOW
    ceremony: int = 0
    max_level: Optional[int] = None

    def validate(self) -> "Rule":
        for name, val in (("principal", self.principal), ("task_type", self.task_type),
                          ("resource", self.resource)):
            if not isinstance(val, str) or not val:
                raise PolicyModelError(f"rule {name} must be a non-empty string (or '*')")
        if self.effect not in _EFFECTS:
            raise PolicyModelError(f"unknown rule effect {self.effect!r}")
        if not isinstance(self.ceremony, int) or isinstance(self.ceremony, bool) or not (0 <= self.ceremony <= 3):
            raise PolicyModelError("rule ceremony must be an int 0..3")
        if self.effect == EFFECT_REQUIRE_CEREMONY and self.ceremony == 0:
            raise PolicyModelError("require-ceremony rule must name a ceremony stage >= 1")
        if self.effect != EFFECT_REQUIRE_CEREMONY and self.ceremony != 0:
            raise PolicyModelError(f"{self.effect} rule must not carry a ceremony stage")
        if self.max_level is not None:
            if not isinstance(self.max_level, int) or isinstance(self.max_level, bool) or not (0 <= self.max_level <= 5):
                raise PolicyModelError("rule max_level must be an int L0..L5 or None")
        return self

    def matches(self, principal: str, task_type: str, resource: str) -> bool:
        return ((self.principal == WILDCARD or self.principal == principal)
                and (self.task_type == WILDCARD or self.task_type == task_type)
                and (self.resource == WILDCARD or self.resource == resource))

    def specificity(self) -> int:
        return ((_W_PRINCIPAL if self.principal != WILDCARD else 0)
                + (_W_TASK_TYPE if self.task_type != WILDCARD else 0)
                + (_W_RESOURCE if self.resource != WILDCARD else 0))

    def to_dict(self) -> dict:
        return {"principal": self.principal, "task_type": self.task_type, "resource": self.resource,
                "effect": self.effect, "ceremony": self.ceremony, "max_level": self.max_level}

    @staticmethod
    def from_dict(d) -> "Rule":
        if not isinstance(d, dict):
            raise PolicyModelError("malformed rule")
        return Rule(
            principal=d.get("principal", WILDCARD), task_type=d.get("task_type", WILDCARD),
            resource=d.get("resource", WILDCARD), effect=d.get("effect", EFFECT_ALLOW),
            ceremony=d.get("ceremony", 0), max_level=d.get("max_level"),
        ).validate()

@dataclass(frozen=True)
class Policy:

    version: int
    effective_time: int
    rules: Tuple[Rule, ...] = ()
    owner_caps: Mapping[str, int] = field(default_factory=dict)
    prev_version_hash: Optional[str] = None
    meta: Mapping = field(default_factory=dict)

    def validate(self) -> "Policy":
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 0:
            raise PolicyModelError("policy version must be a non-negative integer")
        if not isinstance(self.effective_time, int) or isinstance(self.effective_time, bool):
            raise PolicyModelError("policy effective_time must be an integer unix time")
        if self.prev_version_hash is not None and not isinstance(self.prev_version_hash, str):
            raise PolicyModelError("prev_version_hash must be a hex string or None")
        for r in self.rules:
            if not isinstance(r, Rule):
                raise PolicyModelError("policy rules must be Rule instances")
            r.validate()
        for k, v in dict(self.owner_caps).items():
            if not isinstance(k, str) or not k:
                raise PolicyModelError("owner_caps keys must be non-empty principal strings")
            if not isinstance(v, int) or isinstance(v, bool) or not (0 <= v <= 5):
                raise PolicyModelError(f"owner_caps[{k!r}] must be an int L0..L5")
        return self

    def to_dict(self) -> dict:
        return {
            "v": POLICY_VERSION_TAG,
            "version": self.version,
            "effective_time": self.effective_time,
            "prev_version_hash": self.prev_version_hash,
            "owner_caps": {k: int(v) for k, v in dict(self.owner_caps).items()},
            "rules": [r.to_dict() for r in self.rules],
            "meta": dict(self.meta),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def from_dict(d) -> "Policy":
        if not isinstance(d, dict):
            raise PolicyModelError("malformed policy")
        try:
            version = int(d["version"])
            effective_time = int(d["effective_time"])
        except (KeyError, TypeError, ValueError):
            raise PolicyModelError("policy missing/invalid version or effective_time")
        rules = tuple(Rule.from_dict(r) for r in d.get("rules", []))
        owner_caps = {str(k): int(v) for k, v in dict(d.get("owner_caps", {})).items()}
        return Policy(version=version, effective_time=effective_time, rules=rules,
                      owner_caps=owner_caps, prev_version_hash=d.get("prev_version_hash"),
                      meta=dict(d.get("meta", {}))).validate()

    @staticmethod
    def from_json(s) -> "Policy":
        try:
            d = json.loads(s)
        except (ValueError, TypeError):
            raise PolicyModelError("policy is not valid JSON")
        return Policy.from_dict(d)

def canonical_bytes(policy: Policy) -> bytes:

    if not isinstance(policy, Policy):
        raise PolicyModelError("canonical_bytes expects a Policy")
    return policy.to_json().encode("utf-8")

_HASH_DOMAIN = b"brainarbeit/policy/hash/1"

def policy_hash(policy: Policy) -> str:

    return "pol:b2:" + hashlib.blake2s(_HASH_DOMAIN + canonical_bytes(policy),
                                       digest_size=16).hexdigest()

def select_rule(policy: Policy, principal: str, task_type: str, resource: str) -> Optional[Rule]:

    matches = [r for r in policy.rules if r.matches(principal, task_type, resource)]
    if not matches:
        return None
    top = max(r.specificity() for r in matches)
    tied = [r for r in matches if r.specificity() == top]

    for r in tied:
        if r.effect == EFFECT_DENY:
            return r

    ceremonies = [r for r in tied if r.effect == EFFECT_REQUIRE_CEREMONY]
    if ceremonies:
        return max(ceremonies, key=lambda r: r.ceremony)
    return tied[0]

__all__ = [
    "PolicyModelError", "Rule", "Policy", "WILDCARD",
    "EFFECT_ALLOW", "EFFECT_DENY", "EFFECT_REQUIRE_CEREMONY",
    "POLICY_VERSION_TAG", "canonical_bytes", "policy_hash", "select_rule",
]
