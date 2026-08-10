#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
fail=0
note(){ echo "  !! $*"; fail=1; }

ACCEPTANCE="${ACCEPTANCE:-auto}"
ACCEPTANCE_IMAGE="${ACCEPTANCE_IMAGE:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --image)         ACCEPTANCE_IMAGE="$2"; shift 2 ;;
    --acceptance)    ACCEPTANCE=1; shift ;;
    --no-acceptance) ACCEPTANCE=0; shift ;;
    -h|--help)       sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

FILES=""
for g in "$HERE"/*.sh "$HERE"/brainbox-netcfg "$HERE"/brainbox-banner \
         "$HERE"/brainbox-earlyboot "$HERE"/brainbox-caps-detect "$HERE"/brainbox-setup; do
  [ -e "$g" ] && FILES="$FILES $g"
done

for f in $FILES; do
  b="$(basename "$f")"
  if [ ! -s "$f" ]; then note "EMPTY: $b"; continue; fi
  if ! head -1 "$f" | grep -q '^#!'; then note "NO SHEBANG: $b"; continue; fi
  case "$b" in
    release-gate.sh) : ;;
    *) grep -qF '\&\&' "$f" && note "ESCAPED AMPERSAND (\\&\\& should be &&): $b" ;;
  esac
  first="$(head -1 "$f")"
  case "$b" in
    brainbox-setup)
      python3 -m py_compile "$f" 2>/dev/null || note "PYTHON SYNTAX: $b" ;;
    *)
      if printf '%s' "$first" | grep -q 'bash'; then
        bash -n "$f" 2>/dev/null || note "BASH SYNTAX: $b"
      elif printf '%s' "$first" | grep -qE 'python'; then
        python3 -m py_compile "$f" 2>/dev/null || note "PYTHON SYNTAX: $b"
      else
        sh -n "$f" 2>/dev/null || note "SH SYNTAX: $b"
      fi ;;
  esac
done

nfiles="$(echo $FILES | wc -w)"

if [ -f "$HERE/test_netprofile.py" ]; then
  if ! python3 "$HERE/test_netprofile.py" >/tmp/bbx-gate-netprofile.log 2>&1; then
    note "NETZPROFIL-TESTS FEHLGESCHLAGEN (siehe /tmp/bbx-gate-netprofile.log)"
  else
    echo "  Netzprofil-Tests: ok"
  fi
else
  note "FEHLT: test_netprofile.py (das Netzprofil wuerde ungeprueft ausgeliefert)"
fi

if [ -f "$HERE/test_setup_access.py" ]; then
  if ! python3 "$HERE/test_setup_access.py" >/tmp/bbx-gate-access.log 2>&1; then
    note "WIZARD ACCESS TESTS FAILED (see /tmp/bbx-gate-access.log)"
  else
    echo "  wizard access tests: ok"
  fi
else
  note "MISSING: test_setup_access.py (the access card would ship ungated)"
fi

REPO="${REPO_SRC:-$(cd "$HERE/../.." 2>/dev/null && pwd)}"
nart=0
if [ "${SKIP_ARTIFACTS:-0}" = 1 ]; then
  echo "  (artifact gate skipped: SKIP_ARTIFACTS=1)"
else
  echo "  artifact gate: repo root = ${REPO:-<unresolvable>}"
  for spec in \
    "os/pn-vmm/target/release/pn-vmm|x|cd os/pn-vmm && cargo build --release" \
    "os/pn-vmm/kernel/vmlinux.bin|s|see os/pn-vmm/kernel (guest kernel build)" \
    "os/pn-vmm/kernel/vmlinux-rng.bin|s|os/pn-vmm/kernel/build_rng_kernel.sh (the 6.1 guest kernel session cells BOOT — exec interception needs >=5.0)" \
    "os/pn-vmm/kernel/initramfs-cell.cpio|s|see os/pn-vmm/kernel (cell initramfs build)" \
    "os/pn-vmm/kernel/base-owner-session.img|s|python3 os/pn-vmm/build_cell_owner_session.py" \
    "os/pn-vmm/kernel/base-office.img|s|python3 os/pn-vmm/build_cell_office.py (erst build_cell_owner_session.py: braucht kernel/_ownersession)" \
    "components/phantom/target/release/phantom|x|cd components/phantom && cargo build --release" \
    "components/phantom/target/release/phantom-supervise|x|cd components/phantom && cargo build --release" \
  ; do
    rel="${spec%%|*}"; rest="${spec#*|}"; t="${rest%%|*}"; how="${rest#*|}"
    p="$REPO/$rel"
    nart=$((nart+1))
    if [ "$t" = x ]; then
      [ -x "$p" ] && [ -s "$p" ] || note "MISSING/NOT-EXECUTABLE ARTIFACT: $rel   (build: $how)"
    else
      [ -s "$p" ] || note "MISSING ARTIFACT: $rel   (build: $how)"
    fi
  done
fi

BUILDER="$HERE/build-appliance-disk.sh"
if [ -s "$BUILDER" ]; then
  aptline="$(sed -n '/chr apt-get install/,/bbx-apt.log/p' "$BUILDER")"
  for p in tmux ffmpeg espeak-ng e2fsprogs; do
    printf '%s' "$aptline" | grep -qw -- "$p" || note "PACKAGE NOT IN CHROOT APT LIST: $p (build-appliance-disk.sh step 4)"
  done
fi

nhelp=0
SRV="$REPO/cockpit/server"
if [ "${SKIP_ARTIFACTS:-0}" != 1 ] && [ -d "$SRV" ] && [ -s "$BUILDER" ]; then
  staged="$( { sed -n '/---8<--- HELPERS/,/---8<--- END HELPERS/p' "$BUILDER" \
                 | grep -oE '^[[:space:]]*[A-Za-z0-9_.-]+\|' | tr -d ' |'
               grep -E '^[[:space:]]*[^#]*(ln -sf?n?|install )' "$BUILDER" \
                 | grep -ohE '\.local/bin/[A-Za-z0-9_.-]+' | sed 's:.*/::' ; } | sort -u )"
  wanted="$( { grep -rhoE '\.local/bin/[A-Za-z0-9_.-]+' "$SRV" \
                 --binary-files=without-match 2>/dev/null | sed 's:.*/::; s:\.\+$::'
               grep -rhoE '"\.local"[[:space:]]*,[[:space:]]*"bin"[[:space:]]*,[[:space:]]*"[A-Za-z0-9_.-]+"' "$SRV" \
                 --binary-files=without-match 2>/dev/null | grep -oE '"[A-Za-z0-9_.-]+"$' | tr -d '"' ; } \
             | sort -u)"
  # OPTIONAL third-party CLIs: not repo files — `git archive` cannot supply them, and vendoring
  # them is a licensing/size question, not a build defect. Allowed to be absent ONLY because every
  # call site existence-checks the binary and degrades honestly (portal_insights.py:
  # _codex_ready() -> os.path.exists(CODEX) at 209/223, gemini gate -> os.path.exists(GEMINI) at
  # 295/307; insights fall back to the claude brain). runtime=codex/gemini SESSIONS use the staged
  # cell runtime images from step 6c3, never these host binaries. A new call site WITHOUT an
  # existence check must not hide behind this list — re-grep the call sites before extending it.
  OPTIONAL_HELPERS="codex gemini"
  for n in $wanted; do
    nhelp=$((nhelp+1))
    printf '%s\n' $OPTIONAL_HELPERS | grep -qx -- "$n" && continue
    printf '%s\n' "$staged" | grep -qx -- "$n" || \
      note "RUNTIME HELPER NOT STAGED: the portal execs ~/.local/bin/$n but build-appliance-disk.sh
        never puts it there, so the feature behind it is dead on every installed appliance.
        Fix: add '$n|<repo-relative-path>' to the HELPERS block in build-appliance-disk.sh (step 6a)."
  done
  # And every HELPERS target must actually exist in the repo — a moved/renamed path must fail HERE,
  # before the ~1h build, not as a FATAL two thirds of the way through it.
  sed -n '/---8<--- HELPERS/,/---8<--- END HELPERS/p' "$BUILDER" \
    | grep -oE '^[[:space:]]*[A-Za-z0-9_.-]+\|[A-Za-z0-9_./-]+' | tr -d ' ' \
    | while IFS='|' read -r hn hrel; do
        [ -n "$hrel" ] || continue
        [ -s "$REPO/$hrel" ] || echo "  !! HELPER SOURCE MISSING: $hn -> $hrel (path moved, or HEAD is broken)"
      done > /tmp/.rg-helpers.$$ 2>/dev/null
  if [ -s /tmp/.rg-helpers.$$ ]; then cat /tmp/.rg-helpers.$$; fail=1; fi
  rm -f /tmp/.rg-helpers.$$
