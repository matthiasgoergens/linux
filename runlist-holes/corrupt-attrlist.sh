#!/bin/bash
# corrupt-attrlist.sh <clean.img> <out.img> lowvcn|type
# Copy <clean.img> and change ONE field of victim's attribute-list entry for
# the $DATA extent in MFT record 68 (entry 5).  Record 68 stays intact.
#   lowvcn: lowest_vcn 215 -> 2000.  MEASURED NO-OP: map_whole_runlist keeps
#           its search context and still enumerates to record 68.
#   type:   type 0x80 -> 0x100 (list stays sorted).  $DATA then has no entry
#           for vcn 215..1499: map_whole_runlist fails ("Failed to load full
#           runlist") leaving a NOT_MAPPED tail, and a fresh lookup for such a
#           vcn lands on the base extent (highest_vcn 214), so
#           ntfs_map_runlist_nolock() returns -ENOENT, not -EIO.  The attribute list is non-resident at LCN 0x1399 (ntfsinfo), i.e.
# plain data without update-sequence fixups.  Refuses unless the entry is
# exactly {type 0x80, lowest_vcn 215, mft ref 68}.
set -euo pipefail
IN="${1:?in}" OUT="${2:?out}" MODE="${3:?lowvcn|type}"
BASE=$((0x1399 * 4096 + 4 * 32))
u() { od --address-radix=n --format=u$1 --skip-bytes=$(($BASE + $2)) --read-bytes=$1 "$IN" | tr --delete ' '; }
t=$(u 4 0) v=$(u 8 8) r=$(u 4 16)
echo "entry5: type=$t lowest_vcn=$v mft_ref_lo=$r"
[ "$t" = 128 ] && [ "$v" = 215 ] && [ "$r" = 68 ] || { echo "unexpected entry, refusing"; exit 1; }
cp "$IN" "$OUT"
case $MODE in
lowvcn) FMT='<q' VAL=2000 OFF=8 ;;
type)   FMT='<I' VAL=256 OFF=0 ;;
*) echo "bad mode"; exit 1 ;;
esac
python3 -c "import struct,sys; sys.stdout.buffer.write(struct.pack('$FMT', $VAL))" |
	dd of="$OUT" bs=1 seek=$(($BASE + OFF)) count=$([ $MODE = type ] && echo 4 || echo 8) conv=notrunc status=none
echo "mode $MODE: wrote $VAL at byte $(($BASE + OFF)) of $OUT"
cmp --verbose "$IN" "$OUT" || true
