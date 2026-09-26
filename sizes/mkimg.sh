#!/bin/bash
# mkimg.sh <out.img> [size] -- fresh empty NTFS image (4K clusters), userspace
# mkntfs.  Used as the base for ctl.img (mkctl.c writes /cz and /sp onto it).
#
# Requires a built ntfs-3g source tree (https://github.com/tuxera/ntfs-3g):
#   ./autogen.sh && ./configure && make
# Point N3G at its root.  Not byte-for-byte reproducible: mkntfs embeds the
# current time and a volume serial number (see the top-level README).
set -euo pipefail
N3G="${N3G:?set N3G to a built ntfs-3g source tree}"
export LD_LIBRARY_PATH="$N3G/libntfs-3g/.libs"
rm --force "$1"; truncate --size="${2:-64M}" "$1"
"$N3G/ntfsprogs/.libs/mkntfs" --force --fast --quiet --cluster-size 4096 --label cx "$1"
sha256sum "$1"
