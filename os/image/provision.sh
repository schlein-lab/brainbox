#!/bin/bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/schlein-lab/brainarbeit.git}"
REPO="${BRAINBOX_REPO:-$HOME/brainarbeit}"
IMG="$REPO/os/image"

echo "############ Brainbox provision ############"

echo "== 1) repo =="
if [ -d "$REPO/.git" ]; then
  if git -C "$REPO" rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    git -C "$REPO" pull --ff-only || true
  else
    echo "  (lokale Kopie ohne Fernverbindung -- nichts zu holen)"
  fi
else git clone --depth 1 "$REPO_URL" "$REPO"; fi

echo "== 2) dependencies =="
bash "$IMG/install-deps-aarch64.sh"

echo "== 3) native ARM64 bins (pn-init, phantom, wasm; cells stay OFF) =="
bash "$IMG/build-arm64.sh"

echo "== 3b) runtime helper links (~/.local/bin -> repo) =="
mkdir -p "$HOME/.local/bin"
_nlink=0; _miss=""
for spec in $(sed -n '/---8<--- HELPERS/,/---8<--- END HELPERS/p' "$IMG/build-appliance-disk.sh" \
                | grep -oE '^[[:space:]]*[A-Za-z0-9_.-]+\|[A-Za-z0-9_./-]+' | tr -d ' '); do
  hn="${spec%%|*}"; hrel="${spec#*|}"
  if [ -s "$REPO/$hrel" ]; then
    chmod 0755 "$REPO/$hrel" 2>/dev/null || true
    ln -sf "$REPO/$hrel" "$HOME/.local/bin/$hn"; _nlink=$((_nlink+1))
  else _miss="$_miss $hn($hrel)"; fi
done
ln -sf "$REPO/cockpit/server/brainbox-portal" "$HOME/.local/bin/brainbox-portal"
ln -sfn "$REPO/engine" "$HOME/portioneer"
if [ -s "$REPO/engine/tools/device_discover.py" ]; then
  chmod 0755 "$REPO/engine/tools/device_discover.py"
  ln -sf "$REPO/engine/tools/device_discover.py" "$HOME/.local/bin/device_discover.py"
else
  echo "  WARN: engine/tools/device_discover.py not in the checkout — /api/devices/scan will run the"
  echo "        host-neighbour sweep only (no mDNS/SSDP: no Chromecast, Sonos or TV discovery)."
fi
echo "  linked $_nlink helpers into $HOME/.local/bin"
[ -z "$_miss" ] || { echo "  FATAL: helper source(s) missing from the checkout:$_miss"; exit 1; }
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *)
  echo "  NOTE: $HOME/.local/bin is not on PATH in this shell (~/.profile adds it at next login)." ;;
esac

echo "== 4) site config =="
sudo mkdir -p /etc/brainbox
[ -f /etc/brainbox/site.conf ] || sudo cp "$IMG/site.conf.example" /etc/brainbox/site.conf
sudo touch /etc/brainbox/is-appliance
sudo groupadd -f input 2>/dev/null || true
sudo usermod -aG input "$(id -un)" 2>/dev/null || true
sudo usermod -aG kvm "$(id -un)" 2>/dev/null || true

echo "== 5) boot chain (pn-init as PID1 on the Pi) =="
CMD=/boot/firmware/cmdline.txt; [ -f "$CMD" ] || CMD=/boot/cmdline.txt
if [ -f "$CMD" ] && ! grep -q 'init=/sbin/pn-init' "$CMD"; then
  sudo cp "$CMD" "$CMD.pre-brainbox"
  sudo sed -i 's#\bconsole=ttyS0[^ ]*#console=ttyAMA0,115200#' "$CMD" || true
  sudo sed -i 's#$# init=/sbin/pn-init#' "$CMD"
  echo "  set init=/sbin/pn-init + console=ttyAMA0 (backup: $CMD.pre-brainbox)"
fi
if [ -f "$CMD" ] && ! grep -q 'fsck.repair=' "$CMD"; then
  sudo sed -i 's#$# fsck.repair=yes#' "$CMD"
  echo "  set fsck.repair=yes (Selbstreparatur statt Neustartschleife nach Stromausfall)"
fi
SERVICE_USER="$(id -un)"; SERVICE_HOME="$HOME"; SERVICE_UID="$(id -u)"
[ -f /etc/brainbox/site.conf ] && . /etc/brainbox/site.conf || true
: "${SERVICE_USER:=$(id -un)}"; : "${SERVICE_HOME:=$HOME}"; : "${SERVICE_UID:=$(id -u)}"
printf 'SERVICE_USER=%s\nSERVICE_HOME=%s\nSERVICE_UID=%s\n' "$SERVICE_USER" "$SERVICE_HOME" "$SERVICE_UID" | sudo tee /etc/brainbox/service.env >/dev/null
BBX_VERSION="$(cat "$IMG/VERSION" 2>/dev/null || echo 0.0.0-dev)"
sudo tee /usr/lib/os-release >/dev/null <<OSR
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
sudo ln -sfn ../usr/lib/os-release /etc/os-release
printf '%s\n' "$BBX_VERSION" | sudo tee /etc/brainbox/version >/dev/null
sudo apt-get install -y chrony >/dev/null 2>&1 \
  || echo "  WARN: chrony install failed (offline bake?) — install it on first boot or the clock drifts"

