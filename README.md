# fs/ntfs: reproducers for the $MFT bootstrap and runlist-hole series

Standalone reproducers for a six-patch series against the in-kernel `ntfs`
driver (`fs/ntfs`, not `ntfs3`), based on `ntfs-next` at `259abb551e29`
(`https://git.kernel.org/pub/scm/linux/kernel/git/linkinjeon/ntfs.git`).  All
images here are crafted or built entirely
in userspace (raw record edits, or libntfs-3g/mkntfs with no kernel mount, no
loop device and no FUSE); none of this was ever run against a production
host.

The bugs fall into three groups, one per directory, corresponding to the six
patches of the series:

- `runlist-holes/` -- patches 1 and 2: a runlist fragment that cannot be
  mapped is treated as a hole instead of an error.
- `mft-bootstrap/` -- patches 3, 4 and 5: mounting can re-enter $MFT's own
  runlist while it is still being built, which self-deadlocks or crashes the
  mount.
- `sizes/` -- patch 6: a non-resident attribute's `data_size`/
  `initialized_size` can disagree with its `allocated_size`, so reads past
  the allocation return zeros that were never written.

## Patch 1: do not map an unmappable runlist fragment as a hole

`ntfs_attr_vcn_to_rl()` drops the error when the extent mft record holding
part of a file's runlist cannot be read, and hands back `LCN_RL_NOT_MAPPED`.
The read path treats any `lcn` above `LCN_ENOENT` as a hole, so `read()`
returns zeros instead of an error.  `runlist-holes/clean.img.xz` is a 32 MiB
volume built with `mkntfs` and `mkfrag.c` (via `gen-image.sh`): file
`/victim` has 1500 one-cluster runs, so its `$DATA` is split between its
base mft record and an extent record, with self-describing content (each
8-byte word encodes its own file id and offset, so a reader can tell
correct data, zeros and garbage apart without a reference copy; `rdcheck.c`
does this classification).  `corrupt.sh clean.img corrupt-rec68.img 68`
flips one byte, breaking extent record 68's `FILE` magic; `victim`'s
vcn 215-1499 (1285 clusters) then read as zeros with no error, on an
unpatched kernel.  `corrupt-attrlist.sh clean.img corrupt-attrlist-type.img
type` retypes the attribute-list entry for that same extent instead
(record 68 stays intact), which drives the same retry down the
`LCN_ENOENT` path rather than `LCN_RL_NOT_MAPPED`; `corrupt-attrlist.sh
clean.img corrupt-attrlist-lowvcn.img lowvcn` changes the entry's
`lowest_vcn` instead, which is a measured no-op (`ntfs_attr_map_whole_runlist()`
keeps its search context and finds record 68 anyway) and is included as a
control.

## Patch 2: do not turn an unmappable runlist fragment into delalloc on write

