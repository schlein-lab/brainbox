#!/usr/bin/env python3

import io
import os

_ERKLAERUNG = (
    "                 Ohne delegierte cgroup2-Schichten lehnt pnd jeden Auftrag mit rc126\n"
    "                 ab -- das sagt nichts ueber die geprueften Regeln. Auf der Appliance\n"
    "                 ist die Voraussetzung gegeben (pn-init schneidet die Schichten selbst,\n"
    "                 und pnd laeuft dort als Dienst). Auf einem Entwicklungsrechner:\n"
    "                 als root laufen lassen, oder in einer Sitzung mit delegierten\n"
    "                 Controllern (bei systemd: systemd-run --user --scope -p Delegate=yes).\n"
)

def cgroup_delegiert():

    try:
        with io.open("/proc/self/cgroup", encoding="utf-8") as f:
            zweig = f.read().strip().split(":")[-1] or "/"
    except OSError:
        return False, "/proc/self/cgroup ist nicht lesbar (kein cgroup2?)"
    basis = os.path.join("/sys/fs/cgroup", zweig.lstrip("/"))
    if not os.path.isdir(basis):
        return False, "cgroup2 ist nicht eingehaengt (%s fehlt)" % basis
    if not os.access(os.path.join(basis, "cgroup.procs"), os.W_OK):
        return False, "cgroup.procs in %s ist nicht beschreibbar" % basis
    probe = os.path.join(basis, "pn-probe-%d" % os.getpid())
    try:
        os.mkdir(probe)
    except OSError as e:
        return False, "keine eigene Unterschicht anlegbar in %s (%s)" % (basis, e)
    try:
        os.rmdir(probe)
    except OSError:
        pass
    return True, ""

def live_moeglich(zweck="Live-Block"):

    moeglich, grund = cgroup_delegiert()
    if moeglich:
        return True
    print("  UEBERSPRUNGEN  %s braucht ein lauffaehiges eigenes pnd: %s" % (zweck, grund))
    print(_ERKLAERUNG, end="")
    return False