_ppconf="$(mktemp)"
sed -e "s#/home/brainbox#${SERVICE_HOME}#g" -e "s#user=1000#user=${SERVICE_UID}#g" \
    -e "s#/run/user/1000#/run/user/${SERVICE_UID}#g" \
    "$IMG/pn-init.conf.pi" > "$_ppconf"
sudo install -m0644 "$_ppconf" /etc/pn-init.conf
rm -f "$_ppconf"
echo "  rendered pn-init.conf for user=${SERVICE_UID} home=${SERVICE_HOME}"
sudo install -m0755 "$IMG/firstboot.sh" /usr/local/sbin/brainbox-firstboot.sh
sudo install -m0755 "$IMG/factory-clean.sh" /usr/local/sbin/brainbox-factory-clean.sh
sudo install -m0755 "$IMG/brainbox-earlyboot" /usr/local/sbin/brainbox-earlyboot
sudo install -m0755 "$IMG/brainbox-hotplug" /usr/local/sbin/brainbox-hotplug
sudo install -m0755 "$IMG/brainbox-caps-detect" /usr/local/sbin/brainbox-caps-detect
sudo install -m0755 "$IMG/brainbox-setup" /usr/local/sbin/brainbox-setup
sudo install -m0755 "$IMG/brainbox-netcfg" /usr/local/sbin/brainbox-netcfg
sudo install -m0755 "$IMG/brainbox-banner" /usr/local/sbin/brainbox-banner
sudo install -m0755 "$IMG/brainbox-firewall" /usr/local/sbin/brainbox-firewall
_BOOTP=/boot/firmware; [ -d "$_BOOTP" ] || _BOOTP=/boot
sudo bash "$IMG/make-startkarte.sh" "$_BOOTP" pi || true
REPO_D="${BRAINBOX_REPO:-$HOME/brainarbeit}"
sudo install -m0755 "$REPO_D/os/breakglass/pn_breakglassd.py" /usr/local/sbin/brainbox-breakglassd
sudo install -m0755 "$REPO_D/os/breakglass/breakglass.sh"     /usr/local/sbin/brainbox-breakglass
sudo install -d -m0755 /usr/local/share/brainbox-breakglass/static
for a in xterm.js xterm.css addon-fit.js; do
  sudo install -m0644 "$REPO_D/cockpit/server/webapp/static/$a" "/usr/local/share/brainbox-breakglass/static/$a"
done

sudo install -m0755 "$REPO_D/os/init/pn-cgmove"         /usr/local/bin/pn-cgmove
sudo install -m0755 "$REPO_D/os/init/pn-cgtree"         /usr/local/bin/pn-cgtree
sudo install -m0755 "$REPO_D/os/init/pn-netpin"         /usr/local/bin/pn-netpin
sudo install -m0755 "$REPO_D/os/init/pn-portioneer-run" /usr/local/bin/pn-portioneer-run
sudo install -m0755 "$REPO_D/cockpit/server/pn-shutdown" /usr/local/sbin/pn-shutdown
[ -f "$REPO_D/cockpit/server/pn-mediashare-provision" ] && sudo install -m0755 "$REPO_D/cockpit/server/pn-mediashare-provision" /usr/local/sbin/pn-mediashare-provision || true
sudo getent group pnbroker >/dev/null || sudo groupadd -g 4003 pnbroker
sudo install -d -m 0755 /etc/sudoers.d
printf "%s ALL=(root) NOPASSWD: /usr/local/bin/pn-cgmove\n"    "$SERVICE_USER" | sudo tee /etc/sudoers.d/brainbox-cgmove   >/dev/null
printf "%s ALL=(root) NOPASSWD: /usr/local/sbin/pn-shutdown\n" "$SERVICE_USER" | sudo tee /etc/sudoers.d/brainbox-shutdown >/dev/null
[ -x /usr/local/sbin/pn-mediashare-provision ] && printf "%s ALL=(root) NOPASSWD: /usr/local/sbin/pn-mediashare-provision\n" "$SERVICE_USER" | sudo tee /etc/sudoers.d/brainbox-mediashare >/dev/null || true
sudo chmod 0440 /etc/sudoers.d/brainbox-cgmove /etc/sudoers.d/brainbox-shutdown; [ -f /etc/sudoers.d/brainbox-mediashare ] && sudo chmod 0440 /etc/sudoers.d/brainbox-mediashare || true
printf "# pnd tuning — read at service exec time (pn-init envfile=). All optional.\n#PN_BATCH_HIGH=\n#PN_MEM_FLOOR=\n#PN_MAX_CONCURRENT=\n#PN_INTERACTIVE_RESERVE=\n" | sudo tee /etc/brainbox/pnd.env >/dev/null
sudo ssh-keygen -A 2>/dev/null || true
echo "  appliance runtime: pn-Helfer + sudoers + pnd.env + sshd-Hostkeys installiert"

echo "== 6) factory-clean (remove any build-box state before firstboot arms) =="
bash "$IMG/factory-clean.sh" --apply || true

echo "== 6.5) factory-verify (abort if any secret survived the clean) =="
bash "$IMG/factory-verify.sh" / || { echo "ABORT: secrets present after factory-clean"; exit 1; }

echo "== 7) done =="
echo "  Reboot to boot via pn-init; firstboot will mint a unique hostname/PIN/CA and print a"
echo "  welcome card to the console + boot partition."
echo "  Einrichten: http://brainbox.local/   ·   Portal danach: https://brainbox.local:8076/"
