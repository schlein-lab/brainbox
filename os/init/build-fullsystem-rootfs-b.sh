#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMG="${IMG:-/var/tmp/pn-rootfs-b.img}"
SIZE="${SIZE:-2200}"
SUITE="${SUITE:-noble}"
MIRROR="${MIRROR:-http://archive.ubuntu.com/ubuntu}"
PNINIT="${PNINIT:-$HERE/pn-init}"
PNDSTUB="${PNDSTUB:-$HERE/pndstub}"
PNCONF="${PNCONF:-$HERE/pn-init.conf.vmdev-b.example}"
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-brainbox}}"
SVC_HOME="/home/$SERVICE_USER"
[ "$(id -u)" = 0 ] || { echo "must run as root"; exit 1; }
[ -x "$PNINIT" ]  || { echo "build pn-init first (make musl): $PNINIT"; exit 1; }
[ -x "$PNDSTUB" ] || { echo "build pndstub first: $PNDSTUB"; exit 1; }
[ -r "$PNCONF" ]  || { echo "option-b conf not found: $PNCONF"; exit 1; }

MNT="$(mktemp -d)"
cleanup(){ set +e; umount -R "$MNT" 2>/dev/null; losetup -d "$LOOP" 2>/dev/null; rmdir "$MNT" 2>/dev/null; }
trap cleanup EXIT

echo "[1/8] create ${SIZE}MiB ext4 image at $IMG"
rm -f "$IMG"; truncate -s "${SIZE}M" "$IMG"; mkfs.ext4 -q -F -L pnroot "$IMG"
LOOP="$(losetup --show -f "$IMG")"; mount "$LOOP" "$MNT"

echo "[2/8] debootstrap $SUITE (minbase) — the slow step"
debootstrap --variant=minbase \
  --include=openssh-server,udev,iproute2,busybox-static,python3,kmod,libpam-modules,util-linux,dbus \
  "$SUITE" "$MNT" "$MIRROR" >/tmp/debootstrap-b.log 2>&1 || { echo "debootstrap FAILED"; tail -30 /tmp/debootstrap-b.log; exit 1; }

echo "[3/8] base config (hostname, service user uid1000, group pnbroker, sshd password login)"
echo "pn-guest-b" > "$MNT/etc/hostname"
printf '127.0.0.1 localhost\n127.0.1.1 pn-guest-b\n' > "$MNT/etc/hosts"
chroot "$MNT" useradd -m -u 1000 -s /bin/bash "$SERVICE_USER" 2>/dev/null || true
echo "$SERVICE_USER:pntest" | chroot "$MNT" chpasswd
echo 'root:pntest'      | chroot "$MNT" chpasswd
chroot "$MNT" groupadd -f pnbroker 2>/dev/null || true
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' "$MNT/etc/ssh/sshd_config"
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/'              "$MNT/etc/ssh/sshd_config"
chroot "$MNT" ssh-keygen -A >/dev/null 2>&1 || true
chroot "$MNT" sh -c 'dbus-uuidgen > /etc/machine-id 2>/dev/null || cat /proc/sys/kernel/random/uuid | tr -d - > /etc/machine-id; mkdir -p /var/lib/dbus; ln -sf /etc/machine-id /var/lib/dbus/machine-id' || true
mkdir -p "$MNT/run/sshd"
rm -f "$MNT/etc/resolv.conf"; : > "$MNT/etc/resolv.conf"

echo "[3b/8] nsswitch.conf: mirror the BUILD HOST (= the reference build host) NSS policy into the rootfs"
NSSWITCH="${NSSWITCH:-/etc/nsswitch.conf}"
if [ -r "$NSSWITCH" ]; then
  cp "$NSSWITCH" "$MNT/etc/nsswitch.conf"
  echo "    installed nsswitch from $NSSWITCH:"
  grep -E '^(passwd|group|shadow|gshadow|hosts):' "$MNT/etc/nsswitch.conf" | sed 's/^/      /'
  if grep -Eq '^(passwd|group|shadow|gshadow):.*(systemd|mymachines)' "$MNT/etc/nsswitch.conf" \
     || grep -Eq '^hosts:.*mymachines' "$MNT/etc/nsswitch.conf"; then
    echo "  !! FATAL: nsswitch routes user/host lookups through systemd/mymachines NSS modules."
    echo "  !! Under no-systemd pn-init this SIGSEGVs sshd/dbus/login at boot (the cutover bug)."
    echo "  !! Fix $NSSWITCH to files-only user DBs before building. Aborting."
    exit 3
  fi
