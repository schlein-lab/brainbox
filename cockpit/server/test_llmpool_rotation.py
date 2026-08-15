#!/usr/bin/env python3

import json
import os
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import llmpool

NOW = 1_800_000_000.0
HOUR = 3600.0

def _mkhome(root, name, email):
    home = os.path.join(root, name)
    os.makedirs(os.path.join(home, ".claude"), exist_ok=True)
    with open(os.path.join(home, ".claude.json"), "w") as f:
        json.dump({"oauthAccount": {"emailAddress": email, "displayName": name}}, f)
    with open(os.path.join(home, ".claude", ".credentials.json"), "w") as f:
        json.dump({"claudeAiOauth": {"subscriptionType": "max",
                                     "accessToken": "x", "expiresAt": int((NOW + 8 * HOUR) * 1000)}}, f)
    return home

def _usage_row(five, seven, age_h=0.0, fh_reset_h=4.0, sd_reset_h=48.0):

    return {
        "five_hour_pct": five, "seven_day_pct": seven,
        "ts": int(NOW - age_h * HOUR),
        "five_hour_resets_at": NOW + fh_reset_h * HOUR,
        "seven_day_resets_at": NOW + sd_reset_h * HOUR,
    }

def _build(accounts, usage, prefs=None):

    root = tempfile.mkdtemp(prefix="llmpooltest_")
    cfg_accts = []
    for aid, email, enabled in accounts:
        home = _mkhome(root, aid, email)
        cfg_accts.append({"id": aid, "home": home, "enabled": enabled, "provider": "claude"})
    cfg_path = os.path.join(root, "llmpool.json")
    with open(cfg_path, "w") as f:
        json.dump({"accounts": cfg_accts}, f)
    usage_path = os.path.join(root, "usage.json")
    with open(usage_path, "w") as f:
        json.dump(usage, f)
    if prefs is not None:
        with open(cfg_path + ".prefs.json", "w") as f:
            json.dump(prefs, f)
    state_path = os.path.join(root, "state.json")
    llmpool._INFO_CACHE.clear()
    pool = llmpool.LLMPool(cfg_path, state_path, root, usage_path=usage_path)

    clk = [NOW]
    pool._now = lambda: clk[0]
    pool._test_clk = clk
    return pool, root

def _distribution(pool, n=60):

    counts = {}
    clk = getattr(pool, "_test_clk", None)
    for _ in range(n):
        a = pool.pick()
        counts[a["id"]] = counts.get(a["id"], 0) + 1
        pool.record(a["id"], ok=True)
        if clk is not None:
            clk[0] += 1.0
    return counts

PASS, FAIL = [], []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok " if cond else "FAIL ") + name + (("  — " + detail) if detail else ""))

def t1_seven_day_blindspot():
    print("[1] 7d-Erschoepfung meiden (5h niedrig, 7d hoch)")
    pool, root = _build(
        [("full7d", "full@x", True), ("fresh", "fresh@x", True)],
        {"full@x": _usage_row(3, 97), "fresh@x": _usage_row(7, 5)},
        prefs={"switch_pct": 90})
    try:
        counts = _distribution(pool, 40)

        check("7d=97%% wird vollstaendig gemieden", counts.get("full7d", 0) == 0,
              "counts=%s" % counts)
        check("frisches Konto traegt die Last", counts.get("fresh", 0) == 40, "counts=%s" % counts)
    finally:
        shutil.rmtree(root, ignore_errors=True)

def t2_headroom_spread():
    print("[2] Least-loaded-first ueber 3 gesunde Konten")
    pool, root = _build(
        [("a10", "a@x", True), ("b40", "b@x", True), ("c80", "c@x", True)],
        {"a@x": _usage_row(10, 10), "b@x": _usage_row(40, 40), "c@x": _usage_row(80, 80)},
        prefs={"switch_pct": 0})
    try:
        counts = _distribution(pool, 60)

        check("least-loaded (a10) bekommt die Last", counts.get("a10", 0) == 60,
              "counts=%s" % counts)
        check("staerker ausgelastete werden zurueckgestuft",
              counts.get("b40", 0) == 0 and counts.get("c80", 0) == 0, "counts=%s" % counts)
    finally:
        shutil.rmtree(root, ignore_errors=True)

