#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-/tmp/pn-init.cpio.gz}"
[ -x "$HERE/pn-init" ] || { echo "build pn-init first: make"; exit 1; }
BB="$(command -v busybox)"; [ -x "$BB" ] || { echo "busybox not found (apt install busybox-static)"; exit 1; }

R="$(mktemp -d)"
mkdir -p "$R"/{bin,proc,sys,dev,run}
cp "$HERE/pn-init" "$R/init";  chmod 0755 "$R/init"
cp "$BB" "$R/bin/busybox";     chmod 0755 "$R/bin/busybox"
ln -sf busybox "$R/bin/sh"
[ -x "$HERE/pndstub" ] || { echo "build pndstub first: make"; exit 1; }
cp "$HERE/pndstub" "$R/bin/pndstub"; chmod 0755 "$R/bin/pndstub"
[ -x "$HERE/memhog" ] || { echo "build memhog first: make"; exit 1; }
cp "$HERE/memhog" "$R/bin/memhog"; chmod 0755 "$R/bin/memhog"

WDZ="/lib/modules/$(uname -r)/kernel/drivers/watchdog/i6300esb.ko.zst"
if [ -f "$WDZ" ] && command -v zstd >/dev/null; then
  zstd -dqf "$WDZ" -o "$R/i6300esb.ko" && echo "  + i6300esb.ko (decompressed for real HW watchdog)"
elif [ -f "${WDZ%.zst}" ]; then
  cp "${WDZ%.zst}" "$R/i6300esb.ko" && echo "  + i6300esb.ko"
else
  echo "  ! i6300esb module unavailable — pn-init will use software-reset fallback"
fi

( cd "$R" && find . -print0 | cpio --null -o -H newc 2>/dev/null | gzip -9 > "$OUT" )
rm -rf "$R"
echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"
