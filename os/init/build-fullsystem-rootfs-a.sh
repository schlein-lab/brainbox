#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMG="${IMG:-/var/tmp/pn-rootfs-a.img}"
SIZE="${SIZE:-2600}"
SUITE="${SUITE:-noble}"
MIRROR="${MIRROR:-http://archive.ubuntu.com/ubuntu}"
PNINIT="${PNINIT:-$HERE/pn-init}"
PNCONF="${PNCONF:-/tmp/pn-init.conf.vmdev}"
[ "$(id -u)" = 0 ] || { echo "must run as root"; exit 1; }
[ -x "$PNINIT" ]  || { echo "build pn-init first: $PNINIT"; exit 1; }
[ -r "$PNCONF" ]  || { echo "option-a conf not found: $PNCONF"; exit 1; }

MNT="$(mktemp -d)"
cleanup(){ set +e; umount -R "$MNT" 2>/dev/null; losetup -d "$LOOP" 2>/dev/null; rmdir "$MNT" 2>/dev/null; }
trap cleanup EXIT

echo "[1/8] create ${SIZE}MiB ext4 image at $IMG"
rm -f "$IMG"
truncate -s "${SIZE}M" "$IMG"
mkfs.ext4 -q -F -L pnroot "$IMG"
LOOP="$(losetup --show -f "$IMG")"
mount "$LOOP" "$MNT"

echo "[2/8] debootstrap $SUITE with REAL bits (dbus + systemd user manager) — slow step"
debootstrap --variant=minbase \
  --include=openssh-server,udev,iproute2,busybox-static,python3,kmod,libpam-modules,dbus,systemd,systemd-sysv,libpam-systemd \
  "$SUITE" "$MNT" "$MIRROR" >/tmp/debootstrap-a.log 2>&1 || { echo "debootstrap FAILED"; tail -40 /tmp/debootstrap-a.log; exit 1; }

echo "[3/8] base config (hostname, user 1000 'pn', sshd password login)"
echo "pn-guest-a" > "$MNT/etc/hostname"
cat > "$MNT/etc/hosts" <<EOF
127.0.0.1 localhost
127.0.1.1 pn-guest-a
EOF
chroot "$MNT" useradd -m -u 1000 -s /bin/bash pn 2>/dev/null || true
echo 'pn:pntest' | chroot "$MNT" chpasswd
echo 'root:pntest' | chroot "$MNT" chpasswd
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' "$MNT/etc/ssh/sshd_config"
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/'              "$MNT/etc/ssh/sshd_config"
chroot "$MNT" ssh-keygen -A >/dev/null 2>&1 || true
mkdir -p "$MNT/run/sshd"
rm -f "$MNT/etc/resolv.conf"; : > "$MNT/etc/resolv.conf"

echo "[4/8] fstab + netplan (mirror the reference dev shape)"
cat > "$MNT/etc/fstab" <<EOF
LABEL=pnroot   /        ext4   defaults        0 1
/swap.img      none     swap   sw              0 0
EOF
dd if=/dev/zero of="$MNT/swap.img" bs=1M count=128 status=none
chmod 600 "$MNT/swap.img"
mkswap "$MNT/swap.img" >/dev/null 2>&1 || true
mkdir -p "$MNT/etc/netplan"
cat > "$MNT/etc/netplan/01-network-manager-all.yaml" <<EOF
network:
  version: 2
  renderer: NetworkManager
EOF

echo "[5/8] install pn-init + udhcpc lease script"
install -m 0755 "$PNINIT"  "$MNT/sbin/pn-init"
install -d -m 0755 "$MNT/usr/share/udhcpc"
cat > "$MNT/usr/share/udhcpc/default.script" <<'EOF'
#!/bin/sh
[ -n "$1" ] || exit 1
case "$1" in
  deconfig) ip addr flush dev "$interface" 2>/dev/null ;;
  bound|renew)
    PFX="${mask:-24}"
    case "$subnet" in 255.255.255.0) PFX=24;; 255.255.0.0) PFX=16;; 255.0.0.0) PFX=8;;
      255.255.255.128) PFX=25;; 255.255.255.192) PFX=26;; esac
    ip addr flush dev "$interface" 2>/dev/null
    ip addr add "$ip/$PFX" dev "$interface" 2>/dev/null
    ip link set "$interface" up 2>/dev/null
    [ -n "$router" ] && ip route replace default via "$router" dev "$interface" 2>/dev/null
    : > /etc/resolv.conf
    [ -n "$domain" ] && echo "search $domain" >> /etc/resolv.conf
    for d in $dns; do echo "nameserver $d" >> /etc/resolv.conf; done
    echo "[udhcpc] applied $ip/$PFX via ${router:-?} dns=${dns:-none}"
    ;;
