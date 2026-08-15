#!/usr/bin/env python3

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

import portal_release

PUBKEY = "RWSfWcMl+uO9WlJ/fBFneX5VgKf/xRicAqbo6ODQLlQxxoEoubL87MV4"
CHANNEL = os.environ.get("PN_UPDATE_URL", "https://get.brainarbeit.com/updates/portal").rstrip("/")
WURZELN = portal_release.WURZELN

def _data_dir():
    d = os.path.join(os.path.expanduser("~"), ".local", "share", "brainbox-portal")
    os.makedirs(d, exist_ok=True)
    return d

def _state_path():
    return os.path.join(_data_dir(), "update-state.json")

def _progress_path():
    return os.path.join(_data_dir(), "update-progress.json")

def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def _note(state, phase, msg=""):
    p = {"state": state, "phase": phase, "msg": msg, "t": int(time.time())}
    _save(_progress_path(), p)
    return p

def _http_get(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": "brainbox-update/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def current_version():
    st = _load(_state_path(), {})
    if st.get("version"):
        return st["version"]
    try:
        cur = os.path.realpath(portal_release.current_link())
        man = _load(os.path.join(cur, "manifest.json"), {})
        return (man.get("git") or {}).get("kurz") or ""
    except Exception:
        return ""

def _minisign_bin():
    for p in ("/usr/bin/minisign", "/usr/local/bin/minisign"):
        if os.path.exists(p):
            return p
    return shutil.which("minisign")

def _verify_manifest(manifest_bytes, sig_text):
    mb = _minisign_bin()
    with tempfile.TemporaryDirectory() as td:
        mf = os.path.join(td, "latest.json")
        sf = os.path.join(td, "latest.json.minisig")
        with open(mf, "wb") as f:
            f.write(manifest_bytes)
        with open(sf, "w", encoding="utf-8") as f:
            f.write(sig_text if sig_text.endswith("\n") else sig_text + "\n")
        if mb:
            r = subprocess.run([mb, "-V", "-m", mf, "-x", sf, "-P", PUBKEY],
                               capture_output=True, text=True)
            return r.returncode == 0, (r.stderr or r.stdout or "").strip()
    return _verify_py(manifest_bytes, sig_text)

def _verify_py(message, sig_text):
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception as e:
        return False, "kein Verifizierer verfuegbar (minisign fehlt, cryptography fehlt): %s" % e
    try:
        pub_raw = base64.b64decode(PUBKEY.strip().splitlines()[-1])
        key = Ed25519PublicKey.from_public_bytes(pub_raw[10:42])
        lines = [l for l in sig_text.splitlines() if l.strip()]
        sig_b64 = None
        for l in lines:
            if l.startswith("untrusted comment:") or l.startswith("trusted comment:"):
                continue
            if sig_b64 is None:
                sig_b64 = l.strip()
                break
        raw = base64.b64decode(sig_b64)
        algo, sig = raw[:2], raw[10:74]
        if algo == b"ED":
            h = hashlib.blake2b(message, digest_size=64).digest()
            key.verify(sig, h)
        else:
            key.verify(sig, message)
        return True, ""
    except Exception as e:
        return False, "Signatur ungueltig: %s" % (str(e)[:160])

def check(timeout=20):
    try:
        mb = _http_get(CHANNEL + "/latest.json", timeout)
        sg = _http_get(CHANNEL + "/latest.json.minisig", timeout).decode("utf-8", "replace")
    except Exception as e:
        return {"ok": False, "available": False, "error": "Kanal nicht erreichbar: %s" % (str(e)[:140])}
    ok, why = _verify_manifest(mb, sg)
    if not ok:
        return {"ok": False, "available": False, "error": "Signatur nicht verifizierbar: %s" % why}
    try:
        man = json.loads(mb)
    except Exception as e:
        return {"ok": False, "available": False, "error": "Manifest defekt: %s" % e}
    cur = current_version()
    avail = bool(man.get("sha") and man.get("sha") != cur)
    return {"ok": True, "available": avail, "current": cur, "latest": man.get("sha"),
            "version": man.get("version") or man.get("sha"), "notes": man.get("notes") or "",
            "built": man.get("built") or "", "manifest": man}

def _safe_extract(tar_path, dest):
    with tarfile.open(tar_path, "r:gz") as tf:
        base = os.path.realpath(dest)
        for m in tf.getmembers():
            target = os.path.realpath(os.path.join(dest, m.name))
            if not (target == base or target.startswith(base + os.sep)):
                raise RuntimeError("unsicherer Pfad im Bundle: %s" % m.name)
            if m.issym() or m.islnk():
                lt = os.path.realpath(os.path.join(os.path.dirname(target), m.linkname))
                if not (lt == base or lt.startswith(base + os.sep)):
                    raise RuntimeError("unsicherer Link im Bundle: %s" % m.name)
        tf.extractall(dest)

def apply(timeout=120, restart=True):
    _note("running", "pruefe", "Kanal + Signatur")
    info = check(timeout=timeout)
    if not info.get("ok"):
        _note("error", "pruefe", info.get("error", ""))
        return {"ok": False, "error": info.get("error")}
    if not info.get("available"):
        _note("idle", "aktuell", "bereits aktuell (%s)" % info.get("current"))
        return {"ok": True, "applied": False, "reason": "bereits aktuell", "version": info.get("current")}
    man = info["manifest"]
    fname = man.get("file")
    want_hash = (man.get("sha256") or "").lower()
    if not fname or not want_hash:
        _note("error", "pruefe", "Manifest unvollstaendig")
        return {"ok": False, "error": "Manifest ohne file/sha256"}
    with tempfile.TemporaryDirectory() as td:
        _note("running", "lade", fname)
        try:
            blob = _http_get(CHANNEL + "/" + fname, timeout=max(timeout, 300))
        except Exception as e:
            _note("error", "lade", str(e)[:140])
            return {"ok": False, "error": "Download fehlgeschlagen: %s" % (str(e)[:140])}
        got = hashlib.sha256(blob).hexdigest()
        if got != want_hash:
            _note("error", "pruefe", "Hash weicht ab")
            return {"ok": False, "error": "Bundle-Hash weicht ab (erwartet %s, ist %s)" % (want_hash[:12], got[:12])}
        tarp = os.path.join(td, fname)
        with open(tarp, "wb") as f:
            f.write(blob)
        _note("running", "entpacke", "")
        tree = os.path.join(td, "tree")
        os.makedirs(tree, exist_ok=True)
        try:
            _safe_extract(tarp, tree)
        except Exception as e:
            _note("error", "entpacke", str(e)[:140])
            return {"ok": False, "error": "Entpacken fehlgeschlagen: %s" % (str(e)[:140])}
        missing = [w for w in WURZELN if not os.path.isdir(os.path.join(tree, w))]
        if any(not os.path.isdir(os.path.join(tree, w)) for w in ("cockpit/server", "engine/pnlib")):
            _note("error", "entpacke", "Kernbaum fehlt: %s" % missing)
            return {"ok": False, "error": "Bundle unvollstaendig: %s fehlt" % missing}
        prev = os.path.realpath(portal_release.current_link())
        prev_ver = current_version()
        _note("running", "installiere", man.get("sha", ""))
        try:
            git = {"kurz": man.get("sha") or "update", "voll": man.get("sha") or "",
                   "dirty": False, "zweig": "release", "quelle": "update-kanal"}
            final, rel_man = portal_release.build_release(tree, git=git)
        except Exception as e:
            _note("error", "installiere", str(e)[:160])
            return {"ok": False, "error": "build_release: %s" % (str(e)[:160])}
        try:
            portal_release._flip_current(final)
        except Exception as e:
            _note("error", "umschalten", str(e)[:160])
            return {"ok": False, "error": "flip: %s" % (str(e)[:160])}
        _save(_state_path(), {"version": man.get("sha"), "from": prev_ver,
                              "prev_dir": prev, "applied": int(time.time()),
                              "notes": man.get("notes") or ""})
        try:
            portal_release.aufraeumen()
        except Exception:
            pass
        _note("running", "neustart", "sicherer Neustart")
        restarted = _safe_restart() if restart else False
        _note("done", "fertig", "auf %s aktualisiert" % man.get("sha"))
        return {"ok": True, "applied": True, "version": man.get("sha"),
                "from": prev_ver, "restart": restarted, "prev_dir": prev}

def rollback(restart=True):
    st = _load(_state_path(), {})
    prev = st.get("prev_dir")
    if not prev or not os.path.isdir(prev):
        return {"ok": False, "error": "kein Vorgaenger-Release zum Zuruckrollen"}
    try:
        portal_release._flip_current(prev)
    except Exception as e:
        return {"ok": False, "error": "flip: %s" % (str(e)[:160])}
    _save(_state_path(), {"version": st.get("from") or "", "rolled_back_from": st.get("version"),
                          "prev_dir": "", "applied": int(time.time())})
    restarted = _safe_restart() if restart else False
    return {"ok": True, "version": st.get("from") or "", "restart": restarted}

def _safe_restart():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "..", "..", "ops", "brainbox-safe-deploy"),
                 os.path.expanduser("~/brainarbeit/ops/brainbox-safe-deploy")):
        cand = os.path.abspath(cand)
        if os.path.exists(cand):
            try:
                subprocess.Popen(["sh", cand], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                 start_new_session=True, env={**os.environ, "SKIP_PREFLIGHT": os.environ.get("SKIP_PREFLIGHT", "0")})
                return True
            except Exception:
                pass
    pnctl = os.path.expanduser("~/.local/bin/pnctl")
    if os.path.exists(pnctl):
        try:
            svc = subprocess.run([pnctl, "list"], capture_output=True, text=True).stdout
            name = ""
            for line in svc.splitlines():
                if "portal" in line:
                    name = line.split()[0]
                    break
            if name:
                subprocess.Popen([pnctl, "restart", name], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                return True
        except Exception:
            pass
    return False

def progress():
    return _load(_progress_path(), {"state": "idle", "phase": "", "msg": ""})

def _cli(argv):
    if argv and argv[0] in ("--check", "check"):
        r = check()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 1
    if argv and argv[0] in ("--rollback", "rollback"):
        r = rollback()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 1
    no_restart = "--no-restart" in argv
    r = apply(restart=not no_restart)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r.get("ok") else 1

if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