The same unchecked retry exists in
`ntfs_write_simple_iomap_begin_non_resident()`.  On `corrupt-rec68.img`, a
buffered write into the unreadable range (e.g. an 8 KiB `pwrite()` at
vcn 1000, using `rt.c`'s `w`/`wf` commands) returns success on an unpatched
kernel, the write is merged into the runlist as `LCN_DELALLOC` over
clusters that are already allocated on disk, and it is only at `fsync()`
(or never, if nothing calls `fsync()`) that `-EIO` appears; after a remount
the range is unchanged, i.e. the data is silently lost.  No separate image
is needed: this is the write-side counterpart of patch 1's read bug, on the
same `corrupt-rec68.img`.

## Patch 3: fail the mount when $MFT needs its own extent records

`ntfs_read_inode_mount()` builds $MFT's own runlist one `$DATA` extent at a
time and, per its own comment, hopes it never needs a part of $MFT it has
not decoded yet.  A crafted image can violate that: if $MFT's attribute
list places one of its own attributes or a later `$DATA` extent in an
extent record outside the runlist decoded so far, reading that record
re-enters `ntfs_map_runlist_nolock()` for $MFT and self-deadlocks on the
folio lock the mount already holds (`INFO: task mount: blocked in I/O wait
for more than 30 seconds`, stuck in `folio_wait_bit_common`), or crashes in
`map_extent_mft_record()`.  `mft-bootstrap/repro.img` and `repro-2.img` are
the original AFL-found reproducers (28 KiB each, no generator).
`gen-loop-cycle.py` builds a minimal, fully synthetic 256 KiB volume with
the same shape: `loop-cycle.img` (default, `REC_E=64`) places $MFT's second
`$DATA` extent in extent record 64, outside the coverage of the first
extent, and hangs the whole `$DATA`-enumeration loop (the case Hyunchul Lee
identified was missed by the mailed v1 patch); `good-frag.img`
(`REC_E=3`) is the same craft but with the extent record placed *inside*
already-decoded coverage, and is a regression control: the loop must
complete undisturbed (it then fails later, in `load_system_files()`,
identically with and without the series -- this image is a partial volume
that was never meant to mount end to end).  `gen-out-of-order.py` builds
three more 256 KiB volumes reusing `gen-loop-cycle.py`'s record builders:
`ooo-z16.img` (`Z=16`) and `ooo-z3.img` (`Z=3`) place a third `$DATA`
extent in a record that is itself only reachable through a second,
not-yet-covered extent, which self-deadlocks on the runlist rwsem (`Z=16`)
or crashes (`Z=3`, a general-protection fault in `map_extent_mft_record()`,
not root-caused, pre-existing on top of unrelated crafted damage);
`inorder-z16.img` (`Z=16 ORDER=inorder`) is the in-coverage control.
`gen-good.sh` reproduces (structurally; see "Provenance" below) six plain
`mkntfs` volumes of different geometries as mount-regression positive
controls.  `gen-frag.sh` (`fragment-mft.c`) builds `frag.img`: a real,
fully mountable 32 MiB volume, written only through libntfs-3g, whose $MFT
genuinely fragments into an attribute list with three extent records (709
runs, 13894 files) -- proof that the fix does not reject an ordinary
fragmented volume.  `straddle/` goes one step further: with clusters
smaller than a page, an extent record can sit in a folio that runs past the
decoded runlist even though the record itself is in a covered vcn range.
`straddle/gen-straddle.sh` relocates one of `frag.img`'s own extent records
(after freeing a record with `delete-file.c`) into that folio:
`straddle-self.img` triggers it (an unpatched kernel hangs; patch 3 alone
would zero-fill the folio's uncovered tail and silently serve a zeroed mft
record -- this is why patch 3 needs patch 1) and `straddle-other.img`/
`base-del.img` are same-shape controls that must mount unchanged
(`mftlib.py`/`dump-mft-al.py` decode the raw layout to confirm which case is
which; `make-straddle.py` does the relocation).

## Patch 4: do not map a vcn as a hole when its runlist lookup failed

`ntfs_attr_vcn_to_rl()` retries a failed lookup for any `lcn` up to
`LCN_RL_NOT_MAPPED`, which includes `LCN_ENOENT`, but patch 1 only turns a
failed retry into an error for `LCN_RL_NOT_MAPPED`; `LCN_ENOENT` still falls
through to a hole, which defeats patch 3's check for one specific layout.
`gen-sizes.py`'s `mft-short-rl` mode (applied to `mft-bootstrap/good/g32c512.img`,
producing `mft-short-rl.img`) cuts $MFT's single run short and zeroes
`highest_vcn`, so the vcn just past the cut hits `LCN_ENOENT` while still
well short of `allocated_size`; an unpatched kernel hangs on the folio lock
exactly as in patch 3, and patch 3 alone would drop the `-EIO` and read a
zeroed mft record instead of failing.  Patch 4 also carves out a
deliberate exception, tested by the same image: `LCN_ENOENT` *at or beyond*
`allocated_size` is the ordinary end of an attribute (reached on every read
of a file's last folio once clusters are smaller than a page) and must
still read as a hole, or an unrelated read during bootstrap would wrongly
trip patch 3's guard.

## Patch 5: fail the mount when $MFT's data size exceeds its allocation

`ntfs_read_inode_mount()` takes $MFT's `i_size` from `data_size` without
checking it against `allocated_size`, unlike the resident case elsewhere in
the driver.  `gen-sizes.py`'s `mft-shrink` mode (on `good/g32c512.img`,
producing `mft-shrink.img`) cuts $MFT's allocation to 4 clusters while
leaving `data_size`/`initialized_size` claiming 27 records; reading the
folio holding records 0-3 hits the end of the runlist inside that folio,
and an unpatched kernel hangs on the folio lock, exactly as in patch 3.
`mft-shrink-all.img` (`mft-shrink-all` mode) is the same cut with
`data_size`/`initialized_size` shrunk to match -- internally consistent,
but $MFT is still too small to hold the system files a mount needs, so it
hangs too; both fail cleanly once the whole series is applied.
`mft-ds-over.img` (`mft-ds-over` mode: `data_size`/`initialized_size` set
to `allocated_size + 65536`, i.e. the allocation is left alone but the
claimed size is inflated) does *not* hang either way -- the base kernel
mounts and reads it fine, because nothing the mount needs happens to live
past the real allocation -- but patch 5 is a deliberate, stricter
`data_size <= allocated_size` check for $MFT, so it now rejects the mount
outright.

