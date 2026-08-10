
from __future__ import annotations

import abc
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from pnlib.memory.store import MemoryStore, StubEmbedder
from pnlib.origin import Origin

_REMEMBER_EVENT_KIND = "memory.remember"

class LedgerAppender(abc.ABC):

    present: bool = False

    @abc.abstractmethod
    def anchor(self, event: Dict, *, origin: Origin) -> Optional[int]:
        ...

    def close(self) -> None:
        pass

class NoOpLedgerSink(LedgerAppender):

    present = False

    def anchor(self, event: Dict, *, origin: Origin) -> Optional[int]:
        return None

class LedgerWriterSink(LedgerAppender):

    present = True

    def __init__(self, store, spool_path: Optional[str] = None, *,
                 owns_store: bool = False) -> None:
        from pnlib.ledger.append import LedgerWriter
        self._store = store
        self._owns_store = owns_store
        sp = spool_path or (getattr(store, "_path", None)
                            or getattr(getattr(store, "cx", None), "_path", None) or "ledger") + ".spool"

        self._writer = LedgerWriter(store, sp, background=False)

    @classmethod
    def open(cls, ledger_path: str, spool_path: Optional[str] = None) -> "LedgerWriterSink":

        from pnlib.ledger.store import LedgerStore
        store = LedgerStore(ledger_path)
        return cls(store, spool_path or (ledger_path + ".spool"), owns_store=True)

    def anchor(self, event: Dict, *, origin: Origin) -> Optional[int]:

        self._writer.record(event, origin=origin)
        self._writer.flush()
        row = self._store.cx.execute(
            "SELECT MAX(seq) AS s FROM bitemporal_log WHERE stream='ledger'").fetchone()
        return int(row["s"]) if row is not None and row["s"] is not None else None

    def close(self) -> None:
        try:
            self._writer.close()
        finally:
            if self._owns_store:
                try:
                    self._store.close()
                except Exception:
                    pass

def default_sink(ledger_path: Optional[str] = None,
                 spool_path: Optional[str] = None) -> LedgerAppender:

    if ledger_path is None:
        return NoOpLedgerSink()
    try:
        import pnlib.ledger
    except Exception:
        return NoOpLedgerSink()
    return LedgerWriterSink.open(ledger_path, spool_path)

@dataclass(frozen=True)
class RememberResult:

    seq: int
    ledger_seq: Optional[int]
    origin: str
    text: str

    @property
    def anchored(self) -> bool:
        return self.ledger_seq is not None

class AnchoredMemory:

    def __init__(self, store: MemoryStore, sink: Optional[LedgerAppender] = None, *,
                 component: str = "pn-memory") -> None:
        self.store = store
        self.sink = sink or NoOpLedgerSink()
        self.component = component

    @classmethod
    def open(cls, mem_path: str, *, ledger_path: Optional[str] = None, dim: int = 64,
             component: str = "pn-memory", embedder: Optional[StubEmbedder] = None) -> "AnchoredMemory":

        store = MemoryStore(mem_path, dim=dim, embedder=embedder)
        sink = default_sink(ledger_path)
        return cls(store, sink, component=component)

    @property
    def sink_present(self) -> bool:

        return bool(getattr(self.sink, "present", False))

    def remember(self, text: str, *, origin: str, actor: Optional[str] = None,
                 agent_id: Optional[str] = None, valid_time: Optional[float] = None,
                 observed_time: Optional[float] = None,
                 metadata: Optional[Dict] = None) -> RememberResult:

        now = time.time()
        vt = now if valid_time is None else float(valid_time)
        ot = now if observed_time is None else float(observed_time)
        org = Origin(origin, self.component, actor=actor, agent_id=agent_id)
        event = {
            "kind": _REMEMBER_EVENT_KIND,
            "text": text,
            "valid_time": vt,
            "observed_time": ot,
            "metadata": metadata or {},
        }
        ledger_seq = self.sink.anchor(event, origin=org)
        seq = self.store.remember(
            text, origin=origin, actor=actor, agent_id=agent_id,
            valid_time=vt, observed_time=ot, ledger_seq=ledger_seq, metadata=metadata)
        return RememberResult(seq=seq, ledger_seq=ledger_seq, origin=origin, text=text)

    def recall(self, query_text: str, *, k: int = 5, include_model: bool = False,
               observed_as_of: Optional[float] = None) -> List[Dict]:

        return self.store.recall(query_text, k=k, include_model=include_model,
                                 observed_as_of=observed_as_of)

    def as_of(self, query_text: str, observed_time: float, *, k: int = 5,
              include_model: bool = False) -> List[Dict]:

        return self.store.recall(query_text, k=k, include_model=include_model,
                                 observed_as_of=float(observed_time))

    def count(self, *, include_model: bool = True) -> int:
        return self.store.count(include_model=include_model)

    def close(self) -> None:
        try:
            self.sink.close()
        finally:
            self.store.close()
