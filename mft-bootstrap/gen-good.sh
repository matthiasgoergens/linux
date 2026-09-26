#!/bin/bash
# gen-good.sh <outdir> -- the six plain mkntfs volumes used as mount-regression
# positive controls (they must keep mounting with the series applied).
#
# Requires a built ntfs-3g source tree (https://github.com/tuxera/ntfs-3g):
#   ./autogen.sh && ./configure && make
# Point N3G at its root.  Not byte-for-byte reproducible: mkntfs embeds the
# current time and a volume serial number, so this reproduces the six
# volumes' layout and mount behaviour, not good/*.img.xz's exact bytes.
set -euo pipefail
N3G="${N3G:?set N3G to a built ntfs-3g source tree}"
OUT="${1:?outdir}"
mkdir --parents "$OUT"
export LD_LIBRARY_PATH="$N3G/libntfs-3g/.libs"
MK="$N3G/ntfsprogs/.libs/mkntfs"
truncate --size=8m  "$OUT/g8.img";       $MK --quiet --force --fast "$OUT/g8.img"
truncate --size=16m "$OUT/g16.img";      $MK --quiet --force --fast "$OUT/g16.img"
truncate --size=64m "$OUT/g64.img";      $MK --quiet --force --fast -c 4096 "$OUT/g64.img"
truncate --size=32m "$OUT/g32c512.img";  $MK --quiet --force --fast -c 512 "$OUT/g32c512.img"
truncate --size=16m "$OUT/g16lbl.img";   $MK --quiet --force --fast -L testvol "$OUT/g16lbl.img"
truncate --size=16m "$OUT/g16c2048.img"; $MK --quiet --force --fast -s 512 -c 2048 "$OUT/g16c2048.img"
sha256sum "$OUT"/g*.img