fi

# ── boot-chain gate ───────────────────────────────────────────────────────────────────────────
# Every /usr/local/sbin/brainbox-* the generated pn-init.conf references must exist in os/image/.
# A dangling exec target for a `sacred` service makes PID1 respawn-loop forever.
if [ -s "$BUILDER" ]; then
  for svc in $(grep -oE '/usr/local/sbin/brainbox-[a-z-]+(\.sh)?' "$BUILDER" | sort -u); do
    b="$(basename "$svc")"
    case "$b" in
      brainbox-setup)        src="$HERE/brainbox-setup" ;;      # wizard (another owner) or staged
      brainbox-firstboot.sh) src="$HERE/firstboot.sh" ;;        # installed under a renamed path
      brainbox-factory-clean.sh) src="$HERE/factory-clean.sh" ;;
      # Break-glass ships from os/breakglass/, not os/image/ — the builder renames both on
      # install (step 6). Without these two cases the gate is permanently red on a healthy
      # tree, and a gate nobody believes any more cannot catch the real dangling target it
      # exists for: a `sacred` service whose exec path is missing respawn-loops PID1 forever.
      brainbox-breakglassd)  src="$REPO/os/breakglass/pn_breakglassd.py" ;;
      brainbox-breakglass)   src="$REPO/os/breakglass/breakglass.sh" ;;
      *)                     src="$HERE/$b" ;;
    esac
    [ -s "$src" ] || note "BOOT-CHAIN TARGET HAS NO SOURCE: $svc (expected $src)"
  done
fi


# ── SICHERHEITS-RUECKFALLSPERREN (02.08.2026) ────────────────────────────────────────────────────
# Jede Zeile hier steht fuer einen Befund, den drei Audits am fertigen Produkt gefunden haben und
# den dieses Gate vorher NICHT bemerkt haette. Das Gate prueft bisher, ob die Box FUNKTIONIERT --
# es hat nie geprueft, ob sie DICHT ist. Ein Rueckfall ist hier billiger zu finden als beim Kunden.
nsec=0
secfail() { echo "  !! $*"; fail=1; }
sec_ok()  { nsec=$((nsec+1)); }

