#!/usr/bin/env python3
"""
gen-extra.py <in.img> <out.img> <mode> <recno> [args] -- craft the two extra
images for the fs/ntfs v2 series.  Pure userspace and deterministic: edits the
unnamed non-resident $DATA of base mft record <recno> in place, with the MST
fixups re-applied.

modes
  short-rl <recno> <nclu>
      Cut the mapping pairs so they describe only the first <nclu> clusters
      and set highest_vcn to 0, keeping allocated_size, data_size and
      initialized_size.  The attribute then looks like a single-extent
      attribute whose runlist ends inside its allocation.
  alloc-unaligned <recno> <nclu> <extra>
      Cut the mapping pairs to <nclu> clusters, set highest_vcn to 0 and set
      allocated_size = data_size = initialized_size = <nclu> clusters +
      <extra> bytes (0 < extra < cluster size), i.e. an allocated_size that
      is not a multiple of the cluster size.
"""
import struct
import sys


def unfix(rec, sector):
    rec = bytearray(rec)
    uo, uc = struct.unpack_from("<HH", rec, 4)
    usn = rec[uo:uo + 2]
    for i in range(1, uc):
        e = i * sector - 2
        assert rec[e:e + 2] == usn, "MST fixup mismatch"
        rec[e:e + 2] = rec[uo + 2 * i:uo + 2 * i + 2]
    return rec


def refix(rec, sector):
    rec = bytearray(rec)
    uo, uc = struct.unpack_from("<HH", rec, 4)
    usn = rec[uo:uo + 2]
    for i in range(1, uc):
        e = i * sector - 2
        rec[uo + 2 * i:uo + 2 * i + 2] = rec[e:e + 2]
        rec[e:e + 2] = usn
    return bytes(rec)


def attrs(rec):
    off = struct.unpack_from("<H", rec, 20)[0]
    while True:
        t, ln = struct.unpack_from("<II", rec, off)
        if t == 0xffffffff:
            return
        yield t, off, ln
        off += ln


def runs(rec, aoff):
    """(vcn, length, lcn or None, offset of header byte, length bytes, lcn bytes)"""
    i = aoff + struct.unpack_from("<H", rec, aoff + 32)[0]
    out, vcn, lcn = [], 0, 0
    while rec[i]:
        h = rec[i]
        ll, ol = h & 0xf, h >> 4
        ln = int.from_bytes(rec[i + 1:i + 1 + ll], "little", signed=True)
        if ol:
            lcn += int.from_bytes(rec[i + 1 + ll:i + 1 + ll + ol], "little", signed=True)
            out.append((vcn, ln, lcn, i, ll, ol))
        else:
            out.append((vcn, ln, None, i, ll, ol))
        vcn += ln
        i += 1 + ll + ol
    return out


class Img:
    def __init__(self, path):
        self.b = bytearray(open(path, "rb").read())
        b = self.b
        self.sector = struct.unpack_from("<H", b, 11)[0]
        self.cluster = self.sector * b[13]
        self.mft_lcn, self.mirr_lcn = struct.unpack_from("<QQ", b, 48)
        cpr = struct.unpack_from("<b", b, 64)[0]
        self.mrs = self.cluster * cpr if cpr > 0 else 1 << -cpr
        r0 = unfix(b[self.mft_lcn * self.cluster:][:self.mrs], self.sector)
        a = [o for t, o, l in attrs(r0) if t == 0x80][0]
        self.rl = [(v, n, l) for v, n, l, *_ in runs(r0, a)]

    def rec_off(self, no):
        vcn, within = divmod(no * self.mrs, self.cluster)
        for v, n, l in self.rl:
            if v <= vcn < v + n:
                return (l + vcn - v) * self.cluster + within
        raise ValueError("record %d not in $MFT runlist" % no)

    def get(self, no):
        o = self.rec_off(no)
        return unfix(self.b[o:o + self.mrs], self.sector)

    def put(self, no, rec):
        assert no >= 4, "records 0-3 would also need $MFTMirr"
        o = self.rec_off(no)
        self.b[o:o + self.mrs] = refix(rec, self.sector)


def data_attr(rec):
    for t, o, l in attrs(rec):
        if t == 0x80 and rec[o + 8] == 1 and rec[o + 9] == 0:
            return o
    raise ValueError("no unnamed non-resident $DATA")


def filename_of(rec):
    for t, o, l in attrs(rec):
        if t == 0x30 and rec[o + 8] == 0:
            fn = rec[o + struct.unpack_from("<H", rec, o + 20)[0]:]
            return bytes(fn[66:66 + 2 * fn[64]]).decode("utf-16-le")
    return None


def describe(rec, a):
    al, ds, ini = struct.unpack_from("<QQQ", rec, a + 40)
    lo, hi = struct.unpack_from("<qq", rec, a + 16)
    rs = [r[:3] for r in runs(rec, a)]
    end = rs[-1][0] + rs[-1][1] if rs else 0
    return ("lowest_vcn %d highest_vcn %d alloc %d data %d init %d, "
            "%d runs ending at vcn %d, first %r last %r"
            % (lo, hi, al, ds, ini, len(rs), end, rs[:1], rs[-1:]))


def cut_pairs(rec, a, nclu):
    """Cut the mapping pairs after the first nclu clusters."""
    rs = runs(rec, a)
    last = rs[-1]
    assert 0 < nclu < last[0] + last[1], "nclu must be inside the runlist"
    for vcn, ln, lcn, i, ll, ol in rs:
        if vcn + ln >= nclu:
            rec[i + 1:i + 1 + ll] = (nclu - vcn).to_bytes(ll, "little", signed=True)
            end = i + 1 + ll + ol
            old_end = last[3] + 1 + last[4] + last[5]
            rec[end:old_end + 1] = bytes(old_end + 1 - end)
            break
    struct.pack_into("<q", rec, a + 24, 0)


def main():
    src, dst, mode, recno = sys.argv[1:5]
    args = [int(x) for x in sys.argv[5:]]
    recno = int(recno)
    img = Img(src)
    rec = img.get(recno)
    assert struct.unpack_from("<H", rec, 22)[0] & 1, "record not in use"
    assert struct.unpack_from("<Q", rec, 32)[0] == 0, "not a base record"
    a = data_attr(rec)
    assert struct.unpack_from("<q", rec, a + 16)[0] == 0, "not the first extent"
    print("in %s: cluster %d, record %d (%s)" % (src, img.cluster, recno, filename_of(rec)))
    print("before: " + describe(rec, a))
    if mode == "short-rl":
        (nclu,) = args
        cut_pairs(rec, a, nclu)
    elif mode == "alloc-unaligned":
        nclu, extra = args
        assert 0 < extra < img.cluster
        cut_pairs(rec, a, nclu)
        size = nclu * img.cluster + extra
        struct.pack_into("<QQQ", rec, a + 40, size, size, size)
    else:
        sys.exit("bad mode")
    print("after:  " + describe(rec, a))
    img.put(recno, rec)
    open(dst, "wb").write(img.b)


if __name__ == "__main__":
    main()
