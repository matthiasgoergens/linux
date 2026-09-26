#!/usr/bin/env python3
"""Scan the Joliet tree of a remote (HTTP, Range-capable) or local ISO 9660
image without downloading the whole image, and without any kernel mount.

usage: rscan.py URL_OR_PATH OUT_PREFIX

Writes OUT_PREFIX.json (volume descriptor summary + per-image stats) and,
if Joliet is present, OUT_PREFIX.names.tsv.gz with one line per Joliet
directory record:
    kind  name_len  units  utf8_len(kernel view)  dir_path  name
utf8_len is what get_joliet_filename() computes with utf16s_to_utf8s()
(iocharset=utf8 / no nls), after stripping ';1' and trailing dots, as a
true (unwrapped) integer.

Handles 2048-byte ISO, raw 2352 (mode1 / mode2 form1) BIN/IMG/MDF, and
NRG/other images with a leading offset, by locating 'CD001'.
"""
import gzip, json, os, sys, time, urllib.request, urllib.error

S = 2048
UA = 'isofs-joliet-survey/1.0 (research; userspace parser; range reads)'


class Src:
    def __init__(self, loc):
        self.loc = loc
        self.remote = loc.startswith('http')
        self.url = loc
        self.bytes_fetched = 0
        self.requests = 0
        self.size = None
        if not self.remote:
            self.f = open(loc, 'rb')
            self.size = os.fstat(self.f.fileno()).st_size

    def read(self, off, n):
        if n <= 0:
            return b''
        if not self.remote:
            self.f.seek(off)
            return self.f.read(n)
        last = None
        for attempt in range(4):
            try:
                req = urllib.request.Request(self.url, headers={
                    'Range': f'bytes={off}-{off + n - 1}', 'User-Agent': UA})
                with urllib.request.urlopen(req, timeout=120) as r:
                    self.requests += 1
                    if r.status == 206:
                        self.url = r.geturl()  # cache the datanode URL
                        cr = r.headers.get('Content-Range', '')
                        if '/' in cr and cr.split('/')[-1].isdigit():
                            self.size = int(cr.split('/')[-1])
                        d = r.read()
                        self.bytes_fetched += len(d)
                        return d
                    if r.status == 200:
                        # no range support: only acceptable if off == 0
                        if off != 0:
                            raise IOError('server ignored Range')
                        d = r.read(n)
                        self.bytes_fetched += len(d)
                        return d
                    raise IOError(f'HTTP {r.status}')
            except urllib.error.HTTPError as e:
                if e.code == 416:
                    return b''
                if e.code in (401, 403, 404):
                    raise
                last = e
            except Exception as e:  # noqa
                last = e
            time.sleep(2 + 3 * attempt)
        raise IOError(f'read failed at {off}+{n}: {last}')


