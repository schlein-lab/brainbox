#!/usr/bin/env bash
set -euo pipefail

OUT_DIR=${OUT_DIR:-/var/tmp/bbx}
OUT_RAW=${OUT_RAW:-$OUT_DIR/brainbox-appliance-amd64.raw}
OUT_QCOW=${OUT_QCOW:-$OUT_DIR/brainbox-appliance-amd64.qcow2}
SIZE_MB=${SIZE_MB:-20000}
SUITE=${SUITE:-noble}
MIRROR=${MIRROR:-http://archive.ubuntu.com/ubuntu}
SERVICE_USER=${SERVICE_USER:-brainbox}
SERVICE_UID=${SERVICE_UID:-1000}
DEFAULT_PW=${DEFAULT_PW:-$(head -c 4096 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 20)}
ALLOW_SSH_PASSWORD=${ALLOW_SSH_PASSWORD:-0}
REPO_SRC=${REPO_SRC:-$HOME/brainarbeit}
if [ -z "${PNINIT_SRC:-}" ]; then
  if make -C "$REPO_SRC/os/init" pn-init >/dev/null 2>&1 && [ -x "$REPO_SRC/os/init/pn-init" ]; then
    PNINIT_SRC="$REPO_SRC/os/init/pn-init"
  else
    echo "WARN: could not (re)build $REPO_SRC/os/init/pn-init — falling back to /sbin/pn-init (may be stale vs. the repo source)"
    PNINIT_SRC=/sbin/pn-init
  fi
fi
ASSETS=${ASSETS:-$OUT_DIR/assets}
BASE_CACHE=${BASE_CACHE:-$OUT_DIR/base-$SUITE.tar}
DO_QCOW=${DO_QCOW:-1}
FULL_BRAIN=${FULL_BRAIN:-1}

[ "$(id -u)" = 0 ] || { echo "must run as root"; exit 1; }
for t in debootstrap parted losetup mkfs.ext4 grub-install blkid rsync; do
  command -v "$t" >/dev/null || { echo "missing tool: $t"; exit 1; }
done
[ -x "$PNINIT_SRC" ] || { echo "pn-init not found/executable at $PNINIT_SRC"; exit 1; }
[ -d "$REPO_SRC/.git" ] || { echo "repo not found at $REPO_SRC"; exit 1; }
mkdir -p "$OUT_DIR" "$ASSETS"

MNT="$(mktemp -d)"; LOOP=""
cleanup(){ set +e; sync
  umount -R "$MNT" 2>/dev/null
  [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null
  rmdir "$MNT" 2>/dev/null; }
trap cleanup EXIT

say(){ echo "==== [build] $* ===="; }

say "1. create ${SIZE_MB}MiB MBR disk at $OUT_RAW"
rm -f "$OUT_RAW"
truncate -s "${SIZE_MB}M" "$OUT_RAW"
parted -s "$OUT_RAW" mklabel msdos
parted -s "$OUT_RAW" mkpart primary ext4 1MiB 100%
parted -s "$OUT_RAW" set 1 boot on
LOOP="$(losetup --show -fP "$OUT_RAW")"
PART="${LOOP}p1"; [ -e "$PART" ] || PART="${LOOP}1"
[ -e "$PART" ] || { echo "partition device not found for $LOOP"; ls -l "${LOOP}"*; exit 1; }
mkfs.ext4 -q -F -L BBXROOT "$PART"
mount "$PART" "$MNT"
ROOT_UUID="$(blkid -s UUID -o value "$PART")"
echo "  root partition=$PART uuid=$ROOT_UUID"

if [ -f "$BASE_CACHE" ]; then
  say "2. restore cached base ($BASE_CACHE)"
  tar -C "$MNT" -xpf "$BASE_CACHE"
else
  say "2. debootstrap $SUITE (minbase) — the slow step (will be cached)"
  debootstrap --arch=amd64 --variant=minbase --components=main,universe \
    --include=openssh-server,udev,iproute2,busybox-static,python3,python3-venv,python3-pip,kmod,libpam-modules,util-linux,dbus,ca-certificates,avahi-daemon,libnss-mdns,cron,less,nano,curl,openssl,locales,tzdata,sudo,netbase \
    "$SUITE" "$MNT" "$MIRROR" >/tmp/bbx-debootstrap.log 2>&1 \
    || { echo "debootstrap FAILED"; tail -40 /tmp/bbx-debootstrap.log; exit 1; }
  say "2b. snapshot base cache"
  tar -C "$MNT" -cpf "$BASE_CACHE" . 2>/dev/null || true
fi

say "3. bind mounts + apt sources for chroot"
mount -t proc  proc   "$MNT/proc"
mount -t sysfs sysfs  "$MNT/sys"
mount --bind /dev     "$MNT/dev"
mount --bind /dev/pts "$MNT/dev/pts"
cp /etc/resolv.conf "$MNT/etc/resolv.conf"
cat > "$MNT/etc/apt/sources.list" <<EOF
deb $MIRROR $SUITE main universe
deb $MIRROR $SUITE-updates main universe
deb $MIRROR $SUITE-security main universe
EOF
export DEBIAN_FRONTEND=noninteractive
chr(){ chroot "$MNT" /usr/bin/env DEBIAN_FRONTEND=noninteractive "$@"; }

say "4. install kernel, initramfs, grub-pc, runtime deps (chroot apt)"
echo 'grub-pc grub-pc/install_devices_empty boolean true' | chr debconf-set-selections
chr apt-get update -y >/tmp/bbx-apt.log 2>&1 || { echo "apt update FAILED"; tail -30 /tmp/bbx-apt.log; exit 1; }
chr apt-get install -y --no-install-recommends \
    linux-image-generic initramfs-tools grub-pc os-prober git qrencode avahi-utils \
    tmux ffmpeg espeak-ng e2fsprogs chrony nftables acl \
    >>/tmp/bbx-apt.log 2>&1 || { echo "apt install (kernel/grub) FAILED"; tail -40 /tmp/bbx-apt.log; exit 1; }
for b in tmux ffmpeg espeak-ng mke2fs debugfs nft; do
  chr sh -c "command -v $b >/dev/null" \
    || { echo "FATAL: required runtime binary '$b' missing after apt install (see /tmp/bbx-apt.log)"; exit 1; }
done

MEDIA_SERVER="${MEDIA_SERVER:-1}"
if [ "$MEDIA_SERVER" = "1" ]; then
  chr apt-get install -y --no-install-recommends samba samba-common-bin smbclient >>/tmp/bbx-apt.log 2>&1 \
    || { echo "apt install (samba) FAILED — set MEDIA_SERVER=0 to build without the file server"; tail -40 /tmp/bbx-apt.log; exit 1; }
  if chr apt-get install -y --no-install-recommends wsdd >>/tmp/bbx-apt.log 2>&1; then
    say "   wsdd installed (modern Windows auto-discovery)"
  else
    say "   WARN: wsdd not installed — modern Windows won't auto-list the box (non-fatal)"
  fi
  for b in smbd nmbd testparm smbpasswd; do
    chr sh -c "command -v $b >/dev/null" \
      || { echo "FATAL: media-server binary '$b' missing after apt (see /tmp/bbx-apt.log); set MEDIA_SERVER=0 to opt out"; exit 1; }
  done
  mkdir -p "$MNT/etc/avahi/services"
  cat > "$MNT/etc/avahi/services/smb.service" <<'AVAHI'
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<!-- Brainbox media server: makes the box appear as a file server in macOS Finder + Linux Nautilus/GVfs -->
<service-group>
  <name replace-wildcards="yes">Brainbox Medienserver auf %h</name>
  <service>
    <type>_smb._tcp</type>
    <port>445</port>
  </service>
  <service>
    <type>_device-info._tcp</type>
    <port>0</port>
    <txt-record>model=RackMount</txt-record>
  </service>
</service-group>
AVAHI
  say "   samba verified + _smb._tcp advertised (LAN media server: smbd/nmbd present)"
else
  say "   MEDIA_SERVER=0 — building WITHOUT the LAN file server (brainbox-smbd will no-op)"
fi

say "5. base config (hostname, user $SERVICE_USER/$SERVICE_UID, nsswitch, fstab, sshd)"
echo "brainbox" > "$MNT/etc/hostname"
printf '127.0.0.1\tlocalhost\n127.0.1.1\tbrainbox\n' > "$MNT/etc/hosts"

BBX_VERSION="$(cat "$REPO_SRC/os/image/VERSION" 2>/dev/null || echo 0.0.0-dev)"
cat > "$MNT/usr/lib/os-release" <<OSR
NAME="Brainbox OS"
VERSION="$BBX_VERSION"
ID=brainbox
ID_LIKE="ubuntu debian"
VERSION_ID="$BBX_VERSION"
PRETTY_NAME="Brainbox OS $BBX_VERSION"
HOME_URL="https://brainarbeit.com/"
DOCUMENTATION_URL="https://github.com/schlein-lab/brainbox"
BUG_REPORT_URL="https://github.com/schlein-lab/brainbox/issues"
OSR
ln -sfn ../usr/lib/os-release "$MNT/etc/os-release"

chr useradd -m -u "$SERVICE_UID" -s /bin/bash "$SERVICE_USER" 2>/dev/null || true
echo "$SERVICE_USER:$DEFAULT_PW" | chr chpasswd
chr passwd -l root >/dev/null 2>&1 || true
chr install -d -m 0755 /etc/brainbox
printf '%s\n' "$DEFAULT_PW" > "$MNT/etc/brainbox/initial-console-password"
chmod 0600 "$MNT/etc/brainbox/initial-console-password"
SETUP_CODE_BAKE="$(printf '%06d' "$(( $(od -An -N4 -tu4 /dev/urandom | tr -d ' ') % 1000000 ))")"
printf '%s\n' "$SETUP_CODE_BAKE" > "$MNT/etc/brainbox/setup-code"
chmod 0600 "$MNT/etc/brainbox/setup-code"
chr usermod -aG sudo "$SERVICE_USER" 2>/dev/null || true
chr groupadd -f pnbroker 2>/dev/null || true
chr usermod -aG pnbroker "$SERVICE_USER" 2>/dev/null || true
chr groupadd -f input 2>/dev/null || true
chr usermod -aG input "$SERVICE_USER" 2>/dev/null || true

cat > "$MNT/etc/nsswitch.conf" <<'EOF'
passwd:         files
group:          files
shadow:         files
gshadow:        files
hosts:          files mdns4_minimal [NOTFOUND=return] dns
networks:       files
protocols:      db files
services:       db files
ethers:         db files
rpc:            db files
netgroup:       nis
EOF

if [ "$ALLOW_SSH_PASSWORD" = "1" ]; then
  sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' "$MNT/etc/ssh/sshd_config"
  echo "  WARN: SSH-Passwortanmeldung AKTIV (ALLOW_SSH_PASSWORD=1)"
else
  sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' "$MNT/etc/ssh/sshd_config"
  sed -i 's/^#\?KbdInteractiveAuthentication.*/KbdInteractiveAuthentication no/' "$MNT/etc/ssh/sshd_config"
fi
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/'  "$MNT/etc/ssh/sshd_config"
chr chage -d -1 "$SERVICE_USER" 2>/dev/null || true
chr chage -M 99999 "$SERVICE_USER" 2>/dev/null || true
rm -f "$MNT"/etc/ssh/ssh_host_*
mkdir -p "$MNT/run/sshd"

if [ -f "$MNT/etc/avahi/avahi-daemon.conf" ]; then
  if grep -qE '^[[:space:]]*#?[[:space:]]*host-name=' "$MNT/etc/avahi/avahi-daemon.conf"; then
    sed -i 's/^[[:space:]]*#\?[[:space:]]*host-name=.*/host-name=brainbox/' "$MNT/etc/avahi/avahi-daemon.conf"
  else
    sed -i '/^\[server\]/a host-name=brainbox' "$MNT/etc/avahi/avahi-daemon.conf"
  fi
fi

printf '\n  ============  B R A I N B O X  ============\n\n  Einrichten im Browser / set up in your browser:\n\n      http://brainbox.local/\n\n  ==========================================\n\n  \\n \\l\n\n' > "$MNT/etc/issue"

rm -f "$MNT/etc/machine-id" "$MNT/var/lib/dbus/machine-id"; : > "$MNT/etc/machine-id"
rm -f "$MNT/etc/resolv.conf"; cp /etc/resolv.conf "$MNT/etc/resolv.conf"

cat > "$MNT/etc/fstab" <<EOF
UUID=$ROOT_UUID   /        ext4   defaults        0 1
/swap.img         none     swap   sw              0 0
EOF
dd if=/dev/zero of="$MNT/swap.img" bs=1M count=512 status=none
chmod 600 "$MNT/swap.img"; mkswap "$MNT/swap.img" >/dev/null 2>&1 || true

mkdir -p "$MNT/etc/netplan"
cat > "$MNT/etc/netplan/50-brainbox.yaml" <<'EOF'
network:
  version: 2
  renderer: networkd
  ethernets:
    all-eth:
      match: {name: "e*"}
      dhcp4: true
EOF
chmod 600 "$MNT/etc/netplan/50-brainbox.yaml"

install -d -m 0755 "$MNT/usr/share/udhcpc"
cat > "$MNT/usr/share/udhcpc/default.script" <<'EOF'
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

say "6. install pn-init, boot-chain helpers, and the brainbox (clean repo archive)"
install -m 0755 "$PNINIT_SRC" "$MNT/sbin/pn-init"
install -d -m 0755 "$MNT/usr/local/bin" "$MNT/usr/local/sbin"
for b in pn-netpin pn-portioneer-run pn-cgtree pn-cgmove pn-kvm-load; do
  if   [ -f "$REPO_SRC/os/init/local-bin/$b" ]; then install -m 0755 "$REPO_SRC/os/init/local-bin/$b" "$MNT/usr/local/bin/$b"
  elif [ -f "$REPO_SRC/os/init/$b" ];           then install -m 0755 "$REPO_SRC/os/init/$b"           "$MNT/usr/local/bin/$b"
  elif [ -f "/usr/local/bin/$b" ];              then install -m 0755 "/usr/local/bin/$b"              "$MNT/usr/local/bin/$b"
  fi
done
[ -f "$REPO_SRC/os/init/pnctl" ] && install -m 0755 "$REPO_SRC/os/init/pnctl" "$MNT/usr/local/bin/pnctl" || true

install -m 0755 "$REPO_SRC/cockpit/server/pn-shutdown" "$MNT/usr/local/sbin/pn-shutdown"
install -m 0755 "$REPO_SRC/os/image/brainbox-firewall" "$MNT/usr/local/sbin/brainbox-firewall"
install -d -m 0750 "$MNT/etc/sudoers.d"
cat > "$MNT/etc/sudoers.d/brainbox-shutdown" <<EOF
$SERVICE_USER ALL=(root) NOPASSWD: /usr/local/sbin/pn-shutdown
EOF
chmod 0440 "$MNT/etc/sudoers.d/brainbox-shutdown"

cat > "$MNT/etc/sudoers.d/brainbox-cgmove" <<EOF
$SERVICE_USER ALL=(root) NOPASSWD: /usr/local/bin/pn-cgmove
EOF
chmod 0440 "$MNT/etc/sudoers.d/brainbox-cgmove"

install -m 0755 "$REPO_SRC/cockpit/server/pn-mediashare-provision" "$MNT/usr/local/sbin/pn-mediashare-provision"
install -m 0755 "$REPO_SRC/os/image/brainbox-smbd" "$MNT/usr/local/sbin/brainbox-smbd"
install -m 0755 "$REPO_SRC/os/image/brainbox-dlnad" "$MNT/usr/local/sbin/brainbox-dlnad"
cat > "$MNT/etc/sudoers.d/brainbox-mediashare" <<EOF
$SERVICE_USER ALL=(root) NOPASSWD: /usr/local/sbin/pn-mediashare-provision
EOF
chmod 0440 "$MNT/etc/sudoers.d/brainbox-mediashare"
install -d -o "$SERVICE_UID" -g "$SERVICE_UID" -m 2775 "$MNT/data/shares"

install -m 0755 "$REPO_SRC/os/breakglass/pn_breakglassd.py" "$MNT/usr/local/sbin/brainbox-breakglassd"
install -m 0755 "$REPO_SRC/os/breakglass/breakglass.sh"     "$MNT/usr/local/sbin/brainbox-breakglass"
install -d -m 0755 "$MNT/usr/local/share/brainbox-breakglass/static"
for a in xterm.js xterm.css addon-fit.js; do
  install -m 0644 "$REPO_SRC/cockpit/server/webapp/static/$a" "$MNT/usr/local/share/brainbox-breakglass/static/$a"
done
[ -x "$MNT/usr/local/sbin/brainbox-breakglassd" ] && [ -s "$MNT/usr/local/share/brainbox-breakglass/static/xterm.js" ] \
  || { echo "FATAL: break-glass daemon or vendored xterm.js missing"; exit 1; }

SVC_HOME="/home/$SERVICE_USER"
install -d -m 0755 "$MNT$SVC_HOME/brainarbeit" "$MNT$SVC_HOME/.local/bin"
git -c safe.directory="$REPO_SRC" -C "$REPO_SRC" archive HEAD | tar -x -C "$MNT$SVC_HOME/brainarbeit"

_LM_FILE="$REPO_SRC/os/image/leak-markers.txt"
_LM=$(head -1 "$_LM_FILE" 2>/dev/null)
[ -n "$_LM" ] || _LM='-----BEGIN (RSA|OPENSSH|EC|PGP|DSA) PRIVATE KEY-----|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-ant-[A-Za-z0-9-]{30,}|AKIA[0-9A-Z]{16}'
_LEAK=$(grep -rIlE --exclude=build-appliance-disk.sh "$_LM" "$MNT$SVC_HOME/brainarbeit" 2>/dev/null || true)
if [ -n "$_LEAK" ]; then
  echo "FATAL: owner-private marker(s) in the shipped tree — refusing to build:"; echo "$_LEAK" | sed 's/^/  /'
  echo "Scrub the file(s) or add an export-ignore in .gitattributes, then rebuild."
  exit 7
fi
echo "  privacy leak-gate: shipped tree clean (no owner-private markers)"
ln -sf "$SVC_HOME/brainarbeit/cockpit/server/brainbox-portal" "$MNT$SVC_HOME/.local/bin/brainbox-portal"
ln -sf "$SVC_HOME/brainarbeit/cockpit/server/llmpool.py" "$MNT$SVC_HOME/.local/bin/llmpool.py"
ln -sf "$SVC_HOME/brainarbeit/cockpit/server/cell_exchange_sync.py" "$MNT$SVC_HOME/.local/bin/cell-exchange-sync"
ln -sfn "$SVC_HOME/brainarbeit/engine" "$MNT$SVC_HOME/portioneer"

say "6a. runtime helper links (~/.local/bin -> shipped repo tree)"
HELPERS='
  pn|engine/tools/pn
  pn-cron-submit|engine/tools/pn-cron-submit
  pn-selftest|cockpit/server/pn-selftest
  pn-shutdown|cockpit/server/pn-shutdown
  pnctl|os/init/pnctl
  cellfs|cockpit/server/cellfs
  portalctl|cockpit/server/portalctl
  vpn-tunnel|cockpit/server/vpn-tunnel
  sonos|cockpit/server/sonos
  snap-run|components/phantom/tools/snap-run
  seatcast_service.py|cockpit/server/seatcast_service.py
  pn_cast_supervisor.py|cockpit/server/pn_cast_supervisor.py
  pn_vpn_netns.py|cockpit/server/pn_vpn_netns.py
  pn_biomni_bridge.py|cockpit/server/pn_biomni_bridge.py
  pn-claude-sessionmap|cockpit/server/pn-claude-sessionmap
  pnjob|os/pn-vmm/pn_cell_pnjob.py
  pn-batch-run|engine/tools/pn-batch-run
  phantom|components/phantom/phantGNOME/bin/phantom
  phantom-room|components/phantom/phantGNOME/bin/phantom-room
  fusion|components/phantom/phantGNOME/bin/fusion
'
HELPER_MISSING=""
for spec in $HELPERS; do
  hn="${spec%%|*}"; hrel="${spec#*|}"
  if [ -s "$MNT$SVC_HOME/brainarbeit/$hrel" ]; then
    chmod 0755 "$MNT$SVC_HOME/brainarbeit/$hrel"
    ln -sf "$SVC_HOME/brainarbeit/$hrel" "$MNT$SVC_HOME/.local/bin/$hn"
  else
    HELPER_MISSING="$HELPER_MISSING $hn($hrel)"
  fi
done
[ -z "$HELPER_MISSING" ] || {
  echo "FATAL: runtime helper source(s) absent from the repo archive:$HELPER_MISSING"
  echo "       The portal resolves these under \$HOME/.local/bin at runtime; without them the"
  echo "       governed job runner, /selftest, cells, VPN, cast and Sonos fail on the installed box."
  echo "       These are TRACKED files — a miss means the path moved or HEAD is broken. Fix the"
  echo "       path in the HELPERS list above; do NOT ship an image with a dead helper."
  exit 1; }
echo "  linked $(echo $HELPERS | wc -w) helpers into $SVC_HOME/.local/bin"

say "6a2. device_discover.py (governed mDNS/SSDP scan helper)"
DD_REL="engine/tools/device_discover.py"
DD_SRC=""
for c in "$REPO_SRC/$DD_REL" \
         "$(eval echo ~"${SUDO_USER:-$(id -un)}")/.local/bin/device_discover.py"; do
  [ -s "$c" ] && { DD_SRC="$c"; break; }
done
[ -n "$DD_SRC" ] || {
  echo "FATAL: device_discover.py not found — looked at $REPO_SRC/$DD_REL and the build user's"
  echo "       ~/.local/bin/device_discover.py. Shipping without it makes /api/devices/scan report"
  echo "       a successful scan that can never find a Chromecast, a Sonos or a TV."
  exit 1; }
install -d -m 0755 "$MNT$SVC_HOME/brainarbeit/$(dirname "$DD_REL")"
install -m 0755 "$DD_SRC" "$MNT$SVC_HOME/brainarbeit/$DD_REL"
ln -sf "$SVC_HOME/brainarbeit/$DD_REL" "$MNT$SVC_HOME/.local/bin/device_discover.py"
case "$DD_SRC" in
  "$REPO_SRC/"*) echo "  device_discover.py from the repo ($DD_REL)" ;;
  *) echo "  WARN: device_discover.py staged from $DD_SRC — it is NOT tracked in git."
     echo "        Commit it as $DD_REL so the archive carries it and this fallback can go." ;;
