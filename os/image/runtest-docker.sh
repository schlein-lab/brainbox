#!/usr/bin/env bash
set -uo pipefail
OUT=${OUT_DIR:-/var/tmp/bbx}
HERE="$(cd "$(dirname "$0")" && pwd)"
VER="$(cat "$HERE/VERSION" 2>/dev/null | tr -d ' \n')"; VER="${VER:-1.0.0}"
NAME=bbxprobe
ok=0; bad=0
ck(){ if [ "$1" = 0 ]; then echo "  PASS  $2"; ok=$((ok+1)); else echo "  FAIL  $2 ${3:+-- $3}"; bad=$((bad+1)); fi; }
inc(){ sudo docker exec "$NAME" sh -c "$1" 2>&1; }

echo "@@@ 1 Container-Archiv bauen @@@"
sudo env OUT_DIR="$OUT" bash "$HERE/build-docker-image.sh" 2>&1 | tail -20
[ -s "$OUT/brainbox-$VER-docker-amd64.tar.xz" ]; ck $? "Archiv gebaut (brainbox-$VER-docker-amd64.tar.xz)"
[ -s "$OUT/Dockerfile.brainbox" ]; ck $? "Dockerfile daneben gelegt"

echo "@@@ 2 Importieren @@@"
sudo docker rm -f $NAME >/dev/null 2>&1
sudo docker rmi -f brainbox:$VER >/dev/null 2>&1
sudo docker volume rm bbxprobe-data >/dev/null 2>&1
sudo docker import "$OUT/brainbox-$VER-docker-amd64.tar.xz" brainbox:$VER >/dev/null 2>&1
ck $? "docker import"
sudo docker image inspect brainbox:$VER >/dev/null 2>&1; ck $? "Abbild vorhanden"
echo "  Groesse: $(sudo docker image inspect brainbox:$VER --format '{{.Size}}' 2>/dev/null | awk '{printf "%.1f GB", $1/1e9}')"

echo "@@@ 3 Starten wie in docs/docker.md @@@"
KVM=""; [ -e /dev/kvm ] && KVM="--device /dev/kvm"
sudo docker run -d --name $NAME $KVM \
  -p 18080:80 -p 18076:8076 \
  -v bbxprobe-data:/home/brainbox \
  --memory 4g --cpus 3 --stop-signal SIGUSR1 \
  brainbox:$VER /sbin/pn-init >/dev/null 2>&1
ck $? "Container gestartet"

echo "@@@ 4 Warten, bis der Assistent antwortet (bis 240 s) @@@"
gefunden=0
for i in $(seq 1 80); do
  code=$(curl -s -o /dev/null -m 3 -w '%{http_code}' http://127.0.0.1:18080/ 2>/dev/null)
  case "$code" in 200|30?) gefunden=1; echo "  Assistent antwortet nach ~$((i*3)) s (HTTP $code)"; break;; esac
  sleep 3
done
[ "$gefunden" = 1 ]; ck $? "Einrichtungs-Assistent auf :80 erreichbar"

echo "@@@ 5 Haben die Container-Schalter gewirkt? @@@"
LOG="$(sudo docker logs $NAME 2>&1)"
echo "$LOG" | grep -q 'sacred-only bring-up' && ck 1 "pn.allow_uncapped hat NICHT gewirkt (sacred-only)" || ck 0 "pn.allow_uncapped wirkt (volle Bootkette)"
echo "$LOG" | grep -q 'pn.nonet' && ck 0 "pn.nonet wirkt (kein DHCP-Versuch im Container)" || ck 1 "pn.nonet hat nicht gewirkt"
echo "$LOG" | grep -q 'udhcpc: no lease' && ck 1 "es wurde doch DHCP versucht" || ck 0 "kein vergeblicher DHCP-Versuch"

echo "@@@ 6 Was laeuft drin? @@@"
inc 'ps -eo comm= 2>/dev/null | sort -u | tr "\n" " "' | head -c 600; echo
for d in pn-init sshd brainbox-setup; do
  inc "ps -eo args= | grep -v grep | grep -q '$d'" >/dev/null 2>&1
  ck $? "$d laeuft"
done
inc 'test -s /var/lib/brainbox/firstboot.done' >/dev/null 2>&1
ck $? "firstboot ist durchgelaufen (Identitaet, Schluessel, Portal-Saat)"
inc 'ls /etc/ssh/ssh_host_ed25519_key >/dev/null 2>&1' >/dev/null 2>&1
ck $? "eigene SSH-Hostkeys erzeugt"
N_GETTY="$(inc 'ps -eo args= | grep -c "[a]getty"' 2>/dev/null | tr -dc '0-9')"; N_GETTY="${N_GETTY:-0}"
[ "$N_GETTY" = 0 ]; ck $? "kein getty im Container (haette sich endlos neu gestartet)" "gefunden: $N_GETTY"

echo "@@@ 7 Der Bildschirm-Ersatz: sagt das Protokoll, was laeuft? @@@"
echo "$LOG" | grep -qE 'Schritt [1-6] von 6'
ck $? "die Startschritte stehen im Protokoll"

echo "@@@ 8 Zellen ehrlich gemeldet? @@@"
inc 'cat /etc/brainbox/caps.env 2>/dev/null' | head -5

echo "@@@ 9 Ordentlich stoppen @@@"
t0=$(date +%s)
sudo docker stop -t 60 $NAME >/dev/null 2>&1
t1=$(date +%s)
echo "  Stopp dauerte $((t1-t0)) s"
[ $((t1-t0)) -lt 30 ]; ck $? "Stopp lief geordnet durch (kein Ablauf der Frist)" "$((t1-t0)) s"

echo "----"
echo "$ok PASS, $bad FAIL"
[ "$bad" = 0 ] && echo "@@@ DOCKER_GRUEN @@@" || echo "@@@ DOCKER_ROT @@@"
