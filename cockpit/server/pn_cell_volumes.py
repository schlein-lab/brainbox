#!/usr/bin/env python3

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import time
import tempfile
import time
from pn_cell_basis import WORK_GB

def _delta_zustand(delta):

    try:
        r = subprocess.run(["dumpe2fs", "-h", delta], capture_output=True, text=True, timeout=60)
    except Exception as e:
        return ("nicht lesbar (%s)" % e, False)
    if r.returncode != 0:
        return ("nicht lesbar (dumpe2fs rc=%d)" % r.returncode, False)
    txt = (r.stdout or "") + (r.stderr or "")
    zustand, merkmale, fehler = "", "", 0
    for zeile in txt.splitlines():
        if zeile.startswith("Filesystem state:"):
            zustand = zeile.split(":", 1)[1].strip()
        elif zeile.startswith("Filesystem features:"):
            merkmale = zeile.split(":", 1)[1]
        elif zeile.startswith("FS Error count:"):
            try:
                fehler = int(zeile.split(":", 1)[1].strip())
            except ValueError:
                fehler = 1
    gruende = []
    if zustand != "clean":
        gruende.append("Zustand '%s'" % (zustand or "unbekannt"))
    if "needs_recovery" in merkmale:
        gruende.append("Journal nicht wiedergespielt")
    if fehler:
        gruende.append("%d vermerkte(r) Fehler" % fehler)
    return (", ".join(gruende) or "sauber", not gruende)

FSCK_FRIST_S   = float(os.environ.get("PN_DELTA_FSCK_S") or 900)
FSCK_ERNEUT_S  = float(os.environ.get("PN_DELTA_FSCK_RETRY_S") or 21600)

def _heilung_marke(delta):
    return delta + ".heilung-gescheitert"

def _heilung_gescheitert_kuerzlich(delta):

    m = _heilung_marke(delta)
    try:
        alter = time.time() - os.path.getmtime(m)
    except OSError:
        return False, ""
    if alter >= FSCK_ERNEUT_S:
        return False, ""
    try:
        grund = io.open(m, encoding="utf-8").read().strip()[:300]
    except Exception:
        grund = "(Grund nicht lesbar)"
    return True, "%s (vor %.1f h)" % (grund, alter / 3600.0)

def _heilung_merken(delta, grund):
    try:
        with io.open(_heilung_marke(delta), "w", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), grund))
    except OSError:
        pass

def _heilung_marke_loeschen(delta):
    try:
        os.unlink(_heilung_marke(delta))
    except OSError:
        pass

def _delta_gesund_machen(delta, wer=""):

    if not delta or not os.path.exists(delta):
        return True
    zustand, sauber = _delta_zustand(delta)
    if sauber:
        return True
    schon, grund = _heilung_gescheitert_kuerzlich(delta)
    if schon:

        sys.stderr.write("[delta-heilen] %s: Heilung wurde bereits versucht und scheiterte: %s — "
                         "es wird NICHT erneut blockiert. Die Sitzung startet degradiert; die "
                         "Platte braucht eine Reparatur AUSSERHALB des Zellstarts.\n"
                         % (os.path.basename(delta), grund))
        return False
    sys.stderr.write("[delta-heilen] %s%s: %s — e2fsck laeuft (Frist %.0f s), BEVOR etwas "
                     "geschrieben wird (offline schreiben auf ein offenes Journal zerlegt "
                     "Verzeichnisse)\n"
                     % (wer and (wer + " "), os.path.basename(delta), zustand, FSCK_FRIST_S))
    try:
        r = subprocess.run(["e2fsck", "-fy", delta], capture_output=True, text=True,
                           timeout=FSCK_FRIST_S)
    except Exception as e:
        sys.stderr.write("[delta-heilen] %s: e2fsck nicht ausfuehrbar (%s) — es wird NICHT offline "
                         "geschrieben\n" % (os.path.basename(delta), e))
        _heilung_merken(delta, "e2fsck nicht ausfuehrbar: %s" % e)
        return False

    if r.returncode >= 4:
        sys.stderr.write("[delta-heilen] %s: e2fsck rc=%d — NICHT repariert. %s\n"
                         % (os.path.basename(delta), r.returncode,
                            (r.stdout or "")[-400:].replace("\n", " ")))
        _heilung_merken(delta, "e2fsck rc=%d — nicht repariert" % r.returncode)
        return False
    _heilung_marke_loeschen(delta)
    _z2, sauber2 = _delta_zustand(delta)
    sys.stderr.write("[delta-heilen] %s: repariert (e2fsck rc=%d), Zustand jetzt '%s'\n"
                     % (os.path.basename(delta), r.returncode, _z2))
    return sauber2

def _journal_mb(want_mb):

    return 8 if int(want_mb or 0) >= 1024 else 4

