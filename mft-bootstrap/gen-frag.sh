#!/bin/bash
# gen-frag.sh <out.img> -- build frag.img: a real, fully mountable volume
# whose $MFT ends up with an attribute list and three $DATA extents in
# extent records, entirely in userspace through libntfs-3g (fragment-mft.c).
# No kernel mount, no FUSE, no loop device.
#
# Requires a built ntfs-3g source tree (https://github.com/tuxera/ntfs-3g):
#   ./autogen.sh && ./configure && make
# Point N3G at its root.  Not byte-for-byte reproducible: mkntfs embeds the
# current time and a volume serial number, and ntfs_create()/ntfs_delete()
# stamp file times, so this reproduces frag.img's structure (attribute
# list, three extent records, 13894 files) and mount behaviour, not
# frag.img.xz's exact bytes.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
N3G="${N3G:?set N3G to a built ntfs-3g source tree}"
OUT="${1:?out.img}"
export LD_LIBRARY_PATH="$N3G/libntfs-3g/.libs"
gcc -O1 -g -Wall -I"$N3G" -I"$N3G/include" -o "$D/fragment-mft" "$D/fragment-mft.c" \
    -L"$N3G/libntfs-3g/.libs" -lntfs-3g -Wl,-rpath,"$N3G/libntfs-3g/.libs"
truncate --size=32m "$OUT"
"$N3G/ntfsprogs/.libs/mkntfs" --quiet --force --fast -c 512 "$OUT"
"$D/fragment-mft" "$OUT" 100000 11000
sha256sum "$OUT"
