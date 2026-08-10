#!/usr/bin/env bash
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root (sudo bash install-system.sh)." >&2
    exit 1
fi

SRC="$(cd "$(dirname "$0")" && pwd)"
UUID="phantom-ui@phantgnome"

install -D -m644 "$SRC/session/phantom.session" \
    /usr/share/gnome-session/sessions/phantom.session

install -D -m644 "$SRC/session/phantom.desktop" \
    /usr/share/wayland-sessions/phantom.desktop
install -D -m644 "$SRC/session/phantom.desktop" \
    /usr/share/xsessions/phantom.desktop

install -D -m644 "$SRC/modes/phantom.json" \
    /usr/share/gnome-shell/modes/phantom.json

install -D -m644 "$SRC/session/zz-phantom.gschema.override" \
    /usr/share/glib-2.0/schemas/zz-phantom.gschema.override
glib-compile-schemas /usr/share/glib-2.0/schemas

echo "Read-back verification:"
rb_fail=0
verify() {
    local dst="$1" src="$2"
    if [ ! -f "$dst" ]; then
        echo "  MISSING  $dst" >&2; rb_fail=1; return
    fi
    if cmp -s "$dst" "$src"; then
        echo "  OK       $dst"
    else
        echo "  DIFFERS  $dst (does not match $src)" >&2; rb_fail=1
    fi
}
verify /usr/share/gnome-session/sessions/phantom.session "$SRC/session/phantom.session"
verify /usr/share/wayland-sessions/phantom.desktop       "$SRC/session/phantom.desktop"
verify /usr/share/xsessions/phantom.desktop              "$SRC/session/phantom.desktop"
verify /usr/share/gnome-shell/modes/phantom.json         "$SRC/modes/phantom.json"
verify /usr/share/glib-2.0/schemas/zz-phantom.gschema.override "$SRC/session/zz-phantom.gschema.override"
if command -v gsettings >/dev/null 2>&1; then
    val="$(gsettings get org.gnome.shell disable-extension-version-validation 2>/dev/null || echo '?')"
    if [ "$val" = "true" ]; then
        echo "  OK       org.gnome.shell disable-extension-version-validation=true (override live)"
    else
        echo "  WARN     disable-extension-version-validation=$val (override not yet live until next session)" >&2
    fi
fi
if [ "$rb_fail" -ne 0 ]; then
    echo "INSTALL INCOMPLETE — read-back found problems above." >&2
    exit 2
fi

echo "Installed (system scope):"
echo "  /usr/share/gnome-session/sessions/phantom.session"
echo "  /usr/share/wayland-sessions/phantom.desktop"
echo "  /usr/share/xsessions/phantom.desktop"
echo "  /usr/share/gnome-shell/modes/phantom.json"
echo "  /usr/share/glib-2.0/schemas/zz-phantom.gschema.override (+ recompiled schemas)"
echo
echo "Select 'phantom (phantGNOME)' from the gear menu at the GDM login screen."
echo "To uninstall: rm the five files listed above, then re-run"
echo "  glib-compile-schemas /usr/share/glib-2.0/schemas"