def t2b_same_band_spread():
    print("[2b] Gleiche Auslastung -> Streuung per inflight/LRU")
    pool, root = _build(
        [("a", "a@x", True), ("b", "b@x", True), ("c", "c@x", True)],
        {"a@x": _usage_row(15, 15), "b@x": _usage_row(15, 15), "c@x": _usage_row(15, 15)},
        prefs={"switch_pct": 0})
    try:
        counts = _distribution(pool, 60)

        spread = min(counts.values()) if len(counts) == 3 else 0
        check("alle 3 Konten werden genutzt", len(counts) == 3, "counts=%s" % counts)
        check("Verteilung ist ausgeglichen (jeder >=15/60)", spread >= 15, "counts=%s" % counts)
    finally:
        shutil.rmtree(root, ignore_errors=True)

def t3_no_usage_graceful():
    print("[3] Fehlende Auslastung -> graceful round-robin (getesteter Alt-Pfad)")
    pool, root = _build(
        [("a", "a@x", True), ("b", "b@x", True)],
        {},
        prefs={"switch_pct": 90})
    try:
        counts = _distribution(pool, 40)
        check("kein Konto verhungert bei unbekannter Auslastung",
              counts.get("a", 0) > 0 and counts.get("b", 0) > 0, "counts=%s" % counts)
        check("Verteilung ~ausgeglichen (round-robin)", abs(counts.get("a", 0) - counts.get("b", 0)) <= 2,
              "counts=%s" % counts)
    finally:
        shutil.rmtree(root, ignore_errors=True)

def t4_stale_not_trusted():
    print("[4] Stale (>6h) -> als unbekannt behandelt, nicht faelschlich gemieden")
    pool, root = _build(
        [("stale97", "s@x", True), ("fresh50", "f@x", True)],

        {"s@x": _usage_row(2, 97, age_h=10.0), "f@x": _usage_row(50, 50, age_h=0.0)},
        prefs={"switch_pct": 90})
    try:

        up = pool._usage_pct("stale97")
        check("stale Auslastung -> None (unbekannt)", up is None, "got %r" % up)

        check("frische Auslastung -> Zahl", pool._usage_pct("fresh50") == 50.0,
              "got %r" % pool._usage_pct("fresh50"))
    finally:
        shutil.rmtree(root, ignore_errors=True)

def t5_all_over_threshold_escape():
    print("[5] Alle ueber Schwelle -> pick liefert trotzdem eins (kein hartes Nein)")
    pool, root = _build(
        [("x", "x@x", True), ("y", "y@x", True)],
        {"x@x": _usage_row(95, 95), "y@x": _usage_row(92, 96)},
        prefs={"switch_pct": 90})
    try:
        a = pool.pick()
        check("pick liefert ein Konto obwohl beide >Schwelle", a is not None,
              "got %r" % a)
        if a:
            pool.record(a["id"], ok=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)

def t6_default_switch_on():
    print("[6] Ohne Pref: Default-Soft-Avoid AN")
    pool, root = _build(
        [("full", "full@x", True), ("fresh", "fresh@x", True)],
        {"full@x": _usage_row(3, 95), "fresh@x": _usage_row(5, 5)},
        prefs=None)
    try:
        counts = _distribution(pool, 30)
        check("Default-Avoid meidet 7d=95%%", counts.get("full", 0) == 0, "counts=%s" % counts)
    finally:
        shutil.rmtree(root, ignore_errors=True)

if __name__ == "__main__":
    for t in (t1_seven_day_blindspot, t2_headroom_spread, t2b_same_band_spread,
              t3_no_usage_graceful, t4_stale_not_trusted, t5_all_over_threshold_escape,
              t6_default_switch_on):
        t()
    print("\n== %d PASS, %d FAIL ==" % (len(PASS), len(FAIL)))
    sys.exit(1 if FAIL else 0)