class Img:
    """Sector-addressed view over Src, with a cache and coalesced reads."""
    LAYOUTS = [  # (base offset, raw sector size, header size)
        (0, 2048, 0), (0, 2352, 16), (0, 2352, 24), (0, 2336, 8),
        (307200, 2048, 0), (150 * 2352, 2352, 16), (150 * 2352, 2352, 24),
    ]

    def __init__(self, src):
        self.src = src
        self.missing = 0
        self.cache = {}
        head = src.read(0, 512 * 1024)
        self.layout = None
        for base, raw, hdr in self.LAYOUTS:
            o = base + 16 * raw + hdr
            if head[o + 1:o + 6] == b'CD001':
                self.layout = (base, raw, hdr)
                break
        if not self.layout:
            # generic: any PVD-looking CD001 followed by another VD one raw sector later
            p = head.find(b'CD001')
            while p > 0 and not self.layout:
                for raw in (2048, 2336, 2352, 2448):
                    for hdr in (0, 8, 16, 24):
                        base = p - 1 - hdr - 16 * raw
                        if base >= 0 and head[p - 1] == 1 and head[p + raw:p + raw + 5] == b'CD001':
                            self.layout = (base, raw, hdr)
                            break
                    if self.layout:
                        break
                p = head.find(b'CD001', p + 1)
        if not self.layout:
            if b'NSR02' in head or b'NSR03' in head:
                raise ValueError('UDF only (no ISO 9660 descriptors)')
            raise ValueError('no CD001 at sector 16 in any known layout')
        base, raw, hdr = self.layout
        for lba in range(0, (len(head) - base) // raw):
            o = base + lba * raw + hdr
            self.cache[lba] = head[o:o + S]

    def fetch(self, lbas, gap=64):
        """Fetch a set of sectors with coalesced range reads."""
        need = sorted(set(l for l in lbas if l not in self.cache))
        if not need:
            return
        base, raw, hdr = self.layout
        runs = []
        for l in need:
            if runs and l - runs[-1][1] <= gap and l - runs[-1][0] < 16384:
                runs[-1][1] = l
            else:
                runs.append([l, l])
        for a, b in runs:
            d = self.src.read(base + a * raw, (b - a + 1) * raw)
            for i in range((len(d)) // raw):
                self.cache[a + i] = d[i * raw + hdr:i * raw + hdr + S]

    def sectors(self, lba, n):
        self.fetch(range(lba, lba + n))
        miss = [l for l in range(lba, lba + n) if l not in self.cache or len(self.cache[l]) != S]
        self.missing += len(miss)
        return b''.join(self.cache.get(l, b'\0' * S).ljust(S, b'\0') for l in range(lba, lba + n))


def s(b):
    return b.decode('latin-1').strip(' \0')


def kernel_len(name_bytes):
    u = name_bytes.decode('utf-16-be', errors='surrogatepass')
    # utf16s_to_utf8s: pairs combine; lone surrogates are dropped
    out = []
    for ch in u:
        if 0xd800 <= ord(ch) <= 0xdfff:
            continue
        out.append(ch)
    u = ''.join(out)
    b = u.encode('utf-8')
    if len(b) > 2 and b.endswith(b';1'):
        b = b[:-2]
    while len(b) >= 2 and b.endswith(b'.'):
        b = b[:-1]
    return b.decode('utf-8', errors='replace'), len(b)


def main():
    loc, outp = sys.argv[1], sys.argv[2]
    res = {'source': loc}
    t0 = time.time()
    src = Src(loc)
    try:
        img = Img(src)
    except Exception as e:
        res['error'] = f'{type(e).__name__}: {e}'
        res['bytes_fetched'] = src.bytes_fetched
        json.dump(res, open(outp + '.json', 'w'), ensure_ascii=False, indent=1)
        print(json.dumps(res, ensure_ascii=False))
        return
    res['layout'] = img.layout
    res['image_size'] = src.size
    vds = []
    svd = None
    for lba in range(16, 100):
        vd = img.sectors(lba, 1)
        if vd[1:6] != b'CD001':
            break
        t = vd[0]
        d = {'lba': lba, 'type': t}
        if t in (1, 2):
            d.update(system_id=s(vd[8:40]), volume_id=s(vd[40:72]),
                     publisher=s(vd[318:446]), preparer=s(vd[446:574]),
                     application=s(vd[574:702]), created=s(vd[813:830]),
                     escape=vd[88:120].rstrip(b'\0').hex())
            if t == 2:
                esc = vd[88:91]
                d['joliet'] = esc in (b'%/@', b'%/C', b'%/E')
                if d['joliet'] and svd is None:
                    svd = vd
                    # UCS-2 fields
                    for k, a, b in (('volume_id', 40, 72), ('publisher', 318, 446),
                                    ('preparer', 446, 574), ('application', 574, 702),
                                    ('system_id', 8, 40)):
                        d[k] = vd[a:b].decode('utf-16-be', errors='replace').strip(' \0')
        vds.append(d)
        if t == 255:
            break
    res['vds'] = vds
    res['joliet'] = svd is not None
    pvd = None
    for lba in range(16, 100):
        vd = img.sectors(lba, 1)
        if vd[1:6] != b'CD001' or vd[0] == 255:
            break
        if vd[0] == 1:
            pvd = vd
            break
    esc = lambda x: x.replace('\\', '\\\\').replace('\t', '\\t').replace('\n', '\\n').replace('\r', '\\r')
    if pvd is not None:
        _, pst, pempty = walk_tree(img, pvd, False, res)
        res['primary_stats'] = pst
        res['primary_empty_sectors'] = pempty
    if svd is not None:
        rows, stats, jempty = walk_tree(img, svd, True, res)
        res['stats'] = stats
        res['joliet_empty_sectors'] = jempty
        with gzip.open(outp + '.names.tsv.gz', 'wt', encoding='utf-8', errors='surrogatepass') as f:
            for k, nl, u, kl, p, kn, raw in rows:
                f.write(f'{k}\t{nl}\t{u}\t{kl}\t{esc(p)}\t{esc(raw)}\n')
    res['missing_sectors'] = img.missing
    res['bytes_fetched'] = src.bytes_fetched
    res['requests'] = src.requests
    res['seconds'] = round(time.time() - t0, 1)
    json.dump(res, open(outp + '.json', 'w'), ensure_ascii=False, indent=1)
    print(json.dumps({'source': loc, 'joliet': svd is not None, **res.get('stats', {}),
                      'p_empty': len(res.get('primary_empty_sectors', [])),
                      'j_empty': len(res.get('joliet_empty_sectors', [])),
                      'fetched': src.bytes_fetched}, ensure_ascii=False))


def walk_tree(img, vd, joliet, res):
    """Walk a directory tree.  Returns (rows, stats, empty) where empty lists
    every directory-extent sector (index < ceil(data_length/2048)) whose
    first byte is 0, i.e. that holds no directory record."""
    pt_size = int.from_bytes(vd[132:136], 'little')
    pt_lba = int.from_bytes(vd[140:144], 'little')
    dir_lbas = []
    if 0 < pt_size < 64 * 1024 * 1024:
        pt = img.sectors(pt_lba, (pt_size + S - 1) // S)[:pt_size]
        o = 0
        while o + 8 <= len(pt):
            ln = pt[o]
            if ln == 0:
                break
            dir_lbas.append(int.from_bytes(pt[o + 2:o + 6], 'little'))
            o += 8 + ln + (ln & 1)
    img.fetch(dir_lbas, gap=2048)
    rows, empty, bad_dot_paths = [], [], []
    stats = {'dirs': 0, 'dir_sectors': 0, 'records': 0, 'max_units': 0, 'max_utf8': 0,
             'n_over255': 0, 'n_over_joliet64': 0, 'n_over103': 0}
    seen = set()
    root = vd[156:190]
    stats['logical_block_size'] = int.from_bytes(vd[128:130], 'little')
    stats['ea_dirs'] = 0
    stats['bad_dot'] = 0
    stats['multi_extent_skipped'] = 0
    stack = [(int.from_bytes(root[2:6], 'little') + root[1], int.from_bytes(root[10:14], 'little'), '')]
    stats['ea_dirs'] += root[1] != 0
    maxrecs = 3_000_000
    while stack:
        ext, size, path = stack.pop()
        if ext in seen or size <= 0 or size > 64 * 1024 * 1024:
            continue
        seen.add(ext)
        stats['dirs'] += 1
        nsec = (size + S - 1) // S
        stats['dir_sectors'] += nsec
        data = img.sectors(ext, nsec)
        # sanity: first record must be '.' pointing at this extent (or ext-EA)
        if not (data[0] >= 34 and data[32] == 1 and data[33] == 0):
            stats['bad_dot'] += 1
            bad_dot_paths.append(path or '/')
        for i in range(nsec):
            if data[i * S] == 0:
                sec = data[i * S:(i + 1) * S]
                empty.append({'path': path or '/', 'sector_index': i, 'n_sectors': nsec,
                              'data_length': size, 'lba': ext + i,
                              'all_zero': not any(sec[:min(S, size - i * S)])})
        off = 0
        while off < size:
            rl = data[off] if off < len(data) else 0
            if rl == 0:
                off = (off // S + 1) * S
                continue
            r = data[off:off + rl]
            if len(r) < 34:
                break
            nl = r[32]
            name = r[33:33 + nl]
            flags = r[25]
            if flags & 0x80:  # multi-extent: Linux reports the name once
                stats['multi_extent_skipped'] += 1
                off += rl
                continue
            if not (nl == 1 and name in (b'\0', b'\1')):
                if joliet:
                    if nl & 1:
                        stats['odd_name_len'] = stats.get('odd_name_len', 0) + 1
                        name = name[:nl & ~1]  # kernel uses name_len >> 1 units
                    kname, klen = kernel_len(name)
                    units = nl // 2
                    rows.append(('d' if flags & 2 else 'f', nl, units, klen, path, kname,
                                 name.decode('utf-16-be', errors='surrogatepass')))
                    stats['max_units'] = max(stats['max_units'], units)
                    stats['max_utf8'] = max(stats['max_utf8'], klen)
                    stats['n_over255'] += klen > 255
                    stats['n_over_joliet64'] += units > 64
                    stats['n_over103'] += units > 103
                else:
                    kname = name.decode('latin-1')
                stats['records'] += 1
                if flags & 2:
                    stats['ea_dirs'] += r[1] != 0
                    stack.append((int.from_bytes(r[2:6], 'little') + r[1],
                                  int.from_bytes(r[10:14], 'little'),
                                  path + '/' + kname))
                if stats['records'] > maxrecs:
                    stack = []
                    res['truncated_walk'] = True
                    break
            off += rl
    stats['bad_dot_paths'] = bad_dot_paths[:20]
    return rows, stats, empty


if __name__ == '__main__':
    main()
