#!/bin/bash
# gen-base.sh <out.img> <cluster-size> <rounds> -- a fresh 32 MiB NTFS volume
# written entirely in userspace (mkntfs + libntfs-3g, no mount, no loop
# device) holding /victim and /pad, filled by mkfrag.c with self-describing
# content, <rounds> 4 KiB blocks each, allocated alternately.
#
# Needs a built ntfs-3g tree (https://github.com/tuxera/ntfs-3g,
# ./autogen.sh && ./configure && make); point N3G at it.  mkntfs embeds the
# time and a random serial number, so the output reproduces the layout of the
# shipped base images, not their bytes; the crafted images are made from the
# shipped ones by gen-extra.py, which is deterministic.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
N3G="${N3G:?point N3G at a built ntfs-3g tree}"
OUT="${1:?out.img}" CS="${2:?cluster size}" ROUNDS="${3:?rounds}"
export LD_LIBRARY_PATH=$N3G/libntfs-3g/.libs
gcc -O1 -g -Wall -I"$N3G" -I"$N3G/include/ntfs-3g" -o "$D/mkfrag" "$D/mkfrag.c" \
    -L"$N3G/libntfs-3g/.libs" -lntfs-3g
rm --force "$OUT"
truncate --size=32M "$OUT"
"$N3G/ntfsprogs/.libs/mkntfs" --force --fast --quiet --cluster-size "$CS" \
    --label extra "$OUT"
"$D/mkfrag" "$OUT" "$ROUNDS"
sha256sum "$OUT"
