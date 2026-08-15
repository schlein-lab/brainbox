
from __future__ import annotations

from typing import Iterable, Optional, Tuple

class AutonomyError(Exception):
    pass

L0_OBSERVE = 0
L1_READ = 1
L2_ASSIST = 2
L3_SUPERVISED = 3
L4_DELEGATED = 4
L5_FULL_AUTO = 5

LEVELS = (L0_OBSERVE, L1_READ, L2_ASSIST, L3_SUPERVISED, L4_DELEGATED, L5_FULL_AUTO)
MIN_LEVEL = L0_OBSERVE
MAX_LEVEL = L5_FULL_AUTO

LEVEL_NAMES = {
    L0_OBSERVE: "observe",
    L1_READ: "read",
    L2_ASSIST: "assist",
    L3_SUPERVISED: "supervised",
    L4_DELEGATED: "delegated",
    L5_FULL_AUTO: "full-auto",
}

AC_OBSERVE = "observe"
AC_DRY_RUN = "dry_run"
AC_READ = "read"
AC_PROPOSE = "propose"
AC_WRITE_REVERSIBLE = "write_reversible"
AC_EXECUTE_REVERSIBLE = "execute_reversible"
AC_WRITE_IRREVERSIBLE = "write_irreversible"
AC_EXTERNAL_EFFECT = "external_effect"
AC_PRIVILEGED = "privileged"

ALL_ACTION_CLASSES = (
    AC_OBSERVE, AC_DRY_RUN, AC_READ, AC_PROPOSE, AC_WRITE_REVERSIBLE,
    AC_EXECUTE_REVERSIBLE, AC_WRITE_IRREVERSIBLE, AC_EXTERNAL_EFFECT, AC_PRIVILEGED,
)

_LEVEL_DELTA = {
    L0_OBSERVE: (AC_OBSERVE, AC_DRY_RUN),
    L1_READ: (AC_READ, AC_PROPOSE),
    L2_ASSIST: (AC_WRITE_REVERSIBLE,),
    L3_SUPERVISED: (AC_EXECUTE_REVERSIBLE,),
    L4_DELEGATED: (AC_WRITE_IRREVERSIBLE, AC_EXTERNAL_EFFECT),
    L5_FULL_AUTO: (AC_PRIVILEGED,),
}

READ_ONLY_CLASSES = frozenset({AC_OBSERVE, AC_DRY_RUN, AC_READ, AC_PROPOSE})

FAIL_SAFE_MAX_LEVEL = L1_READ

CEREMONY_NONE = 0
CEREMONY_NOTIFY = 1
CEREMONY_APPROVE = 2
CEREMONY_ENVELOPE = 3

CEREMONY_STAGES = (CEREMONY_NONE, CEREMONY_NOTIFY, CEREMONY_APPROVE, CEREMONY_ENVELOPE)
CEREMONY_NAMES = {
    CEREMONY_NONE: "none",
    CEREMONY_NOTIFY: "notify",
    CEREMONY_APPROVE: "approve",
    CEREMONY_ENVELOPE: "envelope",
}

_LEVEL_CEREMONY = {
    L0_OBSERVE: CEREMONY_NONE,
    L1_READ: CEREMONY_NONE,
    L2_ASSIST: CEREMONY_NOTIFY,
    L3_SUPERVISED: CEREMONY_APPROVE,
    L4_DELEGATED: CEREMONY_APPROVE,
    L5_FULL_AUTO: CEREMONY_ENVELOPE,
}

def is_level(level) -> bool:
    return isinstance(level, int) and not isinstance(level, bool) and MIN_LEVEL <= level <= MAX_LEVEL

def require_level(level) -> int:
    if not is_level(level):
        raise AutonomyError(f"invalid autonomy level {level!r} (must be an int L0..L5)")
    return int(level)

def is_ceremony(stage) -> bool:
    return (isinstance(stage, int) and not isinstance(stage, bool)
            and CEREMONY_NONE <= stage <= CEREMONY_ENVELOPE)

def require_ceremony_stage(stage) -> int:
    if not is_ceremony(stage):
        raise AutonomyError(f"invalid ceremony stage {stage!r} (must be 0..3)")
    return int(stage)

def allowed_classes(level) -> frozenset:

    lvl = require_level(level)
    acc = set()
    for n in range(MIN_LEVEL, lvl + 1):
        acc.update(_LEVEL_DELTA[n])
    return frozenset(acc)

def added_classes(level) -> frozenset:

    return frozenset(_LEVEL_DELTA[require_level(level)])

def required_ceremony(level) -> int:

    return _LEVEL_CEREMONY[require_level(level)]

def permits(level, action_class: str) -> bool:

    if action_class not in ALL_ACTION_CLASSES:
        raise AutonomyError(f"unknown action class {action_class!r}")
    return action_class in allowed_classes(level)

def min_level_for(action_class: str) -> int:

    if action_class not in ALL_ACTION_CLASSES:
        raise AutonomyError(f"unknown action class {action_class!r}")
    for lvl in LEVELS:
        if action_class in _LEVEL_DELTA[lvl]:
            return lvl
    raise AutonomyError(f"action class {action_class!r} is on no level")

def strictest_ceremony(*stages: int) -> int:

    best = CEREMONY_NONE
    for s in stages:
        best = max(best, require_ceremony_stage(s))
    return best

def clamp_level(requested, caps: Iterable[Tuple[str, Optional[int]]]) -> Tuple[int, Tuple[str, ...]]:

    eff = require_level(requested)
    notes = []
    for name, val in caps:
        if val is None:
            continue
        v = require_level(val)
        if v < eff:
            notes.append(f"requested L{require_level(requested)} clamped to L{v} by {name}")
            eff = v
    return eff, tuple(notes)

__all__ = [
    "AutonomyError",
    "L0_OBSERVE", "L1_READ", "L2_ASSIST", "L3_SUPERVISED", "L4_DELEGATED", "L5_FULL_AUTO",
    "LEVELS", "MIN_LEVEL", "MAX_LEVEL", "LEVEL_NAMES",
    "AC_OBSERVE", "AC_DRY_RUN", "AC_READ", "AC_PROPOSE", "AC_WRITE_REVERSIBLE",
    "AC_EXECUTE_REVERSIBLE", "AC_WRITE_IRREVERSIBLE", "AC_EXTERNAL_EFFECT", "AC_PRIVILEGED",
    "ALL_ACTION_CLASSES", "READ_ONLY_CLASSES", "FAIL_SAFE_MAX_LEVEL",
    "CEREMONY_NONE", "CEREMONY_NOTIFY", "CEREMONY_APPROVE", "CEREMONY_ENVELOPE",
    "CEREMONY_STAGES", "CEREMONY_NAMES",
    "is_level", "require_level", "is_ceremony", "require_ceremony_stage",
    "allowed_classes", "added_classes", "required_ceremony", "permits", "min_level_for",
    "strictest_ceremony", "clamp_level",
]
