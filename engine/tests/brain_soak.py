#!/usr/bin/env python3

import os, sys, json, time, tempfile, argparse, importlib.util, random, threading, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

SOAK_CIDR = os.environ.get("PN_SOAK_CIDR", "192.0.2.0/24")
sys.path.insert(0, ROOT)
from importlib.machinery import SourceFileLoader

PY = sys.executable
FAKEWARM = os.path.join(ROOT, "tests", "fakewarm.py")

def _load(name, fname):
    loader = SourceFileLoader(name, os.path.join(ROOT, "tools", fname))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod

def _rss_kb():
    try:
        with open(f"/proc/{os.getpid()}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except Exception:
        pass
    return 0

def _open_fds():
    try:
        return len(os.listdir(f"/proc/{os.getpid()}/fd"))
    except Exception:
        return 0

def _child_procs():

    n = 0
    try:
        mypid = os.getpid()
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                with open(f"/proc/{d}/stat") as f:
                    ppid = int(f.read().split(") ", 1)[1].split()[1])
                if ppid == mypid:
                    n += 1
            except Exception:
                continue
    except Exception:
        pass
    return n

def _action_for(turn, rng):
    r = rng.random()
    if r < 0.45:
        return {"op": "submit", "task_type": "echo.test", "params": {"msg": f"work-{turn}"}}
    if r < 0.65:
        return {"op": "message", "to": "tg:owner", "text": f"status {turn}"}
    if r < 0.78:
        return {"op": "propose", "kind": "onboard", "task_type": "net.discover",
                "summary": f"onboard candidate {turn}", "params": {"cidr": SOAK_CIDR}}
    if r < 0.86:

        return {"op": "shell", "cmd": "rm -rf /"}
    return {"op": "sleep", "seconds": 1, "reason": f"idle {turn}"}

class SoakClock:

    def __init__(self, start=1_700_000_000.0, step=90.0):
        self.t = start
        self.step = step
    def __call__(self):
        return self.t
    def tick(self):
        self.t += self.step
        return self.t

def run_soak(turns, step, report_path, *, seed=1337, mem_drift_kb=120_000,
             fd_leak=64, proc_leak=8):
    rng = random.Random(seed)
    pnd = _load("pnd_soak", "pnd")
    pnbrain = _load("pnbrain_soak", "pn-brain")
    pnkeeper = _load("pnkeeper_soak", "pn-brainkeeper")
    from pnlib import db, brain as B, ctxpressure
    from pnlib.warmsession import PinnedReasoner, Saturated
    from pnlib import discoverrun

    workdir = tempfile.mkdtemp(prefix="pn_soak_")
    os.environ["PN_DATA_DIR"] = os.path.join(workdir, "data")
    os.environ["PN_DEVICE_DIR"] = os.path.join(workdir, "device")
    dbpath = os.path.join(workdir, "queue.db")
    cx = db.connect(dbpath)
    pnd.CX = cx
    pnd.LK = threading.Lock()
    brain_uid = (cx.execute("SELECT uid FROM principals WHERE name='brain'").fetchone() or {})["uid"]

    def pnd_fn(req):
        pnd.CX = cx
        r = dict(req); r.pop("principal", None); r.pop("uid", None)
        r["_peer_uid"] = brain_uid
        return pnd.handle(r)

    script_path = os.path.join(workdir, "script.json")
    with open(script_path, "w") as f:
        json.dump([json.dumps(_action_for(0, rng))], f)
    pin = PinnedReasoner("sonnet", f"{PY} {FAKEWARM}", {"PN_FAKEWARM_SCRIPT": script_path},
                         request_timeout=3, ping_timeout=2)
    pin.start()

    rt_turns = [t for t in range(21, turns) if t % 7 == 0]
    wedge_turns = set(rng.sample(rt_turns, k=min(len(rt_turns), max(1, turns // 200)))) if rt_turns else set()
    err_turns = set(rng.sample(range(20, turns), k=max(1, turns // 80)))
    state = {"wedges_seen": 0, "wedges_recovered": 0, "errors_injected": 0, "reason_calls": 0}

    def ask_fn(prompt, *, session=None):
        turn = bn.turns
        if turn in err_turns:
            state["errors_injected"] += 1
            raise RuntimeError("injected reasoning-face error (soak)")
        action = _action_for(turn, rng)

        if turn % 7 == 0:
            with open(script_path, "w") as f:
                json.dump([json.dumps(action)], f)
            if turn in wedge_turns:

                state["wedges_seen"] += 1
                pin.env["PN_FAKEWARM_WEDGE"] = "0"
                pin.rotate()
                r = pin.ask(prompt)
                if not r.get("ok"):
                    state["wedges_recovered"] += 1
                pin.env.pop("PN_FAKEWARM_WEDGE", None)
                pin.rotate()
                return json.dumps(action)
            state["reason_calls"] += 1
            r = pin.ask(prompt)
            return r.get("text") if r.get("ok") else json.dumps(action)
        return json.dumps(action)

    bn = pnbrain.Brain(cx, ask_fn, pnd_fn, principal="brain")

    meter = ctxpressure.build_meter("sonnet", budget=40_000)
    digests_seen = []

    def kill_fn(sess, *, digest=None):
        digests_seen.append(len((digest or "").encode()))
        pin.rotate(digest=digest)

    kp = pnkeeper.Keeper(cx, principal="brain", ping_fn=pin.health, kill_fn=kill_fn,
                         record_note=lambda p, d: None, meter=meter,
                         max_age_s=6 * 3600, max_tasks=25, stuck_s=3 * 3600)

    B.set_state(cx, "brain", "intents", ["keep the LAN observed", "drain the queue", "stay alive"])
    clk = SoakClock(step=step)
    B.install_timer(cx, "daily-discover", interval_s=24 * 3600,
                    action_json={"op": "submit", "task_type": "net.discover",
                                 "params": {"cidr": SOAK_CIDR}},
                    now=clk())

    rss0, fd0, proc0 = _rss_kb(), _open_fds(), _child_procs()
    t_wall0 = time.time()
    samples = {"rss": [], "fd": [], "proc": []}
    rotations = 0
    disposed = 0
    undisposed = 0
    timer_fires = 0
    discover_proposals = 0
    pin_spawns_start = pin.spawns

    for turn in range(turns):
        now = clk()

        try:
            out = bn.run_turn(now=now)
            disposed += 1
            timer_fires += len(out.get("fired_timers", []))
        except Exception as e:
            undisposed += 1
            sys.stderr.write(f"SOAK turn {turn} RAISED (must never happen): {e}\n")

        if turn % 250 == 50:
            rep = discoverrun.run_discovery(own_cidrs=[SOAK_CIDR], mode="mock",
                                            store_path=os.path.join(workdir, "device", "dev.db"))
            discover_proposals += len(bn.consume_discovery_report(rep))

        res = kp.maybe_rotate(now=now)
        if res.get("rotated"):
            rotations += 1

        clk.tick()
        if turn % 50 == 0:
            samples["rss"].append(_rss_kb())
            samples["fd"].append(_open_fds())
            samples["proc"].append(_child_procs())

    pin.retire()
    rss1, fd1, proc1 = _rss_kb(), _open_fds(), _child_procs()
    wall = time.time() - t_wall0

    sim_days = (turns * step) / 86400.0
    db_bytes = os.path.getsize(dbpath) if os.path.exists(dbpath) else 0
    n_jobs = cx.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
    digest = B.get_state(cx, "brain", "digest", default="")

    mem_drift = max(samples["rss"] or [rss1]) - rss0
    fd_growth = max(samples["fd"] or [fd1]) - fd0
    proc_growth = max(samples["proc"] or [proc1]) - proc0

    report = {
        "config": {"turns": turns, "step_s": step, "sim_days": round(sim_days, 1),
                   "seed": seed},
        "wall_s": round(wall, 1),
        "loop": {"disposed": disposed, "undisposed": undisposed,
                 "timer_fires": timer_fires, "discover_proposals": discover_proposals,
                 "brain_acted": bn.acted, "brain_rejected": bn.rejected},
        "rotation": {"keeper_rotations": rotations, "pin_rotations": pin.rotations,
                     "pin_spawns": pin.spawns - pin_spawns_start + 1,
                     "digests_capped_max_bytes": max(digests_seen or [0]),
                     "final_digest_bytes": len(digest.encode())},
        "wedge": {"injected": state["wedges_seen"], "recovered": state["wedges_recovered"],
                  "reason_round_trips": state["reason_calls"],
                  "errors_injected": state["errors_injected"]},
        "memory": {"rss0_kb": rss0, "rss1_kb": rss1, "peak_drift_kb": mem_drift,
                   "threshold_kb": mem_drift_kb},
        "leaks": {"fd0": fd0, "fd_peak_growth": fd_growth, "fd_threshold": fd_leak,
                  "proc0": proc0, "proc_peak_growth": proc_growth, "proc_threshold": proc_leak},
        "queue": {"jobs": n_jobs, "db_bytes": db_bytes},
    }

    verdicts = []
    def verdict(name, ok, detail):
        verdicts.append((name, ok, detail))
    verdict("every turn disposed (loop never raised into the daemon)", undisposed == 0,
            f"undisposed={undisposed}")
    verdict("all injected wedges recovered (watchdog; no forever-hang)",
            state["wedges_recovered"] == state["wedges_seen"] and state["wedges_seen"] > 0,
            f"recovered {state['wedges_recovered']}/{state['wedges_seen']}")
    verdict("memory drift under threshold (no unbounded leak)", mem_drift < mem_drift_kb,
            f"{mem_drift}kB < {mem_drift_kb}kB")
    verdict("no fd leak (warm sessions reaped on rotation)", fd_growth < fd_leak,
            f"peak fd growth {fd_growth} < {fd_leak}")
    verdict("no child-process leak (pinned backend reaped on rotation)", proc_growth < proc_leak,
            f"peak proc growth {proc_growth} < {proc_leak}")
    verdict("the keeper rotated the session many times over the weeks", rotations >= 2,
            f"{rotations} rotations over {sim_days:.0f} sim-days")
    verdict("the digest stayed byte-capped (bounded hot context, runs-for-weeks)",
            len(digest.encode()) <= 4200, f"final digest {len(digest.encode())}B <= 4200B")
    verdict("self-generated work fired (durable daily timer survived the weeks)", timer_fires >= 1,
            f"{timer_fires} timer fires")
    verdict("forbidden actions rejected as no-ops under load (defence in depth)",
            bn.rejected > 0, f"{bn.rejected} rejected")

    cx.close()
    subprocess.run(["rm", "-rf", workdir])

    all_ok = all(ok for _, ok, _ in verdicts)
    report["verdicts"] = [{"name": n, "ok": ok, "detail": d} for n, ok, d in verdicts]
    report["PASS"] = all_ok

    print("=== P5 multi-week SOAK report (FAKE pn-llmd; sandbox; nothing live touched) ===")
    print(f" sim horizon : {sim_days:.1f} days  ({turns} turns x {step}s, wall {wall:.1f}s)")
    print(f" loop        : disposed={disposed} undisposed={undisposed} "
          f"acted={bn.acted} rejected={bn.rejected} timer_fires={timer_fires} "
          f"discover_proposals={discover_proposals}")
    print(f" rotation    : keeper={rotations} pin={pin.rotations} "
          f"final_digest={len(digest.encode())}B")
    print(f" wedge       : injected={state['wedges_seen']} recovered={state['wedges_recovered']} "
          f"reason_round_trips={state['reason_calls']} errors_injected={state['errors_injected']}")
    print(f" memory      : rss {rss0}->{rss1} kB (peak drift {mem_drift} kB)")
    print(f" leaks       : fd +{fd_growth}  child-proc +{proc_growth}")
    print(f" queue       : jobs={n_jobs} db={db_bytes/1024:.0f}kB")
    print(" verdicts:")
    for n, ok, d in verdicts:
        print(f"   {'PASS' if ok else 'FAIL'}  {n}  [{d}]")
    print(f"\n=== SOAK {'PASS' if all_ok else 'FAIL'} ===")

    if report_path:
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)
        print(f" report written: {report_path}")
    return all_ok

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=None)
    ap.add_argument("--weeks", type=float, default=None)
    ap.add_argument("--step", type=float, default=90.0, help="simulated seconds per turn")
    ap.add_argument("--report", default=None)
    ap.add_argument("--fast", action="store_true", default=True)
    ap.add_argument("--long", dest="fast", action="store_false")
    a = ap.parse_args()
    if a.turns is None:
        if a.weeks is not None:
            a.turns = int((a.weeks * 7 * 86400) / a.step)
        else:
            a.turns = 1200

    if a.weeks is None and a.turns == 1200 and a.step == 90.0:
        a.step = (3 * 7 * 86400) / 1200
    ok = run_soak(a.turns, a.step, a.report)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
