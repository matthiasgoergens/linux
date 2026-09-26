# isofs empty-blocks reproducer

Two crafted ISO 9660 images that trigger an unbounded walk in the kernel's
`isofs_read_level3_size()` (`fs/isofs/inode.c`), which handles files split
into several extents ("level 3"/multi-extent files).  The walk follows a
chain of directory records and, on an empty (all-zero) directory block,
simply moves on to the next block with no limit.  A crafted image can put a
long run of such empty blocks between two extents of a file, and the walk
reads every one of them, all the way to the end of the device if it has to.

Run `./gen.py empty-gap-150.iso mixed-60-50.iso` (Python 3, standard
library only) to produce both images.

## What each image contains

**`empty-gap-150.iso`**: a one-block root directory holding a small file
"z" and the first record of a multi-extent file "a".  That first record is
flagged as "more extents follow".  150 all-zero directory blocks come next,
then a final block holding "a"'s last record.  The directory's declared
size is patched down to a single block afterwards, so an ordinary directory
listing (`readdir`) never goes near the 150 empty blocks or the tail
record — only a direct lookup of "a" by name does, and that lookup is what
has to walk across the gap.

**`mixed-60-50.iso`**: a multi-extent file "a" with 60 extents, with an
empty directory block inserted after each of the first 50 extent-to-extent
transitions (110 blocks counted together).  Here the directory's declared
size does cover the whole chain, so a plain listing reaches "a" directly.
The point of this image is different: the kernel's original bound only
counted the 60 real extents (well under its limit of 100), so it let this
image through regardless of how many empty blocks sat in between.

## How to test

Do this on a scratch machine or inside a disposable VM only — never against
a production host's kernel, since the point of the first image is an
unbounded read loop.  A quick way, from a kernel build tree with
`CONFIG_ISO9660_FS` enabled:

```
qemu-system-x86_64 -kernel path/to/bzImage -initrd path/to/initramfs.cpio.gz \
    -append "console=ttyS0" \
    -drive file=empty-gap-150.iso,format=raw,if=virtio,readonly=on \
    -m 512M -display none -serial stdio -no-reboot
```

with an init script (or an interactive shell) that does:

```
mount -t iso9660 -o ro /dev/vda /mnt
ls -la /mnt        # empty-gap-150.iso: only "z" is listed, "a" is not
stat /mnt/a        # this is what actually reaches "a" and its extents
```

For `empty-gap-150.iso`, `stat` (or `open`/`read`) is the only way to reach
"a": `readdir` stops at the directory's declared one-block size and never
sees the far-away records, exactly as `ls -la` above shows.  For
`mixed-60-50.iso`, `ls -la /mnt` already lists "a" directly (its directory
covers the whole chain), and `stat /mnt/a` confirms the same size.

## Expected result

Without the patch:

- `empty-gap-150.iso`: `stat /mnt/a` succeeds and reports the file's full
  size (5096 bytes, the sum of both extents) — the walk simply reads all
  150 empty blocks and both extents' records with nothing to stop it.
- `mixed-60-50.iso`: `stat`/`ls` succeed and report the full size (4770
  bytes, the sum of all 60 extents) — the bound in place only counted
  extents, so 60 extents pass regardless of the 50 empty blocks between
  them.

With the patch, both cases abort partway through and log, on the console
or in `dmesg`:

```
isofs_read_level3_size: More than 100 file sections/empty blocks ?!?, aborting...
isofs_read_level3_size: inode=<N>
```

`stat` still succeeds as a system call, but reports a truncated size: 4096
bytes (just the first extent) for `empty-gap-150.iso`, and 3825 bytes
(the partial sum up to where the combined count passed 100) for
`mixed-60-50.iso`.