B="$REPO/os/image/build-appliance-disk.sh"
P="$REPO/cockpit/server/brainbox-portal"
W="$REPO/os/image/brainbox-setup"

# 1) Kein erzwungener Passwortwechsel: `chage -d 0` laesst PAM auch die per SSH-SCHLUESSEL
#    authentisierte Sitzung scheitern ("no TTY available") -- SSH war damit fuer JEDEN Kunden tot.
if grep -qE '^[^#]*chage -d 0' "$B" "$REPO/os/image/build-appliance-pi-arm64.sh" 2>/dev/null; then
  secfail "chage -d 0 ist zurueck — das macht SSH mit Schluessel unbenutzbar (siehe 69790c5)"
else sec_ok; fi

# 2) fsck.repair=yes: ohne ihn bricht fsck -a bei Inkonsistenzen ab und panic=10 macht daraus eine
#    ENDLOSE Neustartschleife. Ein Stromausfall = Ziegelstein beim Kunden.
# ANGEHEFTET an die ZUWEISUNG, nicht irgendwo in der Datei: ein blosses grep nach
# 'fsck.repair=yes' wird schon vom erklaerenden Kommentar daneben erfuellt -- der Check haette
# sich selbst bestaetigt. Das ist genau die Falle, die der Helfer-Check weiter oben beschreibt
# ("A check that its own documentation can satisfy is not a check"); beim Trockentest dieser
# Sperren ist sie mir prompt passiert: die cmdline war entfernt, das Gate blieb gruen.
grep -qE '^CMD_COMMON=.*fsck\.repair=yes' "$B" \
  || secfail "fsck.repair=yes fehlt in der Kernel-cmdline — ein Stromausfall macht die Box zum Ziegelstein"
[ -f "$REPO/os/image/provision.sh" ] && { grep -qE 'sed -i .*fsck\.repair=yes' "$REPO/os/image/provision.sh" \
  || secfail "fsck.repair fehlt im Pi-Pfad (provision.sh)"; }
sec_ok

# 3) Die Host-Shell-Routen duerfen nicht wieder im Dispatch auftauchen.
if grep -qE '^\s*if path == "/api/term":' "$P" 2>/dev/null; then
  secfail "/api/term ist als aktive Route zurueck — das ist eine unsandboxte Host-Shell fuer jedes angemeldete Konto"
else sec_ok; fi

# 4) Der Assistent darf nach abgeschlossenem Setup nicht per Query-String wieder aufgehen.
if grep -qE 'return "force=1" in' "$W" 2>/dev/null; then
  secfail "brainbox-setup: _force_requested() wertet wieder den Query-String aus — Fernuebernahme einer eingerichteten Box"
else sec_ok; fi
if grep -qE '^\s*CLAUDE_POST_PATHS = \("/api/login' "$W" 2>/dev/null; then
  secfail "brainbox-setup: CLAUDE_POST_PATHS ist wieder befuellt — unauthentifizierter Credential-Tausch nach dem Setup"
else sec_ok; fi

# 5) Host-Firewall muss im Image liegen UND in der Boot-Kette stehen, VOR den lauschenden Diensten.
[ -x "$REPO/os/image/brainbox-firewall" ] || secfail "os/image/brainbox-firewall fehlt oder ist nicht ausfuehrbar"
grep -q 'firewall|oneshot|/usr/local/sbin/brainbox-firewall' "$B" \
  || secfail "Firewall steht nicht in der Boot-Kette (build-appliance-disk.sh)"
grep -q 'brainbox-firewall' "$B" || secfail "brainbox-firewall wird nicht ins Image installiert"
# DHCP muss offen bleiben, sonst holt sich die Box nach einem Neustart keine Adresse mehr.
grep -q 'udp dport { 68, 546 }' "$REPO/os/image/brainbox-firewall" \
  || secfail "Firewall laesst DHCP nicht durch — die Box waere nach einem Neustart nicht erreichbar"
sec_ok

# 6) Leise ausliefern: das Image geht im managed-Profil vom Band.
grep -qE '^NET_PROFILE=managed' "$B" \
  || secfail "Image-Default ist nicht NET_PROFILE=managed — die Box laermt in fremden Netzen (mDNS/SSDP/NetBIOS/LAN-Scan)"
sec_ok

# 7) Der aktive /24-Sweep muss hinter dem netprofile-Gate liegen.
grep -q '_netprofile_allows("lan_scan"' "$REPO/cockpit/server/portal_voice_core.py" \
  || secfail "Der aktive LAN-Sweep laeuft wieder ungated — in ueberwachten Netzen ein sofortiger SOC-Alarm"
sec_ok

# 8) Keine anonym beschreibbare SMB-Freigabe.
if grep -qE '^\s*guest ok = yes' "$REPO/os/image/brainbox-smbd" 2>/dev/null; then
  secfail "brainbox-smbd: eine Freigabe ist wieder anonym erreichbar (guest ok = yes)"
else sec_ok; fi

# 9) Dienste, die nur das Portal bedienen soll, duerfen nicht auf 0.0.0.0 lauschen.
if grep -qE 'ThreadingHTTPServer\(\("0\.0\.0\.0"' "$REPO/cockpit/server/pn_castd.py" 2>/dev/null; then
  secfail "pn_castd lauscht wieder auf 0.0.0.0 — ohne jede Authentisierung im ganzen LAN"
else sec_ok; fi

