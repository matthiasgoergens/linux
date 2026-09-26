#!/usr/bin/env python3
"""Aggregate scans/*.json + names/*.names.tsv.gz into results/.

results/images.tsv        one row per scanned image
results/over255.tsv       every Joliet name whose kernel UTF-8 length > 255
results/near.tsv          names with 86..111 units or UTF-8 > 192 (above spec max)
results/collisions.tsv    per offending directory: capped-name collisions
results/summary.txt       histograms and counts
"""
import collections, glob, gzip, json, os, re, unicodedata

os.makedirs('results', exist_ok=True)

TOOLS = [
    ('Nero', r'NERO'), ('ImgBurn', r'IMGBURN'), ('mkisofs/genisoimage', r'MKISOFS|GENISOIMAGE'),
    ('xorriso/libisofs', r'XORRISO|LIBISOFS|LIBBURN'), ('UltraISO', r'ULTRAISO|EZB'),
    ('PowerISO', r'POWERISO'), ('IMAPI', r'IMAPI|MICROSOFT.*WINDOWS|WINDOWS'),
    ('Roxio/Adaptec', r'ROXIO|ADAPTEC|EASY CD|EASY MEDIA|DIRECTCD|TOAST'), ('CDBurnerXP', r'CDBURNERXP'),
    ('Alcohol', r'ALCOHOL'), ('B\'s Recorder', r"B'S|BHA|B.S RECORDER"), ('WinCDR', r'WINCDR'),
    ('Sonic/RecordNow', r'SONIC|RECORDNOW|VERITAS|STOMP'), ('MagicISO', r'MAGICISO'),
    ('oscdimg/cdimage', r'OSCDIMG|CDIMAGE'), ('Apple hdiutil', r'HDIUTIL|APPLE|MAC OS'),
    ('Ashampoo', r'ASHAMPOO'), ('BurnAware', r'BURNAWARE'), ('AnyBurn', r'ANYBURN'),
    ('InfraRecorder', r'INFRARECORDER'), ('DeepBurner', r'DEEPBURNER'), ('WinISO', r'WINISO'),
    ('Ahead (Nero)', r'AHEAD'), ('CDRWIN', r'CDRWIN|GOLDENHAWK'), ('Gear', r'GEAR'),
    ('Primo/Prassi', r'PRIMO|PRASSI'), ('Padus DiscJuggler', r'DISCJUGGLER|PADUS'),
]


def tool_of(fields):
    s = ' | '.join(f for f in fields if f).upper()
    for name, rx in TOOLS:
        if re.search(rx, s):
            return name
    return 'unknown' if not s.strip(' |') else 'other'


def script_of(ch):
    o = ord(ch)
    if o < 0x800:
        return None
    if 0x1E00 <= o <= 0x1EFF:
        return 'Vietnamese/Latin-ext-add'
    if 0x2000 <= o <= 0x206F:
        return 'General punctuation'
    if 0xFF00 <= o <= 0xFFEF:
        return 'Fullwidth/halfwidth forms'
    if 0x3000 <= o <= 0x303F:
        return 'CJK punctuation'
    n = unicodedata.name(ch, 'UNKNOWN')
    w = n.split()[0]
    return {'CJK': 'Han', 'HIRAGANA': 'Kana', 'KATAKANA': 'Kana', 'HANGUL': 'Hangul',
            'HALFWIDTH': 'Fullwidth/halfwidth forms', 'FULLWIDTH': 'Fullwidth/halfwidth forms'}.get(w, w.title())


def dominant_script(name):
    c = collections.Counter(filter(None, (script_of(ch) for ch in name)))
    if not c:
        return 'none'
    # CJK images mix Han + Kana: report the combination for Japanese
    top = [k for k, _ in c.most_common(2)]
    if set(top) == {'Han', 'Kana'}:
        return 'Japanese (Han+Kana)'
    return top[0]


def kview(raw):
    """kernel view: strip ;1 and trailing dots, lone surrogates dropped"""
    u = ''.join(ch for ch in raw if not 0xD800 <= ord(ch) <= 0xDFFF)
    try:
        u = u.encode('utf-16', 'surrogatepass').decode('utf-16')
    except Exception:
        pass
    b = u.encode('utf-8', errors='replace')
    if len(b) > 2 and b.endswith(b';1'):
        b = b[:-2]
    while len(b) >= 2 and b.endswith(b'.'):
        b = b[:-1]
    return b


def cap255(b):
    """longest valid UTF-8 prefix of at most 255 bytes"""
    if len(b) <= 255:
        return b
    return b[:255].decode('utf-8', 'ignore').encode('utf-8')


CHARSETS = ['cp932', 'euc_jp', 'gbk', 'cp949', 'cp950', 'cp874']


