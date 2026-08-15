

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from salience import Entry

DEFAULT_HOLD_MS = 10_000

_NONCE_LOW = 10
_NONCE_HIGH = 99

_DIGIT_WORDS_DE = {
    "0": "null", "1": "eins", "2": "zwei", "3": "drei", "4": "vier",
    "5": "fuenf", "6": "sechs", "7": "sieben", "8": "acht", "9": "neun",
}

_WORD_DIGITS_DE = {
    "null": "0", "eins": "1", "ein": "1", "eine": "1", "zwei": "2", "zwo": "2",
    "drei": "3", "vier": "4", "fuenf": "5", "fünf": "5", "sechs": "6",
    "sieben": "7", "acht": "8", "neun": "9",
}

class CeremonyState(str, Enum):
    PROMPTED = "prompted"
    HOLDING = "holding"
    EXECUTED = "executed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"

def speak_digits_de(n: int) -> str:

    return "-".join(_DIGIT_WORDS_DE[d] for d in str(n))

def spell_address(addr: str) -> str:

    out = (
        addr.replace("@", " at ")
        .replace(".", " punkt ")
        .replace("_", " unterstrich ")
        .replace("-", " strich ")
    )
    return " ".join(out.split())

def content_digest(data: bytes) -> str:

    return hashlib.sha256(data).hexdigest()[:6]

def build_readback(verb: str, target: Entry, *, subject: Optional[str] = None,
                   attachments: Optional[list[Entry]] = None) -> dict:

    meta = target.meta
    recipient = ""
    spoken_parts: list[str] = []

    if verb == "verb.mail_send":
        recipient = meta.get("address", target.label)
        spoken_parts.append(f"an {spell_address(recipient)}")
    elif verb in ("verb.pay",):
        recipient = meta.get("payee", target.label)
        amount = meta.get("amount")
        currency = meta.get("currency", "")
        spoken_parts.append(f"an {recipient}")
        if amount is not None:
            spoken_parts.append(f"betrag {amount} {currency}".strip())
    elif verb == "verb.kill":
        recipient = f"{meta.get('service_name', target.label)}/{meta.get('pid','?')}"
        spoken_parts.append(f"prozess {meta.get('service_name', target.label)} "
                            f"pid {meta.get('pid','?')}")
    else:
        recipient = target.label
        spoken_parts.append(f"ziel {target.label}")

    subj = subject if subject is not None else meta.get("subject")
    if subj:
        spoken_parts.append(f"betreff wörtlich: {subj}")

    digest_facts = []
    for a in (attachments or []):
        fn = a.meta.get("filename", a.label)
        size = a.meta.get("size")
        dg = a.meta.get("digest")
        if dg is None and "bytes" in a.meta:
            dg = content_digest(a.meta["bytes"])
        size_txt = f", {size} bytes" if size is not None else ""
        dg_txt = f", prüfsumme {'-'.join(dg)}" if dg else ""
        digest_facts.append(f"{fn}{size_txt}{dg_txt}")
        spoken_parts.append(f"anhang {fn}{size_txt}{dg_txt}")

    return {
        "recipient": recipient,
        "subject": subj,
        "digest": digest_facts,
        "spoken": " — ".join(spoken_parts),
    }

def normalize_nonce_response(resp: str) -> str:

    resp = (resp or "").strip().lower()
    resp = resp.replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    tokens = resp.replace("-", " ").replace(",", " ").split()
    out = []
    for t in tokens:
        if t.isdigit():
            out.append(t)
        elif t in _WORD_DIGITS_DE:
            out.append(_WORD_DIGITS_DE[t])

    return "".join(out)

