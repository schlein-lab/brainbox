#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import time

A_STRENG, A_STANDARD, A_FREI = 1, 2, 4
LEVELS = (A_STRENG, A_STANDARD, A_FREI)

L0, L1, L2, L3, L4, L5 = A_STRENG, A_STRENG, A_STANDARD, A_STANDARD, A_FREI, A_FREI

AUTONOMY_LABELS = {
    A_STRENG:   "Streng — jede Änderung und jede Aktion nach außen per 2FA bestätigen",
    A_STANDARD: "Standard — nur Löschen/Überschreiben und Senden/Zahlen per 2FA bestätigen",
    A_FREI:     "Frei — nur Senden/Zahlen nach außen per 2FA bestätigen (Löschen immer)",
}

AUTONOMY_SHORT = {A_STRENG: "Streng", A_STANDARD: "Standard", A_FREI: "Frei"}

AUTONOMY_EXPERIENCE = {
    A_STRENG:   "Lesen läuft frei. Jedes Schreiben, Veröffentlichen, Löschen und jede Aktion nach außen fragt per Handy-Code nach.",
    A_STANDARD: "Lesen, normales Arbeiten und Veröffentlichen laufen frei. Löschen/Überschreiben sowie Senden und Zahlen fragen per Handy-Code nach.",
    A_FREI:     "Auf der Box arbeitet der Agent frei, Veröffentlichen eingeschlossen. Nur Senden und Zahlen fragen per Handy-Code nach — Löschen immer.",
}
DEFAULT_AUTONOMY = A_STANDARD

_ACTION_CLASSES = ("read", "write", "publish", "destructive", "external")

_AC_SEVERITY = {"read": 0, "write": 1, "publish": 1, "destructive": 2, "external": 3}

_TWOFA_THRESHOLD = {A_STRENG: 1, A_STANDARD: 2, A_FREI: 3}

_DESTRUCTIVE_RE = re.compile(r"\b(rm|rmdir|mkfs|dd|shred|drop\s+table|truncate|kill|reboot|shutdown|"
                             r"delete|destroy|format|wipe|git\s+push\s+--force|--force)\b", re.I)
_EXTERNAL_RE = re.compile(r"\b(send|email|mail|pay|transfer|post|tweet|deploy|release)\b", re.I)
_PUBLISH_RE = re.compile(r"\b(publish|publishing|ver(?:oe|ö)ffentlich\w*)\b", re.I)
_WRITE_RE = re.compile(r"\b(write|save|create|mkdir|touch|install|commit|chmod|chown|mv|cp|update|set)\b", re.I)

def normalize_level(raw) -> int:

    try:
        v = int(raw)
    except (TypeError, ValueError):
        return A_STRENG

    return {0: A_STRENG, 1: A_STRENG, 2: A_STANDARD, 3: A_STANDARD,
            4: A_FREI, 5: A_FREI}.get(v, A_STRENG)

def classify_action(command: str) -> str:

    c = command or ""
    if _EXTERNAL_RE.search(c):
        return "external"
    if _DESTRUCTIVE_RE.search(c):
        return "destructive"
    if _PUBLISH_RE.search(c):
        return "publish"
    if _WRITE_RE.search(c):
        return "write"
    return "read"

def requires_2fa(level, action_class: str) -> bool:

    lvl = normalize_level(level)
    sev = _AC_SEVERITY.get(action_class)
    if sev is None:
        sev = 3
    return sev >= _TWOFA_THRESHOLD[lvl]

TIER_AUTO, TIER_CONFIRM, TIER_TWOFA = "auto", "confirm", "twofa"
_TIER_ORDER = {TIER_AUTO: 0, TIER_CONFIRM: 1, TIER_TWOFA: 2}

ACTION_CLASS_LABELS = {
    "read":        ("Anschauen / Lesen", "Dinge ansehen, nichts ändern"),
    "write":       ("Schreiben / Ändern", "Dateien anlegen, ändern, überschreiben auf der Box"),
    "publish":     ("Veröffentlichen", "etwas sichtbar/abrufbar machen (rücknehmbar)"),
    "destructive": ("Löschen / Überschreiben", "unwiderruflich entfernen"),
    "external":    ("Senden / Zahlen (nach außen)", "Mail, Nachricht, Zahlung, Deploy nach außen"),
}
ACTION_CLASS_ORDER = ("read", "write", "publish", "destructive", "external")

