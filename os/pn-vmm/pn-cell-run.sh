#!/usr/bin/env bash
set -u

log() { printf 'pn-cell-run: %s\n' "$*" >&2; }
die() { printf 'pn-cell-run: ERROR: %s\n' "$*" >&2; exit 1; }

[ "$#" -ge 2 ] || die "usage: $0 <kernel> <initramfs> [pn-vmm args...]"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PN_VMM_BIN="${PN_VMM_BIN:-$SCRIPT_DIR/target/release/pn-vmm}"
[ -x "$PN_VMM_BIN" ] || die "pn-vmm binary not found/executable: $PN_VMM_BIN (set PN_VMM_BIN)"

CELL_MEM_MAX="${CELL_MEM_MAX:-512M}"
CELL_MEM_SWAP_MAX="${CELL_MEM_SWAP_MAX:-0}"
CELL_CPU_QUOTA="${CELL_CPU_QUOTA:-50%}"
CELL_CPU_PERIOD="${CELL_CPU_PERIOD:-100000}"
CELL_IO_WEIGHT="${CELL_IO_WEIGHT:-}"
CELL_PIDS_MAX="${CELL_PIDS_MAX:-512}"

to_bytes() {
  local s="$1"
  case "$s" in ""|max) echo max; return 0;; esac
  local up; up="$(printf '%s' "$s" | tr 'a-z' 'A-Z')"
  up="${up%IB}"; up="${up%B}"
  local num mult=1
  case "$up" in
    *K) mult=1024;               num="${up%K}";;
    *M) mult=$((1024*1024));     num="${up%M}";;
    *G) mult=$((1024*1024*1024)); num="${up%G}";;
    *)  num="$up";;
  esac
  case "$num" in *[!0-9]*|"") die "bad size: $s";; esac
  echo $(( num * mult ))
}
to_cpumax() {
  local q="$1" period="$CELL_CPU_PERIOD"
  case "$q" in
    ""|max) echo "max $period"; return 0;;
    *%)     local p="${q%\%}"; case "$p" in *[!0-9]*|"") die "bad CPU quota: $q";; esac
            echo "$(( p * period / 100 )) $period";;
    *)      case "$q" in *[!0-9]*) die "bad CPU quota: $q";; esac
            echo "$q $period";;
  esac
}

MEM_MAX_B="$(to_bytes "$CELL_MEM_MAX")"
SWAP_MAX_B="$(to_bytes "$CELL_MEM_SWAP_MAX")"
CPU_MAX="$(to_cpumax "$CELL_CPU_QUOTA")"

try_systemd() {
  command -v systemd-run >/dev/null 2>&1 || return 1
  [ -d /run/systemd/system ] || return 1
  local xrd="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  [ -S "$xrd/systemd/private" ] || return 1

  local slice_args=""
  if systemctl --user show pn.slice >/dev/null 2>&1; then
    slice_args="--slice=pn.slice"
  fi
  local io_arg=""
  [ -n "$CELL_IO_WEIGHT" ] && io_arg="-p IOWeight=$CELL_IO_WEIGHT"
  log "using systemd-run --user --scope $slice_args (MemoryMax=$CELL_MEM_MAX CPUQuota=$CELL_CPU_QUOTA)"
  exec systemd-run --user --scope $slice_args \
    -p MemoryMax="$CELL_MEM_MAX" \
    -p MemorySwapMax="$CELL_MEM_SWAP_MAX" \
    -p CPUQuota="$CELL_CPU_QUOTA" \
    -p TasksMax="$CELL_PIDS_MAX" \
    $io_arg \
    -- "$PN_VMM_BIN" "$@"
}

try_systemd "$@" || log "systemd path unavailable -> using raw cgroup v2"

CG_ROOT=/sys/fs/cgroup
[ -f "$CG_ROOT/cgroup.controllers" ] || die "cgroup v2 not mounted at $CG_ROOT"

pick_parent() {
  if [ -n "${PN_CELL_PARENT:-}" ]; then echo "$PN_CELL_PARENT"; return 0; fi
  if [ -w "$CG_ROOT/pn.slice/batch" ]; then echo "$CG_ROOT/pn.slice/batch"; return 0; fi
  if [ -w "$CG_ROOT/pn.slice" ]; then echo "$CG_ROOT/pn.slice"; return 0; fi
  local self; self="$(awk -F: '$2==""{print $3}' /proc/self/cgroup 2>/dev/null | head -1)"
  if [ -n "$self" ] && [ -w "$CG_ROOT$self" ]; then echo "$CG_ROOT$self"; return 0; fi
  return 1
}

PARENT="$(pick_parent)" || die "no writable cgroup v2 parent (need delegated pn.slice/batch)"
[ -d "$PARENT" ] || die "parent cgroup does not exist: $PARENT"
log "cgroup parent: $PARENT"

ensure_controller() {
  local ctl="$1"
  case " $(cat "$PARENT/cgroup.subtree_control" 2>/dev/null) " in
    *" $ctl "*) return 0;;
  esac
  case " $(cat "$PARENT/cgroup.controllers" 2>/dev/null) " in
    *" $ctl "*) : ;;  *) return 1;;
  esac
  [ -s "$PARENT/cgroup.procs" ] && return 1
  echo "+$ctl" > "$PARENT/cgroup.subtree_control" 2>/dev/null || return 1
  return 0
}
for c in cpu memory pids; do
  ensure_controller "$c" || log "note: controller '$c' not delegated by parent (limit may be skipped)"
done
IO_OK=1
if [ -n "$CELL_IO_WEIGHT" ]; then
  ensure_controller io || IO_OK=0
fi

CELL="$PARENT/pn-cell-$(date +%s)-$$"
mkdir "$CELL" || die "mkdir cell cgroup failed: $CELL"
log "cell cgroup: $CELL"

cleanup() { rmdir "$CELL" 2>/dev/null && log "removed $CELL" || true; }
trap cleanup EXIT INT TERM

wr() {
  local f="$CELL/$1" v="$2" lbl="$3"
  if [ -e "$f" ] && printf '%s\n' "$v" > "$f" 2>/dev/null; then
    log "set $lbl -> $(tr -d '\n' < "$f")"
  else
    log "note: could NOT set $lbl ($f)"
  fi
}

wr memory.max      "$MEM_MAX_B"  "memory.max ($CELL_MEM_MAX)"
wr memory.swap.max "$SWAP_MAX_B" "memory.swap.max ($CELL_MEM_SWAP_MAX)"
wr cpu.max         "$CPU_MAX"    "cpu.max ($CELL_CPU_QUOTA)"
wr pids.max        "$CELL_PIDS_MAX" "pids.max"
if [ -n "$CELL_IO_WEIGHT" ]; then
  if [ "$IO_OK" = 1 ] && [ -e "$CELL/io.weight" ]; then
    wr io.weight "$CELL_IO_WEIGHT" "io.weight"
  else
    log "note: io controller not delegated by parent slice -> io.weight NOT applied"
  fi
fi

printf 'PN_CELL_CGROUP=%s\n' "$CELL" >&2

log "launching: $PN_VMM_BIN $*"
sh -c 'echo $$ > "$1/cgroup.procs" || exit 97; shift; exec "$@"' _ "$CELL" "$PN_VMM_BIN" "$@"
rc=$?
log "pn-vmm exited rc=$rc"
exit "$rc"