esac

if [ "$FULL_BRAIN" = 1 ]; then
  say "6c. microVM cell stack (pn-vmm + guest kernel + session base image)"
  VMM_DST="$MNT$SVC_HOME/brainarbeit/os/pn-vmm"
  install -d -m 0755 "$VMM_DST/target/release" "$VMM_DST/kernel"
  MISSING=""
  for a in target/release/pn-vmm kernel/vmlinux.bin kernel/vmlinux-rng.bin kernel/initramfs-cell.cpio kernel/base-owner-session.img kernel/base-office.img; do
    if [ -s "$REPO_SRC/os/pn-vmm/$a" ]; then cp "$REPO_SRC/os/pn-vmm/$a" "$VMM_DST/$a"
    else MISSING="$MISSING $a"; fi
  done
  [ -z "$MISSING" ] || {
    echo "FATAL: cell runtime artefact(s) missing under $REPO_SRC/os/pn-vmm:$MISSING"
    echo "       These are gitignored build outputs — 'git archive HEAD' cannot supply them."
    echo "       Build them on this host first, then re-run. Refusing to ship a cell-less image."
    exit 1; }
  chmod 0755 "$VMM_DST/target/release/pn-vmm"
  [ -x "$VMM_DST/target/release/pn-vmm" ] || { echo "FATAL: staged pn-vmm is not executable"; exit 1; }

  say "6c3. session runtime images (agents: gemini+opencode; codex wenn vorhanden)"
  RT_SRC="${RT_SRC:-$(dirname "$REPO_SRC")/.local/share/brainarbeit/runtimes}"
  RT_DST="$MNT$SVC_HOME/.local/share/brainarbeit/runtimes"
  if [ -s "$RT_SRC/agents/current/runtime.img" ]; then
    mkdir -p "$RT_DST/agents"
    AV=$(readlink "$RT_SRC/agents/current")
    cp -a "$RT_SRC/agents/$AV" "$RT_DST/agents/$AV"
    ln -sfn "$AV" "$RT_DST/agents/current"
    say "   agents runtime staged ($AV)"
  else
    echo "FATAL: agents runtime fehlt ($RT_SRC/agents/current) — build_cell_runtime_agents.py zuerst"
    exit 1
  fi
  if [ -s "$RT_SRC/codex/current/runtime.img" ]; then
    mkdir -p "$RT_DST/codex"
    CV=$(readlink "$RT_SRC/codex/current")
    cp -a "$RT_SRC/codex/$CV" "$RT_DST/codex/$CV"
    ln -sfn "$CV" "$RT_DST/codex/current"
    say "   codex runtime staged ($CV)"
  else
    say "   WARN: codex runtime nicht vorhanden — codex-Sessions verweigern ehrlich (non-fatal)"
  fi
  chr usermod -aG kvm "$SERVICE_USER" 2>/dev/null || true

  say "6c2. phantom seat binaries (host-built, copied — same precedent as pn-vmm/pn-init/claude)"
  PH_SRC="${PHANTOM_SRC_DIR:-$REPO_SRC/components/phantom/target/release}"
  PH_DST="$MNT$SVC_HOME/brainarbeit/components/phantom/target/release"
  install -d -m 0755 "$PH_DST"
  PH_MISSING=""
  for b in phantom phantom-supervise; do
    if [ -x "$PH_SRC/$b" ]; then install -m 0755 "$PH_SRC/$b" "$PH_DST/$b"
    else PH_MISSING="$PH_MISSING $b"; fi
  done
  [ -z "$PH_MISSING" ] || {
    echo "FATAL: phantom binaries missing under $PH_SRC:$PH_MISSING"
    echo "       Build them first:  cd $REPO_SRC/components/phantom && cargo build --release"
    echo "       Refusing to ship an image whose Screen/seat feature cannot start."
    exit 1; }
  echo "  phantom + phantom-supervise staged from $PH_SRC"

  say "6d. claude CLI (native binary, copied from build host — no network/installer flakiness)"
  CL_SRC="${CLAUDE_SRC_DIR:-$(eval echo ~"${SUDO_USER:-$(id -un)}")/.local/share/claude}"
  CL_BIN="$(ls -1 "$CL_SRC"/versions/* 2>/dev/null | sort -V | tail -1 || true)"
  if [ -n "$CL_BIN" ] && [ -x "$CL_BIN" ]; then
    CL_VER="$(basename "$CL_BIN")"
    install -d -m 0755 "$MNT$SVC_HOME/.local/share/claude/versions" "$MNT$SVC_HOME/.local/bin"
    cp "$CL_BIN" "$MNT$SVC_HOME/.local/share/claude/versions/$CL_VER"
    chmod 0755 "$MNT$SVC_HOME/.local/share/claude/versions/$CL_VER"
    ln -sf "$SVC_HOME/.local/share/claude/versions/$CL_VER" "$MNT$SVC_HOME/.local/bin/claude"
    ln -sf "$SVC_HOME/.local/bin/claude" "$MNT/usr/local/bin/claude"
    echo "  claude $CL_VER copied from $CL_BIN"
  else
    echo "FATAL: no claude binary under $CL_SRC/versions — pn-llmd (the brain) would be dead on"
    echo "       arrival. Install it on this build host (curl -fsSL https://claude.ai/install.sh | bash),"
    echo "       or point CLAUDE_SRC_DIR at a tree that has one. Set ALLOW_NO_CLAUDE=1 to ship a"
    echo "       deliberately brain-less image."
    [ "${ALLOW_NO_CLAUDE:-0}" = 1 ] || exit 1
    echo "  (ALLOW_NO_CLAUDE=1 — continuing without a brain)"
  fi
