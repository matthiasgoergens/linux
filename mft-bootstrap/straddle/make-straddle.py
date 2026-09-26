#!/usr/bin/env python3
"""
make-straddle.py <in.img> <fresh.img> <out.img> <src-record> <dst-record>

Relocate the $MFT extent record <src-record> (which holds one $DATA extent
of $MFT) into the free $MFT record <dst-record>, touching only what a writer
that had chosen <dst-record> in the first place would have written:

  - dst slot  := the src record's on-disk bytes, with the header's
                 sequence number set to dst's current (free) sequence number
                 and mft_record_number set to dst;
  - src slot  := the same record from a freshly mkntfs'ed volume of the same
                 geometry (record 15: reserved, in use, empty; record 17:
                 free), i.e. what it was before libntfs-3g used it;
  - $MFT's attribute list entry for that extent := reference dst/seq;
  - $MFT/$BITMAP: dst set; src set or cleared to match the fresh record.

Only the two 1 KiB records, one 8-byte attribute-list field and two bits
change.  The two header fields edited are not at sector ends, so the
update-sequence array stays valid.  Nothing is mounted anywhere.
"""
import struct, sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import mftlib


def main():
    inp, fresh, out, src, dst = sys.argv[1:4] + [int(x) for x in sys.argv[4:6]]
    v = mftlib.Vol(inp)
    f = mftlib.Vol(fresh)
    assert (v.cluster, v.mrs, v.mft_lcn) == (f.cluster, f.mrs, f.mft_lcn)

    dst_rec = v.rec(dst)
    assert struct.unpack_from("<H", dst_rec, 22)[0] & 1 == 0, "dst in use"
    dst_seq = struct.unpack_from("<H", dst_rec, 16)[0]

    # Which extent does src hold?
    ents = [e for e in v.al_entries() if e[0] == 0x80 and e[2] == src]
    assert len(ents) == 1, ents
    _, lvcn, _, _, al_off = ents[0]

    raw = bytearray(v.raw_rec(src))
    struct.pack_into("<H", raw, 16, dst_seq)
    struct.pack_into("<I", raw, 44, dst)
    v.write_raw_rec(dst, raw)

    # src slot back to what mkntfs wrote.  Record 15..23 live in the
    # initial contiguous $MFT run in both volumes (vcn 0..53 -> lcn 32).
    fresh_raw = f.raw_rec(src)
    v.write_raw_rec(src, fresh_raw)
    fresh_in_use = struct.unpack_from("<H", f.rec(src), 22)[0] & 1

    # Attribute list entry.
    (_, _, al_lcn), = v.al_runs
    pos = al_lcn * v.cluster + al_off + 16
    struct.pack_into("<Q", v.img, pos, dst | (dst_seq << 48))

    # $MFT/$BITMAP (attribute 0xb0 of record 0, non-resident).
    r0 = v.rec(0)
    bm = [a for t, a in mftlib.attrs(r0) if t == 0xb0][0]
    (_, _, bm_lcn), = mftlib.runs(bm)
    base = bm_lcn * v.cluster

    def setbit(n, on):
        b = base + n // 8
        if on:
            v.img[b] |= 1 << (n % 8)
        else:
            v.img[b] &= ~(1 << (n % 8)) & 0xff

    setbit(dst, True)
    setbit(src, bool(fresh_in_use))
    open(out, "wb").write(v.img)
    print("moved $MFT/$DATA extent lowest_vcn %d: record %d -> record %d "
          "(seq %d); record %d restored from %s (%s); AL entry @%d updated"
          % (lvcn, src, dst, dst_seq, src, fresh,
             "in use" if fresh_in_use else "free", al_off))


if __name__ == "__main__":
    main()
