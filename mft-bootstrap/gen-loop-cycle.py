#!/usr/bin/env python3
"""
Generate an NTFS image (fs/ntfs) that hangs v1 of the $MFT extent patch as mailed.

v1 sets NVolMftBootstrap only around ntfs_read_locked_inode()
inside the first-$DATA-extent branch of ntfs_read_inode_mount(), and clears
it before the extent-enumeration loop continues.  Hyunchul Lee's review says
the loop's ntfs_attr_lookup(AT_DATA, next_vcn) -> ntfs_external_attr_find ->
map_extent_mft_record() -> map_mft_record() -> ntfs_read_folio -> iomap ->
ntfs_attr_vcn_to_rl -> ntfs_map_runlist_nolock() can hit the SAME folio
self-deadlock, but now with the flag CLEAR so the guard does not fire.

This image realises exactly that: $MFT has an attribute list; its $DATA is
split into two extents; extent 1 (lowest_vcn 0) lives in the base record 0
(reachable within extent-1 coverage, so ntfs_read_locked_inode succeeds and
the flag clears); extent 2 (lowest_vcn 128) lives in extent MFT record 64,
which sits at $MFT byte 65536 = vcn 128 -- BEYOND extent 1's runlist
coverage (vcn 0..127).  When the loop asks for the vcn-128 extent it must
read record 64's folio; reading it re-enters ntfs_map_runlist_nolock($MFT)
for vcn 128, which needs record 64 again -> AA on the folio lock.

Geometry (all measured against the driver, no large folios in fs/ntfs):
  sector = cluster = 512, mft_record_size = 1024 (2 clusters/record)
  PIDX(mft_no) = mft_no >> 2  (4 records per 4KiB folio)
  record 0  -> folio 0  (vcn 0..7)    covered by extent 1
  record 64 -> folio 16 (vcn 128..135) NOT covered by extent 1
"""
import struct, sys

SECTOR = 512
CLUSTER = 512
MRS = 1024                     # mft record size
MFT_LCN = 4                    # $MFT starts at cluster 4
MFTMIRR_LCN = 2
NR_CLUSTERS = 512              # 256 KiB volume
import os
REC_E = int(os.environ.get("REC_E", "64"))  # extent record holding $DATA extent 2
N_RECORDS = 65                 # records 0..64 (MFT always 130 clusters)
MFT_CLUSTERS = (N_RECORDS * MRS) // CLUSTER          # 130 clusters
MFT_BYTES = N_RECORDS * MRS                          # 66560
EXT1_HIGH_VCN = 127            # extent 1 covers vcn 0..127 (folios 0..15)
# record 64 at byte 65536 = vcn 128 -> in extent 2 (vcn 128..129)

def le(v, n):
    return int(v).to_bytes(n, "little", signed=False)

def mref(recno, seq):
    return (seq << 48) | (recno & 0xffffffffffff)

def mst_protect(rec, mrs):
    """Apply pre-write MST fixup: USN at usa_ofs, save last 2 bytes of each
    512-byte sector into the USA and replace them with the USN."""
    rec = bytearray(rec)
    usa_ofs = struct.unpack_from("<H", rec, 4)[0]
    usa_count = struct.unpack_from("<H", rec, 6)[0]
    usn = 1  # non-zero, != 0xffff
    struct.pack_into("<H", rec, usa_ofs, usn)
    nsect = mrs // SECTOR
    assert usa_count == nsect + 1
    for i in range(nsect):
        end = (i + 1) * SECTOR - 2
        saved = rec[end:end + 2]
        # store original into USA slot i+1
        struct.pack_into("<2s", rec, usa_ofs + 2 + i * 2, bytes(saved))
        # write USN into the sector's last 2 bytes
        struct.pack_into("<H", rec, end, usn)
    return bytes(rec)

def resident_attr(atype, value, instance, name_off_val=0x18):
    hdr = bytearray(24)
    struct.pack_into("<I", hdr, 0, atype)          # type
    # length filled later
    hdr[8] = 0                                      # non_resident
    hdr[9] = 0                                      # name_length
    struct.pack_into("<H", hdr, 10, 0)              # name_offset
    struct.pack_into("<H", hdr, 12, 0)              # flags
    struct.pack_into("<H", hdr, 14, instance)       # instance
    value_offset = 24
    struct.pack_into("<I", hdr, 16, len(value))     # value_length
    struct.pack_into("<H", hdr, 20, value_offset)   # value_offset
    hdr[22] = 0                                      # resident flags
    hdr[23] = 0                                      # reserved
    body = bytearray(bytes(hdr) + value)
    # pad to 8
    pad = (-len(body)) % 8
    body += b"\x00" * pad
    struct.pack_into("<I", body, 4, len(body))      # length
    return bytes(body)

