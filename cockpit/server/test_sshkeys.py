#!/usr/bin/env python3

import base64
import importlib.machinery
import importlib.util
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pn_sshkeys as K

WIZ = os.path.abspath(os.path.join(HERE, "..", "..", "os", "image", "brainbox-setup"))

FAILED = []

def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else " " + str(extra)))
    if not cond:
        FAILED.append(name)

def mkkey(ktype="ssh-ed25519", sizes=(32,), seed=0, comment="me@laptop"):
    parts = [ktype.encode()]
    for i, sz in enumerate(sizes):
        parts.append(bytes((seed * 31 + i * 7 + j) % 256 for j in range(sz)))
    blob = b"".join(struct.pack(">I", len(x)) + x for x in parts)
    return ktype + " " + base64.b64encode(blob).decode() + ((" " + comment) if comment else "")

K1 = mkkey(seed=1, comment="laptop")
K2 = mkkey("ssh-rsa", sizes=(3, 256), seed=2, comment="phone")
K3 = mkkey("ecdsa-sha2-nistp256", sizes=(8, 65), seed=3, comment="tablet")

CORPUS = [
    ("", "leer"),
    ("   \n\n ", "nur Leerraum"),
    (K1, "ein ed25519"),
    (K1 + "\n" + K2, "zwei Schluessel"),
    (K3, "ecdsa"),
    (mkkey("sk-ssh-ed25519@openssh.com", sizes=(32, 11), seed=9), "FIDO-Schluessel"),
    (K1 + "\r\n" + K2 + "\r\n", "CRLF aus Windows"),
    ("# Kommentar\n" + K1, "Kommentarzeile"),
    (K1 + "\n" + K1, "derselbe zweimal"),
    (K1 + "\n" + K1.rsplit(" ", 1)[0] + " anderer-name", "gleicher Schluessel, anderer Kommentar"),
    ('command="/bin/false" ' + K1, "Zwangsbefehl-Praefix"),
    ("no-pty,no-agent-forwarding " + K1, "Options-Liste"),
    ('environment="PATH=/tmp" ' + K1, "environment-Praefix"),
    ('from="10.0.0.0/8" ' + K1, "from-Praefix"),
    ("ssh-dss " + K1.split(" ")[1], "veraltetes ssh-dss"),
    ("ssh-ed25519 " + K1.split(" ")[1][:60], "abgeschnitten"),
    ("ssh-ed25519 " + K2.split(" ")[1], "Typ passt nicht zum Koerper"),
    ("ssh-ed25519 kein!base64", "kein base64"),
    ("irgendein satz den jemand eingefuegt hat", "Prosa"),
    ("ssh-ed25519", "Typ ohne Koerper"),
    ("\n".join(mkkey(seed=i, comment="k%d" % i) for i in range(K.SSH_KEYS_MAX + 2)), "zu viele"),
    ("x" * (K.SSH_PASTE_MAX + 1), "zu lang"),
    (mkkey(seed=5, comment="ok\x07\x1b[31mrot"), "Steuerzeichen im Kommentar"),
    (mkkey(seed=6, comment="c" * 400), "sehr langer Kommentar"),
    ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1r\n-----END OPENSSH PRIVATE KEY-----",
     "versehentlich der PRIVATE Schluessel"),
]

print("== parse: Grundverhalten")

ok, res = K.parse("")
check("leerer Paste ist gueltig und ergibt []", ok and res == [])

ok, res = K.parse(K1)
check("ed25519 angenommen", ok and len(res) == 1, res)

for bad, label in (('command="/bin/false" ' + K1, "Zwangsbefehl"),
                   ("no-pty " + K1, "Options-Praefix"),
                   ('from="10.0.0.0/8" ' + K1, "from-Praefix")):
    ok, res = K.parse(bad)
    check("ABGELEHNT: %s" % label, not ok, res)

ok, res = K.parse("ssh-ed25519 " + K1.split(" ")[1][:60])
check("abgeschnittener Schluessel abgelehnt", not ok and res.startswith("ssh_bad_"), res)

ok, res = K.parse("-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----")
check("privater Schluessel abgelehnt", not ok, res)

print("== fingerprint")

fp = K.fingerprint(K1)
check("Fingerabdruck hat OpenSSH-Form", fp.startswith("SHA256:") and "=" not in fp, fp)
check("Kommentar aendert den Fingerabdruck NICHT",
      K.fingerprint(K1.rsplit(" ", 1)[0] + " anders") == fp)
check("anderer Schluessel, anderer Fingerabdruck", K.fingerprint(K2) != fp)
check("Muell ergibt leeren Fingerabdruck", K.fingerprint("kaputt") == "")