fi
chr chown -R "$SERVICE_USER:$SERVICE_USER" "$SVC_HOME" 2>/dev/null || true

say "6b. portal python deps (chroot pip, --break-system-packages)"
chr pip3 install --break-system-packages --no-input --quiet \
    segno pillow numpy cryptography pyyaml requests pyjwt websockets \
    >>/tmp/bbx-pip.log 2>&1 || echo "  WARN: some pip deps failed (see /tmp/bbx-pip.log) — non-fatal for skeleton boot"

say "6b-cast. celltv-venv (pychromecast + pyte + pillow) for Spiegeln/seatcast"
chr python3 -m venv "$SVC_HOME/.local/share/celltv-venv" >>/tmp/bbx-pip.log 2>&1   && chr "$SVC_HOME/.local/share/celltv-venv/bin/pip" install --no-input --quiet        pychromecast pyte pillow >>/tmp/bbx-pip.log 2>&1   || echo "  WARN: celltv-venv build failed (see /tmp/bbx-pip.log) — Spiegeln will answer with an honest German error instead of casting"
chr chown -R "$SERVICE_USER:$SERVICE_USER" "$SVC_HOME/.local/share/celltv-venv" 2>/dev/null || true

say "6b-voice. wyoming STT (faster-whisper) venv — model selected by wizard, fetched on first use"
chr python3 -m venv "$SVC_HOME/wyoming-venv" >>/tmp/bbx-pip.log 2>&1 \
  && chr "$SVC_HOME/wyoming-venv/bin/pip" install --no-input --quiet --upgrade pip >>/tmp/bbx-pip.log 2>&1 \
  && chr "$SVC_HOME/wyoming-venv/bin/pip" install --no-input --quiet wyoming-faster-whisper >>/tmp/bbx-pip.log 2>&1 \
  || echo "  WARN: wyoming-venv build failed (see /tmp/bbx-pip.log) — voice STT unavailable until re-run"
