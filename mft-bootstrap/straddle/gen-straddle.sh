#!/bin/bash
# gen-straddle.sh <frag.img> <outdir> -- build straddle-self.img, straddle-other.img
# and base-del.img from a legitimate fragmented volume (../gen-frag.sh's
# frag.img), by relocating one $MFT extent record into the $MFT record
# freed by deleting file t05353.  Userspace only (libntfs-3g + raw record
# edits); nothing is mounted anywhere.  See the top-level README for what
# each image demonstrates.
#
# Requires a built ntfs-3g source tree (https://github.com/tuxera/ntfs-3g):
#   ./autogen.sh && ./configure && make
# Point N3G at its root.
#
# NOT byte-for-byte reproducible, even from the exact frag.img used in the
# VM tests: ntfs_delete() (delete-file.c) stamps the touched records'
# timestamps with the current time.  Measured: re-running this against the
# original frag.img reproduces base-del.img's structure and mount
# behaviour, differing from the shipped image in exactly 24 bytes (two
# mtime/atime fields), never in layout.  straddle-self.img.xz,
# straddle-other.img.xz, base-del.img.xz and fresh.img.xz are therefore
# shipped as the images actually used in the VM tests.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
N3G="${N3G:?set N3G to a built ntfs-3g source tree}"
FRAG="${1:?frag.img}"
OUT="${2:?outdir}"
mkdir --parents "$OUT"
export LD_LIBRARY_PATH="$N3G/libntfs-3g/.libs"
gcc -O1 -g -Wall -I"$N3G" -I"$N3G/include" -o "$D/delete-file" "$D/delete-file.c" \
    -L"$N3G/libntfs-3g/.libs" -lntfs-3g -Wl,-rpath,"$N3G/libntfs-3g/.libs"

# base-del: frag.img with t05353 (MFT record 8314) deleted, freeing that
# record for the relocation below.
cp "$FRAG" "$OUT/base-del.img"
"$D/delete-file" "$OUT/base-del.img" t05353

# fresh: an empty volume of the same geometry as frag.img (512-byte
# clusters), whose record 15 is what "restore this record to what mkntfs
# wrote" pulls from.
truncate --size=32m "$OUT/fresh.img"
"$N3G/ntfsprogs/.libs/mkntfs" --quiet --force --fast -c 512 "$OUT/fresh.img"

# straddle-self: move the extent record holding $DATA extent 2 (lowest_vcn
# 16630, originally record 15) into record 8314 -- the record that was just
# freed, which sits inside the folio where extent-1 coverage ends
# (folio 2078 = vcn 16624..16631, records 8312..8315).
python3 "$D/make-straddle.py" "$OUT/base-del.img" "$OUT/fresh.img" "$OUT/straddle-self.img" 15 8314

# straddle-other: same relocation, but with extent 3 (lowest_vcn 22293,
# originally record 17) instead -- a control whose folio is read only
# after coverage has already passed it.
python3 "$D/make-straddle.py" "$OUT/base-del.img" "$OUT/fresh.img" "$OUT/straddle-other.img" 17 8314

sha256sum "$OUT"/*.img
