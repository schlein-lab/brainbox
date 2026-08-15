#!/bin/bash
set -euo pipefail
WORK="${WORK:-$HOME/pi-image-build}"
BASE_XZ="$WORK/base.img.xz"
IMG="$WORK/brainbox-appliance-arm64.img"
GROW_MB="${GROW_MB:-6144}"
REPO_TAR="$WORK/repo.tar"
SVC="${SVC:-ubuntu}"
LOG="$WORK/bake.log"
say(){ echo "== $* ==" | tee -a "$LOG"; }
exec > >(tee -a "$LOG") 2>&1

[ -f "$BASE_XZ" ] || { echo "FATAL: missing $BASE_XZ"; exit 1; }
[ -f "$REPO_TAR" ] || { echo "FATAL: missing $REPO_TAR (git archive of the repo)"; exit 1; }

MNT=""; LOOP=""
cleanup(){ [ -n "$MNT" ] && sudo umount -R "$MNT" 2>/dev/null || true
           [ -n "$LOOP" ] && sudo losetup -d "$LOOP" 2>/dev/null || true
           [ -n "$MNT" ] && rmdir "$MNT" 2>/dev/null || true; }
trap cleanup EXIT

if [ "${REUSE_IMG:-0}" = "1" ] && [ -f "$IMG" ]; then
  say "1-2) REUSE prepared $IMG (skip decompress/grow/resize) — only safe if last fail was pre-provision"
  LOOP=$(sudo losetup --show -fP "$IMG")
else
  say "1) decompress base + grow image (+${GROW_MB}M)"
  rm -f "$IMG" "$IMG.xz"
  xz -dc "$BASE_XZ" > "$IMG"
  truncate -s +${GROW_MB}M "$IMG"

  say "2) loop-map + grow root partition (p2) to fill"
  LOOP=$(sudo losetup --show -fP "$IMG")
  sudo parted -s "$LOOP" resizepart 2 100%
  sudo e2fsck -fy "${LOOP}p2" || true
  sudo resize2fs "${LOOP}p2"
fi

say "3) mount rootfs (p2) + boot (p1) + chroot binds"
MNT=$(mktemp -d)
sudo mount "${LOOP}p2" "$MNT"
sudo mkdir -p "$MNT/boot/firmware"
sudo mount "${LOOP}p1" "$MNT/boot/firmware"
for m in proc sys dev dev/pts; do sudo mkdir -p "$MNT/$m"; sudo mount --bind "/$m" "$MNT/$m"; done
sudo rm -f "$MNT/etc/resolv.conf"
printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\n' | sudo tee "$MNT/etc/resolv.conf" >/dev/null

say "4) stage brainbox HEAD into /home/$SVC/brainarbeit (+ local git so provision.sh's pull is a no-op)"
sudo install -d -o 1000 -g 1000 "$MNT/home/$SVC/brainarbeit"
sudo tar -x -C "$MNT/home/$SVC/brainarbeit" -f "$REPO_TAR"
sudo chroot "$MNT" runuser -u "$SVC" -- bash -lc "cd ~/brainarbeit && git init -q && git add -A && git -c user.email=b@b -c user.name=bake commit -qm baked-HEAD" \
  || echo "  WARN: baked-HEAD-Commit fehlgeschlagen -- das Abbild traegt dann keine Versionsgeschichte"
sudo chroot "$MNT" chown -R 1000:1000 "/home/$SVC/brainarbeit"

say "5) temporary passwordless sudo for $SVC (provision.sh uses sudo; removed in step 8)"
echo "$SVC ALL=(ALL) NOPASSWD:ALL" | sudo tee "$MNT/etc/sudoers.d/zz-bake" >/dev/null
sudo chmod 0440 "$MNT/etc/sudoers.d/zz-bake"

say "5.5) create appliance user $SVC (uid 1000) — Ubuntu images defer this to cloud-init, which the pn-init boot bypasses"
sudo chroot "$MNT" bash -c "
  id -u $SVC >/dev/null 2>&1 || useradd -u 1000 -d /home/$SVC -s /bin/bash $SVC
  getent group sudo >/dev/null 2>&1 && usermod -aG sudo $SVC 2>/dev/null || true
  # NICHT mehr 'brainbox': das war auf jedem gebauten Geraet dasselbe, im Repo nachlesbare
  # Passwort -- und sshd liess Passwortanmeldung zu. 2026-07-28 live nachgewiesen: damit kam
  # jeder aus dem LAN als root auf pi1 und pi2. Jetzt pro Image zufaellig, nur fuer Konsole/sudo,
  # beim ersten Login zu aendern, und hinterlegt unter /etc/brainbox/initial-console-password.
  BBPW=\$(head -c 4096 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 20)
  echo \"$SVC:\$BBPW\" | chpasswd
  passwd -l root >/dev/null 2>&1 || true
  install -d -m 0755 /etc/brainbox
  printf '%s\\n' \"\$BBPW\" > /etc/brainbox/initial-console-password
  chmod 0600 /etc/brainbox/initial-console-password
  # sshd: Schluessel-only. Die Erstinbetriebnahme laeuft ueber Konsole + Setup-Assistent.
  install -d -m 0755 /etc/ssh/sshd_config.d
  printf '%s\\n' 'PasswordAuthentication no' 'KbdInteractiveAuthentication no' 'PermitRootLogin no' \\
    > /etc/ssh/sshd_config.d/99-brainbox-hardening.conf
  cp -rn /etc/skel/. /home/$SVC/ 2>/dev/null || true
  chown -R 1000:1000 /home/$SVC