def unesc(x):
    return re.sub(r'\\(.)', lambda m: {'t': '\t', 'n': '\n', 'r': '\r', '\\': '\\'}[m.group(1)], x)


TE = lambda v: str(v).replace('\\', '\\\\').replace('\t', '\\t').replace('\n', '\\n').replace('\r', '\\r')
rows = []
over, near, coll, empties = [], [], [], []
hist_units = collections.Counter()
hist_utf8 = collections.Counter()
nls_over = collections.Counter()
for js in sorted(glob.glob('scans/*.json')):
    d = json.load(open(js))
    tag = os.path.basename(js)[:-5]
    pvd = next((v for v in d.get('vds', []) if v.get('type') == 1), {})
    svd = next((v for v in d.get('vds', []) if v.get('type') == 2 and v.get('joliet')), {})
    fields = [pvd.get('application'), pvd.get('preparer'), svd.get('application'), svd.get('preparer'),
              pvd.get('publisher'), pvd.get('system_id')]
    st = d.get('stats', {})
    r = dict(tag=tag, pool=d.get('pool'), identifier=d.get('identifier'), file=d.get('file'),
             size=d.get('file_size'), status=('error' if d.get('error') else 'joliet' if d.get('joliet') else 'no-joliet'),
             error=(d.get('error') or '')[:120].replace('\n', ' ').replace('\t', ' '),
             tool=(tool_of(fields[:4]) if tool_of(fields[:4]) not in ('unknown', 'other') or 'CDEVERYWHERE' not in (pvd.get('system_id') or '') else 'CDEverywhere (sysid)'), app_pvd=pvd.get('application', ''), prep_pvd=pvd.get('preparer', ''),
             app_svd=svd.get('application', ''), prep_svd=svd.get('preparer', ''), sys_id=pvd.get('system_id', ''),
             created=pvd.get('created', ''), records=st.get('records', ''), max_units=st.get('max_units', ''),
             max_utf8=st.get('max_utf8', ''), n_over64=st.get('n_over_joliet64', ''),
             n_over255=st.get('n_over255', ''), fetched=d.get('bytes_fetched', ''),
             truncated=d.get('truncated_walk', False), language=str(d.get('language') or ''))
    pst = d.get('primary_stats', {})
    r['p_dir_sectors'] = pst.get('dir_sectors', '')
    r['j_dir_sectors'] = st.get('dir_sectors', '')
    r['p_empty'] = len(d.get('primary_empty_sectors', []))
    r['j_empty'] = len(d.get('joliet_empty_sectors', []))
    for tree in ('primary', 'joliet'):
        for e in d.get(tree + '_empty_sectors', []):
            empties.append((tag, tree, r['tool'], e['path'], e['sector_index'], e['n_sectors'],
                            e['data_length'], e['lba'], e['all_zero'], r['app_pvd'], r['prep_pvd'],
                            r['app_svd'], r['prep_svd'], r['sys_id']))
    rows.append(r)
    if r['status'] != 'joliet':
        continue
    hist_units[st['max_units']] += 1
    hist_utf8[st['max_utf8'] // 32 * 32] += 1
    nf = f'names/{tag}.names.tsv.gz'
    if not os.path.exists(nf):
        continue
    dirs = collections.defaultdict(list)
    for line in gzip.open(nf, 'rt', encoding='utf-8', errors='surrogatepass'):
        k, nl, u, kl, p, raw = line.rstrip('\n').split('\t')
        p, raw = unesc(p), unesc(raw)
        dirs[p].append((k, int(u), int(kl), raw))
    for p, ents in dirs.items():
        bad = [e for e in ents if e[2] > 255]
        for k, u, kl, raw in ents:
            if u > 85 or kl > 192:
                near.append((tag, r['tool'], k, u, kl, dominant_script(raw), p, raw))
            for cs in CHARSETS:
                name = raw[:-2] if raw.endswith(';1') else raw
                if len(name.encode(cs, errors='replace')) > 255:
                    nls_over[cs] += 1
        if not bad:
            continue
        full = [kview(e[3]) for e in ents]
        capped = [cap255(b) for b in full]
        cnt = collections.Counter(capped)
        ncoll = sum(1 for e, c in zip(ents, capped) if e[2] > 255 and cnt[c] > 1)
        groups = sum(1 for c, n in cnt.items() if n > 1 and any(len(f) > 255 for f, cc in zip(full, capped) if cc == c))
        coll.append((tag, p, len(ents), len(bad), ncoll, groups))
        for k, u, kl, raw in bad:
            over.append((tag, r['tool'], k, u, kl, dominant_script(raw), cnt[cap255(kview(raw))] > 1, p, raw))

cols = list(rows[0].keys()) if rows else []
with open('results/images.tsv', 'w') as f:
    f.write('\t'.join(cols) + '\n')
    for r in rows:
        f.write('\t'.join(TE(r[c]) for c in cols) + '\n')
with open('results/over255.tsv', 'w') as f:
    f.write('image\ttool\tkind\tunits\tutf8\tscript\tcap_collides\tdir\tname\n')
    for x in over:
        f.write('\t'.join(TE(v) for v in x) + '\n')
with open('results/near.tsv', 'w') as f:
    f.write('image\ttool\tkind\tunits\tutf8\tscript\tdir\tname\n')
    for x in near:
        f.write('\t'.join(TE(v) for v in x) + '\n')
with open('results/collisions.tsv', 'w') as f:
    f.write('image\tdir\tentries\tover255\tover255_colliding_after_cap\tcollision_groups\n')
    for x in coll:
        f.write('\t'.join(map(str, x)) + '\n')

with open('results/empty_sectors.tsv', 'w') as f:
    f.write('image\ttree\ttool\tdir\tsector_index\tn_sectors\tdata_length\tlba\tall_zero\tapp_pvd\tprep_pvd\tapp_svd\tprep_svd\tsystem_id\n')
    for x in empties:
        f.write('\t'.join(str(v).replace('\t', ' ') for v in x) + '\n')

out = []
st = collections.Counter(r['status'] for r in rows)
out.append(f'images scanned: {len(rows)}  status: {dict(st)}')
items = collections.defaultdict(set)
for r in rows:
    items[r['status']].add(r['identifier'])
out.append(f'items with >=1 Joliet image: {len(items["joliet"])}')
out.append('\nper pool (images): joliet / no-joliet / error ; images with >255 ; images with >64 units')
for pool in sorted(set(r['pool'] for r in rows), key=str):
    rs = [r for r in rows if r['pool'] == pool]
    c = collections.Counter(r['status'] for r in rs)
    o = sum(1 for r in rs if r['status'] == 'joliet' and int(r['n_over255'] or 0) > 0)
    o64 = sum(1 for r in rs if r['status'] == 'joliet' and int(r['n_over64'] or 0) > 0)
    out.append(f'  {pool:10s} {c["joliet"]:5d} / {c["no-joliet"]:5d} / {c["error"]:4d} ; {o:3d} ; {o64:4d}')
out.append('\nhistogram of per-image max UTF-16 units (Joliet images):')
for k in sorted(hist_units):
    out.append(f'  {k:4d}: {hist_units[k]}')
out.append('\nhistogram of per-image max kernel UTF-8 bytes (bucket of 32):')
for k in sorted(hist_utf8):
    out.append(f'  {k:4d}-{k+31:4d}: {hist_utf8[k]}')
out.append(f'\nnames > 255 UTF-8 bytes: {len(over)} in {len(set(x[0] for x in over))} images')
out.append('  by script: ' + str(collections.Counter(x[5] for x in over).most_common()))
out.append('  by tool:   ' + str(collections.Counter(x[1] for x in over).most_common()))
out.append(f'  directories affected: {len(coll)}; colliding after 255-byte cap: '
           f'{sum(x[4] for x in coll)} names in {sum(1 for x in coll if x[4])} dirs')
out.append(f'\nnames with >85 units or >192 bytes (above-spec): {len(near)} in {len(set(x[0] for x in near))} images')
out.append('  by script: ' + str(collections.Counter(x[5] for x in near).most_common()))
out.append('\nJoliet images by tool: ' + str(collections.Counter(r['tool'] for r in rows if r['status'] == 'joliet').most_common()))
out.append('images with >64-unit names by tool: ' + str(collections.Counter(r['tool'] for r in rows if r['status'] == 'joliet' and int(r['n_over64'] or 0) > 0).most_common()))
out.append('\nnon-UTF-8 iocharsets (uni16_to_x8, unmappable -> 1 byte): names > 255 bytes: ' + str(dict(nls_over)))
ok = [r for r in rows if r['status'] != 'error']
out.append('\nempty directory sectors (first byte 0, within data length):')
out.append(f"  primary tree: {sum(1 for r in ok if r['p_empty'])} of {sum(1 for r in ok if r['p_dir_sectors'] != '')} images, "
           f"{sum(r['p_empty'] for r in ok)} sectors of {sum(int(r['p_dir_sectors'] or 0) for r in ok)} examined")
out.append(f"  joliet tree:  {sum(1 for r in ok if r['j_empty'])} of {sum(1 for r in ok if r['j_dir_sectors'] != '')} images, "
           f"{sum(r['j_empty'] for r in ok)} sectors of {sum(int(r['j_dir_sectors'] or 0) for r in ok)} examined")
out.append('  images with empty sectors by tool: ' + str(collections.Counter(t for _, t in {(e[0], e[2]) for e in empties}).most_common()))
open('results/summary.txt', 'w').write('\n'.join(out) + '\n')
print('\n'.join(out))
