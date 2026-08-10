#!/bin/sh
set -eu

WURZEL=$(cd "$(dirname "$0")/../.." && pwd)
QUELLE="$WURZEL/os/init/local-sbin/pn-bremsenwache"
ZIEL=/usr/local/sbin/pn-bremsenwache
CONF=/etc/pn-init.conf
NAME=pn-bremsenwache
ZEILE="$NAME|sacred|/usr/bin/python3 $ZIEL --takt 60"

[ "$(id -u)" = 0 ] || { echo "Bitte als root aufrufen."; exit 1; }
[ -f "$QUELLE" ] || { echo "Fehlt: $QUELLE"; exit 1; }

python3 -c "import py_compile,sys; py_compile.compile(sys.argv[1], doraise=True)" "$QUELLE"
install -o root -g root -m 0755 "$QUELLE" "$ZIEL"
echo "installiert: $ZIEL"

[ "${1:-}" = "--nur-datei" ] && exit 0

if grep -q "^$NAME|" "$CONF"; then
  echo "Dienstzeile steht schon in $CONF"
else
  SICHER="$CONF.bak-vor-bremsenwache-$(date +%Y%m%d%H%M%S)"
  cp -a "$CONF" "$SICHER"
  TMP="$CONF.neu.$$"
  { cat "$CONF"
    echo
    echo "# ---- Bremsen-Waechter: haelt memory.high an der Marke, die pn-init rechnet, und meldet,"
    echo "#      wenn die Bremse ins Festgenagelte greift statt in den Seitencache. Sacred, damit er"
    echo "#      nicht gerade dann gedrosselt wird, wenn er gebraucht wird. ----"
    echo "$ZEILE"
  } > "$TMP"
  ALT=$(wc -l < "$CONF"); NEU=$(wc -l < "$TMP")
  [ "$NEU" -gt "$ALT" ] || { rm -f "$TMP"; echo "Neue Datei nicht laenger — abgebrochen."; exit 1; }
  mv "$TMP" "$CONF"
  chmod 0644 "$CONF"
  echo "Zeile eingetragen (Sicherung: $SICHER)"
fi

kill -HUP 1
echo "SIGHUP an PID 1 geschickt — pn-init liest die Konfiguration neu."