esac
exit 0
EOF
chmod 0755 "$MNT/usr/share/udhcpc/default.script"

echo "[6/8] /etc/pn-init.conf = OPTION (a) staged conf ($PNCONF)"
install -m 0644 "$PNCONF" "$MNT/etc/pn-init.conf"
echo "  --- installed pn-init.conf ---"; sed -n '1,200p' "$MNT/etc/pn-init.conf" | grep -vE '^\s*#|^\s*$' || true

echo "[7/8] REAL --user unit pntest.service for uid 1000 + enable + linger"
install -d -m 0755 "$MNT/usr/local/bin"
cat > "$MNT/usr/local/bin/pntest-run" <<'EOF'
#!/bin/sh
# pntest-run — stands in for the pn --user stack. Proves a --user unit started under
# `systemd --user` (itself launched by the non-systemd PID1 pn-init).
echo "[pntest] USER_UNIT_UP uid=$(id -u) XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR" > /dev/console 2>/dev/null || true
echo "[pntest] USER_UNIT_UP uid=$(id -u) XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"
: > "${XDG_RUNTIME_DIR:-/run/user/1000}/pntest.up" 2>/dev/null || true
# create a listening unix socket marker too (mirrors pnd.sock), best-effort via python3
python3 - "$XDG_RUNTIME_DIR/pntest.sock" <<'PY' 2>/dev/null &
import socket,sys,os,time
p=sys.argv[1]
try:
    try: os.unlink(p)
    except OSError: pass
    s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.bind(p); s.listen(1)
    while True: time.sleep(60)
except Exception: 
    while True: time.sleep(60)
PY
while true; do sleep 30; done
EOF
chmod 0755 "$MNT/usr/local/bin/pntest-run"

install -d -m 0755 "$MNT/home/pn/.config/systemd/user"
cat > "$MNT/home/pn/.config/systemd/user/pntest.service" <<'EOF'
[Unit]
Description=pn stack stand-in (--user unit; proves option (a))

[Service]
Type=simple
ExecStart=/usr/local/bin/pntest-run
Restart=on-failure

[Install]
WantedBy=default.target
EOF
install -d -m 0755 "$MNT/home/pn/.config/systemd/user/default.target.wants"
ln -sf ../pntest.service "$MNT/home/pn/.config/systemd/user/default.target.wants/pntest.service"
chroot "$MNT" chown -R pn:pn /home/pn/.config 2>/dev/null || true
install -d -m 0755 "$MNT/var/lib/systemd/linger"
: > "$MNT/var/lib/systemd/linger/pn"

echo "[8/8] cleanup APT caches"
chroot "$MNT" apt-get clean >/dev/null 2>&1 || true
rm -rf "$MNT/var/lib/apt/lists/"* "$MNT/var/cache/apt/archives/"*.deb 2>/dev/null || true
if [ -x "$MNT/usr/lib/systemd/systemd" ] || [ -x "$MNT/lib/systemd/systemd" ]; then
  echo "  OK: systemd manager binary present"
else
  echo "  WARN: /usr/lib/systemd/systemd NOT found — option (a) needs it"; ls -l "$MNT"/usr/lib/systemd/systemd "$MNT"/lib/systemd/systemd 2>/dev/null || true
fi
[ -x "$MNT/usr/bin/dbus-daemon" ] && echo "  OK: dbus-daemon present" || echo "  WARN: dbus-daemon missing"
sync
echo "DONE: $IMG ($(du -h "$IMG" | cut -f1)); root label=pnroot, user pn/uid1000 pw=pntest"
