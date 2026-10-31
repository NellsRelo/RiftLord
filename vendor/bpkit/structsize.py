#!/usr/bin/env python3
"""A native struct's size and alignment, read from the game's exe: the
UHT-generated FStructParams for each USTRUCT (4.27, UObject/UObjectGlobals.h)
holds the struct's name and then sizeof and alignof:

    OuterFunc, SuperFunc, StructOpsFunc  three code pointers
    NameUTF8                             const char*
    SizeOf, AlignOf                      SIZE_T each

    python3 -m bpkit.structsize OMDPlayerAnimationSet [More ...]

For SOURCE_STRUCT_SIZES (typedb.py) when no Blueprint holds the struct.
"""
import os
import struct
import sys

EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'OMD-Win64-Shipping.exe')


def sections(data):
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    count = struct.unpack_from('<H', data, pe + 6)[0]
    opt = struct.unpack_from('<H', data, pe + 20)[0]
    base = struct.unpack_from('<Q', data, pe + 24 + 24)[0]
    out = []
    at = pe + 24 + opt
    for i in range(count):
        name, vsize, va, rsize, raw = struct.unpack_from('<8sIIII', data, at + i * 40)
        out.append((name.rstrip(b'\0').decode(), va, vsize, raw, rsize))
    return base, out


def to_va(base, secs, offset):
    for _n, va, _vs, raw, rsize in secs:
        if raw <= offset < raw + rsize:
            return base + va + (offset - raw)
    return None


def to_offset(base, secs, address):
    rva = address - base
    for _n, va, vsize, raw, rsize in secs:
        if va <= rva < va + max(vsize, rsize):
            return raw + (rva - va)
    return None


def measure(data, base, secs, name):
    """[(size, align)] of every FStructParams naming `name` (normally one)."""
    found = []
    needle = name.encode() + b'\0'
    at = data.find(needle)
    while at >= 0:
        if data[at - 1:at] == b'\0':
            va = to_va(base, secs, at)
            ref = struct.pack('<Q', va)
            r = data.find(ref)
            while r >= 0:
                if r % 8 == 0 and r >= 24:
                    size, align = struct.unpack_from('<QQ', data, r + 8)
                    pointers = struct.unpack_from('<3Q', data, r - 24)
                    if 0 < size < 1 << 20 and align in (1, 2, 4, 8, 16) and all(
                            p == 0 or to_offset(base, secs, p) is not None for p in pointers):
                        found.append((size, align))
                r = data.find(ref, r + 1)
        at = data.find(needle, at + 1)
    return found


def main(names):
    data = open(EXE, 'rb').read()
    base, secs = sections(data)
    for name in names:
        print(name, measure(data, base, secs, name) or 'not found')


if __name__ == '__main__':
    main(sys.argv[1:])
