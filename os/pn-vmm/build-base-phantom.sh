#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
K="$HERE/kernel"
PHANTOM="${PHANTOM_BIN:-$HOME/phantom-stufe8/target/release/phantom}"
SRC="$K/base-python.img"
OUT="$K/base-phantom.img"
[ -f "$SRC" ]     || { echo "missing base rootfs $SRC"; exit 1; }
[ -x "$PHANTOM" ] || { echo "missing phantom binary $PHANTOM"; exit 1; }

echo "phantom: $(ls -la "$PHANTOM" | awk '{print $5" bytes"}')  glibc: $(objdump -T "$PHANTOM" | grep -oE 'GLIBC_2\.[0-9]+' | sort -V | tail -1)"
cp -f "$SRC" "$OUT"
debugfs -w -R "rm /bin/phantom" "$OUT" 2>/dev/null || true
printf 'write %s /bin/phantom\nsif /bin/phantom mode 0100755\nsif /bin/phantom uid 0\nsif /bin/phantom gid 0\nquit\n' "$PHANTOM" \
  | debugfs -w "$OUT" >/dev/null 2>&1
echo -n "baked /bin/phantom -> "; debugfs -R "stat /bin/phantom" "$OUT" 2>/dev/null | grep -iE 'Mode:|Size: [0-9]' | tr '\n' ' '; echo
e2fsck -fn "$OUT" >/dev/null 2>&1 && echo "OK: $OUT (fsck clean)"
