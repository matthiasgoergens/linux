#!/usr/bin/env python3
"""Generate a big, writable, mostly-empty AFFS (FFS, DOS\\1) image.

512-byte blocks, big-endian. Layout:
  block 0..1    boot/reserved, "DOS\\1" signature (FFS: data blocks are raw)
  block 2..24   bitmap blocks (23 needed for 92160 blocks @ 4064 bits each)
  block 46080   root block = (reserved + nblocks - 1) / 2, as the kernel computes
  everything else: free data blocks

The root block carries bm_flag = 0xffffffff (bitmap valid -> mount read-write)
and bm_blk[] pointing at the 23 bitmap blocks. Bitmap bit i (1 = free,
0 = used) covers block RESERVED+i; word 0 of each bitmap block is the
checksum (negative sum of all words). 25 root bm_blk slots suffice, so no
bitmap extension block is needed.

Bit layout per fs/affs/bitmap.c: blk = block - reserved;
bmap = blk / s_bmap_bits (4064); bit = blk % s_bmap_bits; word = bit/32 + 1.
"""
import struct, sys

BS = 512
HS = 72                     # hashsize = BS/4 - 56
T_SHORT = 2
ST_ROOT = 1
NBLOCKS = 92160             # 45 MiB; needs 23 bitmap blocks (<= 25 root slots)
RESERVED = 2
BMAP_BITS = BS * 8 - 32     # 4064 blocks per bitmap block (kernel: s_bmap_bits)

def be32s(vals):
    return b"".join(struct.pack(">I", v & 0xffffffff) for v in vals)

def set_checksum(block, off=20):
    words = list(struct.unpack(">%dI" % (BS // 4), block))
    words[off // 4] = 0
    words[off // 4] = (-sum(words)) & 0xffffffff
    return be32s(words)

def pname(name):
    name = name.encode()[:30]
    return bytes([len(name)]) + name

def root_block(diskname, bm_keys):
    tail = struct.pack(">I", 0xffffffff)               # bm_flag: bitmap valid
    bm = list(bm_keys) + [0] * (25 - len(bm_keys))
    tail += struct.pack(">25I", *bm)                   # bm_blk[25]
    tail += struct.pack(">I", 0)                       # bm_ext: none
    tail += struct.pack(">III", 0, 0, 0)               # root_change
    tail += pname(diskname).ljust(32, b"\0")           # disk_name
    tail += struct.pack(">II", 0, 0)                   # spare1, spare2
    tail += struct.pack(">III", 0, 0, 0) * 2           # disk_change, disk_create
    tail += struct.pack(">II", 0, 0)                   # spare3, spare4
    tail += struct.pack(">I", 0)                       # dcache
    tail += struct.pack(">I", ST_ROOT)                 # stype
    head = struct.pack(">IIIII", T_SHORT, 0, 0, HS, 0)
    table = [0] * HS               # empty root directory
    body = head + b"\0" * 4 + be32s(table)
    blk = (body + b"\0" * (BS - 200 - len(body)) + tail)
    assert len(blk) == BS
    return set_checksum(blk)

def bitmap_block(covered, rel_used):
    """covered: number of real blocks this bitmap block describes;
    rel_used: set of bit indices (block - RESERVED - base) that are used.
    bit 1 = free, 0 = used; word 0 = checksum."""
    words = [0] * (BS // 4)
    for i in range(min(covered, BMAP_BITS)):
        if i not in rel_used:
            words[i // 32 + 1] |= 1 << (i % 32)
    words[0] = (-sum(words)) & 0xffffffff
    return be32s(words)

def build():
    nb = NBLOCKS
    img = bytearray(nb * BS)
    root = (RESERVED + nb - 1) // 2
    nbm = (nb - RESERVED + BMAP_BITS - 1) // BMAP_BITS
    print("blocks=%d root=%d bitmap_blocks=%d" % (nb, root, nbm))
    assert nbm <= 25, "need bitmap extension chain"

    bm_keys = list(range(RESERVED, RESERVED + nbm))     # blocks 2..2+nbm-1
    used = set(bm_keys) | {root}   # blocks 0,1 are outside the bitmap's range

    img[0:4] = b"DOS\x01"   # DOS\1 = FFS (raw data blocks)
    img[root * BS:(root + 1) * BS] = root_block("affstest", bm_keys)

    for k in bm_keys:
        base = (k - RESERVED) * BMAP_BITS          # first bit index of this block
        covered = min(BMAP_BITS, nb - RESERVED - base)
        rel_used = {b - RESERVED - base for b in used
                    if base <= b - RESERVED < base + BMAP_BITS}
        img[k * BS:(k + 1) * BS] = bitmap_block(covered, rel_used)
    return bytes(img)

def main():
    out = sys.argv[1]
    img = build()
    with open(out, "wb") as f:
        f.write(img)
    print(out, len(img))

if __name__ == "__main__":
    main()
