#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HERE/pn-factory"

if ! command -v go >/dev/null 2>&1 && [ ! -x "$BIN" ]; then
  echo "go toolchain not found and no prebuilt $BIN — install Go or ship the binary." >&2
  exit 1
fi
if [ ! -x "$BIN" ] || [ "${REBUILD:-0}" = "1" ]; then
  echo "building pn-factory ..." >&2
  ( cd "$HERE" && CGO_ENABLED=0 go build -ldflags '-s -w' -o "$BIN" ./cmd/pn-factory )
fi

exec "$BIN" "${@:-detect}"
