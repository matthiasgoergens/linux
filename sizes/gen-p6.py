#!/usr/bin/env python3
"""
gen-p6.py <in.img> <out.img> <recno> <type-hex> <name|-> <alloc> <data> <init>
Set the sizes of one non-resident attribute in base record <recno> (first
extent, lowest_vcn 0).  Each size is either "=" (keep), an absolute number,
or "+N"/"-N" relative to the attribute's current allocated size ("a+N") or
data size ("d+N").  Uses ../mft-bootstrap/gen-sizes.py's MST/record helpers, so record
0-3 edits also go to $MFTMirr.  Pure userspace.
"""
import importlib.util, os, struct, sys

spec = importlib.util.spec_from_file_location(
    "gs", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mft-bootstrap", "gen-sizes.py"))
gs = importlib.util.module_from_spec(spec); spec.loader.exec_module(gs)


def find(rec, typ, name):
    for t, o, l in gs.attrs(rec):
        if t != typ or rec[o + 8] != 1:
            continue
        nl, no = rec[o + 9], struct.unpack_from("<H", rec, o + 10)[0]
        n = bytes(rec[o + no:o + no + 2 * nl]).decode("utf-16-le")
        if n == ("" if name == "-" else name) and struct.unpack_from("<q", rec, o + 16)[0] == 0:
            return o
    raise SystemExit("attribute not found")


def val(s, cur, al, ds):
    if s == "=":
        return cur
    if s[0] in "ad":
        return (al if s[0] == "a" else ds) + int(s[1:])
    return int(s)


def main():
    src, dst, recno, typ, name, sa, sd, si = sys.argv[1:9]
    img = gs.Img(src)
    no = int(recno)
    rec = img.get(no)
    o = find(rec, int(typ, 16), name)
    al, ds, ini = gs.sizes(rec, o)
    print("rec %d type %s name %s flags 0x%04x before: alloc %d data %d init %d"
          % (no, typ, name, struct.unpack_from("<H", rec, o + 12)[0], al, ds, ini))
    na, nd, ni = val(sa, al, al, ds), val(sd, ds, al, ds), val(si, ini, al, ds)
    gs.set_sizes(rec, o, na, nd, ni)
    print("after: alloc %d data %d init %d" % gs.sizes(rec, o))
    img.put(no, rec)
    open(dst, "wb").write(img.b)


if __name__ == "__main__":
    main()
