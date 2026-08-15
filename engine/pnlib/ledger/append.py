
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

from pnlib.origin import Origin, require_origin
from pnlib.ledger.store import LedgerStore

_ENVELOPE_VERSION = 1

@dataclass(frozen=True)
class Receipt:

    spool_id: int
    durable: bool
    enqueued_at: float

def envelope(event, origin: Origin) -> dict:

    return {"v": _ENVELOPE_VERSION, "origin": origin.to_dict(), "event": event}

class LedgerWriter:

    def __init__(self, store: LedgerStore, spool_path: str, *, background: bool = True,
                 flush_interval: float = 0.05, logger=None) -> None:
        self._store = store
        self._spool_path = spool_path
        self._cursor_path = spool_path + ".cursor"
        self._log = logger
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        d = os.path.dirname(spool_path)
        if d:
            os.makedirs(d, exist_ok=True)

        self._count = self._count_spool_lines()
        self._applied = self._read_cursor()
        self._stop = False
        self._worker: Optional[threading.Thread] = None
        self._flush_interval = max(0.001, float(flush_interval))

        self._drain_locked_safe()
        if background:
            self._worker = threading.Thread(target=self._run, name="pn-ledger-writer", daemon=True)
            self._worker.start()

    def record(self, event, *, origin: Origin = None, valid_time: Optional[float] = None,
               observed_time: Optional[float] = None) -> Receipt:

        org = require_origin(origin)
        now = time.time()
        rec = {
            "origin": org.to_dict(),
            "event": event,
            "vt": now if valid_time is None else float(valid_time),
            "ot": now if observed_time is None else float(observed_time),
            "enqueued_at": now,
        }
        line = json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with self._lock:
            spool_id = self._count
            self._append_spool_line(line)
            self._count += 1
            if self._worker is None:

                self._drain_locked_safe()
            else:
                self._wake.notify()
            return Receipt(spool_id=spool_id, durable=True, enqueued_at=now)

    def flush(self, timeout: Optional[float] = None) -> int:

        deadline = None if timeout is None else time.time() + timeout
        total = 0
        while True:
            with self._lock:
                total += self._drain_locked_safe()
                remaining = self._count - self._applied
            if remaining == 0 or deadline is None or time.time() >= deadline:
                return total
            time.sleep(min(self._flush_interval, 0.02))

    def pending(self) -> int:

        with self._lock:
            return self._count - self._applied

    def applied(self) -> int:
        with self._lock:
            return self._applied

    def close(self, drain: bool = True) -> None:

        with self._lock:
            self._stop = True
            self._wake.notify_all()
        if self._worker is not None:
            self._worker.join(timeout=5.0)
            self._worker = None
        if drain:
            with self._lock:
                self._drain_locked_safe()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _run(self) -> None:
        with self._lock:
            while not self._stop:
                self._drain_locked_safe()
                if self._count == self._applied:

                    self._wake.wait(timeout=self._flush_interval)
                else:

                    self._wake.wait(timeout=self._flush_interval)

    def _append_spool_line(self, line: str) -> None:
        fd = os.open(self._spool_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

    def _count_spool_lines(self) -> int:
        if not os.path.exists(self._spool_path):
            return 0
        n = 0
        with open(self._spool_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    n += 1
        return n

    def _read_cursor(self) -> int:
        try:
            with open(self._cursor_path, "r", encoding="utf-8") as f:
                return int(f.read().strip() or "0")
        except (OSError, ValueError):
            return 0

    def _write_cursor(self, n: int) -> None:
        tmp = self._cursor_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, str(int(n)).encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self._cursor_path)

    def _read_spool_records(self):

        recs = []
        if not os.path.exists(self._spool_path):
            return recs
        with open(self._spool_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    recs.append(json.loads(line))
        return recs

    def _drain_locked_safe(self) -> int:

        applied_now = 0
        try:
            recs = self._read_spool_records()
        except Exception as e:
            self._warn(f"spool read failed: {e!r}")
            return 0
        while self._applied < len(recs):
            rec = recs[self._applied]
            try:
                org = Origin.from_dict(rec["origin"])
                self._store.append(
                    envelope(rec["event"], org),
                    origin=org.kind.value, actor=org.actor, agent_id=org.agent_id,
                    valid_time=rec.get("vt"), observed_time=rec.get("ot"))
            except Exception as e:

                self._warn(f"ledger apply failed at spool_id={self._applied}: {e!r}")
                break
            self._applied += 1
            self._write_cursor(self._applied)
            applied_now += 1
        return applied_now

    def _warn(self, msg: str) -> None:
        if self._log is not None:
            try:
                self._log.warning("pn-ledger-writer: %s", msg)
                return
            except Exception:
                pass

def record(store: LedgerStore, event, *, origin: Origin = None, spool_path: Optional[str] = None,
           valid_time: Optional[float] = None, observed_time: Optional[float] = None) -> Receipt:

    sp = spool_path or (getattr(store, "_path", None) or "ledger") + ".spool"
    w = LedgerWriter(store, sp, background=False)
    rcpt = w.record(event, origin=origin, valid_time=valid_time, observed_time=observed_time)
    w.flush()
    return rcpt
