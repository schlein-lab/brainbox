

from __future__ import annotations

import itertools
import time
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Optional

FLOOR_SCORE = 0.30

CONFIDENCE_GATE = 0.72

AMBIGUITY_MARGIN = 0.12

class Ceremony(str, Enum):
    READ = "read"
    LOW_STAKES = "low-stakes"
    IRREVERSIBLE = "irreversible"

@dataclass
class Entry:

    ref: str
    type: str
    label: str
    terms: tuple[str, ...] = ()
    ts: float = field(default_factory=lambda: time.time() * 1000.0)
    meta: dict = field(default_factory=dict)

@dataclass
class Candidate:
    entry: Entry
    score: float

    strong: bool = False

@dataclass
class Resolution:

    bound: Optional[Entry] = None
    disambiguation: Optional[dict] = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.bound is not None

def _norm(s: str) -> str:

    s = s.strip().lower()
    s = s.replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.split())

_DEIXIS_TYPE = {
    "tabelle": "table",
    "table": "table",
    "datei": "file",
    "file": "file",
    "resultat": "result",
    "ergebnis": "result",
    "result": "result",
    "ausgabe": "result",
    "output": "result",
    "nachricht": "message",
    "mail": "message",
    "message": "message",
    "kontakt": "contact",
    "contact": "contact",
}

_BARE_DEIXIS = {"das", "die", "der", "es", "dies", "das da", "this", "that", "it"}

def _token_overlap(query_tokens: set[str], entry: Entry) -> float:

    hay = {_norm(entry.label)}
    hay.update(_norm(t) for t in entry.terms)
    hay_tokens: set[str] = set()
    for h in hay:
        hay_tokens.update(h.split())
    if not query_tokens:
        return 0.0
    hit = sum(1 for q in query_tokens if q in hay_tokens)
    return hit / len(query_tokens)

class SalienceStack:

    def __init__(self, now: Callable[[], float] | None = None, cap: int = 256):

        self._entries: list[Entry] = []
        self._cap = cap
        self._now = now or (lambda: time.time() * 1000.0)

    def push(self, entry: Entry) -> Entry:

        self._entries = [e for e in self._entries if e.ref != entry.ref]
        if entry.ts is None:
            entry.ts = self._now()
        self._entries.append(entry)
        if len(self._entries) > self._cap:
            self._entries = self._entries[-self._cap :]
        return entry

    def touch(self, ref: str) -> None:

        for e in self._entries:
            if e.ref == ref:
                e.ts = self._now()
                self.push(e)
                return

    def get(self, ref: str) -> Optional[Entry]:
        for e in self._entries:
            if e.ref == ref:
                return e
        return None

    def all(self) -> list[Entry]:
        return list(reversed(self._entries))

    def _recency_rank(self) -> dict[str, int]:

        return {e.ref: i for i, e in enumerate(reversed(self._entries))}

    def _score(
        self, entry: Entry, query_tokens: set[str], rank: int, n: int, type_matched: bool
    ) -> float:

        lex = _token_overlap(query_tokens, entry)
        rec = 1.0 - (rank / max(1, n)) if n > 1 else 1.0

        base = 0.62 if type_matched else 0.0
        lex_credit = 0.80 * lex
        return max(base, lex_credit) + 0.10 * rec + (0.10 if (type_matched and lex) else 0.0)

    def _candidates(
        self, phrase: str, type_hint: Optional[str]
    ) -> tuple[list[Candidate], Optional[str], bool]:

        norm = _norm(phrase)
        tokens = set(norm.split())

        sel_type = type_hint
        bare = False
        if sel_type is None:
            for tok in tokens:
                if tok in _DEIXIS_TYPE:
                    sel_type = _DEIXIS_TYPE[tok]
                    break
        if sel_type is None and (norm in _BARE_DEIXIS or tokens <= _BARE_DEIXIS):
            bare = True

        id_tokens = {
            t
            for t in tokens
            if t not in _DEIXIS_TYPE and t not in _BARE_DEIXIS and t not in {"an", "zu", "die", "der", "das"}
        }

        rank = self._recency_rank()
        n = len(self._entries)
        cands: list[Candidate] = []
        for e in self._entries:
            if sel_type is not None and e.type != sel_type:
                continue

            type_matched = sel_type is not None
            if bare:

                sc = self._score(e, set(), rank[e.ref], n, type_matched=False)
                strong = False
            elif id_tokens:
                sc = self._score(e, id_tokens, rank[e.ref], n, type_matched)

                strong = _token_overlap(id_tokens, e) >= 0.999
            else:

                sc = self._score(e, set(), rank[e.ref], n, type_matched)
                strong = type_matched
            cands.append(Candidate(e, sc, strong=strong))

        cands.sort(key=lambda c: (-c.score, rank[c.entry.ref]))
        return cands, sel_type, bare

    def resolve(
        self,
        phrase: str,
        *,
        ceremony: Ceremony = Ceremony.LOW_STAKES,
        type_hint: Optional[str] = None,
        re_id: str = "",
    ) -> Resolution:

        cands, sel_type, bare = self._candidates(phrase, type_hint)
        usable = [c for c in cands if c.score >= FLOOR_SCORE]

        if not usable:
            return Resolution(
                disambiguation=self._ask(phrase, cands, sel_type, re_id, no_match=True),
                reason="no-candidate",
            )

        if ceremony == Ceremony.READ:
            return Resolution(bound=usable[0].entry, reason="read-guess")

        top = usable[0]
        second = usable[1] if len(usable) > 1 else None

        if second is not None and (top.score - second.score) < AMBIGUITY_MARGIN:
            return Resolution(
                disambiguation=self._ask(phrase, usable, sel_type, re_id),
                reason="ambiguous",
            )

        confident_enough = top.strong
        if not confident_enough or (bare and len(self._entries) > 1):
            return Resolution(
                disambiguation=self._ask(phrase, usable, sel_type, re_id, low_conf=True),
                reason="low-confidence",
            )

        if ceremony == Ceremony.IRREVERSIBLE and second is not None:
            return Resolution(
                disambiguation=self._ask(phrase, usable, sel_type, re_id),
                reason="irreversible-multi",
            )

        return Resolution(bound=top.entry, reason="confident")

    def _ask(
        self,
        phrase: str,
        cands: Iterable[Candidate],
        sel_type: Optional[str],
        re_id: str,
        *,
        low_conf: bool = False,
        no_match: bool = False,
    ) -> dict:

        shown = list(itertools.islice(cands, 5))
        candidate_list = [{"ref": c.entry.ref, "label": c.entry.label} for c in shown]
        if no_match or not candidate_list:
            prompt = f"Ich finde nichts passendes zu „{phrase}“. Was genau meinst du?"
        elif len(candidate_list) == 1 and low_conf:
            c = candidate_list[0]
            prompt = f"Meinst du „{c['label']}“?"
        else:
            names = [c["label"] for c in candidate_list]
            if len(names) == 2:
                joined = f"{names[0]} oder {names[1]}"
            else:
                joined = ", ".join(names[:-1]) + f" oder {names[-1]}"
            prompt = f"Meinst du {joined}?"
        return {
            "verb": "conversation.disambiguate",
            "re": re_id,
            "candidates": candidate_list,
            "prompt": prompt,
        }
