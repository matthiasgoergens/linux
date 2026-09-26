#!/usr/bin/env python3
"""
dump-mft-al.py <image> -- decode $MFT's attribute list and $DATA extents
straight from the raw image (no ntfs-3g, no kernel), in the same order the
kernel's ntfs_read_inode_mount() bootstrap walks them.

For each $DATA extent: which MFT record describes it, its lowest..highest
vcn, how many runs it has, where that record itself lives in $MFT (vcn),
and whether that vcn was already covered by the extents decoded BEFORE it.
"covered=no" on any extent other than the first is the out-of-order /
self-referential layout that v2 refuses; "covered=yes" everywhere is the
layout v2 must keep mounting.
"""
import struct, sys


def fixup(rec, sector):
    rec = bytearray(rec)
    usa_ofs, usa_cnt = struct.unpack_from("<HH", rec, 4)
    usn = rec[usa_ofs:usa_ofs + 2]
    for i in range(1, usa_cnt):
        end = i * sector - 2
        assert rec[end:end + 2] == usn, "MST fixup mismatch"
        rec[end:end + 2] = rec[usa_ofs + 2 * i:usa_ofs + 2 * i + 2]
    return bytes(rec)


def attrs(rec):
    off = struct.unpack_from("<H", rec, 20)[0]
    while True:
        t, ln = struct.unpack_from("<II", rec, off)
        if t == 0xffffffff:
            return
        yield t, rec[off:off + ln]
        off += ln


def runs(a):
    """Decode mapping pairs -> list of (vcn, length, lcn)."""
    lo = struct.unpack_from("<q", a, 16)[0]
    mp = struct.unpack_from("<H", a, 32)[0]
    out, vcn, lcn, i = [], lo, 0, mp
    while a[i]:
        h = a[i]; ll, ol = h & 0xf, h >> 4
        length = int.from_bytes(a[i + 1:i + 1 + ll], "little", signed=True)
        if ol:
            lcn += int.from_bytes(a[i + 1 + ll:i + 1 + ll + ol], "little",
                                  signed=True)
            out.append((vcn, length, lcn))
        else:
            out.append((vcn, length, None))
        vcn += length
        i += 1 + ll + ol
    return out


def main():
    img = open(sys.argv[1], "rb").read()
    sector = struct.unpack_from("<H", img, 11)[0]
    cluster = sector * img[13]
    mft_lcn = struct.unpack_from("<Q", img, 48)[0]
    cpr = struct.unpack_from("<b", img, 64)[0]
    mrs = cluster * cpr if cpr > 0 else 1 << -cpr
    print("sector=%d cluster=%d mft_record_size=%d mft_lcn=%d" %
          (sector, cluster, mrs, mft_lcn))

    rl = []   # decoded so far: (vcn, length, lcn)

    def vcn_to_off(vcn):
        for v, n, l in rl:
            if v <= vcn < v + n and l is not None:
                return (l + vcn - v) * cluster
        return None

    def read_rec(no, bootstrap=False):
        off = mft_lcn * cluster + no * mrs if bootstrap else \
            vcn_to_off(no * mrs // cluster)
        assert off is not None
        return fixup(img[off:off + mrs], sector)

    r0 = read_rec(0, bootstrap=True)
    al = None
    for t, a in attrs(r0):
        if t == 0x20:
            if a[8] == 0:
                vo, vl = struct.unpack_from("<H", a, 20)[0], \
                    struct.unpack_from("<I", a, 16)[0]
                al = a[vo:vo + vl]
                print("attribute list: resident, %d bytes" % vl)
            else:
                size = struct.unpack_from("<Q", a, 48)[0]
                buf = b"".join(img[l * cluster:(l + n) * cluster]
                               for _, n, l in runs(a))
                al = buf[:size]
                print("attribute list: NON-resident, %d bytes" % size)
    if al is None:
        print("NO ATTRIBUTE LIST: $MFT/$DATA is entirely in record 0")
        return
    ents, off = [], 0
    while off < len(al):
        t, ln = struct.unpack_from("<IH", al, off)
        if ln == 0:
            break
        lvcn = struct.unpack_from("<q", al, off + 8)[0]
        ref = struct.unpack_from("<Q", al, off + 16)[0]
        ents.append((t, lvcn, ref & 0xffffffffffff, ref >> 48))
        off += ln
    print("attribute list entries:")
    for t, lvcn, rec, seq in ents:
        print("  type 0x%02x lowest_vcn %-6d in record %d (seq %d)" %
              (t, lvcn, rec, seq))
    print("$DATA extents in bootstrap order:")
    total_runs, ext_in_extent_recs, uncovered = 0, 0, 0
    for t, lvcn, rec, seq in ents:
        if t != 0x80:
            continue
        rec_vcn = rec * mrs // cluster
        covered = rec == 0 or vcn_to_off(rec_vcn) is not None
        # An uncovered record cannot be located from the runlist decoded
        # so far -- that is the whole point.  Read it assuming a physically
        # contiguous $MFT (true of the crafted images) and say so.
        r = read_rec(rec, bootstrap=(rec == 0 or not covered))
        for at, a in attrs(r):
            if at == 0x80 and struct.unpack_from("<q", a, 16)[0] == lvcn:
                lo, hi = struct.unpack_from("<qq", a, 16)
                rr = runs(a)
                break
        else:
            raise SystemExit("extent vcn %d not found in record %d" %
                             (lvcn, rec))
        print("  record %-3d vcn %6d..%-6d runs %-4d record at vcn %-6d "
              "covered=%s" % (rec, lo, hi, len(rr), rec_vcn,
                              "yes" if covered else
                              "NO (read assuming contiguous $MFT)"))
        total_runs += len(rr)
        if rec != 0:
            ext_in_extent_recs += 1
        if not covered:
            uncovered += 1
        rl.extend(rr)
    print("SUMMARY data_extents_in_extent_records=%d total_runs=%d "
          "uncovered=%d mft_records=%d" %
          (ext_in_extent_recs, total_runs, uncovered,
           (rl[-1][0] + rl[-1][1]) * cluster // mrs))


if __name__ == "__main__":
    main()