else
  echo "    WARN: $NSSWITCH not readable — keeping debootstrap default nsswitch (NOT box-faithful)"
fi

echo "[4/8] fstab + netplan (mirror the reference dev shape)"
cat > "$MNT/etc/fstab" <<EOF
LABEL=pnroot   /        ext4   defaults        0 1
/swap.img      none     swap   sw              0 0
EOF
dd if=/dev/zero of="$MNT/swap.img" bs=1M count=128 status=none
chmod 600 "$MNT/swap.img"; mkswap "$MNT/swap.img" >/dev/null 2>&1 || true
mkdir -p "$MNT/etc/netplan"
cat > "$MNT/etc/netplan/01-network-manager-all.yaml" <<EOF
network:
  version: 2
  renderer: NetworkManager
EOF

echo "[5/8] install pn-init + udhcpc lease script"
install -m 0755 "$PNINIT"  "$MNT/sbin/pn-init"
install -m 0755 "$PNDSTUB" "$MNT/bin/pndstub"
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

echo "[6/8] OPTION (b) pn stack STUBS at the REAL ExecStart paths + bring-up oneshot helpers"
install -d -m 0755 "$MNT$SVC_HOME/portioneer/tools"
install -d -m 0755 "$MNT$SVC_HOME/.local/bin"
install -d -m 0755 "$MNT$SVC_HOME/zyrkel/target/release"
install -d -m 0755 "$MNT$SVC_HOME/zyrkel"

cat > "$MNT$SVC_HOME/portioneer/tools/pnd" <<'EOF'
#!/bin/sh
# pnd (stub) — speaks the pn-init watchdog protocol on $XDG_RUNTIME_DIR/pnd.sock (ping->pong,
# canary->ok). The real %h/portioneer/tools/pnd drops in 1:1 here. Broker env is carried but the
# stub does not open the secondary broker socket (not the liveness target).
echo "[pnd] up uid=$(id -u) XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR sock=$XDG_RUNTIME_DIR/pnd.sock BATCH_HIGH=$PN_BATCH_HIGH MAX_CONCURRENT=$PN_MAX_CONCURRENT BROKER_SOCK=$PND_BROKER_SOCK BROKER_GROUP=$PND_BROKER_GROUP"
exec /bin/pndstub "" "$XDG_RUNTIME_DIR/pnd.sock"
EOF
chmod 0755 "$MNT$SVC_HOME/portioneer/tools/pnd"

cat > "$MNT$SVC_HOME/portioneer/tools/pn-llmd" <<'EOF'
#!/bin/sh
echo "[pn-llmd] up uid=$(id -u) POOL=$PN_LLM_POOL MODEL=$PN_LLM_MODEL CMD=[$PN_LLM_CMD]"
exec sleep infinity
EOF
chmod 0755 "$MNT$SVC_HOME/portioneer/tools/pn-llmd"

cat > "$MNT$SVC_HOME/.local/bin/brainbox-portal" <<'EOF'
#!/usr/bin/python3
import os, sys, time
print("[brainbox-portal] up uid=%d args=%s" % (os.getuid(), sys.argv[1:]), flush=True)
while True: time.sleep(60)
EOF
chmod 0755 "$MNT$SVC_HOME/.local/bin/brainbox-portal"

cat > "$MNT$SVC_HOME/zyrkel/.env" <<'EOF'
ZYRKEL_TOKEN=stub-token-from-EnvironmentFile
ZYRKEL_FROM_ENVFILE=yes
EOF
chmod 0600 "$MNT$SVC_HOME/zyrkel/.env"
cat > "$MNT$SVC_HOME/zyrkel/config.json" <<'EOF'
{ "stub": true }
EOF
cat > "$MNT$SVC_HOME/zyrkel/target/release/zyrkel" <<'EOF'
#!/bin/sh
# zyrkel (stub) — reads its EnvironmentFile (~/zyrkel/.env) itself, like the real supervisor.
[ -r "$HOME/zyrkel/.env" ] && . "$HOME/zyrkel/.env"
echo "[zyrkel] up uid=$(id -u) cwd=$(pwd) PATH=$PATH XDG=$XDG_RUNTIME_DIR ENVFILE=${ZYRKEL_FROM_ENVFILE:-no} cfg=$1"
exec sleep infinity
EOF
chmod 0755 "$MNT$SVC_HOME/zyrkel/target/release/zyrkel"

