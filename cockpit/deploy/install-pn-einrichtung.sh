#!/bin/sh
set -eu

WURZEL=$(cd "$(dirname "$0")/../.." && pwd)
QUELLE="$WURZEL/os/init/local-sbin/pn-einrichtung-schreiben"
ZIEL=/usr/local/sbin/pn-einrichtung-schreiben
REGEL_QUELLE="$WURZEL/cockpit/deploy/pn-einrichtung.sudoers"
REGEL=/etc/sudoers.d/pn-einrichtung
KONTO=${1:-$(getent passwd 1000 | cut -d: -f1)}

[ "$(id -u)" = 0 ] || { echo "Bitte als root aufrufen."; exit 1; }
[ -f "$QUELLE" ] || { echo "Fehlt: $QUELLE"; exit 1; }
[ -n "$KONTO" ] || { echo "Kein Eignerkonto gefunden — bitte als Argument angeben."; exit 1; }
id "$KONTO" >/dev/null 2>&1 || { echo "Unbekanntes Konto: $KONTO"; exit 1; }

python3 -c "import py_compile,sys; py_compile.compile(sys.argv[1], doraise=True)" "$QUELLE"

install -o root -g root -m 0755 "$QUELLE" "$ZIEL"

VORLAUF=$(mktemp)
trap 'rm -f "$VORLAUF"' EXIT
sed "s/^SERVICE_USER /$KONTO /" "$REGEL_QUELLE" > "$VORLAUF"
visudo -cqf "$VORLAUF" || { echo "visudo lehnt die Regel ab — nichts geändert."; exit 1; }
install -o root -g root -m 0440 "$VORLAUF" "$REGEL"

echo "installiert: $ZIEL"
echo "Regel:       $REGEL (fuer $KONTO)"
sudo -n -u "$KONTO" true 2>/dev/null || true
echo "Probe:       $(echo '{}' | su -s /bin/sh -c "sudo -n $ZIEL nas" "$KONTO" 2>&1 | head -1)"