"

say "5.6) apt sources: add noble-updates (the Ubuntu RPi image ships it MISSING -> release/updates version splits like libbz2 break every apt install)"
sudo sed -i 's/^Suites: noble$/Suites: noble noble-updates/' "$MNT/etc/apt/sources.list.d/ubuntu.sources" || true
grep -q "noble noble-updates" "$MNT/etc/apt/sources.list.d/ubuntu.sources" && echo "  noble-updates added" || echo "  WARN: could not add noble-updates (apt may still conflict)"

say "6) RUN provision.sh in the chroot as $SVC (repo->deps->ARM bins->boot-chain->firstboot-arm)"
sudo chroot "$MNT" runuser -u "$SVC" -- bash -lc \
  'export HOME=/home/'"$SVC"'; cd $HOME/brainarbeit && BRAINBOX_REPO=$HOME/brainarbeit bash os/image/provision.sh' \
  || { echo "FATAL: provision.sh failed (see $LOG)"; exit 1; }

say "7) build + ship pn-vmm (aarch64) — cells auto-enable at boot via caps-detect iff /dev/kvm present"
sudo chroot "$MNT" runuser -u "$SVC" -- bash -lc \
  'export HOME=/home/'"$SVC"'; source $HOME/.cargo/env 2>/dev/null || true; cd $HOME/brainarbeit/os/pn-vmm && cargo build --release && ls -l target/release/pn-vmm' \
  || echo "  WARN: pn-vmm build failed — appliance still boots, cells stay OFF (caps-detect gates on the binary)"

say "7b) Startkarte auf die Boot-Partition (das Einzige, was ein Neuling nach dem Flashen sieht)"
sudo bash "$(dirname "$0")/make-startkarte.sh" "$MNT/boot/firmware" pi
sudo test -s "$MNT/boot/firmware/START-HIER.txt" \
  || { echo "FATAL: Startkarte fehlt auf der Boot-Partition"; exit 1; }

_kartenkonto=$(sudo sed -n 's/.*ssh \([a-z_][a-z0-9_-]*\)@brainbox\.local.*/\1/p' \
                 "$MNT/boot/firmware/START-HIER.txt" | head -1 | tr -d '\r')
_abbildkonto=$(sudo chroot "$MNT" getent passwd 1000 | cut -d: -f1)
[ -n "$_kartenkonto" ] && [ "$_kartenkonto" = "$_abbildkonto" ] || {
  echo "FATAL: Startkarte nennt '$_kartenkonto', uid 1000 im Abbild ist '$_abbildkonto'"; exit 1; }
say "    Karte und Abbild nennen dasselbe Konto: $_abbildkonto"

say "8) strip temp sudo + build/apt caches (image hygiene)"
sudo rm -f "$MNT/etc/sudoers.d/zz-bake"
sudo chroot "$MNT" chage -d -1 "$SVC" 2>/dev/null || true
sudo chroot "$MNT" chage -M 99999 "$SVC" 2>/dev/null || true
sudo test -s "$MNT/etc/brainbox/initial-console-password" \
  && say "   ok: einmaliges Konsolen-Passwort hinterlegt (/etc/brainbox/initial-console-password)" \
  || { echo "FATAL: kein initial-console-password im Image"; exit 1; }
sudo grep -q "^PasswordAuthentication no" "$MNT/etc/ssh/sshd_config.d/99-brainbox-hardening.conf" \
  && say "   ok: SSH ist schluessel-only" \
  || { echo "FATAL: sshd-Haertung fehlt im Image"; exit 1; }
sudo rm -f "$MNT"/etc/ssh/ssh_host_*
if sudo find "$MNT/etc/ssh" -maxdepth 1 -name 'ssh_host_*' -print -quit 2>/dev/null | grep -q .; then
  echo "FATAL: SSH-Hostkeys noch im Image"; exit 1
fi
say "   ok: keine SSH-Hostkeys im Image (firstboot erzeugt sie pro Geraet)"
sudo chroot "$MNT" bash -c 'apt-get clean 2>/dev/null; rm -rf /var/lib/apt/lists/* /tmp/* /home/'"$SVC"'/.cargo/registry/cache 2>/dev/null' || true

say "9) unmount + package"
sudo umount -R "$MNT"; MNT=""
sudo losetup -d "$LOOP"; LOOP=""
say "compress -> $IMG.xz (this takes a while)"
xz -T0 -6 -c "$IMG" > "$IMG.xz"
echo "DONE_OK"
ls -lh "$IMG" "$IMG.xz"
