
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Tuple
import time as _time

from pnlib.autonomy import levels as L
from .model import (
    Policy, Rule, select_rule,
    EFFECT_ALLOW, EFFECT_DENY, EFFECT_REQUIRE_CEREMONY,
)
from .verify import verify_signed_policy, PolicyVerifyError

PERMIT = "permit"
DENY = "deny"
REQUIRE_CEREMONY = "require-ceremony"

FAIL_SAFE_MAX_LEVEL = L.FAIL_SAFE_MAX_LEVEL

_DEFAULT_OWNER_CAP = L.MAX_LEVEL

@dataclass(frozen=True)
class PolicyDecision:

    effect: str
    effective_level: int
    ceremony: int
    principal: Optional[str]
    reason: str
    audit_notes: Tuple[str, ...] = ()
    fail_safe: bool = False
    matched_rule: Optional[dict] = None

    @property
    def permitted(self) -> bool:
        return self.effect in (PERMIT, REQUIRE_CEREMONY)

    def to_dict(self) -> dict:
        return {"effect": self.effect, "effective_level": self.effective_level,
                "ceremony": self.ceremony, "ceremony_name": L.CEREMONY_NAMES.get(self.ceremony, "?"),
                "principal": self.principal, "reason": self.reason,
                "audit_notes": list(self.audit_notes), "fail_safe": self.fail_safe,
                "matched_rule": self.matched_rule}

def _principal_from_chain(chain: Any) -> str:
    if chain is None:
        raise ValueError("no capability chain")

    agent = getattr(chain, "agent", None)
    if isinstance(agent, str) and agent:
        return agent

    deleg = getattr(chain, "delegation", None)
    if deleg is not None:
        agents = getattr(deleg, "agents", None)
        if agents:
            return agents[-1]
    agents = getattr(chain, "agents", None)
    if agents:
        return agents[-1]
    if isinstance(chain, (tuple, list)) and chain:
        last = chain[-1]
        if isinstance(last, str) and last:
            return last
    if isinstance(chain, str) and chain:
        return chain
    raise ValueError("could not extract a principal from the capability chain")

def _job_fields(job: Any) -> Tuple[str, str]:

    def g(k, d):
        if isinstance(job, dict):
            return job.get(k, d)
        return getattr(job, k, d)
    tt = g("task_type", "*") or "*"
    res = g("resource", "*") or "*"
    return str(tt), str(res)

def _fail_safe(requested_level, principal, reason: str) -> PolicyDecision:

    notes = (f"FAIL-SAFE: {reason}",)
    if L.is_level(requested_level):
        if requested_level <= FAIL_SAFE_MAX_LEVEL:
            eff = int(requested_level)
            return PolicyDecision(
                effect=PERMIT, effective_level=eff, ceremony=L.required_ceremony(eff),
                principal=principal, reason=f"fail-safe read-only permit ({reason})",
                audit_notes=notes, fail_safe=True)

        return PolicyDecision(
            effect=DENY, effective_level=FAIL_SAFE_MAX_LEVEL, ceremony=L.CEREMONY_NONE,
            principal=principal,
            reason=f"fail-safe deny of privileged level L{requested_level} ({reason})",
            audit_notes=notes + (
                f"privileged L{requested_level} denied; only read-only (<=L{FAIL_SAFE_MAX_LEVEL}) "
                f"is permitted under a broken/absent policy",),
            fail_safe=True)

    return PolicyDecision(
        effect=DENY, effective_level=L.MIN_LEVEL, ceremony=L.CEREMONY_NONE, principal=principal,
        reason=f"fail-safe deny (invalid requested level {requested_level!r}; {reason})",
        audit_notes=notes, fail_safe=True)

def decide(captoken_chain: Any, job: Any, requested_level: Any, signed_policy: Any, *,
           owner_pubkey: Optional[bytes] = None, now: Optional[float] = None,
           path: Optional[str] = None, config: Optional[dict] = None) -> PolicyDecision:

    try:
        principal = _principal_from_chain(captoken_chain)
    except Exception as e:
        return _fail_safe(requested_level, None, f"no principal: {e}")

    if not L.is_level(requested_level):
        return _fail_safe(requested_level, principal, "invalid requested autonomy level")

    try:
        policy = verify_signed_policy(signed_policy, owner_pubkey=owner_pubkey,
                                      path=path, config=config)
    except PolicyVerifyError as e:
        return _fail_safe(requested_level, principal, f"policy verify failed: {e}")
    except Exception as e:
        return _fail_safe(requested_level, principal, f"policy unusable: {e}")

    t = float(now) if now is not None else _time.time()
    if policy.effective_time > t:
        return _fail_safe(requested_level, principal,
                          f"policy not yet effective (effective_time {policy.effective_time} > now {int(t)})")

    task_type, resource = _job_fields(job)

    rule = select_rule(policy, principal, task_type, resource)
    if rule is None:

        return PolicyDecision(
            effect=DENY, effective_level=L.MIN_LEVEL, ceremony=L.CEREMONY_NONE, principal=principal,
            reason=f"no policy rule permits principal {principal!r} for "
                   f"task_type {task_type!r} on resource {resource!r} (default deny)",
            audit_notes=(f"policy v{policy.version}: no matching rule",))

    if rule.effect == EFFECT_DENY:
        return PolicyDecision(
            effect=DENY, effective_level=L.MIN_LEVEL, ceremony=L.CEREMONY_NONE, principal=principal,
            reason=f"denied by policy rule for principal {principal!r} / task_type {task_type!r}",
            audit_notes=(f"policy v{policy.version}: matched deny rule",),
            matched_rule=rule.to_dict())

    owner_cap = policy.owner_caps.get(principal, policy.owner_caps.get("*", _DEFAULT_OWNER_CAP))
    eff_level, clamp_notes = L.clamp_level(
        requested_level,
        [("owner-cap", owner_cap), ("rule-max-level", rule.max_level)],
    )

    level_ceremony = L.required_ceremony(eff_level)
    rule_ceremony = rule.ceremony if rule.effect == EFFECT_REQUIRE_CEREMONY else L.CEREMONY_NONE
    final_ceremony = L.strictest_ceremony(level_ceremony, rule_ceremony)

    notes = (f"policy v{policy.version}: matched {rule.effect} rule",) + clamp_notes
    if final_ceremony == L.CEREMONY_NONE:
        effect = PERMIT
        reason = (f"permit principal {principal!r} at L{eff_level} "
                  f"({L.LEVEL_NAMES[eff_level]}) for task_type {task_type!r}")
    else:
        effect = REQUIRE_CEREMONY
        reason = (f"permit principal {principal!r} at L{eff_level} "
                  f"({L.LEVEL_NAMES[eff_level]}) for task_type {task_type!r} "
                  f"UNDER {L.CEREMONY_NAMES[final_ceremony]} ceremony")

    return PolicyDecision(
        effect=effect, effective_level=eff_level, ceremony=final_ceremony,
        principal=principal, reason=reason, audit_notes=notes, matched_rule=rule.to_dict())

__all__ = ["PolicyDecision", "decide", "PERMIT", "DENY", "REQUIRE_CEREMONY", "FAIL_SAFE_MAX_LEVEL"]
