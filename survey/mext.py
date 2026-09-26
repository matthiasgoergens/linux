#!/usr/bin/env python3
"""Multi-extent (file_flags 0x80) versus empty directory sectors.

For every image that has an empty directory sector or a multi-extent record
(from scans/*.json), re-walk both trees (primary + Joliet) with a fresh range
read, and per directory record:
  - every record with flag 0x80: whether its successor (the next record in
    directory order) lies in a later sector ("crosses"), and whether any
    sector strictly between them is empty (first byte 0);
  - every empty sector: the last record before it (skipping earlier empty
    sectors) and whether that record has 0x80 set, i.e. whether
    isofs_read_level3_size() could walk into this sector.
Outputs results/mext_records.tsv, results/mext_empty.tsv, results/mext_summary.txt
"""
import glob, json, os, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rscan import Src, Img, S


def prefetch(img, vd):
    pt_size = int.from_bytes(vd[132:136], 'little')
    pt_lba = int.from_bytes(vd[140:144], 'little')
    lbas = []
    if 0 < pt_size < 64 * 1024 * 1024:
        pt = img.sectors(pt_lba, (pt_size + S - 1) // S)[:pt_size]
        o = 0
        while o + 8 <= len(pt) and pt[o]:
            lbas.append(int.from_bytes(pt[o + 2:o + 6], 'little'))
            o += 8 + pt[o] + (pt[o] & 1)
    img.fetch(lbas, gap=2048)


def walk(img, vd):
    prefetch(img, vd)
    root = vd[156:190]
    stack = [(int.from_bytes(root[2:6], 'little') + root[1], int.from_bytes(root[10:14], 'little'), '')]
    seen = set()
    while stack:
        ext, size, path = stack.pop()
        if ext in seen or size <= 0 or size > 64 * 1024 * 1024:
            continue
        seen.add(ext)
        nsec = (size + S - 1) // S
        data = img.sectors(ext, nsec)
        recs = []  # (sector_index, offset, flags, name)
        empty = [data[i * S] == 0 for i in range(nsec)]
        for i in range(nsec):
            off = 0
            sec = data[i * S:(i + 1) * S]
            while off < S and sec[off]:
                rl = sec[off]
                r = sec[off:off + rl]
                if len(r) < 34:
                    break
                nl = r[32]
                name = r[33:33 + nl]
                recs.append((i, off, r[25], name))
                if r[25] & 2 and not (nl == 1 and name in (b'\0', b'\1')):
                    stack.append((int.from_bytes(r[2:6], 'little') + r[1],
                                  int.from_bytes(r[10:14], 'little'), path + '/' + name.hex()[:16]))
                off += rl
        yield path or '/', ext, nsec, size, recs, empty


def one(tag):
    d = json.load(open(f'scans/{tag}.json'))
    out = {'tag': tag, 'records': [], 'empty': [], 'n_mext': 0, 'err': None}
    try:
        img = Img(Src(d['source']))
        vds = []
        for lba in range(16, 100):
            vd = img.sectors(lba, 1)
            if vd[1:6] != b'CD001' or vd[0] == 255:
                break
            if vd[0] == 1 and not any(t == 'primary' for t, _ in vds):
                vds.append(('primary', vd))
            if vd[0] == 2 and vd[88:91] in (b'%/@', b'%/C', b'%/E') and not any(t == 'joliet' for t, _ in vds):
                vds.append(('joliet', vd))
        for tree, vd in vds:
            for path, ext, nsec, size, recs, empty in walk(img, vd):
                for k, (si, off, fl, name) in enumerate(recs):
                    if fl & 0x80:
                        out['n_mext'] += 1
                        nxt = recs[k + 1] if k + 1 < len(recs) else None
                        between = [j for j in range(si + 1, nxt[0] if nxt else nsec) if empty[j]]
                        out['records'].append((tree, path, ext, si, off, fl,
                                               nxt[0] if nxt else 'END', (nxt[2] & 0x80) if nxt else '',
                                               (nxt[0] != si) if nxt else 'no-successor',
                                               len(between), name.hex()))
                for j in range(nsec):
                    if empty[j]:
                        prev = [r for r in recs if r[0] < j]
                        last = prev[-1] if prev else None
                        later = any(r[0] > j for r in recs)
                        out['empty'].append((tree, path, ext, j, nsec,
                                             'none' if not last else f'{last[2]:#04x}',
                                             bool(last and last[2] & 0x80), later))
    except Exception as e:
        out['err'] = f'{type(e).__name__}: {e}'
    return out


tags = []
for js in glob.glob('scans/*.json'):
    d = json.load(open(js))
    if d.get('error'):
        continue
    m = d.get('stats', {}).get('multi_extent_skipped', 0) + d.get('primary_stats', {}).get('multi_extent_skipped', 0)
    e = len(d.get('primary_empty_sectors', [])) + len(d.get('joliet_empty_sectors', []))
    if m or e:
        tags.append(os.path.basename(js)[:-5])
print(len(tags), 'images to re-walk', flush=True)
with ThreadPoolExecutor(12) as ex:
    res = list(ex.map(one, sorted(tags)))
with open('results/mext_records.tsv', 'w') as f:
    f.write('image\ttree\tdir\tdir_extent\tsector_index\toffset\tflags\tsuccessor_sector\tsuccessor_has_0x80\tsuccessor_in_later_sector\tempty_sectors_between\tname_hex\n')
    for r in res:
        for x in r['records']:
            f.write(r['tag'] + '\t' + '\t'.join(map(str, x)) + '\n')
with open('results/mext_empty.tsv', 'w') as f:
    f.write('image\ttree\tdir\tdir_extent\tsector_index\tn_sectors\tlast_record_flags\tlast_record_has_0x80\trecords_after_empty\n')
    for r in res:
        for x in r['empty']:
            f.write(r['tag'] + '\t' + '\t'.join(map(str, x)) + '\n')
recs = [(r['tag'], x) for r in res for x in r['records']]
emps = [(r['tag'], x) for r in res for x in r['empty']]
lines = [
    f"images re-walked: {len(res)}; errors: {[(r['tag'], r['err']) for r in res if r['err']]}",
    f"multi-extent (0x80) records: {len(recs)} on {len({t for t, _ in recs})} images; by tree: "
    f"{ {t: sum(1 for _, x in recs if x[0] == t) for t in ('primary', 'joliet')} }",
    f"  whose successor is in a later sector (legit sector crossing): {sum(1 for _, x in recs if x[8] is True)} "
    f"on {len({t for t, x in recs if x[8] is True})} images",
    f"  with an empty sector between record and successor: {sum(1 for _, x in recs if x[9])}",
    f"  with no successor in the directory (malformed): {sum(1 for _, x in recs if x[8] == 'no-successor')}",
    f"empty sectors: {len(emps)} on {len({t for t, _ in emps})} images",
    f"  whose last preceding record has 0x80: {sum(1 for _, x in emps if x[6])}",
    f"  last preceding record flags histogram: {dict(__import__('collections').Counter(x[5] for _, x in emps))}",
    f"images with both 0x80 records and empty sectors: {sorted({t for t, _ in recs} & {t for t, _ in emps})}",
]
open('results/mext_summary.txt', 'w').write('\n'.join(lines) + '\n')
print('\n'.join(lines))
