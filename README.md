# Long Joliet names: test images and a disc survey

Supporting data for the isofs series that returns Joliet names longer
than 255 bytes of UTF-8 whole.  Test data only.

## tool-images/

Images written by Windows authoring tools under wine, from source files
named with 110 CJK, 110 Thai and 110 Devanagari characters, 100 CJK
characters plus ".txt", 110 ASCII letters, and a pair of CJK names that
share their first 90 characters.  File contents are small placeholders.

- `win-default.iso`: PowerISO 9.5, default options.  The Joliet names
  are the source names, 110 UTF-16 units (330 bytes of UTF-8 for CJK,
  Thai and Devanagari).
- `win-jl64.iso`: PowerISO 9.5 with `-joliet-name-len 64`, for
  comparison: names shortened to 64 units with a `~0` suffix.
- `uiso-zh_CN-jlong.iso`: UltraISO 9.76 under the Chinese (cp936) code
  page: CJK names 110 units, other scripts replaced with `_`.
- `uiso-th_TH-jlong.iso`: UltraISO 9.76 under the Thai (cp874) code
  page: Thai names 110 units.
- `uiso-hi_IN-jlong.iso`: UltraISO 9.76 under a Hindi locale: there is
  no ANSI code page for Devanagari, so non-ASCII files are left out.

`SHA256SUMS` lists the images.

## crafted/

`joliet-111.iso`: a Joliet-only image whose records use the full
255 bytes without the padding byte, giving 111-unit names, plus a
subdirectory `bad/` with a record whose name length claims more than the
record holds.  Built by `make-joliet-111.sh` (xorriso with
`-compliance joliet_long_names`, then `repack-joliet.py` rewrites the
placeholder names).

## survey/

`rscan.py` reads the volume descriptors, path tables and directory
extents of an ISO 9660 image over HTTP range requests (or from a local
file), without downloading or mounting it, and records every Joliet name
with its length in UTF-16 units and in UTF-8 bytes.  `analyse.py`
summarises the scans; `mext.py` looks at multi-extent records and empty
directory sectors.

`results/images.tsv` lists the archive.org images scanned (item,
file, tool identifiers, name statistics); `results/summary.txt` has the
totals; `results/near.tsv` the images with names longer than 64 units;
`results/over255.tsv` the images with names over 255 bytes (none);
`results/empty_by_image.tsv` the images with empty directory sectors.
