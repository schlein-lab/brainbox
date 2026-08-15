#!/bin/sh
set -eu

DATA="${BRAINARBEIT_DATA:-/var/lib/brainarbeit}"
log(){ echo "[firstboot] $*"; }

ensure_data_mounted() {
    if mountpoint -q "$DATA"; then return 0; fi
    install -d "$DATA"
    dev="$(blkid -L DATA 2>/dev/null || true)"
    [ -n "$dev" ] || { log "FATAL: no partition labelled DATA found"; return 1; }
    if ! blkid -o value -s TYPE "$dev" 2>/dev/null | grep -q '^btrfs$'; then
        log "formatting $dev as btrfs (DATA)"
        mkfs.btrfs -q -L DATA "$dev"
    fi
    mount -o noatime,compress=zstd:1 "$dev" "$DATA"
    log "DATA mounted at $DATA ($dev)"
}

ensure_subvols() {
    btrfs quota enable "$DATA" 2>/dev/null || true
    for sv in secrets queue config record logs llm portal toollayer apps; do
        if [ ! -d "$DATA/$sv" ]; then
            btrfs subvolume create "$DATA/$sv" >/dev/null
            log "subvol + $sv"
        fi
    done
    install -d "$DATA/record/index" "$DATA/record/work" "$DATA/record/.trash"
    chmod 0700 "$DATA/secrets"
    btrfs qgroup limit 32G "$DATA/record/work" 2>/dev/null || true
    btrfs qgroup limit 16G "$DATA/toollayer"   2>/dev/null || true
    btrfs qgroup limit  8G "$DATA/logs"        2>/dev/null || true
}

seed_identity() {
    idfile="$DATA/config/identity.env"
    [ -f "$idfile" ] && return 0
    install -d "$DATA/config"
    suffix="$(head -c2 /dev/urandom 2>/dev/null | od -An -tx1 | tr -d ' \n' || echo 00)"
    name="brainarbeit-${suffix:-node}"
    cat > "$idfile" <<EOF
# Brainarbeit node identity (owner may rename later).
NODE_NAME=$name
MDNS_NAME=$name.local
PROVISIONED_AT=$(date -u +%FT%TZ 2>/dev/null || echo unknown)
EOF
    log "identity seeded: $name (mDNS $name.local)"
}

build_engine_venv() {
    venv="$DATA/engine/.venv"
    src="/usr/lib/brainarbeit/engine-src"
    [ -x "$venv/bin/pnd" ] && { log "engine venv already built"; return 0; }
    if [ -f "$src/.MISSING" ] || [ ! -e "$src/pyproject.toml" ] && [ ! -e "$src/setup.py" ] && [ ! -e "$src/requirements.txt" ]; then
        log "engine source not present in image (submodule was not checked out) — install it later"
        return 0
    fi
    install -d "$DATA/engine"
    log "building pinned engine venv at $venv"
    python3 -m venv "$venv"
    if [ -f "$src/requirements.txt" ]; then
        "$venv/bin/pip" install --no-input -r "$src/requirements.txt" || log "WARN: requirements install incomplete"
    fi
    "$venv/bin/pip" install --no-input "$src" || log "WARN: engine install incomplete (retry later)"
    log "engine venv ready"
}

init_record() {
    rec="$DATA/record/index"
    [ -d "$rec/.git" ] && return 0
    install -d "$rec"
    if command -v git >/dev/null 2>&1; then
        git -C "$rec" init -q
        git -C "$rec" config user.name  "brainarbeit"
        git -C "$rec" config user.email "record@brainarbeit.local"
        : > "$rec/.gitkeep"
        git -C "$rec" add -A || true
        git -C "$rec" commit -q -m "Record genesis (firstboot)" || true
        log "Record initialised (git provenance, record_ok done-gate armed)"
    else
        log "WARN: git absent — Record cannot init (engine venv carries it; will retry)"
    fi
}

seed_backlog() {
    bl="$DATA/config/safe-backlog.json"
    [ -f "$bl" ] && return 0
    install -d "$DATA/config"
    cat > "$bl" <<'JSON'
{
  "comment": "SAFE default backlog seeded at first boot. Every item is observe-only or",
  "comment2": "self-introspective; nothing binds a device or runs a destructive op. The brain",
  "comment3": "may PROPOSE more; the human disposes (product.md §6).",
  "tasks": [
    { "task_type": "self.healthcheck", "params": {}, "schedule": "every 20m",
      "why": "credential + box health canary (product.md §4 mandatory)" },
    { "task_type": "self.introspect", "params": {},
      "why": "summarise what this node can do, for the cockpit welcome card" },
    { "task_type": "net.discover", "params": { "mode": "observe-only", "scope": "own-cidr" },
      "approval": "required", "auto_run": false,
      "why": "OFFER an observe-only LAN sweep; stays a candidate card until a human taps Bind" }
  ]
}
JSON
    log "SAFE backlog seeded (discovery is an OFFER, not auto-run)"
}

main() {
    ensure_data_mounted
    ensure_subvols
    seed_identity
    build_engine_venv
    init_record
    seed_backlog
    : > "$DATA/config/.firstboot-done"
    log "first boot complete"
}

main "$@"
