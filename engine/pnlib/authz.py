

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional

from . import db

__all__ = ["Decision", "decide", "AuthzDenied", "cap_enforce_enabled", "policy_mode",
           "ledger_enabled"]

_TRUTHY = frozenset({"1", "true", "yes", "on"})

def cap_enforce_enabled(env: Mapping[str, str] | None = None) -> bool:

    env = os.environ if env is None else env
    return env.get("PN_CAP_ENFORCE", "off").strip().lower() in _TRUTHY

def policy_mode(env: Mapping[str, str] | None = None) -> str:

    env = os.environ if env is None else env
    m = env.get("PN_POLICY_MODE", "off").strip().lower()
    return m if m in ("off", "observe", "enforce") else "off"

def ledger_enabled(env: Mapping[str, str] | None = None) -> bool:

    env = os.environ if env is None else env
    return env.get("PN_AUTHZ_LEDGER", "off").strip().lower() in _TRUTHY

@dataclass
class Decision:

    allowed: bool
    reason: str = ""
    principal: Optional[str] = None
    task_type: Optional[str] = None
    is_raw: bool = False

    cap_enforced: bool = False
    policy_mode: str = "off"
    ledger_recorded: bool = False
    diagnostics: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.allowed

class AuthzDenied(Exception):

    def __init__(self, decision: "Decision"):
        super().__init__(decision.reason)
        self.decision = decision

def _v3_grant_verdict(caps: set, task_type: Optional[str], cmd: Any,
                      *, principal: Optional[str], cx=None) -> Decision:
    who = principal or "unknown"
    if task_type:
        allowed = (f"task_type:{task_type}" in caps
                   or "task_type:*" in caps
                   or "task.raw" in caps)
        if not allowed:
            return Decision(False, f"principal '{who}' may not run task_type '{task_type}'",
                            principal=principal, task_type=task_type)

        if cx is not None:
            if not db.get_task_type(cx, task_type):
                return Decision(False, f"unknown task_type '{task_type}'",
                                principal=principal, task_type=task_type)
        return Decision(True, "", principal=principal, task_type=task_type)

    if cmd is None:
        return Decision(False, "submit requires either 'task_type' or a raw 'cmd'",
                        principal=principal, task_type=None, is_raw=True)
    if "task.raw" not in caps:
        return Decision(False,
                        f"principal '{who}' may not submit raw commands "
                        f"(needs capability task.raw)",
                        principal=principal, task_type=None, is_raw=True)
    return Decision(True, "", principal=principal, task_type=None, is_raw=True)

def _captoken_verify(captoken, verify: Optional[Callable], caps: set, d: Decision) -> None:

    d.cap_enforced = True
    if verify is None:

        d.allowed = False
        d.reason = "capability-token enforcement is on but no verifier is configured"
        d.diagnostics["captoken"] = "no-verifier"
        return
    ok = bool(verify(captoken, caps))
    d.diagnostics["captoken"] = "ok" if ok else "bad"
    if not ok:
        d.allowed = False
        d.reason = "capability-token verification failed"

def _policy_decide(d: Decision, policy_fn: Optional[Callable], mode: str,
                   ctx: Mapping) -> None:

    d.policy_mode = mode
    if policy_fn is None:
        d.diagnostics["policy"] = "no-policy"
        return
    verdict = policy_fn(dict(ctx))
    if isinstance(verdict, tuple):
        p_allowed, p_reason = bool(verdict[0]), (verdict[1] if len(verdict) > 1 else "")
    else:
        p_allowed, p_reason = bool(verdict), ""
    d.diagnostics["policy"] = "allow" if p_allowed else "deny"
    if mode == "enforce" and d.allowed and not p_allowed:
        d.allowed = False
        d.reason = p_reason or "denied by policy"

def _ledger_record(sink: Callable, d: Decision, ctx: Mapping) -> None:

    sink({"principal": d.principal, "task_type": d.task_type, "is_raw": d.is_raw,
          "allowed": d.allowed, "reason": d.reason, "ctx": dict(ctx)})
    d.ledger_recorded = True

def _normalize(context, caps, task_type, cmd, principal, cx):

    if context is not None:
        get = context.get if isinstance(context, Mapping) else (lambda k, d=None: getattr(context, k, d))
        if caps is None:
            caps = get("caps")
        if task_type is None:
            task_type = get("task_type")
        if cmd is None:
            cmd = get("cmd", None) if isinstance(context, Mapping) else get("cmd")
            if cmd is None and isinstance(context, Mapping):
                cmd = context.get("argv")
        if principal is None:
            principal = get("principal")
        if cx is None:
            cx = get("cx")
    caps = set(caps) if caps is not None else set()
    return caps, task_type, cmd, principal, cx

def decide(context: Any = None, *, caps: Optional[Iterable[str]] = None,
           task_type: Optional[str] = None, cmd: Any = None,
           principal: Optional[str] = None, cx=None,
           captoken: Any = None, captoken_verify: Optional[Callable] = None,
           policy_fn: Optional[Callable] = None, ledger_sink: Optional[Callable] = None,
           env: Mapping[str, str] | None = None) -> Decision:

    caps, task_type, cmd, principal, cx = _normalize(context, caps, task_type, cmd, principal, cx)
    ctx = {"principal": principal, "task_type": task_type, "is_raw": task_type is None,
           "caps": sorted(caps)}

    verified_early_deny = None
    if cap_enforce_enabled(env):
        probe = Decision(True, principal=principal, task_type=task_type, is_raw=task_type is None)
        _captoken_verify(captoken, captoken_verify, caps, probe)
        if not probe.allowed:
            verified_early_deny = probe

    if verified_early_deny is not None:
        d = verified_early_deny
    else:
        d = _v3_grant_verdict(caps, task_type, cmd, principal=principal, cx=cx)
        d.cap_enforced = cap_enforce_enabled(env)

    pm = policy_mode(env)
    if pm != "off":
        _policy_decide(d, policy_fn, pm, ctx)
    else:
        d.policy_mode = "off"

    if ledger_enabled(env) and ledger_sink is not None:
        _ledger_record(ledger_sink, d, ctx)

    return d