chr install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$SVC_HOME/wyoming-data"
chr chown -R "$SERVICE_USER:$SERVICE_USER" "$SVC_HOME/wyoming-venv" 2>/dev/null || true
cat > "$MNT/usr/local/bin/wyoming-stt-run" <<'WRAP'
#!/bin/sh
set -u
[ -r /etc/brainbox/voice.env ] && . /etc/brainbox/voice.env
[ "${VOICE_ENABLED:-1}" = "1" ] || exec sleep 2147483647
MODEL="${WYOMING_MODEL:-medium}"
HD="$(getent passwd "$(id -un)" | cut -d: -f6)"
exec "$HD/wyoming-venv/bin/python" -m wyoming_faster_whisper --uri tcp://0.0.0.0:10300 --model "$MODEL" --data-dir "$HD/wyoming-data" --download-dir "$HD/wyoming-data"
WRAP
chmod 0755 "$MNT/usr/local/bin/wyoming-stt-run"

say "7. /etc/brainbox (service.env, site.conf, caps.env, is-appliance) + firstboot + wizard"
install -d -m 0755 "$MNT/etc/brainbox" "$MNT/var/lib/brainbox"
touch "$MNT/etc/brainbox/is-appliance"
printf 'VOICE_ENABLED=1
WYOMING_MODEL=medium
' > "$MNT/etc/brainbox/voice.env"
printf '%s\n' "$BBX_VERSION" > "$MNT/etc/brainbox/version"
cat > "$MNT/etc/brainbox/service.env" <<EOF
SERVICE_USER=$SERVICE_USER
SERVICE_HOME=$SVC_HOME
SERVICE_UID=$SERVICE_UID
EOF
cat > "$MNT/etc/brainbox/site.conf" <<EOF
SERVICE_USER=$SERVICE_USER
SERVICE_HOME=$SVC_HOME
SERVICE_UID=$SERVICE_UID
HA_ENABLED=0
CELLS_ENABLED=0
NET_PROFILE=managed
TEAMS_ENABLED=0
RELAY_ENABLED=0
PRINTER_ENABLED=0
NAS_ENABLED=0
VOICE_ENABLED=0
LANG_UI=de
EOF
echo "CELLS_ENABLED=0" > "$MNT/etc/brainbox/caps.env"

