#!/usr/bin/env python3

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import portal_placement as pp

ok = fail = 0

def ck(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print("  ok   %s" % name)
    else:
        fail += 1
        print("  FAIL %s" % name)

def knoten(nid, mem_total, disk_free, mem_avail=None, rc=0, arch="aarch64", local=False):
    return {"id": nid, "state": "online", "arch": arch, "local": local,
            "caps": {"cells": True, "cell_base_staged": True},
            "res": {"mem_total_mb": mem_total, "disk_free_mb": disk_free,
                    "mem_avail_mb": mem_total if mem_avail is None else mem_avail,
                    "load1": 0.2, "nproc": 4, "running_cells": rc}}

ck("Annahme unveraendert: 2 GB RAM je Zelle (gemessen: 1.536 MB gefordert)"
   "  [sonst die Erwartungen unten neu ableiten]",
   pp._CAP_MEM_PER_CELL_MB == 2 * 1024)
ck("Annahme unveraendert: 6 GB Platte je Zelle (gemessen: 5.120 MB Deckel in cell.json)"
   "  [sonst die Erwartungen unten neu ableiten]",
   pp._CAP_DISK_PER_CELL_MB == 6 * 1024)
ck("Obergrenze unveraendert: hoechstens 8 Zellen je Knoten",
   pp._CAP_MAX == 8)

ck("kleiner x86-Knoten (5,8 GB RAM, 624 GB Platte): ZWEI Zellen -- der Speicher begrenzt",
   pp._node_cell_cap(knoten("knoten-klein", 5807, 638385, arch="x86_64")) == 2)
ck("Pi mit SD-Karte (16 GB RAM, aber nur 17 GB frei): ZWEI -- die Karte begrenzt, nicht der Speicher",
   pp._node_cell_cap(knoten("pi2", 15969, 17382)) == 2)
ck("Pi mit NVMe (16 GB RAM, 418 GB): SIEBEN -- hier begrenzt wieder der Speicher",
   pp._node_cell_cap(knoten("pi1", 15973, 418267)) == 7)
ck("Deckel ist nie 0 (eine Zelle muss immer gehen)",
   pp._node_cell_cap(knoten("winzig", 900, 3000)) == 1)
ck("Deckel ist gedeckelt (dicke Maschine landet auf der Obergrenze)",
   pp._node_cell_cap(knoten("dick", 512000, 9000000)) == 8)
ck("ohne Ressourcen-Sicht kein Deckel",
   pp._node_cell_cap({"id": "leer", "res": {}}) is None)

os.environ["PN_NODE_CELL_CAP_PI2"] = "5"
ck("je-Knoten-Umgebungsvariable schlaegt die Ableitung (5 statt abgeleiteter 2)",
   pp._node_cell_cap(knoten("pi2", 15969, 17382)) == 5)
del os.environ["PN_NODE_CELL_CAP_PI2"]

NEED = 1536
klein_voll = knoten("knoten-klein", 5807, 638385, mem_avail=5000, rc=2, arch="x86_64")
frei = knoten("pi1", 15973, 418267, mem_avail=12768, rc=1)
ck("der ausgeschoepfte schwache Knoten wird NICHT gewaehlt",
   pp._pick_node_core([klein_voll], NEED, arch_pref="x86_64") is None)
ck("stattdessen faellt die Wahl auf den tragfaehigen Knoten",
   pp._pick_node_core([klein_voll, frei], NEED) == "pi1")

ck("VORFALL 28.07., korrigiert: die dritte Zelle auf dem schwachen Knoten wird abgelehnt",
   pp._pick_node_core([knoten("knoten-klein", 5807, 638385, mem_avail=5000, rc=2, arch="x86_64")],
                      NEED, arch_pref="x86_64") is None)
ck("die zweite Zelle darf er weiterhin bekommen (das war die zu enge Sperre)",
   pp._pick_node_core([knoten("knoten-klein", 5807, 638385, mem_avail=5000, rc=1, arch="x86_64")],
                      NEED, arch_pref="x86_64") == "knoten-klein")
ck("der NVMe-Pi nimmt bis zu sieben, die achte nicht",
   pp._pick_node_core([knoten("pi1", 15973, 418267, mem_avail=12768, rc=6)], NEED) == "pi1"
   and pp._pick_node_core([knoten("pi1", 15973, 418267, mem_avail=12768, rc=7)], NEED) is None)

ck("ist alles voll, wird nichts platziert (Aufgabe wartet)",
   pp._pick_node_core([knoten("knoten-klein", 5807, 638385, mem_avail=5000, rc=2, arch="x86_64"),
                       knoten("pi2", 15969, 17382, mem_avail=11202, rc=2)], NEED) is None)

print("\n%d ok, %d fehlgeschlagen" % (ok, fail))
sys.exit(1 if fail else 0)
