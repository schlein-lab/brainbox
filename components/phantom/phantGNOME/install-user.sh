#!/usr/bin/env bash
set -eu

SRC="$(cd "$(dirname "$0")" && pwd)"
UUID="phantom-ui@phantgnome"
LIFEBOAT_UUID="phantom-lifeboat@phantgnome"

EXT_DST="$HOME/.local/share/gnome-shell/extensions/$UUID"
LIFEBOAT_DST="$HOME/.local/share/gnome-shell/extensions/$LIFEBOAT_UUID"
MODE_DST="$HOME/.local/share/gnome-shell/modes"

echo "== installing umbrella extension -> $EXT_DST"
mkdir -p "$EXT_DST"
cp -f "$SRC/extensions/$UUID/extension.js"   "$EXT_DST/"
cp -f "$SRC/extensions/$UUID/metadata.json"  "$EXT_DST/"
cp -f "$SRC/extensions/$UUID/stylesheet.css" "$EXT_DST/"
mkdir -p "$EXT_DST/schemas"
cp -f "$SRC/extensions/$UUID/schemas/"*.gschema.xml "$EXT_DST/schemas/"

echo "== compiling gsettings schema"
glib-compile-schemas "$EXT_DST/schemas"

echo "== installing independent lifeboat extension -> $LIFEBOAT_DST"
mkdir -p "$LIFEBOAT_DST"
cp -f "$SRC/extensions/$LIFEBOAT_UUID/extension.js"  "$LIFEBOAT_DST/"
cp -f "$SRC/extensions/$LIFEBOAT_UUID/metadata.json" "$LIFEBOAT_DST/"

echo "== installing phantom session mode -> $MODE_DST"
mkdir -p "$MODE_DST"
cp -f "$SRC/modes/phantom.json" "$MODE_DST/"

echo "== done (user scope)."
echo
echo "Enable the extensions in your CURRENT session with:"
echo "    gnome-extensions enable $LIFEBOAT_UUID"
echo "    gnome-extensions enable $UUID"
echo "(a full GNOME Shell restart — log out/in, or Alt-F2 'r' on X11 — is needed"
echo " to pick up newly installed extension code.)"
echo
echo "To boot the bare 'phantom' session from the GDM greeter, run the generated"
echo "privileged installer ONCE as the operator:  sudo bash $SRC/install-system.sh"