chroot "$MNT" chown -R "$SERVICE_USER:$SERVICE_USER" "$SVC_HOME" 2>/dev/null || true

install -d -m 0755 "$MNT/usr/local/bin"
cat > "$MNT/usr/local/bin/pn-netpin" <<'EOF'
#!/bin/sh
echo "[netpin] pinning primary link up"
for i in $(ls /sys/class/net 2>/dev/null); do
  [ "$i" = lo ] && continue
  ip link set "$i" up 2>/dev/null && echo "[netpin] $i up"
done
echo "[netpin] done"
EOF
chmod 0755 "$MNT/usr/local/bin/pn-netpin"
cat > "$MNT/usr/local/bin/pn-portioneer-run" <<'EOF'
#!/bin/sh
# create the broker-socket dir: mkdir + chgrp pnbroker + chmod 2770 (setgid so the socket inherits
# the group). Runs as root (before pnd). Idempotent.
mkdir -p /run/portioneer
chgrp pnbroker /run/portioneer 2>/dev/null || echo "[portioneer-run] WARN chgrp pnbroker failed"
chmod 2770 /run/portioneer
echo "[portioneer-run] /run/portioneer ready: $(ls -ld /run/portioneer)"
EOF
chmod 0755 "$MNT/usr/local/bin/pn-portioneer-run"

cat > "$MNT/usr/local/bin/pn-cgtree" <<'PNEOF'
#!/bin/bash
# pn-cgtree — build the uid-1000-delegated shared-parent cgroup layout that lets the
# unprivileged pnd place governed job leaves.
#
# THE WALL it removes: pnd runs (uid 1000) in pn-critical.slice/pnd; job leaves go in
# pn-batch.slice/pn-job-N. Their common ancestor is the ROOT cgroup (mode 555, un-chownable),
# so an unprivileged migrate/create across the two is EACCES (cgroup-v2 delegation containment).
#
# THE FIX: one delegated parent `pn.slice` (owned by uid 1000 at the .procs/.threads level) with
#   pn.slice/critical  -> pnd lives here, PROTECTED (memory.min floor, top cpu weight)
#   pn.slice/batch      -> per-job leaves live here, CAPPED (memory.high/max/swap.max)
# Now pnd's control cgroup and the job leaves share the WRITABLE common ancestor pn.slice ->
# unprivileged placement is permitted. Caps stay ROOT-owned (delegatee can't lift its own limits).
#
# Idempotent + fail-safe. Reads caps from pn-init's already-sized pn-batch/pn-critical slices so it
# stays correct across hardware. Run as root (boot oneshot + live apply).
set -u
CG=/sys/fs/cgroup
U=__PN_SERVICE_USER__
PN=$CG/pn.slice

log(){ echo "[pn-cgtree] $*"; }
w(){ printf '%s' "$2" > "$1" 2>/dev/null && return 0; log "  warn: could not write $1 <- '$2'"; return 1; }
own(){ chown "$U" "$1" 2>/dev/null || log "  warn: could not chown $1"; }
rd(){ cat "$1" 2>/dev/null; }

[ -d "$CG" ] || { log "no cgroup2 at $CG — abort (fail-safe)"; exit 0; }

# ---- read caps pn-init already computed for this box (fall back to conservative defaults) ----
B=$CG/pn-batch.slice
C=$CG/pn-critical.slice
BHIGH=$(rd $B/memory.high);      [ -n "${BHIGH:-}" ] && [ "$BHIGH" != max ] || BHIGH=$((3*1024*1024*1024))
BMAX=$(rd $B/memory.max);        [ -n "${BMAX:-}"  ] && [ "$BMAX"  != max ] || BMAX=$((4*1024*1024*1024))
BSWAP=$(rd $B/memory.swap.max);  [ -n "${BSWAP:-}" ] && [ "$BSWAP" != max ] || BSWAP=$((1024*1024*1024))
BCPUW=$(rd $B/cpu.weight);       [ -n "${BCPUW:-}" ] || BCPUW=1000
CMIN=536870912   # 512M floor for pnd (tiny process; plenty unreclaimable). Kept modest on purpose.

