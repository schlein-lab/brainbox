#!/bin/bash

CONF="/etc/vm-oom-guard.conf"
TAG="vm-oom-guard"
QEMU_XML_DIR="/etc/libvirt/qemu"
CG="/sys/fs/cgroup"
RESERVE_CAP_PCT="${RESERVE_CAP_PCT:-60}"

log() { logger -t "$TAG" -- "$*" 2>/dev/null; echo "$TAG: $*"; }

[ -r "$CONF" ] || { log "no conf $CONF — nothing to do"; exit 0; }

declare -A TITLE
for x in "$QEMU_XML_DIR"/*.xml; do
    [ -e "$x" ] || continue
    n=$(sed -n 's/.*<name>\([^<]*\)<\/name>.*/\1/p' "$x" | head -1)
    t=$(sed -n 's/.*<title>\([^<]*\)<\/title>.*/\1/p' "$x" | head -1)
    [ -n "$n" ] && TITLE["$n"]="${t:-$n}"
done

regel_fuer() {
    local dom="$1" title="$2" re adj res schluessel
    while IFS='|' read -r re adj res; do
        case "$re" in ''|\#*) continue;; esac
        adj="${adj//[[:space:]]/}"; res="${res//[[:space:]]/}"
        case "$re" in
            uuid:*)
                schluessel=$(printf '%s' "${re#uuid:}" | tr 'A-Z' 'a-z')
                for k in $dom; do
                    [ "$(printf '%s' "$k" | tr 'A-Z' 'a-z')" = "$schluessel" ] && { echo "${adj}|${res}"; return 0; }
                done
                ;;
            *)
                if echo "$title" | grep -Eq "^(${re})$"; then echo "${adj}|${res}"; return 0; fi
                ;;
        esac
    done < "$CONF"
    return 1
}

kennungen() {
    local name="$1" pid="$2" x="$QEMU_XML_DIR/$1.xml" u q
    printf '%s' "$name"
    if [ -r "$x" ]; then
        u=$(sed -n 's/.*<uuid>\([^<]*\)<\/uuid>.*/\1/p' "$x" | head -1)
        [ -n "$u" ] && printf ' %s' "$u"
    fi
    q=$(tr '\0' '\n' < "/proc/$pid/cmdline" 2>/dev/null | grep -A1 -x -- '-uuid' | tail -1)
    case "$q" in *-*-*-*-*) printf ' %s' "$q";; esac
    printf '\n'
}

ram_bytes() {
    local name="$1" x="$QEMU_XML_DIR/$1.xml" kib
    [ -r "$x" ] || { echo 0; return; }
    kib=$(sed -n 's/.*<memory unit=.KiB.>\([0-9]*\)<\/memory>.*/\1/p' "$x" | head -1)
    echo $(( ${kib:-0} * 1024 ))
}

scope_pfad() {
    local pid="$1" p
    p=$(awk -F: '/^0::/{print $3}' "/proc/$pid/cgroup" 2>/dev/null)
    [ -n "$p" ] || return 1
    while [ -n "$p" ] && [ "$p" != "/" ]; do
        case "$p" in *.scope) echo "$p"; return 0;; esac
        p="${p%/*}"
    done
    return 1
}

min_setzen() {
    local rel="$1" wert="$2" datei="$CG$rel/memory.min" ist
    [ -w "$datei" ] || return 1
    ist=$(cat "$datei" 2>/dev/null)
    [ "$ist" = "$wert" ] && return 0
    echo "$wert" > "$datei" 2>/dev/null || return 1
    return 0
}

RUNDIR="/run/libvirt/qemu"; [ -d "$RUNDIR" ] || RUNDIR="/var/run/libvirt/qemu"
if [ ! -d "$RUNDIR" ]; then
    log "kein libvirt auf diesem Wirt ($RUNDIR fehlt) — nichts zu schuetzen. Laeuft die Box unter einem anderen Hypervisor, dort das Aequivalent setzen; im Gast bleibt der Handgriff 'Wirt-Verdacht' die Erkennung."
    exit 0
fi
if [ "$(stat -fc %T "$CG" 2>/dev/null)" != "cgroup2fs" ]; then
    log "cgroup v2 nicht vorhanden ($CG) — die Auslagerungs-Sperre (memory.min/memory.swap.max) gibt es hier nicht. Es wird nur die Abschuss-Reihenfolge gesetzt."
    NUR_ADJ=1
fi
total_bytes=$(( $(awk '/MemTotal/{print $2}' /proc/meminfo) * 1024 ))
kappe=$(( total_bytes * RESERVE_CAP_PCT / 100 ))
reserviert=0
declare -A ELTERN_SUMME
declare -A ZU_SETZEN
declare -A ZU_TITEL

