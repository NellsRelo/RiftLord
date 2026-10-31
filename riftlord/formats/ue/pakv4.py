"""Read and write UE4 pak version 4 -- the format every mod in this game uses.

The shipped chunks are version 11, but the game's loader also accepts 3 and 4, and
v4 has a flat index that is simple enough to write correctly. Files are stored
uncompressed, which is fine for mod-sized payloads.

Layout: each file's data is preceded by its own copy of the entry header, then the
index at the end lists every file again, then a 45-byte footer.
"""

import hashlib
import os
import struct

MAGIC = 0x5A6F12E1
VERSION = 4
FOOTER_SIZE = 45          # bEncryptedIndex(1) + magic(4) + version(4) + off(8) + size(8) + hash(20)


def _fstring(s):
    b = s.encode('utf-8') + b'\x00'
    return struct.pack('<i', len(b)) + b


def _read_fstring(buf, o):
    n = struct.unpack_from('<i', buf, o)[0]
    o += 4
    if n < 0:                                   # UTF-16
        raw = buf[o:o - n * 2]
        return raw.decode('utf-16-le').rstrip('\x00'), o - n * 2
    return buf[o:o + n].decode('utf-8').rstrip('\x00'), o + n


def _entry(offset, size, sha):
    """FPakEntry as v4 writes it: no compression, so no block list."""
    return (struct.pack('<qqq', offset, size, size)
            + struct.pack('<I', 0)              # CompressionMethod: none
            + sha
            + struct.pack('<B', 0)              # Flags
            + struct.pack('<I', 0))             # CompressionBlockSize


ENTRY_SIZE = 8 + 8 + 8 + 4 + 20 + 1 + 4          # 53


def read(path):
    """Enumerate a v4 pak: (mount, version, [(name, offset, size)]).

    Verification aid for write(); it does not handle v3 or v11.
    """
    with open(path, 'rb') as fh:
        data = fh.read()
    foot = data[-FOOTER_SIZE:]
    enc, magic, version = struct.unpack_from('<BII', foot, 0)
    if magic != MAGIC:
        raise ValueError('not a pak, or not version 4 (v3 and v11 differ; '
                         'this reader exists to verify what write() produces)')
    idx_off, idx_size = struct.unpack_from('<qq', foot, 9)
    if enc:
        raise ValueError('encrypted index is not supported')

    idx = data[idx_off:idx_off + idx_size]
    mount, o = _read_fstring(idx, 0)
    count = struct.unpack_from('<i', idx, o)[0]
    o += 4
    out = []
    for _ in range(count):
        name, o = _read_fstring(idx, o)
        off, size, _usize = struct.unpack_from('<qqq', idx, o)
        o += 24
        o += 4 + 20 + 1 + 4                      # method, hash, flags, block size
        out.append((name, off, size))
    return mount, version, out


def write(path, files, mount='../../../'):
    """Write a v4 pak.

    ``files`` maps the path inside the pak (e.g. ``OMD/Content/Maps/x.umap``) to
    either bytes or a path on disk. A pak in the game's Paks folder is written as
    ``<path>.part`` and put in place by riftlord.core.pakguard (a pak the running game
    may have mounted is replaced only once it has closed).
    """
    from riftlord.core import pakguard
    if pakguard.in_game_paks(path):
        got = pakguard.write(path, lambda part: _write(part, files, mount))
        return dict(got, pak=path, size=os.path.getsize(path))
    return _write(path, files, mount)


def _write(path, files, mount):
    """write() straight to `path`."""
    records = []
    with open(path, 'wb') as fh:
        for name, src in files.items():
            blob = src if isinstance(src, (bytes, bytearray)) else open(src, 'rb').read()
            blob = bytes(blob)
            sha = hashlib.sha1(blob).digest()
            offset = fh.tell()
            fh.write(_entry(0, len(blob), sha))  # the in-line header stores offset 0
            fh.write(blob)
            records.append((name.replace('\\', '/'), offset, len(blob), sha))

        index_offset = fh.tell()
        idx = bytearray(_fstring(mount))
        idx += struct.pack('<i', len(records))
        for name, offset, size, sha in records:
            idx += _fstring(name)
            idx += _entry(offset, size, sha)
        fh.write(idx)

        fh.write(struct.pack('<B', 0))
        fh.write(struct.pack('<II', MAGIC, VERSION))
        fh.write(struct.pack('<qq', index_offset, len(idx)))
        fh.write(hashlib.sha1(bytes(idx)).digest())
    return {'pak': path, 'files': len(records), 'index_offset': index_offset,
            'size': os.path.getsize(path)}
