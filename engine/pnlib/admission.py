
from __future__ import annotations
import threading, time, itertools

INTERACTIVE = "interactive"
BATCH = "batch"
_CLASS_RANK = {INTERACTIVE: 0, BATCH: 1}

def _class_rank(klass: str) -> int:
    return _CLASS_RANK.get(klass, 0)

class Ticket:
    __slots__ = ("id", "principal", "cell", "klass", "weight", "seq", "start_tag", "finish_tag",
                 "enqueued_at", "granted", "granted_at", "renewed_at")

    def __init__(self, tid, principal, cell, klass, weight, seq, now):
        self.id = tid
        self.principal = principal or "owner"
        self.cell = cell or ""
        self.klass = klass if klass in _CLASS_RANK else INTERACTIVE
        self.weight = max(1, int(weight or 1))
        self.seq = seq
        self.start_tag = 0.0
        self.finish_tag = 0.0
        self.enqueued_at = now
        self.granted = False
        self.granted_at = None
        self.renewed_at = now

    def as_dict(self):
        return {"id": self.id, "principal": self.principal, "cell": self.cell,
                "klass": self.klass, "weight": self.weight, "seq": self.seq,
                "granted": self.granted,
                "wait_s": round((self.granted_at or time.time()) - self.enqueued_at, 3),
                "age_s": round(time.time() - self.enqueued_at, 3)}

class AdmissionQueue:

    DEFAULT_TTL_S = 120.0

    def __init__(self, slots: int, ttl_s: float | None = None):
        self.slots = max(1, int(slots))
        self.ttl_s = float(ttl_s) if ttl_s else self.DEFAULT_TTL_S
        self._waiting: "dict[object, Ticket]" = {}
        self._granted: "dict[object, Ticket]" = {}
        self._seq = itertools.count(1)
        self._external_used = 0
        self._vtime = 0.0
        self._last_finish: "dict[str, float]" = {}
        self._lock = threading.Lock()
        self.stats = {"admitted": 0, "granted": 0, "released": 0, "swept": 0, "max_wait_seen": 0}
        self._done = []

    def set_external_used(self, n: int):

        with self._lock:
            self._external_used = max(0, int(n or 0))
            self._pump_locked()

    def set_slots(self, n: int) -> int:

        with self._lock:
            self.slots = max(1, int(n or 1))
            self._pump_locked()
            return self.slots

    def _free_locked(self) -> int:
        return self.slots - len(self._granted) - self._external_used

    def _assign_tags_locked(self, t: Ticket):
        start = max(self._vtime, self._last_finish.get(t.principal, 0.0))
        t.start_tag = start
        t.finish_tag = start + t.weight
        self._last_finish[t.principal] = t.finish_tag

    def _ranked_waiting_locked(self):

        return sorted(self._waiting.values(),
                      key=lambda t: (_class_rank(t.klass), t.finish_tag, t.seq))

    def _pump_locked(self):

        while self._free_locked() > 0 and self._waiting:
            ranked = self._ranked_waiting_locked()
            head = ranked[0]
            self._waiting.pop(head.id, None)
            self._vtime = max(self._vtime, head.start_tag)
            head.granted = True
            now = time.time()
            head.granted_at = now
            head.renewed_at = now
            self._granted[head.id] = head
            self.stats["granted"] += 1
            w = int((head.granted_at - head.enqueued_at) * 1000)
            if w > self.stats["max_wait_seen"]:
                self.stats["max_wait_seen"] = w

    def _status_locked(self, tid) -> dict:
        base = {"slots": self.slots, "in_use": len(self._granted),
                "external_used": self._external_used, "waiting": len(self._waiting),
                "free": max(0, self._free_locked())}
        if tid in self._granted:
            t = self._granted[tid]
            return {"ok": True, "granted": True, "position": 0,
                    "wait_ms": int(((t.granted_at or t.enqueued_at) - t.enqueued_at) * 1000),
                    **base}
        if tid in self._waiting:
            ranked = self._ranked_waiting_locked()
            pos = 1 + next(i for i, t in enumerate(ranked) if t.id == tid)
            t = self._waiting[tid]
            return {"ok": True, "granted": False, "position": pos,
                    "wait_ms": int((time.time() - t.enqueued_at) * 1000), **base}
        return {"ok": True, "granted": False, "position": -1, "unknown": True, **base}

    def admit(self, tid, principal, cell, klass=INTERACTIVE, weight=1) -> dict:

        now = time.time()
        with self._lock:
            if tid in self._granted:
                self._granted[tid].renewed_at = now
                return {"already": True, **self._status_locked(tid)}
            if tid in self._waiting:
                self._waiting[tid].renewed_at = now
                self._pump_locked()
                return {"already": True, **self._status_locked(tid)}
            t = Ticket(tid, principal, cell, klass, weight, next(self._seq), now)
            self._assign_tags_locked(t)
            self._waiting[tid] = t
            self.stats["admitted"] += 1
            self._pump_locked()
            return self._status_locked(tid)

    def poll(self, tid) -> dict:

        now = time.time()
        with self._lock:
            if tid in self._granted:
                self._granted[tid].renewed_at = now
            elif tid in self._waiting:
                self._waiting[tid].renewed_at = now
            self._pump_locked()
            return self._status_locked(tid)

    def release(self, tid) -> dict:

        with self._lock:
            had = self._granted.pop(tid, None) or self._waiting.pop(tid, None)
            if had is not None:
                self.stats["released"] += 1
                self._done.append({**had.as_dict(), "done_at": time.time()})
                self._done = self._done[-40:]
            self._pump_locked()
            return {"ok": True, "released": bool(had), **self._status_locked(tid)}

    def sweep(self, now: float | None = None) -> dict:

        now = time.time() if now is None else now
        with self._lock:
            swept = []
            for pool in (self._granted, self._waiting):
                for tid, t in list(pool.items()):
                    if now - t.renewed_at > self.ttl_s:
                        pool.pop(tid, None)
                        swept.append(tid)
            if swept:
                self.stats["swept"] += len(swept)
                self._pump_locked()
            return {"swept": swept, **self._snapshot_locked()}

    def _snapshot_locked(self) -> dict:
        return {"slots": self.slots, "in_use": len(self._granted),
                "external_used": self._external_used, "waiting": len(self._waiting),
                "free": max(0, self._free_locked())}

    def snapshot(self) -> dict:

        with self._lock:
            ranked = self._ranked_waiting_locked()
            return {**self._snapshot_locked(),
                    "granted_list": [t.as_dict() for t in self._granted.values()],
                    "waiting_list": [dict(position=i + 1, **t.as_dict())
                                     for i, t in enumerate(ranked)],
                    "done_list": list(reversed(self._done)),
                    "stats": dict(self.stats)}

    def headroom(self) -> dict:
        with self._lock:
            return self._snapshot_locked()
