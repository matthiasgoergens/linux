#!/bin/bash
# corrupt.sh <in.img> <out.img> <mft-record-no> [mft-lcn] [cluster-size]
# Copy <in.img> and break ONLY the FILE magic of one MFT record ("FILE" ->
# "XILE"), leaving every other byte intact.  Refuses unless the record
# currently starts with FILE and carries the expected record number.
set -euo pipefail
IN="${1:?in}" OUT="${2:?out}" REC="${3:?record}"
MFTLCN="${4:-4}" CS="${5:-4096}"
OFF=$((MFTLCN * CS + REC * 1024))
magic=$(dd if="$IN" bs=1 skip=$OFF count=4 status=none)
recno=$(od --address-radix=n --format=u4 --skip-bytes=$((OFF + 0x2c)) --read-bytes=4 "$IN" | tr --delete ' ')
[ "$magic" = FILE ] || { echo "record $REC at $OFF has magic '$magic', refusing"; exit 1; }
[ "$recno" = "$REC" ] || { echo "record at $OFF says it is $recno, not $REC; refusing"; exit 1; }
cp "$IN" "$OUT"
printf 'X' | dd of="$OUT" bs=1 seek=$OFF count=1 conv=notrunc status=none
echo "corrupted MFT record $REC magic at byte $OFF of $OUT"
cmp --verbose "$IN" "$OUT" || true
