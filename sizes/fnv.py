#!/usr/bin/env python3
"""fnv.py <file|-> -- size and 64-bit FNV-1a, as rt.c's "h" command prints."""
import sys
b = (sys.stdin.buffer if sys.argv[1] == "-" else open(sys.argv[1], "rb")).read()
h = 0xcbf29ce484222325
for c in b:
    h = ((h ^ c) * 0x100000001b3) & 0xffffffffffffffff
print("size=%d fnv=%016x" % (len(b), h))
