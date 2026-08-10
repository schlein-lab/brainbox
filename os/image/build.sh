#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$HERE/out"
ARCH="x86-64"
CONVERT=""
DO_BUILD=1
EXTRA=()

while [ $# -gt 0 ]; do
    case "$1" in
        --arch)     shift; case "$1" in arm64|aarch64) ARCH="arm64";; amd64|x86-64|x86_64) ARCH="x86-64";; *) echo "unknown arch: $1" >&2; exit 2;; esac ;;
        --convert)  shift; CONVERT="$1" ;;
        --summary)  DO_BUILD=0 ;;
        --clean)    rm -rf "$OUT" "$HERE/.mkosi-cache" "$HERE"/*.cache-info 2>/dev/null || true; echo "cleaned"; exit 0 ;;
        -h|--help)  sed -n '2,30p' "$0"; exit 0 ;;
        *)          EXTRA+=("$1") ;;
    esac
    shift
done

if ! command -v mkosi >/dev/null 2>&1; then
    echo "ERROR: mkosi is not installed on this host." >&2
    echo "  This config is build-READY but the IMAGE BUILD needs a privileged host with mkosi." >&2
    echo "  Install:  pipx install mkosi   (or: apt install mkosi)   then re-run." >&2
    echo "  A full disk build also needs root/userns + systemd-repart + ~6-10 GB scratch + network." >&2
    exit 3
fi
echo "mkosi: $(mkosi --version)"

if [ -n "${BRAINARBEIT_VERITY_KEY:-}" ] && [ -n "${BRAINARBEIT_VERITY_CERT:-}" ]; then
    EXTRA+=(--verity-key "$BRAINARBEIT_VERITY_KEY" --verity-certificate "$BRAINARBEIT_VERITY_CERT")
    echo "signing: verity key wired"
else
    echo "signing: no verity key (DEV build — verity advisory, image still boots)"
fi
if [ -n "${BRAINARBEIT_SECUREBOOT_KEY:-}" ] && [ -n "${BRAINARBEIT_SECUREBOOT_CERT:-}" ]; then
    EXTRA+=(--secure-boot-key "$BRAINARBEIT_SECUREBOOT_KEY" --secure-boot-certificate "$BRAINARBEIT_SECUREBOOT_CERT")
fi

export BUILD_SOURCES="$REPO_ROOT"
mkdir -p "$OUT"

cd "$HERE"

if [ "$DO_BUILD" -eq 0 ]; then
    echo "=== mkosi summary (lint/parse only) ==="
    exec mkosi --architecture "$ARCH" "${EXTRA[@]}" summary
fi

echo "=== mkosi build (arch=$ARCH) ==="
mkosi --architecture "$ARCH" --output-dir "$OUT" "${EXTRA[@]}" build

IMG="$OUT/brainarbeit"
[ -f "$IMG.raw" ] && IMG="$IMG.raw"
echo "built: $IMG"

if [ -n "$CONVERT" ]; then
    command -v qemu-img >/dev/null 2>&1 || { echo "qemu-img absent; skipping conversion" >&2; exit 4; }
    IFS=',' read -r -a FMTS <<< "$CONVERT"
    for fmt in "${FMTS[@]}"; do
        case "$fmt" in
            qcow2) out="$OUT/brainarbeit.qcow2"; qemu-img convert -O qcow2 -c "$IMG" "$out" ;;
            vmdk)  out="$OUT/brainarbeit.vmdk";  qemu-img convert -O vmdk  -o subformat=streamOptimized "$IMG" "$out" ;;
            vhdx)  out="$OUT/brainarbeit.vhdx";  qemu-img convert -O vhdx  "$IMG" "$out" ;;
            vdi)   out="$OUT/brainarbeit.vdi";   qemu-img convert -O vdi   "$IMG" "$out" ;;
            raw)   out="$IMG" ;;
            *)     echo "unknown convert target: $fmt (qcow2|vmdk|vhdx|vdi|raw)" >&2; continue ;;
        esac
        echo "converted: $out"
    done
fi

echo "done. Artifacts in $OUT/"
