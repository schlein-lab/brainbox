#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMG="${IMG:-/var/tmp/pn-nosystemd.img}"
SIZE="${SIZE:-2600}"
SUITE="${SUITE:-trixie}"
MIRROR="${MIRROR:-http://deb.debian.org/debian}"
PNINIT="${PNINIT:-$HERE/../init/pn-init}"
PNDSTUB="${PNDSTUB:-$HERE/../init/pndstub}"
CONF="${CONF:-$HERE/mkosi.extra/etc/pn-init.conf}"
[ "$(id -u)" = 0 ] || { echo "must run as root"; exit 1; }
[ -x "$PNINIT" ]  || { echo "build pn-init first (cd ../init && make musl): $PNINIT"; exit 1; }

MNT="$(mktemp -d)"
cleanup(){ set +e; umount -R "$MNT" 2>/dev/null; losetup -d "$LOOP" 2>/dev/null; rmdir "$MNT" 2>/dev/null; }
trap cleanup EXIT

echo "[1/6] ${SIZE}MiB ext4 image $IMG"
rm -f "$IMG"; truncate -s "${SIZE}M" "$IMG"; mkfs.ext4 -q -F -L brainarbeit-root "$IMG"
LOOP="$(losetup --show -f "$IMG")"; mount "$LOOP" "$MNT"

echo "[2/6] debootstrap $SUITE with the no-systemd base set (sysvinit-core + busybox mdev, NO systemd)"
debootstrap --variant=minbase \
  --include=sysvinit-core,dbus,openssh-server,busybox,iproute2,udhcpc,python3,kmod,libpam-modules,procps,iputils-ping \
  --exclude=systemd-sysv,systemd-resolved,systemd-timesyncd \
  "$SUITE" "$MNT" "$MIRROR" >/tmp/debootstrap-ns.log 2>&1 || { echo "debootstrap FAILED"; tail -30 /tmp/debootstrap-ns.log; exit 1; }

echo "[3/6] PROVE the rootfs is systemd-free"
if [ -x "$MNT/lib/systemd/systemd" ] || [ -x "$MNT/usr/lib/systemd/systemd" ]; then
  echo "  !! systemd daemon present in rootfs — purging"; chroot "$MNT" apt-get -y purge systemd 2>/dev/null || true
fi
echo "  init provider: $(readlink -f "$MNT/sbin/init" 2>/dev/null || echo '?')"
echo "  systemd daemon present? $( ([ -x "$MNT/lib/systemd/systemd" ] || [ -x "$MNT/usr/lib/systemd/systemd" ]) && echo YES || echo NO )"

echo "[4/6] host/user/sshd/fstab/udhcpc, install pn-init + the REAL pn-init.conf"
echo brainarbeit > "$MNT/etc/hostname"
printf '127.0.0.1 localhost\n127.0.1.1 brainarbeit\n' > "$MNT/etc/hosts"
chroot "$MNT" useradd -m -u 1000 -s /bin/bash brainarbeit 2>/dev/null || \
  chroot "$MNT" useradd -m -u 1000 -s /bin/bash pn 2>/dev/null || true
U="$(awk -F: '$3==1000{print $1}' "$MNT/etc/passwd" | head -1)"; U="${U:-pn}"
echo "${U}:pntest" | chroot "$MNT" chpasswd
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' "$MNT/etc/ssh/sshd_config"
chroot "$MNT" ssh-keygen -A >/dev/null 2>&1 || true
mkdir -p "$MNT/run/sshd"
cat > "$MNT/etc/fstab" <<EOF
LABEL=brainarbeit-root  /     ext4  defaults  0 1
EOF
install -d -m0755 "$MNT/usr/share/udhcpc"
cat > "$MNT/usr/share/udhcpc/default.script" <<'EOF'
#!/bin/sh
[ -n "$1" ] || exit 1
case "$1" in
 deconfig) ip addr flush dev "$interface" 2>/dev/null ;;
 bound|renew)
   PFX=24; case "$subnet" in 255.255.255.0)PFX=24;;255.255.0.0)PFX=16;;255.0.0.0)PFX=8;;esac
   ip addr flush dev "$interface" 2>/dev/null; ip addr add "$ip/$PFX" dev "$interface" 2>/dev/null
   ip link set "$interface" up 2>/dev/null
   [ -n "$router" ] && ip route replace default via "$router" dev "$interface" 2>/dev/null
   : > /etc/resolv.conf; for d in $dns; do echo "nameserver $d" >> /etc/resolv.conf; done
   echo "[udhcpc] applied $ip/$PFX" ;;