def nonres_data_attr(lowest_vcn, highest_vcn, mapping_pairs,
                     allocated, dsize, isize, instance):
    # non-resident header without compressed_size = 64 bytes
    hdr = bytearray(64)
    struct.pack_into("<I", hdr, 0, 0x80)            # type $DATA
    hdr[8] = 1                                       # non_resident
    hdr[9] = 0                                       # name_length
    struct.pack_into("<H", hdr, 10, 0)              # name_offset
    struct.pack_into("<H", hdr, 12, 0)              # flags
    struct.pack_into("<H", hdr, 14, instance)       # instance
    struct.pack_into("<q", hdr, 16, lowest_vcn)     # lowest_vcn
    struct.pack_into("<q", hdr, 24, highest_vcn)    # highest_vcn
    struct.pack_into("<H", hdr, 32, 64)            # mapping_pairs_offset
    hdr[34] = 0                                      # compression_unit
    struct.pack_into("<Q", hdr, 40, allocated)      # allocated_size
    struct.pack_into("<Q", hdr, 48, dsize)          # data_size
    struct.pack_into("<Q", hdr, 56, isize)          # initialized_size
    body = bytes(hdr) + mapping_pairs
    pad = (-len(body)) % 8
    body += b"\x00" * pad
    body = bytearray(body)
    struct.pack_into("<I", body, 4, len(body))      # length
    return bytes(body)

def mapping_pairs_single(length, lcn_delta):
    """One run: header byte low nibble = length byte count, high nibble =
    offset byte count.  length little-endian (unsigned, top bit kept clear),
    then lcn delta little-endian signed."""
    lb = length.to_bytes(2, "little")               # 2 bytes, top clear
    ob = lcn_delta.to_bytes(1, "little", signed=True) if -128 <= lcn_delta <= 127 \
         else lcn_delta.to_bytes(2, "little", signed=True)
    header = (len(lb)) | (len(ob) << 4)
    return bytes([header]) + lb + ob + b"\x00"      # terminator

def al_entry(atype, lowest_vcn, recno, seq, instance):
    e = bytearray(32)
    struct.pack_into("<I", e, 0, atype)             # type
    struct.pack_into("<H", e, 4, 32)                # length
    e[6] = 0                                         # name_length
    e[7] = 26                                        # name_offset == sizeof(ale)
    struct.pack_into("<q", e, 8, lowest_vcn)        # lowest_vcn
    struct.pack_into("<Q", e, 16, mref(recno, seq)) # mft_reference
    struct.pack_into("<H", e, 24, instance)         # instance
    return bytes(e)

