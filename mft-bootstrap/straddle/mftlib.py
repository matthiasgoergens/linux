#!/usr/bin/env python3
"""
mftlib.py -- minimal raw NTFS $MFT reader for the straddle images (no
ntfs-3g, no kernel).  Decodes $MFT's complete $DATA runlist from record 0
and its attribute list, then reads any record by number.

  mftlib.py <image> show <rec> [<rec> ...]   one line per record
  mftlib.py <image> layout                  extents + per-folio coverage
"""
import struct, sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from importlib import import_module
_d = import_module("dump-mft-al")
fixup, attrs, runs = _d.fixup, _d.attrs, _d.runs

PAGE = 4096


class Vol:
    def __init__(self, path):
        self.path = path
        self.img = bytearray(open(path, "rb").read())
        img = self.img
        self.sector = struct.unpack_from("<H", img, 11)[0]
        self.cluster = self.sector * img[13]
        self.mft_lcn = struct.unpack_from("<Q", img, 48)[0]
        cpr = struct.unpack_from("<b", img, 64)[0]
        self.mrs = self.cluster * cpr if cpr > 0 else 1 << -cpr
        self.rl = []
        r0 = self.raw_rec_at(self.mft_lcn * self.cluster)
        self.al, self.al_runs, self.al_size = None, None, 0
        for t, a in attrs(r0):
            if t == 0x80:
                self.rl.extend(runs(a))
            if t == 0x20:
                assert a[8] == 1, "resident AL not handled"
                self.al_size = struct.unpack_from("<Q", a, 48)[0]
                self.al_runs = runs(a)
                buf = b"".join(bytes(img[l * self.cluster:(l + n) * self.cluster])
                               for _, n, l in self.al_runs)
                self.al = buf[:self.al_size]
        self.extents = []   # (record, lowest_vcn, highest_vcn, nruns)
        for t, lvcn, rec, seq, off in self.al_entries():
            if t != 0x80:
                continue
            if rec == 0:
                a = [a for at, a in attrs(r0) if at == 0x80][0]
            else:
                r = self.rec(rec)
                a = [a for at, a in attrs(r) if at == 0x80 and
                     struct.unpack_from("<q", a, 16)[0] == lvcn][0]
                self.rl.extend(runs(a))
            lo, hi = struct.unpack_from("<qq", a, 16)
            self.extents.append((rec, lo, hi, len(runs(a))))
        self.rl.sort()

    def al_entries(self):
        if self.al is None:
            return
        off = 0
        while off < len(self.al):
            t, ln = struct.unpack_from("<IH", self.al, off)
            if ln == 0:
                break
            lvcn = struct.unpack_from("<q", self.al, off + 8)[0]
            ref = struct.unpack_from("<Q", self.al, off + 16)[0]
            yield t, lvcn, ref & 0xffffffffffff, ref >> 48, off
            off += ln

    def vcn_to_off(self, vcn):
        for v, n, l in self.rl:
            if v <= vcn < v + n and l is not None:
                return (l + vcn - v) * self.cluster
        return None

    def rec_off(self, no):
        """Byte offsets on disk of each cluster of record <no>."""
        cpr = max(1, self.mrs // self.cluster)
        v0 = no * self.mrs // self.cluster
        return [self.vcn_to_off(v0 + i) for i in range(cpr)]

    def raw_rec_at(self, off):
        return fixup(bytes(self.img[off:off + self.mrs]), self.sector)

    def raw_rec(self, no):
        """On-disk bytes (fixups NOT applied) of record <no>."""
        per = min(self.cluster, self.mrs)
        return b"".join(bytes(self.img[o:o + per]) for o in self.rec_off(no))

    def write_raw_rec(self, no, data):
        per = min(self.cluster, self.mrs)
        for i, o in enumerate(self.rec_off(no)):
            self.img[o:o + per] = data[i * per:(i + 1) * per]

    def rec(self, no):
        return fixup(self.raw_rec(no), self.sector)

    def describe(self, no):
        r = self.rec(no)
        magic = r[:4]
        if magic != b"FILE":
            return "rec %d: magic %r" % (no, magic)
        seq = struct.unpack_from("<H", r, 16)[0]
        flags = struct.unpack_from("<H", r, 22)[0]
        base = struct.unpack_from("<Q", r, 32)[0]
        recno = struct.unpack_from("<I", r, 44)[0]
        names, data = [], []
        for t, a in attrs(r):
            if t == 0x30 and a[8] == 0:
                vo = struct.unpack_from("<H", a, 20)[0]
                fn = a[vo:]
                nl = fn[64]
                names.append(fn[66:66 + 2 * nl].decode("utf-16-le"))
            if t == 0x80 and a[8] == 1:
                lo, hi = struct.unpack_from("<qq", a, 16)
                data.append("DATA[%d..%d]" % (lo, hi))
        return ("rec %d: seq %d flags 0x%x(%s) base %d/%d recno %d %s %s" %
                (no, seq, flags, "in-use" if flags & 1 else "free",
                 base & 0xffffffffffff, base >> 48, recno,
                 ",".join(names), " ".join(data)))


def layout(v):
    print("image=%s sector=%d cluster=%d mft_record_size=%d mft_lcn=%d page=%d"
          % (v.path, v.sector, v.cluster, v.mrs, v.mft_lcn, PAGE))
    print("attribute list: %d bytes, runs %s" % (v.al_size, v.al_runs))
    for t, lvcn, rec, seq, off in v.al_entries():
        print("  AL@%-3d type 0x%02x lowest_vcn %-6d in record %d (seq %d)"
              % (off, t, lvcn, rec, seq))
    covered = 0   # vcn end of extents decoded so far, in bootstrap order
    rpf = PAGE // v.mrs
    cpf = PAGE // v.cluster
    print("$DATA extents in bootstrap order (coverage = vcns decoded "
          "BEFORE this extent):")
    for rec, lo, hi, n in v.extents:
        if rec == 0:
            print("  record %-5d vcn %6d..%-6d runs %d" % (rec, lo, hi, n))
        else:
            rv = rec * v.mrs // v.cluster
            folio = rec // rpf
            fs, fe = folio * cpf, (folio + 1) * cpf
            rec_cov = rv + max(1, v.mrs // v.cluster) <= covered
            straddle = fs < covered < fe
            print("  record %-5d vcn %6d..%-6d runs %-4d record at vcn %d, "
                  "folio %d = vcn %d..%d; coverage before = vcn 0..%d; "
                  "record covered=%s; folio straddles coverage end=%s%s"
                  % (rec, lo, hi, n, rv, folio, fs, fe - 1, covered - 1,
                     "yes" if rec_cov else "NO",
                     "YES" if straddle else "no",
                     " (uncovered tail vcn %d..%d = records %d..%d)" %
                     (covered, fe - 1, covered * v.cluster // v.mrs,
                      (folio + 1) * rpf - 1) if straddle else ""))
        covered = hi + 1
    for rec, lo, hi, n in v.extents:
        if rec:
            folio = rec // rpf
            print("  folio %d records:" % folio)
            for r in range(folio * rpf, (folio + 1) * rpf):
                print("    " + v.describe(r))


if __name__ == "__main__":
    v = Vol(sys.argv[1])
    if sys.argv[2] == "show":
        for r in sys.argv[3:]:
            print(v.describe(int(r)))
    elif sys.argv[2] == "layout":
        layout(v)