def _prep_delta(delta, mb=None):

    seed = os.urandom(256)
    if not os.path.exists(delta):
        stg = tempfile.mkdtemp(prefix="pn-delta-")
        os.makedirs(os.path.join(stg, "upper")); os.makedirs(os.path.join(stg, "work"))
        with open(os.path.join(stg, "upper", "seed"), "wb") as f:
            f.write(seed)
        subprocess.run(["truncate", "-s", "%dM" % _delta_want_mb(mb), delta], check=True)
        subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q",
                        "-J", "size=%d" % _journal_mb(_delta_want_mb(mb)),
                        "-d", stg, delta], check=True)
        shutil.rmtree(stg, ignore_errors=True)
        return

    _gesund = _delta_gesund_machen(delta, wer="prep")
    want = _delta_want_mb(mb) * (1 << 20)
    if os.path.getsize(delta) < want:

        try:
            fsck = subprocess.run(["e2fsck", "-fy", delta], capture_output=True,
                                  timeout=FSCK_FRIST_S)
        except Exception as _e:
            sys.stderr.write("[delta-grow] %s: e2fsck nicht ausfuehrbar (%s) — nicht gewachsen\n"
                             % (os.path.basename(delta), _e))
            fsck = subprocess.CompletedProcess([], 4, b"", b"")
        if fsck.returncode < 2 and subprocess.run(["truncate", "-s", str(want), delta],
                                                  capture_output=True).returncode == 0:
            r = subprocess.run(["resize2fs", delta], capture_output=True)
            if r.returncode != 0:
                sys.stderr.write("[delta-grow] resize2fs %s failed: %s\n"
                                 % (delta, (r.stderr or b"").decode("utf-8", "replace")[-200:]))
        else:
            sys.stderr.write("[delta-grow] %s not grown (fsck rc=%d)\n" % (delta, fsck.returncode))

    if not _gesund:

        sys.stderr.write("[delta-heilen] %s: Seed wird NICHT aufgefrischt — die Platte ist nicht "
                         "sauber zu bekommen. Die Sitzung startet trotzdem.\n"
                         % os.path.basename(delta))
        return
    sf = delta + ".seed.tmp"
    with open(sf, "wb") as f:
        f.write(seed)
    subprocess.run(["debugfs", "-w", "-R", "rm upper/seed", delta],
                   capture_output=True)
    r = subprocess.run(["debugfs", "-w", "-R", "write %s upper/seed" % sf, delta],
                       capture_output=True, text=True)
    os.unlink(sf)
    if "written" not in (r.stdout + r.stderr).lower() and r.returncode != 0:

        beiseite = "%s.beschaedigt-%s" % (delta, time.strftime("%Y%m%d-%H%M%S"))
        try:
            os.replace(delta, beiseite)
            sys.stderr.write("[delta-heilen] %s: Seed nicht schreibbar (debugfs rc=%d). Das Delta "
                             "wurde BEISEITEGELEGT nach %s — nichts geloescht. Die Sitzung startet "
                             "mit einer frischen Platte; das alte Gedaechtnis liegt daneben.\n"
                             % (os.path.basename(delta), r.returncode, beiseite))
        except OSError as e:
            sys.stderr.write("[delta-heilen] %s: Seed nicht schreibbar UND Beiseitelegen "
                             "fehlgeschlagen (%s) — es wird NICHTS angefasst.\n"
                             % (os.path.basename(delta), e))
            return
        _prep_delta(delta, mb)

def _delta_want_mb(mb):

    try:
        return max(512, min(int(mb or 0) or 512, 16384))
    except (TypeError, ValueError):
        return 512

def _prep_work(work, gb=None):

    want = max(4, min(int(gb or 0) or WORK_GB, 4096)) * (1 << 30)
    if os.path.exists(work):
        if os.path.getsize(work) < want:
            fsck = subprocess.run(["e2fsck", "-fy", work], capture_output=True)
            if fsck.returncode < 2 and subprocess.run(["truncate", "-s", str(want), work],
                                                      capture_output=True).returncode == 0:
                r = subprocess.run(["resize2fs", work], capture_output=True)
                if r.returncode != 0:
                    sys.stderr.write("[work-grow] resize2fs %s failed: %s\n"
                                     % (work, (r.stderr or b"").decode("utf-8", "replace")[-200:]))
            else:
                sys.stderr.write("[work-grow] %s not grown (fsck rc=%d)\n" % (work, fsck.returncode))
        return
    stg = tempfile.mkdtemp(prefix="pn-work-")
    try:
        os.makedirs(os.path.join(stg, "flatpak"))
        subprocess.run(["truncate", "-s", str(want), work], check=True)
        subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q",
                        "-J", "size=%d" % _journal_mb(want // (1 << 20)),
                        "-d", stg, work], check=True)
    finally:
        shutil.rmtree(stg, ignore_errors=True)

def _kill_delta_orphans(delta):

    try:
        me = os.getpid()
        need = ("PN_VMM_BLK=" , delta)
        for pid in os.listdir("/proc"):
            if not pid.isdigit() or int(pid) == me:
                continue
            try:
                environ = open("/proc/%s/environ" % pid, "rb").read().decode("utf-8", "replace")
            except OSError:
                continue
            if delta in environ and "PN_VMM_BLK=" in environ:
                try:
                    os.kill(int(pid), 9)
                except OSError:
                    pass
    except Exception:
        pass
