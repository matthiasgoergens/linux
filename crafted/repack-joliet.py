#!/usr/bin/env python3
"""Rewrite Joliet directory records of a xorriso-made image so that names can
use the whole 255-byte record: 111 UTF-16 units without the padding byte,
which neither xorriso (103) nor pycdlib (64) will write.

Placeholder records are recognised by a UTF-16BE name of TAG + 'x' * 100.
Records are re-serialised into the directory's existing extent (records never
straddle a sector); PAD* placeholders are dropped only as far as needed to
make room, and no sector of a directory is left empty.  Only the
Joliet tree is touched (Rock Ridge off, path tables left as written: Linux
isofs never reads them).

Usage: repack-joliet.py SRC.iso DST.iso
"""
import struct, sys

S = 2048
K, Z = '漢', '字'            # 漢 字: 3 UTF-8 bytes each
EMO = '\U0001F600'                   # surrogate pair: 2 units, 4 UTF-8 bytes

def u16(t):
    return t.encode('utf-16-be')

# tag -> (name bytes, record length override or None, note)
T = {
    'J01': (u16(K * 110), None, '110 units, 330 B, padded record (254)'),
    'J02': (u16(K * 111), None, '111 units, 333 B, no padding (255)'),
    'J03': (u16(K * 109 + EMO), None, '111 units, ends in a pair, 331 B'),
    'J04': (u16(K * 109 + ';1'), None, '111 units, ";1" stripped -> 327 B'),
    'J05': (u16(K * 110 + '.'), None, '111 units, trailing dot -> 330 B'),
    'J06': (u16('a' * 111), None, '111 ASCII units, 111 B (control)'),
    'J07': (u16('short.txt'), None, 'short name (control)'),
    'D01': (u16(Z * 111), None, 'directory, 111 units, 333 B'),
    'B01': (u16('ok-before-bad'), None, 'bad/: valid record first'),
    # name_len claims 223 bytes in a 255-byte record: one more than it holds
    'B02': (u16(K * 111) + b'\x41', 255, 'bad/: name_len 223 > 255 - 33'),
}

def rec_len(name_len):
    # ECMA-119 pads an even name_len to keep the record even; a 222-byte
    # name has no room for the pad, so it is omitted (255-byte record)
    return min(255, 33 + name_len + (0 if name_len % 2 else 1))

def main(src, dst):
    img = bytearray(open(src, 'rb').read())
    # find the Joliet SVD
    lba = 16
    svd = None
    while True:
        vd = img[lba * S:(lba + 1) * S]
        if vd[0] == 255:
            break
        if vd[0] == 2 and vd[88:91] in (b'%/@', b'%/C', b'%/E'):
            svd = lba
        lba += 1
    assert svd, 'no Joliet SVD'
    root = img[svd * S + 156:svd * S + 156 + 34]
    todo = [(struct.unpack_from('<I', root, 2)[0],
             struct.unpack_from('<I', root, 10)[0])]
    seen = set()
    done = set()
    while todo:
        ext, size = todo.pop()
        if ext in seen:
            continue
        seen.add(ext)
        recs = []
        for sec in range(size // S):
            off = (ext + sec) * S
            p = 0
            while p < S:
                ln = img[off + p]
                if ln == 0:
                    break
                recs.append(bytes(img[off + p:off + p + ln]))
                p += ln
        out = []
        for i, r in enumerate(recs):
            nl = r[32]
            name = r[33:33 + nl]
            if i >= 2 and r[25] & 2:
                todo.append((struct.unpack_from('<I', r, 2)[0],
                             struct.unpack_from('<I', r, 10)[0]))
            tag = None
            try:
                t = name.decode('utf-16-be')
                if t[3:].startswith('x' * 100):
                    tag = t[:3]
            except UnicodeDecodeError:
                pass
            if tag and tag.startswith('P'):
                out.append(('pad', r))  # dropped below only if needed
                continue
            if tag in T:
                new, force, note = T[tag]
                nl2 = len(new)
                if force:
                    ln = force
                    body = new[:ln - 33]
                else:
                    ln = rec_len(nl2)
                    body = new + b'\0' * (ln - 33 - nl2)
                assert ln <= 255, (tag, ln)
                r = bytearray(r[:33]) + body
                r[0] = ln
                r[32] = nl2
                r = bytes(r)
                done.add(tag)
                print(f'{tag}: extent {ext} record {ln} B, name_len {nl2}, '
                      f'{note}')
            out.append(r)
        # repack, dropping PAD records only while the records do not fit;
        # every sector of the extent must keep at least one record (a
        # directory sector that starts with a zero byte is a separate
        # problem and would confound this test)
        def pack(recs):
            buf = bytearray(size)
            p = 0
            for r in recs:
                if (p % S) + len(r) > S:
                    p = (p // S + 1) * S
                if p + len(r) > size:
                    return None
                buf[p:p + len(r)] = r
                p += len(r)
            return buf
        out = [x if isinstance(x, tuple) else ('rec', x) for x in out]
        while True:
            buf = pack([r for _, r in out])
            if buf is not None:
                break
            i = max(i for i, (k, _) in enumerate(out) if k == 'pad')
            del out[i]
        for sec in range(size // S):
            assert buf[sec * S] != 0, ('empty directory sector', ext, sec)
        img[ext * S:ext * S + size] = buf
    missing = set(T) - done
    assert not missing, missing
    open(dst, 'wb').write(img)

main(*sys.argv[1:])
