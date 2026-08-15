#!/bin/bash
set -euo pipefail

REPO="${BRAINBOX_REPO:-$HOME/brainarbeit}"
[ -d "$REPO" ] || { echo "repo not found at $REPO"; exit 1; }

echo "== rust toolchain (aarch64) =="
if ! command -v cargo >/dev/null 2>&1; then
  curl -fsSL https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
  . "$HOME/.cargo/env"
fi
. "$HOME/.cargo/env" 2>/dev/null || true

echo "== 1) pn-init (PID1) — portable C, plain make =="
PN_INIT_DIR="$(dirname "$(find "$REPO" -name 'pn-init.c' -o -name 'pn_init.c' 2>/dev/null | head -1)")"
if [ -n "${PN_INIT_DIR:-}" ] && [ -d "$PN_INIT_DIR" ]; then
  ( cd "$PN_INIT_DIR" && make ) && echo "  pn-init built in $PN_INIT_DIR"
  BIN="$(find "$PN_INIT_DIR" -maxdepth 1 -name 'pn-init' -type f | head -1)"
  [ -n "$BIN" ] && sudo install -m0755 "$BIN" /sbin/pn-init && echo "  installed /sbin/pn-init"
else
  echo "  (pn-init source not found — locate + build manually; needed as PID1)"
fi

echo "== 2) phantom core (std-only Rust; build ONLY the seat bin) =="
PHANTOM_DIR="$(dirname "$(find "$REPO" -path '*/phantom/Cargo.toml' 2>/dev/null | head -1)")"
if [ -n "${PHANTOM_DIR:-}" ] && [ -d "$PHANTOM_DIR" ]; then
  ( cd "$PHANTOM_DIR" && cargo build --release --bin phantom )
  SEAT="$PHANTOM_DIR/target/release/phantom"
  [ -x "$SEAT" ] || { echo "FATAL: phantom seat binary missing after cargo build ($SEAT)"; exit 1; }
  echo "  phantom seat built: $SEAT"
else
  echo "  (phantom seat crate */phantom/Cargo.toml not found — portal seat/cast will be unavailable)"
fi

echo "== 3) phantom-wasm (wasm32-unknown-unknown; wasm-bindgen-cli PINNED 0.2.126) =="
WASM_DIR="$(dirname "$(find "$REPO" -path '*phantom-wasm*/Cargo.toml' 2>/dev/null | head -1)")"
if [ -n "${WASM_DIR:-}" ] && [ -d "$WASM_DIR" ]; then
  rustup target add wasm32-unknown-unknown
  command -v wasm-bindgen >/dev/null 2>&1 || cargo install wasm-bindgen-cli --version 0.2.126
  ( cd "$WASM_DIR" && cargo build --release --target wasm32-unknown-unknown ) && echo "  phantom-wasm built"
else
  echo "  (phantom-wasm not found — the prebuilt .wasm in the repo is arch-neutral; can ship as-is)"
fi

echo "== 4) stamp capability: cells OFF on this arch (no x86 pn-vmm) =="
sudo mkdir -p /etc/brainbox
echo "CELLS_ENABLED=0" | sudo tee /etc/brainbox/caps.env >/dev/null
echo "  wrote /etc/brainbox/caps.env (CELLS_ENABLED=0)"

echo "== build-arm64 done (cells DISABLED for v1; portal/voice/LLM/cast are interpreted, no build) =="
