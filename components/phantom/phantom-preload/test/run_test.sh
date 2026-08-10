#!/bin/sh
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
crate=$(CDPATH= cd -- "$here/.." && pwd)
lib="$crate/target/release/libphantom_preload.so"
stub_src="$here/stub_bus.rs"
stub_bin="${TMPDIR:-/tmp}/phantom_stub_bus"
sock="/run/phantom/preload.sock"

red()   { printf '\033[31m%s\033[0m\n' "$1"; }
green() { printf '\033[32m%s\033[0m\n' "$1"; }

if [ ! -f "$lib" ]; then
    echo "building libphantom_preload.so ..."
    ( cd "$crate" && "${CARGO:-$(command -v cargo || echo "$HOME/.cargo/bin/cargo")}" build --release )
fi
[ -f "$lib" ] || { red "FAIL: library not built: $lib"; exit 1; }
echo "library: $lib"

"${RUSTC:-$(command -v rustc || echo "$HOME/.cargo/bin/rustc")}" -O "$stub_src" -o "$stub_bin"

echo
echo "== TEST: fail-open (no bus socket present) =="
[ -S "$sock" ] && rm -f "$sock" 2>/dev/null || true
out=$(LD_PRELOAD="$lib" /bin/echo "hello-from-host" 2>/dev/null || true)
if [ "$out" = "hello-from-host" ]; then
    green "PASS: host ran normally with no bus (output unchanged, no crash)"
else
    red "FAIL: host output changed under preload with no bus: '$out'"
    exit 1
fi

echo
echo "== TEST: announce on the bus =="
if ! mkdir -p /run/phantom 2>/dev/null; then
    echo "SKIP: cannot create /run/phantom (need root); fail-open already verified."
    echo "      To run this part: sudo mkdir -p /run/phantom && chown \$USER /run/phantom"
    exit 0
fi
if ! ( : > "$sock.wtest" ) 2>/dev/null; then
    echo "SKIP: /run/phantom not writable; fail-open already verified."
    exit 0
fi
rm -f "$sock.wtest"

cap="${TMPDIR:-/tmp}/phantom_announce.out"
: > "$cap"
"$stub_bin" "$sock" --once > "$cap" 2>/tmp/phantom_stub_bus.err &
stub_pid=$!

i=0
while [ ! -S "$sock" ] && [ "$i" -lt 40 ]; do i=$((i+1)); sleep 0.05; done

LD_PRELOAD="$lib" /bin/echo "announce-me" >/dev/null 2>&1 || true

i=0
while kill -0 "$stub_pid" 2>/dev/null && [ "$i" -lt 40 ]; do i=$((i+1)); sleep 0.05; done
kill "$stub_pid" 2>/dev/null || true
wait "$stub_pid" 2>/dev/null || true

echo "bus received:"
sed 's/^/  /' "$cap"
if grep -q '"src":"preload"' "$cap" && grep -q '"comm":"echo"' "$cap"; then
    green "PASS: process announced itself on the bus ({pid,ppid,comm,exe})"
else
    red "FAIL: no valid announce line captured"
    exit 1
fi

echo
green "all tests passed"