@dataclass
class Ceremony:

    re_id: str
    verb: str
    action: Callable[[], object]
    readback: dict
    nonce: str
    hold_ms: int = DEFAULT_HOLD_MS
    require_hardware: bool = False
    state: CeremonyState = CeremonyState.PROMPTED
    result: object = None
    error: Optional[str] = None
    _timer: Optional[threading.Timer] = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _scheduler: Optional[Callable] = field(default=None, repr=False)
    _on_execute: Optional[Callable[["Ceremony"], None]] = field(default=None, repr=False)

    def prompt_event(self) -> dict:
        challenge: dict = {}
        if self.require_hardware:
            challenge["hardware"] = True
        else:
            challenge["nonce"] = self.nonce
        return {
            "verb": "verb.ceremony_prompt",
            "re": self.re_id,
            "readback": {
                "recipient": self.readback.get("recipient"),
                "digest": self.readback.get("digest"),
                "subject": self.readback.get("subject"),
            },
            "challenge": challenge,
            "hold_ms": self.hold_ms,
            "spoken": self._spoken_prompt(),
        }

    def _spoken_prompt(self) -> str:
        rb = self.readback.get("spoken", "")
        if self.require_hardware:
            chal = "bestätige mit der taste am headset"
        else:
            chal = f"sag {speak_digits_de(int(self.nonce))} zum senden"
        return f"{rb}. {chal}. danach: sende in {self.hold_ms // 1000} sekunden, sag stopp zum abbrechen."

    def confirm(self, *, nonce_response: str = "", hardware_confirm: bool = False) -> dict:

        with self._lock:
            if self.state != CeremonyState.PROMPTED:
                return {"accepted": False, "reason": f"not-prompted:{self.state.value}"}

            if self.require_hardware:
                ok = bool(hardware_confirm)
                reason = "" if ok else "hardware-confirm-missing"
            else:
                got = normalize_nonce_response(nonce_response)
                ok = bool(got) and got == self.nonce
                if not got:
                    reason = "no-nonce"
                elif not ok:
                    reason = "wrong-nonce"
                else:
                    reason = ""

            if not ok:
                self.state = CeremonyState.REJECTED
                return {"accepted": False, "reason": reason}

            self.state = CeremonyState.HOLDING

        self._arm_hold()
        return {"accepted": True, "holding_ms": self.hold_ms}

    def _arm_hold(self) -> None:
        if self._scheduler is not None:

            return
        self._timer = threading.Timer(self.hold_ms / 1000.0, self._fire)
        self._timer.daemon = True
        self._timer.start()

    def cancel(self) -> dict:

        with self._lock:
            if self.state in (CeremonyState.CANCELLED, CeremonyState.REJECTED):

                return {"cancelled": True, "already": self.state.value, "executed": False}
            if self.state not in (CeremonyState.PROMPTED, CeremonyState.HOLDING):
                return {"cancelled": False, "reason": f"not-cancellable:{self.state.value}",
                        "executed": self.state in (CeremonyState.EXECUTED, CeremonyState.FAILED)}
            prev = self.state
            self.state = CeremonyState.CANCELLED
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        return {"cancelled": True, "from": prev.value}

    def elapse(self) -> dict:

        return self._fire()

    def _fire(self) -> dict:
        with self._lock:
            if self.state != CeremonyState.HOLDING:

                return {"executed": False, "state": self.state.value}

            self.state = CeremonyState.EXECUTED
        try:
            self.result = self.action()
        except Exception as exc:
            with self._lock:
                self.state = CeremonyState.FAILED
                self.error = f"{type(exc).__name__}: {exc}"
            return {"executed": False, "state": self.state.value, "error": self.error}
        if self._on_execute:
            self._on_execute(self)
        return {"executed": True, "result": self.result}

class CeremonyManager:

    def __init__(self, *, rng: Optional[Callable[[int, int], int]] = None,
                 deterministic_hold: bool = False,
                 on_ledger: Optional[Callable[[Ceremony], None]] = None):
        self._rng = rng or (lambda lo, hi: secrets.randbelow(hi - lo + 1) + lo)
        self._deterministic = deterministic_hold
        self._on_ledger = on_ledger
        self._active: dict[str, Ceremony] = {}
        self._lock = threading.Lock()

    def mint_nonce(self) -> str:
        return str(self._rng(_NONCE_LOW, _NONCE_HIGH))

    def begin(
        self,
        *,
        re_id: str,
        verb: str,
        target: Entry,
        action: Callable[[], object],
        subject: Optional[str] = None,
        attachments: Optional[list[Entry]] = None,
        hold_ms: int = DEFAULT_HOLD_MS,
        require_hardware: bool = False,
    ) -> tuple[Ceremony, dict]:

        readback = build_readback(verb, target, subject=subject, attachments=attachments)
        nonce = self.mint_nonce()
        cer = Ceremony(
            re_id=re_id,
            verb=verb,
            action=action,
            readback=readback,
            nonce=nonce,
            hold_ms=hold_ms,
            require_hardware=require_hardware,
        )
        if self._deterministic:
            cer._scheduler = lambda: None
        cer._on_execute = self._executed
        with self._lock:
            self._active[re_id] = cer
        return cer, cer.prompt_event()

    def confirm(self, re_id: str, **kw) -> dict:
        cer = self._get(re_id)
        if cer is None:
            return {"accepted": False, "reason": "unknown-ceremony"}
        return cer.confirm(**kw)

    def cancel(self, re_id: str) -> dict:
        cer = self._get(re_id)
        if cer is None:
            return {"cancelled": False, "reason": "unknown-ceremony"}
        res = cer.cancel()
        if res.get("cancelled"):
            self._retire(re_id)
        return res

    def elapse(self, re_id: str) -> dict:

        cer = self._get(re_id)
        if cer is None:
            return {"executed": False, "reason": "unknown-ceremony"}
        return cer.elapse()

    def get(self, re_id: str) -> Optional[Ceremony]:
        return self._get(re_id)

    def _get(self, re_id: str) -> Optional[Ceremony]:
        with self._lock:
            return self._active.get(re_id)

    def _retire(self, re_id: str) -> None:
        with self._lock:
            self._active.pop(re_id, None)

    def _executed(self, cer: Ceremony) -> None:

        if self._on_ledger:
            self._on_ledger(cer)
        self._retire(cer.re_id)
