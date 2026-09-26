# fs/ntfs v2: two more crafted images, and sub-page-cluster controls

Companion to `reproducer/2026-09-26-ntfs-mft-runlist`, for the same
six-patch series against the in-kernel `ntfs` driver (`fs/ntfs`), based on
`ntfs-next` at `259abb551e29`.  That tag's images and scripts are unchanged;
this one covers two more cases and adds a control:

- **Patch 4** (`ntfs: do not map a vcn as a hole when its runlist lookup
  failed`) also fails an ordinary file whose single extent (highest_vcn
  0) has mapping pairs that end before its allocated_size.
  `r1-short-rl.img`: `/pad` is a 64-cluster file (4 KiB clusters) whose
  mapping pairs stop after 16 clusters, sizes unchanged.
- **Patch 6** (`ntfs: reject non-resident attributes whose sizes exceed
  their allocation`) also rejects an allocated_size that is not a
  multiple of the cluster size.  `r2-alloc-unaligned.img`: `/pad` has
  allocated_size = data_size = initialized_size = 66440 (16 clusters + 904
  bytes) and mapping pairs for 16 clusters, highest_vcn 0.
- `c512.img` (512-byte clusters, so a folio spans eight clusters and a
  file's last folio usually runs past allocated_size) is a regression
  control for the stricter lookup: odd-sized files, shrink and extend,
  fallocate with and without KEEP_SIZE, sparse and compressed files
  (`init-odd`), plus the series' zero-read regression script
  (`init-regress`, unchanged) on this geometry.
- `sp512.img` (512-byte clusters, `mksparse.c`): `/sp` is a sparse file
  whose $DATA spans 18 extent records.  `init-partial` writes into its
  last extent before anything else maps the runlist, so only that extent
  is mapped, and then reads the last folio, which looks up the vcn at
  allocated_size.  That lookup must not fail.
- `init-dsparse` (on `base64.img`, mounted with `disable_sparse`) writes
  and reads compressed files.  Not a test of the series: it shows that
  this already fails on ntfs-next, identically with the series.

Everything is built in userspace (mkntfs/libntfs-3g and raw record edits);
nothing here was mounted on a host.

## Building the images

`gen-base.sh` needs a built ntfs-3g tree
(`https://github.com/tuxera/ntfs-3g`, `./autogen.sh && ./configure &&
make`).  mkntfs embeds the time and a random serial number, so it
reproduces the layout of the shipped `base64.img.xz` and `c512.img.xz`, not
their bytes.  `gen-extra.py` is a deterministic record edit: run on the
shipped `base64.img` it reproduces `r1-short-rl.img` and
`r2-alloc-unaligned.img` exactly (`MANIFEST.sha256`, `gen-extra.log`).

    N3G=/path/to/ntfs-3g
    N3G=$N3G ./gen-base.sh base64.img 4096 64       # or: xz -dk base64.img.xz
    N3G=$N3G ./gen-base.sh c512.img 512 1500        # or: xz -dk c512.img.xz
    ./gen-extra.py base64.img r1-short-rl.img short-rl 65 16
    ./gen-extra.py base64.img r2-alloc-unaligned.img alloc-unaligned 65 16 904
    # sp512.img: fresh mkntfs with 512-byte clusters, then
    #   mksparse sp512.img 3000 700   (build like gen-base.sh builds mkfrag;
    #   or: xz -dk sp512.img.xz)

Record 65 is `/pad`; `/victim` (record 64) is left alone as a control.  Both
files hold self-describing content (every 8-byte word is `(id << 56) |
offset`, id 0x50 for pad, 0x56 for victim), which `rdcheck.c` classifies
per block as OK, ZERO, BAD or ERR.  `rdcheck.c` is the tag's
`runlist-holes/rdcheck.c`, changed to classify a final partial block too;
`rt.c` and `mkfrag.c` are unchanged copies.

## Running

    ./build-initramfs.sh init-ro initramfs-ro.cpio.gz <busybox-rootfs>
    ./build-initramfs.sh init-odd initramfs-odd.cpio.gz <busybox-rootfs>
    ./build-initramfs.sh init-regress initramfs-regress.cpio.gz <busybox-rootfs>
    ./run-vm.sh bzImage r1-short-rl.img initramfs-ro.cpio.gz r1.log
    ./run-vm.sh bzImage c512.img initramfs-odd.cpio.gz c512-odd.log 600

`run-vm.sh` attaches the image with `snapshot=on`.  The kernels were built
with KASAN, PROVE_LOCKING and the hung-task detector.

## Results (2026-09-26)

| image | ntfs-next | series without these two checks | series |
|---|---|---|---|
| r1-short-rl | `/pad` vcn 16-63 read as zeros, no error | same zeros | vcn 16-63 fail with EIO |
| r2-alloc-unaligned | last 904 bytes of `/pad` read as zeros | same zeros | lookup of `/pad` fails with EIO, "is corrupt" |
| base64 | both files read OK | same | same |
| c512 (init-odd, init-regress) | reference | not run | output identical to ntfs-next |
| sp512 (init-partial) | reads OK | reads OK | output identical to ntfs-next |
| base64 (init-dsparse) | compressed writes fail (EINVAL/EIO), reads EIO | not run | output identical to ntfs-next |

`/victim` reads OK on every run.
