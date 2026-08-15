#!/usr/bin/env python3

import os, sys, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pnlib import acct

_p = 0
_f = 0
def check(cond, msg):
    global _p, _f
    if cond:
        _p += 1; print(f"  PASS  {msg}")
    else:
        _f += 1; print(f"  FAIL  {msg}")

d = tempfile.mkdtemp(prefix="pn_acct_svc_")
db = os.path.join(d, "acct.db")

def seed(rows):
    cx = sqlite3.connect(db)
    cx.execute("DROP TABLE IF EXISTS type_ewma")
    cx.execute("CREATE TABLE type_ewma (task_type TEXT, n INTEGER, mem REAL, "
               "cpu_weight REAL, llm_weight REAL, svc_s REAL, updated_at REAL)")
    cx.executemany("INSERT INTO type_ewma (task_type,n,svc_s) VALUES (?,?,?)", rows)
    cx.commit(); cx.close()

r = acct.AcctReader(path=db)

seed([("a", 10, 5.0), ("b", 30, 10.0)])
g = r.global_service_time()
check(g is not None and abs(g - 8.75) < 1e-6, f"n-weighted mean == 8.75 (got {g})")

seed([("(raw)", 514, 9.43)])
g = r.global_service_time()
check(g is not None and abs(g - 9.43) < 1e-6, f"single bucket == its svc (got {g})")

seed([("x", 0, 5.0), ("y", 3, None), ("z", 2, -1.0)])
check(r.global_service_time() is None, "all-excluded rows -> None")

check(acct.AcctReader(path=os.path.join(d, "nope.db")).global_service_time() is None,
      "absent store -> None")

print(f"=== {_p} passed, {_f} failed ===")
sys.exit(0 if _f == 0 else 1)
