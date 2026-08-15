#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import sys
import time

VOLS = os.path.expanduser("~/.local/share/brainbox-portal/session-cells/session-vols")
STORE = os.path.expanduser("~/.local/share/brainbox-portal/session-cells/session-cells.json")
KARENZ_S = 3600
_SUFFIXE = ("-delta.img", "-work.img", "-keystore.img")

def bekannte_zellen(pfad=None):

    try:
        d = json.load(open(pfad or STORE))
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    ids = set()
    for k, v in d.items():
        ids.add(str(k))
        if isinstance(v, dict):
            for f in ("cell", "cell_id", "id"):
                if v.get(f):
                    ids.add(str(v[f]))

            for f in ("work_vol", "keystore_vol", "delta_vol"):
                p = v.get(f)
                if p:
                    ids.add(zellkennung(os.path.basename(str(p))))
    ids.discard(None)
    return ids

def zellkennung(dateiname):
    for s in _SUFFIXE:
        if dateiname.endswith(s):
            return dateiname[:-len(s)]
    return None

def lebende_zellen():

    lebt = set()
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open("/proc/%s/comm" % pid) as f:
                    if f.read().strip() != "pn-vmm":
                        continue
                with open("/proc/%s/cmdline" % pid, "rb") as f:
                    cl = f.read().decode("utf-8", "replace")
            except OSError:
                continue
            for m in re.finditer(r"(sc-[A-Za-z0-9._-]+?)(?:-delta|-work|-keystore)\.img", cl):
                lebt.add(m.group(1))
    except OSError:
        pass
    return lebt

def belegt_mb(pfad):
    try:
        st = os.lstat(pfad)
        return st.st_blocks * 512 // (1024 * 1024)
    except OSError:
        return 0

PAPIERKORB = ".papierkorb"

def in_den_papierkorb(pfad, vols):

    korb = os.path.join(vols, PAPIERKORB, time.strftime("%Y-%m-%d"))
    try:
        os.makedirs(korb, exist_ok=True)
        ziel = os.path.join(korb, os.path.basename(pfad))
        if os.path.exists(ziel):
            ziel = "%s.%s" % (ziel, time.strftime("%H%M%S"))
        os.replace(pfad, ziel)
        return True
    except OSError:
        return False

def sweep(dry=False, karenz_s=KARENZ_S, vols=None, store=None):
    vols = vols or VOLS
    stats = {"dateien": 0, "bekannt": 0, "lebend": 0, "jung": 0, "entfernt": 0, "mb_frei": 0}
    bekannt = bekannte_zellen(store)
    if bekannt is None:
        stats["fehler"] = "Sitzungs-Speicher unlesbar — nichts angefasst"
        return stats
    lebt = lebende_zellen()
    jetzt = time.time()
    try:
        dateien = sorted(os.listdir(vols))
    except OSError:
        return stats
    for name in dateien:
        if name == PAPIERKORB:
            continue
        z = zellkennung(name)
        if not z:
            continue
        stats["dateien"] += 1
        p = os.path.join(vols, name)
        if z in bekannt:
            stats["bekannt"] += 1
            continue
        if z in lebt:
            stats["lebend"] += 1
            continue
        try:
            if (jetzt - os.path.getmtime(p)) < karenz_s:
                stats["jung"] += 1
                continue
        except OSError:
            continue
        mb = belegt_mb(p)
        if not dry:
            if not in_den_papierkorb(p, vols):
                continue
        stats["entfernt"] += 1
        stats["mb_frei"] += mb
    return stats

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--karenz-min", type=float, default=KARENZ_S / 60.0)
    a = ap.parse_args()
    s = sweep(dry=a.dry, karenz_s=a.karenz_min * 60)
    if s.get("fehler"):
        print("ABBRUCH:", s["fehler"])
        return 1
    print("%sPlatten=%d bekannt=%d lebend=%d jung=%d entfernt=%d (%d MB, %.1f GB)"
          % ("TROCKENLAUF " if a.dry else "", s["dateien"], s["bekannt"], s["lebend"],
             s["jung"], s["entfernt"], s["mb_frei"], s["mb_frei"] / 1024.0))
    return 0

if __name__ == "__main__":
    sys.exit(main())
