
[ -n "${_PN_LIB_SERVICE_SOURCED:-}" ] && return 0
_PN_LIB_SERVICE_SOURCED=1

PN_ETC="${PN_ETC:-/etc}"
PN_INIT_CONF="${PN_INIT_CONF:-$PN_ETC/pn-init.conf}"
PN_DRY_RUN="${PN_DRY_RUN:-0}"
PN_CONF_MAX_SVC="${PN_CONF_MAX_SVC:-40}"

PNCTL="${PNCTL:-$(command -v pnctl 2>/dev/null || echo /usr/local/bin/pnctl)}"

svc_log(){ printf '\033[36m[svc]\033[0m %s\n' "$*"; }
svc_warn(){ printf '\033[33m[svc]\033[0m %s\n' "$*"; }
svc_skip(){ printf '\033[33m[svc] UEBERSPRUNGEN\033[0m %s\n' "$*"; }
svc_dry(){ printf '\033[35m[svc] TROCKEN\033[0m %s\n' "$*"; }

svc_dry_run(){ [ "$PN_DRY_RUN" = "1" ]; }

_svc_detect(){
  case "${PN_SVCMGR:-}" in
    pninit|systemd|none) echo "$PN_SVCMGR"; return 0 ;;
  esac
  local c1=""
  c1="$(cat /proc/1/comm 2>/dev/null || true)"
  case "$c1" in
    pn-init*) echo pninit; return 0 ;;
  esac
  if [ -f "$PN_INIT_CONF" ] && command -v pnctl >/dev/null 2>&1; then echo pninit; return 0; fi
  command -v systemctl >/dev/null 2>&1 || { echo none; return 0; }
  local err
  err="$(systemctl --user is-system-running 2>&1 >/dev/null || true)"
  case "$err" in
    *"Failed to connect to bus"*|*"No medium found"*) echo none; return 0 ;;
  esac
  echo systemd
}

svc_mgr(){
  [ -n "${PN_SVCMGR_DETECTED:-}" ] || PN_SVCMGR_DETECTED="$(_svc_detect)"
  echo "$PN_SVCMGR_DETECTED"
}

svc_announce(){
  local m; m="$(svc_mgr)"
  case "$m" in
    pninit)  svc_log "Dienstverwaltung: pn-init (PID 1) — Dienste stehen in $PN_INIT_CONF, gesetzt mit pnctl" ;;
    systemd) svc_log "Dienstverwaltung: systemd (User-Bus erreichbar) — Units unter \$HOME/.config/systemd/user" ;;
    none)    svc_warn "Dienstverwaltung: KEINE erkannt (kein pn-init als PID 1, kein erreichbarer systemd-User-Bus)." ;;
  esac
  svc_dry_run && svc_dry "PN_DRY_RUN=1 — es wird NICHTS veraendert, nur gezeigt."
  [ "$PN_ETC" != "/etc" ] && svc_log "PN_ETC=$PN_ETC (Wegwerf-Ziel, nicht die laufende Anlage)"
  [ "$PN_INIT_CONF" != "$PN_ETC/pn-init.conf" ] && svc_log "PN_INIT_CONF=$PN_INIT_CONF"
  return 0
}

svc_needs_root(){ [ "$PN_ETC" = "/etc" ]; }

svc_priv(){
  if ! svc_needs_root; then "$@"; return $?; fi
  if [ "$(id -u)" = "0" ]; then "$@"; return $?; fi
  if [ -n "${SUDO_PASSWORD:-}" ]; then echo "${SUDO_PASSWORD}" | sudo -S "$@"; return $?; fi
  sudo -n "$@" 2>/dev/null || { svc_warn "root noetig fuer: $*  (SUDO_PASSWORD in \$HOME/.env setzen)"; return 1; }
}

pninit_names(){
  [ -f "$PN_INIT_CONF" ] || return 0
  grep -vE '^[[:space:]]*(#|$)' "$PN_INIT_CONF" 2>/dev/null | cut -d'|' -f1 | sed 's/[[:space:]]*$//'
  return 0
}
pninit_has(){ pninit_names | grep -qx "$1"; }
pninit_count(){ local n; n="$(pninit_names | grep -c . 2>/dev/null)" || n=0; echo "${n:-0}"; }

pninit_line(){
  local name="$1" flags="$2"; shift 2
  printf '%s|%s|%s\n' "$name" "$flags" "$*"
}

pninit_get(){
  [ -f "$PN_INIT_CONF" ] || return 0
  grep -vE '^[[:space:]]*(#|$)' "$PN_INIT_CONF" 2>/dev/null \
    | awk -F'|' -v n="$1" '{ nm=$1; gsub(/^[ \t]+|[ \t]+$/,"",nm); if (nm==n) { print; exit } }'
  return 0
}

