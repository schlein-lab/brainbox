#!/usr/bin/env bash
set -euo pipefail

PHANTOM_REPO="${PHANTOM_REPO:-https://github.com/schlein-lab/phantom}"
ZYRKEL_REPO="${ZYRKEL_REPO:-https://github.com/schlein-lab/zyrkel}"
REF="${REF:-main}"
SRC_DIR="${SRC_DIR:-$HOME/.local/src}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"

LANG_CHOICE=""; WITH_PORTAL=""; WITH_VOICE=""; WITH_SEAT=""
ASSUME_YES=""; FROM_SOURCE=""; NO_PORTAL=""

if [ -t 1 ]; then
  B=$'\e[1m'; DIM=$'\e[2m'; R=$'\e[0m'; GRN=$'\e[32m'; YEL=$'\e[33m'; RED=$'\e[31m'; PUR=$'\e[38;5;99m'
else
  B=""; DIM=""; R=""; GRN=""; YEL=""; RED=""; PUR=""
fi
step(){ printf '\n%s==>%s %s%s%s\n' "$PUR" "$R" "$B" "$*" "$R"; }
ok(){   printf '  %s[ok]%s %s\n' "$GRN" "$R" "$*"; }
warn(){ printf '  %s[!]%s  %s\n' "$YEL" "$R" "$*"; }
die(){  printf '\n%sFEHLER/ERROR:%s %s\n' "$RED" "$R" "$*" >&2; exit 1; }
have(){ command -v "$1" >/dev/null 2>&1; }
de(){ [ "$LANG_CHOICE" = "de" ]; }
interactive(){ [ -z "$ASSUME_YES" ] && { : >/dev/tty; } 2>/dev/null; }
ask(){ interactive || return 1; printf '%s [y/N] ' "$1" >/dev/tty; local a=""; read -r a </dev/tty || a=""; case "$a" in [yYjJ]*) return 0;; *) return 1;; esac; }

usage(){ cat <<'USAGE'
phantom + zyrkel installer

  bash install.sh [flags]            run locally
  curl -fsSL https://phantomlinux.com/install.sh | bash

Flags:
  --lang en|de        UI language (default: from $LANG/$LC_ALL, else English)
  --with-portal       set up + enable the LAN web portal as a service
  --with-voice        add Whisper STT + Piper TTS to the portal (implies portal)
  --with-seat         install the phantGNOME desktop session (GNOME, advanced)
  --no-portal         never offer the portal
  --from-source       build phantom/zyrkel from source instead of prebuilt
  --ref <branch|tag>  which ref of the repos to use (default: main)
  -y, --yes           non-interactive: accept the alpha/safety notice + defaults
  -h, --help          this help

ALPHA software. Use a disposable VM, keep no sensitive data on it. NO WARRANTY.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --lang)        [ $# -ge 2 ] || die "value missing for --lang"; LANG_CHOICE="$2"; shift 2;;
    --lang=*)      LANG_CHOICE="${1#*=}"; shift;;
    --with-portal) WITH_PORTAL=1; shift;;
    --with-voice)  WITH_VOICE=1; WITH_PORTAL=1; shift;;
    --with-seat)   WITH_SEAT=1; shift;;
    --no-portal)   NO_PORTAL=1; shift;;
    --from-source) FROM_SOURCE=1; shift;;
    --ref)         [ $# -ge 2 ] || die "value missing for --ref"; REF="$2"; shift 2;;
    --ref=*)       REF="${1#*=}"; shift;;
    -y|--yes)      ASSUME_YES=1; shift;;
    -h|--help)     usage; exit 0;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2;;
  esac
done

if [ -z "$LANG_CHOICE" ]; then
  _loc="${LC_ALL:-${LANG:-}}"
  case "$_loc" in de_*|de) LANG_CHOICE="de";; *) LANG_CHOICE="en";; esac
  if [ -z "$ASSUME_YES" ] && { : >/dev/tty; } 2>/dev/null; then
    printf '\n  Language / Sprache?  [1] English  [2] Deutsch  (default: %s) ' "$LANG_CHOICE" >/dev/tty 2>/dev/null
    read -r _l </dev/tty 2>/dev/null || _l=""
    case "$_l" in 1) LANG_CHOICE="en";; 2) LANG_CHOICE="de";; esac
  fi
fi
[ "$LANG_CHOICE" = "de" ] || [ "$LANG_CHOICE" = "en" ] || LANG_CHOICE="en"

cat <<BANNER

