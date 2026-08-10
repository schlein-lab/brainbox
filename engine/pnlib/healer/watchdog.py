
from __future__ import annotations

import time

class Watchdog:
    def __init__(self, fast_interval_s=5.0, deep_interval_s=60.0,
                 liveness_timeout_s=None, clock=None, on_stale=None):
        self.fast_interval_s = float(fast_interval_s)
        self.deep_interval_s = float(deep_interval_s)

        self.liveness_timeout_s = (float(liveness_timeout_s)
                                   if liveness_timeout_s is not None
                                   else 3.0 * self.fast_interval_s)
        self._clock = clock or time.time
        self.on_stale = on_stale
        start = self._clock()
        self.last_pet = start
        self._last_fast = None
        self._last_deep = None

        self.fast_ticks = 0
        self.deep_ticks = 0
        self.stale_events = 0

    def pet(self, now=None):

        self.last_pet = self._clock() if now is None else now

    def liveness_ok(self, now=None):
        now = self._clock() if now is None else now
        return (now - self.last_pet) <= self.liveness_timeout_s

    def due(self, now=None):

        now = self._clock() if now is None else now
        fast_due = (self._last_fast is None
                    or (now - self._last_fast) >= self.fast_interval_s)
        deep_due = (self._last_deep is None
                    or (now - self._last_deep) >= self.deep_interval_s)
        return {"fast": fast_due, "deep": deep_due}

    def tick(self, now=None, fast_fn=None, deep_fn=None):

        now = self._clock() if now is None else now
        due = self.due(now)
        fired = {"fast": False, "deep": False, "live": True, "deep_result": None}

        if due["fast"]:
            self._last_fast = now
            self.fast_ticks += 1
            fired["fast"] = True
            live = self.liveness_ok(now)
            fired["live"] = live
            if not live:
                self.stale_events += 1
                if self.on_stale is not None:
                    self.on_stale(now, now - self.last_pet)
            if fast_fn is not None:
                fast_fn(now)

        if due["deep"]:
            self._last_deep = now
            self.deep_ticks += 1
            fired["deep"] = True
            if deep_fn is not None:
                fired["deep_result"] = deep_fn(now)

        return fired