## Patch 6: reject non-resident attributes whose sizes exceed their allocation

The same missing check (`0 <= initialized_size <= data_size <=
allocated_size`) is missing for every other non-resident attribute, read
through `ntfs_read_locked_inode()`, `ntfs_read_locked_attr_inode()` and
`ntfs_read_locked_index_inode()`.  `../mft-bootstrap/gen-sizes.py`'s
`file-ds-over`/`file-ds-over-noinit` modes and this directory's
`gen-p6.py` both edit sizes of one attribute on top of an existing image.
Against `../runlist-holes/clean.img`: `pad-ds-over.img`
(`file-ds-over`, both `data_size` and `initialized_size` past
`allocated_size`) reads 16 clusters of invented zeros with no error on an
unpatched kernel -- data claimed as "initialized" that is not actually on
disk. `pad-ds-over-noinit.img` (`file-ds-over-noinit`, only `data_size`
moved; `initialized_size` stays equal to `allocated_size`) reads the same
16 zero clusters, but was *not* considered a bug beforehand, because
`initialized_size == allocated_size` makes the tail look like an ordinary
uninitialized gap rather than invented data; patch 6 rejects it too, since
`data_size <= allocated_size` is violated either way, regardless of
`initialized_size`. `pad-init-over.img` (`gen-p6.py`,
`initialized_size` moved above `data_size` but still inside the
allocation) reads normally without the series and is a deliberate new
rejection with it; `mftbmp-ds-over.img` (`gen-p6.py`, $MFT's own
`$BITMAP`) exercises `ntfs_read_locked_attr_inode()` and now fails the
mount with "Failed to load $MFT/$BITMAP attribute"; `root-ia-ds-over.img`
(`gen-p6.py`, the root directory's `$INDEX_ALLOCATION`) exercises
`ntfs_read_locked_index_inode()` -- the mount still succeeds, but every
root lookup now gets `-EIO` instead of silently walking a corrupt index.
`ctl.img` (built with `mkimg.sh` + `mkctl.c`, a compressed file `/cz` and a
sparse file `/sp` written through libntfs-3g) is the base for
`cz-ds-over.img` and `sp-ds-over.img` (`gen-p6.py`, both `data_size`/
`initialized_size` past `allocated_size`): unpatched, `/sp` reads 16
invented zero pages with errno 0 and `/cz` fails with `-EIO` after a burst
of "Still have pages left!" from the decompressor; both are rejected
cleanly at lookup with patch 6.  `ctl.img` itself, unmodified, and
`expected.py`/`fnv.py` are the positive control: both kernels must read
`/cz` and `/sp` with the exact size and FNV-1a hash `expected.py` computes
independently of libntfs-3g.

## Building the images

Every generator here is plain Python 3 or a small C helper; none needs a
kernel or a mount.  The C helpers and three shell scripts
(`runlist-holes/gen-image.sh`, `mft-bootstrap/gen-good.sh`,
`mft-bootstrap/gen-frag.sh`, `mft-bootstrap/straddle/gen-straddle.sh`,
`sizes/mkimg.sh`, `sizes/build-mkctl.sh`) need a **built ntfs-3g source
tree** (`https://github.com/tuxera/ntfs-3g`, `./autogen.sh && ./configure
&& make`); point `N3G` at its root before running them.  For example, from a
clean checkout:

    N3G=/path/to/ntfs-3g
    bash runlist-holes/gen-image.sh runlist-holes/clean.img 1500
    bash runlist-holes/corrupt.sh runlist-holes/clean.img runlist-holes/corrupt-rec68.img 68
    bash runlist-holes/corrupt-attrlist.sh runlist-holes/clean.img runlist-holes/corrupt-attrlist-type.img type
    bash runlist-holes/corrupt-attrlist.sh runlist-holes/clean.img runlist-holes/corrupt-attrlist-lowvcn.img lowvcn

    python3 mft-bootstrap/gen-loop-cycle.py mft-bootstrap/loop-cycle.img
    REC_E=3 python3 mft-bootstrap/gen-loop-cycle.py mft-bootstrap/good-frag.img
    python3 mft-bootstrap/gen-out-of-order.py mft-bootstrap/ooo-z16.img
    Z=3 python3 mft-bootstrap/gen-out-of-order.py mft-bootstrap/ooo-z3.img
    Z=16 ORDER=inorder python3 mft-bootstrap/gen-out-of-order.py mft-bootstrap/inorder-z16.img
    N3G=$N3G bash mft-bootstrap/gen-good.sh mft-bootstrap/good
    N3G=$N3G bash mft-bootstrap/gen-frag.sh mft-bootstrap/frag.img
    python3 mft-bootstrap/gen-sizes.py mft-bootstrap/good/g32c512.img mft-bootstrap/mft-ds-over.img mft-ds-over 65536
    python3 mft-bootstrap/gen-sizes.py mft-bootstrap/good/g32c512.img mft-bootstrap/mft-shrink.img mft-shrink 4
    python3 mft-bootstrap/gen-sizes.py mft-bootstrap/good/g32c512.img mft-bootstrap/mft-shrink-all.img mft-shrink-all 4
    python3 mft-bootstrap/gen-sizes.py mft-bootstrap/good/g32c512.img mft-bootstrap/mft-short-rl.img mft-short-rl 4
    N3G=$N3G bash mft-bootstrap/straddle/gen-straddle.sh mft-bootstrap/frag.img mft-bootstrap/straddle

    python3 mft-bootstrap/gen-sizes.py runlist-holes/clean.img sizes/pad-ds-over.img file-ds-over 65 65536
    python3 mft-bootstrap/gen-sizes.py runlist-holes/clean.img sizes/pad-ds-over-noinit.img file-ds-over-noinit 65 65536
    python3 sizes/gen-p6.py runlist-holes/clean.img sizes/pad-init-over.img 65 80 - = a-65536 =
    python3 sizes/gen-p6.py runlist-holes/clean.img sizes/mftbmp-ds-over.img 0 b0 - = a+4096 a+4096
    python3 sizes/gen-p6.py runlist-holes/clean.img sizes/root-ia-ds-over.img 5 a0 '$I30' = a+4096 a+4096
    N3G=$N3G bash sizes/mkimg.sh sizes/ctl.img 64M
    N3G=$N3G bash sizes/build-mkctl.sh
    LD_LIBRARY_PATH=$N3G/libntfs-3g/.libs sizes/mkctl sizes/ctl.img
    python3 sizes/gen-p6.py sizes/ctl.img sizes/cz-ds-over.img 64 80 - = a+65536 a+65536
    python3 sizes/gen-p6.py sizes/ctl.img sizes/sp-ds-over.img 65 80 - = a+65536 a+65536

The `.img.xz` files already in this repository are the exact images the
series was tested against; `xz -d` them in place if you would rather use
those than rebuild.

### Provenance and what "reproduce" means here

`gen-loop-cycle.py`, `gen-out-of-order.py`, `gen-sizes.py`, `gen-p6.py` and
`corrupt.sh`/`corrupt-attrlist.sh` are pure record edits: given the same
input image they are **byte-for-byte deterministic**, and every image they
produce in this repository was regenerated and `cmp`-verified against the
one actually used in the VM tests before being committed.  `mkntfs` and
libntfs-3g, however, embed the current time and a random volume serial
number, so `gen-image.sh`, `gen-good.sh`, `gen-frag.sh` and
`gen-straddle.sh` reproduce an image's **layout and mount behaviour**, not
its exact bytes: re-running them was measured to differ from the shipped
`clean.img`/`good/*.img`/`frag.img`/`straddle/*.img` in only a handful of
timestamp and serial-number bytes, never in structure (confirmed with
`mftlib.py`/`dump-mft-al.py`, whose decoded layout matches exactly).  That is
why `clean.img.xz`, `good/*.img.xz`, `frag.img.xz` and
`straddle/*.img.xz` are shipped as images rather than left to be
regenerated, while every deterministic derivative (`corrupt-rec68.img`,
`mft-ds-over.img` and friends, `pad-ds-over.img` and friends,
`cz-ds-over.img`/`sp-ds-over.img`, ...) is not: run the corresponding
command above and it reproduces the shipped result exactly.
`mft-bootstrap/repro.img`/`repro-2.img` have no generator at all (found by
fuzzing) and are shipped as-is, uncompressed (28 KiB each).

## Testing

Build two kernels from the same `ntfs-next` base commit `259abb551e29`:
one unpatched ("without the series"), one with all six
`v2-000{1..6}-*.patch` from the series applied on top ("with the series").
Enable `CONFIG_KASAN`, `CONFIG_PROVE_LOCKING` and the hung-task detector
(`CONFIG_DETECT_HUNG_TASK=y`, `hung_task_timeout_secs=30` on the kernel
command line) -- several of these bugs are silent, unkillable mount hangs
at 0% CPU with no other symptom, and only the hung-task detector reports
them (`INFO: task mount:N blocked in I/O wait for more than 30 seconds`).
Boot each image under qemu with the crafted or built image attached as a
plain virtio disk, **snapshot mode on** (`-drive
file=IMAGE,format=raw,if=virtio,snapshot=on`) so nothing persists, and an
initramfs whose init mounts it (`mount -t ntfs -o ro /dev/vda /mnt`, or
`-o rw` for the patch 2 write test) and reports the result; e.g.:

    qemu-system-x86_64 -kernel bzImage -initrd initramfs.cpio.gz \
        -append "console=ttyS0 panic=-1 oops=panic" \
        -drive file=IMAGE,format=raw,if=virtio,snapshot=on \
        -m 1G -smp 1 -display none -serial stdio -no-reboot -cpu max -net none

Never attach any of these images to a real block device, and never run
this against a production host: several of them are designed to hang or
crash the mounting kernel.

## Expected results

| image(s) | without the series | with the series |
|---|---|---|
| loop-cycle, ooo-z16, repro, repro-2, straddle-self | hang (hung-task at 30s) | mount fails cleanly, no hang |
| ooo-z3 | general-protection fault in `map_extent_mft_record()` | mount fails cleanly |
| mft-short-rl, mft-shrink, mft-shrink-all | hang (hung-task at 30s) | mount fails cleanly |
| good-frag, inorder-z16 | `$DATA` loop completes, then fails in `load_system_files()` (partial volume, never mounts end to end) | identical failure |
| straddle-other, base-del | mounts; 13893 files, matching listing/content hashes | unchanged |
| corrupt-rec68, corrupt-attrlist-type | reads 1285 clusters of zeros, no error | `-EIO` from vcn 215 onward |
| corrupt-attrlist-lowvcn | reads correctly (no-op corruption) | unchanged |
| corrupt-rec68, buffered write into the unreadable range | write "succeeds", data lost | write fails with `-EIO` |
| pad-ds-over, sp-ds-over | 16 invented zero clusters/pages, no error | lookup fails, "is corrupt" |
| cz-ds-over | `-EIO` after a burst of "Still have pages left!" | lookup fails cleanly |
| mft-ds-over, pad-ds-over-noinit, pad-init-over, mftbmp-ds-over | mounts, reads normally | rejected (deliberate stricter check) |
| root-ia-ds-over | mounts; root lookups silently walk a corrupt index | mounts; root lookups fail with `-EIO` |
| good/g8, g16, g64, g32c512, g16lbl, g16c2048 | mount | mount (unchanged) |
| frag | mounts; 13894 files, matching listing/content hashes | unchanged |
| ctl | mounts; `/cz`, `/sp` read with the expected size and FNV-1a hash | unchanged |
| clean | reads correctly | unchanged |

These are the outcomes measured on 2026-09-26, as reported in the series'
cover letter; this tag only carries the reproducers.

## Layout

    runlist-holes/    patches 1, 2: LCN_RL_NOT_MAPPED / early LCN_ENOENT read as a hole
    mft-bootstrap/    patches 3, 4, 5: $MFT bootstrap re-entrancy and size checks
      good/             plain mkntfs volumes (mount-regression positive controls)
      straddle/         the folio-straddles-coverage-end case
    sizes/            patch 6: non-resident attribute size invariant