def mft_header(mft_no, seq, flags, base_ref, next_instance, bytes_in_use):
    h = bytearray(MRS)
    struct.pack_into("<I", h, 0, 0x454c4946)        # "FILE"
    struct.pack_into("<H", h, 4, 48)                # usa_ofs
    struct.pack_into("<H", h, 6, (MRS // SECTOR) + 1)  # usa_count = 3
    struct.pack_into("<Q", h, 8, 0)                 # lsn
    struct.pack_into("<H", h, 16, seq)              # sequence_number
    struct.pack_into("<H", h, 18, 1)               # link_count
    struct.pack_into("<H", h, 20, 56)              # attrs_offset
    struct.pack_into("<H", h, 22, flags)           # flags
    struct.pack_into("<I", h, 24, bytes_in_use)    # bytes_in_use
    struct.pack_into("<I", h, 28, MRS)             # bytes_allocated
    struct.pack_into("<Q", h, 32, base_ref)        # base_mft_record
    struct.pack_into("<H", h, 40, next_instance)   # next_attr_instance
    struct.pack_into("<H", h, 42, 0)               # reserved
    struct.pack_into("<I", h, 44, mft_no)          # mft_record_number
    return h

def build_record0():
    seq = 1
    # $STANDARD_INFORMATION value (v1, 48 bytes)
    si = bytearray(48)
    struct.pack_into("<I", si, 32, 0x80)            # file_attributes = NORMAL
    std = resident_attr(0x10, bytes(si), instance=0)

    # $ATTRIBUTE_LIST value: 4 entries
    al = b"".join([
        al_entry(0x10, 0, 0, seq, 0),               # STD_INFO @rec0
        al_entry(0x20, 0, 0, seq, 1),               # ATTR_LIST @rec0
        al_entry(0x80, 0, 0, seq, 2),               # DATA vcn0 @rec0
        al_entry(0x80, 128, REC_E, seq, 3),         # DATA vcn128 @rec64  <-- trap
    ])
    assert len(al) == 128
    attrlist = resident_attr(0x20, al, instance=1)

    # $DATA extent 1: vcn 0..127 -> clusters MFT_LCN..MFT_LCN+127
    mp1 = mapping_pairs_single(EXT1_HIGH_VCN + 1, MFT_LCN)   # length 128, lcn +4
    data1 = nonres_data_attr(0, EXT1_HIGH_VCN, mp1,
                             allocated=MFT_BYTES, dsize=MFT_BYTES,
                             isize=MFT_BYTES, instance=2)

    attrs = bytes(std) + bytes(attrlist) + bytes(data1)
    end = struct.pack("<II", 0xffffffff, 0)         # AT_END + length 0
    body = attrs + end
    attrs_offset = 56
    bytes_in_use = attrs_offset + len(body)
    rec = mft_header(0, seq, flags=1, base_ref=0,
                     next_instance=4, bytes_in_use=bytes_in_use)
    rec[attrs_offset:attrs_offset + len(body)] = body
    assert bytes_in_use <= MRS, bytes_in_use
    return mst_protect(rec, MRS)

def build_recordE():
    # Extent record 64 holding $DATA extent 2 (vcn 128..129).  The kernel
    # deadlocks reading this record's FOLIO before it validates the content,
    # so the content is only for on-disk consistency.
    seq = 1
    mp2 = mapping_pairs_single(2, MFT_LCN + 128)    # vcn128..129 -> clusters 132..133
    data2 = nonres_data_attr(128, 129, mp2,
                             allocated=MFT_BYTES, dsize=MFT_BYTES,
                             isize=MFT_BYTES, instance=3)
    attrs = bytes(data2)
    end = struct.pack("<II", 0xffffffff, 0)
    body = attrs + end
    attrs_offset = 56
    bytes_in_use = attrs_offset + len(body)
    rec = mft_header(REC_E, seq, flags=1, base_ref=mref(0, 1),
                     next_instance=4, bytes_in_use=bytes_in_use)
    rec[attrs_offset:attrs_offset + len(body)] = body
    return mst_protect(rec, MRS)

def build_boot():
    b = bytearray(SECTOR)
    b[0:3] = bytes([0xeb, 0x52, 0x90])              # jump
    b[3:11] = b"NTFS    "                            # oem_id
    struct.pack_into("<H", b, 11, SECTOR)          # bytes_per_sector
    b[13] = CLUSTER // SECTOR                        # sectors_per_cluster = 1
    struct.pack_into("<H", b, 14, 0)               # reserved_sectors
    b[16] = 0                                        # fats
    struct.pack_into("<H", b, 17, 0)               # root_entries
    struct.pack_into("<H", b, 19, 0)               # sectors
    b[21] = 0xf8                                     # media_type
    struct.pack_into("<H", b, 22, 0)               # sectors_per_fat
    struct.pack_into("<H", b, 24, 63)              # sectors_per_track
    struct.pack_into("<H", b, 26, 255)             # heads
    struct.pack_into("<I", b, 28, 0)               # hidden_sectors
    struct.pack_into("<I", b, 32, 0)               # large_sectors
    # unused[4] @36
    struct.pack_into("<Q", b, 40, NR_CLUSTERS)     # number_of_sectors (spc=1)
    struct.pack_into("<Q", b, 48, MFT_LCN)         # mft_lcn
    struct.pack_into("<Q", b, 56, MFTMIRR_LCN)     # mftmirr_lcn
    struct.pack_into("<b", b, 64, 2)               # clusters_per_mft_record=2 ->1024
    struct.pack_into("<b", b, 68, 1)               # clusters_per_index_record=1
    struct.pack_into("<Q", b, 72, 0x1122334455667788)  # serial
    struct.pack_into("<I", b, 80, 0)               # checksum
    struct.pack_into("<H", b, 510, 0xaa55)         # end marker
    return bytes(b)

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "loop-cycle.img"
    img = bytearray(NR_CLUSTERS * CLUSTER)
    # boot sector at byte 0
    img[0:SECTOR] = build_boot()
    # $MFT at MFT_LCN
    mft_off = MFT_LCN * CLUSTER
    rec0 = build_record0()
    recE = build_recordE()
    # lay records: record 0 at mft_off, fillers (zero) 1..63, record 64
    img[mft_off:mft_off + MRS] = rec0
    eoff = mft_off + REC_E * MRS
    img[eoff:eoff + MRS] = recE
    with open(out, "wb") as f:
        f.write(img)
    print("wrote %s (%d bytes)" % (out, len(img)))
    print("  MFT_LCN=%d  MFT_CLUSTERS=%d  MFT_BYTES=%d" %
          (MFT_LCN, MFT_CLUSTERS, MFT_BYTES))
    print("  record 0 folio 0 (vcn 0..7, covered); record %d folio %d "
          "(vcn 128, UNcovered)" % (REC_E, (REC_E * MRS) // 4096))

if __name__ == "__main__":
    main()