cat > "$MNT/etc/brainbox/pnd.env" <<'EOF'
EOF

[ -f "$REPO_SRC/os/image/brainbox-caps-detect" ] || {
  echo "FATAL: $REPO_SRC/os/image/brainbox-caps-detect missing — caps-detect is a boot-chain oneshot."
  exit 1; }
install -m 0755 "$REPO_SRC/os/image/brainbox-caps-detect" "$MNT/usr/local/sbin/brainbox-caps-detect"

for b in brainbox-earlyboot brainbox-hotplug brainbox-netcfg brainbox-banner; do
  if [ -f "$REPO_SRC/os/image/$b" ]; then
    install -m 0755 "$REPO_SRC/os/image/$b" "$MNT/usr/local/sbin/$b"
  else
    echo "  WARN: $b missing in $REPO_SRC/os/image"
  fi
done
for b in brainbox-earlyboot brainbox-hotplug brainbox-netcfg brainbox-banner; do
  [ -x "$MNT/usr/local/sbin/$b" ] || {
    echo "FATAL: boot-chain script missing under $REPO_SRC/os/image ($b)"
    echo "       — refusing to bake a pn-init boot chain that references a non-existent service."
    exit 1
  }
done

for f in firstboot.sh factory-clean.sh; do
  [ -f "$REPO_SRC/os/image/$f" ] && install -m 0755 "$REPO_SRC/os/image/$f" "$MNT/usr/local/sbin/brainbox-${f%.sh}.sh" || true
