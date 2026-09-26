# affs: reproducer for the extension-cache compaction bug

Reproducer for "affs: compact the linear extension cache, not the
associative one".  affs_grow_extcache() applies the linear cache's
compaction loop to the associative cache i_ac, so it reads past the end
of i_ac and leaves i_lc stale.  After an open file grows past
AFFS_LC_SIZE (512 with 4K pages) extension blocks, reads through the
linear cache return data from the wrong blocks.

## Files

- `gen_affs_big.py`: writes a 45 MiB, mostly empty, writable FFS
  (`DOS\1`) image with 512-byte blocks: `./gen_affs_big.py affs-big.img`
- `affs_test.c`: run in the guest against the image mounted on /mnt.
  Writes a file to 320 extension blocks, primes the linear cache,
  grows it to 700 extension blocks with the file still open, forces the
  cache to grow, then reads 4000 random aligned words and checks each
  against the written pattern (the u32 at offset o is o / 4, so a
  misrouted read shows where its data came from).
- `init.sh`: initramfs /init: mounts the image read-write and runs the
  test.
- `run-vm.sh <bzImage> <log> [timeout]`: boots the kernel under QEMU
  with the image as a virtio disk (snapshot=on, so the image is not
  modified).

## Running

    ./gen_affs_big.py affs-big.img
    gcc -static -O2 -o rootfs/affs_test affs_test.c
    cp init.sh rootfs/init    # plus a static busybox as rootfs/bin/sh etc.
    ./run-vm.sh arch/x86/boot/bzImage console.log

The kernel needs CONFIG_AFFS_FS=y (KASAN optional but useful).

## Results

Unpatched v7.3-rc3-based kernel, KASAN:

    PHASE1 primed linear cache at 11796480 bytes (320 ext blocks)
    PHASE2 grown to 25804800 bytes (700 ext blocks), fd still open
    BUG: KASAN: slab-use-after-free in affs_get_extblock_slow+0x1199/0x1640
    Read of size 8 at addr ff11000003a1d000 by task affs_test/72
    PHASE3 shift-change trigger read done
    RESULT reads=4000 ok=10 mismatches=3990 (in_primed_region=1867) read_errors=0
    VERDICT BUG-REPRODUCED

With the patch, no KASAN report in affs:

    RESULT reads=4000 ok=4000 mismatches=0 (in_primed_region=0) read_errors=0
    VERDICT CLEAN