log "caps: batch high=$BHIGH max=$BMAX swap=$BSWAP cpuw=$BCPUW ; critical min=$CMIN"

# ---- pn.slice (shared parent). No member procs -> safe to distribute controllers. ----
mkdir -p "$PN" 2>/dev/null
w "$PN/memory.min" "$CMIN"
w "$PN/cgroup.subtree_control" "+cpu +io +memory +pids"
# delegate ONLY the migration-relevant core files (NOT subtree_control -> delegatee can't
# undistribute the memory controller and thereby uncap batch). Caps stay root-owned.
own "$PN/cgroup.procs"
own "$PN/cgroup.threads"

# ---- pn.slice/critical (protected home for pnd) ----
mkdir -p "$PN/critical" 2>/dev/null
w "$PN/critical/memory.min" "$CMIN"
w "$PN/critical/memory.low" "$CMIN"
w "$PN/critical/cpu.weight" "10000"
w "$PN/critical/cgroup.subtree_control" "+cpu +memory +pids"
mkdir -p "$PN/critical/pnd" 2>/dev/null   # pnd's leaf (root places pnd here via pn-cgmove)

# ---- pn.slice/batch (capped tier for job leaves) ----
mkdir -p "$PN/batch" 2>/dev/null
w "$PN/batch/memory.high" "$BHIGH"
w "$PN/batch/memory.max"  "$BMAX"
w "$PN/batch/memory.swap.max" "$BSWAP"
w "$PN/batch/cpu.weight"  "$BCPUW"
w "$PN/batch/cgroup.subtree_control" "+cpu +memory +pids"
# delegate the batch tier so the unprivileged child can mkdir + configure + join leaves
own "$PN/batch"
own "$PN/batch/cgroup.procs"
own "$PN/batch/cgroup.subtree_control"
own "$PN/batch/cgroup.threads"

log "layout:"
for d in "$PN" "$PN/critical" "$PN/critical/pnd" "$PN/batch"; do
  printf '  %s ' "$d"; ls -ld "$d" 2>/dev/null | awk '{print $1,$3,$4}'
done
log "ownership of delegated core files:"
ls -l "$PN/cgroup.procs" "$PN/batch/cgroup.procs" "$PN/batch" 2>/dev/null | sed 's/^/  /'
log "done."
PNEOF
sed -i "s/^U=__PN_SERVICE_USER__\$/U=$SERVICE_USER/" "$MNT/usr/local/bin/pn-cgtree"
chmod 0755 "$MNT/usr/local/bin/pn-cgtree"
cat > "$MNT/usr/local/bin/pn-cgmove" <<'PNEOF'
#!/bin/bash
# pn-cgmove — migrate the running pnd (uid 1000) into pn.slice/critical/pnd so pnd's control
# cgroup and the job leaves (pn.slice/batch/*) share the delegated common ancestor pn.slice ->
# unprivileged job placement is permitted. Run as ROOT. Idempotent + fail-safe (never bricks boot).
#
# Used both live (after a governed pnd restart) and as a pn-init boot oneshot AFTER pnd. pn-init
# supervises pnd BY PID (watchdog socket + pid), which a cgroup move does not change -> supervision
# stays intact.
set -u
CG=/sys/fs/cgroup
DEST=$CG/pn.slice/critical/pnd
log(){ echo "[pn-cgmove] $*"; }

[ -d "$CG/pn.slice/batch" ] || { log "pn.slice not built (run pn-cgtree first) — skip"; exit 0; }
mkdir -p "$DEST" 2>/dev/null

# locate pnd by its EXACT ExecStart argv (uid 1000). tools/pnd is NOT a substring of tools/pn-llmd,
# so this never grabs the broker; still guard explicitly.
pid=""
for p in $(pgrep -f "portioneer/tools/pnd" 2>/dev/null); do
  cmd=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)
  case "$cmd" in
    *portioneer/tools/pn-llmd*) continue ;;
    *portioneer/tools/pnd*)     pid=$p; break ;;
  esac
