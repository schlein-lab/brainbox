#!/usr/bin/env bash
set -u
USER_NAME="${SUDO_USER:-$USER}"
UID_N="$(id -u "$USER_NAME")"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
EXT_DIR="$HOME_DIR/.local/share/gnome-shell/extensions"
RUNUSER="XDG_RUNTIME_DIR=/run/user/$UID_N DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$UID_N/bus"

echo "[1/5] session-modes: let the phantom extensions enable in the ubuntu session"
for u in phantom-ui@phantgnome phantom-lifeboat@phantgnome; do
  f="$EXT_DIR/$u/metadata.json"
  [ -f "$f" ] && sed -i 's/"session-modes": \["phantom", "user"\]/"session-modes": ["ubuntu", "phantom", "user"]/' "$f" && echo "  $u -> $(grep -o '"session-modes":[^]]*]' "$f")"
done

echo "[2/5] enabled-extensions: turn the phantom extensions on (user dconf)"
sudo -u "$USER_NAME" env $RUNUSER python3 - <<'PY'
from gi.repository import Gio
s = Gio.Settings.new('org.gnome.shell')
cur = list(s.get_strv('enabled-extensions'))
for u in ['ubuntu-dock@ubuntu.com','ubuntu-appindicators@ubuntu.com','ding@rastersoft.com',
          'tiling-assistant@ubuntu.com','phantom-ui@phantgnome','phantom-lifeboat@phantgnome']:
    if u not in cur: cur.append(u)
s.set_strv('enabled-extensions', cur)
print('  enabled-extensions =', s.get_strv('enabled-extensions'))
PY

echo "[3/5] autologin session -> ubuntu (Accounts D-Bus is authoritative; a file sed gets clobbered by accounts-daemon)"
gdbus call --system --dest org.freedesktop.Accounts --object-path /org/freedesktop/Accounts/User${UID_N} --method org.freedesktop.Accounts.User.SetXSession "ubuntu" >/dev/null
gdbus call --system --dest org.freedesktop.Accounts --object-path /org/freedesktop/Accounts/User${UID_N} --method org.freedesktop.Accounts.User.SetSession "ubuntu" >/dev/null
echo "  Session set: $(grep -E '^Session' /var/lib/AccountsService/users/$USER_NAME 2>/dev/null)"

echo "[4/5] foot phantom theme + font"
install -D -m644 "$HOME_DIR/phantGNOME/config/foot/phantom.ini" "$HOME_DIR/.config/foot/phantom.ini" 2>/dev/null && chown "$USER_NAME":"$USER_NAME" "$HOME_DIR/.config/foot/phantom.ini"
DEBIAN_FRONTEND=noninteractive apt-get install -y fonts-jetbrains-mono >/dev/null 2>&1 || true

echo "[5/5] apply: sudo systemctl restart gdm"
echo "DONE. phantGNOME (ubuntu-session flavor) applied. Revert: bash revert-to-stock-ubuntu.sh"
