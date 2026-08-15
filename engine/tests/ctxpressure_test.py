#!/usr/bin/env python3

import os, sys, tempfile, importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from pnlib import ctxpressure as C, brain as B, db
from importlib.machinery import SourceFileLoader

PASS = FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {label}")
    else:
        FAIL += 1; print(f"  FAIL  {label}")

def _load(name, fname):
    loader = SourceFileLoader(name, os.path.join(ROOT, "tools", fname))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod

def test_estimate():
    print("[a] estimate_tokens monotonic + deterministic; estimate_pressure splits by part")
    a = C.estimate_tokens("x" * 400)
    b = C.estimate_tokens("x" * 4000)
    check(b > a and C.estimate_tokens("x" * 400) == a, "monotonic + deterministic token estimate")
    est = C.estimate_pressure({"digest": "x" * 4000, "intents": ["i"]}, budget=10_000)
    check(est["tokens"] == sum(est["by_part"].values()), "tokens == sum of per-part estimates")
    check(0 < est["ratio"] < 1, "ratio is fraction of budget")

def test_small_vs_large():
    print("[b] small context = no pressure; large context = pressure (high-water crossed)")
    m = C.ContextMeter(budget=10_000, high_water=0.75, low_water=0.55, alpha=1.0)
    v = m.observe({"digest": "tiny"})
    check(v["pressure"] is False, "a small context is NOT under pressure")
    v2 = m.observe({"digest": "x" * 40_000})
    check(v2["pressure"] is True and v2["reason"] in ("high-water", "hard-over-budget"),
          "a large context trips context-pressure")

def test_hysteresis():
    print("[c] hysteresis: stays tripped until below low-water (no thrash around the line)")
    m = C.ContextMeter(budget=10_000, high_water=0.75, low_water=0.55, alpha=1.0)
    m.observe({"digest": "x" * 40_000})
    check(m.signal() is True, "tripped at high-water")
    v = m.observe({"digest": "x" * 28_000})
    check(v["pressure"] is True, "between low and high water it STAYS tripped (hysteresis)")
    v2 = m.observe({"digest": "x" * 8_000})
    check(v2["pressure"] is False, "below low-water pressure is relieved")

def test_hard_over():
    print("[d] hard-over-budget trips instantly regardless of smoothing")
    m = C.ContextMeter(budget=10_000, high_water=0.75, alpha=0.1, hard_ratio=0.95)
    v = m.observe({"digest": "x" * 80_000})
    check(v["pressure"] is True and v["reason"] == "hard-over-budget",
          "a single over-budget turn trips immediately (no EWMA wait)")

def test_system_contract():
    print("[e] build_system_contract states the CLOSED world")
    s = C.build_system_contract(task_type_allowlist=B.DEFAULT_TASK_TYPE_ALLOWLIST,
                                allowed_ops=B.ALLOWED_OPS)
    check("EXACTLY ONE JSON" in s, "demands exactly one JSON action")
    check("NO shell/raw/exec" in s or "no shell/raw" in s.lower(), "forbids shell/raw/exec")
    check("net.discover" in s and "summary.notify" in s, "names the task-type allowlist")
    check("propose" in s and ("cannot" in s.lower() or "human approves" in s.lower()),
          "states propose is human-gated (the brain cannot self-approve)")

def test_engineer_digest():
    print("[f] engineer_digest is structured + bounded (caps + a HARD byte cap)")
    jobs = [{"id": i, "task_type": "echo.test", "state": "done"} for i in range(100)]
    props = [{"kind": "onboard", "summary": f"dev {i}"} for i in range(50)]
    d = C.engineer_digest(principal="brain", intents=["keep the lights on"],
                          recent_jobs=jobs, open_proposals=props,
                          max_jobs=20, max_proposals=10, max_bytes=4000)
    check("standing intents" in d and "recent jobs" in d, "structured sections present")
    check(d.count("echo.test") <= 20, "recent jobs capped at max_jobs")
    check(len(d.encode()) <= 4000 + 80, "digest is HARD byte-capped (bounded hot context)")
    check("truncated" in d.lower() or len(d.encode()) <= 4000, "elision noted when truncated")

def test_keeper_integration():
    print("[g] keeper: a real ContextMeter fed a large context fires 'context-pressure' rotation")
    pnkeeper = _load("pnkeeper_ctx", "pn-brainkeeper")
    path = tempfile.mktemp(prefix="ctx_keeper_", suffix=".db")
    cx = db.connect(path)
    try:

        B.new_session(cx, "brain")
        B.set_state(cx, "brain", "digest", "x" * 200_000)

        meter = C.ContextMeter(budget=2_000, high_water=0.75, alpha=1.0)
        kp = pnkeeper.Keeper(cx, principal="brain", meter=meter,
                             max_age_s=10**9, max_tasks=10**9, stuck_s=10**9)
        info = kp.measure_pressure()
        check(info["pressure"] is True, "measure_pressure trips on a fat live context")
        res = kp.maybe_rotate()
        check(res["rotated"] is True and res["reason"] == "context-pressure",
              "maybe_rotate fired 'context-pressure' from the REAL measured signal (wired)")
    finally:
        cx.close()
        for p in (path, path + "-wal", path + "-shm"):
            try:
                os.unlink(p)
            except OSError:
                pass

def main():
    print("=== P5 context-pressure signal + prompt/digest engineering ===")
    test_estimate()
    test_small_vs_large()
    test_hysteresis()
    test_hard_over()
    test_system_contract()
    test_engineer_digest()
    test_keeper_integration()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