done
[ -n "$pid" ] || { log "pnd not running — nothing to migrate (skip)"; exit 0; }

before=$(cat /proc/$pid/cgroup 2>/dev/null)
if printf '%s' "$pid" > "$DEST/cgroup.procs" 2>/dev/null; then
  after=$(cat /proc/$pid/cgroup 2>/dev/null)
  log "pnd pid=$pid migrated:"
  log "  before: $before"
  log "  after : $after"
  case "$after" in
    *pn.slice/critical/pnd*) log "OK — pnd now under the delegated shared parent" ;;
    *) log "WARN: migration not reflected (LLM may stay down; box unaffected)" ;;
  esac
else
  log "WARN: could not migrate pnd pid=$pid (leaving in place; LLM stays down, box unaffected)"
fi
PNEOF
chmod 0755 "$MNT/usr/local/bin/pn-cgmove"

echo "[7/8] /etc/pn-init.conf = OPTION (b) reference cutover conf ($PNCONF) + a selftest oneshot"
sed "s|@SERVICE_USER@|$SERVICE_USER|g" "$PNCONF" > "$MNT/etc/pn-init.conf"
chmod 0644 "$MNT/etc/pn-init.conf"
cat > "$MNT/usr/local/bin/pn-selftest" <<'EOF'
#!/bin/sh
sleep 10
echo "==== PN-SELFTEST BEGIN ===="
echo "[selftest] PID1 comm: $(cat /proc/1/comm)"
echo "[selftest] SYSTEMD_PROCS=$(ps -e -o comm= 2>/dev/null | grep -c '^systemd' || echo 0)"
echo "[selftest] systemd proc list: $(ps -e -o pid=,comm= 2>/dev/null | grep systemd || echo NONE)"
echo "[selftest] pn stack as uid 1000:"
ps -e -o uid=,pid=,comm=,args= 2>/dev/null | awk '$1==1000{print "  "$0}' | grep -E 'pnd|pndstub|pn-llmd|phantom|python|zyrkel|sleep' | head -20
echo "[selftest] ip: $(ip -br addr 2>/dev/null | tr '\n' '|')"
echo "[selftest] sshd :22: $( (ss -tln 2>/dev/null||busybox netstat -tln 2>/dev/null)|grep -q ':22 ' && echo LISTENING || echo NO)"
echo "[selftest] /run/user/1000: $(ls -ld /run/user/1000 2>/dev/null||echo MISSING)"
echo "[selftest] pnd user socket: $(ls -l /run/user/1000/pnd.sock 2>/dev/null && echo PND_SOCK_PRESENT || echo PND_SOCK_MISSING)"
echo "[selftest] /run/portioneer: $(ls -ld /run/portioneer 2>/dev/null||echo MISSING)"
echo "==== PN-SELFTEST END ===="
EOF
chmod 0755 "$MNT/usr/local/bin/pn-selftest"
printf '\n# post-boot selftest (proves PID1/zero-systemd/stack/pnd.sock to serial)\nselftest|oneshot|/usr/local/bin/pn-selftest\n' >> "$MNT/etc/pn-init.conf"

echo "    --- installed /etc/pn-init.conf (non-comment lines) ---"
grep -vE '^\s*#|^\s*$' "$MNT/etc/pn-init.conf" | sed 's/^/      /'

echo "[8/8] shrink + sanity"
chroot "$MNT" apt-get clean >/dev/null 2>&1 || true
rm -rf "$MNT/var/lib/apt/lists/"* "$MNT/var/cache/apt/archives/"*.deb 2>/dev/null || true
[ -x "$MNT/sbin/agetty" ] && echo "  OK: agetty present (console recovery getty)" || echo "  WARN: agetty missing"
echo "  systemd daemon on disk (present but NEVER PID1; pn-init is): $( ([ -x "$MNT/lib/systemd/systemd" ]||[ -x "$MNT/usr/lib/systemd/systemd" ]) && echo yes || echo no )"
sync
echo "DONE: $IMG ($(du -h "$IMG"|cut -f1)); service user uid1000 pw=pntest; root label=pnroot"
