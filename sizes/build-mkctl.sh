#!/bin/bash
# build-mkctl.sh -- compile mkctl.c against a built ntfs-3g source tree.
# Point N3G at its root (see mkimg.sh).
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
N3G="${N3G:?set N3G to a built ntfs-3g source tree}"
gcc -O1 -g -Wall -I"$N3G" -I"$N3G/include/ntfs-3g" -o "$D/mkctl" "$D/mkctl.c" \
    -L"$N3G/libntfs-3g/.libs" -lntfs-3g
echo "built $D/mkctl"