# 10) Das oeffentliche Image darf keine privaten Hostkeys enthalten (beide Bau-Pfade loeschen sie).
grep -q 'rm -f "$MNT"/etc/ssh/ssh_host_\*' "$B" \
  || secfail "amd64-Bau loescht die SSH-Hostkeys nicht mehr"
grep -q 'rm -f "$MNT"/etc/ssh/ssh_host_\*' "$REPO/os/image/build-appliance-pi-arm64.sh" \
  || secfail "Pi-Bau loescht die SSH-Hostkeys nicht — jedes geflashte Geraet haette denselben Schluessel"
sec_ok

# 11) Konsolen-Passwort pro GERAET (der Bau-Wert steht im oeffentlichen Image).
# An den WIRKSAMEN Befehl angeheftet, nicht an das Wort: `grep -q initial-console-password`
# wurde schon vom erklaerenden Kommentar erfuellt -- wer den chpasswd-Block loescht und den
# Kommentar stehen laesst, haette das Gate gruen gehalten. Zweite Instanz derselben Falle an
# einem Tag; deshalb steht sie jetzt auch in der Datei selbst dokumentiert (siehe oben).
grep -qE '^\s*NEWPW=' "$REPO/os/image/firstboot.sh" \
  && grep -qE 'chpasswd' "$REPO/os/image/firstboot.sh" \
  || secfail "firstboot erneuert das Konsolen-Passwort nicht — alle Geraete aus einer ISO teilen es"
sec_ok

# 12) ERSTKONTAKT: keine stille Sekunde, kein kaputt aussehender Text.
#
# Die Zusagen dieser Gruppe sind gemessen, nicht geraten (Zeitleiste vom 02.08.2026):
# vom Einschalten bis zum ersten fuer Menschen lesbaren Satz vergingen 49 Sekunden, davon
# 20,6 s vollstaendig schwarzer Bildschirm; GRUB malte jedes Zeichen jenseits von ASCII
# als '?', so dass woertlich "Brainbox wird geladen ? bitte warten." dastand.
#
# ASCII-Pruefung mit LC_ALL=C und grep -P: ein einziges Byte >= 0x80 in einer Datei, die auf
# die Konsole schreibt, ist ein sichtbarer Defekt fuer jeden Erstnutzer.
for f in os/image/brainbox-banner os/image/iso-installer/scripts-brainbox; do
  if LC_ALL=C grep -qP '[\x80-\xFF]' "$REPO/$f" 2>/dev/null; then
    secfail "$f enthaelt Zeichen jenseits von ASCII — die Konsolenschrift malt sie als '?'"
  fi
done
# Im ISO-Bauer nur der GRUB-Block (der Rest der Datei darf deutsche Kommentare haben).
if sed -n '/boot\/grub\/grub.cfg" <<EOF/,/^EOF$/p' "$REPO/os/image/build-appliance-iso.sh" \
   | LC_ALL=C grep -qP '[\x80-\xFF]'; then
  secfail "grub.cfg des ISO enthaelt Zeichen jenseits von ASCII — GRUB malt sie als '?'"
fi
sec_ok

# 13) Der Banner ist der EINZIGE Maler und spricht, BEVOR firstboot laeuft.
for conf in os/image/build-appliance-disk.sh os/image/pn-init.conf.pi; do
  n="$(grep -cE '^banner\|sacred\|' "$REPO/$conf" || true)"
  [ "$n" = 1 ] || secfail "$conf: $n Banner-Eintraege in der Bootkette (genau 1 erwartet) — zwei Maler loeschen sich gegenseitig"
  bl="$(grep -nE '^banner\|sacred\|' "$REPO/$conf" | head -1 | cut -d: -f1)"
  fl="$(grep -nE '^firstboot\|oneshot\|' "$REPO/$conf" | head -1 | cut -d: -f1)"
  if [ -n "$bl" ] && [ -n "$fl" ] && [ "$bl" -gt "$fl" ]; then
    secfail "$conf: banner steht hinter firstboot — waehrend der laengsten Startphase bliebe der Bildschirm leer"
  fi
done
grep -q 'for dev in /dev/tty1 /dev/console' "$REPO/os/image/firstboot.sh" \
  && secfail "firstboot malt wieder selbst auf die Konsole — der Banner ueberschreibt es nach vier Sekunden"
sec_ok

# 14) Der Banner preist keine Adresse an, die niemand annimmt.
# Ohne diese Pruefung stuende die Adresse schon auf dem Schirm, waehrend der Assistent noch
# startet: der Nutzer tippt sie ab, bekommt "Verbindung abgelehnt" und glaubt der Box nie
# wieder. An den wirksamen Aufruf angeheftet, nicht an den Funktionsnamen im Kommentar.
grep -qE '^horcht\(\)' "$REPO/os/image/brainbox-banner" \
  && grep -qE 'if horcht 80; then' "$REPO/os/image/brainbox-banner" \
  || secfail "brainbox-banner prueft nicht mehr, ob der Assistent wirklich horcht, bevor er seine Adresse anzeigt"
grep -q '/run/brainbox/boot-status' "$REPO/os/image/brainbox-banner" \
  || secfail "brainbox-banner zeigt den laufenden Startschritt nicht mehr an — 'haengt er oder arbeitet er' bleibt unbeantwortet"
grep -qE '^\s*schritt [1-6] ' "$REPO/os/image/firstboot.sh" \
  || secfail "firstboot meldet keine Schritte mehr an den Bildschirm"