import subprocess
tmpk = tempfile.mkdtemp(prefix="fp")
try:
    kf = os.path.join(tmpk, "id")
    r = subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "x@y", "-f", kf],
                       capture_output=True)
    if r.returncode == 0:
        pub = open(kf + ".pub").read().strip()
        out = subprocess.run(["ssh-keygen", "-lf", kf + ".pub"],
                             capture_output=True, text=True).stdout.split()
        their = [w for w in out if w.startswith("SHA256:")]
        check("Fingerabdruck stimmt mit ssh-keygen ueberein",
              bool(their) and K.fingerprint(pub) == their[0],
              "%s vs %s" % (K.fingerprint(pub), their))
    else:
        print("  --   ssh-keygen nicht verfuegbar, Gegenprobe uebersprungen")
finally:
    shutil.rmtree(tmpk, ignore_errors=True)

print("== read / add / remove")

tmp = tempfile.mkdtemp(prefix="sk")
try:
    home = os.path.join(tmp, "home")
    os.makedirs(home)

    check("read auf leerem Zuhause ergibt []", K.read(home) == [])

    added, total = K.add(home, K.parse(K1)[1])
    check("erster Schluessel eingetragen", added == 1 and total == 1, (added, total))
    ak = K.path_for(home)
    check("Datei ist 0600", oct(os.stat(ak).st_mode & 0o777) == "0o600")
    check(".ssh ist 0700", oct(os.stat(os.path.dirname(ak)).st_mode & 0o777) == "0o700")

    added, total = K.add(home, K.parse(K2)[1])
    check("zweiter ANGEHAENGT, erster bleibt", added == 1 and total == 2, (added, total))

    added, total = K.add(home, K.parse(K1)[1])
    check("derselbe nochmal aendert nichts", added == 0 and total == 2, (added, total))

    added, total = K.add(home, K.parse(K1.rsplit(" ", 1)[0] + " neuer-name")[1])
    check("gleicher Schluessel mit neuem Kommentar dupliziert nicht",
          added == 0 and total == 2, (added, total))

    rows = K.read(home)
    check("read meldet Typ und Kommentar",
          rows[0]["type"] == "ssh-ed25519" and rows[0]["comment"] == "laptop", rows[0])
    check("read meldet Fingerabdruecke", all(r["fp"].startswith("SHA256:") for r in rows))

    with open(ak, "a") as f:
        f.write('command="/usr/bin/backup" ' + K3 + "\n")
    rows = K.read(home)
    check("Options-Zeile wird als 'nicht geparst' GEMELDET, nicht verschwiegen",
          len(rows) == 3 and rows[2]["parsed"] is False, rows)
    check("...und bekommt keinen Fingerabdruck zum Loeschen", rows[2]["fp"] == "")

    hit, rest = K.remove(home, K.fingerprint(K1))
    check("Entfernen ueber Fingerabdruck trifft", hit and rest == 1, (hit, rest))
    body = open(ak).read()
    check("der andere Schluessel ist noch da", K.body(K2) in body)
    check("die Options-Zeile hat das Entfernen ueberlebt", "command=" in body)

    hit, rest = K.remove(home, K.fingerprint(K1))
    check("zweites Entfernen meldet ehrlich 'nicht getroffen'", not hit, (hit, rest))

    hit, rest = K.remove(home, "SHA256:gibtesnicht")
    check("unbekannter Fingerabdruck entfernt nichts", not hit)

    with open(ak, "w") as f:
        f.write(K1 + "\n" + K2 + "\n" + K3 + "\n")
    K.remove(home, K.fingerprint(K2))
    left = [r["fp"] for r in K.read(home)]
    check("genau der adressierte Schluessel verschwindet",
          K.fingerprint(K1) in left and K.fingerprint(K3) in left
          and K.fingerprint(K2) not in left, left)

    with open(ak, "w") as f:
        f.write(K1 + "\n")
    K.remove(home, K.fingerprint(K1))
    check("letzten entfernen laesst eine leere, keine kaputte Datei",
          open(ak).read().strip() == "" and K.read(home) == [])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("== Paritaet mit dem Ersteinrichtungs-Assistenten")

if not os.path.exists(WIZ):
    check("Assistent gefunden", False, WIZ)
else:
    spec = importlib.util.spec_from_loader(
        "bbxwiz", importlib.machinery.SourceFileLoader("bbxwiz", WIZ))
    W = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(W)

    check("beide kennen dieselben Schluesseltypen",
          tuple(W.SSH_KEY_TYPES) == tuple(K.SSH_KEY_TYPES))
    check("beide haben dieselben Grenzen",
          (W.SSH_KEYS_MAX, W.SSH_PASTE_MAX, W.SSH_COMMENT_MAX)
          == (K.SSH_KEYS_MAX, K.SSH_PASTE_MAX, K.SSH_COMMENT_MAX))

    drift = []
    for text, label in CORPUS:
        a = K.parse(text)
        b = W.validate_ssh_keys(text)
        if a != b:
            drift.append("%s: portal=%r wizard=%r" % (label, a, b))
    check("identische Urteile ueber den ganzen Korpus (%d Faelle)" % len(CORPUS),
          not drift, drift[:3])

print("== password_auth")

pw = K.password_auth()
check("password_auth liefert True, False oder None", pw in (True, False, None), pw)

print()
if FAILED:
    print("FAILED %d: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("alle pn_sshkeys-Tests bestanden")