for pf in "$RUNDIR"/*.pid; do
    [ -e "$pf" ] || continue
    name=$(basename "$pf" .pid)
    pid=$(cat "$pf" 2>/dev/null)
    [ -n "$pid" ] && [ -d "/proc/$pid" ] || continue
    case "$(cat "/proc/$pid/comm" 2>/dev/null)" in qemu*) ;; *) continue;; esac
    title="${TITLE[$name]:-$name}"
    regel=$(regel_fuer "$(kennungen "$name" "$pid")" "$title") || continue
    adj="${regel%%|*}"; res="${regel##*|}"; [ "$res" = "$regel" ] && res=""

    cur=$(cat "/proc/$pid/oom_score_adj" 2>/dev/null)
    if [ "$cur" != "$adj" ]; then
        echo "$adj" > "/proc/$pid/oom_score_adj" 2>/dev/null \
            && log "set '$title' (pid $pid) oom_score_adj $cur -> $adj" \
            || log "FAILED to set '$title' (pid $pid) oom_score_adj -> $adj"
    fi

    [ "${NUR_ADJ:-0}" = "1" ] && continue
    case "$res" in
        ""|0|none) continue;;
        full)      want=$(ram_bytes "$name");;
        *[gG])     want=$(( ${res%[gG]} * 1024 * 1024 * 1024 ));;
        *[mM])     want=$(( ${res%[mM]} * 1024 * 1024 ));;
        *)         want="$res";;
    esac
    [ "${want:-0}" -gt 0 ] 2>/dev/null || continue

    if [ $(( reserviert + want )) -gt "$kappe" ]; then
        log "RESERVE SKIPPED for '$title': $((want/1024/1024)) MiB would exceed the ${RESERVE_CAP_PCT}% cap ($((kappe/1024/1024)) MiB) — a guard that starves the host is worse than none"
        continue
    fi

    rel=$(scope_pfad "$pid") || { log "no cgroup scope for '$title' (pid $pid) — no reserve set"; continue; }

    eltern="${rel%/*}"
    while [ -n "$eltern" ] && [ "$eltern" != "" ]; do
        ELTERN_SUMME["$eltern"]=$(( ${ELTERN_SUMME["$eltern"]:-0} + want ))
        eltern="${eltern%/*}"
    done

    ZU_SETZEN["$rel"]="$want"
    ZU_TITEL["$rel"]="$title"
    reserviert=$(( reserviert + want ))
done

for pfad in "${!ELTERN_SUMME[@]}"; do
    [ -n "$pfad" ] || continue
    min_setzen "$pfad" "${ELTERN_SUMME[$pfad]}" \
        && log "parent $pfad memory.min=$(( ${ELTERN_SUMME[$pfad]} /1024/1024 )) MiB (sum of protected children)" \
        || log "FAILED to set memory.min on parent $pfad — every reservation below it stays ineffective"
done
for rel in "${!ZU_SETZEN[@]}"; do
    want="${ZU_SETZEN[$rel]}"; title="${ZU_TITEL[$rel]}"
    if min_setzen "$rel" "$want"; then
        log "reserve '$title': memory.min=$((want/1024/1024)) MiB on $rel"
    else
        log "FAILED to set memory.min for '$title' on $rel — the VM stays swappable"
    fi

    sm="$CG$rel/memory.swap.max"
    if [ -w "$sm" ]; then
        if [ "$(cat "$sm" 2>/dev/null)" != "0" ]; then
            echo 0 > "$sm" 2>/dev/null \
                && log "reserve '$title': memory.swap.max=0 — this VM is never swapped out" \
                || log "FAILED to forbid swap for '$title' — it can still be paged to disk"
        fi
    else
        log "no memory.swap.max for '$title' ($rel) — swap cannot be forbidden here"
    fi
done

sum_kib=0
for pf in "$RUNDIR"/*.pid; do
    [ -e "$pf" ] || continue
    name=$(basename "$pf" .pid)
    x="$QEMU_XML_DIR/$name.xml"
    [ -r "$x" ] || continue
    mem=$(sed -n 's/.*<memory unit=.KiB.>\([0-9]*\)<\/memory>.*/\1/p' "$x" | head -1)
    sum_kib=$((sum_kib + ${mem:-0}))
done
total_kib=$(( total_bytes / 1024 ))
if [ "$sum_kib" -gt 0 ] && [ "$sum_kib" -gt $(( total_kib * 80 / 100 )) ]; then
    log "OVERCOMMIT: running VMs promise $((sum_kib/1024)) MiB against host $((total_kib/1024)) MiB — protected VMs keep their pages, the others get swapped or shot"
fi
exit 0