done
WIZ_REPO="$REPO_SRC/os/image/brainbox-setup"
WIZ_SRC=""
if [ -f "$ASSETS/brainbox-setup" ] && [ -f "$WIZ_REPO" ]; then
  if [ "$ASSETS/brainbox-setup" -nt "$WIZ_REPO" ]; then
    WIZ_SRC="$ASSETS/brainbox-setup"; echo "  wizard: staged override (newer than repo)"
  else
    WIZ_SRC="$WIZ_REPO"
    echo "  wizard: repo (IGNORING stale staged copy in $ASSETS — it is older than the repo file)"
  fi
elif [ -f "$WIZ_REPO" ]; then
  WIZ_SRC="$WIZ_REPO"; echo "  wizard: repo"
elif [ -f "$ASSETS/brainbox-setup" ]; then
  WIZ_SRC="$ASSETS/brainbox-setup"; echo "  wizard: staged (no repo copy found)"
fi
if [ -n "$WIZ_SRC" ]; then
  install -m 0755 "$WIZ_SRC" "$MNT/usr/local/sbin/brainbox-setup"
  python3 -m py_compile "$MNT/usr/local/sbin/brainbox-setup" 2>/dev/null || {
    echo "FATAL: staged setup wizard does not compile: $WIZ_SRC"; exit 1; }
