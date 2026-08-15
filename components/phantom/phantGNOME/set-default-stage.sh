#!/usr/bin/env bash
set -euo pipefail

SCHEMA="org.gnome.shell.extensions.phantom"
KEY="default-stage"
STAGE="${1:-A}"

case "$STAGE" in
    A|B|C) ;;
    *) echo "usage: $0 A|B|C   (got '$STAGE')" >&2; exit 1;;
esac

EXT_SCHEMA_DIR="$HOME/.local/share/gnome-shell/extensions/phantom-ui@phantgnome/schemas"
GS=(gsettings)
if [ -f "$EXT_SCHEMA_DIR/gschemas.compiled" ]; then
    GS=(env GSETTINGS_SCHEMA_DIR="$EXT_SCHEMA_DIR" gsettings)
fi

if ! "${GS[@]}" writable "$SCHEMA" "$KEY" >/dev/null 2>&1; then
    echo "error: GSettings schema '$SCHEMA' not found." >&2
    echo "       Install the user extension first (install-user.sh) so its schema is compiled." >&2
    exit 2
fi

"${GS[@]}" set "$SCHEMA" "$KEY" "$STAGE"
GOT="$("${GS[@]}" get "$SCHEMA" "$KEY")"
echo "set $SCHEMA $KEY = $STAGE  (read-back: $GOT)"

if [ "$GOT" = "'$STAGE'" ]; then
    echo "OK — phantom session will boot into stage $STAGE."
    [ "$STAGE" = A ] && echo "     (Mode A: only the LLM window + HUD visible; everything else headless.)"
else
    echo "WARNING: read-back ($GOT) does not match requested stage '$STAGE'." >&2
    exit 3
fi
