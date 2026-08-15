
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pnlib import fleetpick as F

NOW = 1_800_000_000.0
PASS, FAIL = [], []
def chk(n, c, d=""):
    (PASS if c else FAIL).append(n); print(("  ok " if c else "FAIL ")+n+(("  — "+d) if d else ""))

def node(load1, nproc=4, kinds=("exec","cell"), state="online", age=10.0, ep="http://x:8099", arch=None):
    r = {"state": state, "endpoint": ep, "last_seen": NOW-age,
         "facts": {"health": {"load1": load1, "nproc": nproc}}}
    if arch is not None:
        r["facts"]["health"]["arch"] = arch
    if kinds is not None:
        r["caps"] = {"kinds": list(kinds)}
    if arch is not None:
        r.setdefault("caps", {})["arch"] = arch
    return r

W = {"pi1": node(0.05), "pi2": node(0.09, kinds=None), "musik": node(0.17)}
r = F.pick_compute_node(W, "exec", box_load1=5.0, box_nproc=6, reserved_cores=1, now=NOW)
chk("Box(5/5eff) voll -> least-loaded Node (pi1)", r == "pi1", "got %r" % r)

r = F.pick_compute_node({"pi1": node(0.5)}, "exec", box_load1=0.1, box_nproc=6, reserved_cores=1, now=NOW)

chk("Box idler als Node -> Box (None)", r is None, "got %r" % r)

W = {"pi1": node(0.0), "pi2": node(0.0, kinds=None)}
inflight = {}
counts = {}
for _ in range(6):
    pick = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, reserved_cores=1,
                               inflight=inflight, now=NOW)
    counts[pick] = counts.get(pick, 0) + 1
    inflight[pick] = inflight.get(pick, 0) + 1
chk("Burst verteilt sich auf beide Nodes (3/3)", counts.get("pi1") == 3 and counts.get("pi2") == 3,
    "counts=%s" % counts)

W = {"cellonly": node(0.0, kinds=("cell",)), "pi1": node(0.5)}
r = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, now=NOW)
chk("kind='exec' meidet cell-only-Node", r == "pi1", "got %r" % r)

W = {"pi2": node(0.0, kinds=None)}
r = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, now=NOW)
chk("Node ohne caps.kinds ist erlaubt", r == "pi2", "got %r" % r)

W = {"stale": node(0.0, age=999.0)}
r = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, now=NOW, max_health_age_s=180)
chk("veralteter Node -> Box (None)", r is None, "got %r" % r)

W = {"off": node(0.0, state="offline")}
r = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, now=NOW)
chk("offline-Node -> Box (None)", r is None, "got %r" % r)

W = {"nohealth": {"state": "online", "endpoint": "http://x", "last_seen": NOW, "caps": {"kinds": ["exec"]}, "facts": {}}}
r = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, now=NOW)
chk("Node ohne health-load -> Box (None)", r is None, "got %r" % r)

W = {"pi1": node(0.7)}
r0 = F.pick_compute_node(W, "exec", box_load1=2.0, box_nproc=4, reserved_cores=0, now=NOW)
r2 = F.pick_compute_node(W, "exec", box_load1=2.0, box_nproc=4, reserved_cores=2, now=NOW)

r0b = F.pick_compute_node({"pi1": node(0.9)}, "exec", box_load1=0.9, box_nproc=4, reserved_cores=0, now=NOW)
r2b = F.pick_compute_node({"pi1": node(0.9)}, "exec", box_load1=0.9, box_nproc=4, reserved_cores=3, now=NOW)

chk("Reserve kippt Box->Node bei Gleichlast", r0b is None and r2b == "pi1", "r0b=%r r2b=%r" % (r0b, r2b))

chk("leerer Fleet -> Box (None)", F.pick_compute_node({}, "exec", 6.0, 6, now=NOW) is None)

W = {"arm": node(0.0, arch="aarch64"), "x86": node(0.5, arch="x86_64")}
r = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, now=NOW, box_arch="x86_64")
chk("Arch-Gate: x86-Box meidet aarch64-Node", r == "x86", "got %r" % r)

W = {"arm": node(0.0, arch="aarch64")}
r = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, now=NOW, box_arch="x86_64")
chk("Arch-Gate: nur fremd-arch -> Box (None)", r is None, "got %r" % r)

W = {"arm": node(0.0, arch="aarch64")}
r = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, now=NOW, box_arch="x86_64", arch_ok=True)
chk("arch_ok=True erlaubt cross-arch-Node", r == "arm", "got %r" % r)

W = {"arm": node(0.0, arch="aarch64")}
r = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, now=NOW)
chk("kein box_arch -> Gate inaktiv (alte Semantik)", r == "arm", "got %r" % r)

W = {"noarch": node(0.0)}
r = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, now=NOW, box_arch="x86_64")
chk("Node ohne arch + aktives Gate -> Box (None)", r is None, "got %r" % r)

W = {"drain": node(0.0), "x86": node(0.9)}
W["drain"]["facts"]["health"].update({"draining": True, "node_active": False, "mode": "off"})
r = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, now=NOW)
chk("drainierender Node wird gemieden", r == "x86", "got %r" % r)

W = {"drain": node(0.0)}
W["drain"]["facts"]["health"]["draining"] = True
r = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, now=NOW)
chk("nur drainierender Node -> Box (None)", r is None, "got %r" % r)

W = {"off": node(0.0)}
W["off"]["facts"]["health"]["mode"] = "off"
r = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, now=NOW)
chk("mode=off -> Box (None)", r is None, "got %r" % r)

W = {"alt": node(0.0)}
r = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, now=NOW)
chk("Node ohne drain-Felder bleibt zulaessig", r == "alt", "got %r" % r)

def node_ram(load1, avail_mb, total_mb, **kw):
    r = node(load1, **kw)
    r["facts"]["health"]["mem_avail_mb"] = avail_mb
    r["facts"]["health"]["mem_total_mb"] = total_mb
    return r

W = {"ramarm": node_ram(0.0, 800, 16000), "fleissig": node_ram(2.0, 12800, 16000)}
r = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, now=NOW)
chk("RAM-armer Node verliert trotz wenig Load", r == "fleissig", "got %r" % r)

W = {"alt": node(0.4), "neu": node_ram(0.8, 15000, 16000)}

r = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, now=NOW)
chk("Node ohne RAM-Felder bleibt CPU-only vergleichbar", r == "alt", "got %r" % r)

os.environ["PN_FLEETPICK_RAM_WEIGHT"] = "0"
W = {"ramarm": node_ram(0.0, 800, 16000), "fleissig": node_ram(2.0, 12800, 16000)}
r = F.pick_compute_node(W, "exec", box_load1=6.0, box_nproc=6, now=NOW)
chk("RAM-Gewicht 0 -> alte CPU-only-Wahl", r == "ramarm", "got %r" % r)
del os.environ["PN_FLEETPICK_RAM_WEIGHT"]

W = {"pi1": node_ram(0.8, 12000, 16000)}
r = F.pick_compute_node(W, "exec", box_load1=0.1, box_nproc=6, reserved_cores=1, now=NOW,
                        box_mem_avail_mb=250, box_mem_total_mb=6000)
chk("RAM-knappe Box gibt ab trotz idle CPU", r == "pi1", "got %r" % r)

W = {"pi1": node_ram(0.9, 12000, 16000)}
r = F.pick_compute_node(W, "exec", box_load1=0.1, box_nproc=6, reserved_cores=1, now=NOW)

chk("ohne box_mem bleibt Box CPU-only", r is None, "got %r" % r)

print("\n== %d PASS, %d FAIL ==" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
