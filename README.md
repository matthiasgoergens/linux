# Reproducers for "fs: Deferred inode reclaim" v2 testing

Test-only material used to test Jan Kara's series "fs: Deferred inode
reclaim" v2 (https://lore.kernel.org/all/20260911081309.14137-1-jack@suse.cz/)
against its base, 08df884136f1, in QEMU with KASAN and lockdep.  Nothing
here is meant for merging.

## Kernel debug patch

`0001-test-instrumentation.patch` applies to both kernels.  It makes
`echo 2 > /proc/sys/vm/drop_caches` run drop_slab() under
memalloc_noreclaim_save(), which approximates reclaim context, and counts
work done under PF_MEMALLOC (__GFP_NOFAIL slowpath entries, buffer cache
grows, ext4 inode table reads, evictions, ext4 deletions).
`echo 4 > /proc/sys/vm/drop_caches` prints the totals.  The "hook" mode of
ovl_evict and the lazytime workload rely on it; the "reclaim" mode of
ovl_evict uses real memory pressure only.

## Programs (build statically, run as root in the guest)

- `repro_memalloc.c`: lazytime churn on ext4 under memory pressure.  Set
  `CHURN_DIR` to a directory on ext4 (the default /tmp is tmpfs on
  syzbot's image, which does not exercise ext4).
- `ovl_evict.c`: overlayfs over ext4.  Instantiates overlay inodes for
  lower and upper files, unlinks the files directly in lowerdir/upperdir,
  then drops caches ("hook") or applies real memory pressure
  ("reclaim"), so the last reference to the unlinked ext4 inodes is put
  from reclaim context.  Usage: `ovl_evict <hook|reclaim> <rounds>
  <files-per-kind> [hog-MiB]`.
- `hibernate_smoke.c`: drives one hibernation cycle with
  /sys/power/pm_test set to devices.  Its header comment refers to an
  earlier, unposted approach; here it is only used to run the cycle.

## Scripts (in the guest)

- `scripts/run-memalloc.sh <seconds> <hog-MiB> [sync]`: test 1.
- `scripts/run-hib.sh <cycles> <hog-MiB> <freeze_filesystems 0|1>
  <files>`: test 2, hibernation cycles while the lazytime load runs,
  writing 300 fsync'ed files per cycle so ext4 gives them inode
  preallocations.
- `scripts/run-ovl.sh <hook|reclaim> <rounds> <files> [hog-MiB]`: test 3.
- `scripts/killall_comm.sh`: helper for busybox without pkill.

The guest was syzbot's disk image for extid 7f94fe3ce0f6613e12b8 with a
config close to syzbot's (KASAN, PROVE_LOCKING, HIBERNATION, PM_DEBUG,
OVERLAY_FS), 2-3 GiB of memory.