${PUR}${B}   phantom${R} ${DIM}+${R} ${PUR}${B}zyrkel${R}  -  an LLM-operated Linux  ${DIM}(alpha)${R}
   ${DIM}headless Wayland . the hub . agent rooms . fusion . the LAN portal${R}

BANNER

if de; then cat <<'WARN_DE'
  ------------------------------------------------------------------------
   WICHTIG - bitte lesen (ALPHA-Software, experimentell)
  ------------------------------------------------------------------------
   Dieses Paket macht deinen Rechner von einem Sprachmodell VOLL bedienbar:
   Tastatur, Fenster, Shell, Programme - und (optional) per Sprache und uebers
   LAN. Das ist maechtig und experimentell. Deshalb dringend:

     * Nimm eine EIGENE, FRISCHE VM nur dafuer (Sandbox). Nicht deinen
       Arbeitsrechner.
     * Lege KEINE wichtigen, privaten oder SENSIBLEN/KRITISCHEN Daten auf
       diese VM. Behandle sie als wegwerfbar.
     * Es kann Dinge VERAENDERN oder KAPUTT machen - ein Agent fuehrt echte
       Befehle aus. Snapshots der VM helfen.
     * Es laedt + fuehrt vorgebaute Binaries und Fremd-Installer (rustup,
       Claude-CLI) aus dem Netz aus (Binaries werden gegen SHA256SUMS geprueft).
     * Das optionale Portal gibt JEDEM in deinem LAN (PIN-geschuetzt) volle
       Kontrolle ueber die Maschine. Nur in vertrauenswuerdigen Netzen, mit PIN.
     * KEINE GEWAEHR. Nutzung auf eigenes Risiko (MIT-Lizenz).
  ------------------------------------------------------------------------
WARN_DE
else cat <<'WARN_EN'
  ------------------------------------------------------------------------
   IMPORTANT - please read (ALPHA software, experimental)
  ------------------------------------------------------------------------
   This package makes your machine FULLY operable by a language model:
   keyboard, windows, shell, programs - and (optionally) by voice and over
   your LAN. It is powerful and experimental. So, strongly:

     * Use a DEDICATED, FRESH VM just for this (a sandbox). Not your work
       machine.
     * Keep NO important, private or SENSITIVE/CRITICAL data on this VM. Treat
       it as disposable.
     * It can CHANGE or BREAK things - an agent runs real commands. VM
       snapshots are your friend.
     * It downloads + runs prebuilt binaries and third-party installers
       (rustup, the Claude CLI) from the network (binaries are checked against
       SHA256SUMS).
     * The optional portal gives ANYONE on your LAN (PIN-gated) full control
       of the machine. Trusted networks only, keep the PIN.
     * NO WARRANTY. Use at your own risk (MIT license).
  ------------------------------------------------------------------------
WARN_EN
fi

if de; then PROMPT="Verstanden - auf einer Wegwerf-VM fortfahren?"; else PROMPT="Understood - continue on a disposable VM?"; fi
if [ -n "$ASSUME_YES" ]; then
  echo "  $(de && echo '--yes: Alpha-/Sicherheitshinweis automatisch akzeptiert.' || echo '--yes: alpha/safety notice auto-accepted.')"
elif { : >/dev/tty; } 2>/dev/null; then
  printf '\n%s [y/N] ' "$PROMPT" >/dev/tty 2>/dev/null
  read -r _ans </dev/tty 2>/dev/null || _ans=""
  case "$_ans" in [yYjJ]*) :;; *) de && echo "Abgebrochen." || echo "Aborted."; exit 0;; esac
else
  die "$(de && echo 'Kein Terminal fuer die Zustimmung. Mit --yes erneut starten, um den Alpha-/Sicherheitshinweis zu akzeptieren.' || echo 'No terminal for consent. Re-run with --yes to accept the alpha/safety notice.')"
fi

step "$(de && echo 'System pruefen' || echo 'Checking system')"
[ "$(uname -s)" = "Linux" ] || die "$(de && echo 'Nur Linux wird unterstuetzt.' || echo 'Linux only.')"
have apt-get || warn "$(de && echo 'Kein apt gefunden - getestet ist Ubuntu/Debian.' || echo 'No apt found - tested on Ubuntu/Debian.')"
MACH="$(uname -m)"
case "$MACH" in
  x86_64|amd64) ARCH_SUFFIX="x86_64-linux";;
  aarch64|arm64) ARCH_SUFFIX="aarch64-linux";;
  *) ARCH_SUFFIX=""; FROM_SOURCE=1
     if de; then warn "Unbekannte Architektur $MACH - baue aus dem Quellcode."; else warn "Unknown arch $MACH - will build from source."; fi;;
