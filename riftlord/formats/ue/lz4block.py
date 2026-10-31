"""Decompress an LZ4 block, with no dependencies.

Unreal compresses pak blocks with either Zlib or LZ4, and OMD3's ice DLC pak uses
LZ4. The `lz4` wheel is not installable here -- the system Python is externally
managed -- and without it `gamefiles` raised on every ice map. Because the caller
swallowed the error, the three ice levels were silently absent from the donor
index rather than reported as unreadable.

The block format is small enough to implement outright:

    token       one byte: high nibble literal length, low nibble match length
    (extend)    if a nibble is 15, add 0xff bytes until one is not 0xff
    literals    that many bytes, copied straight out
    offset      uint16, little endian, how far back the match starts
    match       low nibble + 4 bytes, copied from earlier output

Matches may overlap the write position -- an offset of 1 with a length of 10 is a
run of one byte -- so an overlapping match repeats the last `offset` bytes rather than
slicing once (checked identical to a byte-at-a-time copy on 1,602 pak blocks, 65 MB).
"""

import struct


class LZ4Error(Exception):
    pass


def decompress(src, uncompressed_size=None):
    """One LZ4 block -> bytes.

    `uncompressed_size` is checked rather than trusted: a block that decodes to a
    different length means the block boundaries were read wrong, and silently
    returning short data would corrupt a package in a way that is very hard to
    trace back to here.
    """
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1

        length = token >> 4
        if length == 15:
            while True:
                if i >= n:
                    raise LZ4Error('literal length ran off the end')
                add = src[i]
                i += 1
                length += add
                if add != 0xff:
                    break
        if i + length > n:
            raise LZ4Error('literal run of %d exceeds the block' % length)
        out += src[i:i + length]
        i += length

        # The final sequence is literals only, with no match after it.
        if i >= n:
            break
        if i + 2 > n:
            raise LZ4Error('truncated match offset')
        offset, = struct.unpack_from('<H', src, i)
        i += 2
        if offset == 0 or offset > len(out):
            raise LZ4Error('match offset %d is outside the output' % offset)

        match = (token & 0x0f) + 4
        if (token & 0x0f) == 15:
            while True:
                if i >= n:
                    raise LZ4Error('match length ran off the end')
                add = src[i]
                i += 1
                match += add
                if add != 0xff:
                    break

        start = len(out) - offset
        if offset >= match:
            out += out[start:start + match]
        else:
            # an overlapping match repeats the last `offset` bytes: the same bytes the
            # byte-at-a-time copy writes, without a Python loop per byte
            out += (out[start:] * (match // offset + 1))[:match]

    if uncompressed_size is not None and len(out) != uncompressed_size:
        raise LZ4Error('block decoded to %d bytes, expected %d'
                       % (len(out), uncompressed_size))
    return bytes(out)
