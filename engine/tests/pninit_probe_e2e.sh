#!/bin/bash
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 3
TMP="$(mktemp -d /tmp/pn_e2e_XXXXXX)"
RT="$TMP/rt"; DATA="$TMP/data"; mkdir -p "$RT" "$DATA"
SOCK="$RT/pnd.sock"

cc -O2 -o "$TMP/pninit_probe" "$ROOT/tests/pninit_probe.c" || { echo "COMPILE FAIL"; exit 3; }

cat > "$TMP/boot.py" <<PY
import sys, runpy
sys.path.insert(0, "$ROOT")
from pnlib import sched, db, DB_PATH
_orig = sched.Config.autoscale
def _permissive():
    c=_orig(); c.psi_stop=1e9; c.mem_floor=1; c.batch_high=1<<30; c.slack=0; return c
sched.Config.autoscale = staticmethod(_permissive)
cx = db.connect(DB_PATH); cx.commit()
runpy.run_path("$ROOT/tools/pnd", run_name="__main__")
PY

XDG_RUNTIME_DIR="$RT" XDG_DATA_HOME="$DATA" PN_DURABILITY=normal \
  python3 "$TMP/boot.py" >"$TMP/pnd.out" 2>"$TMP/pnd.err" &
PND=$!
trap 'kill $PND 2>/dev/null; rm -rf "$TMP"' EXIT

for i in $(seq 1 100); do [ -S "$SOCK" ] && break; sleep 0.1; done
if [ ! -S "$SOCK" ]; then
  echo "PND DID NOT COME UP; stderr tail:"; tail -20 "$TMP/pnd.err"; exit 3
fi
echo "real pnd up on $SOCK (pid $PND)"

echo -n "JSON ping over real pnd: "
python3 -c "
import sys; sys.path.insert(0,'$ROOT')
from pnlib import ipc
print(ipc.send_request({'verb':'ping'}, path='$SOCK'))
"

echo "--- pn-init's VERBATIM probe() against the real pnd (no adapter) ---"
"$TMP/pninit_probe" "$SOCK"
RC=$?
echo "probe exit code: $RC (0 = both L1 and L2 healthy)"
exit $RC