esac; exit 0
EOF
chmod 0755 "$MNT/usr/share/udhcpc/default.script"
rm -f "$MNT/etc/resolv.conf"; : > "$MNT/etc/resolv.conf"

install -m0755 "$PNINIT"  "$MNT/usr/lib/brainarbeit/pn-init" 2>/dev/null || { install -d "$MNT/usr/lib/brainarbeit"; install -m0755 "$PNINIT" "$MNT/usr/lib/brainarbeit/pn-init"; }
install -m0755 "$PNINIT"  "$MNT/sbin/pn-init"
[ -x "$PNDSTUB" ] && install -m0755 "$PNDSTUB" "$MNT/bin/pndstub"

install -d "$MNT/usr/lib/brainarbeit"
cat > "$MNT/usr/lib/brainarbeit/run-engine" <<'EOF'
#!/bin/sh
# run-engine <component> [args...] — stand-in for the pinned on-DATA venv entry point.
comp="$1"; shift 2>/dev/null || true
case "$comp" in
  pnd)     echo "[pnd] up uid=$(id -u) XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR sock=$XDG_RUNTIME_DIR/pnd.sock"
           exec /bin/pndstub "" "$XDG_RUNTIME_DIR/pnd.sock" ;;
  pn-llmd) echo "[pn-llmd] up uid=$(id -u) MODEL=$PN_LLM_MODEL POOL=$PN_LLM_POOL"; exec sleep infinity ;;
  pn-portal) echo "[portal] up uid=$(id -u)"; exec sleep infinity ;;
  zyrkel)  echo "[zyrkel] up uid=$(id -u) PATH=$PATH"; exec sleep infinity ;;
  reprofleet) echo "[reprofleet] up uid=$(id -u)"; exec sleep infinity ;;
  *)       echo "[run-engine] unknown component: $comp"; exec sleep infinity ;;
esac
EOF
chmod 0755 "$MNT/usr/lib/brainarbeit/run-engine"
cp "$MNT/usr/lib/brainarbeit/run-engine" "$MNT/usr/lib/brainarbeit/run-display"

install -D -m0644 "$CONF" "$MNT/etc/pn-init.conf"

cat > "$MNT/usr/local/bin/pn-selftest" <<'EOF'
#!/bin/sh
sleep 10
echo "==== PN-SELFTEST BEGIN ===="
echo "[selftest] PID1 comm: $(cat /proc/1/comm)"
echo "[selftest] systemd processes: $(ps -e -o comm= 2>/dev/null | grep -c '^systemd' || echo 0)"
echo "[selftest] systemd proc list: $(ps -e -o pid=,comm= 2>/dev/null | grep systemd || echo NONE)"
echo "[selftest] ip: $(ip -br addr 2>/dev/null | tr '\n' '|')"
echo "[selftest] sshd :22: $( (ss -tln 2>/dev/null||busybox netstat -tln 2>/dev/null)|grep -q ':22 ' && echo LISTENING || echo NO)"
echo "[selftest] /run/user/1000: $(ls -ld /run/user/1000 2>/dev/null||echo MISSING)"
echo "==== PN-SELFTEST END ===="
EOF
chmod 0755 "$MNT/usr/local/bin/pn-selftest"
printf '\nselftest|oneshot|/usr/local/bin/pn-selftest\n' >> "$MNT/etc/pn-init.conf"

echo "[5/6] shrink"
chroot "$MNT" apt-get clean >/dev/null 2>&1 || true
rm -rf "$MNT/var/lib/apt/lists/"* "$MNT/var/cache/apt/archives/"*.deb 2>/dev/null || true
echo "[6/6] sync"; sync
echo "DONE: $IMG ($(du -h "$IMG"|cut -f1)); user=$U pw=pntest; root label=brainarbeit-root"
