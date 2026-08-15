#!/bin/bash
set -euo pipefail

APP_HOME="${HOME}"
VENV_ROOT="$APP_HOME/.local/share"

echo "== 1/5 apt packages =="
sudo apt-get update -y
sudo apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  ffmpeg xvfb x11vnc openssl \
  tmux espeak-ng e2fsprogs \
  sshpass git curl xdotool scrot avahi-daemon avahi-utils libnss-mdns qrencode \
  build-essential make pkg-config libssl-dev \
  nodejs npm

echo "== 1b) claude CLI (the LLM brain backend for pn-llmd; BYO auth via the setup wizard) =="
curl -fsSL https://claude.ai/install.sh | bash || \
  echo "  (claude install failed — re-run, or: npm i -g @anthropic-ai/claude-code; brain needs it)"

echo "== 2/5 system pip (imported directly by the portal) =="
sudo pip3 install --break-system-packages --upgrade --ignore-installed \
  segno pillow numpy cryptography pyyaml requests pyjwt websockets

echo "== 3/5 venv: cast (celltv) =="
python3 -m venv "$VENV_ROOT/celltv-venv"
"$VENV_ROOT/celltv-venv/bin/pip" install --upgrade pip
"$VENV_ROOT/celltv-venv/bin/pip" install PyChromecast zeroconf casttube ifaddr protobuf requests

echo "== 4/5 venv: voice STT/TTS =="
python3 -m venv "$VENV_ROOT/voiced-venv"
"$VENV_ROOT/voiced-venv/bin/pip" install --upgrade pip
"$VENV_ROOT/voiced-venv/bin/pip" install \
  faster-whisper ctranslate2 onnxruntime av piper-tts huggingface_hub tokenizers numpy typer rich || {
    echo "  (some voice wheels may need piwheels: add --extra-index-url https://www.piwheels.org/simple)"; }
WYO_VENV="$APP_HOME/wyoming-venv"; WYO_DATA="$APP_HOME/wyoming-data"
mkdir -p "$WYO_DATA"
python3 -m venv "$WYO_VENV"
"$WYO_VENV/bin/pip" install --upgrade pip
"$WYO_VENV/bin/pip" install wyoming-faster-whisper wyoming-piper || \
  echo "  (wyoming voice servers install failed — STT/TTS lane unavailable until re-run; non-fatal)"

echo "== 5/5 models (small footprint; NOT bundled in git) =="
MODELS="$APP_HOME/.local/share/brainbox-portal"
mkdir -p "$MODELS/piper"
"$VENV_ROOT/voiced-venv/bin/python" - <<'PY' || echo "  (whisper model fetch skipped — fetch on first use)"
try:
    from huggingface_hub import snapshot_download
    snapshot_download("Systran/faster-whisper-small")
    print("  faster-whisper-small ready")
except Exception as e:
    print("  whisper model:", e)
PY
curl -fsSL -o "$MODELS/piper/de_DE-thorsten-medium.onnx" \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx 2>/dev/null || \
  echo "  (piper voice fetch skipped — provide de_DE-thorsten-medium.onnx manually)"
curl -fsSL -o "$MODELS/piper/de_DE-thorsten-medium.onnx.json" \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx.json 2>/dev/null || true

echo "== deps done =="
