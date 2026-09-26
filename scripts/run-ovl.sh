#!/bin/sh
# Test 3: overlayfs-over-ext4 unlinked-inode eviction under reclaim.
# usage: run-ovl.sh <hook|reclaim> <rounds> <files-per-kind> [hog-MiB]
echo 4 > /proc/sys/vm/drop_caches
/root/ovl_evict "$@"
echo 4 > /proc/sys/vm/drop_caches
echo OVL_RUN_DONE
