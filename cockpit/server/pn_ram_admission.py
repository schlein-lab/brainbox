
import os, json, time, fcntl, errno

DATA = os.environ.get("PHANTOM_PORTAL_DATA",
                      os.path.expanduser("~/.local/share/brainbox-portal"))
LEDGER = os.path.join(DATA, "ram_admission.json")
LOCK = os.path.join(DATA, "ram_admission.lock")

CELL_RUN_DIR = os.environ.get("PN_CELL_RUN_DIR", "/tmp/pn-cells")

SCREEN_MB = int(os.environ.get("PN_RAM_SCREEN_MB", "2048"))
SESSION_MB = int(os.environ.get("PN_RAM_SESSION_MB", "1536"))

OFFICE_MB = int(os.environ.get("PN_RAM_OFFICE_MB", "4096"))

LIVE_MARGIN_MB = int(os.environ.get("PN_RAM_LIVE_MARGIN_MB", "768"))

OVERCOMMIT = os.environ.get("PN_RAM_OVERCOMMIT", "1") not in ("0", "false", "no", "")
OVERCOMMIT_FLOOR_MB = int(os.environ.get("PN_RAM_OVERCOMMIT_FLOOR_MB", "2048"))

def _meminfo():

    total = avail = 0
    try:
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemTotal:"):
                    total = int(ln.split()[1]) // 1024
                elif ln.startswith("MemAvailable:"):
                    avail = int(ln.split()[1]) // 1024
                if total and avail:
                    break
    except OSError:
        pass
    return total, avail

def host_reserve_mb(total):

    env = os.environ.get("PN_RAM_HOST_RESERVE_MB")
    if env:
        try:
            return max(0, int(env))
        except ValueError:
            pass
    return max(2560, int(total * 0.18))

def default_mem_for(kind):
    if kind == "office":
        return OFFICE_MB
    return SCREEN_MB if kind in ("screen", "gui", "x11") else SESSION_MB

def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except OSError as e:
        return e.errno == errno.EPERM
    except (TypeError, ValueError):
        return False

class _Locked:

    def __init__(self):
        self.fd = None

    def __enter__(self):
        os.makedirs(DATA, exist_ok=True)
        self.fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None

def _load():
    try:
        with open(LEDGER) as f:
            data = json.load(f)
        recs = data.get("reservations", [])
        return recs if isinstance(recs, list) else []
    except (OSError, ValueError):
        return []

def _save(recs):
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"reservations": recs, "updated": time.time()}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, LEDGER)

def _session_cell_dead(rec):

    rid = rec.get("id") or ""
    if not rid.startswith("sess:"):
        return False
    label = rec.get("label")
    if not label:
        return False
    return not os.path.isdir(os.path.join(CELL_RUN_DIR, str(label)))

def _reap(recs):

    live = [r for r in recs
            if pid_alive(r.get("pid")) and not _session_cell_dead(r)]
    return live, (len(live) != len(recs))

def _committed(recs):
    return sum(int(r.get("mem_mb") or 0) for r in recs)

