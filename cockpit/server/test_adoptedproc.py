#!/usr/bin/env python3

import sys, os, time, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pn_cell_session as pcs

p = subprocess.Popen(["sleep", "60"])
st = pcs._read_proc_stat(p.pid)
assert st is not None, "read_proc_stat None for a live pid"
state, starttime = st

ap = pcs._AdoptedProc(p.pid, starttime)
live_poll_none = ap.poll() is None

ap_wrong = pcs._AdoptedProc(p.pid, str(int(starttime) + 987654))
wrong_poll_dead = ap_wrong.poll() is not None
ap_wrong.kill()
ap_wrong.terminate()
time.sleep(0.3)
real_survived_wrong_kill = (p.poll() is None)

correct_poll_alive = ap.poll() is None
ap.kill()
try: p.wait(timeout=3)
except Exception: pass
time.sleep(0.3)
correct_poll_dead = ap.poll() is not None

ap_ghost = pcs._AdoptedProc(2147480000, "1")
ghost_dead = ap_ghost.poll() is not None
ap_ghost.kill()

ok = (live_poll_none and wrong_poll_dead and real_survived_wrong_kill
      and correct_poll_alive and correct_poll_dead and ghost_dead)
print("LIVE_POLL_NONE=%s" % live_poll_none)
print("WRONG_STARTTIME_POLL_DEAD=%s  WRONG_KILL_NOOP(real survived)=%s  <-- D1 reuse-immunity" % (wrong_poll_dead, real_survived_wrong_kill))
print("CORRECT_POLL_ALIVE=%s  CORRECT_POLL_DEAD_AFTER_KILL=%s" % (correct_poll_alive, correct_poll_dead))
print("GHOST_PID_POLL_DEAD=%s" % ghost_dead)
try: p.kill()
except Exception: pass
print("ADOPTEDPROC_UNIT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