sec_ok

# 15) Die Startphase bleibt ansprechbar und leise.
grep -qE '^CMD_COMMON=.*loglevel=4' "$REPO/os/image/build-appliance-disk.sh" \
  || secfail "loglevel=4 fehlt in der Kernel-cmdline — 531 Zeilen Fachjargon vor dem ersten lesbaren Satz, und die serielle Ausgabe bremst den Start"
grep -q 'echo 3 > /proc/sys/kernel/printk' "$REPO/os/image/iso-installer/scripts-brainbox" \
  || secfail "Der Installer stellt die Kernel-Meldungen nicht mehr leise — sie zerhacken die Fortschrittsanzeige"
grep -q 'Von der Festplatte starten' "$REPO/os/image/build-appliance-iso.sh" \
  || secfail "Dem ISO-Startmenue fehlt der Eintrag 'Von der Festplatte starten' — beim vergessenen ISO bleibt nur der Hypervisor"
sec_ok

# 16) Die Startkarte -- das Einzige, was ein Neuling nach dem Flashen ueberhaupt sieht.
# Geprueft wird das ERZEUGNIS, nicht der Aufruf: die Karte wird hier wirklich gebaut.
[ -x "$REPO/os/image/make-startkarte.sh" ] || secfail "os/image/make-startkarte.sh fehlt oder ist nicht ausfuehrbar"
for b in build-appliance-iso.sh build-appliance-pi-arm64.sh provision.sh; do
  grep -q 'make-startkarte.sh' "$REPO/os/image/$b" \
    || secfail "$b legt keine Startkarte ab — nach dem Flashen/Einhaengen steht der Nutzer vor einer leeren Partition"
done
if [ -x "$REPO/os/image/make-startkarte.sh" ]; then
  _kd="$(mktemp -d)"
  if bash "$REPO/os/image/make-startkarte.sh" "$_kd" pi >/dev/null 2>&1; then
    for _f in START-HIER.txt START-HERE.txt START-HIER.html; do
      [ -s "$_kd/$_f" ] || secfail "Startkarte: $_f wurde nicht erzeugt"
    done
    LC_ALL=C grep -qP '[\x80-\xFF]' "$_kd/START-HIER.txt" \
      && secfail "Startkarte: START-HIER.txt ist nicht reines ASCII — auf fremden Systemen erscheint Buchstabensalat"
    _cr="$(printf '\r')"          # kein Bashismus: das Gate laeuft mit sh (dash),
    LC_ALL=C grep -qU "${_cr}$" "$_kd/START-HIER.txt" \
      || secfail "Startkarte: keine Windows-Zeilenenden — der Windows-Editor zeigt eine einzige lange Zeile"
    grep -q 'http://brainbox.local/' "$_kd/START-HIER.txt" \
      || secfail "Startkarte: die Einrichtungsadresse steht nicht drauf"
    grep -q 'http://brainbox.local/' "$_kd/START-HIER.html" \
      || secfail "Startkarte: der Knopf in START-HIER.html zeigt nicht auf die Einrichtungsadresse"
    grep -q 'brainbox-authorized_keys' "$_kd/START-HIER.txt" \
      || secfail "Startkarte: der kopflose Weg (SSH-Schluessel vorbelegen) fehlt"
    # Das Konto MUSS auf der Karte stehen, und zwar je Abbild das richtige: die Platte legt
    # `brainbox` an, das Pi-Abbild erbt `ubuntu` vom Ubuntu-RPi-Grundabbild.
    grep -q 'ssh ubuntu@brainbox.local' "$_kd/START-HIER.txt" \
      || secfail "Startkarte (pi): nennt das SSH-Konto nicht — der falsche Name meldet sich als 'Permission denied (publickey)' und schickt den Leser zum Schluessel statt zum Namen"
    if bash "$REPO/os/image/make-startkarte.sh" "$_kd" iso >/dev/null 2>&1; then
      grep -q 'ssh brainbox@brainbox.local' "$_kd/START-HIER.txt" \
        || secfail "Startkarte (iso): nennt nicht das Konto 'brainbox' des Platten-Abbilds"
    else
      secfail "Startkarte: make-startkarte.sh laeuft fuer die Art 'iso' nicht durch"
    fi
    # Und die Doku muss BEIDE kennen, sonst beschreibt sie nur die Haelfte der Abbilder.
    for _n in brainbox ubuntu; do
      grep -q "ssh $_n@brainbox.local" "$REPO/docs/ACCESS.md" \
        || secfail "docs/ACCESS.md nennt den Anmeldenamen '$_n' nicht — dann gilt sie nur fuer eines der beiden Abbilder"
    done
  else
    secfail "Startkarte: make-startkarte.sh laeuft nicht durch"
  fi
  rm -rf "$_kd"
fi
sec_ok

# 17) Der Container-Pfad: versprochen im Vorwort, also auch vorhanden.
[ -x "$REPO/os/image/build-docker-image.sh" ] \
  || secfail "os/image/build-docker-image.sh fehlt — das Vorwort verspricht ein Container-Abbild"
[ -s "$REPO/docs/docker.md" ] \
  || secfail "docs/docker.md fehlt — das Vorwort verweist darauf, der Verweis zeigte ins Leere"
