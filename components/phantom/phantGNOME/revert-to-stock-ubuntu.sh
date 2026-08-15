#!/usr/bin/env bash
set -u
gnome-extensions disable phantom-ui@phantgnome 2>/dev/null || true
gnome-extensions disable phantom-lifeboat@phantgnome 2>/dev/null || true
python3 - <<'PY'
from gi.repository import Gio
s = Gio.Settings.new('org.gnome.shell')
cur = [u for u in s.get_strv('enabled-extensions') if not u.endswith('@phantgnome')]
s.set_strv('enabled-extensions', cur)
print('enabled-extensions =', s.get_strv('enabled-extensions'))
PY
echo "phantom extensions disabled -> stock Ubuntu on next login (sudo systemctl restart gdm)."
echo "Re-apply phantGNOME: sudo bash apply-ubuntu-mode.sh"
