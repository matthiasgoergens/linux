#!/usr/bin/env python3
"""expected.py -- independent expected size/fnv of mkctl.c's /cz and /sp."""
M = (1 << 64) - 1
comp = bytes(b"compressible "[i % 13] for i in range(65536))
x, rnd = 0x9e3779b97f4a7c15, bytearray()
for _ in range(65536):
    x ^= (x << 13) & M; x ^= x >> 7; x ^= (x << 17) & M
    rnd.append(x >> 56)
def fnv(b):
    h = 0xcbf29ce484222325
    for c in b:
        h = ((h ^ c) * 0x100000001b3) & M
    return h
cz = comp + bytes(rnd) + bytes(131072) + comp[:40000]
sp = bytes(rnd[:4096]) + bytes(8 * 1048576 + 123 - 4096) + comp[:4096]
for n, b in (("cz", cz), ("sp", sp)):
    print("expected /%s: size=%d fnv=%016x" % (n, len(b), fnv(b)))