# Die Dienstliste des Containers muss ABGELEITET werden. Eine zweite, von Hand gepflegte
# Kette waere ein zweites Produkt: sie driftet, und bei jedem Fehler weiss niemand, ob er die
# Box oder nur den Container betrifft.
grep -q 'etc/pn-init.conf' "$REPO/os/image/build-docker-image.sh" \
  || secfail "build-docker-image.sh leitet die Dienstliste nicht aus der Bootkette des Images ab"
grep -q 'pn.allow_uncapped' "$REPO/os/image/build-docker-image.sh" \
  || secfail "build-docker-image.sh setzt pn.allow_uncapped nicht — im Container faellt pn-init sonst auf 'nur sacred' zurueck und firstboot laeuft nie"
# Und die tragenden Dienste duerfen beim Filtern nicht mitgerissen werden.
for _muss in earlyboot banner firstboot brainbox-setup brainbox-portal sshd; do
  grep -q "$_muss" "$REPO/os/image/build-docker-image.sh" \
    || secfail "build-docker-image.sh prueft nicht mehr, dass $_muss in der Container-Kette bleibt"
done
sec_ok

# 18) Die drei Container-Zusagen, alle aus einem echten Lauf erkaempft.
grep -q 'pn.nonet' "$REPO/os/init/pn-init.c" \
  || secfail "pn-init kennt pn.nonet nicht mehr — im Container 15 s vergeblicher DHCP und ein Fehlschlag, der keiner ist"
grep -q 'pn.container' "$REPO/os/init/pn-init.c" \
  || secfail "pn-init kennt pn.container nicht mehr — docker stop lief dann 61 s in die Frist und endete mit SIGKILL"
grep -q 'CMDLINE_EXTRA_PATH' "$REPO/os/init/pn-init.c" \
  || secfail "pn-init liest /etc/pn-init.cmdline nicht mehr — im Container ist PID1 damit unansprechbar (/proc/cmdline gehoert dem Wirt)"
for _sch in pn.nonet pn.container pn.allow_uncapped; do
  grep -q "$_sch" "$REPO/os/image/build-docker-image.sh" \
    || secfail "build-docker-image.sh setzt $_sch nicht mehr"
done
# Und die Startschritte muessen im PROTOKOLL stehen, nicht nur in der Statusdatei: im
# Container gibt es keinen Banner, dort IST docker logs der Bildschirm.
grep -qE 'log "Schritt \$1 von \$SCHRITTE' "$REPO/os/image/firstboot.sh" \
  || secfail "firstboot schreibt die nummerierten Startschritte nicht ins Protokoll — im Container fehlt damit jede Orientierung"
sec_ok

# 19) Der Besitznachweis: Beanspruchen ueber Netz braucht den Code vom Bildschirm.
#     ⚠️ Dieser Block stand bis 07.08.2026 in ZEILE 13 — mitten im Kopfkommentar, vor `set -u`,
#     vor $REPO und vor der Definition von secfail(). Er ist NIE gelaufen; das Gate meldete
#     trotzdem OK, weil ein nicht gefundenes Kommando `fail` nicht setzt. Eine Sperre am
#     falschen Platz ist keine Sperre. Deshalb zaehlt das Gate unten seine Gruppen nach.
grep -q "def _claim_proof" "$REPO/os/image/brainbox-setup" \
  || secfail "brainbox-setup: _claim_proof fehlt -- jedes Geraet im LAN koennte die frische Box beanspruchen"
grep -q "self._claim_proof(" "$REPO/os/image/brainbox-setup" \
  || secfail "brainbox-setup: _claim_proof wird nirgends gerufen (totes Tor)"
grep -q "claim_code" "$REPO/os/image/brainbox-setup" \
  || secfail "brainbox-setup: /api/apply nimmt keinen claim_code entgegen"
grep -q "compare_digest" "$REPO/os/image/brainbox-setup" \
  || secfail "brainbox-setup: Setup-Code wird nicht konstantzeitig verglichen"
grep -q "\-\-claim-code" "$REPO/os/image/acceptance.sh" \
  || secfail "acceptance.sh: legt den Setup-Code nicht vor -- Besitznachweis wuerde die Abnahme brechen oder nie getestet"
sec_ok

# 20) Die Notfallkonsole darf nicht mit totem JavaScript ausgeliefert werden.
#     Befund 07.08.2026 (Owner: "kein knopf funktioniert, die sind einfach platt"): ein `\n`,
#     das fuer JavaScript gedacht war, stand in einem GEWOEHNLICHEN Python-String. Python
#     machte daraus beim Uebersetzen einen echten Zeilenumbruch, der ausgelieferte JS-String
#     lief ueber drei Zeilen -> Syntaxfehler -> der GANZE <script>-Block fiel aus. Auf einer
#     ausgelieferten Box heisst das: die letzte Tuer sieht heil aus, HTTP 200, nichts im
#     Protokoll -- und kein einziger Knopf tut etwas. py_compile merkt davon nichts.
#     Der fehlende Roh-String-Praefix ist die Ursache und ohne jedes Werkzeug pruefbar.
_bgq="$REPO/os/breakglass/pn_breakglassd.py"
_bgre='^[A-Z_]+_HTML[[:space:]]*=[[:space:]]*"""'
if [ -s "$_bgq" ]; then
  if grep -qE "$_bgre" "$_bgq" 2>/dev/null; then
    secfail "Notfallkonsole: $(grep -cE "$_bgre" "$_bgq") Seite(n) ohne Roh-String-Praefix -- ein Backslash-n fuer JavaScript wird beim Uebersetzen zum Zeilenumbruch und legt jeden Knopf der Seite still"
  fi
  grep -q 'href="/passkey"' "$_bgq" \
    || secfail "Notfallkonsole: die Startseite verlinkt die Passkey-Seite nicht mehr -- der starke Ausweis waere nur fuer den erreichbar, der die URL auswendig kann"
  grep -q '/ca.crt' "$_bgq" \
    || secfail "Notfallkonsole: das Wurzelzertifikat wird nicht mehr angeboten -- ohne es traut kein Handy der Box, und ohne Vertrauen gibt es keinen Passkey (WebAuthn verlangt einen sicheren Kontext)"
