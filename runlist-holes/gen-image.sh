#!/bin/bash
# gen-image.sh <out.img> [rounds] -- build a fragmented NTFS image entirely in
# userspace (mkntfs + mkfrag.c on libntfs-3g, no mount).  Prints victim's
# layout on stdout.
#
# Requires a built ntfs-3g source tree (https://github.com/tuxera/ntfs-3g):
#   ./autogen.sh && ./configure && make
# Point N3G at its root.
#
# Not byte-for-byte reproducible: mkntfs embeds the current time and a
# volume serial number, so re-running this will not reproduce clean.img.xz
# byte for byte, only its layout and behaviour (see the top-level README).
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
N3G="${N3G:?set N3G to a built ntfs-3g source tree}"
OUT="${1:?out.img}"
ROUNDS="${2:-1500}"
export LD_LIBRARY_PATH="$N3G/libntfs-3g/.libs"
gcc -O1 -g -Wall -I"$N3G" -I"$N3G/include/ntfs-3g" -o "$D/mkfrag" "$D/mkfrag.c" \
    -L"$N3G/libntfs-3g/.libs" -lntfs-3g
rm --force "$OUT"
truncate --size=32M "$OUT"
"$N3G/ntfsprogs/.libs/mkntfs" --force --fast --quiet --cluster-size 4096 \
    --label zeroread "$OUT"
"$D/mkfrag" "$OUT" "$ROUNDS"
sha256sum "$OUT"
