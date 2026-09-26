#!/usr/bin/env python3
"""
gen-sizes.py <in.img> <out.img> <mode> [args] -- craft NTFS images whose
non-resident $DATA sizes disagree with the runlist (patches 4 to 6).  Pure userspace; edits raw MFT records with MST
fixups re-applied.  Record 0 edits are copied to $MFTMirr too, so the mirror
check does not mask the effect.

modes
  mft-ds-over <extra>     $MFT: data_size = initialized_size = allocated_size + extra
  mft-shrink <nclu>       $MFT: single run cut to <nclu> clusters, highest_vcn and
                          allocated_size to match; data_size/initialized_size kept
  mft-shrink-all <nclu>   as mft-shrink, but data_size = initialized_size = allocated_size
  mft-short-rl <nclu>     $MFT: single run cut to <nclu> clusters and highest_vcn = 0;
                          allocated_size, data_size, initialized_size kept
  file-ds-over <recno> <extra>  base record <recno> of a file: data_size =
                          initialized_size = allocated_size + extra
  file-ds-over-noinit <recno> <extra>  as file-ds-over, initialized_size unchanged
"""
import struct, sys


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
    mp = aoff + struct.unpack_from("<H", rec, aoff + 32)[0]
    out, vcn, lcn, i = [], 0, 0, mp
    while rec[i]:
        h = rec[i]; ll, ol = h & 0xf, h >> 4
        ln = int.from_bytes(rec[i + 1:i + 1 + ll], "little", signed=True)
        if ol:
            lcn += int.from_bytes(rec[i + 1 + ll:i + 1 + ll + ol], "little", signed=True)
            out.append((vcn, ln, lcn, i, ll, ol))
        else:
            out.append((vcn, ln, None, i, ll, ol))
        vcn += ln
        i += 1 + ll + ol
    assert vcn - 1 == struct.unpack_from("<q", rec, aoff + 24)[0] or \
        struct.unpack_from("<q", rec, aoff + 24)[0] == 0 or True
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
        byte = no * self.mrs
        vcn, within = divmod(byte, self.cluster)
        assert self.mrs <= self.cluster or True
        for v, n, l in self.rl:
            if v <= vcn < v + n:
                return (l + vcn - v) * self.cluster + within
        raise ValueError("record %d not in $MFT runlist" % no)

    def get(self, no):
        o = self.rec_off(no)
        return unfix(self.b[o:o + self.mrs], self.sector)

    def put(self, no, rec):
        o = self.rec_off(no)
        self.b[o:o + self.mrs] = refix(rec, self.sector)
        if no < 4:
            m = self.mirr_lcn * self.cluster + no * self.mrs
            self.b[m:m + self.mrs] = refix(rec, self.sector)


def data_attr(rec):
    for t, o, l in attrs(rec):
        if t == 0x80 and rec[o + 8] == 1 and rec[o + 9] == 0:
            return o
    raise ValueError("no unnamed non-resident $DATA")


def sizes(rec, a):
    return struct.unpack_from("<QQQ", rec, a + 40)   # allocated, data, initialized


def set_sizes(rec, a, alloc, data, init):
    struct.pack_into("<QQQ", rec, a + 40, alloc, data, init)


def cut_single_run(img, rec, a, nclu, hvcn):
    rs = runs(rec, a)
    assert len(rs) == 1, "only single-run $MFT handled, got %r" % (rs,)
    v, ln, l, i, ll, ol = rs[0]
    assert nclu < ln and nclu < (1 << (8 * ll - 1))
    rec[i + 1:i + 1 + ll] = nclu.to_bytes(ll, "little", signed=True)
    struct.pack_into("<q", rec, a + 24, hvcn)


def filename_of(rec):
    for t, o, l in attrs(rec):
        if t == 0x30 and rec[o + 8] == 0:
            vo = struct.unpack_from("<H", rec, o + 20)[0]
            fn = rec[o + vo:]
            return bytes(fn[66:66 + 2 * fn[64]]).decode("utf-16-le")
    return None


def short(rs):
    if len(rs) <= 4:
        return repr(rs)
    return "%d runs %r .. %r" % (len(rs), rs[:2], rs[-2:])


def main():
    src, dst, mode = sys.argv[1:4]
    args = sys.argv[4:]
    img = Img(src)
    print("in %s: sector %d cluster %d mrs %d mft_lcn %d mirr_lcn %d $MFT rl %r"
          % (src, img.sector, img.cluster, img.mrs, img.mft_lcn, img.mirr_lcn, img.rl))
    if mode.startswith("mft-"):
        no = 0
        rec = img.get(0)
        a = data_attr(rec)
        al, ds, ini = sizes(rec, a)
        print("before: rec 0 $DATA alloc %d data %d init %d highest_vcn %d"
              % (al, ds, ini, struct.unpack_from("<q", rec, a + 24)[0]))
        if mode == "mft-ds-over":
            x = int(args[0]); set_sizes(rec, a, al, al + x, al + x)
        elif mode in ("mft-shrink", "mft-shrink-all"):
            n = int(args[0]); cut_single_run(img, rec, a, n, n - 1)
            na = n * img.cluster
            if mode == "mft-shrink":
                set_sizes(rec, a, na, ds, ini)
            else:
                set_sizes(rec, a, na, na, na)
        elif mode == "mft-short-rl":
            n = int(args[0]); cut_single_run(img, rec, a, n, 0)
        else:
            sys.exit("bad mode")
    else:
        no, x = int(args[0]), int(args[1])
        rec = img.get(no)
        name = "record %d" % no
        a = data_attr(rec)
        al, ds, ini = sizes(rec, a)
        print("before: rec %d (%s) $DATA alloc %d data %d init %d runs %r"
              % (no, name, al, ds, ini, short([r[:3] for r in runs(rec, a)])))
        if mode == "file-ds-over":
            set_sizes(rec, a, al, al + x, al + x)
        elif mode == "file-ds-over-noinit":
            set_sizes(rec, a, al, al + x, ini)
        else:
            sys.exit("bad mode")
    al, ds, ini = sizes(rec, a)
    print("after:  rec %d $DATA alloc %d data %d init %d highest_vcn %d runs %r"
          % (no, al, ds, ini, struct.unpack_from("<q", rec, a + 24)[0],
             short([r[:3] for r in runs(rec, a)])))
    img.put(no, rec)
    open(dst, "wb").write(img.b)


if __name__ == "__main__":
    main()
