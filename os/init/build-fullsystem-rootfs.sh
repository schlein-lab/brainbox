#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMG="${IMG:-/var/tmp/pn-rootfs.img}"
SIZE="${SIZE:-2200}"
SUITE="${SUITE:-noble}"
MIRROR="${MIRROR:-http://archive.ubuntu.com/ubuntu}"
PNINIT="${PNINIT:-$HERE/pn-init}"
PNDSTUB="${PNDSTUB:-$HERE/pndstub}"
[ "$(id -u)" = 0 ] || { echo "must run as root"; exit 1; }
[ -x "$PNINIT" ]  || { echo "build pn-init first (make musl): $PNINIT"; exit 1; }
[ -x "$PNDSTUB" ] || { echo "build pndstub first (make musl): $PNDSTUB"; exit 1; }

MNT="$(mktemp -d)"
cleanup(){ set +e; umount -R "$MNT" 2>/dev/null; losetup -d "$LOOP" 2>/dev/null; rmdir "$MNT" 2>/dev/null; }
trap cleanup EXIT

echo "[1/7] create ${SIZE}MiB ext4 image at $IMG"
rm -f "$IMG"
truncate -s "${SIZE}M" "$IMG"
mkfs.ext4 -q -F -L pnroot "$IMG"
LOOP="$(losetup --show -f "$IMG")"
mount "$LOOP" "$MNT"

echo "[2/7] debootstrap $SUITE (minimal) — this is the slow step"
debootstrap --variant=minbase \
  --include=openssh-server,udev,iproute2,busybox-static,python3,kmod,libpam-modules \
  "$SUITE" "$MNT" "$MIRROR" >/tmp/debootstrap.log 2>&1 || { echo "debootstrap FAILED"; tail -30 /tmp/debootstrap.log; exit 1; }

echo "[3/7] base config (hostname, user 1000, sshd, password login for the test)"
echo "pn-guest" > "$MNT/etc/hostname"
cat > "$MNT/etc/hosts" <<EOF
127.0.0.1 localhost
127.0.1.1 pn-guest
EOF
chroot "$MNT" useradd -m -u 1000 -s /bin/bash pn 2>/dev/null || true
echo 'pn:pntest' | chroot "$MNT" chpasswd
echo 'root:pntest' | chroot "$MNT" chpasswd
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' "$MNT/etc/ssh/sshd_config"
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/'              "$MNT/etc/ssh/sshd_config"
chroot "$MNT" ssh-keygen -A >/dev/null 2>&1 || true
mkdir -p "$MNT/run/sshd"
rm -f "$MNT/etc/resolv.conf"; : > "$MNT/etc/resolv.conf"

echo "[4/7] fstab + netplan (mirror the reference dev host's shape: root by-label, NM-rendered netplan)"
cat > "$MNT/etc/fstab" <<EOF
LABEL=pnroot   /        ext4   defaults        0 1
/swap.img      none     swap   sw              0 0
EOF
dd if=/dev/zero of="$MNT/swap.img" bs=1M count=128 status=none
chmod 600 "$MNT/swap.img"
mkswap "$MNT/swap.img" >/dev/null 2>&1 || true
mkdir -p "$MNT/etc/netplan"
cat > "$MNT/etc/netplan/01-network-manager-all.yaml" <<EOF
# Mirrors the reference dev host: NetworkManager renders the netplan. pn-init F3 does direct DHCP for the test.
network:
  version: 2
  renderer: NetworkManager
EOF

