#!/bin/sh
set -eu

SRC="${1:?usage: pn-cell-stage.sh <src_dir> <out_data_img>}"
OUT="${2:?usage: pn-cell-stage.sh <src_dir> <out_data_img>}"

if [ ! -d "$SRC" ]; then
  echo "pn-cell-stage: source dir '$SRC' does not exist or is not a directory" >&2
  exit 1
fi

export PATH="/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

SRC_KB=$(du -sk "$SRC" | cut -f1)
SIZE_MB=$(( (SRC_KB * 2) / 1024 + 8 ))

rm -f "$OUT"
truncate -s "${SIZE_MB}M" "$OUT"
mke2fs -t ext4 -F -q -d "$SRC" "$OUT"

echo "pn-cell-stage: packed '$SRC' (${SRC_KB}KB) into '$OUT' (${SIZE_MB}MB ext4 -> /dev/vdc)"
