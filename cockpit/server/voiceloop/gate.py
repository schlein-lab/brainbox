

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

class State(enum.Enum):
    IDLE = "idle"
    ARMED = "armed"
    CAPTURING = "capturing"
    DISPATCH = "dispatch"

class ArmSource(enum.Enum):
    WAKE_WORD = "wake-word"
    PTT = "ptt"

class GateError(Exception):
    pass

@dataclass
class GateEvent:

    frm: State
    to: State
    reason: str
    ts: float

EarconHook = Callable[[str], None]

@dataclass
class WakeGate:

    arm_timeout_s: float = 8.0
    earcon_hook: Optional[EarconHook] = None
    clock: Callable[[], float] = time.monotonic

    state: State = field(default=State.IDLE, init=False)
    arm_source: Optional[ArmSource] = field(default=None, init=False)
    armed_at: Optional[float] = field(default=None, init=False)
    history: List[GateEvent] = field(default_factory=list, init=False)

    def _go(self, to: State, reason: str) -> None:
        ev = GateEvent(frm=self.state, to=to, reason=reason, ts=self.clock())
        self.state = to
        self.history.append(ev)

    def _expire_if_stale(self) -> bool:

        if self.state is State.ARMED and self.armed_at is not None:
            if (self.clock() - self.armed_at) >= self.arm_timeout_s:
                self.arm_source = None
                self.armed_at = None
                self._go(State.IDLE, "arm-timeout")
                return True
        return False

    def arm_wake(self) -> None:

        self._arm(ArmSource.WAKE_WORD)

    def ptt_press(self) -> None:

        self._arm(ArmSource.PTT)

    def _arm(self, source: ArmSource) -> None:
        self._expire_if_stale()
        if self.state is not State.IDLE:
            raise GateError(f"cannot arm from {self.state.value} (already active)")
        self.arm_source = source
        self.armed_at = self.clock()
        self._go(State.ARMED, f"arm:{source.value}")

    def submit_audio(self, audio_ref: str) -> str:

        self._expire_if_stale()
        if self.state is not State.ARMED:
            raise GateError(
                f"pre-wake audio rejected (state={self.state.value}); "
                "raw audio never leaves the client until wake (invariant §9.10)"
            )
        if not audio_ref:
            raise GateError("empty audio_ref")
        self._go(State.CAPTURING, "endpoint")

        if self.earcon_hook is not None:
            self.earcon_hook("working")
        return audio_ref

    def ptt_release(self) -> None:

        if self.state is State.ARMED and self.arm_source is ArmSource.PTT:
            self.arm_source = None
            self.armed_at = None
            self._go(State.IDLE, "ptt-release-no-audio")

    def begin_dispatch(self) -> None:

        if self.state is not State.CAPTURING:
            raise GateError(f"cannot dispatch from {self.state.value}")
        self._go(State.DISPATCH, "dispatch")

    def finish_turn(self) -> None:

        if self.state not in (State.DISPATCH, State.CAPTURING):
            raise GateError(f"cannot finish from {self.state.value}")
        self.arm_source = None
        self.armed_at = None
        self._go(State.IDLE, "turn-complete")

    def cancel(self) -> None:

        if self.state is State.IDLE:
            return
        self.arm_source = None
        self.armed_at = None
        self._go(State.IDLE, "cancel")

    def is_idle(self) -> bool:
        self._expire_if_stale()
        return self.state is State.IDLE

    def accepts_audio(self) -> bool:
        self._expire_if_stale()
        return self.state is State.ARMED
