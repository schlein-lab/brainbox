#!/usr/bin/env bash
set -euo pipefail
REPO="$HOME/portioneer"; USERD="$HOME/.config/systemd/user"
log(){ printf '\033[36m[llmd]\033[0m %s\n' "$*"; }
. "$(dirname "$(readlink -f "$0")")/lib-service.sh"

chmod +x "$REPO/tools/pn-llmd"
mkdir -p "$USERD"
svc_announce

PN_LLM_POOL="${PN_LLM_POOL:-2}"
PN_LLM_MODEL="${PN_LLM_MODEL:-sonnet}"
PN_LLM_CMD="${PN_LLM_CMD:-claude -p --model {model}}"

case "$(svc_mgr)" in

pninit)
  svc_declare pn-llmd \
    "sacred user=$(id -u) env=PN_LLM_POOL=$PN_LLM_POOL env=PN_LLM_MODEL=$PN_LLM_MODEL env=PN_LLM_CMD=$PN_LLM_CMD" \
    "$REPO/tools/pn-llmd"
  ;;

systemd)
  cp "$REPO/systemd/pn-llmd.service" "$USERD/pn-llmd.service"
  if svc_dry_run; then
    svc_dry "systemctl --user daemon-reload; systemctl --user enable --now pn-llmd.service"
  else
    systemctl --user daemon-reload
    systemctl --user enable --now pn-llmd.service
  fi
  ;;

none)
  svc_skip "pn-llmd wird nicht als Dienst eingerichtet — keine Dienstverwaltung erkannt."
  svc_skip "  Vordergrund-Start zum Pruefen: $REPO/tools/pn-llmd"
  ;;
esac

if svc_dry_run || [ "$(svc_mgr)" = "none" ]; then
  exit 0
fi
sleep 1
if svc_active pn-llmd; then
  log "pn-llmd active:"; "$HOME/.local/bin/pn" llm --status 2>/dev/null || python3 - <<'PY'
import os,socket
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
s.connect(os.path.join(os.environ.get("XDG_RUNTIME_DIR","/run/user/1000"),"pn-llmd.sock"))
s.sendall(b'{"verb":"lstatus"}\n'); print(s.recv(4096).decode())
PY
else
  log "WARN: pn-llmd not active"
  svc_diag pn-llmd
fi