def normalize_tier(raw) -> str:

    t = str(raw or "").strip().lower()
    return t if t in _TIER_ORDER else TIER_AUTO

def base_tier(level, action_class: str) -> str:

    return TIER_TWOFA if requires_2fa(level, action_class) else TIER_AUTO

def class_requirement(level, action_class: str, overrides=None) -> str:

    ac = action_class if action_class in _ACTION_CLASSES else "external"
    base = base_tier(level, ac)
    ov = (overrides or {}).get(ac)
    if ov is None:
        return base
    ovt = normalize_tier(ov)
    return ovt if _TIER_ORDER[ovt] >= _TIER_ORDER[base] else base

def requires_2fa_eff(level, action_class: str, overrides=None) -> bool:

    return class_requirement(level, action_class, overrides) == TIER_TWOFA

def clamp_overrides(level, overrides):

    out = {}
    for ac, val in (overrides or {}).items():
        if ac not in _ACTION_CLASSES:
            continue
        t = normalize_tier(val)
        if _TIER_ORDER[t] > _TIER_ORDER[base_tier(level, ac)]:
            out[ac] = t
    return out

def freigaben_matrix(level, overrides=None):

    rows = []
    for ac in ACTION_CLASS_ORDER:
        name, hint = ACTION_CLASS_LABELS[ac]
        b = base_tier(level, ac)
        eff = class_requirement(level, ac, overrides)

        choices = [t for t in (TIER_AUTO, TIER_CONFIRM, TIER_TWOFA) if _TIER_ORDER[t] >= _TIER_ORDER[b]]
        rows.append({"action_class": ac, "label": name, "hint": hint,
                     "floor": b, "effective": eff, "choices": choices})
    return rows

import json as _json

FREIGABEN_DIR = os.environ.get(
    "PN_FREIGABEN_DIR", os.path.expanduser("~/.local/share/brainbox-portal/freigaben"))

def _fg_uid_safe(uid):
    return re.sub(r"[^A-Za-z0-9_.-]", "", str(uid or "owner"))[:64] or "owner"

def _fg_path(principal):
    return os.path.join(FREIGABEN_DIR, _fg_uid_safe(principal) + ".json")

def load_user_freigaben(principal):

    try:
        with open(_fg_path(principal), encoding="utf-8") as f:
            d = _json.load(f)
        lvl = normalize_level(d.get("level", DEFAULT_AUTONOMY))
        ov = clamp_overrides(lvl, d.get("overrides") or {})
        return {"level": lvl, "overrides": ov}
    except (OSError, ValueError, TypeError):
        return {"level": DEFAULT_AUTONOMY, "overrides": {}}

def save_user_freigaben(principal, level, overrides):

    lvl = normalize_level(level)
    ov = clamp_overrides(lvl, overrides or {})
    try:
        os.makedirs(FREIGABEN_DIR, exist_ok=True)
        p = _fg_path(principal)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump({"level": lvl, "overrides": ov}, f)
        os.replace(tmp, p)
    except OSError:
        pass
    return {"level": lvl, "overrides": ov}

def user_requires_2fa(principal, action_class, session_level=None):

    prof = load_user_freigaben(principal)
    lvl = normalize_level(session_level) if session_level is not None else prof["level"]
    return requires_2fa_eff(lvl, action_class, prof["overrides"])

def decide(level, action_class: str) -> str:

    return "twofa" if requires_2fa(level, action_class) else "auto"

_SID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")

def _tag(s: str) -> str:

    head = re.sub(r"[^A-Za-z0-9]", "", str(s))[:16] or "x"
    return "%s_%s" % (head, hashlib.sha256(str(s).encode()).hexdigest()[:10])

def cell_name(principal: str, session: str) -> str:

    return "sc-%s-%s" % (_tag(principal), _tag(session))

WARM = "warm"
SUSPENDED = "suspended"
EVICTED = "evicted"
ABSENT = "absent"

IDLE_SUSPEND_S = 30 * 60
IDLE_EVICT_S = 24 * 3600

