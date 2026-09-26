#!/usr/bin/env python3
"""
Task B: the "out-of-order" $MFT layout, to test whether v2 rejects a layout
that the base kernel handles.

$MFT/$DATA has three extents, described in three different records:

  E1  vcn   0..127  (records  0..63, folios  0..15)  in record 0 (base)
  E2  vcn 128..135  (records 64..67, folio  16)      in record X
  E3  vcn 136..151  (records 68..75, folios 17..18)  in record Z

X = 68 lies in E3's range, i.e. BEYOND the runlist decoded when the loop
asks for E2.  Z lies INSIDE E1's coverage.  Extents are folio aligned
(4 KiB folio = 4 records = 8 clusters) so that reading X's folio needs
only E3, never E2: the nested ntfs_map_runlist_nolock($MFT, vcn 136)
looks up E3, which lives in the covered record Z.

Variants (env Z):
  Z=3   Z shares folio 0 with record 0, already in the page cache
  Z=16  Z in its own folio 4 (the mkntfs/Windows MFT-extension reserve,
        records 16..23), not yet read at that point
Env ORDER=inorder puts X in a covered record too (X=20, folio 5, with Z=16), as a
control that has the same three extents but no out-of-order read.

Reuses the record builders of ./gen-loop-cycle.py.  Like that image, this
is a partial volume: $MFT records 1+ are empty, so a kernel that gets
through the $DATA loop fails later in load_system_files() ("Failed to load
$MFT/$BITMAP attribute").  That message is the marker for "the $DATA
enumeration completed".
"""
import importlib.util, os, struct, sys

sys.dont_write_bytecode = True  # do not litter this directory with __pycache__

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "g", os.path.join(HERE, "gen-loop-cycle.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

MRS, CLUSTER, MFT_LCN = g.MRS, g.CLUSTER, g.MFT_LCN
N_RECORDS = 76
MFT_BYTES = N_RECORDS * MRS
E1 = (0, 127)
E2 = (128, 135)
E3 = (136, 151)
Z = int(os.environ.get("Z", "16"))
X = 20 if os.environ.get("ORDER") == "inorder" else 68
SEQ = 1


def data_attr(lo, hi, instance):
    mp = g.mapping_pairs_single(hi - lo + 1, MFT_LCN + lo)
    # Only the base extent's sizes are used; keep them consistent anyway.
    return g.nonres_data_attr(lo, hi, mp, allocated=MFT_BYTES,
                              dsize=MFT_BYTES, isize=MFT_BYTES,
                              instance=instance)


def record(mft_no, flags_base, attrs, next_instance):
    body = attrs + struct.pack("<II", 0xffffffff, 0)
    bytes_in_use = 56 + len(body)
    rec = g.mft_header(mft_no, SEQ, flags=1, base_ref=flags_base,
                       next_instance=next_instance,
                       bytes_in_use=bytes_in_use)
    rec[56:56 + len(body)] = body
    assert bytes_in_use <= MRS
    return g.mst_protect(rec, MRS)


def build():
    si = bytearray(48)
    struct.pack_into("<I", si, 32, 0x80)
    std = g.resident_attr(0x10, bytes(si), instance=0)
    al = b"".join([
        g.al_entry(0x10, 0, 0, SEQ, 0),
        g.al_entry(0x20, 0, 0, SEQ, 1),
        g.al_entry(0x80, E1[0], 0, SEQ, 2),
        g.al_entry(0x80, E2[0], X, SEQ, 3),
        g.al_entry(0x80, E3[0], Z, SEQ, 4),
    ])
    attrlist = g.resident_attr(0x20, al, instance=1)
    rec0 = record(0, 0, std + attrlist + data_attr(*E1, 2), 5)
    recX = record(X, g.mref(0, SEQ), data_attr(*E2, 3), 5)
    recZ = record(Z, g.mref(0, SEQ), data_attr(*E3, 4), 5)
    img = bytearray(g.NR_CLUSTERS * CLUSTER)
    img[0:g.SECTOR] = g.build_boot()
    base = MFT_LCN * CLUSTER
    for no, r in ((0, rec0), (X, recX), (Z, recZ)):
        img[base + no * MRS:base + (no + 1) * MRS] = r
    return img


def main():
    out = sys.argv[1]
    assert Z * MRS // 4096 != X * MRS // 4096, "X and Z share a folio"
    assert Z * MRS // CLUSTER <= E1[1], "Z must be inside E1 coverage"
    with open(out, "wb") as f:
        f.write(build())
    print("wrote %s: E1 vcn %d..%d @rec0, E2 vcn %d..%d @rec%d (vcn %d, "
          "folio %d), E3 vcn %d..%d @rec%d (vcn %d, folio %d)" % (
              out, *E1, *E2, X, X * MRS // CLUSTER, X * MRS // 4096,
              *E3, Z, Z * MRS // CLUSTER, Z * MRS // 4096))


if __name__ == "__main__":
    main()