elif [ "${ALLOW_PLACEHOLDER_WIZARD:-0}" != 1 ]; then
  echo "FATAL: no setup wizard found (looked in $ASSETS and $WIZ_REPO)."
  echo "       A customer image MUST NOT ship the placeholder stub."
  echo "       Set ALLOW_PLACEHOLDER_WIZARD=1 only for a boot-path smoke test."
  exit 1
else
  cat > "$MNT/usr/local/sbin/brainbox-setup" <<'PYEOF'
import http.server, socketserver, os
PORT = 80 if os.geteuid()==0 else 8099
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.end_headers()
        self.wfile.write(b"<h1>Brainbox</h1><p>Setup-Assistent folgt. Boot + Netzwerk + Web OK.</p>")
    def log_message(self,*a): pass
print("[brainbox-setup placeholder] serving on :%d" % PORT, flush=True)
socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("0.0.0.0",PORT), H) as s: s.serve_forever()
PYEOF
  chmod 0755 "$MNT/usr/local/sbin/brainbox-setup"
fi

say "8. /etc/pn-init.conf (boot chain, user=$SERVICE_UID home=$SVC_HOME)"
cat > "$MNT/etc/pn-init.conf" <<EOF
earlyboot|oneshot|/usr/local/sbin/brainbox-earlyboot
banner|sacred|/usr/local/sbin/brainbox-banner
firstboot|oneshot|/usr/local/sbin/brainbox-firstboot.sh
caps-detect|oneshot|/usr/local/sbin/brainbox-caps-detect
netcfg|oneshot|/usr/local/sbin/brainbox-netcfg
firewall|oneshot|/usr/local/sbin/brainbox-firewall
dbus|sacred|/usr/bin/dbus-daemon --system --nofork --nopidfile
avahi|sacred|/usr/sbin/avahi-daemon --no-chroot
sshd|sacred|/usr/sbin/sshd -D -e
chronyd|sacred|/usr/sbin/chronyd -d
breakglass|sacred user=$SERVICE_UID|/usr/local/sbin/brainbox-breakglassd --port 8090 --session breakglass --cmd /usr/local/sbin/brainbox-breakglass
getty-vga|sacred|/sbin/agetty --noclear tty1 linux
getty-ser|sacred|/sbin/agetty -L 115200 ttyS0 vt100
netpin|oneshot|/usr/local/bin/pn-netpin
portioneer-run|oneshot|/usr/local/bin/pn-portioneer-run
pn-cgtree|oneshot|/usr/local/bin/pn-cgtree
pnd|sacred pnd user=$SERVICE_UID envfile=/etc/brainbox/pnd.env pndsock=/run/user/$SERVICE_UID/pnd.sock env=PND_BROKER_SOCK=/run/portioneer/pnd-broker.sock env=PND_BROKER_GROUP=pnbroker|$SVC_HOME/portioneer/tools/pnd
pn-cgmove|oneshot|/usr/local/bin/pn-cgmove
pn-llmd|sacred user=$SERVICE_UID envfile=/etc/brainbox/secrets.env env=PN_LLM_POOL=1 env=PN_LLM_MODEL=sonnet env=PN_LLM_CMD=claude -p --model {model}|$SVC_HOME/portioneer/tools/pn-llmd
pn-acctd|user=$SERVICE_UID|$SVC_HOME/portioneer/tools/pn-acctd --socket /run/user/$SERVICE_UID/pn-acctd.sock --interval 5 --workers 4
brainbox-setup|sacred|/usr/local/sbin/brainbox-setup serve
brainbox-portal|sacred user=$SERVICE_UID envfile=/etc/brainbox/secrets.env|/usr/bin/python3 $SVC_HOME/.local/bin/brainbox-portal serve
mediashare-smbd|oneshot|/usr/local/sbin/brainbox-smbd
mediashare-dlna|oneshot|/usr/local/sbin/brainbox-dlnad
wyoming-stt|batch user=$SERVICE_UID|/usr/local/bin/wyoming-stt-run
cron||/usr/sbin/cron -f
EOF
grep -vE '^\s*#|^\s*$' "$MNT/etc/pn-init.conf" | sed 's/^/    /'