echo "[5/7] install pn-init + pndstub + the pn-stack --user session + udhcpc lease script"
install -m 0755 "$PNINIT"  "$MNT/sbin/pn-init"
install -m 0755 "$PNDSTUB" "$MNT/bin/pndstub"
install -d -m 0755 "$MNT/usr/share/udhcpc"
cat > "$MNT/usr/share/udhcpc/default.script" <<'EOF'
#!/bin/sh
# minimal udhcpc lease handler: apply IP/mask/router + write resolv.conf on bound/renew.
[ -n "$1" ] || exit 1
case "$1" in
  deconfig) ip addr flush dev "$interface" 2>/dev/null ;;
  bound|renew)
    # busybox udhcpc exports $subnet (dotted netmask); $mask (CIDR prefix) on some builds.
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
install -d -m 0755 "$MNT/usr/local/bin"
cat > "$MNT/usr/local/bin/pn-user-session" <<'EOF'
#!/bin/sh
# pn-user-session — stands in for `systemd --user` for uid 1000. Proves F4: it runs as uid 1000
# with a valid $XDG_RUNTIME_DIR and starts the pn --user stack. The real cutover would exec
# /usr/lib/systemd/systemd --user here instead, leaving the pn user UNITS byte-for-byte unchanged.
echo "[user-session] up as uid=$(id -u) HOME=$HOME XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"
[ -d "$XDG_RUNTIME_DIR" ] && echo "[user-session] XDG_RUNTIME_DIR exists and is writable: $( [ -w "$XDG_RUNTIME_DIR" ] && echo yes || echo no )"
# pnd: the real socket-speaking stub, on the per-user socket (mirrors $XDG_RUNTIME_DIR/pnd.sock)
PND_SOCK="$XDG_RUNTIME_DIR/pnd.sock" /bin/pndstub "" "$XDG_RUNTIME_DIR/pnd.sock" &
# llmd / portal / zyrkel / reprofleet markers (the real binaries drop in 1:1 here)
( echo "[pn-llmd] up (uid $(id -u))";        while true; do sleep 30; done ) &
( echo "[brainbox-portal] up (uid $(id -u))"; while true; do sleep 30; done ) &
( echo "[zyrkel] up (uid $(id -u))";         while true; do sleep 30; done ) &
( echo "[reprofleet] up (uid $(id -u))";     while true; do sleep 30; done ) &
echo "[user-session] pn-stack launched (pnd,pn-llmd,brainbox-portal,zyrkel,reprofleet)"
wait
EOF
chmod 0755 "$MNT/usr/local/bin/pn-user-session"

cat > "$MNT/usr/local/bin/pn-selftest" <<'EOF'
#!/bin/sh
sleep 8
echo "==== PN-SELFTEST BEGIN ===="
echo "[selftest] ip addr:"; ip -br addr 2>/dev/null
echo "[selftest] default route:"; ip route 2>/dev/null | grep default || echo "  (none)"
echo "[selftest] resolv.conf:"; cat /etc/resolv.conf 2>/dev/null | grep -v '^#' | grep . | head -3
echo "[selftest] mounts:"; (mount | awk '{print $1,$3,$5}') 2>/dev/null
echo "[selftest] sshd listening:"; (ss -tln 2>/dev/null || busybox netstat -tln 2>/dev/null) | grep ':22 ' && echo "  SSHD_LISTENING_22" || echo "  SSHD_NOT_LISTENING"
echo "[selftest] swap:"; (swapon --show 2>/dev/null || cat /proc/swaps) | tail -2
echo "[selftest] user runtime dir:"; ls -ld /run/user/1000 2>/dev/null || echo "  /run/user/1000 MISSING"
echo "[selftest] pnd user socket:"; ls -l /run/user/1000/pnd.sock 2>/dev/null && echo "  PND_SOCK_PRESENT" || echo "  PND_SOCK_MISSING"
echo "==== PN-SELFTEST END ===="
EOF
chmod 0755 "$MNT/usr/local/bin/pn-selftest"

echo "[6/7] /etc/pn-init.conf — the declarative full-system service tree"
cat > "$MNT/etc/pn-init.conf" <<'EOF'
# pn-init full-system service tree (mirrors the load-bearing subset of the reference dev host's systemd units).
# Format: name|flags|argv...   flags: sacred,pnd,disabled,oneshot,user=<uid|name>,env=K=V
#
# sshd: the acceptance bar — sacred, starts first, supervised.
sshd|sacred|/usr/sbin/sshd -D -e
# the pn --user session for uid 1000 (stands in for `systemd --user`; runs the pn stack).
# pndsock= repoints the watchdog L1/L2 probe at pnd's per-user socket (XDG_RUNTIME_DIR/pnd.sock).
user-session|user=1000 pnd pndsock=/run/user/1000/pnd.sock|/usr/local/bin/pn-user-session
# a post-boot self-test that proves net+ssh+pn-socket to the serial log (oneshot).
selftest|oneshot|/usr/local/bin/pn-selftest
EOF

echo "[7/7] cleanup APT caches to shrink the image"
chroot "$MNT" apt-get clean >/dev/null 2>&1 || true
rm -rf "$MNT/var/lib/apt/lists/"* "$MNT/var/cache/apt/archives/"*.deb 2>/dev/null || true
sync
echo "DONE: $IMG ($(du -h "$IMG" | cut -f1)); root label=pnroot, user pn/uid1000 pw=pntest"
