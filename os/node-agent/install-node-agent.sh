#!/usr/bin/env bash
set -euo pipefail

PORT="${PN_NODE_AGENT_PORT:-8097}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_DIR="$HOME/.config/pn-node-agent"
TOKEN_FILE="$CONF_DIR/token"
APP_DIR="$HOME/.local/share/pn-node-agent"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/pn-node-agentd.service"

command -v python3 >/dev/null || { echo "FEHLER: python3 fehlt auf diesem Node" >&2; exit 1; }
command -v systemctl >/dev/null || {
  echo "FEHLER: kein systemd — Agent manuell starten: python3 $APP_DIR/pn_node_agentd.py" >&2
  exit 1
}

mkdir -p "$CONF_DIR" && chmod 700 "$CONF_DIR"
if [ ! -s "$TOKEN_FILE" ]; then
  ( umask 077; python3 -c 'import secrets; print(secrets.token_hex(16))' > "$TOKEN_FILE" )
fi
chmod 600 "$TOKEN_FILE"

mkdir -p "$APP_DIR"
install -m 0755 "$SRC_DIR/pn_node_agentd.py" "$APP_DIR/pn_node_agentd.py"

mkdir -p "$UNIT_DIR"
cat > "$UNIT" <<EOF
[Unit]
Description=Brainarbeit Node-Agent (Fleet-Compute-Plane, Port ${PORT})
After=network.target

[Service]
Environment=PN_NODE_AGENT_PORT=${PORT}
ExecStart=/usr/bin/env python3 ${APP_DIR}/pn_node_agentd.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

loginctl enable-linger "$USER" 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now pn-node-agentd.service >/dev/null 2>&1 || {
  systemctl --user restart pn-node-agentd.service
}

LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.*src \([0-9.]*\).*/\1/p' | head -n1)"
[ -n "$LAN_IP" ] || LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$LAN_IP" ] || LAN_IP="<lan-ip>"

echo "Endpoint: http://${LAN_IP}:${PORT}"
echo "Token:    liegt in ${TOKEN_FILE} (0600) — fuer das Pairing: cat ${TOKEN_FILE}"
