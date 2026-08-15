#!/usr/bin/env bash
set -euo pipefail
OUT_DIR=${OUT_DIR:-/var/tmp/bbx}
SRC_RAW=${SRC_RAW:-$OUT_DIR/brainbox-appliance-amd64.raw}
HERE="$(cd "$(dirname "$0")" && pwd)"
VER="$(cat "$HERE/VERSION" 2>/dev/null | tr -d ' \n')"; VER="${VER:-0.0.0}"
TAR="$OUT_DIR/brainbox-$VER-docker-amd64.tar"
DOCKERFILE="$OUT_DIR/Dockerfile.brainbox"
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
[ -r "$SRC_RAW" ] || { echo "FATAL: $SRC_RAW fehlt (erst build-appliance-disk.sh laufen lassen)"; exit 1; }

say(){ echo "==== [docker] $* ===="; }
MNT="$(mktemp -d)"; LOOP=""
cleanup(){ set +e; umount -R "$MNT" 2>/dev/null; [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null; rmdir "$MNT" 2>/dev/null; }
trap cleanup EXIT

say "1. Appliance-Baum schreibgeschuetzt einhaengen"
LOOP="$(losetup --show -fP -r "$SRC_RAW")"
PART="${LOOP}p1"; [ -e "$PART" ] || PART="${LOOP}1"
mount -o ro "$PART" "$MNT"

say "2. Dienstliste fuer den Container ableiten (nicht neu erfinden)"
WORK="$(mktemp -d)"
{
  echo "# /etc/pn-init.conf -- CONTAINER-Fassung, abgeleitet aus der Bootkette des Images."
  echo "# Erzeugt von os/image/build-docker-image.sh. Nicht von Hand pflegen: die Quelle ist"
  echo "# die Kette des Appliance-Images; hier fallen nur die Eintraege heraus, die in einem"
  echo "# Container nicht laufen koennen. Wer einen Dienst vermisst, sucht ihn dort."
  echo "#   getty-*   : es gibt keine Konsole; agetty auf einem nicht vorhandenen tty stirbt"
  echo "#               sofort und wuerde endlos neu gestartet."
  echo "#   firewall  : Netzregeln gehoeren dem Wirt; nftables braucht CAP_NET_ADMIN."
  echo "#   netcfg    : die Adresse vergibt Docker."
  echo "#   chronyd   : die Uhr stellt der Wirt-Kernel; im Container ist sie nicht setzbar."
  echo "#   mediashare-*: SMB/DLNA im Container sind eine Wirt-Entscheidung, keine Vorgabe."
  grep -vE '^\s*#|^\s*$' "$MNT/etc/pn-init.conf" \
    | grep -vE '^(getty-vga|getty-ser|firewall|netcfg|chronyd|mediashare-smbd|mediashare-dlna)\|'
} > "$WORK/pn-init.conf"
echo "  Container-Kette:"; grep -vE '^\s*#' "$WORK/pn-init.conf" | cut -d'|' -f1 | tr '\n' ' '; echo
for muss in earlyboot banner firstboot brainbox-setup brainbox-portal sshd; do
  grep -qE "^$muss\|" "$WORK/pn-init.conf" \
    || { echo "FATAL: $muss fehlt in der Container-Kette"; exit 1; }
done

say "3. Wurzelbaum einpacken (ohne Kernel, Module, Caches)"
rm -f "$TAR" "$TAR.xz"
tar --numeric-owner --acls --xattrs \
    --exclude='./boot/*' \
    --exclude='./lib/modules/*' \
    --exclude='./usr/lib/modules/*' \
    --exclude='./var/cache/apt/archives/*' \
    --exclude='./var/lib/apt/lists/*' \
    --exclude='./proc/*' --exclude='./sys/*' --exclude='./dev/*' --exclude='./run/*' \
    --exclude='./var/tmp/*' --exclude='./tmp/*' \
    -C "$MNT" -cf "$TAR" .

say "4. Container-Eigenheiten in das Archiv nachtragen"
ADD="$(mktemp -d)"
mkdir -p "$ADD/etc/brainbox"
cp "$WORK/pn-init.conf" "$ADD/etc/pn-init.conf"
cat > "$ADD/etc/pn-init.cmdline" <<'EOF'
# Startschalter fuer pn-init im Container. Auf einer echten Box gibt es diese Datei nicht --
# dort stehen dieselben Schalter auf der Kernel-Kommandozeile. Im Container zeigt
# /proc/cmdline die Kommandozeile des WIRTS, die niemand je Container setzen kann.
#
# pn.allow_uncapped: Docker haengt /sys/fs/cgroup schreibgeschuetzt ein, pn-init kann die
# cgroup2-Schichten also nicht selbst schneiden. Ohne diesen Schalter faellt es auf
# "nur sacred" zurueck und die Einmal-Schritte (firstboot) liefen nie. Die Grenzen setzt
# hier die aeussere Laufzeit: docker run --memory / --cpus.
pn.allow_uncapped
pn.fullsystem
#
# pn.nonet: die Adresse hat der Container laengst -- Docker vergibt sie, bevor PID1 laeuft.
# Ohne diesen Schalter versucht pn-init 15 Sekunden lang, sie selbst einzurichten, scheitert
# an fehlenden Rechten (RTNETLINK: Operation not permitted) und meldet einen Fehlschlag, der
# keiner ist.
pn.nonet
#
# pn.container: beim Herunterfahren darf ein Container kein reboot(2). Auf Blech waere
# "manual power cycle needed" die richtige Aussage; hier gibt es niemanden, der das tun
# koennte -- das Beenden von PID1 IST das Ausschalten. Ohne den Schalter haengt
# "docker stop" bis zur Frist und Docker schiesst mit SIGKILL nach.
pn.container
EOF
echo "container" > "$ADD/etc/brainbox/platform"
tar --numeric-owner -C "$ADD" -rf "$TAR" ./etc
rm -rf "$ADD" "$WORK"

say "5. Dockerfile daneben legen"
cat > "$DOCKERFILE" <<EOF
# Brainbox $VER — Container-Fassung.
# Bauen:  docker build -f Dockerfile.brainbox -t brainbox:$VER .
#         (brainbox-$VER-docker-amd64.tar.xz muss im selben Verzeichnis liegen)
# ADD packt .tar.xz selbst aus -- das Archiv muss nicht vorher entpackt werden.
FROM scratch
ADD brainbox-$VER-docker-amd64.tar.xz /
LABEL org.opencontainers.image.title="Brainbox"
LABEL org.opencontainers.image.version="$VER"
LABEL org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0"
LABEL org.opencontainers.image.source="https://github.com/schlein-lab/brainbox"
# :80 Einrichtungs-Assistent, :8076 Portal (HTTPS), :22 SSH
EXPOSE 80 8076 22
STOPSIGNAL SIGUSR1
CMD ["/sbin/pn-init"]
EOF

say "6. Komprimieren"
nice -n 19 ionice -c3 xz -T2 -3 -f "$TAR"
ls -lh "$TAR.xz" "$DOCKERFILE"
echo "DOCKER_IMAGE_DONE $TAR.xz"