svc_declare(){
  local name="$1" flags="$2"; shift 2
  local line; line="$(pninit_line "$name" "$flags" "$@")"
  case "$(svc_mgr)" in
    pninit) : ;;
    *) return 10 ;;
  esac
  local cur; cur="$(pninit_get "$name")"
  if [ -n "$cur" ]; then
    if [ "$cur" = "$line" ]; then
      svc_log "pn-init: $name steht bereits genau so — nichts zu tun"
      return 0
    fi
    if [ "${PN_SVC_FORCE:-0}" != "1" ]; then
      svc_warn "$name ist bereits konfiguriert und wird NICHT ueberschrieben (additiver Installer)."
      svc_warn "  IST : $cur"
      svc_warn "  SOLL: $line"
      svc_warn "  Uebernehmen — nur wenn der Unterschied oben wirklich gewollt ist:"
      svc_warn "    PN_SVC_FORCE=1 bash \$HOME/portioneer/install/install.sh"
      svc_warn "  (oder gezielt:  sudo pnctl add $name '$flags' $*)"
      return 0
    fi
    svc_warn "PN_SVC_FORCE=1 — $name wird ERSETZT:"
    svc_warn "  IST : $cur"
    svc_warn "  SOLL: $line"
  fi
  if ! pninit_has "$name"; then
    local n; n="$(pninit_count)"
    if [ "${n:-0}" -ge "$PN_CONF_MAX_SVC" ]; then
      svc_warn "VERWEIGERT: $PN_INIT_CONF hat bereits $n Dienste; pn-init liest hoechstens"
      svc_warn "            CONF_MAX_SVC=$PN_CONF_MAX_SVC (os/init/pn-init.c) und IGNORIERT jeden weiteren"
      svc_warn "            beim Booten.  Erst einen Dienst entfernen oder CONF_MAX_SVC anheben + PID1 neu bauen."
      svc_warn "            NICHT aufgenommen: $line"
      return 1
    fi
  fi
  if svc_dry_run; then
    svc_dry "pnctl add $name '$flags' $*"
    svc_dry "  Zeile: $line"
    return 0
  fi
  svc_log "pn-init: $line"
  local out rc=0
  out="$(svc_priv env PN_INIT_CONF="$PN_INIT_CONF" "$PNCTL" add "$name" "$flags" "$@" 2>&1)" || rc=$?
  [ -n "$out" ] && printf '%s\n' "$out" | sed 's/^/    /'
  [ "$rc" = "0" ] && return 0
  if printf '%s' "$out" | grep -qE '^(aufgenommen|ersetzt):'; then
    svc_warn "$name steht in $PN_INIT_CONF, aber PID 1 hat noch nicht neu eingelesen."
    svc_warn "  Nachholen (root):  sudo pnctl reload    — bis dahin laeuft $name NICHT."
    return 0
  fi
  svc_warn "pnctl add $name fehlgeschlagen (rc=$rc) — $name ist NICHT konfiguriert."
  return 1
}

svc_undeclare(){
  local name="$1"
  case "$(svc_mgr)" in pninit) : ;; *) return 10 ;; esac
  pninit_has "$name" || { svc_log "pn-init: $name war nicht konfiguriert"; return 0; }
  if svc_dry_run; then svc_dry "pnctl rm $name"; return 0; fi
  local out rc=0
  out="$(svc_priv env PN_INIT_CONF="$PN_INIT_CONF" "$PNCTL" rm "$name" 2>&1)" || rc=$?
  [ -n "$out" ] && printf '%s\n' "$out" | sed 's/^/    /'
  [ "$rc" = "0" ] && return 0
  if printf '%s' "$out" | grep -q '^entfernt:'; then
    svc_warn "$name ist aus $PN_INIT_CONF raus, aber PID 1 hat noch nicht neu eingelesen."
    svc_warn "  Nachholen (root):  sudo pnctl reload    — bis dahin LAEUFT $name weiter."
    return 0
  fi
  svc_warn "pnctl rm $name fehlgeschlagen (rc=$rc)."
  return 1
}

svc_restart(){
  local name="$1"
  if svc_dry_run; then svc_dry "Neustart $name (unterdrueckt)"; return 0; fi
  case "$(svc_mgr)" in
    pninit)  svc_priv env PN_INIT_CONF="$PN_INIT_CONF" "$PNCTL" restart "$name" ;;
    systemd) systemctl --user restart "$name.service" ;;
    none)    svc_skip "Neustart $name — keine Dienstverwaltung" ;;
  esac
}

svc_active(){
  local name="$1"
  case "$(svc_mgr)" in
    pninit)  env PN_INIT_CONF="$PN_INIT_CONF" "$PNCTL" pid "$name" >/dev/null 2>&1 ;;
    systemd) systemctl --user is-active --quiet "$name.service" ;;
    none)    return 1 ;;
  esac
}

svc_diag(){
  local name="$1"
  case "$(svc_mgr)" in
    pninit)  env PN_INIT_CONF="$PN_INIT_CONF" "$PNCTL" status "$name" 2>&1 || true ;;
    systemd) systemctl --user status "$name.service" --no-pager -n 10 2>&1 || true ;;
  esac
}

svc_envfile_set(){
  local f="$1" k="$2" v="$3"
  if [ -f "$f" ] && grep -qE "^[[:space:]]*${k}=" "$f" 2>/dev/null; then
    svc_log "  $f: $k bleibt (Betreiberwert: $(sed -n "s/^[[:space:]]*${k}=//p" "$f" | head -1))"
    return 0
  fi
  if svc_dry_run; then svc_dry "  $f += $k=$v"; return 0; fi
  if svc_priv sh -c "mkdir -p '$(dirname "$f")'; printf '%s=%s\n' '$k' '$v' >> '$f'"; then
    svc_log "  $f += $k=$v"
  else
    svc_warn "  $f: $k=$v konnte nicht geschrieben werden (root noetig?)"
  fi
  return 0
}
