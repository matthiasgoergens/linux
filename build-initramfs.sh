#!/bin/bash
# build-initramfs.sh <init> <out.cpio.gz> <busybox-rootfs-dir> -- the given
# busybox rootfs plus <init>, static rdcheck and static rt.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
INIT="${1:?init}" OUT="${2:?out}" ROOTFS="${3:?busybox rootfs dir}"
Rt=$(mktemp --directory /var/tmp/ntfs-extra-rootfs.XXXXXX)
cp --archive "$ROOTFS/." "$Rt/"
rm --force "$Rt/init.bak"
cp "$INIT" "$Rt/init"
chmod +x "$Rt/init"
gcc -O1 -static -Wall -o "$Rt/rdcheck" "$D/rdcheck.c"
gcc -O1 -static -Wall -o "$Rt/rt" "$D/rt.c"
(cd "$Rt" && find . | cpio -o -H newc --quiet | gzip -9) > "$OUT"
rm --recursive --force "$Rt"
echo "initramfs built: $(sha256sum "$OUT")"