say "9. install GRUB (BIOS) to the disk MBR + write grub.cfg"
KVER="$(ls "$MNT/boot" | sed -n 's/^vmlinuz-//p' | sort -V | tail -1)"
[ -n "$KVER" ] || { echo "no kernel in image /boot"; ls -l "$MNT/boot"; exit 1; }
echo "  kernel: $KVER"
grub-install --target=i386-pc --boot-directory="$MNT/boot" \
  --modules="part_msdos ext2 biosdisk" "$LOOP" >/tmp/bbx-grub.log 2>&1 \
  || { echo "grub-install FAILED"; tail -30 /tmp/bbx-grub.log; exit 1; }
CMD_COMMON="root=UUID=$ROOT_UUID ro console=tty0 console=ttyS0,115200 fsck.repair=yes loglevel=4"
cat > "$MNT/boot/grub/grub.cfg" <<EOF
set default=0
set timeout_style=hidden
set timeout=0
serial --unit=0 --speed=115200
terminal_input console serial
terminal_output console serial

menuentry "Brainbox (pn-init)" {
    echo ''
    echo '  Brainbox startet ...  /  starting ...'
    echo '  Der erste Start dauert ein bis drei Minuten.'
    echo '  First boot takes one to three minutes.'
    echo ''
    linux  /boot/vmlinuz-$KVER $CMD_COMMON init=/sbin/pn-init pn.fullsystem panic=10
    initrd /boot/initrd.img-$KVER
}
menuentry "Brainbox — recovery (pn-init minimal)" {
    linux  /boot/vmlinuz-$KVER $CMD_COMMON init=/sbin/pn-init pn.fullsystem pn.recovery
    initrd /boot/initrd.img-$KVER
}
menuentry "Brainbox — rescue (systemd, single)" {
    linux  /boot/vmlinuz-$KVER $CMD_COMMON single
    initrd /boot/initrd.img-$KVER
}
EOF
sed 's/^/    /' "$MNT/boot/grub/grub.cfg" | sed -n '1,6p'

say "10. cleanup + regen initramfs + unmount"
chr update-initramfs -u -k "$KVER" >>/tmp/bbx-apt.log 2>&1 || true
chr apt-get clean >/dev/null 2>&1 || true
rm -rf "$MNT/var/lib/apt/lists/"* "$MNT/var/cache/apt/archives/"*.deb 2>/dev/null || true
rm -f "$MNT/etc/resolv.conf"; : > "$MNT/etc/resolv.conf"
sync
umount -R "$MNT" 2>/dev/null || true
losetup -d "$LOOP" 2>/dev/null || true; LOOP=""
trap - EXIT; rmdir "$MNT" 2>/dev/null || true

echo "RAW image: $OUT_RAW ($(du -h "$OUT_RAW" | cut -f1))"
if [ "$DO_QCOW" = 1 ] && command -v qemu-img >/dev/null; then
  say "11. convert to qcow2 (compressed)"
  qemu-img convert -O qcow2 -c "$OUT_RAW" "$OUT_QCOW"
  echo "QCOW2 image: $OUT_QCOW ($(du -h "$OUT_QCOW" | cut -f1))"
fi
say "BUILD DONE"
