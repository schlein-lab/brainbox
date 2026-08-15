#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
import py_compile
import shutil
import subprocess
import sys
import time

SCHEMA = 1
BEHALTEN_N = 5
ARCHIV_ALTER_S = 7 * 86400

WURZELN = ("cockpit/server", "engine/pnlib", "engine/relaylib", "os/pn-vmm/vendor")
ENV_AKTIV = "PN_PORTAL_RELEASE_DIR"
ENV_SCHALTER = "PN_PORTAL_RELEASE"
ENV_FALLBACK = "PN_PORTAL_RELEASE_FALLBACK"

_AUSSCHLUSS_TEILE = (".bak", ".pn-bak")
_AUSSCHLUSS_NAMEN = ("__pycache__", ".git")
_AUSSCHLUSS_ENDUNGEN = (".pyc", ".pyo")

def basis():
    return os.path.join(os.path.expanduser("~"), ".local", "lib", "brainbox-portal")

def releases_dir():
    return os.path.join(basis(), "releases")

def archiv_dir():
    return os.path.join(basis(), "archiv")

def current_link():
    return os.path.join(basis(), "current")

def _daten_log():
    return os.path.join(os.path.expanduser("~"), ".local", "share",
                        "brainbox-portal", "portal.console.log")

def _log(zeile):
    try:
        pfad = _daten_log()
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        with open(pfad, "a", encoding="utf-8", errors="replace") as f:
            f.write("%s [portal-release] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), zeile))
    except Exception:
        pass

def laut(kopf, detail=""):

    balken = "#" * 78
    _log(balken)
    _log("##  %s" % kopf)
    for z in (detail or "").splitlines():
        _log("##  " + z)
    _log("##  FOLGE: das Portal serviert jetzt aus dem LEBENDEN WORKTREE (altes")
    _log("##  Verhalten). Editier-Hunks gehen wieder SOFORT an Nutzer — bitte den")
    _log("##  Release-Mechanismus reparieren. Notausstieg-Doku: docs/portal-release-serving.md")
    _log(balken)

def _ausgeschlossen(name):
    if name in _AUSSCHLUSS_NAMEN:
        return True
    if any(t in name for t in _AUSSCHLUSS_TEILE):
        return True
    return name.endswith(_AUSSCHLUSS_ENDUNGEN)

def _dateiliste(wurzel):

    ergebnis = []
    for verz, dirs, dateien in os.walk(wurzel, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not _ausgeschlossen(d))
        for d in sorted(dateien):
            if _ausgeschlossen(d):
                continue
            ergebnis.append(os.path.relpath(os.path.join(verz, d), wurzel))
    return ergebnis

def _vermessen(wurzel, liste=None):
    liste = _dateiliste(wurzel) if liste is None else liste
    gesamt = 0
    for rel in liste:
        try:
            gesamt += os.path.getsize(os.path.join(wurzel, rel))
        except OSError:
            pass
    return len(liste), gesamt

def _git_info(repo):

    info = {"rev": "", "kurz": "nogit", "zeit": 0, "betreff": "", "dirty": False,
            "dirty_dateien": []}
    try:
        out = subprocess.run(["git", "-C", repo, "log", "-1", "--pretty=%H %ct %s"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        if out:
            rev, ts, betreff = (out.split(" ", 2) + ["", ""])[:3]
            info.update(rev=rev, kurz=rev[:12], zeit=int(ts or 0), betreff=betreff)
        porc = subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                              capture_output=True, text=True, timeout=10).stdout
        for zeile in porc.splitlines():
            pfad = zeile[3:].strip().strip('"')
            if " -> " in pfad:
                pfad = pfad.split(" -> ", 1)[1].strip('"')
            if not any(pfad == w or pfad.startswith(w + "/") for w in WURZELN):
                continue
            eintrag = {"pfad": pfad, "status": zeile[:2].strip()}
            voll = os.path.join(repo, pfad)
            try:
                with open(voll, "rb") as f:
                    inhalt = f.read()
                eintrag["sha1"] = hashlib.sha1(inhalt).hexdigest()
                eintrag["bytes"] = len(inhalt)
            except OSError:
                eintrag["sha1"] = None
            info["dirty_dateien"].append(eintrag)
        info["dirty"] = bool(info["dirty_dateien"])
    except Exception as e:
        info["fehler"] = "%s: %s" % (type(e).__name__, e)
    return info

def _kopieren(repo, ziel):

    zaehler = {"dateien": 0, "bytes": 0, "wurzeln": []}
    for wurzel in WURZELN:
        quelle = os.path.join(repo, wurzel)
        if not os.path.isdir(quelle):
            if wurzel == "cockpit/server":
                raise RuntimeError("Code-Wurzel fehlt: %s" % quelle)
            zaehler["wurzeln"].append({"wurzel": wurzel, "fehlt": True})
            continue
        liste = _dateiliste(quelle)
        n, b = _vermessen(quelle, liste)
        for rel in liste:
            q = os.path.join(quelle, rel)
            z = os.path.join(ziel, wurzel, rel)
            os.makedirs(os.path.dirname(z), exist_ok=True)
            shutil.copy2(q, z)
        zaehler["wurzeln"].append({"wurzel": wurzel, "dateien": n, "bytes": b})
        zaehler["dateien"] += n
        zaehler["bytes"] += b
    return zaehler

def _pruefen(repo, ziel, quelle_zaehler):

    ziel_dateien = 0
    ziel_bytes = 0
    for wurzel in WURZELN:
        zw = os.path.join(ziel, wurzel)
        if not os.path.isdir(zw):
            continue
        n, b = _vermessen(zw)
        ziel_dateien += n
        ziel_bytes += b
    if (ziel_dateien, ziel_bytes) != (quelle_zaehler["dateien"], quelle_zaehler["bytes"]):
        raise RuntimeError("Kopie unvollständig: Quelle %d Dateien/%d B, Ziel %d Dateien/%d B"
                           % (quelle_zaehler["dateien"], quelle_zaehler["bytes"],
                              ziel_dateien, ziel_bytes))
    fehler = []
    geprueft = 0
    for verz, dirs, dateien in os.walk(ziel):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for d in dateien:
            if not d.endswith(".py"):
                continue
            voll = os.path.join(verz, d)
            geprueft += 1
            try:
                py_compile.compile(voll, doraise=True, quiet=2)
            except Exception as e:
                fehler.append("%s: %s" % (os.path.relpath(voll, ziel), e))

    launcher = os.path.join(ziel, "cockpit", "server", "brainbox-portal")
    if os.path.exists(launcher):
        geprueft += 1
        try:
            py_compile.compile(launcher, cfile=launcher + ".pyc-pruefung", doraise=True, quiet=2)
            os.unlink(launcher + ".pyc-pruefung")
        except py_compile.PyCompileError as e:
            fehler.append("brainbox-portal: %s" % e)
    if fehler:
        raise RuntimeError("py_compile schlug fehl (%d/%d):\n" % (len(fehler), geprueft)
                           + "\n".join(fehler[:20]))
    return {"dateien_ziel": ziel_dateien, "bytes_ziel": ziel_bytes,
            "py_compile_geprueft": geprueft}

def _sandbox_wache():

    b = os.path.realpath(basis())
    heim = os.path.realpath(os.path.expanduser("~"))
    if not b.startswith(heim + os.sep):
        raise RuntimeError("Release-Basis %r liegt nicht unter $HOME %r — Abbruch" % (b, heim))
    if not b.endswith(os.path.join(".local", "lib", "brainbox-portal")):
        raise RuntimeError("Release-Basis %r hat nicht den erwarteten Pfad — Abbruch" % b)
    return b

def build_release(repo, git=None):

    _sandbox_wache()
    t0 = time.time()
    if git is None:
        git = _git_info(repo)
    rid = "%s-%s%s" % (time.strftime("%Y%m%d-%H%M%S"), git["kurz"],
                       "-dirty" if git["dirty"] else "")
    os.makedirs(releases_dir(), exist_ok=True)

    quell_dateien = quell_bytes = 0
    for w in WURZELN:
        p = os.path.join(repo, w)
        if os.path.isdir(p):
            n, b = _vermessen(p)
            quell_dateien += n
            quell_bytes += b
    st = os.statvfs(releases_dir())
    frei = st.f_bavail * st.f_frsize
    if frei < quell_bytes * 3 + 50 * 1024 * 1024:
        raise RuntimeError("zu wenig Platz: %d MB frei, brauche konservativ %d MB"
                           % (frei // 2**20, (quell_bytes * 3) // 2**20 + 50))
    bau = os.path.join(releases_dir(), ".bau-%d-%s" % (os.getpid(), rid))
    try:
        zaehler = _kopieren(repo, bau)
        pruefung = _pruefen(repo, bau, zaehler)
        manifest = {
            "schema": SCHEMA, "id": rid,
            "zeit_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "zeit_epoch": int(t0),
            "quelle": repo, "git": git, "kopie": zaehler, "pruefung": pruefung,
            "dauer_s": round(time.time() - t0, 2),
            "erbauer": "portal_release schema=%d" % SCHEMA,
            "python": sys.version.split()[0], "pid": os.getpid(),
        }
        with open(os.path.join(bau, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        final = os.path.join(releases_dir(), rid)
        if os.path.exists(final):
            final = "%s-p%d" % (final, os.getpid())
            manifest["id"] = os.path.basename(final)
        os.rename(bau, final)
        return final, manifest
    except Exception:

        try:
            os.makedirs(archiv_dir(), exist_ok=True)
            shutil.move(bau, os.path.join(archiv_dir(), "fehlschlag-" + os.path.basename(bau)))
        except Exception:
            pass
        raise

def _flip_current(release_dir):

    ziel_rel = os.path.join("releases", os.path.basename(release_dir))
    tmp = os.path.join(basis(), ".current-neu-%d" % os.getpid())
    try:
        os.unlink(tmp)
    except OSError:
        pass
    os.symlink(ziel_rel, tmp)
    os.replace(tmp, current_link())

def aufraeumen(jetzt=None):

    _sandbox_wache()
    jetzt = time.time() if jetzt is None else jetzt
    bericht = {"behalten": [], "archiviert": [], "uebersprungen": []}
    rdir = releases_dir()
    if not os.path.isdir(rdir):
        return bericht
    try:
        current_ziel = os.path.realpath(current_link())
    except OSError:
        current_ziel = ""
    eintraege = sorted((e for e in os.listdir(rdir) if not e.startswith(".bau-")),
                       reverse=True)
    bau_reste = [e for e in os.listdir(rdir) if e.startswith(".bau-")]
    for i, name in enumerate(eintraege):
        voll = os.path.join(rdir, name)
        if i < BEHALTEN_N or os.path.realpath(voll) == current_ziel:
            bericht["behalten"].append(name)
            continue
        try:
            alter = jetzt - os.stat(voll).st_mtime
        except OSError:
            bericht["uebersprungen"].append(name)
            continue
        if alter <= ARCHIV_ALTER_S:
            bericht["behalten"].append(name)
            continue
        try:
            os.makedirs(archiv_dir(), exist_ok=True)
            shutil.move(voll, os.path.join(archiv_dir(), name))
            bericht["archiviert"].append(name)
        except Exception as e:
            bericht["uebersprungen"].append("%s (%s)" % (name, e))
    for name in bau_reste:
        voll = os.path.join(rdir, name)
        try:
            if jetzt - os.stat(voll).st_mtime > ARCHIV_ALTER_S:
                os.makedirs(archiv_dir(), exist_ok=True)
                shutil.move(voll, os.path.join(archiv_dir(), "fehlschlag-" + name))
                bericht["archiviert"].append("fehlschlag-" + name)
        except Exception as e:
            bericht["uebersprungen"].append("%s (%s)" % (name, e))
    return bericht

def serve_bootstrap(launcher_realpath):

    try:
        if os.environ.get(ENV_SCHALTER, "1") in ("0", "false", "no"):
            _log("Notausstieg PN_PORTAL_RELEASE=0 — Worktree-Serving wie früher.")
            return
        if os.environ.get(ENV_AKTIV):
            return
        b = os.path.realpath(basis())
        if os.path.realpath(launcher_realpath).startswith(b + os.sep):
            _log("Launcher liegt bereits unter %s — kein erneuter Build." % b)
            return
        repo = os.path.dirname(os.path.dirname(os.path.dirname(launcher_realpath)))
        release_dir, manifest = build_release(repo)
        _flip_current(release_dir)
        bericht = aufraeumen()
        _log("Release %s gebaut in %.2f s (%d Dateien, %.1f MB, py_compile %d ok)"
             % (manifest["id"], manifest["dauer_s"], manifest["kopie"]["dateien"],
                manifest["kopie"]["bytes"] / 2**20,
                manifest["pruefung"]["py_compile_geprueft"]))
        if manifest["git"]["dirty"]:
            _log("ACHTUNG: Worktree war dirty — %d Datei(en) im Manifest festgehalten."
                 % len(manifest["git"]["dirty_dateien"]))
        if bericht["archiviert"]:
            _log("Aufbewahrung: %s → Archiv." % ", ".join(bericht["archiviert"]))
        neu = os.path.join(release_dir, "cockpit", "server", "brainbox-portal")
        env = dict(os.environ)
        env[ENV_AKTIV] = release_dir
        if os.path.isdir(os.path.join(release_dir, "engine", "pnlib")):
            env.setdefault("PNLIB_HOME", os.path.join(release_dir, "engine"))
        _log("exec → %s (PID bleibt %d, pn-init sieht denselben Prozess)"
             % (neu, os.getpid()))
        os.execve(sys.executable or "/usr/bin/python3",
                  [sys.executable or "/usr/bin/python3", neu] + sys.argv[1:], env)
    except Exception as e:
        import traceback
        os.environ[ENV_FALLBACK] = "%s: %s" % (type(e).__name__, e)
        laut("SNAPSHOT FEHLGESCHLAGEN — SERVIERE AUS DEM LEBENDEN WORKTREE",
             traceback.format_exc())
        return

def active_info():

    try:
        d = os.environ.get(ENV_AKTIV)
        if not d:
            info = {"modus": "worktree"}
            if os.environ.get(ENV_SCHALTER, "1") in ("0", "false", "no"):
                info["grund"] = "PN_PORTAL_RELEASE=0 (Notausstieg)"
            elif os.environ.get(ENV_FALLBACK):
                info["grund"] = "FALLBACK: " + os.environ[ENV_FALLBACK]
            return info
        man = {}
        try:
            with open(os.path.join(d, "manifest.json"), encoding="utf-8") as f:
                man = json.load(f)
        except Exception:
            pass
        git = man.get("git") or {}
        return {"modus": "release", "id": man.get("id") or os.path.basename(d),
                "verzeichnis": d, "git_rev": git.get("rev", ""),
                "git_betreff": git.get("betreff", ""), "git_zeit": git.get("zeit", 0),
                "dirty": bool(git.get("dirty")), "zeit": man.get("zeit_iso", "")}
    except Exception:
        return {"modus": "unbekannt"}