fi
sec_ok

# 21) Ein Dienst, der in der ausgelieferten pn-init.conf steht, muss von PID 1 auch GELADEN
#     werden koennen. pn-init traegt einen einkompilierten Deckel (CONF_MAX_SVC) und einen
#     Lesepuffer (CONF_BUF); was darueber liegt, existiert fuer PID 1 nicht.
#     Befund 07.08.2026: `homeassistant` stand als Dienst #41 bei einem Deckel von 40. pn-init
#     hat das gemeldet -- auf die Bootkonsole, die niemand liest -- und `pnctl list` sagte
#     "DOWN", was "laeuft gerade nicht" heisst statt "PID 1 kennt ihn nicht". Ergebnis: Home
#     Assistant lief nie unter Aufsicht, kein Assist-Satellit, der Sprachassistent verband sich
#     nicht. Der Deckel war zu diesem Zeitpunkt bereits ZWEIMAL nach genau demselben Vorfall
#     angehoben worden (24 -> 40). Diese Sperre bricht die Reihe: ein Abbild, dessen conf ueber
#     den Deckel laeuft, wird gar nicht erst ausgeliefert.
_pnc="$REPO/os/init/pn-init.c"
if [ -s "$_pnc" ]; then
  _cap="$(sed -n 's/^#define CONF_MAX_SVC[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$_pnc" | head -1)"
  _buf="$(sed -n 's/^#define CONF_BUF[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$_pnc" | head -1)"
  for _conf in "$REPO"/os/init/pn-init.conf.*example; do
    [ -s "$_conf" ] || continue
    _n="$(grep -vE '^[[:space:]]*(#|$)' "$_conf" | grep -c '|' || true)"
    _sz="$(wc -c < "$_conf" | tr -d " ")"
    if [ -n "$_cap" ] && [ "$_n" -gt "$_cap" ]; then
      secfail "pn-init.conf: $(basename "$_conf") deklariert $_n Dienste, PID 1 laedt hoechstens $_cap -- die letzten $((_n - _cap)) wuerden in der Datei stehen und NIE laufen (auch nach einem Neustart nicht)"
    fi
    if [ -n "$_buf" ] && [ "$_sz" -ge "$_buf" ]; then
      secfail "pn-init.conf: $(basename "$_conf") ist $_sz Bytes, der Lesepuffer von PID 1 fasst $_buf -- das ENDE der Datei wird abgeschnitten und die zuletzt eingetragenen Dienste existieren fuer PID 1 nicht"
    fi
  done
  # Und die Lage muss ueberhaupt nach draussen dringen: ohne publish_state() faellt jede
  # kuenftige Deckelueberschreitung wieder in ein Bootlog, das niemand liest.
  grep -q "static void publish_state(void)" "$_pnc" \
    || secfail "pn-init veroeffentlicht seine Lage nicht mehr (publish_state fehlt) -- ein Deckelueberlauf waere wieder nur eine Zeile im Bootlog, und pnctl muesste die name->pid-Zuordnung wieder raten"
fi
sec_ok

# 22) Es darf GENAU EINE Update-Welt im Baum stehen. Bis 07.08.2026 lagen zwei vollstaendige
#     Selbst-Update-Implementierungen nebeneinander: Go (installer/internal/selfupdate, live via
#     `pn-factory update`) und Python (engine/pnlib/update, VERWAIST -- kein Aufrufer ausser den
#     eigenen Tests). Zwei Welten heisst: dieselbe Sicherheitszusage (Ed25519-Signatur unter dem
#     UPDATE-Domain-Tag, Downgrade-Sperre) muss zweimal richtig sein, und ein Leser weiss nie,
#     welche gilt. Die Python-Welt ist nach ~/.brainbox-backups/update-python-welt-* archiviert
#     (verify.py bleibt dort die REFERENZ fuer die Go-Portierung, S-17/S-03). Diese Sperre haelt
#     den Zustand: kommt die zweite Welt zurueck, wird das Abbild nicht ausgeliefert.
if [ -d "$REPO/engine/pnlib/update" ]; then
  secfail "zwei Update-Welten: engine/pnlib/update/ ist wieder da neben installer/internal/selfupdate -- eine Sicherheitszusage, die zweimal implementiert ist, ist zweimal ein Risiko; die verwaiste Python-Welt gehoert archiviert (S-42)"
fi
[ -d "$REPO/installer/internal/selfupdate" ] \
  || secfail "keine Update-Welt: installer/internal/selfupdate/ fehlt -- die eine, die bleiben soll, ist weg"
sec_ok

