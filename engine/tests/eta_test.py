#!/usr/bin/env python3

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pnlib import eta

_p = 0
_f = 0
def check(cond, msg):
    global _p, _f
    if cond:
        _p += 1; print(f"  PASS  {msg}")
    else:
        _f += 1; print(f"  FAIL  {msg}")
def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol

r = eta.project_eta(1, 100.0, 2)
check(r["ahead"] == 0 and approx(r["eta_start_s"], 0.0), f"pos1 -> 0 wait ({r})")
check(approx(r["eta_done_s"], 100.0), f"pos1 -> done == own svc ({r})")

r = eta.project_eta(5, 100.0, 2)
check(r["ahead"] == 4 and approx(r["eta_start_s"], 200.0), f"pos5/mc2/svc100 -> start 200 ({r})")
check(approx(r["eta_done_s"], 300.0), f"pos5 -> done 300 ({r})")

r = eta.project_eta(3, 60.0, 0)
check(approx(r["eta_start_s"], 120.0), f"mc=0 guarded -> start 120 ({r})")

r = eta.project_eta(3, 100.0, 2, own_svc_s=10.0)
check(approx(r["eta_start_s"], 100.0) and approx(r["eta_done_s"], 110.0), f"own override ({r})")

r = eta.project_eta(4, None, 2)
check(approx(r["eta_start_s"], 0.0) and approx(r["eta_done_s"], 0.0), f"None svc -> 0 ({r})")
check(approx(eta.project_eta(4, -50.0, 2)["eta_start_s"], 0.0), "negative svc -> 0")

r = eta.project_eta("garbage", 60.0, 2)
check(r["position"] == 1 and r["ahead"] == 0, f"bad position -> pos1 ({r})")
check(eta.project_eta(-3, 60.0, 2)["position"] == 1, "position<1 -> 1")

check(eta.waiting_on(1, "queued") == "dispatching", "pos1 queued -> dispatching")
check(eta.waiting_on(5, "queued") == "position", "pos5 queued -> position")
check(eta.waiting_on(3, "running") == "running", "running -> running")
check(eta.waiting_on(3, "done") == "done", "done -> done")

check(eta.human(45) == "~45s", f"human 45 -> {eta.human(45)}")
check(eta.human(180) == "~3m", f"human 180 -> {eta.human(180)}")
check(eta.human(3600) == "~1h", f"human 3600 -> {eta.human(3600)}")
check(eta.human(4800) == "~1h20m", f"human 4800 -> {eta.human(4800)}")
check(eta.human(90000) == ">1d", f"human 90000 -> {eta.human(90000)}")
check(eta.human(None) == "?" and eta.human(-5) == "?", "human None/neg -> ?")

print(f"=== {_p} passed, {_f} failed ===")
sys.exit(0 if _f == 0 else 1)
