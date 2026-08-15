#!/usr/bin/env python3

import importlib.util, os, shutil, sys, tempfile, time

spec = importlib.util.spec_from_file_location(
    "na", os.path.join(os.path.dirname(os.path.abspath(__file__)), "pn_node_agentd.py"))
na = importlib.util.module_from_spec(spec)
sys.argv = ["pn_node_agentd.py"]
spec.loader.exec_module(na)

ok = fail = 0
def ck(name, cond):
    global ok, fail
    if cond: ok += 1; print("  ok   " + name)
    else: fail += 1; print("  FAIL " + name)

basis = tempfile.mkdtemp(prefix="archivtest-")
def zelle(tag, name, alter_s, mit_bild=True):
    d = os.path.join(basis, tag, name)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "vmm.out"), "w").write("Gast-Konsole")
    open(os.path.join(d, "cell.json"), "w").write("{}")
    if mit_bild:
        open(os.path.join(d, "delta.img"), "w").write("x" * 100)
    t = time.time() - alter_s
    os.utime(d, (t, t))
    return d

frisch = zelle("20260728", "frisch", 60)
mittel = zelle("20260728", "mittel", 8 * 3600)
alt    = zelle("20260714", "alt", 20 * 86400)

b, e = na._sweep_archive(basis)
ck("frische Zelle bleibt vollstaendig", os.path.exists(os.path.join(frisch, "delta.img")))
ck("nach 8 h ist das Bild weg", not os.path.exists(os.path.join(mittel, "delta.img")))
ck("aber die Gast-Konsole bleibt", os.path.exists(os.path.join(mittel, "vmm.out")))
ck("nach 20 Tagen ist der ganze Eintrag weg", not os.path.exists(alt))
ck("Zaehler stimmen (2 Bilder, 1 Eintrag)", (b, e) == (2, 1))

b2, e2 = na._sweep_archive(basis)
ck("zweiter Lauf raeumt nichts doppelt ab", (b2, e2) == (0, 0))
ck("die frische Zelle ist immer noch da", os.path.exists(os.path.join(frisch, "vmm.out")))

ck("fehlendes Archiv ist kein Fehler", na._sweep_archive(os.path.join(basis, "gibtsnicht")) == (0, 0))

shutil.rmtree(basis, ignore_errors=True)
print("\n%d ok, %d fehlgeschlagen" % (ok, fail))
sys.exit(1 if fail else 0)