# ⛔ SELBSTPROBE. Sperre 19 stand bis 07.08.2026 in Zeile 13 — VOR der Definition von
# secfail()/sec_ok(). Die Shell fand die Kommandos nicht, schrieb je eine Zeile auf stderr
# und lief weiter; `fail` blieb 0 und das Gate meldete OK. Eine Sicherheitssperre am
# falschen Platz sieht im Quelltext vollstaendig aus und prueft nichts.
# Deshalb: keine BENUTZUNG darf vor der DEFINITION stehen. Statisch, ohne Fehlalarm.
_def_zeile="$(grep -n '^secfail()' "$0" | head -1 | cut -d: -f1)"
_erste_nutzung="$(grep -nE '(^|[^A-Za-z_])(secfail|sec_ok)([^A-Za-z_(]|$)' "$0" \
                  | grep -vE '^[0-9]+:[[:space:]]*#' | head -1 | cut -d: -f1)"
if [ -n "$_def_zeile" ] && [ -n "$_erste_nutzung" ] && [ "$_erste_nutzung" -lt "$_def_zeile" ]; then
  note "release-gate: eine Sicherheitssperre steht in Zeile $_erste_nutzung, secfail() wird erst in Zeile $_def_zeile definiert -- diese Sperre laeuft NICHT (Kommando nicht gefunden) und das Gate wuerde trotzdem OK melden"
fi
echo "  Sicherheits-Rueckfallsperren: $nsec Durchlaeufe, erste Sperre in Zeile ${_erste_nutzung:-?} (Definition: ${_def_zeile:-?})"

# ── acceptance gate ───────────────────────────────────────────────────────────────────────────
# Everything above is STATIC: syntax, presence, package names. All of it was green on the image
# whose Sessions, Screen and Claude sign-in were dead on arrival. acceptance.sh --image boots the
# artifact in QEMU, drives the first-run wizard over HTTP and runs the whole feature suite against
# the freshly installed system, exiting non-zero on any failure. Its verdict is THE release verdict.
#
# It costs minutes, and the same script is a pre-commit hook for a 5-second syntax check. A gate
# nobody runs is worthless; a gate that blocks a syntax check for ten minutes gets disabled by the
# first person in a hurry. Hence three modes, and a mode line printed in EVERY run so a green
# "release-gate: OK" can never be mistaken for "the image was proven" when it wasn't.
ACC_MODE="SKIPPED"; ACC_WHY=""
HARNESS="$HERE/acceptance.sh"

# Resolve the image: explicit wins, else the artifacts the builder writes to OUT_DIR.
acc_img="$ACCEPTANCE_IMAGE"
if [ -z "$acc_img" ]; then
  for cand in "${OUT_DIR:-/var/tmp/bbx}/brainbox-appliance-amd64.raw" \
              "${OUT_DIR:-/var/tmp/bbx}/brainbox-appliance-amd64.qcow2"; do
    [ -r "$cand" ] && { acc_img="$cand"; break; }
  done
fi

case "$ACCEPTANCE" in
  0|off|no|skip)
    ACC_WHY="ACCEPTANCE=0 — opted out"
    echo "  acceptance gate: SKIPPED — $ACC_WHY (this run did NOT prove the image works)"
    ;;
  1|on|yes|full|auto)
    if [ ! -s "$HARNESS" ]; then
      # In auto this is only a skip; if acceptance was REQUIRED, a missing harness is a failure.
      ACC_WHY="harness missing: $HARNESS"
      [ "$ACCEPTANCE" = auto ] || note "ACCEPTANCE REQUIRED but the harness is missing: $HARNESS"
    elif [ -z "$acc_img" ]; then
      # (b) Never silently pass. Say exactly what was looked for and how to supply it.
      ACC_WHY="no image found (looked at ACCEPTANCE_IMAGE, --image, ${OUT_DIR:-/var/tmp/bbx}/brainbox-appliance-amd64.{raw,qcow2})"
      if [ "$ACCEPTANCE" = auto ]; then
        echo "  acceptance gate: SKIPPED — $ACC_WHY"
        echo "                   this run did NOT prove the image works. Build one, or pass --image PATH."
      else
        note "ACCEPTANCE REQUIRED but $ACC_WHY — pass --image PATH (refusing to pass without proof)"
      fi
    elif [ "$fail" != 0 ]; then
      # The tree is already broken; booting a VM for ten minutes to confirm it would only delay the
      # answer the operator already has.
      ACC_WHY="static checks already FAILED — not spending minutes booting a known-broken tree"
      echo "  acceptance gate: SKIPPED — $ACC_WHY"
    else
      ACC_MODE="FULL"
      echo "  acceptance gate: FULL — booting $acc_img and running the feature suite (this takes minutes)"
      if bash "$HARNESS" --image "$acc_img" ${ACCEPTANCE_ARGS:-}; then
        echo "  acceptance gate: PASSED — the built image was proven to WORK, not just to boot"
      else
        note "ACCEPTANCE FAILED (exit $?) for $acc_img — this image is NOT shippable (it failed to boot, to complete the wizard, or a feature is dead); see the harness output above"
      fi
    fi
    ;;
  *)
    note "unknown ACCEPTANCE=$ACCEPTANCE (use 1 / 0 / auto)"
    ACC_WHY="unknown mode"
    ;;
esac
if [ "$fail" = 0 ]; then
  echo "release-gate: OK ($nfiles scripts checked, $nart artifacts present, $nhelp runtime helpers staged, packages + boot chain OK, acceptance=$ACC_MODE)"
  [ "$ACC_MODE" = FULL ] || echo "release-gate: NOTE — acceptance was $ACC_MODE, so this OK covers the SOURCE only, not the built image."
else
  echo "release-gate: FAILED — fix the above before committing/building. (acceptance=$ACC_MODE)"
  exit 1
fi
