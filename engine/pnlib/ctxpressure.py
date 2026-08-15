
from __future__ import annotations
import json, time

MODEL_BUDGETS = {
    "haiku": 180_000,
    "sonnet": 180_000,
    "opus": 180_000,
    "fable": 180_000,
}
DEFAULT_BUDGET = 180_000

DEFAULT_HIGH_WATER = 0.75

DEFAULT_LOW_WATER = 0.55

def estimate_tokens(obj) -> int:

    if obj is None:
        return 0
    if isinstance(obj, str):
        s = obj
    else:
        try:
            s = json.dumps(obj, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            s = str(obj)
    return max(1, (len(s) + 3) // 4)

def estimate_pressure(parts: dict, *, budget=DEFAULT_BUDGET) -> dict:

    by_part = {k: estimate_tokens(v) for k, v in parts.items()}
    total = sum(by_part.values())
    budget = max(1, int(budget))
    return {"tokens": total, "budget": budget, "ratio": total / budget, "by_part": by_part}

class ContextMeter:

    def __init__(self, *, budget=DEFAULT_BUDGET, high_water=DEFAULT_HIGH_WATER,
                 low_water=DEFAULT_LOW_WATER, alpha=0.4, hard_ratio=0.95):
        self.budget = max(1, int(budget))
        self.high_water = high_water
        self.low_water = low_water
        self.alpha = alpha
        self.hard_ratio = hard_ratio
        self.ewma = 0.0
        self.tripped = False
        self.last = None

    def observe(self, parts: dict) -> dict:

        est = estimate_pressure(parts, budget=self.budget)
        ratio = est["ratio"]
        self.ewma = ratio if self.last is None else (self.alpha * ratio + (1 - self.alpha) * self.ewma)
        reason = None
        if ratio >= self.hard_ratio:
            self.tripped = True
            reason = "hard-over-budget"
        elif self.ewma >= self.high_water:
            if not self.tripped:
                reason = "high-water"
            self.tripped = True
        elif self.ewma < self.low_water:
            self.tripped = False
        self.last = est
        return {"pressure": self.tripped, "ratio": ratio, "ewma": self.ewma,
                "tokens": est["tokens"], "budget": self.budget, "reason": reason,
                "by_part": est["by_part"]}

    def signal(self) -> bool:

        return self.tripped

def build_meter(model="sonnet", **kw) -> ContextMeter:

    kw.setdefault("budget", MODEL_BUDGETS.get(model, DEFAULT_BUDGET))
    return ContextMeter(**kw)

def build_system_contract(*, task_type_allowlist, allowed_ops) -> str:

    return (
        "You are the REASONING face of portioneer's autonomous brain. You PROPOSE exactly one "
        "action; a deterministic daemon DISPOSES it. You never act, never touch a broker, never "
        "name a principal, never self-approve.\n"
        "Output EXACTLY ONE JSON object, no prose, no code fences. It must have a closed `op` in "
        + json.dumps(sorted(set(allowed_ops))) + ".\n"
        "Closed world: there is NO shell/raw/exec op and NO `cmd`/`argv` field — they do not exist. "
        "Submitting work uses `op:submit` with a `task_type` from the allowlist "
        + json.dumps(sorted(task_type_allowlist)) + " and typed `params`.\n"
        "To onboard a discovered device, OPEN AN EGRESS, or do anything irreversible, use "
        "`op:propose` (a human approves; you cannot). To do nothing, use `op:sleep`."
    )

def engineer_digest(*, principal, intents, recent_jobs, open_proposals=None,
                    max_jobs=20, max_proposals=10, max_bytes=4000, now=None) -> str:

    now = time.time() if now is None else now
    lines = [f"# brain hot-context digest — principal {principal} @ {int(now)}",
             "# (authoritative truth = queue + Record; this is a bounded summary only)"]
    lines.append("## standing intents")
    for it in (intents or [])[:20]:
        lines.append(f"- {it}")
    if not intents:
        lines.append("- (none)")
    lines.append("## recent jobs (id type -> state)")
    for j in (recent_jobs or [])[:max_jobs]:
        tt = j.get("task_type") or j.get("client_tag") or "?"
        lines.append(f"- {j.get('id')} {tt} -> {j.get('state')}")
    if open_proposals:
        lines.append("## open proposals awaiting human approval")
        for p in open_proposals[:max_proposals]:
            lines.append(f"- {p.get('kind','proposal')}: {p.get('summary','')}")
    digest = "\n".join(lines)
    if len(digest.encode()) > max_bytes:

        enc = digest.encode()[:max_bytes]
        digest = enc.decode("utf-8", "ignore").rsplit("\n", 1)[0] + "\n# …(truncated; see Record)"
    return digest
