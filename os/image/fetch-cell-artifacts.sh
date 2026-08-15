#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
VER="${BBX_VERSION:-$(cat "$HERE/VERSION" 2>/dev/null || echo 1.0.0)}"
BASE_URL="${BBX_ARTIFACT_URL:-https://get.brainarbeit.com/v$VER}"
BUNDLE="brainbox-cell-artifacts-$VER.tar.xz"
DST="$REPO/os/pn-vmm"

PUB=""
for p in "$REPO/minisign.pub" "$REPO/ops/release-public/kit/minisign.pub"; do
  [ -s "$p" ] && PUB="$p" && break
done

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"
echo "== fetch $BASE_URL/$BUNDLE"
curl -fL --retry 3 -o "$BUNDLE" "$BASE_URL/$BUNDLE"
curl -fL --retry 3 -o "$BUNDLE.minisig" "$BASE_URL/$BUNDLE.minisig"

if command -v minisign >/dev/null 2>&1 && [ -n "$PUB" ]; then
  minisign -Vm "$BUNDLE" -p "$PUB"
else
  echo "ABBRUCH: Signatur nicht pruefbar ($([ -n "$PUB" ] && echo 'minisign nicht installiert' || echo 'angehefteter Schluessel nicht gefunden'))" >&2
  echo "Installiere minisign (apt-get install minisign) bzw. hinterlege den Release-Schluessel." >&2
  exit 1
fi

tar -C "$DST" -xJf "$BUNDLE" kernel
RT_DST="${BBX_RT_DST:-$HOME/.local/share/brainarbeit/runtimes}"
if tar -tJf "$BUNDLE" runtimes >/dev/null 2>&1; then
  mkdir -p "$RT_DST"
  tar -C "$(dirname "$RT_DST")" -xJf "$BUNDLE" runtimes
fi
echo "OK: cell artifacts $VER -> $DST/kernel + $RT_DST"
ls -l "$DST/kernel" | sed 's/^/   /'