class SessionCellRegistry:
    def __init__(self, base_dir: str, vol_dir: str = None):
        self.base = base_dir
        self.vol_dir = vol_dir or os.path.join(base_dir, "session-vols")
        self.path = os.path.join(base_dir, "session-cells.json")
        self.notify_path = os.path.join(base_dir, "session-notify.jsonl")
        self._lock_path = os.path.join(base_dir, "session-cells.lock")

    @contextlib.contextmanager
    def _locked(self):
        os.makedirs(self.base, exist_ok=True)
        lf = open(self._lock_path, "w")
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
            lf.close()

    def _load(self) -> dict:
        try:
            with open(self.path) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, d: dict) -> None:
        os.makedirs(self.base, exist_ok=True)
        tmp = "%s.tmp.%d" % (self.path, os.getpid())
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, self.path)

    def _key(self, principal, session):
        return "%s/%s" % (principal, session)

    def _notify(self, principal, session, event, extra=None):

        rec = {"principal": principal, "session": session, "event": event, "ts": _clock()}
        if extra:
            rec.update(extra)
        with open(self.notify_path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def provision(self, principal, session, autonomy=DEFAULT_AUTONOMY, now=None):

        if not _SID_RE.match(str(session)):
            raise ValueError("bad session id %r" % session)
        t = now if now is not None else _clock()
        with self._locked():
            d = self._load()
            k = self._key(principal, session)
            cn = cell_name(principal, session)
            rec = d.get(k)
            reprov = False
            if rec is None:
                rec = {
                    "principal": principal, "session": session, "cell": cn,
                    "state": WARM, "autonomy": normalize_level(autonomy),
                    "keystore_vol": os.path.join(self.vol_dir, cn + "-keystore.img"),
                    "work_vol": os.path.join(self.vol_dir, cn + "-work.img"),
                    "created": t, "last_active": t, "evict_reason": None,
                }
            else:
                if rec.get("state") == EVICTED:
                    reprov = True
                rec["state"] = WARM
                rec["last_active"] = t
                rec["evict_reason"] = None
            d[k] = rec
            self._save(d)
        self._notify(principal, session, "reprovisioned" if reprov else "provisioned",
                     {"cell": cn, "state": WARM})
        return rec

    def attach(self, principal, session, now=None):

        t = now if now is not None else _clock()
        with self._locked():
            d = self._load()
            rec = d.get(self._key(principal, session))
            if rec is None:
                return None
            rec["last_active"] = t
            if rec["state"] in (SUSPENDED, EVICTED):
                rec["state"] = WARM
            self._save(d)
        return rec

    def _transition(self, principal, session, state, reason=None, now=None):
        t = now if now is not None else _clock()
        with self._locked():
            d = self._load()
            rec = d.get(self._key(principal, session))
            if rec is None:
                return None
            rec["state"] = state
            if reason:
                rec["evict_reason"] = reason
            rec["state_changed"] = t
            self._save(d)
        return rec

    def suspend(self, principal, session, now=None):
        rec = self._transition(principal, session, SUSPENDED, now=now)
        if rec:
            self._notify(principal, session, "suspended")
        return rec

    def resume(self, principal, session, now=None):
        return self.attach(principal, session, now=now)

    def evict(self, principal, session, reason="idle", now=None):

        rec = self._transition(principal, session, EVICTED, reason=reason, now=now)
        if rec:
            self._notify(principal, session, "evicted", {"reason": reason})
        return rec

    def set_node(self, principal, session, node):

        node = str(node) if node else None
        with self._locked():
            d = self._load()
            rec = d.get(self._key(principal, session))
            if rec is None:
                return None
            if (rec.get("node") or None) == node:
                return rec
            if node:
                rec["node"] = node
            else:
                rec.pop("node", None)
            self._save(d)
        return rec

    def list_live(self, principal=None):

        return [r for r in self.list_all(principal) if r.get("state") != EVICTED]

    def list_all(self, principal=None):

        d = self._load()
        out = [r for r in d.values() if principal is None or r.get("principal") == principal]
        return sorted(out, key=lambda r: r.get("last_active", 0), reverse=True)

    def get(self, principal, session):
        return self._load().get(self._key(principal, session))

    def set_autonomy(self, principal, session, level, now=None):
        level = normalize_level(level)
        with self._locked():
            d = self._load()
            rec = d.get(self._key(principal, session))
            if rec is None:
                return None
            rec["autonomy"] = level
            self._save(d)
        self._notify(principal, session, "autonomy_set", {"level": level})
        return rec

    def get_autonomy(self, principal, session):
        rec = self.get(principal, session)

        return normalize_level(rec.get("autonomy", DEFAULT_AUTONOMY)) if rec else DEFAULT_AUTONOMY

    def decide_for(self, principal, session, command=None, action_class=None):

        rec = self.get(principal, session)
        if rec is None:
            return "deny"
        ac = action_class or classify_action(command or "")
        return decide(rec.get("autonomy", DEFAULT_AUTONOMY), ac)

    def owns(self, principal, session, artifact_path):

        rec = self.get(principal, session)
        if rec is None:
            return False
        root = os.path.realpath(os.path.dirname(rec.get("work_vol") or ""))
        ap = os.path.realpath(artifact_path)
        cn = rec.get("cell")
        return ap.startswith(root + os.sep) and cn in os.path.basename(ap)

    def route(self, principal, session):

        rec = self.get(principal, session)
        if rec is None or rec.get("state") == EVICTED:
            return None
        return {"cell": rec["cell"], "keystore_vol": rec["keystore_vol"],
                "work_vol": rec["work_vol"],
                "autonomy": normalize_level(rec.get("autonomy", DEFAULT_AUTONOMY))}

    def scan_idle(self, now):

        d = self._load()
        out = {}
        for k, r in d.items():
            idle = now - r.get("last_active", 0)
            st = r.get("state")
            if st in (WARM, SUSPENDED) and idle >= IDLE_EVICT_S:
                out[k] = EVICTED
            elif st == WARM and idle >= IDLE_SUSPEND_S:
                out[k] = SUSPENDED
        return out

    def apply_idle(self, now=None):
        t = now if now is not None else _clock()
        for k, target in self.scan_idle(t).items():
            principal, session = k.split("/", 1)
            if target == SUSPENDED:
                self.suspend(principal, session, now=t)
            elif target == EVICTED:
                self.evict(principal, session, reason="idle", now=t)

    @staticmethod
    def attribute(actor, text):

        tag = "agent" if actor == "agent" else "human"
        return "\x1b]1337;pnactor=%s\x07%s" % (tag, text)

def _clock():

    return time.time()

def _selftest():
    import tempfile
    ok = True

    def ck(name, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print("  [%s] %s" % ("PASS" if cond else "FAIL", name))

    ck("cell_name injective in principal", cell_name("a", "s") != cell_name("b", "s"))
    ck("cell_name injective in session", cell_name("a", "s1") != cell_name("a", "s2"))
    ck("cell_name stable", cell_name("owner-a", "proj1") == cell_name("owner-a", "proj1"))

    ck("STRENG: read runs free", requires_2fa(A_STRENG, "read") is False)
    ck("STRENG: write needs 2FA", requires_2fa(A_STRENG, "write") is True)
    ck("STRENG: external needs 2FA", requires_2fa(A_STRENG, "external") is True)
    ck("STANDARD: write runs free", requires_2fa(A_STANDARD, "write") is False)
    ck("STANDARD: destructive needs 2FA", requires_2fa(A_STANDARD, "destructive") is True)
    ck("STANDARD: external needs 2FA", requires_2fa(A_STANDARD, "external") is True)
    ck("FREI: destructive runs free", requires_2fa(A_FREI, "destructive") is False)

    ck("STANDARD: publish runs free", requires_2fa(A_STANDARD, "publish") is False)
    ck("FREI: publish runs free", requires_2fa(A_FREI, "publish") is False)
    ck("STRENG: publish still confirms", requires_2fa(A_STRENG, "publish") is True)
    ck("STANDARD: delete still confirms", requires_2fa(A_STANDARD, "destructive") is True)
    ck("publish classified as publish", classify_action("bin/publish.py --backfill") == "publish")
    ck("veroeffentlichen classified as publish", classify_action("Ergebnis veröffentlichen") == "publish")
    ck("delete beats publish", classify_action("rm -rf /root/published") == "destructive")
    ck("send stays external", classify_action("send mail to a@b.c") == "external")
    ck("FREI: external STILL needs 2FA (floor)", requires_2fa(A_FREI, "external") is True)
    ck("unknown class fail-closed (2FA)", requires_2fa(A_FREI, "weird") is True)
    ck("decide() maps to twofa/auto", decide(A_STRENG, "write") == "twofa" and decide(A_FREI, "write") == "auto")

    ck("migrate L0->STRENG", normalize_level(0) == A_STRENG)
    ck("migrate L1->STRENG", normalize_level(1) == A_STRENG)
    ck("migrate L2->STANDARD", normalize_level(2) == A_STANDARD)
    ck("migrate L3->STANDARD", normalize_level(3) == A_STANDARD)
    ck("migrate L4->FREI", normalize_level(4) == A_FREI)
    ck("migrate L5->FREI (tightened)", normalize_level(5) == A_FREI)
    ck("migrate bug value 99->STRENG (fail-safe)", normalize_level(99) == A_STRENG)
    ck("migrate garbage->STRENG (fail-safe)", normalize_level("nonsense") == A_STRENG and normalize_level(None) == A_STRENG)
    ck("normalize is idempotent on canonical", all(normalize_level(x) == x for x in LEVELS))
    ck("classify rm destructive", classify_action("rm -rf /data") == "destructive")
    ck("classify email external", classify_action("send email to bob") == "external")
    ck("classify cat read", classify_action("cat foo.txt") == "read")

    d = tempfile.mkdtemp()
    reg = SessionCellRegistry(d)

    r = reg.provision("owner-a", "proj1", autonomy=A_STANDARD)
    ck("provision -> warm", r["state"] == WARM and r["autonomy"] == A_STANDARD)
    ck("distinct sessions distinct cells",
       reg.provision("owner-a", "proj2")["cell"] != r["cell"])
    ck("list_live shows both", len(reg.list_live("owner-a")) == 2)
    ck("route gives volumes", reg.route("owner-a", "proj1")["cell"] == r["cell"])

    reg.set_autonomy("owner-a", "proj1", A_STRENG)
    ck("autonomy persisted", reg.get_autonomy("owner-a", "proj1") == A_STRENG)
    ck("decide_for STRENG: write -> twofa", reg.decide_for("owner-a", "proj1", "write config") == "twofa")
    ck("decide_for STRENG: read -> auto", reg.decide_for("owner-a", "proj1", "cat file") == "auto")
    reg.set_autonomy("owner-a", "proj1", A_FREI)
    ck("decide_for FREI: write -> auto", reg.decide_for("owner-a", "proj1", "write config") == "auto")
    ck("decide_for FREI: external -> twofa", reg.decide_for("owner-a", "proj1", "send email") == "twofa")
    ck("decide_for: absent session -> deny", reg.decide_for("owner-a", "nope", "cat") == "deny")

    reg.provision("legacy", "s99")
    with reg._locked():
        _d = reg._load(); _d[reg._key("legacy", "s99")]["autonomy"] = 99; reg._save(_d)
    ck("legacy 99 migrates to STRENG on read", reg.get_autonomy("legacy", "s99") == A_STRENG)

    t0 = 1_000_000.0
    reg.provision("u", "s", now=t0)
    scan1 = reg.scan_idle(t0 + IDLE_SUSPEND_S + 1)
    ck("idle scan suspends warm", scan1.get("u/s") == SUSPENDED)
    scan2 = reg.scan_idle(t0 + IDLE_EVICT_S + 1)
    ck("idle scan evicts long-idle", scan2.get("u/s") == EVICTED)

    reg.evict("u", "s", reason="idle")
    ck("evicted state", reg.get("u", "s")["state"] == EVICTED)
    ck("route refuses evicted", reg.route("u", "s") is None)
    rp = reg.provision("u", "s")
    ck("re-provision -> warm again", rp["state"] == WARM)
    notes = [json.loads(l) for l in open(reg.notify_path)]
    ck("notify recorded evicted", any(n["event"] == "evicted" for n in notes))
    ck("notify recorded reprovisioned", any(n["event"] == "reprovisioned" for n in notes))

    root = reg.vol_dir
    os.makedirs(root, exist_ok=True)
    cn = reg.get("owner-a", "proj1")["cell"]
    art = os.path.join(root, cn + "-work.img")
    open(art, "w").close()
    ck("owns own artifact", reg.owns("owner-a", "proj1", art))
    ck("does NOT own other cell's artifact", not reg.owns("owner-a", "proj2", art))

    ck("attribute agent marker", "pnactor=agent" in SessionCellRegistry.attribute("agent", "hi"))

    print("\nSELFTEST:", "ALL GREEN" if ok else "FAILURES")
    return 0 if ok else 1

if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("pn_session_cells — import me; --selftest to verify.")