esac
mkdir -p "$BIN_DIR" "$SRC_DIR"
ok "$(uname -m) / $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-Linux}")"

step "$(de && echo 'System-Pakete' || echo 'System packages')"
APT_PKGS="git curl ca-certificates python3 python3-venv python3-pip tmux openssl iproute2"
[ -n "$WITH_VOICE" ] && APT_PKGS="$APT_PKGS ffmpeg"
[ -n "$WITH_SEAT" ]  && APT_PKGS="$APT_PKGS foot"
apt_install(){
  have apt-get || return 0
  local miss="" p
  for p in "$@"; do dpkg -s "$p" >/dev/null 2>&1 || miss="$miss $p"; done
  [ -n "${miss# }" ] || return 0
  echo "  $(de && echo 'installiere:' || echo 'installing:')$miss"
  local SUDO=""; [ "$(id -u)" -eq 0 ] || SUDO="sudo"
  $SUDO apt-get update -qq
  $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y -q $miss
}
apt_install $APT_PKGS
ok "$(de && echo 'Pakete bereit' || echo 'packages ready')"

clone_or_update(){
  if [ -d "$2/.git" ]; then
    git -C "$2" fetch --depth 1 origin "$REF" -q 2>/dev/null && git -C "$2" checkout -q FETCH_HEAD 2>/dev/null || true
  else
    git clone --depth 1 --branch "$REF" "$1" "$2" -q 2>/dev/null \
      || git clone --depth 1 "$1" "$2" -q 2>/dev/null \
      || die "$(de && echo "Repo nicht erreichbar: $1 (privat? Netz?)" || echo "cannot reach repo: $1 (private? network?)")"
  fi
}
step "$(de && echo 'Quellen holen' || echo 'Fetching sources')"
SELF_DIR=""
[ -f "$0" ] && SELF_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)" || true
if [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/Cargo.toml" ] && grep -q 'name = "phantom"' "$SELF_DIR/Cargo.toml" 2>/dev/null; then
  PH_SRC="$SELF_DIR"; ok "phantom: $(de && echo 'lokales Repo' || echo 'local checkout') $PH_SRC"
else
  PH_SRC="$SRC_DIR/phantom"; clone_or_update "$PHANTOM_REPO" "$PH_SRC"; ok "phantom -> $PH_SRC"
fi
ZY_SRC="$SRC_DIR/zyrkel"; clone_or_update "$ZYRKEL_REPO" "$ZY_SRC"; ok "zyrkel -> $ZY_SRC"

ensure_toolchain(){
  apt_install build-essential pkg-config
  have cargo && return 0
  [ -x "$HOME/.cargo/bin/cargo" ] && { export PATH="$HOME/.cargo/bin:$PATH"; return 0; }
  step "$(de && echo 'Rust installieren (fuer den Quellcode-Build)' || echo 'Installing Rust (for the source build)')"
  curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal || die "rustup"
  export PATH="$HOME/.cargo/bin:$PATH"
  have cargo || die "cargo"
}

ref_taglike(){ case "$REF" in */*) return 1;; *) return 0;; esac; }
rel_path(){ [ "$REF" = main ] && echo "latest/download" || echo "download/$REF"; }

verify_sha(){
  local sums want got
  sums="$(curl -fsSL "$2/releases/$(rel_path)/SHA256SUMS" 2>/dev/null)" || sums=""
  [ -n "$sums" ] || { warn "$(de && echo 'keine SHA256SUMS - Binary ungeprueft' || echo 'no SHA256SUMS - binary unverified')"; return 0; }
  want="$(printf '%s\n' "$sums" | awk -v a="$3" '$2==a||$2=="*"a{print $1; exit}')"
  [ -n "$want" ] || { warn "$(de && echo 'Asset nicht in SHA256SUMS - ungeprueft' || echo 'asset not in SHA256SUMS - unverified')"; return 0; }
  got="$(sha256sum "$1" | awk '{print $1}')"
  [ "$want" = "$got" ]
}

fetch_or_build(){
  local name="$1" asset="$2" src="$3" cbin="$4" dest="$BIN_DIR/$1" base url
  case "$name" in zyrkel) base="$ZYRKEL_REPO";; *) base="$PHANTOM_REPO";; esac
  if [ -z "$FROM_SOURCE" ] && [ -n "$ARCH_SUFFIX" ] && ref_taglike; then
    url="$base/releases/$(rel_path)/$asset-$ARCH_SUFFIX"
    if curl -fsSL "$url" -o "$dest.tmp" 2>/dev/null && [ -s "$dest.tmp" ]; then
      if verify_sha "$dest.tmp" "$base" "$asset-$ARCH_SUFFIX"; then
        chmod +x "$dest.tmp"; mv "$dest.tmp" "$dest"
        ok "$name $(de && echo '(fertiges Binary, geprueft)' || echo '(prebuilt, verified)')"; return 0
      fi
      warn "$(de && echo "$name: Pruefsumme passt nicht - baue aus Quellcode" || echo "$name: checksum mismatch - building from source")"
    fi
    rm -f "$dest.tmp"
  fi
  ensure_toolchain
  if ! ( cd "$src" && cargo build --release --bin "$cbin" -q ); then
    die "$(de && echo "Build von $name fehlgeschlagen. Pruefe build-essential/pkg-config und Netz, dann erneut." || echo "building $name failed. Check build-essential/pkg-config + network, then retry.")"
  fi
  install -m755 "$src/target/release/$cbin" "$dest"
  ok "$name $(de && echo '(aus Quellcode gebaut)' || echo '(built from source)')"
}

step "$(de && echo 'phantom + zyrkel installieren' || echo 'Installing phantom + zyrkel')"
fetch_or_build phantom           phantom           "$PH_SRC" phantom
fetch_or_build phantom-supervise phantom-supervise "$PH_SRC" phantom-supervise
fetch_or_build zyrkel            zyrkel            "$ZY_SRC" zyrkel

step "$(de && echo 'Werkzeuge installieren (rooms, portal, fusion ...)' || echo 'Installing tools (rooms, portal, fusion ...)')"
TOOLS_SRC="$PH_SRC/phantGNOME/bin"
if [ -d "$TOOLS_SRC" ]; then
  for t in phantom-room phantom-serve fusion kartei; do
    [ -f "$TOOLS_SRC/$t" ] && install -m755 "$TOOLS_SRC/$t" "$BIN_DIR/$t" && ok "$t"
  done
else
  warn "phantGNOME/bin $(de && echo 'nicht gefunden - uebersprungen' || echo 'not found - skipped')"
fi

step "$(de && echo 'Claude-CLI' || echo 'Claude CLI')"
if have claude || [ -x "$HOME/.local/bin/claude" ]; then
  ok "$(de && echo 'bereits vorhanden' || echo 'already present')"
elif curl -fsSL https://claude.ai/install.sh | bash >/dev/null 2>&1; then ok "claude"
elif have npm && npm install -g @anthropic-ai/claude-code >/dev/null 2>&1; then ok "claude (npm)"
else warn "$(de && echo 'Claude-CLI nicht automatisch installierbar - siehe https://claude.com/claude-code' || echo 'could not auto-install Claude CLI - see https://claude.com/claude-code')"; fi

step "$(de && echo 'zyrkel-Konfiguration' || echo 'zyrkel config')"
ZY_CFG_DIR="$HOME/.config/zyrkel"; mkdir -p "$ZY_CFG_DIR"
if [ ! -f "$ZY_CFG_DIR/config.json" ] && [ -f "$ZY_SRC/config.example.json" ]; then
  cp "$ZY_SRC/config.example.json" "$ZY_CFG_DIR/config.json"
  ok "$(de && echo "Vorlage kopiert -> $ZY_CFG_DIR/config.json (bitte IDs/Pfade eintragen)" || echo "seeded $ZY_CFG_DIR/config.json (edit ids/paths)")"
else
  ok "$(de && echo 'Konfiguration vorhanden' || echo 'config present')"
fi
if [ ! -f "$HOME/.env" ]; then
  cat > "$HOME/.env" <<'ENVT'
# phantom/zyrkel secrets - fill in what you use, then keep this file private.
# Nothing here is committed anywhere; tools read these at runtime.
# TELEGRAM_BOT_TOKEN=
# OPENAI_API_KEY=
# SLACK_BOT_TOKEN=
ENVT
  ok "$(de && echo "Vorlage ~/.env angelegt" || echo "created ~/.env template")"
fi
chmod 600 "$HOME/.env" 2>/dev/null || true

if [ -z "$WITH_PORTAL" ] && [ -z "$NO_PORTAL" ] && ask "$(de && echo 'Optionales LAN-Portal einrichten (Terminal/Voice/Auftraege im Browser)?' || echo 'Set up the optional LAN portal (browser terminal/voice/commissions)?')"; then
  WITH_PORTAL=1
fi
if [ -n "$WITH_PORTAL" ]; then
  step "$(de && echo 'LAN-Portal' || echo 'LAN portal')"
  if [ -x "$BIN_DIR/phantom-portal" ]; then
    "$BIN_DIR/phantom-portal" setup || warn "portal setup"
    if [ -n "$WITH_VOICE" ] || ask "$(de && echo 'Voice (Whisper+Piper) hinzufuegen? Laedt Modelle, dauert.' || echo 'Add voice (Whisper+Piper)? Downloads models, slow.')"; then
      "$BIN_DIR/phantom-portal" install-voice || warn "install-voice"
    fi
    warn "$(de && echo 'Achtung: Der Dienst macht die VOLLE Maschinensteuerung fuer JEDEN im LAN erreichbar (nur per PIN geschuetzt) - nur in vertrauenswuerdigen Netzen.' || echo 'Note: the service makes FULL machine control reachable by ANYONE on your LAN (PIN-protected only) - trusted networks only.')"
    if [ -n "$ASSUME_YES" ] || ask "$(de && echo 'Portal als Dienst aktivieren (Autostart)?' || echo 'Enable the portal as a service (autostart)?')"; then
      "$BIN_DIR/phantom-portal" install-service || warn "install-service"
    fi
  else
    warn "phantom-portal $(de && echo 'nicht installiert' || echo 'not installed')"
  fi
fi

if [ -n "$WITH_SEAT" ] && [ -f "$PH_SRC/phantGNOME/install-user.sh" ]; then
  step "$(de && echo 'phantGNOME-Sitzung (Desktop, fortgeschritten)' || echo 'phantGNOME session (desktop, advanced)')"
  bash "$PH_SRC/phantGNOME/install-user.sh" || warn "install-user.sh"
  warn "$(de && echo "Fuer den Login-Eintrag einmalig: sudo bash $PH_SRC/phantGNOME/install-system.sh" || echo "For the login entry, once: sudo bash $PH_SRC/phantGNOME/install-system.sh")"
fi

case ":$PATH:" in
  *":$BIN_DIR:"*) :;;
  *) if ! grep -qsF '# phantom-installer PATH' "$HOME/.bashrc" 2>/dev/null; then
       printf '\n# phantom-installer PATH\nexport PATH="%s:$PATH"\n' "$BIN_DIR" >> "$HOME/.bashrc"
       if de; then warn "$BIN_DIR in ~/.bashrc ergaenzt - neue Shell oeffnen"; else warn "appended $BIN_DIR to ~/.bashrc - open a new shell"; fi
     fi;;
esac

LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
[ -n "$LAN_IP" ] || LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
step "$(de && echo 'Fertig' || echo 'Done')"
if de; then cat <<DONE_DE

  Installiert nach ${BIN_DIR}:
    phantom, phantom-supervise, zyrkel, phantom-room, phantom-portal,
    phantom-serve, fusion, kartei

  Naechste Schritte:
    1) Einmal  ${B}claude${R}  starten und anmelden (OAuth, einmalig).
    2) Tokens in  ${B}~/.env${R}  eintragen (z.B. TELEGRAM_BOT_TOKEN, OPENAI_API_KEY).
    3) zyrkel-Konfig pruefen:  ${B}~/.config/zyrkel/config.json${R}
    4) Multi-Agent-Raum starten:  ${B}phantom-room new demo${R}
$( [ -n "$WITH_PORTAL" ] && printf '    5) Portal: %shttps://%s:8077/%s  (PIN siehe Setup-Ausgabe; im ganzen LAN erreichbar - PIN schuetzen)\n' "$B" "${LAN_IP:-<lan-ip>}" "$R" )

  Erinnerung: Alpha-Software. Wegwerf-VM, keine sensiblen Daten, keine Gewaehr.
DONE_DE
else cat <<DONE_EN

  Installed into ${BIN_DIR}:
    phantom, phantom-supervise, zyrkel, phantom-room, phantom-portal,
    phantom-serve, fusion, kartei

  Next steps:
    1) Run  ${B}claude${R}  once and sign in (OAuth, one-time).
    2) Put your tokens in  ${B}~/.env${R}  (e.g. TELEGRAM_BOT_TOKEN, OPENAI_API_KEY).
    3) Review the zyrkel config:  ${B}~/.config/zyrkel/config.json${R}
    4) Start a multi-agent room:  ${B}phantom-room new demo${R}
$( [ -n "$WITH_PORTAL" ] && printf '    5) Portal: %shttps://%s:8077/%s  (PIN shown by setup; reachable across your LAN - protect the PIN)\n' "$B" "${LAN_IP:-<lan-ip>}" "$R" )

  Reminder: alpha software. Disposable VM, no sensitive data, no warranty.
DONE_EN
fi