def plan(want_mem_mb, kind, exclude_id=None):

    want = int(want_mem_mb or default_mem_for(kind))
    total, avail = _meminfo()
    reserve = host_reserve_mb(total)
    budget = max(0, total - reserve)
    with _Locked():
        recs = _load()
        recs, changed = _reap(recs)
        if changed:
            _save(recs)
    if exclude_id is not None:
        recs = [r for r in recs if r.get("id") != exclude_id]
    committed = _committed(recs)
    would = committed + want
    fits_budget = would <= budget
    fits_live = (want + LIVE_MARGIN_MB) <= avail

    fits_overcommit = OVERCOMMIT and ((want + OVERCOMMIT_FLOOR_MB) <= avail)
    grant = (fits_budget and fits_live) or fits_overcommit
    running = sorted(
        ({"id": r.get("id"), "kind": r.get("kind"), "mem_mb": int(r.get("mem_mb") or 0),
          "owner": r.get("owner"), "session": r.get("session"), "label": r.get("label"),
          "pid": r.get("pid"), "ctl_pid": r.get("ctl_pid"), "ts": r.get("ts")} for r in recs),
        key=lambda x: (-x["mem_mb"], x.get("ts") or 0))
    if grant and fits_budget:
        reason = "OK — %d MiB, danach %d/%d MiB Gast-RAM belegt." % (want, would, budget)
    elif grant:
        reason = ("OK (Overcommit) — %d MiB; worst-case-Reservierung %d/%d MiB voll, aber real "
                  "%d MiB frei (Floor %d MiB)." % (want, would, budget, avail, OVERCOMMIT_FLOOR_MB))
    elif OVERCOMMIT:
        reason = ("Zu wenig freier Speicher aktuell (%d MiB frei, %d+%d MiB nötig). "
                  "Bitte eine VM stoppen oder kurz warten." % (avail, want, OVERCOMMIT_FLOOR_MB))
    elif not fits_budget:
        reason = ("RAM-Budget erschöpft: %d/%d MiB durch %d laufende VM(s) belegt, %d MiB angefragt. "
                  "Bitte zuerst eine VM herunterfahren." % (committed, budget, len(recs), want))
    else:
        reason = ("Zu wenig freier Speicher aktuell (%d MiB frei, %d+%d MiB nötig). "
                  "Bitte eine VM stoppen oder kurz warten." % (avail, want, LIVE_MARGIN_MB))
    return {
        "grant": grant, "reason": reason, "want_mb": want, "kind": kind,
        "total_mb": total, "avail_mb": avail, "host_reserve_mb": reserve, "budget_mb": budget,
        "committed_mb": committed, "would_use_mb": would,
        "free_budget_mb": max(0, budget - committed),
        "fits_budget": fits_budget, "fits_live": fits_live, "fits_overcommit": fits_overcommit,
        "overcommit": OVERCOMMIT, "overcommit_floor_mb": OVERCOMMIT_FLOOR_MB,
        "running": running, "count": len(recs),

        "slots_left_est": max(0, (budget - committed) // want) if want else 0,
    }

def reserve(res_id, kind, mem_mb, pid, owner=None, session=None, label=None, ctl_pid=None):

    rec = {"id": res_id, "kind": kind, "mem_mb": int(mem_mb), "pid": int(pid),
           "ctl_pid": (int(ctl_pid) if ctl_pid else None),
           "owner": owner, "session": session, "label": label, "ts": time.time()}
    with _Locked():
        recs = [r for r in _load() if r.get("id") != res_id]
        recs, _ = _reap(recs)
        recs.append(rec)
        _save(recs)
    return rec

def release(res_id):

    with _Locked():
        recs = _load()
        keep = [r for r in recs if r.get("id") != res_id]
        if len(keep) != len(recs):
            _save(keep)
            return True

        keep, changed = _reap(keep)
        if changed:
            _save(keep)
    return False

def snapshot():

    total, avail = _meminfo()
    reserve = host_reserve_mb(total)
    budget = max(0, total - reserve)
    with _Locked():
        recs = _load()
        recs, changed = _reap(recs)
        if changed:
            _save(recs)
    committed = _committed(recs)
    running = sorted(
        ({"id": r.get("id"), "kind": r.get("kind"), "mem_mb": int(r.get("mem_mb") or 0),
          "owner": r.get("owner"), "session": r.get("session"), "label": r.get("label"),
          "pid": r.get("pid"), "ctl_pid": r.get("ctl_pid"), "alive": pid_alive(r.get("pid")),
          "ts": r.get("ts")} for r in recs),
        key=lambda x: (-x["mem_mb"], x.get("ts") or 0))
    return {
        "total_mb": total, "avail_mb": avail, "host_reserve_mb": reserve, "budget_mb": budget,
        "committed_mb": committed, "free_budget_mb": max(0, budget - committed),
        "used_pct": round(100.0 * committed / budget, 1) if budget else None,
        "count": len(recs), "running": running,
        "screen_mb": SCREEN_MB, "session_mb": SESSION_MB, "office_mb": OFFICE_MB,
        "live_margin_mb": LIVE_MARGIN_MB,
        "overcommit": OVERCOMMIT, "overcommit_floor_mb": OVERCOMMIT_FLOOR_MB,

        "can_screen": plan(SCREEN_MB, "screen")["grant"],
        "can_session": plan(SESSION_MB, "session")["grant"],
        "can_office": plan(OFFICE_MB, "office")["grant"],
    }

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "plan":
        k = sys.argv[2] if len(sys.argv) > 2 else "session"
        print(json.dumps(plan(None, k), indent=2))
    else:
        print(json.dumps(snapshot(), indent=2))
